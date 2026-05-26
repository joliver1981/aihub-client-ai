"""
DataCollectionAgent — phased, schema-driven conversational data collection.

This agent guides a user through filling out a form schema by chatting one
section at a time. Its design mirrors `WorkflowAgent.py`:

  - LangChain AgentExecutor with bound tools
  - Dynamic system prompt rebuilt on every phase change (not on every turn)
  - Tool closures that read/write the persisted CollectionSession (so state
    is deterministic and survives page refreshes)
  - chat_history rehydrated from the saved session, so the agent can resume

The tools the agent exposes mutate the session state directly. The agent's
own LLM memory is therefore never the source of truth — the schema and the
saved session are.

Runtime flow:
  - Frontend POSTs a user message
  - Route loads the saved session, instantiates this agent, calls process_message
  - Tool calls update the session (saved each time)
  - Route returns the response + metadata for the UI to refresh the progress panel
"""

import json
import logging
import os
from logging.handlers import WatchedFileHandler
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_classic.agents.format_scratchpad import format_to_tool_messages
from langchain_classic.agents.output_parsers import ToolsAgentOutputParser
from langchain_classic.agents import AgentExecutor
from langchain_core.callbacks import BaseCallbackHandler

from CommonUtils import rotate_logs_on_startup, get_log_path

from .schema_loader import (
    get_section,
    get_field,
    get_lookup_values,
    get_section_order,
    get_next_section_id,
)
from .state_manager import (
    CollectionSession,
    save_session,
    set_current_section,
    set_section_status,
    set_status,
    SECTION_NOT_STARTED,
    SECTION_IN_PROGRESS,
    SECTION_COMPLETE,
    STATUS_IN_PROGRESS,
    STATUS_REVIEW,
)
from .validation_engine import (
    coerce_value,
    resolve_select_value,
    resolve_multi_select_value,
    validate_field,
    validate_section,
    validate_all,
    is_section_complete,
    get_missing_required_fields,
    is_field_visible,
    visible_fields_by_section,
)
from .voice_normalizer import normalize_voice_value
from .debug_mode import debug_log, is_enabled as debug_enabled, truncate_for_display


rotate_logs_on_startup(os.getenv('DCA_AGENT_LOG', get_log_path('data_collection_agent_log.txt')))

logger = logging.getLogger("DataCollectionAgent")
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'DEBUG'), logging.DEBUG)
logger.setLevel(log_level)
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_handler = WatchedFileHandler(
    filename=os.getenv('DCA_AGENT_LOG', get_log_path('data_collection_agent_log.txt')),
    encoding='utf-8',
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)


class CollectionPhase(Enum):
    GREETING = "greeting"
    COLLECTING = "collecting"
    SECTION_CONFIRM = "section_confirm"
    REVIEW = "review"
    SUBMITTED = "submitted"


