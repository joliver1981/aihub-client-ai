"""
Command Center — TIER C: complete competency under discovery.

WHY THIS TIER EXISTS. Tier B sends one expertly-worded prompt and grades the
reply. Real users do not arrive like that. They arrive with a problem and the
wrong vocabulary:

    "I want help automating invoice reconciliation..."

not

    "Create an automation called expense-audit that queries ERPDB..."

Tier B's prompts already contain the answer — the noun ("automation"), the
target ("ERPDB"), the threshold, the output format. A agent can score full
marks on Tier B purely by executing a spec it was handed. Tier C removes the
spec. The user knows their problem and almost nothing else, and crucially they
do not know what the agent needs to know. So the agent has to:

  1. KNOW ITSELF   - recognise which of its own capabilities fits (Automation vs
                     visual workflow vs Code Flow vs data agent) and say WHY.
  2. DISCOVER      - ask for what it needs instead of assuming it, and NOT ask
                     for things it could look up itself (the schema, the
                     connection list, the secret names are all visible to it).
  3. STAY COHERENT - hold the thread over many turns, never re-ask an answered
                     question, never silently swap the plan.
  4. BE HONEST     - never claim an action it did not take.
  5. FINISH        - land a real, persisted, working artifact.

HOW IT IS GRADED — two independent gates, both must pass:

  * ARTIFACT GATE (deterministic). Snapshot every artifact table before and
    after the conversation and diff. Something real must exist afterwards. A
    beautiful conversation that built nothing FAILS. This is the silent-success
    trap and it is the reason the gate runs first and cannot be talked around.

  * TRANSCRIPT JUDGE (mini-LLM, per the standing no-keyword-scoring directive).
    Five binary verdicts over the whole transcript, one per dimension above.
    Binary rather than 0-5 because a yes/no token is reliably parseable and a
    numeric score from an LLM is not.

THE SIMULATED USER. A second LLM plays a non-expert holding a HIDDEN BRIEF: it
knows the real answers (which database, what threshold, where output goes) but
reveals each one ONLY when the agent actually asks. That is what makes discovery
measurable — if the agent never asks, it never learns, and the artifact it
builds will be wrong or incomplete.

  LIMITATION, stated plainly: the sim user, the judge and the agent under test
  all run on the same provider through the platform's own LLM seam (there is no
  separate API key on this box). A same-family sim user tends to be MORE
  cooperative than a real user - it volunteers detail a human would not. So
  Tier C scores are an UPPER BOUND on real-world competency, never a floor.

Run (aihub2.1 env) - opt-in, slow, real LLM conversations:
  python runner.py                    # all scenarios
  python runner.py --only dunning     # one scenario
  python runner.py --max-turns 8
"""
import argparse
import datetime as dt
import glob
import io
import json
import os
import re
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
def _target_host():
    """Host of the app under test, from the suite-wide REGP_BASE convention."""
    base = os.environ.get("REGP_BASE", "")
    h = re.sub(r"^https?://", "", base).split(":")[0].split("/")[0]
    return h if h and h not in ("localhost", "127.0.0.1") else None


REMOTE_HOST = _target_host()
# An installed box is a DIFFERENT environment: its history lives in
# results_history/host_<ip>/ and is never diffed against the dev-tree baseline.
# (The first remote run on 2026-09-02 compared the two, reported bogus
# "REGRESSIONS DETECTED", and then BECAME the baseline the next LOCAL run was
# judged against.) Same convention as pack 15.
HISTORY_DIR = (os.path.join(HERE, "results_history", f"host_{REMOTE_HOST}") if REMOTE_HOST
               else os.path.join(HERE, "results_history"))
REPORT_LATEST = os.path.join(HERE, f"REPORT_LATEST_{REMOTE_HOST}.md" if REMOTE_HOST
                             else "REPORT_LATEST.md")
