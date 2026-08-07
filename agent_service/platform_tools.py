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

import json
import contextvars
from typing import Any

import httpx

from agent_config import get_base_url, AI_HUB_API_KEY, logger
from claude_agent_sdk import tool, create_sdk_mcp_server

# Per-request session envelope (set by main.py before each turn); tools read
# identity from here — never from anything the model wrote.
CURRENT_USER: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "CURRENT_USER", default={"user_id": 0, "role": 2, "username": "agent-service"}
)

_TIMEOUT = httpx.Timeout(30.0, read=120.0)


def _headers():
    return {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}


def _unwrap(data):
    """Several legacy endpoints double-encode (JSON string containing JSON)."""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except Exception:
            return data
    return data


async def _get(path: str):
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{get_base_url()}{path}", headers=_headers())
        r.raise_for_status()
        return _unwrap(r.json())


async def _post(path: str, body: dict):
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
    return out


async def _resolve_connection(ref) -> tuple:
    """Accept a numeric id or a case-insensitive connection name."""
    s = str(ref).strip()
    conns = await _connections_index()
    if s.isdigit():
        return s, None
    for c in conns:
        if str(c.get("name", "")).strip().lower() == s.lower():
            return str(c.get("id")), None
    names = ", ".join(str(c.get("name")) for c in conns if c.get("name"))
    return None, f"No connection named '{ref}'. Known connections: {names or '(none)'}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    "list_data_connections",
    "List the data connections configured in AI Hub (id, name, type, database). "
    "Call this first whenever a request involves data, to see what exists — "
    "never assume a connection name.",
    {},
)
async def list_data_connections(args: dict[str, Any]) -> dict[str, Any]:
    try:
        conns = await _connections_index()
        if not conns:
            return _text("No data connections are configured.")
        lines = [f"- id {c['id']} — {c['name']} ({c['type']}, db {c['database']})"
                 for c in conns]
        return _text("Data connections:\n" + "\n".join(lines))
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
        conn_id, err = await _resolve_connection(args["connection"])
        if err:
            return _text(err, is_error=True)
        table = (args.get("table") or "").strip()
        if not table:
            data = await _get(f"/api/discover/tables/{conn_id}")
            tables = data.get("tables") or []
            if not tables:
                return _text(f"Connection {conn_id} reports no tables.")
            lines = []
            for t in tables[:200]:
                name = t.get("TABLE_NAME") or t.get("table_name")
                doc = " (documented)" if t.get("is_documented") else ""
                lines.append(f"- {name}{doc}")
            return _text(f"Tables on connection {conn_id}:\n" + "\n".join(lines))

        from urllib.parse import quote
        path = f"/api/discover/schema/{conn_id}?table={quote(table)}"
        if args.get("column"):
            path += f"&column={quote(str(args['column']))}"
        data = await _get(path)
        if not data.get("success", True) and data.get("error"):
            return _text(f"Schema lookup failed: {data['error']}", is_error=True)
        cols = data.get("columns") or []
        lines = [f"Table {data.get('table', table)} — source: {data.get('source', '?')}"]
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
    except Exception as e:
        logger.error(f"get_connection_schema failed: {e}")
        return _text(f"Schema lookup failed: {e}", is_error=True)


@tool(
    "probe_connection_query",
    "Run ONE small read-only SELECT against a connection to verify assumptions "
    "(row counts, filter values, joins) before answering. The server enforces "
    "read-only and caps rows (~50). Zero rows is a finding — usually a filter "
    "value that doesn't exist; say so rather than guessing.",
    {
        "type": "object",
        "properties": {
            "connection": {"type": "string", "description": "Connection id or name"},
            "sql": {"type": "string", "description": "A single SELECT statement"},
        },
        "required": ["connection", "sql"],
        "additionalProperties": False,
    },
)
async def probe_connection_query(args: dict[str, Any]) -> dict[str, Any]:
    try:
        conn_id, err = await _resolve_connection(args["connection"])
        if err:
            return _text(err, is_error=True)
        data, status = await _post(f"/api/discover/query/{conn_id}",
                                   {"sql": str(args["sql"]).strip()})
        if data.get("rejected"):
            return _text(f"Query rejected by the read-only gate: {data.get('error')}",
                         is_error=True)
        if data.get("sql_error"):
            return _text(f"SQL error: {data.get('error')}", is_error=True)
        if not data.get("success"):
            return _text(f"Probe failed (HTTP {status}): {data.get('error', data)}",
                         is_error=True)
        rows = data.get("rows") or []
        cols = data.get("columns") or []
        if not rows:
            return _text("0 rows returned. This is almost always a filter value that "
                         "does not exist — verify values with get_connection_schema "
                         "before assuming the data is missing.")
        lines = [" | ".join(str(c) for c in cols)]
        for r in rows[:15]:
            lines.append(" | ".join(str(v) for v in r))
        note = f"\n({data.get('row_count')} rows returned"
        if data.get("cap_applied"):
            note += f", server cap {data.get('row_cap')} applied"
        if data.get("truncated_columns"):
            note += ", some columns truncated"
        note += ")"
        return _text("\n".join(lines) + note)
    except Exception as e:
        logger.error(f"probe_connection_query failed: {e}")
        return _text(f"Probe failed: {e}", is_error=True)


@tool(
    "ask_data_agent",
    "Ask one of AI Hub's configured data agents a natural-language question about "
    "its database; it writes and runs the SQL itself and answers with data. Use "
    "when the user wants an answer from data rather than schema exploration. "
    "Requires the numeric agent id.",
    {
        "type": "object",
        "properties": {
            "agent_id": {"type": "integer", "description": "The data agent's id"},
            "question": {"type": "string", "description": "The question to ask"},
        },
        "required": ["agent_id", "question"],
        "additionalProperties": False,
    },
)
async def ask_data_agent(args: dict[str, Any]) -> dict[str, Any]:
    try:
        data, status = await _post(f"/api/agents/{int(args['agent_id'])}/chat",
                                   {"prompt": str(args["question"]), "history": "[]"})
        if status >= 400:
            return _text(f"Agent chat failed (HTTP {status}): {data.get('error', data)}",
                         is_error=True)
        answer = data.get("response") or data.get("answer") or ""
        if not answer:
            return _text(f"Agent returned no answer (raw: {json.dumps(data)[:400]})",
                         is_error=True)
        return _text(str(answer))
    except Exception as e:
        logger.error(f"ask_data_agent failed: {e}")
        return _text(f"Agent chat failed: {e}", is_error=True)


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


AIHUB_TOOLS = [
    list_data_connections,
    get_connection_schema,
    probe_connection_query,
    ask_data_agent,
    list_playbooks,
    list_recent_runs,
]

aihub_server = create_sdk_mcp_server(name="aihub", version="0.1.0", tools=AIHUB_TOOLS)
