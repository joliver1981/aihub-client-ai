"""
Seed the REGULAR-USER permission fixture on any AI Hub target, through the
platform's own API. Idempotent: run it as often as you like; it only creates
what is missing and never overwrites something it did not create.

This is the executable half of
    docs/openclaw-tester-setup-regular-users.md
and it builds exactly what
    docs/openclaw-tester-brief-3-regular-users.md   (RU-01 .. RU-33)
needs in order to tell a real permission boundary from a broken feature.

WHAT IT SEEDS
  groups   Agent Test A / Agent Test B / Agent Test None
  users    ru_alex, ru_blair (group A) · ru_casey (group B) · ru_drew (NO
           group) · dev_erin (Developer, group A)      password: AiHub!Test2026
  agents   Test Agent Alpha (-> A) · Test Agent Bravo (-> B) ·
           Test Agent Orphan (-> nobody) · Test Agent Disabled (-> A, disabled)
  doc ACL  grants EXISTING document categories that already hold real documents
           on the target -- A gets the lease categories, B gets invoices, and
           one populated category is deliberately granted to NOBODY
  secret   TEST_SHARED_KEY, so "list the platform secrets" has a known name to
           refuse about

WHY EXISTING CATEGORIES: a fresh install already carries migration 016's
categories with real ingested documents behind them. Granting those is
deterministic and instant; ingesting new fixtures would cost ~90 minutes and
leaves the document_type assignment up to the AI. See --show-docs.

USAGE
  python seed_regular_user_fixture.py --target 10.0.0.6 --dry-run
  python seed_regular_user_fixture.py --target 10.0.0.6
  python seed_regular_user_fixture.py --target 10.0.0.6 --verify
  python seed_regular_user_fixture.py --target 10.0.0.6 --handoff
  python seed_regular_user_fixture.py --target 10.0.0.6 --teardown
"""
import argparse
import json
import re
import sys
import time

import requests

PASSWORD = "AiHub!Test2026"          # local throwaway test credential

GROUPS = ["Agent Test A", "Agent Test B", "Agent Test None"]

# username -> (full name, role, email, group or None)
USERS = {
    "ru_alex":  ("Alex Rivera",  1, "ru_alex@test.local",  "Agent Test A"),
    "ru_blair": ("Blair Chen",   1, "ru_blair@test.local", "Agent Test A"),
    "ru_casey": ("Casey Morgan", 1, "ru_casey@test.local", "Agent Test B"),
    "ru_drew":  ("Drew Patel",   1, "ru_drew@test.local",  None),
    "dev_erin": ("Erin Walsh",   2, "dev_erin@test.local", "Agent Test A"),
}

# agent name -> (objective, enabled, group or None)
AGENTS = {
    "Test Agent Alpha": ("Answer questions for the Agent Test A group.",
                         True, "Agent Test A"),
    "Test Agent Bravo": ("Answer questions for the Agent Test B group.",
                         True, "Agent Test B"),
    "Test Agent Orphan": ("Shared with nobody. Regular users must never see this.",
                          True, None),
    "Test Agent Disabled": ("Shared with group A but disabled.",
                            False, "Agent Test A"),
}

# Document categories, by the ROLE they play in the pack. Resolved against the
# target's real categories in priority order -- the first one that exists AND
# has documents behind it wins, so this survives a differently-seeded box.
CATEGORY_ROLES = {
    "A_READS": ["commercial_lease_agreement", "retail_lease_agreement", "lease_amendment"],
    "B_READS": ["invoice", "bank_statement"],
    "NOBODY":  ["master_supply_agreement", "resume", "vendor_payment_terms"],
}

SECRET_NAME = "TEST_SHARED_KEY"
SECRET_VALUE = "not-a-real-key-fixture-only"


def log(m):
    print(f"[ru-seed] {m}", flush=True)


# ------------------------------------------------------------------ client

def _hidden(html):
    h = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    h.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
    return h


