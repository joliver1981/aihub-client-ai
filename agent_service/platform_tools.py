"""
The Agent's platform tools — A0 read-only set.

Each tool is a thin async HTTP wrapper over an EXISTING main-app REST endpoint,
called with the platform service key (X-API-Key), exactly the way Command
Center's native tools work. The model never sees credentials: list_connections
whitelists identity fields only, and the probe endpoint's sql_gate enforces
single read-only SELECTs with a server-side row cap.

Honesty rules carried over from CC's tool bodies:
- never raise into the agent loop; return is_error text the model can read
- report empty results as first-class information, never as silent success
- surface server rejections (gate refusals, SQL errors) verbatim
"""

import asyncio
import json
import contextvars
import os
import re
from typing import Any, Optional

import httpx

from agent_config import get_base_url, AI_HUB_API_KEY, logger
from claude_agent_sdk import tool, create_sdk_mcp_server

# Per-request session envelope (set by main.py before each turn); tools read
# identity from here — never from anything the model wrote.
_DEFAULT_USER: dict = {"user_id": 0, "role": 2, "username": "agent-service"}
CURRENT_USER: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "CURRENT_USER", default=_DEFAULT_USER
)


# ---------------------------------------------------------------------------
# Per-turn search coverage (2026-09-05, handoff "three usability defects" #1).
# A negative answer ("there is no August data") is only as strong as the
# search behind it, and the model does not track that distinction on its own:
# live, it queried 2 of 6 connections and generalised the absence to all 6.
# The data tools record, on the turn's own envelope dict, which connections
# exist and which ones a row-level query actually touched; brain.run_turn
# turns that into a deterministic coverage note when a reply asserts absence.
# The record lives on the per-turn envelope only (never the module default),
# so it cannot leak between users or turns.
# ---------------------------------------------------------------------------
_COVERAGE_KEY = "_coverage"


def _coverage_record() -> Optional[dict]:
    ctx = CURRENT_USER.get()
    if not isinstance(ctx, dict) or ctx is _DEFAULT_USER:
        return None
    rec = ctx.get(_COVERAGE_KEY)
    if not isinstance(rec, dict):
        rec = {"known": [], "queried": []}
        ctx[_COVERAGE_KEY] = rec
    return rec


def note_known_connections(conns: list) -> None:
    """Remember every connection the platform reports this turn."""
    rec = _coverage_record()
    if rec is None:
        return
    names = [str(c.get("name") or "").strip() for c in (conns or [])
             if isinstance(c, dict) and c.get("name")]
    rec["known"] = [n for n in names if n]


def note_queried_connection(name) -> None:
    """Remember that a row-level query ran against `name` this turn."""
    rec = _coverage_record()
    if rec is None or not name:
        return
    n = str(name).strip()
    if n and n not in rec["queried"]:
        rec["queried"].append(n)


def reset_coverage(ctx: Optional[dict]) -> None:
    """Drop the record at the start of a turn (run_turn) so a reused envelope
    never carries a previous turn's coverage."""
    if isinstance(ctx, dict):
        ctx.pop(_COVERAGE_KEY, None)


def coverage_snapshot(ctx: Optional[dict] = None) -> tuple:
    """(known_names, queried_names) recorded on `ctx` (default: this turn's
    envelope). Empty lists when nothing was recorded."""
    ctx = ctx if ctx is not None else CURRENT_USER.get()
    rec = ctx.get(_COVERAGE_KEY) if isinstance(ctx, dict) else None
    if not isinstance(rec, dict):
        return [], []
    return list(rec.get("known") or []), list(rec.get("queried") or [])


_FOOTER_MAX_NAMES = 12


def coverage_footer(ctx: Optional[dict] = None) -> str:
    """One line for the END of every query result: which connections this
    turn's row-level queries touched and which they have not. Names only —
    the doctrine lives in the system prompt and the connection listing, not
    repeated per result (James, 2026-09-05)."""
    known, queried = coverage_snapshot(ctx)
    if not known:
        return ""
    qset = {q.lower() for q in queried}
    done = [k for k in known if k.lower() in qset]
    left = [k for k in known if k.lower() not in qset]
    if not left:
        return f"\nQueried this turn: all {len(known)} connections."
    more = (f" (+{len(left) - _FOOTER_MAX_NAMES} more)"
            if len(left) > _FOOTER_MAX_NAMES else "")
    return (f"\nQueried this turn: {', '.join(done) or '(none)'}. "
            f"Not queried: {', '.join(left[:_FOOTER_MAX_NAMES])}{more}.")

_TIMEOUT = httpx.Timeout(30.0, read=120.0)


def _user_assertion_header() -> dict:
    """{'X-AIHub-User': <assertion>} for the signed-in user this turn runs for,
    or {} for the service principal (user_id 0 — the contextvar default) and
    identity-less runs.

    The main app's identity-aware routes (document search / records / listing,
    /api/agents/<id>/chat) treat an ABSENT assertion as unrestricted, so this
    header is what makes the caller's own ACLs apply to delegated calls
    (doc-acl G3, 2026-09-03). If signing FAILS for a real user (PyJWT missing,
    no API_KEY so there is no secret) this RAISES rather than sending the call
    identity-less: a silently unrestricted call is the fail-open that
    document_tools._headers() still carries (background doc G5). Every tool
    wraps its call, so the raise surfaces as an honest error text.
    """
    user = CURRENT_USER.get() or {}
    uid = user.get("user_id")
    if uid in (None, "", 0, "0"):
        return {}
    try:
        import shared_auth
        token = shared_auth.sign_user_assertion(
            uid, user.get("tenant_id"), user.get("role"))
    except Exception as e:
        logger.error(f"user assertion signing failed for user_id={uid}: {e} — "
                     f"refusing to send an identity-less (unrestricted) platform call")
        raise RuntimeError(f"could not sign the user assertion: {e}") from e
    return {"X-AIHub-User": token}