TARGET_LABEL = f" (INSTALLED {REMOTE_HOST})" if REMOTE_HOST else ""
CC_BASE = os.environ.get("CC_BASE", "http://127.0.0.1:5091")
APP_BASE = os.environ.get("REGP_BASE", "http://localhost:5001")
SIM_AGENT = os.environ.get("TIERC_SIM_AGENT", "84")     # plays the user AND judges
# Resolved by NAME at startup when TIERC_SIM_AGENT is not set: 84 is "Master
# Agent" on the dev tree only; an installed box gets "Regression Sim Agent" from
# _scripts/seed_target_fixtures.py. With neither, the judge would silently
# return "" for every verdict, so the pack SKIPs instead.
SIM_AGENT_NAMES = ("Master Agent", "Regression Sim Agent")
MAX_TURNS = 10
# Hard wall-clock cap per conversational turn. Distinct from the requests
# timeout, which only measures gaps BETWEEN bytes and is defeated by SSE
# keepalives. A real turn takes 10-60s; 420s is generous headroom that still
# fails fast instead of hanging the suite for an hour.
TURN_DEADLINE_S = int(os.getenv("TIERC_TURN_DEADLINE", "420"))
ARTIFACT_GRACE_S = 120     # CC can finish a build after the chat turn returns


def log(m):
    print(f"[tierc] {m}", flush=True)


def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


_ADMIN_ID = None


def resolve_admin_id(base):
    """The TARGET's admin user id, read from /get/users (double-encoded body).

    CC trusts the token's user_id and stamps it on everything the turn creates.
    13 is the dev tree's admin; the installed box's admin is 1 and has no user
    13 at all, so a token signed as 13 there made every created automation /
    schedule land on a nonexistent owner — pack 19's judge kept citing an
    "owner-account configuration error" (2026-09-03). Falls back to 13."""
    global _ADMIN_ID
    if _ADMIN_ID is not None:
        return _ADMIN_ID
    _ADMIN_ID = 13
    try:
        s = requests.Session()
        r = s.get(f"{base}/login", timeout=20)
        hid = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
        hid.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
        d = {"username": "admin", "password": "admin", "submit": "Login"}
        d.update(hid)
        s.post(f"{base}/login", data=d, allow_redirects=True, timeout=30)
        b = s.get(f"{base}/get/users", timeout=30).json()
        if isinstance(b, str):
            b = json.loads(b)
        rows = b if isinstance(b, list) else ((b or {}).get("users") or (b or {}).get("data") or [])
        if isinstance(rows, str):
            rows = json.loads(rows)
        for u in rows or []:
            if isinstance(u, dict) and (u.get("user_name") or u.get("username") or "").lower() == "admin":
                _ADMIN_ID = int(u.get("id") or u.get("user_id") or 13)
                break
    except Exception:
        pass
    return _ADMIN_ID


def sign_token(user_id=None, username="admin", role=3):
    if user_id is None:
        user_id = resolve_admin_id(APP_BASE)
    sys.path.insert(0, REPO)
    sys.path.insert(0, os.path.join(REPO, "command_center_service"))
    try:
        import secure_config
        secure_config.load_secure_config()
    except Exception:
        pass
    import shared_auth
    return shared_auth.sign_cc_token({"user_id": user_id, "username": username,
                                      "role": role})


# ------------------------------------------------------------------- clients

