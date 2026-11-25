"""
The Agent's Agent Builder tools — the General Agent creation page
(/custom_agent_enhanced, "Agent Builder") exposed conversationally.

A General Agent is one of the classic chat agents users pick on the
Assistants screen: a name, an objective (system prompt), a set of core tools
(core_tools.yaml) and custom tools (tools/<name>/config.json), an optional
document-TYPE allow list for its document tools, its own knowledge documents,
and the groups it is shared with. Everything here is a thin wrapper over the
main app's EXISTING routes — the same ones the Builder page calls:

    POST /add/agent                         create / replace-all update
    POST /delete/agent                      delete
    GET  /api/agents/summary                listing (general + data agents)
    GET  /api/tools/by-category             selectable core tools
    POST /api/tool/dependencies             mandatory tools + dependency preview
    GET  /api/document-types                document types (for the allow list)
    GET  /get/agent_knowledge/<id>          knowledge documents
    POST /add/agent_knowledge               upload a knowledge document (multipart)
    POST /delete/agent_knowledge/<id>       remove one
    POST /save/permissions                  group <-> agent sharing (admin)
    GET  /api/agent-email/config/<id>       email status (read-only here)

Reads of the agent's CURRENT configuration (tools, document types, groups)
come straight from the platform DB through readthrough._db() — the same
read-only service pattern My Work uses — because the page's own listing
route (/get/agents) is session-cookie-only. Every write goes through the
main app so its in-memory agent registry reloads (load_agents) exactly as it
does for the page.

/add/agent has REPLACE-ALL semantics for tools and document types, so every
partial edit here first reads the current configuration and re-posts the
merged whole — nothing the user didn't ask to change is dropped.

Role model mirrors the page: the Builder page is @developer_required, so
create / update / delete / tools / document access / knowledge need role >= 2;
sharing an agent with groups is the Permissions page's admin-only action
(role >= 3). Listing is open, but regular users only see agents shared with
one of their groups (the same rule the Assistants screen applies).

Honesty rules carried from the other tool modules: never raise into the
agent loop; verify every write by read-back and report exactly what the
read-back shows (the platform auto-adds mandatory tools and dependencies —
report the FINAL set, not the requested one); destructive actions are
two-step (confirmed=true after the user says yes).
"""

import asyncio
import json
import os
from typing import Any, Optional

import httpx

from claude_agent_sdk import tool

from agent_config import APP_ROOT, get_base_url, logger
from platform_tools import CURRENT_USER, _text, _get, _post, _headers

# Custom tools live in APP_ROOT/<CUSTOM_TOOLS_FOLDER> (config.py default
# 'tools'); each is a directory with a config.json. Read directly — there is
# no listing route (the page renders them server-side into its template).
_CUSTOM_TOOLS_DIR = os.path.join(APP_ROOT, os.getenv("CUSTOM_TOOLS_FOLDER", "tools"))

_DEFAULT_OBJECTIVE = ("You are a helpful AI assistant named {name}. "
                      "Help users with their requests.")

# Knowledge uploads run the document pipeline synchronously inside the
# request (extraction + AI typing); large PDFs take minutes.
_KNOWLEDGE_READ_TIMEOUT = float(os.getenv("AGENT_KNOWLEDGE_UPLOAD_TIMEOUT", "1800"))

_LIST_CAP = 60


# ---------------------------------------------------------------------------
# Pure helpers (kept free of I/O so the unit pack can test the semantics)
# ---------------------------------------------------------------------------

def merge_names(current: list, requested: list, mode: str) -> list:
    """Replace / add / remove semantics over a name list, order-preserving,
    de-duplicated, case-preserving (tool and document-type names are exact)."""
    cur = list(dict.fromkeys(str(x) for x in (current or []) if str(x).strip()))
    req = list(dict.fromkeys(str(x).strip() for x in (requested or []) if str(x).strip()))
    mode = (mode or "replace").lower()
    if mode == "replace":
        return req
    if mode == "add":
        return cur + [r for r in req if r not in cur]
    if mode == "remove":
        return [c for c in cur if c not in req]
    raise ValueError(f"mode must be replace, add or remove (got '{mode}')")


def match_agent(ref, agents: list) -> tuple:
    """Resolve an id-or-name reference against [{id, name, ...}] rows.
    Returns (row, None) | (None, error_text). Names match case-insensitively;
    an ambiguous name lists the candidates instead of guessing."""
    s = str(ref if ref is not None else "").strip()
    if not s:
        return None, "Give me the agent's id or name."
    if s.isdigit():
        for a in agents:
            if int(a["id"]) == int(s):
                return a, None
        return None, f"No agent with id {s} exists."
    hits = [a for a in agents if str(a.get("name") or "").strip().lower() == s.lower()]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        ids = ", ".join(f"id {a['id']}" for a in hits)
        return None, (f"{len(hits)} agents are named '{s}' ({ids}) — tell me "
                      "which id you mean.")
    near = [a for a in agents if s.lower() in str(a.get("name") or "").lower()]
    hint = ""
    if near:
        hint = " Similar names: " + "; ".join(
            f"{a['name']} (id {a['id']})" for a in near[:6])
    return None, f"No agent named '{s}'.{hint}"


def unknown_names(requested: list, known: set) -> list:
    return [r for r in (requested or []) if r not in known]


def suggest(name: str, known, limit: int = 4) -> list:
    """Cheap near-match helper for typos in tool/doc-type names."""
    n = str(name).lower().replace(" ", "_")
    scored = []
    for k in known:
        kl = str(k).lower().replace(" ", "_")
        if n in kl or kl in n:
            scored.append((0, k))
        else:
            common = len(set(n.split("_")) & set(kl.split("_")))
            if common:
                scored.append((1.0 / common, k))
    scored.sort(key=lambda t: (t[0], str(t[1]).lower()))
    return [k for _, k in scored[:limit]]


def visible_to(agent: dict, role: int, user_group_ids: set) -> bool:
    """Listing rule: Developer+ sees every agent (the Builder page does);
    regular users see only agents shared with one of their groups."""
    if int(role) >= 2:
        return True
    return bool(set(int(g) for g in (agent.get("group_ids") or [])) & user_group_ids)


# ---------------------------------------------------------------------------
# DB read-through (agent configuration as stored)
# ---------------------------------------------------------------------------

def _db_read(fn):
    """Run a blocking pyodbc read on a worker thread; fn(cursor) -> value."""
    import readthrough
    conn = readthrough._db()
    try:
        cur = conn.cursor()
        try:
            return fn(cur)
        finally:
            cur.close()
    finally:
        conn.close()


async def _sql(fn):
    return await asyncio.to_thread(_db_read, fn)


async def _rt(op: str, sql_fn, **params):
    """HTTP read-through on the main app first (works on installs, where this
    process has no database credentials), the direct-SQL thunk otherwise."""
    import readthrough

    def _go():
        return readthrough.fetch_or_sql(op, lambda: _db_read(sql_fn), **params)
    return await asyncio.to_thread(_go)


def _q_agents(cur) -> list:
    cur.execute("""
        SELECT a.id, a.description, a.objective, a.enabled,
               ISNULL(a.is_data_agent, 0), ISNULL(a.allow_personal_connections, 1),
               a.create_date
        FROM [dbo].[Agents] a
        ORDER BY a.id""")
    rows = []
    for r in cur.fetchall():
        rows.append({"id": int(r[0]), "name": r[1] or "", "objective": r[2] or "",
                     "enabled": bool(r[3]), "is_data_agent": bool(r[4]),
                     "allow_personal_connections": bool(r[5]),
                     "created": r[6].strftime("%Y-%m-%d") if r[6] else None})
    cur.execute("SELECT agent_id, group_id FROM [dbo].[AgentGroups]")
    by_agent: dict = {}
    for aid, gid in cur.fetchall():
        by_agent.setdefault(int(aid), []).append(int(gid))
    # Data agents: the connection(s) they query — what lets "the ERPDB agent"
    # resolve to an id for ask_agent.
    conns: dict = {}
    try:
        cur.execute("SELECT t.agent_id, c.connection_name FROM [dbo].[AgentConnections] t "
                    "JOIN [dbo].[Connections] c ON c.id = t.connection_id")
        for aid, cname in cur.fetchall():
            conns.setdefault(int(aid), []).append(str(cname))
    except Exception:
        pass
    for a in rows:
        a["group_ids"] = by_agent.get(a["id"], [])
        a["connections"] = conns.get(a["id"], [])
    return rows


