"""Map a LoopResult onto the legacy engine's return shapes (plan §3).

The agentic engine must be indistinguishable from LLMDataEngine at the boundary,
so the UI, the Command Center delegator, and GeneralAgent can't tell which engine
answered. Legacy returns either:

  * 8-tuple: (answer, explain, clarify, answer_type, special_message,
              input_question, revised_question, return_query)
  * rich dict (when cfg.ENABLE_RICH_CONTENT_RENDERING): {answer, answer_type,
    rich_content, rich_content_enabled, explain, clarify, special_message, query}

answer_type ∈ {string, dataframe, chart, none}. Table answers return a real
pd.DataFrame. return_query preserves the '=== Data Query ===\\n<sql>' format the
UI/CC display.
"""
import logging

import config as cfg

logger = logging.getLogger("nlq_agentic.contract")

_VALID_ANSWER_TYPES = ("string", "dataframe", "chart", "none")


def _resolve_core(loop_result, state, input_question):
    """Return (answer, answer_type, explain, clarify, special_message, return_query)."""
    fallback = cfg.DATA_AGENT_FALLBACK_RESPONSE
    explain = ""
    clarify = ""
    special_message = ""

    return_query = ""
    if state.current_query:
        return_query = "=== Data Query ===\n" + state.current_query

    terminal = loop_result.terminal

    # Unresolved: timed out or hit the iteration cap without a final answer.
    if terminal is None:
        if loop_result.timed_out:
            answer = ("I wasn't able to finish answering that in time. Please try again or "
                      "narrow the question.")
        else:
            answer = fallback
        return answer, "string", "Agentic loop did not reach a final answer.", clarify, special_message, return_query

    kind = terminal.get("answer_kind", "text")
    text = (terminal.get("text") or "").strip()

    # Clarification request (ask_user tool) — populate clarify so the UI can prompt.
    if terminal.get("tool") == "ask_user":
        answer = text or "Could you clarify your question?"
        return answer, "string", explain, answer, special_message, return_query

    if kind == "chart":
        chart = getattr(state, "pending_chart", None)
        if chart and chart.get("html"):
            explain = text or explain
            return "See chart...", "chart", explain, clarify, chart["html"], return_query
        # Chart requested but none rendered — degrade to a table, else text.
        logger.info("[contract] chart answer with no pending chart; degrading")
        kind = "table" if state.datasets else "text"

    if kind == "table":
        ref = terminal.get("dataset_ref")
        ds = state.get_dataset(ref) if ref else None
        # Fall back to the most recent dataset if the model gave a bad/missing ref.
        if ds is None and state.datasets:
            last_ref = list(state.datasets.keys())[-1]
            ds = state.datasets[last_ref]
            logger.info(f"[contract] table answer with unresolved ref {ref!r}; using {last_ref}")
        if ds is not None and ds.get("df") is not None:
            if text:
                explain = text  # keep the model's caption without overwriting the grid
            display_df = _format_for_display(ds["df"], state)
            return display_df, "dataframe", explain, clarify, special_message, return_query
        # No dataset to show — degrade to whatever text we have.
        answer = text or fallback
        return answer, "string", explain, clarify, special_message, return_query

    # text answer (scalar values, explanations)
    answer = text or fallback
    return answer, "string", explain, clarify, special_message, return_query


def _format_for_display(df, state):
    """Apply deterministic dictionary formatting to a display copy (best-effort)."""
    try:
        from .formatting import format_dataframe_for_display
        return format_dataframe_for_display(df, state.connection_id)
    except Exception as e:
        logger.debug(f"[contract] display formatting skipped: {e}")
        return df


def build_result(loop_result, state, input_question, engine):
    """Build the legacy-shaped return value (tuple or rich dict)."""
    answer, answer_type, explain, clarify, special_message, return_query = _resolve_core(
        loop_result, state, input_question
    )
    if answer_type not in _VALID_ANSWER_TYPES:
        answer_type = "string"

    if not getattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", False):
        return (answer, explain, clarify, answer_type, special_message,
                input_question, "", return_query)

    # Rich-content path — mirror legacy get_answer's tail using the engine's renderer.
    try:
        rich_answer, _rich_type = engine.format_response_with_rich_content(
            answer, answer_type, context={"question": input_question, "agent_id": state.agent_id}
        )
        rich_enabled = True
    except Exception as e:
        logger.warning(f"[contract] rich rendering failed ({e}); returning plain answer")
        rich_answer, rich_enabled = None, False

    return {
        "answer": answer,
        "answer_type": answer_type,
        "rich_content": rich_answer,
        "rich_content_enabled": rich_enabled,
        "explain": explain,
        "clarify": clarify,
        "special_message": special_message,
        "query": return_query,
    }