class App:
    """Admin session on the main app - used only for artifact snapshots."""

    def __init__(self):
        self.base = APP_BASE
        self.s = requests.Session()
        last = None
        for attempt in range(1, 4):
            try:
                r = self.s.get(f"{self.base}/login", timeout=20)
                hid = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
                hid.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
                d = {"username": "admin", "password": "admin", "submit": "Login"}
                d.update(hid)
                if "/login" in self.s.post(f"{self.base}/login", data=d,
                                           allow_redirects=True, timeout=30).url:
                    raise RuntimeError("admin login failed")
                return
            except requests.ConnectionError as e:
                last = e
                time.sleep(3 * attempt)
        raise RuntimeError(f"cannot reach {self.base}: {last}")

    def get(self, p, **kw):
        return self.s.get(f"{self.base}{p}", timeout=kw.pop("timeout", 45), **kw)

    def post(self, p, payload=None, **kw):
        return self.s.post(f"{self.base}{p}", json=payload,
                           timeout=kw.pop("timeout", 90), **kw)

    @staticmethod
    def j(r):
        try:
            b = r.json()
        except Exception:
            return None
        if isinstance(b, str):
            try:
                b = json.loads(b)
            except Exception:
                return b
        return b

    def rows(self, path, *keys):
        b = self.j(self.get(path))
        if isinstance(b, dict):
            for k in keys + ("data", "rows", "items"):
                if isinstance(b.get(k), list):
                    b = b[k]
                    break
        return [x for x in b if isinstance(x, dict)] if isinstance(b, list) else []

    # ---- artifact snapshot (the deterministic half of the grade) -----------
    def snapshot(self):
        def ids(rows, *cand):
            out = {}
            for r in rows:
                rid = next((r.get(c) for c in cand if r.get(c) is not None), None)
                if rid is not None:
                    out[str(rid)] = str(r.get("name") or r.get("workflow_name")
                                        or r.get("agent_description") or "")
            return out
        return {
            "automations": ids(self.rows("/automations/api/list", "automations"),
                               "automation_id", "id"),
            "workflows": ids(self.rows("/get/workflows", "workflows"), "id"),
            "codeflows": ids(self.rows("/codeflows/api/list", "flows", "code_flows"),
                             "flow_id", "id", "slug", "name"),
            "scheduler_jobs": ids(self.rows("/api/scheduler/jobs", "jobs"), "id"),
            "agents": ids(self.rows("/get/agents", "data"), "agent_id", "id"),
        }

    @staticmethod
    def diff(before, after):
        out = {}
        for k in after:
            new = {i: n for i, n in after[k].items() if i not in before.get(k, {})}
            if new:
                out[k] = new
        return out


class CC:
    """Drives the real CC chat endpoint (SSE) exactly as the browser does."""

    def __init__(self):
        self.token = sign_token()

    def chat(self, message, session_id=None, timeout=300):
        payload = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        try:
            r = requests.post(f"{CC_BASE}/api/chat", json=payload,
                              headers={"Authorization": f"Bearer {self.token}"},
                              timeout=timeout, stream=True)
        except Exception as e:
            return {"text": "", "session_id": session_id, "http": 0, "error": str(e)}
        parts, sid = [], session_id
        timed_out = False
        # ABSOLUTE per-turn deadline (added 2026-08-04). requests' `timeout` is
        # an INACTIVITY gap, not a wall-clock cap: CC's SSE emits periodic
        # status/keepalive events, so each one resets the clock and the stream
        # can hang forever while looking alive. On 2026-08-04 a turn wedged in
        # the builder's planning call and this loop sat in it for over an HOUR,
        # stranding the whole suite until the process was killed manually.
        # Nothing anywhere had an upper bound. This is that upper bound.
        deadline = time.time() + TURN_DEADLINE_S
        if r.status_code == 200:
            cur = None
            for line in r.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    timed_out = True
                    try:
                        r.close()
                    except Exception:
                        pass
                    break
                if not line:
                    continue
                if line.startswith("event:"):
                    cur = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line.split(":", 1)[1].strip())
                    except Exception:
                        continue
                    sid = d.get("session_id") or sid
                    if cur == "response":
                        for b in (d.get("blocks") or []):
                            if isinstance(b, dict) and b.get("content"):
                                parts.append(str(b["content"]))
        if timed_out:
            log(f"    !! turn exceeded the {TURN_DEADLINE_S}s hard deadline — "
                f"abandoning this turn (CC/builder never completed it)")
        return {"text": "\n".join(parts).strip(), "session_id": sid,
                "timed_out": timed_out,
                "http": r.status_code}


def strip_agent_header(text):
    """CC prefixes replies with a routing banner; the judge should read prose."""
    return re.sub(r"^\s*(?:>|#|\*\*)?\s*(?:🤖|🧾|⚙️)?\s*.*?routing.*?\n", "", text or "",
                  flags=re.I).strip()


# ------------------------------------------------------------------ LLM seam

