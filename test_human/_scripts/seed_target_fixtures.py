"""
Seed the regression-suite fixtures on ANY target — the local dev tree or an
INSTALLED box — through the platform's own API. Idempotent: run it as often as
you like; it only creates what is missing and never overwrites.

WHY: packs 14/15/16/17 grew up on the dev tree, where the NL->SQL oracle agent
("Retail Demo - AIRDB2 (15 stores)", historically id 281) and its data
dictionary have always existed. A fresh install has none of that, so on an
installed box those checks either SKIP (pack 15) or hard-FAIL (pack 16 — 4 of
its 8 remote failures on 2026-09-02 were nothing but this). Seeding the same
fixtures on the target turns those SKIPs into real coverage, and the packs now
resolve the oracle by NAME (see REGP_ORACLE_AGENT) so ids no longer matter.

WHAT IT SEEDS (all named so the packs can find them; none of it is secret)
  connections   "AIRDB2" and "AIRDB"      -> SQL Server 10.0.0.6 / ai_user
  data agents   "Retail Demo - AIRDB2 (15 stores)"  (the oracle: 15 stores,
                75 employees) and "Demo AirDB Agent 4" (the second-agent
                reference target), both bound to the AIRDB2 connection
  dictionary    the AIRDB2 data dictionary (10 tables / ~94 columns) from
                fixtures/airdb2_dictionary.json, via /api/table/update and
                /api/column/update — WITHOUT this the NLQ engine answers
                "no documented schema" and never writes SQL
  secrets       AUTODEMO_SFTP + SFTP_TEST_PASSWORD (= the throwaway SFTP test
                rig password) so File-Transfer checks have a secret to name
  general agent "Regression Sim Agent" (prompt-only) — pack 19's simulated
                user/judge, pack 15's LLM probes

USAGE
  python seed_target_fixtures.py --target local
  python seed_target_fixtures.py --target 10.0.0.6 [--verify] [--dry-run]
  python seed_target_fixtures.py --target local --export     # refresh the
        dictionary fixture from a box that already has the AIRDB2 dictionary

--verify asks the seeded oracle agent the pack-15 question ("How many stores
are there in total?") and requires the answer to contain 15 — proof that the
whole chain (connection -> agent -> dictionary -> NL->SQL) works on the target.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "airdb2_dictionary.json")
MANIFESTS = os.path.join(HERE, "seed_manifests")

# ---- the names every pack resolves against (keep in sync with the runners)
ORACLE_CONN_NAME = "AIRDB2"
AIRDB_CONN_NAME = "AIRDB"
ORACLE_AGENT_NAME = "Retail Demo - AIRDB2 (15 stores)"
ORACLE_AGENT_OBJECTIVE = "Query data warehouse and answer user questions."
SECOND_AGENT_NAME = "Demo AirDB Agent 4"
SECOND_AGENT_OBJECTIVE = "Query the data warehouse."
SIM_AGENT_NAME = "Regression Sim Agent"
SECRET_NAMES = ("AUTODEMO_SFTP", "SFTP_TEST_PASSWORD")
SECRET_VALUE = "testpass"          # the loopback SFTP rig's throwaway password

# On-prem TEST SQL Server (see the onprem-test-resources skill): these are
# test-fixture credentials, identical to the ones pack 15 already carries.
SQL = {"database_type": "SQL Server", "server": "10.0.0.6", "user_name": "ai_user",
       "password": "Bradynov11", "port": 1433, "parameters": "Connect Timeout=15;"}


def log(m):
    print(f"[seed] {m}", flush=True)


# ------------------------------------------------------------------ client

def _hidden(html):
    h = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    h.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
    return h


class Api:
    def __init__(self, base, user, password, retries=3):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        last = None
        for attempt in range(1, retries + 1):
            try:
                r = self.s.get(f"{self.base}/login", timeout=20)
                d = {"username": user, "password": password, "submit": "Login"}
                d.update(_hidden(r.text))
                r = self.s.post(f"{self.base}/login", data=d, allow_redirects=True, timeout=30)
                if "/login" in r.url:
                    raise RuntimeError(f"login as {user!r} failed (landed on {r.url})")
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

    def delete(self, path, **kw):
        return self._do("delete", path, **kw)

    @staticmethod
    def j(r):
        try:
            b = r.json()
        except Exception:
            return None
        if isinstance(b, str):
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
        if isinstance(b, str):
            try:
                b = json.loads(b)
            except Exception:
                b = []
        return [x for x in (b or []) if isinstance(x, dict)] if isinstance(b, list) else []


# ------------------------------------------------------------------ lookups

def connections(api):
    return api.rows("/get/connections", "connections")


def connection_by_name(api, name):
    return next((c for c in connections(api)
                 if (c.get("connection_name") or "").strip() == name), None)


def data_agents(api):
    return api.rows("/get/data_agents")


def data_agent_by_name(api, name):
    return next((a for a in data_agents(api)
                 if (a.get("agent_description") or "").strip() == name), None)


def general_agents(api):
    return api.rows("/get/agents")


def general_agent_by_name(api, name):
    return next((a for a in general_agents(api)
                 if (a.get("agent_description") or "").strip() == name), None)


def secret_names(api):
    body = api.j(api.get("/workflow/secrets/list")) or {}
    rows = body.get("secrets") if isinstance(body, dict) else body
    return {(s.get("name") if isinstance(s, dict) else str(s)) for s in (rows or [])}


def agent_id_of(row):
    v = row.get("agent_id") if row.get("agent_id") is not None else row.get("id")
    try:
        return int(float(v))
    except Exception:
        return v


# ------------------------------------------------------------------ ensure-*

def ensure_connection(api, name, database, dry):
    row = connection_by_name(api, name)
    if row:
        return int(row["id"]), "present"
    if dry:
        return None, "would create"
    payload = dict(SQL, connection_name=name, database_name=database, connection_id=0)
    r = api.post("/add/connection", payload)
    body = api.j(r) or {}
    cid = body.get("response") if str(body.get("response", "")).isdigit() else None
    if not cid:
        row = connection_by_name(api, name)
        cid = row.get("id") if row else None
    if not cid:
        raise RuntimeError(f"/add/connection {name!r} -> http={r.status_code} body={str(body)[:200]}")
    # Prove the target box can actually reach the SQL Server with these creds.
    t = api.post(f"/api/connections/{cid}/test", {})
    tb = json.dumps(api.j(t) or {}).lower()
    if t.status_code != 200 or ("success" not in tb and "true" not in tb):
        raise RuntimeError(f"connection {name!r} created (id {cid}) but its test failed: "
                           f"http={t.status_code} {tb[:160]} — is 10.0.0.6:1433 reachable "
                           f"from the target?")
    return int(cid), "created"


def ensure_data_agent(api, name, objective, connection_id, dry):
    row = data_agent_by_name(api, name)
    if row:
        bound = row.get("connection_id")
        try:
            bound = int(float(bound))
        except Exception:
            pass
        note = "present" if bound == connection_id else f"present (bound to connection {bound}, not {connection_id})"
        return agent_id_of(row), note
    if dry:
        return None, "would create"
    r = api.post("/add/data_agent", {"agent_id": 0, "agent_description": name,
                                     "agent_objective": objective, "agent_enabled": True,
                                     "connection_id": connection_id})
    body = api.j(r) or {}
    aid = body.get("message") if body.get("status") == "success" else None
    if not aid:
        row = data_agent_by_name(api, name)
        aid = agent_id_of(row) if row else None
    if not aid:
        raise RuntimeError(f"/add/data_agent {name!r} -> http={r.status_code} body={str(body)[:200]}")
    return int(aid), "created"


TABLE_FIELDS = ("table_name", "table_schema", "table_type", "table_description", "table_alias",
                "table_category", "primary_key_columns", "business_rules", "row_count_estimate",
                "refresh_frequency", "data_owner", "is_deprecated", "related_tables",
                "common_filters")
COLUMN_FIELDS = ("column_name", "data_type", "column_alias", "column_description",
                 "data_type_precision", "is_primary_key", "is_foreign_key", "foreign_key_table",
                 "foreign_key_column", "is_nullable", "default_value", "is_calculated",
                 "calculation_formula", "calculation_dependencies", "semantic_type",
                 "value_format", "units", "value_range", "common_aggregations", "is_sensitive",
                 "synonyms", "examples")


def _clean(d, fields):
    out = {}
    for k in fields:
        v = d.get(k)
        if isinstance(v, float) and v != v:      # NaN from a dataframe export
            v = None
        out[k] = v
    return out


def ensure_dictionary(api, connection_id, fixture, dry):
    """Import every fixture table/column that the target does not already have."""
    have = {t["table_name"]: t for t in api.rows(f"/api/tables/{connection_id}", "tables")}
    t_new = c_new = 0
    for t in fixture["tables"]:
        name = t["table_name"]
        if name in have:
            tid = have[name]["id"]
        else:
            if dry:
                t_new += 1
                c_new += len(t.get("columns") or [])
                continue
            payload = _clean(t, TABLE_FIELDS)
            # table_schema mirrors the source rows (NULL on the dev tree: the
            # schema prefix lives inside table_name, e.g. "TS.sales", and the
            # NLQ engine never reads table_schema). Do NOT default it to
            # 'dbo'/'public' — that would render a different dictionary.
            payload.update({"id": None, "connection_id": connection_id,
                            "is_deprecated": int(bool(payload.get("is_deprecated") or 0))})
            r = api.post("/api/table/update", payload)
            body = api.j(r) or {}
            tid = body.get("id")
            if r.status_code not in (200, 201) or not tid:
                raise RuntimeError(f"/api/table/update {name!r} -> http={r.status_code} {str(body)[:200]}")
            t_new += 1
        have_cols = {c["column_name"] for c in api.rows(f"/api/columns/{tid}", "columns")}
        for c in t.get("columns") or []:
            if c["column_name"] in have_cols:
                continue
            if dry:
                c_new += 1
                continue
            payload = _clean(c, COLUMN_FIELDS)
            for flag in ("is_primary_key", "is_foreign_key", "is_calculated", "is_sensitive"):
                payload[flag] = int(bool(payload.get(flag) or 0))
            payload["is_nullable"] = int(bool(payload.get("is_nullable", 1)))
            payload["data_type"] = payload.get("data_type") or "VARCHAR"
            payload.update({"id": None, "table_id": tid})
            r = api.post("/api/column/update", payload)
            if r.status_code not in (200, 201):
                raise RuntimeError(f"/api/column/update {name}.{c['column_name']} -> "
                                   f"http={r.status_code} {r.text[:200]}")
            c_new += 1
    return t_new, c_new


def ensure_secret(api, name, value, dry):
    if name in secret_names(api):
        return "present"
    if dry:
        return "would create"
    r = api.post("/api/local-secrets", {"name": name, "value": value,
                                        "description": "regression SFTP test rig (throwaway)",
                                        "category": "api_keys"})
    if r.status_code >= 400:
        raise RuntimeError(f"/api/local-secrets {name} -> http={r.status_code} {r.text[:160]}")
    return "created"


def ensure_general_agent(api, name, dry):
    row = general_agent_by_name(api, name)
    if row:
        return agent_id_of(row), "present"
    if dry:
        return None, "would create"
    r = api.post("/add/agent", {"agent_id": 0, "agent_description": name,
                                "agent_objective": "Answer precisely and concisely. Used by the "
                                                   "regression suite as a simulated user and judge.",
                                "agent_enabled": True, "tool_names": [], "core_tool_names": []})
    body = api.j(r) or {}
    aid = body.get("agent_id") or body.get("id")
    if not aid and str(body.get("message", "")).strip().isdigit():
        aid = int(body["message"])
    if not aid:
        row = general_agent_by_name(api, name)
        aid = agent_id_of(row) if row else None
    if not aid:
        raise RuntimeError(f"/add/agent {name!r} -> http={r.status_code} {str(body)[:200]}")
    return int(aid), "created"


# ------------------------------------------------------------------ export / verify

def export_fixture(api, connection_id):
    tables = api.rows(f"/get/tables/{connection_id}")
    if not tables:
        tables = api.rows(f"/api/tables/{connection_id}", "tables")
    out = []
    for t in sorted(tables, key=lambda x: x["table_name"]):
        cols = api.rows(f"/api/columns/{t['id']}", "columns")
        row = _clean(t, TABLE_FIELDS)
        row["columns"] = [_clean(c, COLUMN_FIELDS) for c in cols]
        out.append(row)
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    doc = {"source": api.base, "connection": ORACLE_CONN_NAME,
           "exported": datetime.datetime.now().isoformat(timespec="seconds"),
           "tables": out}
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, default=str)
    ncols = sum(len(t["columns"]) for t in out)
    log(f"exported {len(out)} tables / {ncols} columns -> {FIXTURE}")
    return len(out), ncols


def verify(api, connection_id, agent_id):
    r = api.get(f"/api/discover/schema/{connection_id}?table=TS.sales&values=0", timeout=120)
    body = api.j(r) or {}
    documented = bool(body.get("documented"))
    log(f"discover/schema TS.sales: http={r.status_code} documented={documented} "
        f"source={body.get('source')} columns={len(body.get('columns') or [])}")
    api.get("/data_assistants", timeout=45)      # seed the chat session (pack 15 lesson)
    t0 = time.time()
    r = api.post("/chat/data", {"agent_id": str(agent_id),
                                "question": "How many stores are there in total?",
                                "history": [], "format_table_as_json": False,
                                "caution_level": "medium"}, timeout=240)
    text = json.dumps(api.j(r) or r.text)
    ok = r.status_code == 200 and re.search(r"\b15\b", text) is not None
    log(f"/chat/data oracle question: http={r.status_code} contains-15={ok} "
        f"({time.time() - t0:.1f}s) tail={text[-160:]!r}")
    return documented and ok


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="'local' or an installed host, e.g. 10.0.0.6")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--dry-run", action="store_true", help="report what would be created")
    ap.add_argument("--verify", action="store_true",
                    help="ask the oracle agent the pack-15 question and require 15")
    ap.add_argument("--export", action="store_true",
                    help="refresh fixtures/airdb2_dictionary.json from this target and exit")
    args = ap.parse_args()

    is_local = args.target in ("local", "localhost", "127.0.0.1")
    host = "localhost" if is_local else args.target
    base = args.base_url or os.environ.get("REGP_BASE") or f"http://{host}:5001"
    log(f"target={'LOCAL' if is_local else 'INSTALLED ' + host} base={base} "
        f"{'(dry run)' if args.dry_run else ''}")
    api = Api(base, args.user, args.password)

    if args.export:
        row = connection_by_name(api, ORACLE_CONN_NAME)
        if not row:
            log(f"no connection named {ORACLE_CONN_NAME!r} on {base}; nothing to export")
            return 2
        export_fixture(api, int(row["id"]))
        return 0

    if not os.path.isfile(FIXTURE):
        log(f"fixture missing: {FIXTURE} — run once with --target local --export")
        return 2
    with open(FIXTURE, encoding="utf-8") as fh:
        fixture = json.load(fh)

    manifest = {"target": host, "base": base, "when": datetime.datetime.now().isoformat(timespec="seconds"),
                "dry_run": args.dry_run}
    dry = args.dry_run

    cid, note = ensure_connection(api, ORACLE_CONN_NAME, "AIRDB2", dry)
    log(f"connection {ORACLE_CONN_NAME!r}: id={cid} ({note})")
    manifest["airdb2_connection_id"] = cid
    aid, note = ensure_connection(api, AIRDB_CONN_NAME, "AIRDB", dry)
    log(f"connection {AIRDB_CONN_NAME!r}: id={aid} ({note})")
    manifest["airdb_connection_id"] = aid

    if cid:
        t_new, c_new = ensure_dictionary(api, cid, fixture, dry)
        have = api.rows(f"/api/tables/{cid}", "tables")
        log(f"dictionary on {ORACLE_CONN_NAME!r}: +{t_new} tables / +{c_new} columns "
            f"(now {len(have)} tables documented; fixture has {len(fixture['tables'])})")
        manifest["dictionary_tables"] = len(have)

        oid, note = ensure_data_agent(api, ORACLE_AGENT_NAME, ORACLE_AGENT_OBJECTIVE, cid, dry)
        log(f"data agent {ORACLE_AGENT_NAME!r}: id={oid} ({note})")
        manifest["oracle_agent_id"] = oid
        sid, note = ensure_data_agent(api, SECOND_AGENT_NAME, SECOND_AGENT_OBJECTIVE, cid, dry)
        log(f"data agent {SECOND_AGENT_NAME!r}: id={sid} ({note})")
        manifest["second_agent_id"] = sid
    else:
        oid = None

    for name in SECRET_NAMES:
        log(f"secret {name}: {ensure_secret(api, name, SECRET_VALUE, dry)}")
    gid, note = ensure_general_agent(api, SIM_AGENT_NAME, dry)
    log(f"general agent {SIM_AGENT_NAME!r}: id={gid} ({note})")
    manifest["sim_agent_id"] = gid

    if args.verify and not dry and cid and oid:
        manifest["verified"] = verify(api, cid, oid)
        log(f"VERIFY: {'PASS' if manifest['verified'] else 'FAIL'}")

    os.makedirs(MANIFESTS, exist_ok=True)
    path = os.path.join(MANIFESTS, f"seed_{host}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, default=str)
    log(f"manifest -> {path}")
    return 0 if manifest.get("verified", True) else 1


if __name__ == "__main__":
    sys.exit(main())