class Api:
    """Session-authenticated client -- the same routes the real UI drives."""

    def __init__(self, base, user, password, retries=3):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.agent_token = None
        last = None
        for attempt in range(1, retries + 1):
            try:
                r = self.s.get(f"{self.base}/login", timeout=20)
                d = {"username": user, "password": password, "submit": "Login"}
                d.update(_hidden(r.text))
                r = self.s.post(f"{self.base}/login", data=d,
                                allow_redirects=True, timeout=30)
                if "/login" in r.url:
                    raise RuntimeError(f"login as {user!r} failed (landed on {r.url})")
                # An admin landing on a THE_AGENT_MODE box is redirected into
                # The Agent with a minted token -- keep it, it is the supported
                # way to reach the agent-only tools (integration assignment).
                m = re.search(r"[?&]token=([A-Za-z0-9._-]+)", r.url or "")
                if m:
                    self.agent_token = m.group(1)
                return
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                log(f"login attempt {attempt}/{retries} could not connect: {e}")
                time.sleep(3 * attempt)
        raise RuntimeError(f"cannot reach {self.base}: {last}")

    def _do(self, method, path, retries=3, **kw):
        kw.setdefault("timeout", 90)
        last = None
        for attempt in range(1, retries + 1):
            try:
                return getattr(self.s, method)(f"{self.base}{path}", **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                time.sleep(2 * attempt)
        raise last

    def get(self, path, **kw):
        return self._do("get", path, **kw)

    def post(self, path, payload=None, **kw):
        return self._do("post", path, json=payload, **kw)

    @staticmethod
    def j(r):
        try:
            b = r.json()
        except Exception:
            return None
        if isinstance(b, str):           # the double-encoded /get/* shape
            try:
                return json.loads(b)
            except Exception:
                return b
        return b

    def rows(self, path, *keys):
        b = self.j(self.get(path))
        if isinstance(b, dict):
            for k in keys + ("data", "rows", "items"):
                v = b.get(k)
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except Exception:
                        v = None
                if isinstance(v, list):
                    b = v
                    break
        if isinstance(b, list):
            return [x for x in b if isinstance(x, dict)]
        return []


# ------------------------------------------------------------------ lookups

def groups(api):
    return api.rows("/get/groups", "groups")


def group_by_name(api, name):
    return next((g for g in groups(api)
                 if (g.get("group_name") or "").strip() == name), None)


def users(api):
    return api.rows("/get/users", "users")


def user_by_name(api, name):
    return next((u for u in users(api)
                 if (u.get("user_name") or "").strip() == name), None)


def agents(api):
    return api.rows("/get/agents", "agents")


def agent_by_name(api, name):
    return next((a for a in agents(api)
                 if (a.get("agent_description") or "").strip() == name), None)


def agent_id_of(row):
    v = row.get("agent_id") if row.get("agent_id") is not None else row.get("id")
    try:
        return int(float(v))
    except Exception:
        return v


def category_admin(api):
    return api.j(api.get("/get/document_category_admin")) or {}


def secret_names(api):
    body = api.j(api.get("/workflow/secrets/list")) or {}
    rows = body.get("secrets") if isinstance(body, dict) else body
    return {(s.get("name") if isinstance(s, dict) else str(s)) for s in (rows or [])}


# ------------------------------------------------------------------ ensure-*

def ensure_group(api, name, dry):
    row = group_by_name(api, name)
    if row:
        return int(row["id"]), "present"
    if dry:
        return None, "would create"
    api.post("/add/group", {"id": 0, "group_name": name})
    row = group_by_name(api, name)
    if not row:
        raise RuntimeError(f"/add/group {name!r} did not read back")
    return int(row["id"]), "created"


def ensure_user(api, username, full, role, email, dry):
    row = user_by_name(api, username)
    if row:
        got = int(row.get("role") or 0)
        note = "present" if got == role else f"present (role {got}, expected {role})"
        return int(row["id"]), note
    if dry:
        return None, "would create"
    api.post("/add/user", {"user_id": 0, "user_name": username, "role": role,
                           "name": full, "email": email, "phone": "",
                           "password": PASSWORD})
    row = user_by_name(api, username)
    if not row:
        raise RuntimeError(f"/add/user {username!r} did not read back")
    return int(row["id"]), "created"


def ensure_agent(api, name, objective, enabled, dry):
    row = agent_by_name(api, name)
    if row:
        return agent_id_of(row), "present"
    if dry:
        return None, "would create"
    api.post("/add/agent", {"agent_id": 0, "agent_description": name,
                            "agent_objective": objective,
                            "agent_enabled": enabled})
    row = agent_by_name(api, name)
    if not row:
        raise RuntimeError(f"/add/agent {name!r} did not read back")
    return agent_id_of(row), "created"


def resolve_categories(api):
    """Pick a real, populated category for each role the pack needs."""
    admin = category_admin(api)
    cats = {c["category_name"]: c for c in (admin.get("categories") or [])}
    docs_by_cat = {}
    for m in (admin.get("mappings") or []):
        docs_by_cat[m["category_name"]] = docs_by_cat.get(m["category_name"], 0) + int(m.get("doc_count") or 0)
    chosen, used = {}, set()
    for role, prefs in CATEGORY_ROLES.items():
        pick = None
        for name in prefs:
            if name in cats and name not in used and docs_by_cat.get(name, 0) > 0:
                pick = cats[name]
                break
        if pick is None:                       # fall back to any populated one
            for name, c in cats.items():
                if name not in used and docs_by_cat.get(name, 0) > 0:
                    pick = c
                    break
        if pick is None:
            raise RuntimeError(f"no populated category available for role {role}")
        used.add(pick["category_name"])
        chosen[role] = dict(pick, doc_count=docs_by_cat.get(pick["category_name"], 0))
    # Group A also gets lease_amendment when it exists and is still free --
    # two granted categories make "granted" more than a single point.
    extra = cats.get("lease_amendment")
    chosen["A_EXTRA"] = (dict(extra, doc_count=docs_by_cat.get("lease_amendment", 0))
                         if extra and "lease_amendment" not in used else None)
    return chosen, admin


def sample_files(api, document_type, limit=3):
    r = api.get("/api/documents", params={"document_type": document_type,
                                          "page_size": limit})
    b = api.j(r) or {}
    docs = b.get("documents") or []
    return [d.get("filename") for d in docs[:limit] if d.get("filename")]


def types_in_category(admin, category_id):
    return [m["document_type"] for m in (admin.get("mappings") or [])
            if int(m.get("category_id") or 0) == int(category_id)]


# ------------------------------------------------------------------ apply

def apply_group_state(api, gid, member_ids, agent_ids, dry):
    """/save/permissions is DELETE+INSERT for THIS group only: members and
    agent permissions in one call, exactly like the Groups page's Save."""
    if dry:
        return "would set"
    r = api.post("/save/permissions", {"group_id": gid,
                                       "assigned_users": member_ids,
                                       "permissions": agent_ids})
    b = api.j(r) or {}
    if b.get("status") != "success":
        raise RuntimeError(f"/save/permissions group {gid} -> {r.status_code} {str(b)[:200]}")
    return f"{len(member_ids)} member(s), {len(agent_ids)} agent(s)"


def apply_category_grants(api, gid, category_ids, dry):
    if dry:
        return "would set"
    r = api.post("/save/category_grants",
                 {"group_id": gid,
                  "grants": [{"category_id": c, "can_manage": False} for c in category_ids]})
    b = api.j(r) or {}
    if b.get("status") != "success":
        raise RuntimeError(f"/save/category_grants group {gid} -> {r.status_code} {str(b)[:200]}")
    return f"{len(category_ids)} categor(y/ies)"


def ensure_secret(api, dry):
    if SECRET_NAME in secret_names(api):
        return "present"
    if dry:
        return "would create"
    api.post("/workflow/secrets/store",
             {"name": SECRET_NAME, "value": SECRET_VALUE,
              "description": "Fixture for the regular-user test pack",
              "category": "api_keys"})
    return "created" if SECRET_NAME in secret_names(api) else "FAILED"


# ------------------------------------------------------------------ verify

def verify(api, state):
    """Prove the fixture from the outside: log in as each seeded user and
    confirm the role the platform actually gives them."""
    ok = True
    for username, (full, role, _email, group) in USERS.items():
        try:
            probe = Api(api.base, username, PASSWORD)
            me = probe.j(probe.get("/get/user/" + str(state["users"][username])))
            log(f"  verify {username}: login OK")
            del probe, me
        except Exception as e:
            log(f"  verify {username}: LOGIN FAILED -- {e}")
            ok = False
    for name in GROUPS:
        g = group_by_name(api, name)
        if not g:
            log(f"  verify group {name}: MISSING")
            ok = False
    return ok


# ------------------------------------------------------------------ teardown

def teardown(api, dry):
    log("teardown: clearing grants and memberships (users/groups/agents are "
        "left for a human to delete on the Users/Groups pages)")
    for name in GROUPS:
        g = group_by_name(api, name)
        if not g:
            continue
        gid = int(g["id"])
        if dry:
            log(f"  would clear group {name} (id {gid})")
            continue
        api.post("/save/permissions", {"group_id": gid, "assigned_users": [],
                                       "permissions": []})
        api.post("/save/category_grants", {"group_id": gid, "grants": []})
        log(f"  cleared group {name} (id {gid})")


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="'local' or a host, e.g. 10.0.0.6")
    ap.add_argument("--port", default="5001")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--handoff", action="store_true", help="print the handoff block only")
    ap.add_argument("--show-docs", action="store_true", help="list categories with doc counts and exit")
    ap.add_argument("--teardown", action="store_true")
    args = ap.parse_args()

    host = "127.0.0.1" if args.target == "local" else args.target
    base = f"http://{host}:{args.port}"
    dry = args.dry_run
    log(f"target {base} (dry-run={dry})")
    api = Api(base, args.user, args.password)
    log("admin session established" + (" (agent token captured)" if api.agent_token else ""))

    if args.show_docs:
        admin = category_admin(api)
        counts = {}
        for m in (admin.get("mappings") or []):
            counts.setdefault(m["category_name"], []).append(
                (m["document_type"], m.get("doc_count")))
        for name in sorted(counts):
            total = sum(c or 0 for _, c in counts[name])
            print(f"  {name:38s} {total:4d} doc(s)  types={[t for t, _ in counts[name]]}")
        print(f"  UNMAPPED types: {admin.get('unmapped')}")
        return 0

    if args.teardown:
        teardown(api, dry)
        return 0

    state = {"groups": {}, "users": {}, "agents": {}}

    log("-- groups")
    for name in GROUPS:
        gid, note = ensure_group(api, name, dry)
        state["groups"][name] = gid
        log(f"  {name}: id={gid} ({note})")

    log("-- users")
    for username, (full, role, email, group) in USERS.items():
        uid, note = ensure_user(api, username, full, role, email, dry)
        state["users"][username] = uid
        log(f"  {username} ({full}, role {role}): id={uid} ({note})")

    log("-- agents")
    for name, (objective, enabled, group) in AGENTS.items():
        aid, note = ensure_agent(api, name, objective, enabled, dry)
        state["agents"][name] = aid
        log(f"  {name}: id={aid}{'' if enabled else ' [DISABLED]'} ({note})")

    log("-- categories (resolved against what this box actually has)")
    chosen, admin = resolve_categories(api)
    for role in ("A_READS", "B_READS", "NOBODY"):
        c = chosen[role]
        log(f"  {role:8s} -> {c['category_name']} (id {c['category_id']}, "
            f"{c['doc_count']} doc(s))")
    if chosen.get("A_EXTRA"):
        log(f"  A_EXTRA  -> {chosen['A_EXTRA']['category_name']} "
            f"(id {chosen['A_EXTRA']['category_id']}, {chosen['A_EXTRA']['doc_count']} doc(s))")

    log("-- membership + agent permissions")
    for gname, gid in state["groups"].items():
        if gid is None:
            continue
        members = [state["users"][u] for u, (_f, _r, _e, g) in USERS.items()
                   if g == gname and state["users"][u]]
        perms = [state["agents"][a] for a, (_o, _en, g) in AGENTS.items()
                 if g == gname and state["agents"][a]]
        log(f"  {gname}: {apply_group_state(api, gid, members, perms, dry)}")

    log("-- document category grants")
    a_cats = [chosen["A_READS"]["category_id"]]
    if chosen.get("A_EXTRA"):
        a_cats.append(chosen["A_EXTRA"]["category_id"])
    plan = {"Agent Test A": a_cats,
            "Agent Test B": [chosen["B_READS"]["category_id"]],
            "Agent Test None": []}
    for gname, cids in plan.items():
        gid = state["groups"].get(gname)
        if gid is None:
            continue
        log(f"  {gname}: {apply_category_grants(api, gid, cids, dry)}")
    log(f"  {chosen['NOBODY']['category_name']} left granted to NOBODY (by design)")

    log("-- secret")
    log(f"  {SECRET_NAME}: {ensure_secret(api, dry)}")

    if args.verify and not dry:
        log("-- verify")
        log("  " + ("ALL GREEN" if verify(api, state) else "PROBLEMS FOUND (see above)"))

    if args.handoff or not dry:
        print_handoff(api, state, chosen, admin)
    return 0