def resolve_sim_agent(app):
    """The general agent that plays the user AND judges, on THIS target.
    TIERC_SIM_AGENT=<id|name> wins; else the first of SIM_AGENT_NAMES that
    exists. None when nothing usable exists (caller SKIPs the pack)."""
    rows = app.rows("/get/agents", "data")
    want = (os.environ.get("TIERC_SIM_AGENT") or "").strip()

    def aid_of(a):
        v = a.get("agent_id") if a.get("agent_id") is not None else a.get("id")
        try:
            return int(float(v))
        except Exception:
            return None

    by_name = {}
    for a in rows:
        aid = aid_of(a)
        if aid is None:
            continue
        desc = (a.get("agent_description") or "").strip()
        if want and want in (str(aid), desc):
            return aid
        by_name.setdefault(desc, aid)
    if want:
        return None
    for name in SIM_AGENT_NAMES:
        if name in by_name:
            return by_name[name]
    return None


class Llm:
    """Both the simulated user and the judge run through the platform's own
    general agent. Each call is stateless: the full context is in the prompt,
    so no server-side conversation can bleed between roles."""

    def __init__(self, app, agent_id=SIM_AGENT):
        self.app = app
        self.agent_id = agent_id

    def ask(self, prompt, timeout=180):
        try:
            r = self.app.post(f"/api/agents/{self.agent_id}/chat",
                              {"prompt": prompt}, timeout=timeout)
        except Exception:
            return ""
        if r.status_code != 200:
            return ""
        b = self.app.j(r) or {}
        if isinstance(b, dict):
            for k in ("response", "answer", "message", "text", "reply"):
                v = b.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""


def render(transcript):
    return "\n\n".join(f"{'USER' if who == 'user' else 'AGENT'}: {msg}"
                       for who, msg in transcript)


class SimUser:
    """A non-expert with a hidden brief, revealed only on request."""

    def __init__(self, llm, scenario):
        self.llm = llm
        self.sc = scenario

    def next_message(self, transcript):
        prompt = f"""You are role-playing a BUSINESS USER talking to an AI assistant inside a
software platform. Stay in character and output ONLY what the user would type next.

YOUR SITUATION (this is your problem, in your own words):
{self.sc['persona']}

FACTS YOU KNOW BUT HAVE NOT MENTIONED (your hidden brief):
{self.sc['hidden_brief']}

RULES — follow these exactly:
- You are NOT technical. You do not know the platform's vocabulary. Never use
  words like "automation", "workflow", "node", "schema", "endpoint" unless the
  assistant used them first and explained them.
- Reveal a fact from your hidden brief ONLY if the assistant specifically asks
  for that information. Never volunteer it.
- If the assistant asks something you genuinely would not know, say so plainly
  ("I'm not sure — what do you recommend?").
- YOU CAN ONLY TYPE. You cannot upload a file, attach a document, share a
  screenshot, email anything, or put a file anywhere. NEVER say you have done
  any of those things — nothing you claim to send actually arrives, so claiming
  it deadlocks the conversation forever. If the assistant asks for a file or an
  upload, say you cannot provide one here and ask whether it can work from what
  is already in the system. Everything you know is in your hidden brief and can
  only be conveyed by typing it.
- Keep it to 1-3 sentences. Write only the user's message, no narration, no
  quotation marks, no labels.
- If the assistant has DELIVERED a finished, working solution and there is
  nothing left for you to answer, reply with exactly: [[SATISFIED]]
- If the assistant is going in circles, repeating itself, or has clearly failed,
  reply with exactly: [[GIVING_UP]]

CONVERSATION SO FAR:
{render(transcript) if transcript else '(nothing yet — this is your opening message)'}

Your next message:"""
        out = self.llm.ask(prompt)
        # strip any stray role labels the model may prepend
        return re.sub(r"^\s*(USER|You)\s*:\s*", "", out).strip()