async def _all_agents() -> list:
    return await _rt("agents", _q_agents)


# ---------------------------------------------------------------------------
# Agents as sources of knowledge (2026-11-21)
# ---------------------------------------------------------------------------
# WHY: a General Agent can hold its OWN knowledge documents (manuals, policies,
# customer requirements). search_documents does not search them — they belong to
# the agent — and list_agents showed a name and nothing about what an agent
# knows. So a question whose answer sits in an agent's knowledge ended with "I
# found nothing among the documents" after a document and database hunt, and the
# agent that holds the answer was never asked (measured: a question answered
# correctly by a compliance agent, 142 s of other tools, no ask_agent).
# This is the missing directory: which agents the signed-in user can ask, what
# each is about, and what documents it holds. list_agents shows it, and
# search_documents adds it to every result, so it is in front of the model at
# the moment it would otherwise conclude "not found".

_KNOWLEDGE_NAMES_SHOWN = 6        # document names shown per agent (the count is always exact)
_KNOWLEDGE_INLINE_AGENTS = 25     # agents spelled out inside a search result; the rest are counted and listable
_ABOUT_CHARS = 220

_UPLOAD_PREFIX_LEN = 37           # "<uuid4>_" that the knowledge upload puts before the file name


def display_filename(name: str) -> str:
    """File name as the user knows it: without the '<uuid>_' the knowledge upload
    route prefixes to keep stored names unique."""
    s = str(name or "").strip()
    head = s[:_UPLOAD_PREFIX_LEN]
    if (len(s) > _UPLOAD_PREFIX_LEN and head[-1] == "_"
            and [len(p) for p in head[:-1].split("-")] == [8, 4, 4, 4, 12]
            and all(c in "0123456789abcdefABCDEF-" for c in head[:-1])):
        return s[_UPLOAD_PREFIX_LEN:]
    return s


def about_line(objective: str) -> str:
    """Start of an agent's objective, on one line — what the agent says it is for."""
    s = " ".join(str(objective or "").split())
    return s if len(s) <= _ABOUT_CHARS else s[:_ABOUT_CHARS].rstrip() + "…"


def knowledge_sources(agents: list, knowledge: list, role: int, user_group_ids: set) -> list:
    """Agents the user can ask that hold knowledge documents: enabled General
    Agents, visible to this user, with at least one document. Most documents
    first. `knowledge` = [{agent_id, documents, names}] (see _q_knowledge_agents)."""
    by_agent = {int(k["agent_id"]): k for k in (knowledge or []) if int(k.get("documents") or 0) > 0}
    out = []
    for a in agents or []:
        k = by_agent.get(int(a["id"]))
        if not k or a.get("is_data_agent") or not a.get("enabled"):
            continue
        if not visible_to(a, role, user_group_ids):
            continue
        out.append({"id": int(a["id"]), "name": a.get("name") or "", "about": about_line(a.get("objective")),
                    "documents": int(k["documents"]),
                    "names": [display_filename(n) for n in (k.get("names") or [])]})
    out.sort(key=lambda s: (-s["documents"], s["id"]))
    return out


def knowledge_line(src: dict) -> str:
    """'<N> knowledge documents (e.g. a; b; … +M more)' for one agent."""
    n = int(src["documents"])
    names = [x for x in (src.get("names") or []) if x]
    shown = "; ".join(names[:_KNOWLEDGE_NAMES_SHOWN])
    more = n - min(len(names), _KNOWLEDGE_NAMES_SHOWN)
    tail = ""
    if shown:
        tail = f" ({'e.g. ' if more > 0 else ''}{shown}{f'; +{more} more' if more > 0 else ''})"
    return f"{n} knowledge document{'s' if n != 1 else ''}{tail}"


def format_knowledge_sources(sources: list, limit: Optional[int] = None) -> str:
    """One block of lines, one agent each: id, name, what it is about, its documents."""
    rows = sources if limit is None else sources[:limit]
    lines = []
    for s in rows:
        line = f"- id {s['id']} — {s['name']}: {knowledge_line(s)}"
        if s.get("about"):
            line += f"\n    about: {s['about']}"
        lines.append(line)
    if limit is not None and len(sources) > limit:
        lines.append(f"… {len(sources) - limit} more agents hold knowledge documents — "
                     "list_agents with with_knowledge=true shows all of them.")
    return "\n".join(lines)


def _q_knowledge_agents(user_id):
    """Per agent: how many ACTIVE knowledge documents this user's questions can
    draw on (agent-level ones plus the user's own — the same rule the agent's
    knowledge search applies) and the names of the newest ones."""
    def fn(cur):
        cur.execute("""
            SELECT ak.agent_id, d.filename
            FROM [dbo].[AgentKnowledge] ak
            JOIN [dbo].[Documents] d ON d.document_id = ak.document_id
            WHERE ak.is_active = 1
              AND (ISNULL(ak.added_by, 'USER') = 'USER' OR ISNULL(ak.added_by, 'USER') = ?)
            ORDER BY ak.agent_id, ak.knowledge_id DESC""", str(user_id))
        out: dict = {}
        for aid, fname in cur.fetchall():
            row = out.setdefault(int(aid), {"agent_id": int(aid), "documents": 0, "names": []})
            row["documents"] += 1
            if len(row["names"]) < _KNOWLEDGE_NAMES_SHOWN:
                row["names"].append(str(fname or ""))
        return list(out.values())
    return fn


async def knowledge_sources_for_user() -> list:
    """The knowledge directory for the signed-in user. Never raises: a directory
    that cannot be read is an empty one (logged), not a failed turn."""
    try:
        user = CURRENT_USER.get() or {}
        role = int(user.get("role") or 0)
        uid = int(user.get("user_id") or 0)
        groups: set = set()
        if role < 2:
            import readthrough
            groups = set(await asyncio.to_thread(readthrough.user_group_ids, uid))
        agents = await _all_agents()
        knowledge = await _rt("knowledge_agents", _q_knowledge_agents(uid), user_id=uid)
        return knowledge_sources(agents, knowledge, role, groups)
    except Exception as e:
        logger.warning(f"knowledge directory unavailable: {e}")
        return []


def _q_agent_detail(agent_id: int):
    def fn(cur):
        cur.execute("""
            SELECT a.id, a.description, a.objective, a.enabled,
                   ISNULL(a.is_data_agent, 0), ISNULL(a.allow_personal_connections, 1),
                   a.create_date
            FROM [dbo].[Agents] a WHERE a.id = ?""", int(agent_id))
        r = cur.fetchone()
        if not r:
            return None
        agent = {"id": int(r[0]), "name": r[1] or "", "objective": r[2] or "",
                 "enabled": bool(r[3]), "is_data_agent": bool(r[4]),
                 "allow_personal_connections": bool(r[5]),
                 "created": r[6].strftime("%Y-%m-%d %H:%M") if r[6] else None}
        cur.execute("SELECT tool_name, ISNULL(custom_tool, 0) FROM [dbo].[AgentTools] "
                    "WHERE agent_id = ? ORDER BY tool_name", int(agent_id))
        core, custom = [], []
        for name, is_custom in cur.fetchall():
            (custom if is_custom else core).append(str(name))
        agent["core_tools"], agent["custom_tools"] = core, custom
        cur.execute("SELECT document_type FROM [dbo].[AgentDocumentTypes] "
                    "WHERE agent_id = ? ORDER BY document_type", int(agent_id))
        agent["document_types"] = [str(r[0]) for r in cur.fetchall()]
        cur.execute("SELECT g.id, g.group_name FROM [dbo].[AgentGroups] ag "
                    "JOIN [dbo].[Groups] g ON g.id = ag.group_id "
                    "WHERE ag.agent_id = ? ORDER BY g.group_name", int(agent_id))
        agent["groups"] = [{"id": int(r[0]), "name": str(r[1])} for r in cur.fetchall()]
        agent["group_ids"] = [g["id"] for g in agent["groups"]]
        return agent
    return fn


async def _fetch_agent(agent_id: int) -> Optional[dict]:
    return await _rt("agent_detail", _q_agent_detail(agent_id), agent_id=int(agent_id))