def _headers():
    h = {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}
    h.update(_user_assertion_header())
    return h


def _unwrap(data):
    """Several legacy endpoints double-encode (JSON string containing JSON)."""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except Exception:
            return data
    return data


class ConnectionAccessDenied(Exception):
    """The platform refused a connection for THIS user (403, access: denied —
    the connection ACL, 2026-09-22). The message is the platform's own honest
    text: an access restriction, never "does not exist"."""


def _denied_message(r) -> Optional[str]:
    """The platform's denial text when `r` is a connection-ACL 403, else None."""
    if getattr(r, "status_code", None) != 403:
        return None
    try:
        body = r.json()
    except Exception:
        return None
    if isinstance(body, dict) and body.get("access") == "denied":
        return str(body.get("error") or "this connection is not shared with your account")
    return None


async def _get(path: str, timeout: Optional[httpx.Timeout] = None):
    async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
        r = await client.get(f"{get_base_url()}{path}", headers=_headers())
        denied = _denied_message(r)
        if denied:
            raise ConnectionAccessDenied(denied)
        r.raise_for_status()
        return _unwrap(r.json())


# ---------------------------------------------------------------------------
# Connection ACL wording (2026-09-22). The platform scopes /get/connections
# and /api/discover/* to the connections behind the Data Assistants shared
# with a regular user's groups (role below CONNECTION_ACL_UNRESTRICTED_ROLE,
# default 2). The tools must then describe what they see as an ACCESS-scoped
# view — never as the platform's total state (RU pack F-2: "that document is
# not in the store" about a document the user simply could not read).
# ---------------------------------------------------------------------------
def _acl_floor() -> int:
    try:
        return int(os.getenv("CONNECTION_ACL_UNRESTRICTED_ROLE", "2") or 2)
    except (TypeError, ValueError):
        return 2


def restricted_caller() -> bool:
    """True when this turn runs for a user whose connection list the platform
    scopes (verified role below the ACL floor)."""
    user = CURRENT_USER.get() or {}
    try:
        return int(user.get("role") or 0) < _acl_floor()
    except (TypeError, ValueError):
        return False


ACCESS_HINT = ("An administrator grants access by sharing a Data Assistant (data agent) "
               "that uses the connection with one of your groups (Groups page).")

SCOPED_NOTE = ("\nThis list is scoped to your access: other connections may exist on the "
               "platform that are not shared with your account. " + ACCESS_HINT)

NO_ACCESS_TEXT = ("No data connections are shared with your account — this is an access "
                  "restriction, not an empty platform. " + ACCESS_HINT + " Agents shared "
                  "with you (list_agents / ask_agent) can still answer from their own sources.")


def _no_connections_text() -> str:
    return NO_ACCESS_TEXT if restricted_caller() else "No data connections are configured."


async def _post(path: str, body: dict, timeout: float | None = None):
    t = httpx.Timeout(30.0, read=timeout) if timeout else _TIMEOUT
    async with httpx.AsyncClient(timeout=t) as client:
        r = await client.post(f"{get_base_url()}{path}", json=body, headers=_headers())
        # Probe/manage endpoints return structured errors with 200; other 4xx/5xx
        # should surface as readable text, not exceptions.
        try:
            return _unwrap(r.json()), r.status_code
        except Exception:
            return {"error": r.text[:500]}, r.status_code


def _text(msg: str, is_error: bool = False) -> dict:
    out: dict[str, Any] = {"content": [{"type": "text", "text": msg}]}
    if is_error:
        out["is_error"] = True
    return out


def _pick(row: dict, *names):
    low = {str(k).lower(): v for k, v in row.items()}
    for n in names:
        if n in low and low[n] not in (None, ""):
            return low[n]
    return None


async def _connections_index():
    data = await _get("/get/connections")
    out = []
    for row in (data or []):
        if not isinstance(row, dict):
            continue
        out.append({
            "id": _pick(row, "id", "connection_id"),
            "name": _pick(row, "connection_name", "name"),
            "type": _pick(row, "connection_type", "type", "db_type", "engine", "provider"),
            "database": _pick(row, "database", "database_name", "initial_catalog"),
        })
    note_known_connections(out)
    return out


_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _base_name(name) -> str:
    """'EDW (SQL Server)' -> 'EDW': the display name without the trailing
    parenthetical admins append to connection names (engine, environment)."""
    return _PAREN_RE.sub("", str(name or "")).strip()


def _known_names(conns: list) -> str:
    return ", ".join(str(c.get("name")) for c in (conns or [])
                     if isinstance(c, dict) and c.get("name"))


