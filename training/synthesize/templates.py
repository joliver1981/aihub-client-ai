"""Template-driven synthetic plan generation.

Enumerates combinations of:
  - SKELETONS:  structural shape ("DB -> AI Action -> Alert", "Folder -> Loop -> Doc -> AI Extract -> End Loop", ...)
  - DOMAINS:    business context (invoices, stock, tickets, expenses, compliance, ...)

For each (skeleton, domain) pair, asks the LLM to produce BOTH:
  - A realistic numbered plan describing what a user of that domain would ask for.
  - The matching workflow commands JSON.

Every generated pair is fed through the four-gate judge (training.synthesize.judge.pass_all_gates).
Records that fail any gate are written to a parallel rejected.jsonl for inspection.

Cost: each accepted record costs ~2 LLM calls (generation + judge). Use --dry-run to preview counts and --limit to cap a run.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.capture.extract_seeds import load_system_prompt_text
from training.llm import complete, usage_summary
from training.synthesize.judge import pass_all_gates

logger = logging.getLogger("synthesize.templates")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


SKELETONS = [
    # Original 10
    ("db_alert",             "Database -> Alert"),
    ("db_ai_alert",          "Database -> AI Action -> Alert"),
    ("db_ai_excel",          "Database -> AI Action -> Excel Export"),
    ("folder_loop_ai_alert", "Folder Selector -> Loop -> AI Action -> End Loop -> Alert"),
    ("doc_extract_cond",     "Folder Selector -> Document -> AI Extract -> Conditional (approval threshold) -> Human Approval | Alert -> Alert (confirm)"),
    ("loop_file_ai_excel",   "Folder Selector -> Loop -> File (read) -> AI Extract -> Excel Export (append) -> End Loop"),
    ("setvar_cond_branch",   "Set Variable (compute) -> Conditional -> Alert (true) | Alert (false)"),
    ("integration_alert",    "Integration (fetch) -> Set Variable (reshape) -> Alert"),
    ("db_cond_human_alert",  "Database -> Conditional (row count > 0) -> Human Approval | Alert"),
    ("exec_app_alert",       "Execute Application -> Alert"),
    # Added in v2: more breadth across the node-type space
    ("alert_only",                       "Alert"),
    ("setvar_alert",                     "Set Variable -> Alert"),
    ("db_setvar_alert",                  "Database -> Set Variable (reshape) -> Alert"),
    ("integration_db_alert",             "Integration -> Database (insert) -> Alert"),
    ("integration_loop_setvar_alert",    "Integration -> Loop -> Set Variable -> End Loop -> Alert"),
    ("doc_extract_db_excel",             "Folder Selector -> Document -> AI Extract -> Database (insert) -> Excel Export (append)"),
    ("loop_doc_aiextract_db",            "Folder Selector -> Loop -> Document -> AI Extract -> Database (insert) -> End Loop"),
    ("file_check_cond_alert",            "File (check) -> Conditional (exists) -> Alert | Alert"),
    ("ai_action_cond_human_alert",       "AI Action -> Conditional (contains keyword) -> Human Approval | Alert -> Alert (merge)"),
    ("multi_db_setvar_alert",            "Database -> Database -> Set Variable (combine) -> Alert"),
    ("loop_db_ai_alert",                 "Database -> Loop -> AI Action -> Alert -> End Loop"),
    ("exec_app_setvar_db",               "Execute Application -> Set Variable (parse output) -> Database (insert)"),
    ("exec_app_cond_branch",             "Execute Application -> Conditional (exit code) -> Alert (success) | Alert (fail)"),
    ("integration_ai_excel",             "Integration -> AI Action (transform) -> Excel Export (new)"),
    ("schedule_db_ai_excel_alert",       "Database -> AI Action -> Excel Export (template) -> Alert (with attachment-style summary)"),
    ("loop_integration_db",              "Loop (over IDs from Database) -> Integration -> Database (update) -> End Loop"),
    ("doc_aiextract_human_db",           "Document -> AI Extract -> Human Approval -> Database (insert on approval)"),
    ("setvar_setvar_setvar_alert",       "Set Variable -> Set Variable -> Set Variable -> Alert"),
    ("folder_loop_file_excel",           "Folder Selector -> Loop -> File (read) -> Excel Export (append) -> End Loop"),
    ("db_cond_loop_alert",               "Database -> Conditional (any rows) -> Loop -> Alert (per row) -> End Loop | Alert (none)"),
]

DOMAINS = [
    # Original 10
    ("invoice_ap",        "Accounts payable team processing vendor invoices (PDFs in a network folder)."),
    ("inventory",         "Retail operations monitoring low-stock items on SKU level, daily cadence."),
    ("expense_reports",   "Finance processing employee expense reports with receipt PDFs."),
    ("compliance_audit",  "Compliance team auditing vendor contract requirements, strict change tracking."),
    ("hr_onboarding",     "HR team running a new-hire onboarding checklist."),
    ("payments_ops",      "Payments ops team monitoring Stripe charges and reconciling failures."),
    ("support_tickets",   "Support team triaging tickets and escalating by priority."),
    ("sales_pipeline",    "Sales ops generating weekly pipeline and outreach summaries."),
    ("data_quality",      "Data team auditing DQ rules and sending alerts on violations."),
    ("doc_review",        "Legal team extracting structured fields from contract PDFs."),
    # Added in v2
    ("manufacturing_qc",  "Manufacturing QA reviewing inspection reports for defects per shift."),
    ("logistics_routing", "Logistics ops monitoring shipment delays and rerouting cargo."),
    ("clinical_intake",   "Clinic intake team processing new-patient forms and routing to specialists."),
    ("real_estate_lease", "Property managers extracting lease terms and tracking renewals."),
    ("insurance_claims",  "Insurance adjusters triaging incoming claim PDFs and routing by amount."),
    ("marketing_campaigns","Marketing team running A/B email campaigns and reporting performance."),
    ("partner_onboarding","Channel team running partner-onboarding checklists and credentialing."),
    ("security_audit",    "Security team monitoring failed logins and escalating high-risk events."),
    ("research_curation", "Research analysts curating articles and extracting citations."),
    ("agriculture_yield", "Ag-tech monitoring sensor readings per field and triggering irrigation."),
]


# Two-call strategy:
#   1. PLAN_SYSTEM asks a frontier model for a realistic plan given (skeleton, domain).
#   2. COMMANDS via the actual CommandGenerator system prompt + the plan from step 1
#      — same path the production system uses, so outputs are faithful.

PLAN_SYSTEM = """You invent realistic natural-language workflow plans for a no-code automation platform.