async def _resolve(ref) -> tuple:
    """(agent_detail, err) for an id-or-name reference."""
    try:
        agents = await _all_agents()
    except Exception as e:
        return None, f"Could not read the agent list: {e}"
    row, err = match_agent(ref, agents)
    if err:
        return None, err
    detail = await _fetch_agent(row["id"])
    if not detail:
        return None, f"Agent {row['id']} vanished between lookup and read."
    return detail, None


def _q_groups(cur) -> list:
    cur.execute("SELECT id, group_name FROM [dbo].[Groups] ORDER BY group_name")
    return [{"id": int(r[0]), "name": str(r[1])} for r in cur.fetchall()]


def _q_group_membership(group_id: int):
    def fn(cur):
        cur.execute("SELECT user_id FROM [dbo].[UserGroups] WHERE group_id = ?", int(group_id))
        users = [int(r[0]) for r in cur.fetchall()]
        cur.execute("SELECT agent_id FROM [dbo].[AgentGroups] WHERE group_id = ?", int(group_id))
        agents = [int(r[0]) for r in cur.fetchall()]
        return users, agents
    return fn


def _q_knowledge_row(knowledge_id: int):
    def fn(cur):
        cur.execute("""
            SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description,
                   d.filename, d.document_type, d.page_count, ak.is_active
            FROM [dbo].[AgentKnowledge] ak
            LEFT JOIN [dbo].[Documents] d ON d.document_id = ak.document_id
            WHERE ak.knowledge_id = ?""", int(knowledge_id))
        r = cur.fetchone()
        if not r:
            return None
        return {"knowledge_id": int(r[0]), "agent_id": int(r[1]), "document_id": r[2],
                "description": r[3] or "", "filename": r[4] or "",
                "document_type": r[5] or "", "page_count": r[6],
                "is_active": bool(r[7]) if r[7] is not None else True}
    return fn


# ---------------------------------------------------------------------------
# Catalogs (core tools, custom tools, document types)
# ---------------------------------------------------------------------------

async def _core_catalog() -> tuple:
    """(categories: {name: {description, tools:[{name, display_name, description}]}},
    mandatory: [names]) from the main app's dependency manager."""
    cats: dict = {}
    data = await _get("/api/tools/by-category")
    if isinstance(data, dict) and data.get("status") == "success":
        cats = data.get("categories") or {}
    mandatory: list = []
    try:
        dep, status = await _post("/api/tool/dependencies", {"tools": []}, timeout=30)
        if status < 400 and isinstance(dep, dict):
            mandatory = [m.get("name") for m in (dep.get("mandatory_tools") or [])
                         if m.get("name")]
    except Exception:
        pass
    return cats, mandatory


def _core_names(cats: dict) -> set:
    out = set()
    for c in cats.values():
        for t in (c.get("tools") or []):
            if t.get("name"):
                out.add(str(t["name"]))
    return out


def _custom_catalog() -> tuple:
    """([{name, display_name, description}], readable: bool)."""
    tools = []
    if not os.path.isdir(_CUSTOM_TOOLS_DIR):
        return tools, False
    try:
        for name in sorted(os.listdir(_CUSTOM_TOOLS_DIR)):
            d = os.path.join(_CUSTOM_TOOLS_DIR, name)
            if not os.path.isdir(d):
                continue
            display, desc = name.replace("_", " ").title(), ""
            cfg_path = os.path.join(d, "config.json")
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as fh:
                        cfg = json.load(fh)
                    display = cfg.get("display_name") or display
                    desc = cfg.get("description") or ""
                except Exception:
                    desc = "(config.json unreadable)"
            tools.append({"name": name, "display_name": display, "description": desc})
        return tools, True
    except Exception as e:
        logger.warning(f"custom tools folder unreadable: {e}")
        return tools, False


async def _document_types() -> list:
    data = await _get("/api/document-types")
    if isinstance(data, list):
        return [{"name": str(t.get("name")), "count": int(t.get("count") or 0)}
                for t in data if t.get("name")]
    return []


# ---------------------------------------------------------------------------
# Save (the page's /add/agent) + read-back
# ---------------------------------------------------------------------------

async def _save_agent(agent_id: int, name: str, objective: str, enabled: bool,
                      core_tools: list, custom_tools: list, document_types: list,
                      allow_personal_connections: bool) -> tuple:
    """POST /add/agent (id 0 = create). Returns (new_or_same_id, err)."""
    body = {
        "agent_id": int(agent_id or 0),
        "agent_description": name,
        "agent_objective": objective,
        "agent_enabled": 1 if enabled else 0,
        "tool_names": list(custom_tools or []),
        "core_tool_names": list(core_tools or []),
        "allowed_document_types": list(document_types or []),
        "allow_personal_connections": bool(allow_personal_connections),
    }
    data, status = await _post("/add/agent", body, timeout=180)
    if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
        msg = data.get("message", data) if isinstance(data, dict) else data
        return None, f"Save FAILED (HTTP {status}): {msg} — nothing changed."
    try:
        return int(data.get("message")), None
    except (TypeError, ValueError):
        return None, f"Save reported success but returned no agent id ({data!r})."


def _role_gate(min_role: int, what: str):
    user = CURRENT_USER.get() or {}
    if int(user.get("role") or 0) < min_role:
        who = "an admin" if min_role >= 3 else "a Developer (or admin)"
        return _text(f"{what} requires {who} role — this user can't do it "
                     "through The Agent. An admin can do it on the "
                     + ("Permissions page." if min_role >= 3 else "Agent Builder page."),
                     is_error=True)
    return None


def _general_only(agent: dict):
    if agent.get("is_data_agent"):
        return _text(f"Agent {agent['id']} '{agent['name']}' is a DATA agent "
                     "(SQL-bound assistant). These tools manage General Agents "
                     "only — data agents are configured on the Data Assistants "
                     "page.", is_error=True)
    return None


def _fmt_agent(agent: dict, knowledge: Optional[list] = None,
               email: Optional[dict] = None) -> str:
    kind = "data agent" if agent.get("is_data_agent") else "general agent"
    state = "enabled" if agent.get("enabled") else "DISABLED"
    lines = [f"Agent {agent['id']} — \"{agent['name']}\" ({kind}, {state}"
             + (f", created {agent['created']}" if agent.get("created") else "") + ")"]
    obj = (agent.get("objective") or "").strip()
    # WHOLE: changing an objective means sending its full new text, so the model
    # has to see all of the current one. Cut at 600 characters, a longer objective
    # could only be written back shortened.
    lines.append("Objective: " + obj)
    lines.append("Personal MCP connections: "
                 + ("allowed" if agent.get("allow_personal_connections") else "not allowed"))
    core = agent.get("core_tools") or []
    custom = agent.get("custom_tools") or []
    lines.append(f"Core tools ({len(core)}): " + (", ".join(core) if core else "(none)"))
    lines.append(f"Custom tools ({len(custom)}): " + (", ".join(custom) if custom else "(none)"))
    dts = agent.get("document_types") or []
    lines.append("Document-type access: "
                 + ("unrestricted (all document types)" if not dts
                    else "restricted to " + ", ".join(dts)))
    if knowledge is not None:
        if knowledge:
            lines.append(f"Knowledge documents ({len(knowledge)}):")
            for k in knowledge[:40]:
                extra = ", ".join(str(x) for x in (k.get("document_type"),
                                                   f"{k.get('page_count')} pages"
                                                   if k.get("page_count") else None) if x)
                attrs = k.get("attributes") or {}
                attr_txt = ("; ".join(f"{n}: {v}" for n, v in attrs.items())
                            if isinstance(attrs, dict) and attrs else "")
                lines.append(f"  - [knowledge_id {k.get('knowledge_id')}] "
                             f"{k.get('filename')}" + (f" ({extra})" if extra else "")
                             + (f" — {k.get('description')}" if k.get("description")
                                and k.get("description") != k.get("filename") else "")
                             + (f" [attributes — {attr_txt}]" if attr_txt else ""))
            if len(knowledge) > 40:
                lines.append(f"  … {len(knowledge) - 40} more")
        else:
            lines.append("Knowledge documents: (none)")
    groups = agent.get("groups") or []
    lines.append("Shared with groups: "
                 + (", ".join(f"{g['name']} (id {g['id']})" for g in groups)
                    if groups else "(none — visible to developers/admins only)"))
    if email is not None:
        lines.append("Email: " + email.get("summary", "unknown"))
    return "\n".join(lines)


async def _knowledge_list(agent_id: int) -> Optional[list]:
    try:
        data = await _get(f"/get/agent_knowledge/{int(agent_id)}")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"agent knowledge list unavailable: {e}")
        return None