def match_connection(ref, conns: list, restricted: bool = False) -> tuple:
    """Resolve a connection reference against the index -> (row, error).

    `restricted=True` (a regular user whose index the platform scoped, see
    restricted_caller): an unknown id/name is worded as an ACCESS restriction
    and an EMPTY index refuses a numeric id instead of passing it through —
    for that caller an empty index is deny-all, not "index unavailable".

    Ladder, first hit wins: numeric id -> exact name (case-insensitive) ->
    exact BASE name ('EDW' -> 'EDW (SQL Server)', 'EDWDB' -> 'EDWDB (Postgres)')
    -> unique prefix -> unique substring. A tier with several candidates is an
    honest 'ambiguous' error that lists them; an unknown name lists every
    connection. Never a silent guess.

    Why (live 2026-09-05, TA-26a): the exact-name-only resolver rejected 'EDW'
    and 'EDWDB' against 'EDW (SQL Server)' / 'EDWDB (Postgres)'; the model
    burned two calls, fell back to a numeric id for one, and came away
    believing it had 'tried' a source it never read a row from.
    """
    s = str(ref if ref is not None else "").strip()
    conns = [c for c in (conns or []) if isinstance(c, dict)]
    if not s:
        return None, "No connection given — pass a connection id or name."
    if s.isdigit():
        hit = [c for c in conns if str(c.get("id")) == s]
        if hit:
            return hit[0], None
        if restricted:
            return None, (f"Connection id {s} is not among the connections available to "
                          f"your account ({_known_names(conns) or 'none are shared with you'})"
                          " — an access restriction, not a missing connection. " + ACCESS_HINT)
        if not conns:
            # index unavailable: keep the pre-existing pass-through so an id
            # the platform would accept is not refused on a listing hiccup
            return {"id": s, "name": s}, None
        return None, (f"No connection with id {s}. Known connections: "
                      f"{_known_names(conns) or '(none)'}")
    low = s.lower()
    names = [(c, str(c.get("name") or "").strip()) for c in conns]
    exact = [c for c, n in names if n.lower() == low]
    if exact:
        return exact[0], None      # first exact wins, as before this ladder
    tiers = [[c for c, n in names if _base_name(n).lower() == low]]
    if len(low) >= 2:
        tiers.append([c for c, n in names if n.lower().startswith(low)])
        tiers.append([c for c, n in names if low in n.lower()])
    for tier in tiers:
        if len(tier) == 1:
            return tier[0], None
        if len(tier) > 1:
            cands = ", ".join(f"{c.get('name')} (id {c.get('id')})" for c in tier)
            return None, (f"'{ref}' is ambiguous — it matches {len(tier)} "
                          f"connections: {cands}. Use the full name or the id.")
    if restricted:
        return None, (f"'{ref}' is not among the connections available to your account "
                      f"({_known_names(conns) or 'none are shared with you'}) — an access "
                      "restriction, not a missing connection. " + ACCESS_HINT)
    return None, (f"No connection named '{ref}'. Known connections: "
                  f"{_known_names(conns) or '(none)'}")


def _resolution_note(ref, row: dict) -> str:
    """One line the tool prepends when a reference was resolved by something
    other than an exact name/id, so the model always knows which connection
    it actually touched (and never mistakes 'EDW' for a source it didn't read)."""
    s = str(ref if ref is not None else "").strip()
    name = str(row.get("name") or "")
    if s.lower() == name.lower() or s == str(row.get("id")):
        return ""
    return f"(connection '{s}' resolved to '{name}', id {row.get('id')})\n"


async def _resolve_connection_row(ref) -> tuple:
    """(index row, None) or (None, honest error) — see match_connection."""
    conns = await _connections_index()
    # Recorded here as well as inside _connections_index: a resolution is
    # proof the platform reported these connections this turn, whichever
    # path produced the index.
    note_known_connections(conns)
    return match_connection(ref, conns, restricted=restricted_caller())


async def _resolve_connection(ref) -> tuple:
    """Accept a numeric id or a connection name -> (id, None) | (None, error)."""
    row, err = await _resolve_connection_row(ref)
    if err:
        return None, err
    return str(row.get("id")), None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

COVERAGE_RULE = (
    "Coverage rule: a 'no data / nothing recorded' answer covers only the "
    "connections you actually queried. When you do not know where data lives, "
    "search_tables checks every connection in one call; every query result ends "
    "with a coverage line (queried / NOT queried this turn) — scope a negative "
    "answer to the queried list, or check the rest first.")


@tool(
    "list_data_connections",
    "List the data connections configured in AI Hub (id, name, type, database). "
    "Call this first whenever a request involves data, to see what exists — "
    "never assume a connection name. A negative answer ('no data for X') is "
    "only as strong as its coverage: query every connection that could hold "
    "the data, or state which ones you checked and which you did not.",
    {},
)
async def list_data_connections(args: dict[str, Any]) -> dict[str, Any]:
    try:
        conns = await _connections_index()
        if not conns:
            return _text(_no_connections_text())
        lines = [f"- id {c['id']} — {c['name']} ({c.get('type') or 'type ?'}, "
                 f"db {c['database']})" for c in conns]
        if restricted_caller():
            return _text("Data connections available to your account:\n" + "\n".join(lines)
                         + SCOPED_NOTE + "\n\n" + COVERAGE_RULE)
        return _text("Data connections:\n" + "\n".join(lines) + "\n\n" + COVERAGE_RULE)
    except Exception as e:
        logger.error(f"list_data_connections failed: {e}")
        return _text(f"Could not list connections: {e}", is_error=True)


