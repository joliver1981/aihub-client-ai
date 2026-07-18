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


# Follow-up cues within an ONGOING code-flow authoring conversation (AIHUB-0035).
# Used only when a code_flow_context marker is set, so these can be terse — the
# point is to keep "now dry-run it" / "wire the fail edge" / "schedule it" in
# `converse` (where the code-flow tools live) instead of re-classifying them as a
# fresh 'build' that goes to the visual Builder.
_CODE_FLOW_FOLLOWUP = re.compile(
    r"\b(dry[- ]?run|code\s?flow|the\s+flow|wire\b|add\s+(?:a\s+)?step|step\s*\d|"
    r"promote\s+it|schedule\s+it|run\s+it|run\s+the\s+flow|upload\s+step|alert\s+(?:step|handler)|"
    r"failure\s+(?:handler|branch|edge)|fail[- ]edge|update\s+step|fix\s+step)\b", re.I)
_CODE_FLOW_CONTINUE = re.compile(
    r"^\s*(continue|proceed|go\s+ahead|do\s+it|yes[.! ]?|next|keep\s+going|finish(?:\s+it)?)\b", re.I)


def looks_like_code_flow_followup(text: str) -> bool:
    """True for a natural follow-up turn in a code-flow conversation. Matches
    code-flow-specific cues anywhere, or a TERSE standalone continuation
    ('continue', 'do it') — kept short so an unrelated long message that happens
    to contain 'continue' doesn't match. An object-build follow-up ('now create
    a data agent') does NOT match, so it still routes to the Builder."""
    t = (text or "").strip()
    if _CODE_FLOW_FOLLOWUP.search(t):
        return True
    return len(t.split()) <= 6 and bool(_CODE_FLOW_CONTINUE.match(t))


# ── Native-agent (CC_AGENT="native") visual-workflow routing ──────────────
# Under the native A/B agent, a build request that is about a VISUAL WORKFLOW
# itself is handled in `converse` with CC's own deterministic workflow tools
# instead of the builder delegation. Phase 1 keeps NON-workflow platform
# objects (agents, MCP, knowledge bases, custom tools, connections) on the
# classic builder path, so a request naming one of those — even alongside a
# workflow — conservatively keeps the builder path, which can build both.

_VISUAL_WORKFLOW_SIGNAL = re.compile(r"\b(?:visual\s+)?work\s?flows?\b|\bcanvas\b", re.I)

_NON_WORKFLOW_OBJECT = re.compile(
    r"\b(agent|assistant|chatbot|mcp\s*server|mcp|knowledge\s*base|custom\s+tool|"
    r"data\s+agent|integration)\b", re.I)

# Follow-up cues within an ONGOING native workflow-authoring conversation —
# the visual_workflow twin of _CODE_FLOW_FOLLOWUP, matched only when a
# code_flow_context marker with kind="visual_workflow" is set (which only the
# native agent's workflow tools write, so classic routing never sees this).
_WORKFLOW_FOLLOWUP = re.compile(
    r"\b(work\s?flows?|the\s+flow|node|step|wire|unwire|connect|edge|"
    r"start\s+node|variable|run\s+it|test\s+it|save\s+it|schedule\s+it|"
    r"add\s+(?:a\s+|an\s+)?\w{0,20}\s?(?:node|step)|fix|update|remove|delete|rename)\b", re.I)


def looks_like_visual_workflow_build(text: str) -> bool:
    """True when a build-intent turn is about a visual workflow itself — the
    native agent builds those with its own tools. False when a non-workflow
    platform object is (also) requested: those keep the classic builder path
    in phase 1, and the builder can include the workflow part too."""
    t = text or ""
    if not _VISUAL_WORKFLOW_SIGNAL.search(t):
        return False
    return not (_NON_WORKFLOW_OBJECT.search(t) or _OBJECT_BUILD_CONN.search(t))


def looks_like_workflow_followup(text: str) -> bool:
    """Follow-up matcher for an ongoing NATIVE workflow-authoring session
    (marker kind="visual_workflow"). Same shape as looks_like_code_flow_followup:
    workflow-ish cues anywhere, or a terse standalone continuation."""
    t = (text or "").strip()
    if _WORKFLOW_FOLLOWUP.search(t):
        return True
    return len(t.split()) <= 6 and bool(_CODE_FLOW_CONTINUE.match(t))


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