async def _email_status(agent_id: int) -> dict:
    try:
        data = await _get(f"/api/agent-email/config/{int(agent_id)}")
    except Exception:
        return {"summary": "unknown (email feature not reachable)"}
    if not isinstance(data, dict) or not data.get("configured"):
        return {"summary": "not configured (set up on the agent's Email page)"}
    cfg = data.get("config") or {}
    bits = [str(cfg.get("email_address") or "address unknown")]
    bits.append("active" if cfg.get("is_active") else "disabled")
    if cfg.get("inbound_enabled"):
        bits.append("inbound on")
    if cfg.get("auto_respond_enabled"):
        bits.append("auto-respond on")
    return {"summary": ", ".join(bits)}


# ---------------------------------------------------------------------------
# Tools — reads
# ---------------------------------------------------------------------------

@tool(
    "list_agents",
    "List AI Hub's agents: the General Agents built on the Agent Builder page "
    "and, with kind='data' or 'all', the SQL-bound data agents (shown with the "
    "connections they query, e.g. 'data: ERPDB'). Shows id, name, enabled state "
    "and the groups each is shared with, and for an agent that holds its own "
    "knowledge documents how many and which. Call this BEFORE ask_agent or before "
    "referring to an agent by name — never assume an id. Developers/admins see "
    "every agent; regular users see only agents shared with their groups.\n"
    "with_knowledge=true answers 'WHO KNOWS ABOUT THIS?': it lists only the "
    "agents that hold their own knowledge documents (manuals, policies, "
    "procedures, customer requirements — documents search_documents does NOT "
    "search), each with what it is about and its documents. Use it to find the "
    "agent to ask before you tell a user that something is not in the documents.",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["general", "data", "all"],
                     "description": "Which agents to list (default general)"},
            "name_contains": {"type": "string",
                              "description": "Case-insensitive substring filter on the name"},
            "include_disabled": {"type": "boolean",
                                 "description": "Include disabled agents (default true)"},
            "with_knowledge": {"type": "boolean",
                               "description": "Only agents that hold their own knowledge "
                                              "documents, with what each is about and its documents"},
        },
        "additionalProperties": False,
    },
)
async def list_agents(args: dict[str, Any]) -> dict[str, Any]:
    try:
        kind = str(args.get("kind") or "general").lower()
        needle = str(args.get("name_contains") or "").strip().lower()
        include_disabled = args.get("include_disabled", True)
        if args.get("with_knowledge"):
            sources = await knowledge_sources_for_user()
            if needle:
                sources = [s for s in sources if needle in s["name"].lower()]
            if not sources:
                return _text("No agent you can ask holds knowledge documents"
                             + (f" (name matching '{needle}')" if needle else "") + ".")
            return _text(f"Agents that hold their own knowledge documents ({len(sources)}) — "
                         "ask one with ask_agent (it answers from its documents and cites them):\n"
                         + format_knowledge_sources(sources))
        user = CURRENT_USER.get() or {}
        role = int(user.get("role") or 0)
        groups: set = set()
        if role < 2:
            import readthrough
            groups = set(readthrough.user_group_ids(int(user.get("user_id") or 0)))
        agents = await _all_agents()
        try:
            held = {int(k["agent_id"]): k for k in await _rt(
                "knowledge_agents", _q_knowledge_agents(int(user.get("user_id") or 0)),
                user_id=int(user.get("user_id") or 0))}
        except Exception as e:
            logger.warning(f"knowledge counts unavailable for list_agents: {e}")
            held = {}
        rows = []
        for a in agents:
            if kind == "general" and a["is_data_agent"]:
                continue
            if kind == "data" and not a["is_data_agent"]:
                continue
            if needle and needle not in a["name"].lower():
                continue
            if not include_disabled and not a["enabled"]:
                continue
            if not visible_to(a, role, groups):
                continue
            rows.append(a)
        if not rows:
            scope = "" if role >= 2 else " shared with your groups"
            return _text(f"No {kind if kind != 'all' else ''} agents{scope}"
                         + (f" matching '{needle}'" if needle else "") + ".")
        rows.sort(key=lambda a: a["id"], reverse=True)
        lines = []
        for a in rows[:_LIST_CAP]:
            tag = "general"
            if a["is_data_agent"]:
                tag = "data" + (f": {', '.join(a['connections'])}" if a.get("connections") else "")
            st = "" if a["enabled"] else ", DISABLED"
            shared = (f", groups {a['group_ids']}" if a.get("group_ids") else "")
            k = held.get(a["id"])
            knows = ""
            if k and not a["is_data_agent"]:
                knows = " — " + knowledge_line({
                    "documents": k["documents"],
                    "names": [display_filename(n) for n in (k.get("names") or [])]})
            lines.append(f"- id {a['id']} — {a['name']} ({tag}{st}{shared}){knows}")
        more = ""
        if len(rows) > _LIST_CAP:
            more = (f"\n… {len(rows) - _LIST_CAP} more not shown (newest first) — "
                    "narrow with name_contains.")
        note = "" if role >= 2 else "\n(Showing only agents shared with your groups.)"
        return _text(f"Agents ({len(rows)}):\n" + "\n".join(lines) + more + note)
    except Exception as e:
        logger.error(f"list_agents failed: {e}")
        return _text(f"Could not list agents: {e}", is_error=True)


@tool(
    "get_agent_config",
    "Show how a General Agent is configured, exactly as the Agent Builder page "
    "shows it: objective, core and custom tools, document-type access, knowledge "
    "documents (with knowledge_ids), the groups it is shared with, and email "
    "status. Read this BEFORE changing an agent so you can describe what will "
    "change. Accepts the agent id or its exact name.",
    {
        "type": "object",
        "properties": {"agent": {"type": "string", "description": "Agent id or name"}},
        "required": ["agent"],
        "additionalProperties": False,
    },
)
async def get_agent_config(args: dict[str, Any]) -> dict[str, Any]:
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        user = CURRENT_USER.get() or {}
        role = int(user.get("role") or 0)
        if role < 2:
            import readthrough
            groups = set(readthrough.user_group_ids(int(user.get("user_id") or 0)))
            if not visible_to(agent, role, groups):
                return _text(f"Agent {agent['id']} is not shared with any of your "
                             "groups.", is_error=True)
        knowledge = await _knowledge_list(agent["id"])
        email = await _email_status(agent["id"])
        return _text(_fmt_agent(agent, knowledge, email))
    except Exception as e:
        logger.error(f"get_agent_config failed: {e}")
        return _text(f"Could not read the agent: {e}", is_error=True)


@tool(
    "get_agent_builder_options",
    "The choices the Agent Builder page offers: selectable CORE tools by "
    "category (exact names to pass to set_agent_tools), the mandatory tools "
    "every agent gets automatically, installed CUSTOM tools, the document TYPES "
    "an agent can be restricted to, and the user groups an agent can be shared "
    "with. Call this before configuring tools or document access — never guess "
    "a tool or type name.",
    {
        "type": "object",
        "properties": {
            "section": {"type": "string",
                        "enum": ["all", "tools", "document_types", "groups"],
                        "description": "Which options to show (default all)"},
        },
        "additionalProperties": False,
    },
)
async def get_agent_builder_options(args: dict[str, Any]) -> dict[str, Any]:
    section = str(args.get("section") or "all").lower()
    out = []
    try:
        if section in ("all", "tools"):
            cats, mandatory = await _core_catalog()
            if not cats:
                out.append("Core tools: the catalog could not be read from the "
                           "platform (tool names cannot be validated right now).")
            else:
                out.append("CORE TOOLS (selectable), by category:")
                for cname, c in cats.items():
                    out.append(f"  [{cname}] {c.get('description') or ''}".rstrip())
                    for t in (c.get("tools") or []):
                        d = str(t.get("description") or "").strip().replace("\n", " ")
                        if len(d) > 110:
                            d = d[:110] + "…"
                        out.append(f"    - {t.get('name')} — {t.get('display_name') or ''}"
                                   + (f": {d}" if d else ""))
            if mandatory:
                out.append("Mandatory tools (added to every agent automatically): "
                           + ", ".join(mandatory))
            customs, readable = _custom_catalog()
            if not readable:
                out.append("CUSTOM TOOLS: folder not readable from this service "
                           "(names are passed through unvalidated).")
            elif not customs:
                out.append("CUSTOM TOOLS: none installed.")
            else:
                out.append(f"CUSTOM TOOLS ({len(customs)}, exact names):")
                for t in customs:
                    d = str(t.get("description") or "").strip().replace("\n", " ")
                    if len(d) > 110:
                        d = d[:110] + "…"
                    out.append(f"  - {t['name']}" + (f" — {d}" if d else ""))
        if section in ("all", "document_types"):
            types = await _document_types()
            if types:
                out.append("DOCUMENT TYPES (for set_agent_document_types; count = "
                           "documents of that type in the store):")
                out.append("  " + ", ".join(f"{t['name']} ({t['count']})" for t in types))
            else:
                out.append("DOCUMENT TYPES: none yet — types appear as documents are "
                           "imported; an agent with no restriction sees all types.")
        if section in ("all", "groups"):
            groups = await _rt("groups", _q_groups)
            if groups:
                out.append("GROUPS (for assign_agent_groups): "
                           + ", ".join(f"{g['name']} (id {g['id']})" for g in groups))
            else:
                out.append("GROUPS: none defined.")
        return _text("\n".join(out) if out else "Nothing to show.")
    except Exception as e:
        logger.error(f"get_agent_builder_options failed: {e}")
        return _text(f"Could not read the builder options: {e}", is_error=True)