class DataCollectionAgent:
    """
    Schema-driven conversational data collection agent.

    Construction is cheap: pass in a loaded schema and a CollectionSession.
    The agent's LangChain executor and bound tools are built fresh per request,
    which keeps the design stateless across processes (multi-worker safe).
    """

    def __init__(self, session: CollectionSession, schema: Dict,
                 just_extracted: Optional[List[Dict]] = None):
        self.session = session
        self.schema = schema
        self.phase = self._infer_phase()

        # Field values that were JUST captured by the pre-extractor this
        # turn, in response to the user's most recent message. Used by
        # the system prompt builder to highlight the delta to the agent
        # so it doesn't re-ask for values that were just saved. This is
        # the explicit signal that "DATA COLLECTED SO FAR has new
        # entries because of the user's last message" — without it the
        # agent has to deduce that by comparing state against chat
        # history, and reasoning models do that unreliably.
        self.just_extracted: List[Dict] = list(just_extracted or [])

        # rich_blocks accumulated by tools during a turn; collected by the route
        self._pending_rich_blocks: List[Dict] = []
        # Side-channel actions the route should react to (e.g. UI navigation)
        self._pending_actions: List[Dict] = []

        self._initialize_llm()
        self._set_system_prompt()
        self._register_tools()
        self._build_agent_executor()

        # LangChain chat history rehydrated from session
        self.chat_history = self._rehydrate_chat_history()

        logger.info(
            f"Initialized DataCollectionAgent for session {session.session_id} "
            f"(config={session.config_id}, phase={self.phase.value})"
        )

    # ------------------------------------------------------------------
    # LLM setup — same pattern as WorkflowAgent._initialize_llm()
    # ------------------------------------------------------------------
    def _initialize_llm(self):
        from api_keys_config import get_openai_config
        config = get_openai_config(use_alternate_api=False)
        reasoning_effort = config.get('reasoning_effort')
        temperature = 1.0 if reasoning_effort else 0.7

        if config['api_type'] == 'open_ai':
            self.llm = ChatOpenAI(
                model=config['model'],
                api_key=config['api_key'],
                temperature=temperature,
                max_tokens=8192,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )
        else:
            self.llm = AzureChatOpenAI(
                azure_deployment=config['deployment_id'],
                model=config['deployment_id'],
                api_version=config['api_version'],
                azure_endpoint=config['api_base'],
                api_key=config['api_key'],
                temperature=temperature,
                max_tokens=8192,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------
    def _infer_phase(self) -> CollectionPhase:
        """Determine starting phase based on session state."""
        if self.session.status == 'submitted':
            return CollectionPhase.SUBMITTED
        if self.session.status == 'review':
            return CollectionPhase.REVIEW
        if not self.session.chat_history:
            return CollectionPhase.GREETING
        # If all sections are complete, we should be in REVIEW
        all_sections = get_section_order(self.schema)
        if all_sections and all(
            self.session.section_status.get(sid) == SECTION_COMPLETE
            for sid in all_sections
        ):
            return CollectionPhase.REVIEW
        return CollectionPhase.COLLECTING

    def update_phase(self, new_phase: CollectionPhase):
        if new_phase == self.phase:
            return
        logger.info(f"Phase transition: {self.phase.value} -> {new_phase.value}")
        self.phase = new_phase
        # Rebuild prompt and executor on phase change
        self._set_system_prompt()
        self._build_agent_executor()

    def refresh_for_voice_mode_change(self):
        """
        Rebuild the system prompt + executor after voice_mode flipped on the
        session. Call after `set_voice_mode(...)` so the next agent turn
        receives the right instructions (concise, no markdown, etc.).
        """
        self._set_system_prompt()
        self._build_agent_executor()

    # ------------------------------------------------------------------
    # System prompt — schema-aware, rebuilt on phase change
    # ------------------------------------------------------------------
    def _set_system_prompt(self):
        guidelines = self.schema.get('agent_guidelines', '').strip()
        schema_name = self.schema.get('name', 'this form')

        # Solution authors can attach broad context the AI should use to
        # support the user better — domain background, definitions of
        # terms, FAQ-style notes, etc. Distinct from `agent_guidelines`
        # (which is "how the AI should behave"); helpful_context is
        # "what the AI should know about this form's domain".
        helpful_context = (self.schema.get('helpful_context') or '').strip()
        form_tips = self.schema.get('tips') or []

        sections_overview = self._format_sections_overview()
        current_section_block = self._format_current_section()
        collected_so_far = self._format_collected_data()
        just_extracted_block = self._format_just_extracted()
        lookup_block = self._format_lookups_for_current_section()
        helpful_context_block = ""
        if helpful_context or form_tips:
            lines = ["FORM HELPFUL CONTEXT (use to support the user — share excerpts when relevant):"]
            if helpful_context:
                lines.append(helpful_context)
            if form_tips:
                lines.append("")
                lines.append("FORM TIPS:")
                for t in form_tips:
                    if isinstance(t, dict):
                        lines.append(f"  - {t.get('text', '')}"
                                     + (f"  [trigger: {t.get('trigger')}]" if t.get('trigger') else ''))
                    else:
                        lines.append(f"  - {t}")
            helpful_context_block = '\n'.join(lines)

        # PHASE is now just a one-line label. All actual behavior rules
        # live in the unified RESPONSE GUIDE section below — having them
        # here too created contradictions (e.g. phase_guidance said
        # "always call update_field" while the newer guide says "the
        # extractor handled it, don't call").
        phase_label = {
            CollectionPhase.GREETING:        "GREETING (this is the first turn — open with a brief greeting and ask the first question)",
            CollectionPhase.COLLECTING:      "COLLECTING (gather the next missing required field — see NEXT TO ASK above)",
            CollectionPhase.SECTION_CONFIRM: "SECTION_CONFIRM (current section is done — confirm briefly and call advance_section)",
            CollectionPhase.REVIEW:          "REVIEW (all sections are done — call show_recap, then mark_ready_for_submission once the user confirms)",
            CollectionPhase.SUBMITTED:       "SUBMITTED (form is finalized — be friendly but make clear the submission is final)",
        }
        phase_line = f"PHASE: {phase_label.get(self.phase, self.phase.value)}"

        # Voice-mode addendum — kept SHORT now that the pre-extractor
        # handles raw STT transcripts. The old "ALWAYS call update_field
        # first with raw words" rule was correct when there was no
        # extractor; with the extractor it actively conflicts (the
        # extractor already cleaned the value before the agent runs).
        voice_addendum = ""
        if getattr(self.session, 'voice_mode', False):
            voice_addendum = """

VOICE MODE ACTIVE — your replies will be spoken aloud:
- Be conversational and concise. 1–3 sentences per response.
- No markdown formatting. No asterisks, headings, bullet lists, or code
  fences. Speak in plain language.
- Don't enumerate long option lists or read tables verbatim. Summarize,
  and offer to go deeper if the user asks.
- Forgive disfluencies, fillers, and self-corrections — interpret what
  the user clearly meant.
- If the user says any equivalent of "stop" / "nevermind" / "pause" /
  "wait" / "hold on", call pause_listening with a brief reason.
"""

        # Compose the prompt as ORDERED, NON-OVERLAPPING sections:
        #   1. Identity / form name
        #   2. Schema author's guidance (agent_guidelines + helpful_context + tips)
        #   3. WHERE WE ARE (current section + completion + NEXT TO ASK)
        #   4. WHAT'S COLLECTED (DATA COLLECTED SO FAR)
        #   5. WHAT JUST CHANGED (JUST CAPTURED THIS TURN — when present)
        #   6. SECTIONS OVERVIEW (all sections at a glance)
        #   7. LOOKUP IDS (option ids reachable in this section)
        #   8. PHASE label (one line)
        #   9. RESPONSE GUIDE (the ONLY place behavior rules live)
        #  10. Voice addendum (when active)
        # Each section appears once; nothing duplicates or contradicts.
        author_block = ""
        if guidelines or helpful_context_block:
            chunks = []
            if guidelines:
                chunks.append("SCHEMA AUTHOR'S GUIDANCE:\n" + guidelines)
            if helpful_context_block:
                chunks.append(helpful_context_block)
            author_block = "\n\n".join(chunks)

        self.SYSTEM = f"""You guide a user through filling out: {schema_name}.

{author_block}

WHERE WE ARE
{current_section_block}

DATA COLLECTED SO FAR:
{collected_so_far}

{just_extracted_block}

SECTIONS OVERVIEW:
{sections_overview}

{lookup_block}

{phase_line}


RESPONSE GUIDE

How the data flow works:
A background extractor runs BEFORE every one of your turns. It reads
the user's latest message and saves any field values it can extract,
including short / ambiguous / natural-language answers. By the time
you see this prompt, those values are already in DATA COLLECTED SO
FAR. When something was just captured, JUST CAPTURED THIS TURN lists
it explicitly — trust that block, do not re-ask.

What the user sees:
The user sees only the chat bubbles you write and the rich content
you produce by calling render tools. Anything in this prompt is
invisible to them. So don't refer to options, tables, or values as
"shown" unless you actually rendered them with a tool this turn (or
in a recent prior turn).

Per-turn decision tree (follow in order):

1. If a JUST CAPTURED THIS TURN block is present:
     → briefly confirm those values, then ask about NEXT TO ASK
       (or move on if NEXT TO ASK says nothing's left).
     → typically zero tool calls.

2. Else if NEXT TO ASK is set:
     → If it's a select/lookup field AND that lookup hasn't been
       rendered this conversation, call show_lookup_data (or
       compare_options for richer cards) FIRST, then ask.
     → Otherwise just ask.
     → If the user's last message clearly contains the answer for
       NEXT TO ASK and the value would coerce cleanly to the field's
       type (a literal date for a date field, a literal number for a
       number field, etc.), you may call update_field — but only as
       a fallback for cases where the extractor missed it. Don't
       pass raw natural language ("1 week from now", "tomorrow") to
       update_field; let the extractor handle it on the next user
       message if needed.

3. Else if the section is complete (no NEXT TO ASK):
     → Call advance_section. The tool tells you what's next.

4. The user asks a meta-question:
     → "What is X?" / "explain option Y" → show_option_details or
       compare_options.
     → "What should I put for X?" / "why do you need this?" →
       show_field_help.
     → "What have I told you so far?" → show_recap.

5. The user wants to backtrack:
     → "go back to section X" → navigate_to_section.
     → "remove what I said for X" / "leave it blank" → clear_field.
     → "start over" → confirm with the user, then
       restart_session(confirm=true).

6. The user signals "nothing more" / "done":
     → If current section still has missing required fields → ask
       about NEXT TO ASK.
     → If current section is complete → call advance_section.

7. The user asks for research / external info (reviews, ratings,
   directions, hours, photos, web lookup, etc.) — typically handled
   by schema-supplied custom tools:
     → Call the appropriate custom tool (e.g. yelp_*, google_*,
       web_*) with the user's request. Show the result.
     → If the result asks for disambiguation (multiple matches
       across cities, branches, etc.) and the user then narrows
       it ("the NJ one", "downtown", "the Boylston one"), CALL
       THE TOOL AGAIN with the narrowed query — don't just
       confirm a field value as if the question was answered.
       Saving the field is a SEPARATE step; finish the research
       request first, THEN ask whether to save / use that option.
     → Don't conflate "looking up a place for the user" with
       "the user picked an approved venue from our list." When
       the field is restricted to an approved list, remind the
       user the saved value must come from that list and ask
       which approved venue they want to use, after the research
       is complete.

Tool result discipline:
After every tool result, READ IT. If a tool returns "NO-OP" or
"VALIDATION ERROR", honor that — don't pretend it succeeded, don't
re-try with the same input, don't ask the user to fix what the
system already handled. Don't pad with unneeded calls; don't skip
calls you should make.

Style:
Be conversational and concise. One question per turn. Confirm saved
values briefly when they were just captured.{voice_addendum}"""

    def _format_sections_overview(self) -> str:
        lines = []
        for sid in get_section_order(self.schema):
            section = get_section(self.schema, sid)
            if not section:
                continue
            status = self.session.section_status.get(sid, SECTION_NOT_STARTED)
            marker = {
                SECTION_NOT_STARTED: '○',
                SECTION_IN_PROGRESS: '◐',
                SECTION_COMPLETE: '●',
            }.get(status, '○')
            lines.append(
                f"  {marker} [{sid}] {section.get('title', sid)}"
                f" — {section.get('description', '')}"
            )
        return '\n'.join(lines) if lines else '  (no sections defined)'

    def _format_current_section(self) -> str:
        """Render the current section with field listing AND explicit
        completion status AND a 'next to ask' hint. The agent should
        not have to deduce which field comes next — we tell it.

        Conditionally-hidden fields are skipped; they don't apply yet."""
        sid = self.session.current_section_id
        if not sid:
            return "CURRENT SECTION: (none — start with the first section)"
        section = get_section(self.schema, sid)
        if not section:
            return f"CURRENT SECTION: {sid} (not found in schema)"

        section_data = self.session.collected_data.get(sid) or {}

        # Walk visible fields, classifying each. We also identify the
        # FIRST visible required-and-missing field — that's "NEXT TO ASK".
        rows = []
        next_to_ask = None
        n_required = 0
        n_required_done = 0
        for fld in section.get('fields', []):
            fid = fld.get('id', '')
            if not is_field_visible(self.schema, sid, fid, self.session.collected_data):
                continue
            req = bool(fld.get('required'))
            has_value = fid in section_data and section_data[fid] not in (None, '', [])
            if req:
                n_required += 1
                if has_value:
                    n_required_done += 1
            type_info = fld.get('type', 'text')
            extras = []
            if fld.get('options_ref'):
                extras.append(f"options_ref={fld['options_ref']}")
            elif fld.get('lookup_ref'):
                extras.append(f"lookup_ref={fld['lookup_ref']}")
            if fld.get('validation'):
                extras.append(f"validation={json.dumps(fld['validation'])}")
            extras_str = (' ' + ' '.join(extras)) if extras else ''
            hint = f"  [{fld['prompt_hint']}]" if fld.get('prompt_hint') else ''
            status_marker = '✓' if has_value else ('*' if req else 'o')
            rows.append(
                f"    {status_marker} {fid} ({type_info}){extras_str}{hint}"
            )
            if next_to_ask is None and req and not has_value:
                next_to_ask = (fid, fld)

        # Section completion summary
        if n_required == 0:
            completion = "no required fields"
        elif n_required_done == n_required:
            completion = f"all {n_required} required complete ✓"
        else:
            completion = f"{n_required_done} of {n_required} required complete"

        lines = [
            f"CURRENT SECTION: [{sid}] {section.get('title')}",
        ]
        if section.get('description'):
            lines.append(f"  Purpose: {section['description']}")
        lines.append(f"  Status: {completion}")
        if next_to_ask:
            fid, fld = next_to_ask
            label = fld.get('label', fid)
            hint = fld.get('prompt_hint') or ''
            lines.append(
                f"  NEXT TO ASK: {fid} ({fld.get('type', 'text')}) — '{label}'"
                + (f"  Suggested phrasing: \"{hint}\"" if hint else '')
            )
        elif n_required_done == n_required and n_required > 0:
            lines.append(
                "  NEXT TO ASK: nothing — section is complete; "
                "call advance_section to move on."
            )
        lines.append("  Fields  (✓ = answered, * = required-missing, o = optional-missing):")
        lines.extend(rows)
        return '\n'.join(lines)

    def _format_just_extracted(self) -> str:
        """A short, explicit list of values the pre-extractor just saved
        from the user's most recent message. This is the delta — what
        changed THIS turn — so the agent can confirm the saved values
        without having to deduce them from comparing DATA COLLECTED SO
        FAR against chat history.

        Also surfaces FAILED extractions (the extractor identified a
        candidate but coercion / validation rejected it) so the agent
        can ask a precise follow-up instead of going silent and
        re-asking an already-saved field."""
        records = self.just_extracted or []
        applied = [r for r in records if r.get('applied')]
        failed = [r for r in records if not r.get('applied') and r.get('error')]
        if not applied and not failed:
            return ""
        lines = []
        if applied:
            lines.append(
                "JUST CAPTURED THIS TURN (the pre-extractor saved these from the user's "
                "most recent message — they are already in DATA COLLECTED SO FAR; "
                "do NOT ask the user to provide them again, just briefly confirm and "
                "ask the next missing question):"
            )
            for r in applied:
                sec_id = r.get('section_id')
                fid = r.get('field_id')
                val = r.get('final_value')
                field = get_field(self.schema, sec_id, fid)
                label = (field.get('label') if field else None) or fid
                lines.append(f"  - {label} ({sec_id}.{fid}) = {val!r}")
        if failed:
            if lines:
                lines.append("")
            lines.append(
                "TRIED-BUT-FAILED THIS TURN (the pre-extractor identified these "
                "candidates from the user's message but they did NOT pass validation; "
                "ALREADY-SAVED fields are still saved — do not re-ask them. Re-ask "
                "ONLY the failed field, briefly explaining why, and offer the user "
                "the available choices if it's a select):"
            )
            for r in failed:
                sec_id = r.get('section_id')
                fid = r.get('field_id')
                raw = r.get('raw_value')
                err = r.get('error')
                field = get_field(self.schema, sec_id, fid) if sec_id and fid else None
                label = (field.get('label') if field else None) or fid or '?'
                lines.append(
                    f"  - {label} ({sec_id}.{fid}): tried {raw!r} → {err}"
                )
        return '\n'.join(lines)

    def _format_collected_data(self) -> str:
        if not self.session.collected_data:
            return "  (nothing collected yet)"
        lines = []
        for sid, fields in self.session.collected_data.items():
            section = get_section(self.schema, sid)
            title = section.get('title', sid) if section else sid
            lines.append(f"  [{sid}] {title}:")
            for fid, val in (fields or {}).items():
                lines.append(f"    {fid} = {val!r}")
        return '\n'.join(lines)

    def _format_lookups_for_current_section(self) -> str:
        """Compact (id, label) pairs for the current section's lookup-backed
        fields. Drops descriptions / examples / icons — those are
        retrievable on-demand via show_option_details / compare_options.
        Keeping this small is critical for tool-calling reliability;
        prior versions dumped full lookup JSON and bloated the prompt."""
        sid = self.session.current_section_id
        if not sid:
            return ""
        section = get_section(self.schema, sid)
        if not section:
            return ""
        refs = set()
        for fld in section.get('fields', []):
            if fld.get('options_ref'):
                refs.add(fld['options_ref'])
            if fld.get('lookup_ref'):
                refs.add(fld['lookup_ref'])
        if not refs:
            return ""
        lines = ["LOOKUP IDS (call show_option_details / compare_options for descriptions):"]
        for ref in refs:
            values = get_lookup_values(self.schema, ref, self.session.collected_data) or []
            pairs = []
            for v in values[:25]:
                if isinstance(v, dict):
                    from .validation_engine import get_option_id as _get_id
                    pairs.append(f"{_get_id(v)}={v.get('label')!r}")
                else:
                    pairs.append(repr(v))
            more = f" (+{len(values) - 25} more)" if len(values) > 25 else ""
            lines.append(f"  {ref}: {', '.join(pairs)}{more}")
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # Tool registration — closures over self / self.session
    # ------------------------------------------------------------------
    def _register_tools(self):
        agent = self  # captured by the closures so tools can read/write state

        @tool
        def update_field(section_id: str, field_id: str, value: str) -> str:
            """Save a field value. Pass the user's raw words; cleanup +
            validation happen inside. Use only when the pre-extractor
            missed something (it usually doesn't)."""
            voice_mode = getattr(agent.session, 'voice_mode', False)
            logger.info(
                "update_field called: section=%s field=%s value=%r voice_mode=%s",
                section_id, field_id, value, voice_mode,
            )
            debug_log(agent.session, 'tool_call', {
                'tool': 'update_field',
                'args': {'section_id': section_id, 'field_id': field_id, 'value': value},
                'voice_mode': voice_mode,
            })

            def _return(result: str) -> str:
                # Single exit so debug logging always sees the outcome
                debug_log(agent.session, 'tool_result', {
                    'tool': 'update_field',
                    'result': result,
                })
                return result

            field = get_field(agent.schema, section_id, field_id)
            if not field:
                return _return(f"ERROR: No field {field_id} in section {section_id}.")

            field_type = field.get('type', 'text')

            # Capture any existing value BEFORE we touch anything. The
            # background extractor often saves the cleaned value before
            # the agent's turn runs (e.g. "1 week from now" -> ISO
            # date). If the agent then redundantly calls update_field
            # with the user's raw words, coerce_value will fail on the
            # raw string and we'd otherwise return VALIDATION ERROR —
            # which the agent reports as "couldn't save", asking the
            # user to repeat. That's the bug. Detect it and tell the
            # agent the truth: a valid value is already in place.
            existing_value = (agent.session.collected_data.get(section_id) or {}).get(field_id)

            # Voice-mode normalization: hand the raw transcript + field
            # schema to a fast mini LLM that returns what the user clearly
            # intended. Generalizes naturally across field types — no
            # per-type regex rules to maintain. Failures fall through
            # unchanged; downstream validation still gets a chance.
            if voice_mode and isinstance(value, str):
                # Pass select/lookup options through so the model can pick
                # an option id directly when the transcript matches.
                options = None
                if field_type in ('select', 'lookup', 'multi_select'):
                    if field.get('options'):
                        options = field['options']
                    elif field.get('options_ref'):
                        options = get_lookup_values(agent.schema, field['options_ref'], agent.session.collected_data)
                    elif field.get('lookup_ref'):
                        options = get_lookup_values(agent.schema, field['lookup_ref'], agent.session.collected_data)

                cleaned = normalize_voice_value(value, field, options=options, session=agent.session)
                if cleaned != value:
                    logger.info(
                        "  voice cleanup: %r -> %r (field=%s, type=%s)",
                        value, cleaned, field_id, field_type,
                    )
                else:
                    logger.info("  voice cleanup left value unchanged")
                value = cleaned

            # For select / lookup / multi_select fields the user (and the LLM)
            # often supplies a label or shorthand ("B", "Type B", "priority")
            # rather than the option's id. Resolve it before coercion so
            # natural-language answers map to the right id.
            if field_type in ('select', 'lookup'):
                resolved_id, match_err = resolve_select_value(value, agent.schema, field, agent.session.collected_data)
                if match_err:
                    return _return(f"VALIDATION ERROR: {match_err}")
                if resolved_id is not None:
                    value = resolved_id
            elif field_type == 'multi_select':
                resolved_list, match_err = resolve_multi_select_value(value, agent.schema, field, agent.session.collected_data)
                if match_err:
                    return _return(f"VALIDATION ERROR: {match_err}")
                if resolved_list is not None:
                    value = resolved_list

            coerced, coerce_err = coerce_value(value, field_type)
            if coerce_err:
                # Don't destroy a perfectly valid existing value just
                # because the agent passed in raw text the coercer can't
                # parse. This is the extractor-already-saved case: tell
                # the agent the truth so it doesn't ask the user again.
                if existing_value not in (None, '', []):
                    msg = (
                        f"NO-OP. The {field_id} field already has the "
                        f"correct value {existing_value!r} (saved by the "
                        f"background extractor on this same turn). Your "
                        f"raw input {value!r} could not be coerced "
                        f"({coerce_err}), but you DON'T need to save it "
                        f"again — the existing value reflects what the "
                        f"user meant. Just confirm {existing_value!r} "
                        f"with the user and move to the next question."
                    )
                    return _return(msg)
                return _return(f"VALIDATION ERROR: {coerce_err}")

            # Tentatively place the value, then validate against full data
            tentative = {**agent.session.collected_data}
            tentative.setdefault(section_id, {})
            tentative[section_id] = {**tentative[section_id], field_id: coerced}

            errors = validate_field(agent.schema, section_id, field_id, coerced, tentative)
            if errors:
                # Same protection on the validate step — don't let a
                # bad re-attempt erase a good prior value.
                if existing_value not in (None, '', []) and existing_value != coerced:
                    msg = (
                        f"NO-OP. {field_id} already has {existing_value!r} "
                        f"(valid). The new value {coerced!r} you tried to "
                        f"save failed validation: {' '.join(errors)}. The "
                        f"existing value is fine; do NOT ask the user to "
                        f"re-enter it."
                    )
                    return _return(msg)
                return _return("VALIDATION ERROR: " + " ".join(errors))

            # Idempotent: if the new coerced value matches what's already
            # there, don't pretend we did work — tell the agent it was
            # already saved so it doesn't double-confirm.
            if existing_value == coerced:
                return _return(
                    f"NO-OP. {field_id} was already set to {coerced!r}. "
                    f"Nothing to do — confirm with the user and move on."
                )

            # Commit
            agent.session.set_field_value(section_id, field_id, coerced)
            save_session(agent.session)
            return _return(f"Saved {field_id} = {coerced!r} in section {section_id}.")

        @tool
        def show_lookup_data(lookup_ref: str, filter_text: str = "") -> str:
            """Render a simple table of lookup options (id + label only).
            For richer cards use compare_options instead."""
            # Dedup: if this lookup was rendered in the last few turns,
            # don't render again. The user can scroll up. This stops the
            # "agent re-asks and re-renders the same table" pattern.
            recently_rendered = (agent.session.rendered_lookups or [])[-5:]
            if not filter_text and lookup_ref in recently_rendered:
                return (
                    f"NO-OP. The {lookup_ref} options were already rendered "
                    f"earlier in this conversation and are still visible to "
                    f"the user (they can scroll up). Don't re-ask the user "
                    f"to look at them — instead, just confirm what they meant "
                    f"if their last message was unclear, or accept their answer "
                    f"and move on. The user almost certainly remembers."
                )
            values = get_lookup_values(agent.schema, lookup_ref, agent.session.collected_data)
            if filter_text:
                ft = filter_text.lower()
                values = [
                    v for v in values
                    if any(ft in str(v.get(k, '')).lower() for k in v.keys())
                    if isinstance(v, dict)
                ]
            if not values:
                return f"No items found for lookup '{lookup_ref}'."

            # Build table block — rendered by richContentRenderer.js
            if isinstance(values[0], dict):
                columns = list(values[0].keys())
                rows = [[v.get(c, '') for c in columns] for v in values[:50]]
            else:
                columns = ['value']
                rows = [[v] for v in values[:50]]

            # Platform's renderTable expects {headers, rows} on content
            # and `title` on metadata. Older code mistakenly used
            # `columns` and put `title` on content, which caused the
            # platform to render "Unable to render table" and the user
            # saw nothing.
            agent._pending_rich_blocks.append({
                'type': 'table',
                'content': {
                    'headers': columns,
                    'rows': rows,
                },
                'metadata': {
                    'title': f"Available options ({len(values)} total)",
                },
            })
            agent.session.rendered_lookups.append(lookup_ref)
            return f"Displayed {len(values)} options for {lookup_ref} to the user."

        @tool
        def get_collection_status() -> str:
            """Return a summary of what's been collected and what's still needed.

            Use this when you're unsure of the current state, or before moving on.
            """
            order = get_section_order(agent.schema)
            lines = []
            for sid in order:
                section = get_section(agent.schema, sid)
                title = section.get('title') if section else sid
                status = agent.session.section_status.get(sid, SECTION_NOT_STARTED)
                missing = get_missing_required_fields(agent.schema, sid, agent.session.collected_data)
                missing_str = ''
                if missing:
                    missing_str = f"  Missing required: {[f.get('id') for f in missing]}"
                lines.append(f"[{sid}] {title} — {status}{missing_str}")
            return '\n'.join(lines)

        @tool
        def navigate_to_section(section_id: str) -> str:
            """Jump to a different section. Only for cross-section moves;
            field corrections within the current section don't need this."""
            section = get_section(agent.schema, section_id)
            if not section:
                return f"ERROR: No section with id '{section_id}'."
            updated = set_current_section(agent.session.session_id, section_id)
            if updated:
                agent.session = updated
            agent._pending_actions.append({
                'type': 'navigate_to_section',
                'section_id': section_id,
            })
            agent.update_phase(CollectionPhase.COLLECTING)
            return (
                f"Now in section [{section_id}] {section.get('title')}. "
                f"Existing values: {agent.session.collected_data.get(section_id, {})}."
            )

        @tool
        def advance_section() -> str:
            """Mark the current section as complete and move to the next.

            If all sections are complete, transitions to REVIEW phase instead.
            """
            current_sid = agent.session.current_section_id
            if not current_sid:
                # No current section — pick the first
                order = get_section_order(agent.schema)
                if not order:
                    return "ERROR: No sections defined in the schema."
                first = order[0]
                updated = set_current_section(agent.session.session_id, first)
                if updated:
                    agent.session = updated
                agent.update_phase(CollectionPhase.COLLECTING)
                return f"Started with section [{first}]."

            # Check that the current section is actually complete
            if not is_section_complete(agent.schema, current_sid, agent.session.collected_data):
                missing = get_missing_required_fields(
                    agent.schema, current_sid, agent.session.collected_data
                )
                if missing:
                    missing_labels = [f"{f.get('label')} ({f.get('id')})" for f in missing]
                    return (
                        f"Cannot advance — section [{current_sid}] still has missing required fields: "
                        f"{', '.join(missing_labels)}."
                    )
                # Maybe a validation error
                section_errs = validate_section(
                    agent.schema, current_sid, agent.session.collected_data
                )
                if section_errs:
                    return f"Cannot advance — section [{current_sid}] has validation errors: {section_errs}"

            # Mark complete
            updated = set_section_status(
                agent.session.session_id, current_sid, SECTION_COMPLETE
            )
            if updated:
                agent.session = updated

            next_sid = get_next_section_id(agent.schema, current_sid)
            if next_sid:
                updated = set_current_section(agent.session.session_id, next_sid)
                if updated:
                    agent.session = updated
                next_section = get_section(agent.schema, next_sid)
                agent._pending_actions.append({
                    'type': 'navigate_to_section',
                    'section_id': next_sid,
                })
                agent.update_phase(CollectionPhase.COLLECTING)
                # Plain confirmation only — no prescriptive guidance about
                # what to ask. The agent has the full schema in ALL
                # SECTIONS plus chat history; it can pick the next
                # question naturally for the new section.
                next_section_data = (agent.session.collected_data.get(next_sid) or {})
                next_section_fields = (
                    [f.get('id') for f in (next_section.get('fields') or [])
                     if f.get('id')]
                    if next_section else []
                )
                return (
                    f"Section [{current_sid}] complete. "
                    f"Now in section [{next_sid}] "
                    f"{next_section.get('title') if next_section else ''}. "
                    f"Fields in this section: {next_section_fields}. "
                    f"Existing values: {next_section_data}."
                )

            # No next section — go to review.
            # Auto-emit the recap panel right here so the user sees it
            # even if the agent forgets to call show_recap. Trust-but-
            # verify: the LLM has been observed to claim "I've put the
            # recap on screen" without actually calling the tool.
            updated = set_status(agent.session.session_id, STATUS_REVIEW)
            if updated:
                agent.session = updated
            agent.update_phase(CollectionPhase.REVIEW)
            # Idempotent: only add the recap if we don't already have one
            # in this turn's pending blocks.
            already_has_recap = any(
                (b.get('type') == 'recap_panel') for b in agent._pending_rich_blocks
            )
            if not already_has_recap:
                agent._pending_rich_blocks.extend(
                    _build_recap_blocks(agent.schema, agent.session)
                )
            return (
                "All sections complete. The recap panel was rendered on "
                "screen automatically. Briefly tell the user it's there, "
                "ask them to confirm, and call mark_ready_for_submission "
                "when they approve. Do NOT call show_recap — already shown."
            )

        @tool
        def show_field_help(section_id: str, field_id: str) -> str:
            """Render a help card for a specific field. Use when the user
            asks "what should I put?" / "what's this field for?"."""
            field = get_field(agent.schema, section_id, field_id)
            if not field:
                return f"ERROR: No field {field_id} in section {section_id}."
            # Body falls back through helpful_context -> description -> prompt_hint
            body = (field.get('helpful_context')
                    or field.get('description')
                    or field.get('prompt_hint')
                    or '')
            content = {
                'field_label': field.get('label') or field_id,
                'field_type':  field.get('type', 'text'),
                'required':    bool(field.get('required')),
                'body':        body,
            }
            if field.get('examples'):
                content['examples'] = field['examples']
            if field.get('tips'):
                content['tips'] = field['tips']
            if field.get('common_mistakes'):
                content['common_mistakes'] = field['common_mistakes']
            agent._pending_rich_blocks.append({
                'type': 'field_help',
                'content': content,
            })
            return (f"Rendered field help for {field.get('label', field_id)} "
                    f"on screen.")

        @tool
        def show_option_details(lookup_ref: str, option_id: str) -> str:
            """Render a detail card for ONE option. Use for "what is X?"
            / "tell me more about Y"."""
            values = get_lookup_values(agent.schema, lookup_ref, agent.session.collected_data) or []
            from .validation_engine import get_option_id as _get_id
            opt = next(
                (v for v in values if isinstance(v, dict) and str(_get_id(v)) == str(option_id)),
                None,
            )
            if not opt:
                return f"ERROR: option '{option_id}' not in lookup '{lookup_ref}'."
            content = {k: v for k, v in opt.items()}
            agent._pending_rich_blocks.append({
                'type': 'option_detail',
                'content': content,
            })
            return f"Rendered detail card for {opt.get('label', option_id)} on screen."

        @tool
        def compare_options(lookup_ref: str, option_ids: List[str] = None) -> str:
            """Render side-by-side comparison cards for options. Use for
            "what's the difference between X and Y?" / "compare my
            choices". Pass None for option_ids to show all."""
            # Dedup: same lookup, no specific subset, recently rendered
            # → no-op. (When option_ids is set, the user is asking for a
            # specific subset comparison which is fresh content.)
            recently_rendered = (agent.session.rendered_lookups or [])[-5:]
            if not option_ids and lookup_ref in recently_rendered:
                return (
                    f"NO-OP. Comparison for {lookup_ref} was already shown "
                    f"recently. The user can scroll up. Just answer their "
                    f"question or accept their choice — don't re-render."
                )
            values = get_lookup_values(agent.schema, lookup_ref, agent.session.collected_data) or []
            if not values:
                return f"ERROR: lookup '{lookup_ref}' has no options."
            wanted = list(option_ids) if option_ids else None
            options = []
            for v in values:
                if not isinstance(v, dict):
                    continue
                from .validation_engine import get_option_id as _get_id
                if wanted and str(_get_id(v)) not in [str(w) for w in wanted]:
                    continue
                options.append({k: val for k, val in v.items()})
            if not options:
                return f"ERROR: none of the requested option ids found in '{lookup_ref}'."
            agent._pending_rich_blocks.append({
                'type': 'comparison',
                'content': {
                    'title': f"Comparison: {lookup_ref.replace('_', ' ').title()}",
                    'options': options,
                },
            })
            agent.session.rendered_lookups.append(lookup_ref)
            return f"Rendered side-by-side comparison of {len(options)} option(s) on screen."

        @tool
        def recommend_options(lookup_ref: str, n: int = 3, criteria: str = "") -> str:
            """Recommend up to N items from a lookup as a side-by-side
            comparison. Use when the user asks "who would you recommend?"
            / "what's the best fit?" / "give me your top X".

            How ranking works: the schema's lookup definition can declare
            an `order_by` field — e.g. ORDER BY rating DESC. We pull from
            the lookup with that ordering applied (DB-backed lookups use
            the SQL ORDER BY clause; inline lookups sort the list in
            Python). Filtering rules from the lookup's filter_by still
            apply, so recommendations only come from compliant rows.

            `criteria` is free-form natural language describing what the
            user is looking for ("near downtown Boston", "experienced
            with cardiology") — passed back in the result for the agent
            to mention while presenting the recommendation.
            """
            try:
                k = int(n) if n else 3
            except Exception:
                k = 3
            k = max(1, min(10, k))

            ld = (agent.schema.get('lookup_data') or {}).get(lookup_ref)
            if not ld:
                return f"ERROR: unknown lookup_ref '{lookup_ref}'."

            # Pull the values via the standard lookup path so filter_by
            # + collected_data interpolation already apply (so we never
            # recommend a non-compliant row).
            values = get_lookup_values(
                agent.schema, lookup_ref, agent.session.collected_data,
            ) or []
            if not values:
                return (
                    f"No items in lookup '{lookup_ref}' under the current "
                    f"filters. Cannot recommend. The user's earlier answers "
                    f"may have filtered the candidate set to zero — ask them "
                    f"to relax a constraint."
                )

            # Apply schema-declared ordering if present. order_by is a
            # list of {column, direction:'asc'|'desc'} dicts. For
            # database lookups, the DB layer should ideally handle the
            # ORDER BY at query time — but since we already fetched the
            # whole filtered set, sort in Python here for both inline
            # and DB cases. Cheap.
            order_by = ld.get('order_by') or []
            for spec in reversed(order_by):  # stable multi-key sort
                col = spec.get('column') if isinstance(spec, dict) else str(spec)
                direction = (spec.get('direction', 'desc') if isinstance(spec, dict) else 'desc').lower()
                values = sorted(
                    values,
                    key=lambda v: (v.get(col) is None, v.get(col) if isinstance(v, dict) else None),
                    reverse=(direction == 'desc'),
                )

            top = [v for v in values if isinstance(v, dict)][:k]
            if not top:
                return f"No usable rows in '{lookup_ref}' to recommend."

            agent._pending_rich_blocks.append({
                'type': 'comparison',
                'content': {
                    'title': f"Recommended {lookup_ref.replace('_', ' ').title()} (top {len(top)})",
                    'options': top,
                },
            })
            agent.session.rendered_lookups.append(lookup_ref)
            return (
                f"Rendered top {len(top)} recommendation(s) from {lookup_ref}"
                + (f" for criteria: {criteria!r}" if criteria else "")
                + ". Briefly tell the user what made these the top picks "
                  "and ask which one they'd like to choose."
            )

        @tool
        def show_tip(message: str, kind: str = "tip", title: str = "") -> str:
            """Show a callout box. kind ∈ tip|info|warning|success.
            Use sparingly to highlight something noteworthy."""
            valid = {'tip', 'info', 'warning', 'success'}
            if kind not in valid:
                kind = 'tip'
            agent._pending_rich_blocks.append({
                'type': 'tip_callout',
                'content': {
                    'message': message,
                    'kind': kind,
                    **({'title': title} if title else {}),
                },
            })
            return f"Rendered a {kind} callout on screen."

        @tool
        def show_recap() -> str:
            """Render the section-by-section recap panel with edit buttons.
            Use for "show me everything I've entered". (advance_section
            already auto-renders this on the final transition.)"""
            blocks = _build_recap_blocks(agent.schema, agent.session)
            agent._pending_rich_blocks.extend(blocks)
            return (
                "Presented the full recap on screen. "
                "Ask them to confirm — once they approve, call mark_ready_for_submission."
            )

        @tool
        def mark_ready_for_submission() -> str:
            """Signal that the user has confirmed the recap and is ready to submit.

            Sets session status to 'review' and flags the frontend to trigger the
            completion actions. Does NOT execute the actions itself — that's the
            route's responsibility.
            """
            updated = set_status(agent.session.session_id, STATUS_REVIEW)
            if updated:
                agent.session = updated
            # Defensive: if for some reason the user reached this point
            # without ever seeing a recap (e.g. agent skipped show_recap),
            # render one now so the "Ready to submit" banner has something
            # actually visible above it. Idempotent — won't duplicate
            # if a recap was already emitted this turn.
            already_has_recap = any(
                (b.get('type') == 'recap_panel') for b in agent._pending_rich_blocks
            )
            if not already_has_recap:
                # Also check the chat history — if a recent assistant turn
                # already showed a recap_panel, don't re-render. Cheap heuristic:
                # we don't track per-turn block history, so a fresh emit here
                # is safe (the panel renders deterministically from current
                # state and is the same every time).
                agent._pending_rich_blocks.extend(
                    _build_recap_blocks(agent.schema, agent.session)
                )
            agent._pending_actions.append({'type': 'ready_to_submit'})
            return "Marked ready for submission. The frontend will now trigger the completion actions."

        @tool
        def pause_listening(reason: str = "") -> str:
            """Voice-mode only: pause auto-listening when the user says
            stop / nevermind / hold on / pause / wait / etc. Judge by
            intent, not exact words."""
            logger.info("pause_listening called: reason=%r", reason)
            debug_log(agent.session, 'tool_call', {
                'tool': 'pause_listening',
                'args': {'reason': reason},
            })
            agent._pending_actions.append({
                'type': 'pause_listening',
                'reason': reason or '',
            })
            return "Auto-listen paused. The mic will not reopen automatically until the user taps it."

        @tool
        def clear_field(section_id: str, field_id: str) -> str:
            """Remove a value the user previously gave. Use for "leave it
            blank", "remove my email", "forget what I said about X"."""
            field = get_field(agent.schema, section_id, field_id)
            if not field:
                return f"ERROR: No field {field_id} in section {section_id}."
            section_data = agent.session.collected_data.get(section_id) or {}
            had_value = field_id in section_data
            if had_value:
                del section_data[field_id]
                agent.session.collected_data[section_id] = section_data
                save_session(agent.session)
            debug_log(agent.session, 'tool_call', {
                'tool': 'clear_field',
                'args': {'section_id': section_id, 'field_id': field_id},
                'had_value': had_value,
                'required': bool(field.get('required')),
            })
            note = ''
            if field.get('required'):
                note = " (note: this field is required and will need a value before submission)"
            return f"Cleared {field_id} in section {section_id}.{note}"

        @tool
        def restart_session(confirm: bool = False) -> str:
            """Wipe ALL data and start over. DESTRUCTIVE. First call with
            confirm=False to ask the user; only call with confirm=True
            after they've explicitly said yes."""
            if not confirm:
                debug_log(agent.session, 'tool_call', {
                    'tool': 'restart_session',
                    'args': {'confirm': False},
                    'note': 'awaiting user confirmation',
                })
                return ("Not restarted. Confirm with the user first, then "
                        "call this tool again with confirm=true.")
            agent.session.collected_data = {}
            agent.session.section_status = {}
            order = get_section_order(agent.schema)
            if order:
                first = order[0]
                updated = set_current_section(agent.session.session_id, first)
                if updated:
                    agent.session = updated
            save_session(agent.session)
            agent.update_phase(CollectionPhase.COLLECTING)
            agent._pending_actions.append({'type': 'restart_session'})
            debug_log(agent.session, 'tool_call', {
                'tool': 'restart_session',
                'args': {'confirm': True},
            })
            return ("Form reset. All previously-collected data has been "
                    "cleared and we're back at the first section. "
                    "Ask the user the first question fresh.")

        self.tools = [
            update_field,
            clear_field,
            show_lookup_data,
            show_field_help,
            show_option_details,
            compare_options,
            recommend_options,
            show_tip,
            get_collection_status,
            navigate_to_section,
            advance_section,
            show_recap,
            mark_ready_for_submission,
            pause_listening,
            restart_session,
        ]

        # Schema-author-selected custom tools from the platform's tools/
        # folder. Same format / secrets / lifecycle as any other custom
        # tool on the platform — DCA just opts a session into the named
        # subset by listing them in the schema's `custom_tools` array.
        # Failures isolated per tool: a missing or broken custom tool
        # is skipped with a warning, the rest of the agent runs fine.
        schema_custom_tool_names = self.schema.get('custom_tools') or []
        if schema_custom_tool_names:
            try:
                from .custom_tool_loader import load_schema_custom_tools
                extra_tools = load_schema_custom_tools(schema_custom_tool_names)
                if extra_tools:
                    self.tools.extend(extra_tools)
                    logger.info(
                        "Schema requested %d custom tool(s); %d loaded successfully: %s",
                        len(schema_custom_tool_names), len(extra_tools),
                        [getattr(t, 'name', '?') for t in extra_tools],
                    )
            except Exception as e:
                logger.warning("custom tool loading failed: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Agent executor build
    # ------------------------------------------------------------------
    def _build_agent_executor(self):
        if not getattr(self, 'tools', None):
            self._register_tools()

        # Escape any literal { or } in the system prompt for ChatPromptTemplate
        safe_system = self.SYSTEM.replace('{', '{{').replace('}', '}}')

        prompt = ChatPromptTemplate.from_messages([
            ("system", safe_system),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        # Use bind_tools (idiomatic) instead of bind(tools=...). bind_tools
        # is the documented entry point for tool-calling models — it
        # validates schemas, sets the right tool_choice default, and
        # integrates with ToolsAgentOutputParser correctly. The previous
        # `bind(tools=convert_to_openai_tool(t))` path was a manual
        # construction that worked sometimes but lost reliability with
        # reasoning models (gpt-5.x) where tool dispatch is more sensitive.
        if hasattr(self.llm, 'bind_tools'):
            llm_with_tools = self.llm.bind_tools(self.tools)
        else:
            # Older LangChain versions — fall back to manual format
            tools_formatted = [convert_to_openai_tool(t) for t in self.tools]
            llm_with_tools = self.llm.bind(tools=tools_formatted)
        agent = (
            {
                "input": lambda x: x["input"],
                "chat_history": lambda x: x.get("chat_history", []),
                "agent_scratchpad": lambda x: format_to_tool_messages(x["intermediate_steps"]),
            }
            | prompt
            | llm_with_tools
            | ToolsAgentOutputParser()
        )
        # Hard ceilings on the agent's loop so a single turn can't hang the
        # chat. max_execution_time is wall-clock seconds; if exceeded,
        # early_stopping_method='generate' tells the LLM to produce a
        # final answer instead of erroring or looping silently. Tunable
        # via env var so deployments on slow proxies can loosen it.
        try:
            _max_iter = int(os.getenv('DCA_AGENT_MAX_ITERATIONS', '6'))
        except ValueError:
            _max_iter = 6
        try:
            _max_exec = float(os.getenv('DCA_AGENT_MAX_EXECUTION_SECONDS', '25'))
        except ValueError:
            _max_exec = 25.0

        self.agent_executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=False,
            handle_parsing_errors=True,
            max_iterations=_max_iter,
            max_execution_time=_max_exec,
            early_stopping_method='generate',
        )

    # ------------------------------------------------------------------
    # Chat history rehydration
    # ------------------------------------------------------------------
    def _rehydrate_chat_history(self) -> List:
        out = []
        for msg in (self.session.chat_history or []):
            role = msg.get('role')
            content = msg.get('content', '')
            if role == 'user':
                out.append(HumanMessage(content=content))
            elif role == 'assistant':
                out.append(AIMessage(content=content))
            # System / metadata messages are skipped (system prompt is rebuilt fresh)
        return out

    # ------------------------------------------------------------------
    # Initial greeting (called once at session creation)
    # ------------------------------------------------------------------
    def bootstrap_greeting(self) -> str:
        """
        Run the agent once on a fresh session to produce an opening message
        that introduces the form and asks the first question.

        Persists ONLY the assistant response to chat_history (no synthetic user
        message — that would be visible on session resume). Returns the greeting
        text so the route can include it in the create-session response.
        """
        kickoff = (
            "This is a brand new session — no chat history yet. Begin the "
            "conversation now. Greet the user briefly, list the sections we'll "
            "go through, and ask the first question for the first section. "
            "Keep it concise — one or two short paragraphs."
        )
        try:
            result = self.agent_executor.invoke({
                "input": kickoff,
                "chat_history": [],
            })
            response = (result.get("output") or "").strip()
        except Exception as e:
            logger.error(f"bootstrap_greeting failed: {e}", exc_info=True)
            response = ""

        if not response:
            # Deterministic fallback if the LLM fails
            name = self.schema.get('name', 'this form')
            sections = sorted(
                (self.schema.get('sections') or []),
                key=lambda s: s.get('order', 999),
            )
            section_list = '\n'.join(f"  - {s.get('title', s['id'])}" for s in sections)
            response = (
                f"Hi! I'll help you complete the {name}.\n\n"
                f"We have {len(sections)} section(s) to go through:\n{section_list}\n\n"
                "Let's get started."
            )

        # Persist ONLY the assistant turn — never a synthetic user message
        self.session.append_chat('assistant', response)
        save_session(self.session)
        return response

    # ------------------------------------------------------------------
    # REMOVED: regex-based auto-render safety net.
    #
    # This used to scan the agent's response for phrases like "from the
    # options" / "I've shown" and auto-render the current section's
    # lookup if no render tool was called. It was a band-aid for cases
    # where the LLM narrated a visual without calling a tool. Removed
    # because it was producing false positives — re-rendering a table
    # that was already visible from a prior turn just because the
    # agent referred back to it. The architecture is cleaner without
    # it: trust the agent's tool calls, dedup at the tool level via
    # session.rendered_lookups, surface bluffs as `error` debug
    # events for observability rather than silently papering over
    # them. Method body deliberately removed to make the deletion
    # obvious in code archaeology.
    # ------------------------------------------------------------------
    def _auto_render_if_agent_claimed_visual_DEPRECATED(self, response: str) -> None:
        # Intentionally empty. See comment above for why this was removed.
        return

    # ------------------------------------------------------------------
    # Loop-limit fallback — used when the LLM agent loops without
    # producing a final answer. Build a deterministic response so the
    # user never sees an empty turn / endless "Thinking…".
    # ------------------------------------------------------------------
    def _fallback_response_after_loop_limit(self) -> str:
        sid = self.session.current_section_id
        if not sid:
            return ("I'm having trouble figuring out the next step. "
                    "Could you tell me what you'd like to do?")
        section = get_section(self.schema, sid)
        section_title = (section.get('title') if section else None) or sid

        missing = get_missing_required_fields(self.schema, sid, self.session.collected_data)
        if missing:
            first = missing[0]
            label = first.get('label') or first.get('id')
            hint = first.get('prompt_hint') or ''
            extra = f" {hint}" if hint else ""
            return (f"Let's keep going on {section_title}. "
                    f"What's your {label}?{extra}")

        # No missing required fields in current section — try to advance
        next_sid = get_next_section_id(self.schema, sid)
        if next_sid:
            next_section = get_section(self.schema, next_sid)
            next_title = (next_section.get('title') if next_section else None) or next_sid
            return (f"{section_title} looks complete. "
                    f"Moving on to {next_title}. What can you tell me about it?")

        # All sections done
        return ("It looks like we have everything we need. "
                "Would you like to review and submit?")

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    def process_message(self, message: str) -> Tuple[str, Dict]:
        """
        Process one user message. Returns (response_text, metadata).

        metadata includes:
          - phase, current_section, section_status, collected_data
          - rich_blocks: list of rich content blocks emitted by tools
          - actions: list of side-channel actions for the UI
          - validation_errors: per-section per-field errors
        """
        self._pending_rich_blocks = []
        self._pending_actions = []

        # Debug visibility: log the system prompt, the user message, and the
        # voice_mode flag at the very start of the turn. The tool_call /
        # tool_result / voice_normalize events get logged from inside their
        # respective code paths.
        debug_log(self.session, 'turn_start', {
            'phase': self.phase.value,
            'voice_mode': getattr(self.session, 'voice_mode', False),
            'current_section': self.session.current_section_id,
        })
        if debug_enabled():
            debug_log(self.session, 'system_prompt', {
                'prompt': truncate_for_display(self.SYSTEM, max_len=20000),
                'length_chars': len(self.SYSTEM),
            })
            debug_log(self.session, 'user_message', {
                'message': message,
            })

        import time as _t
        invoke_started_at = _t.time()

        # Per-turn debug callback — captures every LLM iteration's
        # output AND every tool dispatch / result, so the debug panel
        # has full visibility into what the agent actually does. The
        # tool_call / tool_result events go through the existing
        # filter chips in the panel.
        class _IterCallback(BaseCallbackHandler):
            def __init__(self, sess):
                self._sess = sess
                self._iter = 0

            def on_llm_end(self, response, **kwargs):
                try:
                    self._iter += 1
                    gen = response.generations[0][0] if response.generations else None
                    msg = getattr(gen, 'message', None) if gen else None
                    text = (getattr(gen, 'text', None) or '') if gen else ''
                    tool_calls = []
                    if msg is not None:
                        tcs = getattr(msg, 'tool_calls', None) or []
                        for tc in tcs:
                            if isinstance(tc, dict):
                                tool_calls.append({
                                    'name': tc.get('name'),
                                    'args': tc.get('args'),
                                })
                            else:
                                tool_calls.append({
                                    'name': getattr(tc, 'name', None),
                                    'args': getattr(tc, 'args', None),
                                })
                    debug_log(self._sess, 'note', {
                        'where': f'llm_iter_{self._iter}',
                        'text_preview': (text or '')[:600],
                        'tool_calls': tool_calls,
                        'tool_call_count': len(tool_calls),
                    })
                    # Surface "narrated without calling tool" as a loud
                    # error event in the debug panel so the user sees
                    # immediately when the LLM is bluffing.
                    text_lc = (text or '').lower()
                    visual_claim_phrases = [
                        "i've shown", "i have shown", "i've put", "i have put",
                        "i've displayed", "i've highlighted", "i've laid out",
                        "i've listed", "i've put together", "i've put the recap",
                        "shown above", "shown you", "on screen", "options i've",
                        "from the options", "from the list", "from the choices",
                    ]
                    if (not tool_calls) and any(p in text_lc for p in visual_claim_phrases):
                        debug_log(self._sess, 'error', {
                            'where': 'llm_narrated_without_tool',
                            'text_preview': (text or '')[:300],
                            'detected_phrase': next(
                                (p for p in visual_claim_phrases if p in text_lc),
                                None,
                            ),
                            'note': 'LLM claimed a visual action without calling a render tool. '
                                    'Auto-render safety net should fire in the post-process step.',
                        })
                except Exception as e:
                    logger.debug("iter callback failed: %s", e)

            def on_tool_start(self, serialized, input_str, **kwargs):
                # Emits a tool_call event for EVERY tool the agent
                # dispatches. Previously only 4 tools logged manually,
                # which is why the debug panel showed zero tool calls
                # even when the agent was clearly using them.
                try:
                    name = (serialized or {}).get('name')
                    debug_log(self._sess, 'tool_call', {
                        'tool': name,
                        'args_or_input': str(input_str)[:600],
                        'source': 'on_tool_start (LangChain callback)',
                    })
                except Exception:
                    pass

            def on_tool_end(self, output, **kwargs):
                try:
                    name = None
                    serialized = kwargs.get('serialized') or {}
                    name = serialized.get('name')
                    debug_log(self._sess, 'tool_result', {
                        'tool': name,
                        'result_preview': str(output)[:600],
                        'source': 'on_tool_end (LangChain callback)',
                    })
                except Exception:
                    pass

            def on_tool_error(self, error, **kwargs):
                try:
                    name = (kwargs.get('serialized') or {}).get('name')
                    debug_log(self._sess, 'error', {
                        'where': 'tool_error',
                        'tool': name,
                        'error': str(error),
                    })
                except Exception:
                    pass

        try:
            result = self.agent_executor.invoke(
                {
                    "input": message,
                    "chat_history": self.chat_history,
                },
                {"callbacks": [_IterCallback(self.session)]},
            )
            response = result.get("output", "") or ""
            elapsed = _t.time() - invoke_started_at
            logger.info("agent_executor.invoke complete in %.2fs", elapsed)
            debug_log(self.session, 'note', {
                'where': 'agent_executor.invoke',
                'elapsed_s': round(elapsed, 2),
            })
            # If the executor hit max_iterations / max_execution_time it
            # returns the early-stopping output, which can be empty or a
            # generic "Agent stopped" string. Detect and produce a
            # graceful response so the user is never stuck staring at
            # nothing.
            if not response or response.strip().lower().startswith('agent stopped'):
                logger.warning(
                    "agent stopped early (likely hit max_iterations or "
                    "max_execution_time) after %.2fs", elapsed
                )
                debug_log(self.session, 'error', {
                    'where': 'agent_executor.invoke',
                    'error': 'early stopping — empty or "Agent stopped" output',
                    'elapsed_s': round(elapsed, 2),
                })
                response = self._fallback_response_after_loop_limit()
        except Exception as e:
            logger.error(f"Error in agent_executor.invoke: {e}", exc_info=True)
            debug_log(self.session, 'error', {
                'where': 'agent_executor.invoke',
                'error': str(e),
                'elapsed_s': round(_t.time() - invoke_started_at, 2),
            })
            response = (
                "Sorry — I ran into a problem processing that. "
                "Could you rephrase or try again?"
            )

        # NOTE: We previously had a regex-based auto-render safety net
        # here that scanned the agent's response for phrases like
        # "from the options" / "I've shown" and would auto-render the
        # current section's lookup if no rendering tool was called.
        # Removed because it caused worse problems than it solved:
        # duplicate renders when the agent legitimately referred to a
        # table that was already in the chat from a prior turn (the
        # regex couldn't distinguish "I'm showing options now" from
        # "I'm referring to the options I showed earlier"). The
        # `llm_narrated_without_tool` debug event still fires as a
        # diagnostic signal so we can see when the LLM bluffs, but it
        # no longer takes action. Per-tool dedup (rendered_lookups)
        # is the new defense — see show_lookup_data / compare_options.

        # Persist the conversation turn
        self.session.append_chat('user', message)
        self.session.append_chat('assistant', response)
        save_session(self.session)

        # Build metadata for the frontend
        validation_errors = validate_all(self.schema, self.session.collected_data)
        metadata = {
            'phase': self.phase.value,
            'session_id': self.session.session_id,
            'config_id': self.session.config_id,
            'status': self.session.status,
            'current_section': self.session.current_section_id,
            'section_status': self.session.section_status,
            'collected_data': self.session.collected_data,
            'validation_errors': validation_errors,
            'rich_blocks': list(self._pending_rich_blocks),
            'actions': list(self._pending_actions),
            # Map of section_id -> [field_id, ...] for fields whose
            # conditional.show_when currently evaluates true. The
            # frontend uses this to hide fields in the progress panel
            # that don't apply yet (e.g. follow-up phone when the user
            # says they don't need a follow-up call).
            'visible_fields': visible_fields_by_section(
                self.schema, self.session.collected_data
            ),
        }
        debug_log(self.session, 'llm_response', {
            'response': truncate_for_display(response, max_len=8000),
        })
        # Visibility into what's actually being shipped to the frontend
        # for rendering. If the user "never sees" rich blocks, this
        # event tells us whether the blocks were even produced. If
        # block_count=0 here, the agent didn't emit any (or auto-render
        # didn't fire). If block_count>0 but the user doesn't see them,
        # it's a frontend rendering issue.
        rb_types = [(b or {}).get('type') for b in self._pending_rich_blocks]
        debug_log(self.session, 'note', {
            'where': 'metadata_build',
            'rich_block_count': len(self._pending_rich_blocks),
            'rich_block_types': rb_types,
            'actions': list(self._pending_actions),
        })
        logger.info(
            "metadata built: rich_blocks=%d types=%s actions=%s",
            len(self._pending_rich_blocks), rb_types, self._pending_actions,
        )
        debug_log(self.session, 'turn_end', {
            'phase': metadata['phase'],
            'actions': metadata['actions'],
            'validation_errors': validation_errors,
        })
        return response, metadata


# ----------------------------------------------------------------------
# Recap builder (deterministic — does NOT use the LLM)
# ----------------------------------------------------------------------
def _build_recap_blocks(schema: Dict, session: CollectionSession) -> List[Dict]:
    """
    Build a single polished `recap_panel` block summarizing all collected
    data, with per-field edit buttons so the user can fix anything from
    the recap itself. Falls back gracefully when fields are missing.
    """
    panel_sections = []
    missing_required = []

    for sid in get_section_order(schema):
        section = get_section(schema, sid)
        if not section:
            continue
        section_data = session.collected_data.get(sid) or {}
        rows = []
        for fld in section.get('fields', []):
            fid = fld.get('id')
            if not fid:
                continue
            # Skip fields whose conditional.show_when currently
            # evaluates false — they don't apply to this submission.
            # If the user previously answered the field and then made
            # a change that hid it, we drop the value from the recap
            # so it isn't shown / submitted as if it counted.
            if not is_field_visible(schema, sid, fid, session.collected_data):
                continue
            if fid not in section_data:
                # Track missing-required for the bottom callout
                if fld.get('required'):
                    missing_required.append({
                        'section': section.get('title', sid),
                        'label': fld.get('label', fid),
                    })
                continue
            value = section_data[fid]
            display = _format_value_for_display(value, fld, schema, session.collected_data)
            rows.append({
                'label': fld.get('label', fid),
                'value': display,
                'field_id': fid,
            })
        panel_sections.append({
            'id': sid,
            'title': section.get('title', sid),
            'rows': rows,
        })

    return [{
        'type': 'recap_panel',
        'content': {
            'intro': ("Here's everything you've provided. Use the pencil "
                      "icon next to any value to edit it, or just tell "
                      "me what to change."),
            'sections': panel_sections,
            'missing_required': missing_required,
        },
    }]


def _format_value_for_display(
    value: Any, field: Dict, schema: Dict,
    collected_data: Optional[Dict] = None,
) -> str:
    """Render a stored value for human-readable display in the recap.
    `collected_data` is passed through to lookups so DB-backed sources
    can interpolate any `{{collected.X}}` filters."""
    if value is None or value == '':
        return '—'

    field_type = field.get('type', 'text')

    if field_type == 'boolean':
        return 'Yes' if value else 'No'

    if field_type == 'multi_select' and isinstance(value, list):
        return ', '.join(str(v) for v in value)

    if field_type == 'lookup' and field.get('lookup_ref'):
        # Try to expand the id to a friendly label
        from .validation_engine import get_option_id
        items = get_lookup_values(schema, field['lookup_ref'], collected_data)
        for item in items:
            if isinstance(item, dict) and str(get_option_id(item)) == str(value):
                return str(item.get('label') or item.get('name') or value)
        return str(value)

    if field_type == 'select' and field.get('options_ref'):
        from .validation_engine import get_option_id
        items = get_lookup_values(schema, field['options_ref'], collected_data)
        for item in items:
            if isinstance(item, dict) and str(get_option_id(item)) == str(value):
                return str(item.get('label') or value)
        return str(value)

    return str(value)