@tool(
    "get_connection_schema",
    "Inspect a connection's schema. With only `connection`, lists its tables. "
    "With `table` (qualified names like 'TS.employee_data' are fine), lists that "
    "table's columns with types, keys, and sample values. With `column` too, "
    "enumerates that column's values. Always probe schema before writing SQL — "
    "never trust remembered table or column names.",
    {
        "type": "object",
        "properties": {
            "connection": {"type": "string", "description": "Connection id or name"},
            "table": {"type": "string", "description": "Optional table name"},
            "column": {"type": "string", "description": "Optional column for value lookup"},
        },
        "required": ["connection"],
        "additionalProperties": False,
    },
)
async def get_connection_schema(args: dict[str, Any]) -> dict[str, Any]:
    try:
        row, err = await _resolve_connection_row(args["connection"])
        if err:
            return _text(err, is_error=True)
        conn_id = str(row.get("id"))
        conn_name = str(row.get("name") or conn_id)
        note = _resolution_note(args["connection"], row)
        table = (args.get("table") or "").strip()
        if not table:
            data = await _get(f"/api/discover/tables/{conn_id}")
            tables = data.get("tables") or []
            if not tables:
                return _text(f"{note}Connection {conn_id} ({conn_name}) reports no tables.")
            lines = []
            for t in tables[:200]:
                name = t.get("TABLE_NAME") or t.get("table_name")
                doc = " (documented)" if t.get("is_documented") else ""
                lines.append(f"- {name}{doc}")
            return _text(f"{note}Tables on connection {conn_id} ({conn_name}):\n"
                         + "\n".join(lines))

        from urllib.parse import quote
        path = f"/api/discover/schema/{conn_id}?table={quote(table)}"
        if args.get("column"):
            path += f"&column={quote(str(args['column']))}"
        data = await _get(path)
        if not data.get("success", True) and data.get("error"):
            return _text(f"{note}Schema lookup failed: {data['error']}", is_error=True)
        cols = data.get("columns") or []
        lines = [f"{note}Table {data.get('table', table)} on {conn_name} — "
                 f"source: {data.get('source', '?')}"]
        if data.get("table_description"):
            lines.append(f"Description: {data['table_description']}")
        for c in cols[:120]:
            tags = []
            if c.get("is_primary_key"):
                tags.append("PK")
            if c.get("is_foreign_key"):
                tags.append(f"FK->{c.get('foreign_key_table')}.{c.get('foreign_key_column')}")
            tag = f" [{', '.join(tags)}]" if tags else ""
            desc = f" — {c['column_description']}" if c.get("column_description") else ""
            line = f"- {c.get('COLUMN_NAME')} ({c.get('DATA_TYPE')}){tag}{desc}"
            vals = c.get("column_values")
            if vals:
                shown = ", ".join(str(v) for v in vals[:12])
                more = f" …(+{c['distinct_count'] - 12} more)" if c.get("distinct_count", 0) > 12 else ""
                line += f" values: [{shown}]{more}"
            elif c.get("values_too_many"):
                line += " (too many distinct values to enumerate)"
            lines.append(line)
        if len(cols) > 120:
            lines.append(f"…(+{len(cols) - 120} more columns)")
        if data.get("source") == "dictionary_only":
            lines.append("NOTE: live DB unreachable — this is Data Dictionary info and may be stale.")
        return _text("\n".join(lines))
    except ConnectionAccessDenied as e:
        return _text(f"Access denied: {e}", is_error=True)
    except Exception as e:
        logger.error(f"get_connection_schema failed: {e}")
        return _text(f"Schema lookup failed: {e}", is_error=True)


@tool(
    "probe_connection_query",
    "Run ONE small read-only SELECT against a connection to verify assumptions "
    "(row counts, filter values, joins) before answering. The server enforces "
    "read-only and caps rows (~50). Zero rows is a finding — usually a filter "
    "value that doesn't exist; say so rather than guessing — and zero rows on "
    "ONE connection says nothing about the others. CHARTS: pass "
    "chart='bar'|'line'|'area'|'pie'|'doughnut'|'hbar' (and chart_title) to get "
    "a ready-made chart block built from the rows — first text column = "
    "labels, numeric columns = series — that you paste into your reply "
    "VERBATIM; the numbers never pass through you. Shape the SQL for the "
    "chart (label column first, one row per bar/point, ORDER BY, under ~60 rows).",
    {
        "type": "object",
        "properties": {
            "connection": {"type": "string", "description": "Connection id or name"},
            "sql": {"type": "string", "description": "A single SELECT statement"},
            "chart": {"type": "string",
                      "enum": ["bar", "line", "area", "pie", "doughnut", "hbar"],
                      "description": "Optional: also return a chart block of the rows"},
            "chart_title": {"type": "string"},
        },
        "required": ["connection", "sql"],
        "additionalProperties": False,
    },
)
async def probe_connection_query(args: dict[str, Any]) -> dict[str, Any]:
    try:
        row, err = await _resolve_connection_row(args["connection"])
        if err:
            return _text(err, is_error=True)
        conn_id = str(row.get("id"))
        conn_name = str(row.get("name") or conn_id)
        rnote = _resolution_note(args["connection"], row)
        data, status = await _post(f"/api/discover/query/{conn_id}",
                                   {"sql": str(args["sql"]).strip()})
        if data.get("access") == "denied":
            # Connection ACL: the platform's own wording — an access
            # restriction, never "does not exist".
            return _text(f"{rnote}Access denied: {data.get('error')}", is_error=True)
        if data.get("rejected"):
            return _text(f"{rnote}Query rejected by the read-only gate: {data.get('error')}",
                         is_error=True)
        if data.get("sql_error"):
            return _text(f"{rnote}SQL error: {data.get('error')}", is_error=True)
        if not data.get("success"):
            return _text(f"{rnote}Probe failed (HTTP {status}): {data.get('error', data)}",
                         is_error=True)
        # The query ran against this connection — the per-turn coverage ledger
        # (see coverage_footer) is what a negative answer can honestly cover.
        note_queried_connection(conn_name)
        rows = data.get("rows") or []
        cols = data.get("columns") or []
        if not rows:
            return _text(f"{rnote}0 rows returned from {conn_name}. This is almost "
                         "always a filter value that does not exist — verify values "
                         "with get_connection_schema before assuming the data is "
                         "missing." + coverage_footer())
        lines = [rnote + " | ".join(str(c) for c in cols)]
        for r in rows[:15]:
            # rows arrive as dicts keyed by column — iterating the dict itself
            # would render the KEYS (column names) instead of the values
            vals = [r.get(c) for c in cols] if isinstance(r, dict) else list(r)
            lines.append(" | ".join(str(v) for v in vals))
        note = f"\n({data.get('row_count')} rows returned"
        if data.get("cap_applied"):
            note += f", server cap {data.get('row_cap')} applied"
        if data.get("truncated_columns"):
            note += ", some columns truncated"
        note += ")"
        chart_part = ""
        if args.get("chart"):
            import rich_blocks
            block, cnote = rich_blocks.chart_from_rows(
                cols, rows, str(args.get("chart")), str(args.get("chart_title") or ""))
            if block:
                # Store the spec and hand back a reference the model cannot
                # paraphrase (numbers never pass through it).
                try:
                    import json as _json
                    spec = _json.loads(block.split("\n", 1)[1].rsplit("\n```", 1)[0])
                    uid = int((CURRENT_USER.get() or {}).get("user_id") or 0)
                    block = rich_blocks.ref_fence(uid, "chart", spec)
                except Exception as e:
                    logger.warning(f"probe chart: reference store failed, inline block: {e}")
                chart_part = ("\n\nChart block — paste the 3-line block below into your "
                              "reply EXACTLY as it is (a reference; the data is stored "
                              f"server-side; it renders as a {args.get('chart')} chart; "
                              f"{cnote}):\n" + block)
            else:
                chart_part = f"\n\n(No chart block: {cnote})"
        return _text("\n".join(lines) + note + coverage_footer() + chart_part)
    except Exception as e:
        logger.error(f"probe_connection_query failed: {e}")
        return _text(f"Probe failed: {e}", is_error=True)