# ---------------------------------------------------------------------------
# Tools — writes
# ---------------------------------------------------------------------------

async def _validate_tools(core: list, custom: list,
                          already: Optional[list] = None) -> Optional[str]:
    """Return an error string if any requested name is not selectable.

    `already` = the agent's CURRENT core tools: the platform stores mandatory
    tools and auto-added dependencies that are NOT user-selectable (e.g.
    wait_seconds), and a replace that keeps them must not be refused."""
    problems = []
    if core:
        cats, mand = await _core_catalog()
        if cats:
            known = _core_names(cats) | set(mand or []) | set(already or [])
            bad = unknown_names(core, known)
            for b in bad:
                near = suggest(b, known)
                problems.append(f"core tool '{b}' is not a selectable tool"
                                + (f" (did you mean {', '.join(near)}?)" if near else ""))
    if custom:
        customs, readable = _custom_catalog()
        if readable:
            known = {t["name"] for t in customs}
            bad = unknown_names(custom, known)
            for b in bad:
                near = suggest(b, known)
                problems.append(f"custom tool '{b}' is not installed"
                                + (f" (did you mean {', '.join(near)}?)" if near else ""))
    if problems:
        return ("Nothing saved — " + "; ".join(problems)
                + ". Call get_agent_builder_options for the exact names.")
    return None


async def _validate_doc_types(types: list) -> Optional[str]:
    if not types:
        return None
    known_rows = await _document_types()
    known = {t["name"] for t in known_rows}
    if not known:
        return ("Nothing saved — no document types exist in the store yet, so a "
                "restriction can't be set (types appear as documents are imported).")
    bad = unknown_names(types, known)
    if bad:
        near = {b: suggest(b, known) for b in bad}
        parts = [f"'{b}'" + (f" (did you mean {', '.join(n)}?)" if n else "")
                 for b, n in near.items()]
        return ("Nothing saved — unknown document type(s): " + ", ".join(parts)
                + ". Known types: " + ", ".join(sorted(known)) + ".")
    return None


def _readback_report(before: Optional[dict], after: dict, requested_core: list,
                     requested_custom: list, requested_types: list) -> str:
    """Honest post-save summary: what the platform actually stored."""
    lines = []
    core_after = after.get("core_tools") or []
    custom_after = after.get("custom_tools") or []
    auto = [t for t in core_after if t not in (requested_core or [])]
    missing_core = [t for t in (requested_core or []) if t not in core_after]
    missing_custom = [t for t in (requested_custom or []) if t not in custom_after]
    lines.append(f"Core tools now ({len(core_after)}): "
                 + (", ".join(core_after) if core_after else "(none)"))
    if auto:
        lines.append("  (added automatically by the platform — mandatory tools / "
                     "dependencies: " + ", ".join(auto) + ")")
    lines.append(f"Custom tools now ({len(custom_after)}): "
                 + (", ".join(custom_after) if custom_after else "(none)"))
    dts = after.get("document_types") or []
    lines.append("Document-type access now: "
                 + ("unrestricted" if not dts else "restricted to " + ", ".join(dts)))
    if missing_core or missing_custom:
        lines.append("WARNING — read-back does NOT contain: "
                     + ", ".join(missing_core + missing_custom)
                     + ". Report this as UNVERIFIED.")
    if sorted(dts) != sorted(requested_types or []):
        lines.append("WARNING — document-type read-back differs from what was "
                     "requested. Report this as UNVERIFIED.")
    return "\n".join(lines)