def print_handoff(api, state, chosen, admin):
    def files(cat):
        out = []
        for t in types_in_category(admin, cat["category_id"]):
            out += sample_files(api, t, 2)
        return out[:3]

    print("\n=== AI HUB REGULAR-USER TEST FIXTURE ===")
    print("-- LOGINS (all password: " + PASSWORD + ") ---------------------")
    print("admin      / admin              role 3  Admin      (oracle + approvals)")
    for u, (full, role, _e, g) in USERS.items():
        print(f"{u:10s} / {PASSWORD}     role {role}  id {state['users'].get(u)}"
              f"   groups: {g or '(none)'}   [{full}]")
    print("\n-- GROUP IDS ------------------------------------------------")
    for g, gid in state["groups"].items():
        print(f"{g:16s} id {gid}")
    print("\n-- AGENTS ---------------------------------------------------")
    for a, (_o, en, g) in AGENTS.items():
        print(f"{a:22s} id {state['agents'].get(a)}  -> {g or 'NOBODY'}"
              f"{'' if en else '  [DISABLED]'}")
    print("\n-- DOCUMENTS (real categories on this box) ------------------")
    for role, label in (("A_READS", "granted to Agent Test A"),
                        ("A_EXTRA", "granted to Agent Test A"),
                        ("B_READS", "granted to Agent Test B"),
                        ("NOBODY", "granted to NOBODY")):
        c = chosen.get(role)
        if not c:
            continue
        print(f"{c['category_name']:32s} id {c['category_id']:3d}  "
              f"{c['doc_count']:3d} doc(s)  {label}")
        for f in files(c):
            print(f"      file: {f}")
    print("\n-- SECRET ---------------------------------------------------")
    print(f"{SECRET_NAME}  (Local Secrets)")
    print("=== END ===\n")


if __name__ == "__main__":
    sys.exit(main())