# ---------------------------------------------------------------------------
# Cross-connection table search (2026-09-05). The capability that was missing
# when the model concluded "no August data" from 2 of 6 connections: there was
# no way to ask the platform WHERE data of a kind lives except by walking the
# connections one schema call at a time. One call, every connection, and the
# result says which connections matched, which had no match, and which could
# not be listed — so an unlisted connection reads as UNCHECKED, never as empty.
# ---------------------------------------------------------------------------
_SEARCH_MAX_PER_CONN = 40
_SEARCH_TIMEOUT = httpx.Timeout(15.0, read=45.0)


def _split_patterns(raw) -> list:
    parts = re.split(r"[,\s]+", str(raw or "").strip().lower())
    return [p for p in parts if p]


def _match_table(name: str, pats: list) -> bool:
    n = str(name or "").lower()
    return any(p in n for p in pats)


async def _tables_for(conn: dict) -> tuple:
    """(connection, [qualified table names], error) — never raises."""
    try:
        data = await _get(f"/api/discover/tables/{conn.get('id')}", timeout=_SEARCH_TIMEOUT)
        if isinstance(data, dict) and data.get("success") is False:
            return conn, [], str(data.get("error") or "listing failed")
        tables = (data or {}).get("tables") or []
        names = [str(t.get("TABLE_NAME") or t.get("table_name") or "")
                 for t in tables if isinstance(t, dict)]
        return conn, [n for n in names if n], None
    except Exception as e:
        return conn, [], str(e) or type(e).__name__


@tool(
    "search_tables",
    "Find which connections hold a table whose name contains any of the given "
    "words — across EVERY data connection in ONE call (e.g. 'sales, orders' or "
    "'invoice'). The first step when you do not know where some data lives, and "
    "the required step before saying data does not exist anywhere: it reports "
    "matches per connection, the connections with NO match, and any connection "
    "it could not list (unlisted = unchecked, not empty). Then probe the "
    "candidates. Pass `connections` to restrict the search.",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string",
                        "description": "One or more name fragments, comma or space "
                                       "separated; case-insensitive substring match "
                                       "on schema.table names"},
            "connections": {"type": "array", "items": {"type": "string"},
                            "description": "Optional connection ids/names to search "
                                           "(default: all)"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
)
async def search_tables(args: dict[str, Any]) -> dict[str, Any]:
    pats = _split_patterns(args.get("pattern"))
    if not pats:
        return _text("Give at least one table-name fragment to search for.", is_error=True)
    try:
        conns = await _connections_index()
        note_known_connections(conns)
        if not conns:
            return _text(_no_connections_text())
        wanted = args.get("connections") or []
        if wanted:
            picked, errs = [], []
            restricted = restricted_caller()
            for ref in wanted:
                row, err = match_connection(ref, conns, restricted=restricted)
                if err:
                    errs.append(err)
                elif row not in picked:
                    picked.append(row)
            if errs:
                return _text("; ".join(errs), is_error=True)
            conns = picked
        results = await asyncio.gather(*(_tables_for(c) for c in conns))
        hits, misses, failed = [], [], []
        for conn, names, err in results:
            label = f"{conn.get('name')} (id {conn.get('id')})"
            if err:
                failed.append(f"{label}: {err[:160]}")
                continue
            found = [n for n in names if _match_table(n, pats)]
            if found:
                shown = ", ".join(found[:_SEARCH_MAX_PER_CONN])
                extra = (f" (+{len(found) - _SEARCH_MAX_PER_CONN} more)"
                         if len(found) > _SEARCH_MAX_PER_CONN else "")
                hits.append(f"- {label}: {shown}{extra}")
            else:
                misses.append(label)
        scope = " available to your account" if restricted_caller() else ""
        lines = [f"Tables matching [{', '.join(pats)}] across {len(conns)} connection(s){scope}:"]
        lines += hits or ["- (no connection has a matching table name)"]
        if misses:
            lines.append(f"No match on: {', '.join(misses)}")
        if failed:
            lines.append("COULD NOT LIST (unchecked, not empty): " + "; ".join(failed))
        lines.append("A name match is a candidate, not a finding — probe it "
                     "(probe_connection_query) before concluding anything about its data.")
        return _text("\n".join(lines))
    except Exception as e:
        logger.error(f"search_tables failed: {e}")
        return _text(f"Table search failed: {e}", is_error=True)