@tool(
    "create_general_agent",
    "Create a new General Agent (a chat agent on the Assistants screen) exactly "
    "as the Agent Builder page does. Only `name` is required: without an "
    "objective the platform's default objective is used (say which one you "
    "used); tools, document access, knowledge and group sharing can be added "
    "afterwards with the other agent-builder tools — or passed here. Tool and "
    "document-type names must be EXACT (get_agent_builder_options). Refuses to "
    "silently duplicate an existing name (pass allow_duplicate_name=true after "
    "the user confirms they want a second agent with that name). Developer+ "
    "only. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Agent name (shown to users)"},
            "objective": {"type": "string",
                          "description": "System prompt / objective (optional)"},
            "core_tools": {"type": "array", "items": {"type": "string"},
                           "description": "Exact core tool names (optional)"},
            "custom_tools": {"type": "array", "items": {"type": "string"},
                             "description": "Exact custom tool names (optional)"},
            "allowed_document_types": {"type": "array", "items": {"type": "string"},
                                       "description": "Restrict document tools to "
                                                      "these types; empty = all"},
            "allow_personal_connections": {"type": "boolean",
                                           "description": "Let the calling user's "
                                                          "personal MCP connections be "
                                                          "used (default true)"},
            "enabled": {"type": "boolean", "description": "Default true"},
            "allow_duplicate_name": {"type": "boolean"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def create_general_agent(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Creating an agent")
    if gate:
        return gate
    try:
        name = str(args.get("name") or "").strip()
        if not name:
            return _text("The agent needs a name.", is_error=True)
        if len(name) > 200:
            return _text("Agent names are limited to 200 characters.", is_error=True)
        objective = str(args.get("objective") or "").strip()
        used_default = False
        if not objective:
            objective = _DEFAULT_OBJECTIVE.format(name=name)
            used_default = True
        core = [str(t).strip() for t in (args.get("core_tools") or []) if str(t).strip()]
        custom = [str(t).strip() for t in (args.get("custom_tools") or []) if str(t).strip()]
        types = [str(t).strip() for t in (args.get("allowed_document_types") or [])
                 if str(t).strip()]
        apc = bool(args.get("allow_personal_connections", True))
        enabled = bool(args.get("enabled", True))

        agents = await _all_agents()
        dupes = [a for a in agents if a["name"].strip().lower() == name.lower()]
        if dupes and not args.get("allow_duplicate_name"):
            ids = ", ".join(f"id {a['id']}" + (" (data agent)" if a["is_data_agent"] else "")
                            for a in dupes)
            return _text(f"An agent named '{name}' already exists ({ids}). Nothing "
                         "created. Use update_general_agent / set_agent_tools to "
                         "change it, pick a different name, or — if the user "
                         "really wants a second agent with the same name — call "
                         "again with allow_duplicate_name=true.", is_error=True)
        err = await _validate_tools(core, custom)
        if err:
            return _text(err, is_error=True)
        err = await _validate_doc_types(types)
        if err:
            return _text(err, is_error=True)

        new_id, err = await _save_agent(0, name, objective, enabled, core, custom,
                                        types, apc)
        if err:
            return _text(err, is_error=True)
        after = await _fetch_agent(new_id)
        if not after:
            return _text(f"The platform returned id {new_id} but the agent cannot be "
                         "read back — report this as UNVERIFIED.", is_error=True)
        head = (f"Created General Agent id {new_id} — \"{after['name']}\" "
                f"({'enabled' if after['enabled'] else 'disabled'}), verified by read-back.")
        if used_default:
            head += (f"\nObjective (platform default, since none was given): "
                     f"\"{objective}\" — offer to refine it with update_general_agent.")
        else:
            head += f"\nObjective: {after['objective'][:300]}"
        body = _readback_report(None, after, core, custom, types)
        tail = ("\nNot yet shared with any group — regular users cannot see it "
                "until an admin shares it (assign_agent_groups); developers/"
                "admins can use it now on the Assistants screen.")
        return _text(head + "\n" + body + tail)
    except Exception as e:
        logger.error(f"create_general_agent failed: {e}")
        return _text(f"Create failed: {e}", is_error=True)


@tool(
    "update_general_agent",
    "Change a General Agent's name, objective (system prompt), enabled state or "
    "personal-connections setting — only the fields you pass change; tools, "
    "document access, knowledge and groups are preserved. Use set_agent_tools / "
    "set_agent_document_types for those. Developer+ only. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or current name"},
            "name": {"type": "string", "description": "New name"},
            "objective": {"type": "string", "description": "New objective (full text)"},
            "enabled": {"type": "boolean"},
            "allow_personal_connections": {"type": "boolean"},
        },
        "required": ["agent"],
        "additionalProperties": False,
    },
)
async def update_general_agent(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Editing an agent")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        bad = _general_only(agent)
        if bad:
            return bad
        changes = []
        name = agent["name"]
        if args.get("name") is not None and str(args["name"]).strip():
            name = str(args["name"]).strip()
            if name != agent["name"]:
                changes.append(f"name '{agent['name']}' -> '{name}'")
        objective = agent["objective"]
        if args.get("objective") is not None and str(args["objective"]).strip():
            objective = str(args["objective"]).strip()
            if objective != agent["objective"]:
                changes.append("objective rewritten")
        enabled = agent["enabled"]
        if args.get("enabled") is not None:
            enabled = bool(args["enabled"])
            if enabled != agent["enabled"]:
                changes.append("enabled" if enabled else "DISABLED")
        apc = agent["allow_personal_connections"]
        if args.get("allow_personal_connections") is not None:
            apc = bool(args["allow_personal_connections"])
            if apc != agent["allow_personal_connections"]:
                changes.append(f"personal connections {'allowed' if apc else 'not allowed'}")
        if not changes:
            return _text(f"Nothing to change for agent {agent['id']} '{agent['name']}' "
                         "— the values given match what is stored.")
        _id, err = await _save_agent(agent["id"], name, objective, enabled,
                                     agent["core_tools"], agent["custom_tools"],
                                     agent["document_types"], apc)
        if err:
            return _text(err, is_error=True)
        after = await _fetch_agent(agent["id"])
        if not after:
            return _text("Save reported success but the agent cannot be read back — "
                         "report this as UNVERIFIED.", is_error=True)
        ok = (after["name"] == name and after["objective"].strip() == objective.strip()
              and after["enabled"] == enabled and after["allow_personal_connections"] == apc)
        verdict = ("verified by read-back" if ok
                   else "read-back DIFFERS from the request — report as UNVERIFIED")
        kept = (f"tools ({len(after['core_tools'])} core, {len(after['custom_tools'])} "
                f"custom) and document access preserved")
        return _text(f"Updated agent {after['id']} '{after['name']}': "
                     + "; ".join(changes) + f" — {verdict}; {kept}.")
    except Exception as e:
        logger.error(f"update_general_agent failed: {e}")
        return _text(f"Update failed: {e}", is_error=True)


@tool(
    "set_agent_tools",
    "Manage a General Agent's tools like the Builder page's tool checklist. "
    "mode='add' (default) adds the given tools to what it has, 'remove' takes "
    "them away, 'replace' makes the given lists the whole set. Names must be "
    "EXACT (get_agent_builder_options). The platform auto-adds mandatory tools "
    "and required dependencies — the read-back reports the FINAL set; relay "
    "that, not what was requested. Developer+ only.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or name"},
            "core_tools": {"type": "array", "items": {"type": "string"}},
            "custom_tools": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
        },
        "required": ["agent"],
        "additionalProperties": False,
    },
)
async def set_agent_tools(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Changing an agent's tools")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        bad = _general_only(agent)
        if bad:
            return bad
        mode = str(args.get("mode") or "add").lower()
        core_req = [str(t).strip() for t in (args.get("core_tools") or []) if str(t).strip()]
        custom_req = [str(t).strip() for t in (args.get("custom_tools") or [])
                      if str(t).strip()]
        if not core_req and not custom_req and mode != "replace":
            return _text("Give me at least one core_tools or custom_tools name "
                         "(or mode='replace' with empty lists to clear).", is_error=True)
        if mode in ("add", "replace"):
            err = await _validate_tools(core_req, custom_req, agent["core_tools"])
            if err:
                return _text(err, is_error=True)
        try:
            new_core = merge_names(agent["core_tools"], core_req, mode)
            new_custom = merge_names(agent["custom_tools"], custom_req, mode)
        except ValueError as ve:
            return _text(str(ve), is_error=True)
        if mode == "remove":
            not_present = ([t for t in core_req if t not in agent["core_tools"]]
                           + [t for t in custom_req if t not in agent["custom_tools"]])
            if not_present:
                return _text(f"Agent {agent['id']} doesn't have: "
                             + ", ".join(not_present)
                             + ". Its current tools — core: "
                             + (", ".join(agent["core_tools"]) or "(none)")
                             + "; custom: " + (", ".join(agent["custom_tools"]) or "(none)")
                             + ". Nothing changed.", is_error=True)
        if (new_core == agent["core_tools"] and new_custom == agent["custom_tools"]):
            return _text(f"Agent {agent['id']} '{agent['name']}' already has exactly "
                         "those tools — nothing changed.")
        _id, err = await _save_agent(agent["id"], agent["name"], agent["objective"],
                                     agent["enabled"], new_core, new_custom,
                                     agent["document_types"],
                                     agent["allow_personal_connections"])
        if err:
            return _text(err, is_error=True)
        after = await _fetch_agent(agent["id"])
        if not after:
            return _text("Save reported success but the agent cannot be read back — "
                         "report this as UNVERIFIED.", is_error=True)
        # In remove mode the "requested" set for the read-back is the survivors.
        report = _readback_report(agent, after, new_core, new_custom,
                                  agent["document_types"])
        return _text(f"Tools updated for agent {after['id']} '{after['name']}' "
                     f"(mode {mode}), verified by read-back:\n" + report)
    except Exception as e:
        logger.error(f"set_agent_tools failed: {e}")
        return _text(f"Tool update failed: {e}", is_error=True)


@tool(
    "set_agent_document_types",
    "Control which DOCUMENT TYPES a General Agent's document tools may see — "
    "the Builder page's 'allowed document types' list. mode='replace' (default) "
    "sets the list (an EMPTY list = unrestricted, all types); 'add' / 'remove' "
    "adjust the current restriction. Type names must exist in the document "
    "store (get_agent_builder_options section=document_types). To give an "
    "agent a specific FILE instead, use add_agent_knowledge. Developer+ only.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or name"},
            "document_types": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["replace", "add", "remove"]},
        },
        "required": ["agent", "document_types"],
        "additionalProperties": False,
    },
)
async def set_agent_document_types(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Changing an agent's document access")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        bad = _general_only(agent)
        if bad:
            return bad
        mode = str(args.get("mode") or "replace").lower()
        req = [str(t).strip() for t in (args.get("document_types") or []) if str(t).strip()]
        if mode in ("add", "replace"):
            err = await _validate_doc_types(req)
            if err:
                return _text(err, is_error=True)
        try:
            new_types = merge_names(agent["document_types"], req, mode)
        except ValueError as ve:
            return _text(str(ve), is_error=True)
        if mode == "remove":
            not_present = [t for t in req if t not in agent["document_types"]]
            if not_present:
                return _text(f"Agent {agent['id']} isn't restricted to: "
                             + ", ".join(not_present) + ". Current restriction: "
                             + (", ".join(agent["document_types"]) or "unrestricted")
                             + ". Nothing changed.", is_error=True)
        if new_types == agent["document_types"]:
            return _text(f"Agent {agent['id']} '{agent['name']}' already has that "
                         "document-type access — nothing changed.")
        _id, err = await _save_agent(agent["id"], agent["name"], agent["objective"],
                                     agent["enabled"], agent["core_tools"],
                                     agent["custom_tools"], new_types,
                                     agent["allow_personal_connections"])
        if err:
            return _text(err, is_error=True)
        after = await _fetch_agent(agent["id"])
        if not after:
            return _text("Save reported success but the agent cannot be read back — "
                         "report this as UNVERIFIED.", is_error=True)
        got = after["document_types"]
        if sorted(got) != sorted(new_types):
            return _text(f"Save reported success but read-back shows "
                         f"{got or 'unrestricted'} instead of "
                         f"{new_types or 'unrestricted'} — report as UNVERIFIED.",
                         is_error=True)
        now = ("unrestricted — its document tools see every document type"
               if not got else "restricted to " + ", ".join(got))
        return _text(f"Document-type access for agent {after['id']} '{after['name']}' "
                     f"is now {now} (verified by read-back). Tools and knowledge "
                     "documents unchanged.")
    except Exception as e:
        logger.error(f"set_agent_document_types failed: {e}")
        return _text(f"Document access update failed: {e}", is_error=True)


@tool(
    "delete_general_agent",
    "DELETE a General Agent permanently (its tool bindings go with it; its "
    "knowledge document links and group shares are left behind exactly as the "
    "Builder page's delete does). DESTRUCTIVE: first call WITHOUT confirmed to "
    "get a summary of what would be deleted, ask the user, then call again with "
    "confirmed=true. Developer+ only. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or name"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["agent"],
        "additionalProperties": False,
    },
)
async def delete_general_agent(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Deleting an agent")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        bad = _general_only(agent)
        if bad:
            return bad
        if not args.get("confirmed"):
            knowledge = await _knowledge_list(agent["id"]) or []
            return _text(
                f"CONFIRMATION REQUIRED to delete agent {agent['id']} "
                f"\"{agent['name']}\" ({'enabled' if agent['enabled'] else 'disabled'}; "
                f"{len(agent['core_tools'])} core + {len(agent['custom_tools'])} custom "
                f"tools; {len(knowledge)} knowledge document(s); shared with "
                f"{len(agent['groups'])} group(s)). This cannot be undone. Ask the "
                "user to confirm, then call again with confirmed=true.")
        data, status = await _post("/delete/agent", {"agent_id": agent["id"]}, timeout=60)
        if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
            msg = data.get("message", data) if isinstance(data, dict) else data
            return _text(f"Delete FAILED (HTTP {status}): {msg} — the agent still "
                         "exists.", is_error=True)
        still = await _fetch_agent(agent["id"])
        if still:
            return _text(f"Delete reported success but agent {agent['id']} can still "
                         "be read back — report this as UNVERIFIED.", is_error=True)
        return _text(f"Deleted agent {agent['id']} \"{agent['name']}\" (verified by "
                     "read-back: it no longer exists).")
    except Exception as e:
        logger.error(f"delete_general_agent failed: {e}")
        return _text(f"Delete failed: {e}", is_error=True)


async def _post_multipart(path: str, fields: dict, filename: str, payload: bytes,
                          read_timeout: float) -> tuple:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=read_timeout)) as client:
        r = await client.post(f"{get_base_url()}{path}", data=fields,
                              files={"file": (filename, payload)}, headers=_headers())
        try:
            return r.json(), r.status_code, r.headers.get("Retry-After")
        except Exception:
            return {"error": (r.text or "")[:500]}, r.status_code, None