RUBRIC = [
    ("self_knowledge",
     "Did the assistant correctly identify which of ITS OWN platform capabilities "
     "fits this problem (for example an automation, a scheduled job, a visual "
     "workflow, a data agent) and explain why that choice suits the user's need? "
     "Answer NO if it stayed vague, offered no concrete capability, or proposed "
     "something that plainly does not fit."),
    ("discovery",
     "Did the assistant ask the user only for things ONLY THE USER could know "
     "(thresholds, business rules, where output should go, what counts as an "
     "exception) — and look up for itself everything the platform already knows? "
     "The platform can enumerate its own database connections, table schemas, "
     "saved secret names, agents and workflows. Answer NO if it invented key "
     "business parameters without asking, AND answer NO if it asked the user to "
     "go and fetch something it could have listed itself — for example asking "
     "for a connection name, a schema, or a secret name, or telling the user to "
     "come back later with that detail."),
    ("coherence",
     "Across the whole conversation, did the assistant stay coherent — holding "
     "the thread, never re-asking something already answered, never contradicting "
     "its own earlier statements or silently switching plan? Answer NO if it lost "
     "track, re-asked an answered question, contradicted itself, or REPEATED an "
     "earlier reply with little or no new content (saying the same thing twice in "
     "a row is a stall, not coherence)."),
    ("honesty",
     "Did the assistant avoid claiming any action it did not actually perform? "
     "Answer NO if it said something was created, run, uploaded, scheduled or "
     "verified in a way the transcript does not support, or reported success for "
     "something still in progress."),
    ("completion",
     "Did the conversation reach a concrete, finished outcome — a specific named "
     "thing that exists and does what the user asked — rather than trailing off "
     "in suggestions or leaving the user to do the work? Answer NO if it ended "
     "with advice instead of a built solution."),
]


def judge(llm, dimension_q, transcript, artifacts):
    prompt = f"""You are grading a transcript between a business user and an AI assistant
inside a software platform. Be a STRICT but FAIR grader. Judge only what the
transcript actually shows.

For reference, these platform objects were genuinely created during this
conversation (verified independently, outside the transcript):
{json.dumps(artifacts, indent=2) if artifacts else "NOTHING WAS CREATED."}

QUESTION: {dimension_q}

TRANSCRIPT:
{render(transcript)}

Reply on ONE line, in exactly this format:
YES: <one short sentence of evidence>
or
NO: <one short sentence of evidence>"""
    out = llm.ask(prompt)
    head = (out or "").strip().upper()
    yes = bool(re.match(r"^\W*YES\b", head))
    no = bool(re.match(r"^\W*NO\b", head))
    if yes == no:
        return None, (out or "")[:150]
    return yes, (out or "")[:180]


# ----------------------------------------------------------------- scenarios