@tool(
    "ask_agent",
    "Ask one of AI Hub's configured agents a question and get its answer — a "
    "DATA agent (it writes and runs the SQL against its database itself) or a "
    "GENERAL agent (a chat agent with its own tools, knowledge documents and "
    "objective). Use it to delegate: 'ask the ERPDB agent for last month's "
    "revenue', 'what does the HR Policy agent say about PTO'. Needs the numeric "
    "agent id — call list_agents FIRST if you don't know it (it shows each data "
    "agent's connections). The answer comes back verbatim; attribute it to the "
    "agent.",
    {
        "type": "object",
        "properties": {
            "agent_id": {"type": "integer", "description": "The agent's id (list_agents)"},
            "question": {"type": "string", "description": "The question to ask"},
        },
        "required": ["agent_id", "question"],
        "additionalProperties": False,
    },
)
async def ask_agent(args: dict[str, Any]) -> dict[str, Any]:
    try:
        data, status = await _post(f"/api/agents/{int(args['agent_id'])}/chat",
                                   {"prompt": str(args["question"]), "history": "[]"},
                                   timeout=300)
        if status >= 400:
            return _text(f"Agent chat failed (HTTP {status}): {data.get('error', data)}",
                         is_error=True)
        answer = data.get("response") or data.get("answer") or ""
        if not answer:
            return _text(f"Agent returned no answer (raw: {json.dumps(data)[:400]})",
                         is_error=True)
        return _text(f"Agent {int(args['agent_id'])} answered:\n{answer}")
    except Exception as e:
        logger.error(f"ask_agent failed: {e}")
        return _text(f"Agent chat failed: {e}", is_error=True)


def _q_contact(uid: int):
    def fn(cur):
        cur.execute("SELECT name, email, phone, user_name FROM [dbo].[User] WHERE id = ?",
                    int(uid))
        r = cur.fetchone()
        if not r:
            return None
        return {"name": str(r[0] or "").strip(), "email": str(r[1] or "").strip(),
                "phone": str(r[2] or "").strip(), "username": str(r[3] or "").strip()}
    return fn


@tool(
    "get_my_contact_info",
    "Look up the SIGNED-IN user's own contact details on file (name, email, "
    "phone). Use it to resolve 'me' / 'my email' — e.g. BEFORE emailing the user "
    "themselves ('email me the summary') — instead of guessing an address. "
    "Returns only the current user's info, never anyone else's.",
    {},
)
async def get_my_contact_info(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)
    if not uid:
        return _text("There is no signed-in user in this context.", is_error=True)
    try:
        import asyncio
        import readthrough

        def _read():
            conn = readthrough._db()
            try:
                return _q_contact(uid)(conn.cursor())
            finally:
                conn.close()
        row = await asyncio.to_thread(_read)
    except Exception as e:
        logger.warning(f"get_my_contact_info lookup failed: {e}")
        row = None
    if not row:
        # Fall back to what the session envelope carries.
        name = str(user.get("name") or "").strip()
        if not name:
            return _text("I couldn't look up your contact info right now (user "
                         "directory unavailable).", is_error=True)
        return _text(f"Your contact info on file — name: {name}; email and phone "
                     "could not be read from the user directory right now.")
    return _text("Your contact info on file — "
                 f"name: {row['name'] or '—'}; email: {row['email'] or '(none on file)'}; "
                 f"phone: {row['phone'] or '(none on file)'}; username: "
                 f"{row['username'] or '—'}.")


def _filter_users(rows: list, query: str) -> list:
    q = " ".join(str(query or "").lower().split())
    if not q:
        return sorted(rows, key=lambda u: (u["name"] or u["username"]).lower())
    toks = q.split()
    return [u for u in rows
            if q in u["name"].lower() or q == u["username"].lower()
            or q in u["email"].lower()
            or all(t in u["name"].lower() for t in toks)]


def _q_find_users(query: str):
    def fn(cur):
        cur.execute("SELECT id, name, user_name, email, phone FROM [dbo].[User]")
        rows = [{"id": int(r[0]), "name": str(r[1] or "").strip(),
                 "username": str(r[2] or "").strip(), "email": str(r[3] or "").strip(),
                 "phone": str(r[4] or "").strip()} for r in cur.fetchall()]
        return _filter_users(rows, query)
    return fn