@tool(
    "add_agent_knowledge",
    "Give a General Agent a specific document as its own KNOWLEDGE (the Builder "
    "page's knowledge upload): the file is extracted, stored as a knowledge "
    "document and bound to that agent, which can then answer from it. `path` is "
    "a server file path, an /api/files/ link of a file delivered in this chat, "
    "or a chat attachment. Runs the document pipeline synchronously — large "
    "PDFs take minutes; a BUSY reply means a queue, not a failure. Developer+ "
    "only. Verified by read-back (returns the knowledge_id). Optional "
    "`attributes` (e.g. {\"customer\": \"Acme\"}) are attached to the document: "
    "when an agent holds documents of several customers, a question that names "
    "one is answered from that customer's documents only — the file name does "
    "not matter.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or name"},
            "path": {"type": "string",
                     "description": "Server path, /api/files/<id> link, or attachment"},
            "description": {"type": "string",
                            "description": "Optional label shown on the Builder page"},
            "attributes": {"type": "object",
                           "description": "Optional name/value pairs for the document, "
                                          "e.g. {\"customer\": \"Acme\", \"region\": \"East\"}",
                           "additionalProperties": {"type": "string"}},
        },
        "required": ["agent", "path"],
        "additionalProperties": False,
    },
)
async def add_agent_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Adding knowledge to an agent")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        bad = _general_only(agent)
        if bad:
            return bad
        from document_tools import _resolve_read_path, _ext_ok, _fmt_size
        path, perr = _resolve_read_path(args.get("path"))
        if perr:
            return _text(perr, is_error=True)
        filename = os.path.basename(path)
        if not _ext_ok(filename):
            return _text(f"'{filename}' is not a supported document type for "
                         "knowledge (PDF, Word, Excel, images, text/CSV/JSON/HTML).",
                         is_error=True)
        with open(path, "rb") as fh:
            payload = fh.read()
        description = str(args.get("description") or "").strip() or filename
        before = await _knowledge_list(agent["id"]) or []
        before_docs = {str(k.get("document_id")) for k in before}
        fields = {"agent_id": str(agent["id"]), "description": description}
        wanted_attrs = args.get("attributes")
        if isinstance(wanted_attrs, dict) and wanted_attrs:
            fields["attributes"] = json.dumps({str(k): str(v) for k, v in wanted_attrs.items()})
        data, status, retry_after = await _post_multipart(
            "/add/agent_knowledge", fields,
            filename, payload, _KNOWLEDGE_READ_TIMEOUT)
        if status == 503:
            msg = data.get("message") if isinstance(data, dict) else None
            ra = (data.get("retry_after") if isinstance(data, dict) else None) or retry_after
            return _text((msg or "The document stack is BUSY (another import or "
                          "extraction is holding it).")
                         + " This is a queue, not a failure — the file was NOT "
                           "processed and the agent's knowledge is unchanged."
                         + (f" Retry in about {ra} seconds." if ra else
                            " Wait about a minute and call once more."),
                         is_error=True)
        if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
            msg = data.get("message", data) if isinstance(data, dict) else data
            return _text(f"Knowledge upload FAILED (HTTP {status}): {msg}. The agent's "
                         "knowledge is unchanged.", is_error=True)
        doc_id = str(data.get("document_id") or "")
        after = await _knowledge_list(agent["id"])
        hit = None
        if after is not None:
            for k in after:
                if doc_id and str(k.get("document_id")) == doc_id:
                    hit = k
                    break
            if hit is None:
                new_rows = [k for k in after if str(k.get("document_id")) not in before_docs]
                if len(new_rows) == 1:
                    hit = new_rows[0]
        if not hit:
            return _text(f"The platform reported the upload succeeded (document "
                         f"{doc_id or '?'}) but the knowledge list for agent "
                         f"{agent['id']} does not show it — report as UNVERIFIED.",
                         is_error=True)
        extra = ", ".join(str(x) for x in (hit.get("document_type"),
                                           f"{hit.get('page_count')} pages"
                                           if hit.get("page_count") else None) if x)
        return _text(f"Added \"{filename}\" ({_fmt_size(len(payload))}) as knowledge for "
                     f"agent {agent['id']} '{agent['name']}' — knowledge_id "
                     f"{hit.get('knowledge_id')}, document {hit.get('document_id')}"
                     + (f" ({extra})" if extra else "")
                     + f". The agent now has {len(after)} knowledge document(s) "
                     "(verified by read-back)."
                     + _attributes_report(fields.get("attributes"), hit, data))
    except Exception as e:
        logger.error(f"add_agent_knowledge failed: {e}")
        return _text(f"Knowledge upload failed: {e}", is_error=True)


def _attributes_report(requested_json, hit: dict, data: dict) -> str:
    """What to say about attributes after an upload: only what the read-back shows."""
    if not requested_json:
        return ""
    have = hit.get("attributes") if isinstance(hit, dict) else None
    if isinstance(have, dict) and have:
        return " Attributes on the document: " + "; ".join(f"{n}: {v}" for n, v in have.items()) + "."
    warn = data.get("attributes_warning") if isinstance(data, dict) else None
    return " Its attributes were NOT stored" + (f" ({warn})" if warn else "") + " — set them with set_agent_knowledge_attributes."