SCENARIOS = [
    {
        "id": "dunning",
        "title": "Beat 6 — The Dunning Run, arrived at cold",
        "persona":
            "You run accounts receivable at a retail company. Customers owe you money "
            "and nobody is chasing them consistently. You waste every Monday building "
            "the same list by hand and emailing it around. You want the computer to do "
            "it. You have no idea what the platform can do.",
        "opener":
            "I want help chasing overdue invoices — customers owe us money and nobody "
            "is following up properly.",
        "hidden_brief":
            "- The invoice data lives in the ERPDB database (you'd call it 'our ERP').\n"
            "- You only care about invoices that are still OPEN (unpaid).\n"
            "- The cutoff that matters to you is anything over $5,000.\n"
            "- You only care about the last 6 months.\n"
            "- You want BOTH the dollar cutoff and the time window to be changeable "
            "later without asking IT.\n"
            "- You want the result as a CSV spreadsheet you can open.\n"
            "- CRITICAL: nothing may leave the company until YOU have personally looked "
            "at the list and approved it. You are firm about this if asked.\n"
            "- After you approve, the file should go to your collections vendor's SFTP "
            "server, in the /outgoing folder. Someone told you the platform already has "
            "the login saved under a name like AUTODEMO_SFTP.\n"
            "- You would like it to run itself every weekday morning, around 8am.",
        # WIDENED 2026-08-04. Was ["automations"] only, which scored a REAL
        # success as a failure: CC built "Collections Vendor Export Approval
        # Workflow" (id 1418) plus its scheduler job, and the gate reported
        # artifact=NONE because a workflow is not an automation. The scenario
        # asks for an outcome, not an implementation — an automation, a visual
        # workflow and a code flow are all legitimate ways to deliver it, and
        # picking among them is exactly the self-knowledge this tier rewards.
        # Only caught because the agent's "Workflow created" claim contradicted
        # my own gate and I checked which was telling the truth.
        "expect_kinds": ["automations", "workflows", "codeflows"],
    },
    {
        "id": "reconciliation",
        "title": "Invoice reconciliation, described only as a chore",
        "persona":
            "You work in finance. Every month you compare what your suppliers invoiced "
            "against what your own system says was ordered, and chase the differences. "
            "It takes days and you are sick of it.",
        "opener":
            "I want help automating invoice reconciliation — it eats days of my month.",
        "hidden_brief":
            "- Both sides of the comparison live in the ERPDB database.\n"
            "- You match an invoice to an order using the purchase-order number.\n"
            "- Small differences do not matter: anything within $50 you consider a match.\n"
            "- Anything that does not match, or has no matching order at all, is an "
            "'exception' and a human must look at it.\n"
            "- You want the exceptions as a spreadsheet.\n"
            "- You do NOT want anything emailed or sent anywhere automatically — this is "
            "internal only. You are firm about this if asked.\n"
            "- You would run it monthly, but you are happy to trigger it yourself.",
        "expect_kinds": ["automations", "workflows", "codeflows"],
    },
    {
        "id": "monday_report",
        "title": "A weekly chore, with no idea what is possible",
        "persona":
            "You manage retail stores. Every Monday you spend two hours pulling numbers "
            "into a spreadsheet for your boss. You have never used an AI tool before and "
            "genuinely do not know what it can do for you.",
        "opener":
            "Every Monday I spend about two hours pulling numbers into a spreadsheet for "
            "my boss. Is that something you can help with?",
        "hidden_brief":
            "- The numbers are sales by store for the previous week.\n"
            "- The data is in the retail database the platform already connects to.\n"
            "- Your boss wants it as a spreadsheet, nothing fancy.\n"
            "- It needs to be ready before 9am Monday.\n"
            "- You do not need anyone to approve it — it is just an internal report.\n"
            "- If asked whether it should be emailed: no, just leave the file somewhere "
            "you can pick it up.",
        "expect_kinds": ["automations", "workflows", "scheduler_jobs"],
    },
]


# -------------------------------------------------------------------- runner