@tool(
    "find_user_contact",
    "Look up platform USERS and their contact details (name, username, email, "
    "phone) — the user directory behind 'email this to John Smith', 'what is "
    "Ann's phone number' or 'who are the users on the platform'. Pass a name, "
    "username or email fragment to search, or an EMPTY query to list everyone. "
    "Returns every match; when a name matches more than one person, ask which "
    "they meant rather than guessing. send_email resolves names on its own, so "
    "call this to confirm, disambiguate, or answer directory questions.",
    {
        "type": "object",
        "properties": {"query": {"type": "string",
                                 "description": "Name, username or email fragment; "
                                                "empty = list all users"}},
        "additionalProperties": False,
    },
)
async def find_user_contact(args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("query") or "").strip()
    try:
        import asyncio
        import readthrough

        def _sql():
            conn = readthrough._db()
            try:
                return _q_find_users(q)(conn.cursor())
            finally:
                conn.close()

        def _read():
            try:
                users = readthrough.fetch("users")
            except readthrough.ReadthroughUnavailable:
                return _sql()
            return _filter_users([{"id": int(u["id"]), "name": str(u.get("name") or "").strip(),
                                   "username": str(u.get("username") or "").strip(),
                                   "email": str(u.get("email") or "").strip(),
                                   "phone": str(u.get("phone") or "").strip()}
                                  for u in (users or [])], q)
        rows = await asyncio.to_thread(_read)
    except Exception as e:
        return _text(f"Could not read the user directory: {e}", is_error=True)
    if not rows:
        return _text(f"No user matches '{q}' in the directory." if q
                     else "The user directory is empty.")
    cap = 50 if not q else 15
    lines = [f"- {u['name'] or '(no name)'} (username {u['username'] or '—'}): "
             f"email {u['email'] or '(none)'}; phone {u['phone'] or '(none)'}"
             for u in rows[:cap]]
    if not q:
        head = f"All {len(rows)} platform users:"
    else:
        head = (f"{len(rows)} users match '{q}'"
                + (" — ask which one they mean:" if len(rows) > 1 else ":"))
    return _text(head + "\n" + "\n".join(lines)
                 + (f"\n… {len(rows) - cap} more not shown — search by name to narrow"
                    if len(rows) > cap else ""))


@tool(
    "list_playbooks",
    "List the deterministic artifacts that exist in AI Hub: visual workflows and "
    "code flows (from the workflow store) plus automations. Read-only inventory.",
    {},
)
async def list_playbooks(args: dict[str, Any]) -> dict[str, Any]:
    lines = []
    try:
        rows = await _get("/get/workflows")
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            wid = _pick(row, "id", "workflow_id")
            name = _pick(row, "workflow_name", "name")
            kind = "workflow"
            wd = row.get("workflow_data") or row.get("WORKFLOW_DATA")
            if isinstance(wd, str) and '"code_flow"' in wd:
                kind = "code_flow"
            lines.append(f"- [{kind}] id {wid} — {name}")
    except Exception as e:
        lines.append(f"(could not list workflows: {e})")
    try:
        user = CURRENT_USER.get()
        body = {"action": "list",
                "user_context": {"user_id": int(user.get("user_id") or 0),
                                 "role": int(user.get("role") or 2),
                                 "username": str(user.get("username") or "agent")},
                "payload": {}}
        data, status = await _post("/automations/api/internal/manage", body)
        if status < 400:
            for a in (data.get("automations") or []):
                lines.append(f"- [automation] {a.get('automation_id')} — {a.get('name')} "
                             f"(v{a.get('current_version')}, pinned v{a.get('pinned_version')})")
        else:
            lines.append(f"(could not list automations: HTTP {status} {data.get('error', '')})")
    except Exception as e:
        lines.append(f"(could not list automations: {e})")
    if not lines:
        return _text("No playbooks exist yet.")
    return _text("Playbooks (workflows, code flows, automations):\n" + "\n".join(lines))


@tool(
    "list_recent_runs",
    "Show recent execution history. Without arguments: the latest workflow "
    "executions with their honest statuses. With automation_id: that "
    "automation's recent runs.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string",
                              "description": "Optional automation id for its run history"},
            "limit": {"type": "integer", "description": "Max rows (default 10)"},
        },
        "required": [],
        "additionalProperties": False,
    },
)
async def list_recent_runs(args: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(args.get("limit") or 10), 50)
    try:
        if args.get("automation_id"):
            user = CURRENT_USER.get()
            body = {"action": "runs",
                    "user_context": {"user_id": int(user.get("user_id") or 0),
                                     "role": int(user.get("role") or 2),
                                     "username": str(user.get("username") or "agent")},
                    "payload": {"automation_id": str(args["automation_id"]),
                                "limit": limit}}
            data, status = await _post("/automations/api/internal/manage", body)
            if status >= 400:
                return _text(f"Could not fetch runs (HTTP {status}): {data.get('error', data)}",
                             is_error=True)
            runs = data.get("runs") or []
            if not runs:
                return _text(f"Automation {args['automation_id']} has no recorded runs.")
            lines = [f"- {r.get('started_at')} — {r.get('status')} "
                     f"(v{r.get('version')}, trigger {r.get('trigger_source')}, "
                     f"exit {r.get('exit_code')})" for r in runs[:limit]]
            return _text(f"Runs for automation {args['automation_id']}:\n" + "\n".join(lines))

        data = await _get(f"/api/workflow/executions?limit={limit}")
        execs = data.get("executions") or []
        if not execs:
            return _text("No workflow executions recorded.")
        lines = []
        for e in execs[:limit]:
            lines.append(f"- exec {e.get('execution_id') or e.get('id')} — "
                         f"workflow {e.get('workflow_id')} — {e.get('status')} — "
                         f"started {e.get('started_at')}")
        return _text("Recent workflow executions:\n" + "\n".join(lines))
    except Exception as e:
        logger.error(f"list_recent_runs failed: {e}")
        return _text(f"Could not fetch runs: {e}", is_error=True)