Given a STRUCTURAL skeleton (list of node types in execution order) and a DOMAIN (business context), write the PLAN that a real user of that domain would hand to a workflow builder.

RULES:
- Numbered steps, one per node, in execution order.
- For Loops, describe both the Loop step and the End Loop step.
- For Conditionals, describe both the pass and fail branches.
- Include concrete specifics that a real user of the domain would know or care about: file paths, variable names, SQL query text, AI Extract field lists (name/type/required/description), email recipients, agent IDs (as integers given as strings, e.g. "3"), connection IDs (as strings).
- Do not reference node types that aren't in the skeleton. Do not invent triggers or schedulers.
- Use the domain's vocabulary and concerns naturally.
- Output ONLY the plan text. No JSON, no markdown fences, no preamble, no explanation.
"""


_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> Optional[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\s*|```\s*$", "", raw, flags=re.MULTILINE).strip()
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def generate_one(
    skeleton_id: str,
    skeleton: str,
    domain_id: str,
    domain_desc: str,
    system_prompt: str,
    *,
    backend: Optional[str],
    model: Optional[str],
    dry_run: bool,
) -> Optional[Tuple[str, Dict]]:
    if dry_run:
        placeholder_cmds = {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": f"Dry run {skeleton_id}/{domain_id}",
                    "config": {
                        "alertType": "email",
                        "recipients": "user@example.com",
                        "messageTemplate": "dry run",
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        }
        return f"<DRY RUN: {skeleton_id} x {domain_id}>", placeholder_cmds

    # --- Call 1: generate the plan from (skeleton, domain). Short output. ---
    plan_user = f"SKELETON: {skeleton}\nDOMAIN: {domain_desc}\n\nWrite the plan."
    plan = complete(
        PLAN_SYSTEM,
        plan_user,
        backend=backend,
        model=model,
        temperature=0.7,
        max_tokens=700,
    ).strip()
    # Strip any stray markdown fences even though the prompt forbids them.
    plan = re.sub(r"^```[a-z]*\s*|```\s*$", "", plan, flags=re.MULTILINE).strip()
    if not plan or len(plan) < 40:
        return None

    # --- Call 2: use the actual CommandGenerator system prompt + this plan. ---
    # JSON mode guarantees the response is valid JSON; downstream code needs
    # the {"action": "build_workflow", "commands": [...]} shape.
    cmds_user = f"Convert this workflow plan to JSON commands:\n\n{plan}"
    raw = complete(
        system_prompt,
        cmds_user,
        backend=backend,
        model=model,
        temperature=0.2,
        max_tokens=2200,
        response_format={"type": "json_object"},
    )
    commands = _extract_json(raw)
    if not commands or not isinstance(commands, dict):
        return None
    # Tolerate either shape: {"commands":[...]} or {"action":..., "commands":[...]}.
    if "commands" not in commands:
        return None
    if "action" not in commands:
        commands = {"action": "build_workflow", "commands": commands["commands"]}
    return plan, commands


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen")
    ap.add_argument(
        "--out",
        default=None,
        help="Default: training/data/<task>/synthetic/templates.jsonl",
    )
    ap.add_argument(
        "--rejected-out",
        default=None,
        help="Default: training/data/<task>/synthetic/templates.rejected.jsonl",
    )
    ap.add_argument("--limit", type=int, default=0, help="Generate only N combinations")
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-judge", action="store_true", help="Run LLM judge (gate 4)")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    if args.out is None:
        args.out = os.path.join(_REPO_ROOT, "training", "data", args.task, "synthetic", "templates.jsonl")
    if args.rejected_out is None:
        args.rejected_out = os.path.join(
            _REPO_ROOT, "training", "data", args.task, "synthetic", "templates.rejected.jsonl"
        )

    system_prompt = load_system_prompt_text()
    if not system_prompt:
        logger.error("Could not load COMMAND_GENERATOR_SYSTEM_PROMPT")
        return 2

    combos = list(itertools.product(SKELETONS, DOMAINS))
    if args.skip:
        combos = combos[args.skip :]
    if args.limit:
        combos = combos[: args.limit]
    logger.info("Total combinations to attempt: %d", len(combos))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Skip combos that are already in the output file (resume).
    already_done = set()
    if os.path.exists(args.out):
        with open(args.out, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    m = r.get("_meta", {})
                    already_done.add((m.get("skeleton_id"), m.get("domain_id")))
                except json.JSONDecodeError:
                    continue
    if already_done:
        logger.info("Resuming: %d combo(s) already in output", len(already_done))
    accepted = 0
    rejected = 0
    gen_failures = 0
    # Append mode — preserves already-written combos on resume.
    with open(args.out, "a", encoding="utf-8") as out, open(args.rejected_out, "a", encoding="utf-8") as rej:
        for i, ((skel_id, skel), (dom_id, dom_desc)) in enumerate(combos, start=1):
            if (skel_id, dom_id) in already_done:
                continue
            try:
                result = generate_one(
                    skel_id,
                    skel,
                    dom_id,
                    dom_desc,
                    system_prompt,
                    backend=args.backend,
                    model=args.model,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%d/%d] generate failed %s x %s: %s", i, len(combos), skel_id, dom_id, exc)
                gen_failures += 1
                continue
            if result is None:
                gen_failures += 1
                continue
            plan, commands = result

            assistant_block = "```json\n" + json.dumps(commands, indent=2) + "\n```"
            ok, details = pass_all_gates(
                plan,
                assistant_block,
                run_compile=True,
                run_judge=args.run_judge,
                judge_dry_run=args.dry_run,
            )
            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": plan},
                    {"role": "assistant", "content": assistant_block},
                ],
                "_meta": {
                    "source": "synth_template",
                    "skeleton_id": skel_id,
                    "domain_id": dom_id,
                    "n_commands": len(commands.get("commands", [])),
                    "dry_run": bool(args.dry_run),
                    "gates": details.get("gates"),
                },
            }
            if ok:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                accepted += 1
                logger.info("[%d/%d] %s x %s  OK  cmds=%d", i, len(combos), skel_id, dom_id, record["_meta"]["n_commands"])
            else:
                record["_meta"]["reject_reason"] = details
                rej.write(json.dumps(record, ensure_ascii=False) + "\n")
                rej.flush()
                rejected += 1
                reason = ",".join(k for k, v in (details.get("gates") or {}).items() if v is False)
                logger.info("[%d/%d] %s x %s  REJECT  (%s)", i, len(combos), skel_id, dom_id, reason or "unknown")

    logger.info(
        "Done. generated=%d accepted=%d rejected=%d gen_failures=%d out=%s",
        len(combos),
        accepted,
        rejected,
        gen_failures,
        args.out,
    )
    logger.info("LLM usage: %s", json.dumps(usage_summary()))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