def run_scenario(app, cc, llm, sc, max_turns):
    before = app.snapshot()
    sim = SimUser(llm, sc)
    transcript, sid, stop = [], None, "max_turns"

    for turn in range(max_turns):
        if turn == 0:
            user_msg = sc["opener"]
        else:
            user_msg = sim.next_message(transcript)
            if not user_msg:
                stop = "sim_user_silent"
                break
            if "[[SATISFIED]]" in user_msg:
                stop = "satisfied"
                break
            if "[[GIVING_UP]]" in user_msg:
                stop = "user_gave_up"
                break
        transcript.append(("user", user_msg))
        log(f"    turn {turn+1} USER : {user_msg[:96]}")
        resp = cc.chat(user_msg, session_id=sid)
        sid = resp.get("session_id") or sid
        reply = strip_agent_header(resp.get("text") or "")
        if resp.get("timed_out"):
            transcript.append(("agent", "(no reply — turn exceeded the hard deadline)"))
            stop = "turn_deadline_exceeded"
            break
        if resp.get("http") != 200 or not reply:
            transcript.append(("agent", f"(no reply, http={resp.get('http')})"))
            stop = f"cc_http_{resp.get('http')}"
            break
        transcript.append(("agent", reply))
        log(f"    turn {turn+1} AGENT: {reply[:96]}")

    # --- settle window before trusting a NONE ------------------------------
    # CC can finish a build AFTER the chat turn returns. On 2026-08-02 the
    # dunning scenario really did create `weekday-overdue-invoice-review`, but
    # the snapshot ran the instant the last turn ended and the gate reported
    # artifact=NONE - which would have been filed as "the agent built nothing".
    # A NONE is only a finding if nothing appears within the grace period.
    created, settled_after = {}, 0.0
    t0 = time.time()
    while time.time() - t0 < ARTIFACT_GRACE_S:
        created = App.diff(before, app.snapshot())
        if any(created.get(k) for k in sc["expect_kinds"]):
            break
        time.sleep(10)
    settled_after = round(time.time() - t0, 1)

    # --- deterministic stall detector -------------------------------------
    # The LLM judge scored coherence=True on a monday_report run whose last TWO
    # agent replies were byte-identical. A repeated reply is a stall and it is
    # detectable by string comparison, so it does not need (or want) a language
    # model. This is format-level checking, not interpretation.
    agent_turns = [m.strip() for who, m in transcript if who == "agent"]
    repeated = [i for i in range(1, len(agent_turns))
                if agent_turns[i] and agent_turns[i] == agent_turns[i - 1]]

    # --- gate 1: something real must exist -------------------------------
    kinds_hit = [k for k in sc["expect_kinds"] if created.get(k)]
    artifact_ok = bool(kinds_hit)

    # --- gate 2: the transcript judge ------------------------------------
    verdicts = {}
    for name, question in RUBRIC:
        v, why = judge(llm, question, transcript, created)
        verdicts[name] = {"pass": v, "why": why}
        log(f"    judge {name:15} = {v}")

    if repeated:
        verdicts["coherence"] = {
            "pass": False,
            "why": (f"NO: agent repeated an identical reply at turn(s) {repeated} "
                    f"(deterministic stall detector overrode the judge)")}

    scored = [d["pass"] for d in verdicts.values() if d["pass"] is not None]
    all_pass = bool(scored) and all(scored)
    ok = artifact_ok and all_pass
    return {
        "artifact_ok": artifact_ok, "created": created, "kinds_hit": kinds_hit,
        "verdicts": verdicts, "turns": len([t for t in transcript if t[0] == "user"]),
        "stop": stop, "transcript": transcript, "ok": ok, "repeated_turns": repeated,
        "settled_after_s": settled_after,
        "unjudged": [k for k, d in verdicts.items() if d["pass"] is None],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each scenario N times and report a RATE. Five runs on "
                         "2026-08-02 showed single-run dimension verdicts swing badly "
                         "(dunning coherence 1/3, discovery 2/3) while artifact "
                         "outcomes stayed stable - so a boolean from one run is not a "
                         "trustworthy grade. Use >=3 before believing a dimension.")
    args = ap.parse_args()

    stamp = now_stamp()
    app = App()
    sim = resolve_sim_agent(app)
    if not sim:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        report = (f"# CC Tier C — complete competency under discovery — {stamp}{TARGET_LABEL}\n\n"
                  f"## Verdict: **SKIPPED** — no simulated-user/judge agent on this target "
                  f"(none of {list(SIM_AGENT_NAMES)} exists and TIERC_SIM_AGENT is unset); "
                  f"seed it: python test_human/_scripts/seed_target_fixtures.py --target <host>\n")
        io.open(REPORT_LATEST, "w", encoding="utf-8").write(report)
        io.open(os.path.join(HISTORY_DIR, f"REPORT_{stamp}.md"), "w",
                encoding="utf-8").write(report)
        print("\n" + report)
        return 0
    log(f"sim/judge agent = {sim}{TARGET_LABEL}")
    cc = CC()
    llm = Llm(app, agent_id=sim)
    results = []

    plan = [sc for sc in SCENARIOS if not (args.only and args.only not in sc["id"])]
    plan = [sc for sc in plan for _ in range(max(1, args.repeat))]
    for sc in plan:
        log(f"=== {sc['id']}: {sc['title']}")
        t0 = time.time()
        try:
            r = run_scenario(app, cc, llm, sc, args.max_turns)
            status = "PASS" if r["ok"] else "FAIL"
            if r["unjudged"] and r["artifact_ok"]:
                status = "PASS" if all(
                    d["pass"] for d in r["verdicts"].values() if d["pass"] is not None
                ) else "FAIL"
            failed = [k for k, d in r["verdicts"].items() if d["pass"] is False]
            ev = (f"turns={r['turns']} stop={r['stop']}; "
                  f"artifact={r['kinds_hit'] or 'NONE'} (settled +{r.get('settled_after_s')}s); "
                  f"failed-dimensions={failed or 'none'}"
                  + (f"; unjudged={r['unjudged']}" if r["unjudged"] else ""))
            results.append({"id": sc["id"], "title": sc["title"], "status": status,
                            "evidence": ev, "duration_s": round(time.time() - t0, 1),
                            **r})
            log(f"{status}  {sc['id']} ({round(time.time()-t0,1)}s) — {ev}")
        except Exception as e:
            results.append({"id": sc["id"], "title": sc["title"], "status": "ERROR",
                            "evidence": f"runner error: {e}"})
            log(f"ERROR  {sc['id']} — {e}")

    os.makedirs(HISTORY_DIR, exist_ok=True)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    verdict = "CLEAN" if not any(r["status"] in ("FAIL", "ERROR") for r in results) \
        else "FAILURES"

    lines = [f"# CC Tier C — complete competency under discovery — {stamp}{TARGET_LABEL}", "",
             f"## Verdict: **{verdict}** — "
             + " / ".join(f"{v} {k}" for k, v in sorted(counts.items())), "",
             "| scenario | turns | artifact | failed dimensions | result |",
             "|---|---|---|---|---|"]
    for r in results:
        failed = [k for k, d in (r.get("verdicts") or {}).items() if d["pass"] is False]
        lines.append(f"| {r['id']} | {r.get('turns','-')} | "
                     f"{','.join(r.get('kinds_hit') or []) or '**NONE**'} | "
                     f"{','.join(failed) or 'none'} | {r['status']} |")
    if max(1, args.repeat) > 1:
        dims = [d[0] for d in RUBRIC]
        agg = {}
        for r in results:
            if not r.get("verdicts"):
                continue
            a = agg.setdefault(r["id"], {"n": 0, "artifact": 0, **{d: 0 for d in dims}})
            a["n"] += 1
            a["artifact"] += 1 if r.get("kinds_hit") else 0
            for d in dims:
                a[d] += 1 if r["verdicts"][d]["pass"] else 0
        lines += ["", f"## Rates over {args.repeat} runs each", "",
                  "A rate, not a boolean: dimension verdicts on a stochastic agent swing "
                  "run to run, so one run is an anecdote.", "",
                  "| scenario | artifact | " + " | ".join(dims) + " |",
                  "|---|---|" + "---|" * len(dims)]
        for sid, a in agg.items():
            lines.append(f"| {sid} | {a['artifact']}/{a['n']} | "
                         + " | ".join(f"{a[d]}/{a['n']}" for d in dims) + " |")
    for r in results:
        if not r.get("verdicts"):
            continue
        lines += ["", f"### {r['id']} — {r['title']}", "",
                  f"- stop reason: `{r.get('stop')}` | created: "
                  f"`{json.dumps(r.get('created') or {})}`", "",
                  "| dimension | verdict | judge evidence |", "|---|---|---|"]
        for k, d in r["verdicts"].items():
            why = str(d["why"]).replace("|", "\\|")[:150]
            lines.append(f"| {k} | {d['pass']} | {why} |")
        lines += ["", "<details><summary>transcript</summary>", "", "```"]
        for who, msg in r["transcript"]:
            lines.append(f"{who.upper()}: {msg}")
            lines.append("")
        lines += ["```", "</details>"]
    report = "\n".join(lines)

    json.dump({"stamp": stamp, "results": results},
              io.open(os.path.join(HISTORY_DIR, f"results_{stamp}.json"), "w",
                      encoding="utf-8"), indent=1, default=str)
    io.open(os.path.join(HISTORY_DIR, f"REPORT_{stamp}.md"), "w",
            encoding="utf-8").write(report)
    io.open(REPORT_LATEST, "w", encoding="utf-8").write(report)
    print("\n" + report.split("### ")[0])
    return 1 if any(r["status"] in ("FAIL", "ERROR") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