@tool(
    "list_secret_names",
    "List the NAMES of secrets in AI Hub's encrypted Local Secrets store "
    "(values are never exposed). Check here before asking a user for a "
    "credential — it may already exist — and use these exact names in "
    "automation manifests.",
    {},
)
async def list_secret_names(args: dict[str, Any]) -> dict[str, Any]:
    # All-users rollout (james 2026-08-24): the Local Secrets store is
    # TENANT-GLOBAL (list/store take no user identity — verified in app.py
    # /workflow/secrets/*), so both secrets tools are Developer+. Revisit with
    # per-user scoping when portals open to regular users (Phase 3).
    if int((CURRENT_USER.get() or {}).get("role") or 0) < 2:
        return _text("The Local Secrets store is shared platform-wide and "
                     "requires a Developer role.", is_error=True)
    try:
        data = await _get("/workflow/secrets/list")
        secrets = data.get("secrets") or []
        if not secrets:
            return _text("The Local Secrets store is empty.")
        lines = [f"- {s['name']}" + (f" — {s['description']}" if s.get("description") else "")
                 for s in secrets]
        return _text(f"Stored secret names ({len(secrets)}):\n" + "\n".join(lines))
    except Exception as e:
        logger.error(f"list_secret_names failed: {e}")
        return _text(f"Could not list secret names: {e}", is_error=True)


@tool(
    "store_platform_secret",
    "Store an API key/password/token a user just provided into AI Hub's "
    "encrypted Local Secrets store, so automations reference it BY NAME in "
    "their manifest and the server injects the value at run time. Use this "
    "IMMEDIATELY when a user hands you a credential in chat — never hard-code "
    "it, never echo it back (in full or in part), and refer to it only by "
    "name afterwards. Name must be UPPER_SNAKE_CASE (e.g. SENDGRID_API_KEY). "
    "Platform-reserved names (API_KEY, CC_JWT_SECRET, ANTHROPIC_API_KEY, "
    "OPENAI_API_KEY, ...) and platform-managed namespaces (PORTAL_, INT_, "
    "CONN_PWD_, OAUTH_, SOL_) are never overwritten: the value is saved under a "
    "CUSTOM_ prefix instead and the result tells you the FINAL name — use that.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "UPPER_SNAKE_CASE secret name"},
            "value": {"type": "string", "description": "The secret value (never echoed)"},
            "description": {"type": "string", "description": "What this credential is for"},
            "category": {"type": "string",
                         "enum": ["api_keys", "credentials", "database", "other"]},
        },
        "required": ["name", "value"],
        "additionalProperties": False,
    },
)
async def store_platform_secret(args: dict[str, Any]) -> dict[str, Any]:
    # Same Developer+ gate as list_secret_names: the store is tenant-global,
    # and a regular user writing SENDGRID_API_KEY would clobber the shared
    # credential every automation references. Never echo the value regardless.
    if int((CURRENT_USER.get() or {}).get("role") or 0) < 2:
        return _text("Secret NOT stored: the Local Secrets store is shared "
                     "platform-wide and requires a Developer role.",
                     is_error=True)
    try:
        data, status = await _post("/workflow/secrets/store", {
            "name": str(args["name"]).strip().upper(),
            "value": str(args["value"]),
            "description": str(args.get("description") or "provided via The Agent chat"),
            "category": str(args.get("category") or "api_keys"),
        })
        if status >= 400 or not data.get("success"):
            return _text(f"Secret NOT stored (HTTP {status}): "
                         f"{data.get('error', data)}", is_error=True)
        verb = "updated" if data.get("is_update") else "stored"
        # Read-back: confirm the name now exists before claiming success.
        rb = await _get("/workflow/secrets/list")
        names = {s.get("name") for s in (rb.get("secrets") or [])}
        if data.get("name") not in names:
            return _text(f"Store call returned success but '{data.get('name')}' "
                         "does not appear in the read-back list — report this "
                         "as NOT stored.", is_error=True)
        if data.get("renamed"):
            # Reserved-name collision (2026-09-08): the server kept the value but
            # moved the name. Tell the model the FINAL name so it never refers to
            # (or believes it wrote) the platform's own slot.
            return _text(f"Secret {verb} as '{data['name']}' in the encrypted Local "
                         f"Secrets store (verified by read-back). NOTE: "
                         f"{data.get('reason') or 'the requested name is reserved'}, "
                         f"so '{data.get('requested_name')}' was NOT written — the "
                         f"value lives under '{data['name']}'. Reference it by THAT "
                         "name in manifests; the value is never shown again.")
        return _text(f"Secret '{data['name']}' {verb} in the encrypted Local "
                     "Secrets store (verified by read-back). Reference it by "
                     "this name in manifests; the value is never shown again.")
    except Exception as e:
        logger.error(f"store_platform_secret failed: {e}")
        return _text(f"Secret NOT stored: {e}", is_error=True)


AIHUB_TOOLS = [
    list_data_connections,
    get_connection_schema,
    probe_connection_query,
    search_tables,
    ask_agent,
    get_my_contact_info,
    find_user_contact,
    list_playbooks,
    list_recent_runs,
    list_secret_names,
    store_platform_secret,
]

# The combined MCP server (read + authoring tools) is assembled in brain.py to
# avoid a circular import with authoring_tools.
