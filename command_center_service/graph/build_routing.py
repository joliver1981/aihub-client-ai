"""
Deterministic build-shape signals for classify_intent.

Kept dependency-free (only `re`) and separate from the heavy graph.nodes module
so the routing precision guard is unit-testable in isolation.

`looks_like_code_process()` is a HIGH-PRECISION fast-path: it returns True only
for a clear code/data PROCESS (parse files, look something up in a database,
reconcile/transform, move files, on a schedule, alert on failure) with NO
object-builder signal. Such a request belongs to the automation family — the
`converse` node owns BOTH the automation tools and the code-flow tools — and
must never degrade to the visual Workflow Builder (AIHUB-0033 F1: a "build me a
nightly PDF→DB→SFTP process, alert on failure" request routed to the visual
builder, which has none of the code-flow tools, producing an empty flow).

Because it only fires on strong, unambiguous signals, an ambiguous build request
still falls through to the LLM build-shape decision in `_classify_build_shape`.
"""
import re

# Each entry is one INDEPENDENT "this is a code/data process" signal.
_PROCESS_SIGNALS = [
    # parse / read structured files or documents
    re.compile(r"\b(pdf|excel|xlsx?|csv|spreadsheet|parse|parsing|extract|scrape)\b", re.I),
    # move files between systems
    re.compile(r"\b(sftp|ftps?|s3|upload|download|transfer|drop\s+(?:it|the\s+file)|push\s+(?:it|the\s+file))\b", re.I),
    # transform / reconcile / enrich data
    re.compile(r"\b(reconcil\w*|transform|enrich|aggregate|pipeline|etl|normali[sz]e|de-?dup\w*)\b", re.I),
    # look something up in a database as a processing step
    re.compile(r"\blook\s?up\b|\b(query|read|pull|fetch|join)\b.{0,40}\b(database|db|table|airdb|erpdb|erp|sql|records?)\b", re.I),
    # runs on a schedule (a hallmark of an automation, not a chat)
    re.compile(r"\b(nightly|every\s+night|each\s+night|daily|every\s+day|weekly|hourly|on\s+a\s+schedule|scheduled|cron)\b", re.I),
    # failure branching / alerting
    re.compile(r"\bif\b.{0,30}\bfail|\bon\s+(?:any\s+)?failure\b|\bsend\s+an?\s+alert\b|\balert\s+(?:me|us|someone)\b|\bnotify\s+(?:me|us|on)\b", re.I),
    # explicit "code process" framing (this group ALONE is sufficient)
    re.compile(r"\bautomat\w+\b|\bcode\s?flow\b|\bmulti-?step\s+(?:process|automation|pipeline|flow|job)\b|\bbatch\s+job\b|\bnightly\s+job\b", re.I),
]
_STRONG_SIGNAL = _PROCESS_SIGNALS[6]  # the explicit "automate / code flow / multi-step process" group

# Objects that are (almost) always Builder targets — a hard veto: if the user
# asks for one of these, respect that (Builder object, or a visual workflow they
# want to see/edit), not an automation.
_OBJECT_BUILDER_SIGNALS = re.compile(
    r"\b(agent|assistant|chatbot|mcp\s*server|mcp|knowledge\s*base|custom\s+tool|"
    r"data\s+agent|visual\s+workflow|workflow|canvas|dashboard)\b", re.I)

# 'connection'/'secret'/'credential' are NORMAL words in a code process that
# REFERENCES an existing one by name (aihub.connection('AIRDB')). Veto only when
# the user is CREATING one (AIHUB-0033 F1b-R2: the verbatim prompt "...look up
# each employee using the existing 'AIRDB' connection..." must NOT be vetoed —
# referencing an existing connection is the documented Code Flow pattern).
_OBJECT_BUILD_CONN = re.compile(
    r"\b(create|build|make|set\s?up|configure|add|new|register|provision)\b"
    r"[^.]{0,25}\b(connection|secret|credentials?)\b", re.I)


def looks_like_code_process(text: str) -> bool:
    """True for a clear code/data process with no object-builder signal.

    Precision rules: an explicit "automate / code flow / multi-step process"
    phrase is sufficient on its own; otherwise require >=2 INDEPENDENT process
    signals. Vetoed by an object-builder signal (agent, MCP, knowledge base,
    custom tool, or an explicit 'workflow'/'canvas' the user wants), or by a
    CREATE-a-connection/secret ask — but NOT by merely referencing an existing
    connection/secret by name, which is the normal Code Flow pattern."""
    t = text or ""
    if _OBJECT_BUILDER_SIGNALS.search(t) or _OBJECT_BUILD_CONN.search(t):
        return False
    if _STRONG_SIGNAL.search(t):
        return True
    hits = sum(1 for p in _PROCESS_SIGNALS if p.search(t))
    return hits >= 2