@tool(
    "set_agent_knowledge_attributes",
    "Attach ATTRIBUTES to a knowledge document a General Agent already holds — "
    "name/value pairs such as customer, region, department. Needs the "
    "knowledge_id from get_agent_config. The attribute `customer` steers the "
    "agent's knowledge search: when its documents belong to several customers, "
    "a question that names one is answered from that customer's documents only, "
    "whatever the files are called. Other attributes are stored and shown with "
    "the document. By default the given names are set and the rest kept; give a "
    "name an empty value to remove it; replace=true leaves exactly the given "
    "attributes. Developer+ only. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "knowledge_id": {"type": "integer", "description": "From get_agent_config"},
            "attributes": {"type": "object",
                           "description": "e.g. {\"customer\": \"Acme\"}; an empty value removes that name",
                           "additionalProperties": {"type": "string"}},
            "replace": {"type": "boolean",
                        "description": "true = the document ends up with exactly these attributes"},
        },
        "required": ["knowledge_id", "attributes"],
        "additionalProperties": False,
    },
)
async def set_agent_knowledge_attributes(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Setting attributes on knowledge")
    if gate:
        return gate
    try:
        kid = int(args.get("knowledge_id") or 0)
        attrs = args.get("attributes")
        if not isinstance(attrs, dict):
            return _text("Give the attributes as an object, e.g. {\"customer\": \"Acme\"}.",
                         is_error=True)
        data, status = await _post(f"/api/agent_knowledge/{kid}/attributes",
                                   {"attributes": {str(k): str(v if v is not None else "")
                                                   for k, v in attrs.items()},
                                    "replace": bool(args.get("replace"))}, timeout=60)
        if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
            msg = data.get("message", data) if isinstance(data, dict) else data
            return _text(f"Attributes NOT changed (HTTP {status}): {msg}", is_error=True)
        try:
            check = await _get(f"/api/agent_knowledge/{kid}/attributes")
        except Exception as read_err:
            logger.warning(f"attributes read-back failed for knowledge {kid}: {read_err}")
            check = None
        now = check.get("attributes") if isinstance(check, dict) else None
        if not isinstance(now, dict):
            return _text(f"The platform reported success for knowledge_id {kid} but the "
                         "attributes could not be read back — report as UNVERIFIED.", is_error=True)
        steering = check.get("steering") or []
        shown = "; ".join(f"{n}: {v}" for n, v in now.items()) or "(none)"
        return _text(f"knowledge_id {kid} now carries: {shown} (verified by read-back)."
                     + (f" Attributes that steer the agent's search: {', '.join(steering)}." if steering else ""))
    except Exception as e:
        logger.error(f"set_agent_knowledge_attributes failed: {e}")
        return _text(f"Setting attributes failed: {e}", is_error=True)


@tool(
    "delete_agent_knowledge",
    "Remove one knowledge document from a General Agent (the Builder page's "
    "knowledge delete). Needs the knowledge_id from get_agent_config. "
    "DESTRUCTIVE: call without confirmed to see what would be removed, ask the "
    "user, then call again with confirmed=true. Developer+ only.",
    {
        "type": "object",
        "properties": {
            "knowledge_id": {"type": "integer"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["knowledge_id"],
        "additionalProperties": False,
    },
)
async def delete_agent_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(2, "Removing agent knowledge")
    if gate:
        return gate
    try:
        kid = int(args.get("knowledge_id") or 0)
        row = await _rt("knowledge_row", _q_knowledge_row(kid), knowledge_id=int(kid))
        if not row or not row.get("is_active"):
            return _text(f"No active knowledge item with knowledge_id {kid}. "
                         "get_agent_config lists an agent's knowledge_ids.",
                         is_error=True)
        label = (f"knowledge_id {kid}: \"{row['filename'] or row['document_id']}\""
                 + (f" ({row['document_type']})" if row.get("document_type") else "")
                 + f" on agent {row['agent_id']}")
        if not args.get("confirmed"):
            return _text(f"CONFIRMATION REQUIRED to remove {label}. Ask the user, then "
                         "call again with confirmed=true.")
        data, status = await _post(f"/delete/agent_knowledge/{kid}", {}, timeout=60)
        if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
            msg = data.get("message", data) if isinstance(data, dict) else data
            return _text(f"Remove FAILED (HTTP {status}): {msg} — nothing changed.",
                         is_error=True)
        still = await _rt("knowledge_row", _q_knowledge_row(kid), knowledge_id=int(kid))
        if still and still.get("is_active"):
            return _text(f"Remove reported success but {label} is still active — "
                         "report as UNVERIFIED.", is_error=True)
        return _text(f"Removed {label} (verified by read-back).")
    except Exception as e:
        logger.error(f"delete_agent_knowledge failed: {e}")
        return _text(f"Remove failed: {e}", is_error=True)


@tool(
    "assign_agent_groups",
    "ADMIN ONLY: set which user GROUPS a General Agent is shared with — this is "
    "what makes it appear for regular users (the Permissions page's rule). Pass "
    "the FULL list of group ids (it replaces the agent's current groups; empty "
    "list = developers/admins only). Ask the user which groups before calling "
    "(get_agent_builder_options section=groups lists them). Group membership "
    "(which users are in each group) is never changed. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Agent id or name"},
            "group_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["agent", "group_ids"],
        "additionalProperties": False,
    },
)
async def assign_agent_groups(args: dict[str, Any]) -> dict[str, Any]:
    gate = _role_gate(3, "Sharing an agent with groups")
    if gate:
        return gate
    try:
        agent, err = await _resolve(args.get("agent"))
        if err:
            return _text(err, is_error=True)
        want = sorted({int(g) for g in (args.get("group_ids") or [])})
        groups = await _rt("groups", _q_groups)
        by_id = {g["id"]: g["name"] for g in groups}
        unknown = [g for g in want if g not in by_id]
        if unknown:
            return _text(f"Unknown group id(s): {unknown}. Groups: "
                         + ", ".join(f"{g['name']} (id {g['id']})" for g in groups)
                         + ". Nothing changed.", is_error=True)
        have = sorted(agent["group_ids"])
        if have == want:
            return _text(f"Agent {agent['id']} '{agent['name']}' is already shared with "
                         f"exactly {[by_id[g] for g in want] or 'no groups'} — nothing "
                         "changed.")
        to_add = [g for g in want if g not in have]
        to_remove = [g for g in have if g not in want]
        failures = []
        for gid in to_add + to_remove:
            _m = await _rt("group_membership", _q_group_membership(gid), group_id=int(gid))
            users, agents_in_group = ((_m["users"], _m["agents"]) if isinstance(_m, dict)
                                      else _m)
            if gid in to_add and agent["id"] not in agents_in_group:
                agents_in_group.append(agent["id"])
            if gid in to_remove:
                agents_in_group = [a for a in agents_in_group if a != agent["id"]]
            data, status = await _post("/save/permissions",
                                       {"group_id": gid, "assigned_users": users,
                                        "permissions": agents_in_group}, timeout=60)
            if status >= 400 or not isinstance(data, dict) or data.get("status") != "success":
                failures.append(f"{by_id[gid]} (id {gid}): HTTP {status} "
                                f"{data.get('message', '') if isinstance(data, dict) else data}")
        after = await _fetch_agent(agent["id"])
        got = sorted(after["group_ids"]) if after else []
        names = [by_id.get(g, str(g)) for g in got]
        if failures or got != want:
            return _text(f"Group sharing for agent {agent['id']} is now "
                         f"{names or 'no groups'} — expected "
                         f"{[by_id[g] for g in want] or 'no groups'}."
                         + (" Failures: " + "; ".join(failures) if failures else "")
                         + " Report this as PARTIAL/UNVERIFIED.", is_error=True)
        who = ", ".join(names) if names else "no groups (developers/admins only)"
        return _text(f"Agent {agent['id']} '{agent['name']}' is now shared with {who} "
                     "(verified by read-back). Group memberships were not changed.")
    except Exception as e:
        logger.error(f"assign_agent_groups failed: {e}")
        return _text(f"Group sharing failed: {e}", is_error=True)


AGENT_BUILDER_TOOLS = [
    list_agents, get_agent_config, get_agent_builder_options,
    create_general_agent, update_general_agent, set_agent_tools,
    set_agent_document_types, delete_general_agent,
    add_agent_knowledge, set_agent_knowledge_attributes, delete_agent_knowledge,
    assign_agent_groups,
]
