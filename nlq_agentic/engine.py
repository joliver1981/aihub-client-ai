"""AgenticNLQEngine — the V3 engine (plan §3/§4).

A drop-in for LLMDataEngine as the entry points use it: same get_answer surface,
same return shapes, picklable through the same session stores, same helper
methods the routes touch. Internally it is a single OpenAI tool loop over
get_table_details / run_sql / respond, with a deterministic read-only SQL gate
and an automatic fallback to the legacy engine on any failure.

Nothing here imports the V2 engine modules; the small amount of borrowed logic
(target-DB resolution, the empty-schema guard, the rich-content wrapper) is
copied and adapted per the plan's borrowing policy. Shared read-only utilities
(DataUtils, AppUtils, api_keys_config) are imported normally — importing does
not modify them.
"""
import logging
import time
from datetime import datetime

import config as cfg

from .state import AgentSessionState
from .tools import ToolContext, build_tool_schemas
from .loop import run_loop
from . import contract
from .telemetry import RequestTrace

logger = logging.getLogger("nlq_agentic.engine")


def _load_renderer():
    """Lazy, defensive load of the same renderer the legacy engine uses."""
    try:
        if getattr(cfg, "SMART_RENDER_HYBRID_ENABLED", False):
            from SmartContentRenderer_hybrid import SmartContentRendererHybrid as _R
        else:
            from SmartContentRenderer import SmartContentRenderer as _R
        return _R()
    except Exception:
        try:
            from SmartContentRenderer import SmartContentRenderer as _R
            return _R()
        except Exception as e:
            logger.warning(f"[engine] SmartContentRenderer unavailable: {e}")
            return None


class AgenticNLQEngine:
    def __init__(self, provider="openai"):
        self.provider = provider
        self.state = AgentSessionState()
        self.environment = self.state          # compatibility alias for legacy reads
        self._client = None
        self._config = None
        self.content_renderer = _load_renderer()
        # Some legacy code paths reference these; keep present + inert.
        self.query_engine = None
        self.analytical_engine = None

    # ── pickle: never persist the live client / renderer handle ─────────
    def __getstate__(self):
        st = self.__dict__.copy()
        st["_client"] = None
        st["_config"] = None
        st["content_renderer"] = None
        return st

    def __setstate__(self, st):
        self.__dict__.update(st)
        self._client = None
        self._config = None
        self.content_renderer = _load_renderer()
        self.environment = self.state          # re-establish the alias

    # ── interface parity with LLMDataEngine ─────────────────────────────
    def clear_chat_hist(self):
        self.state.clear_history()

    def add_message_to_hist(self, message, is_user=True):
        self.state.add_message(message, is_user=is_user)

    def set_conversation_history(self, conversation_history):
        """Accept the client history format [{'role':'Q'|'A','content':..}] (str or list)."""
        self.clear_chat_hist()
        entries = conversation_history
        if isinstance(entries, str):
            parsed = None
            for loader in (self._json_load, self._ast_load):
                parsed = loader(entries)
                if parsed is not None:
                    break
            entries = parsed or []
        if not isinstance(entries, list):
            return
        for entry in entries:
            if isinstance(entry, dict) and "role" in entry:
                self.add_message_to_hist(entry.get("content", ""), is_user=(entry.get("role") == "Q"))

    @staticmethod
    def _json_load(s):
        import json
        try:
            return json.loads(s)
        except Exception:
            return None

    @staticmethod
    def _ast_load(s):
        import ast
        try:
            return ast.literal_eval(s)
        except Exception:
            return None

    @property
    def question_count(self):
        return self.state.question_count

    def explain(self):
        return ""

    def format_response_with_rich_content(self, answer, answer_type, context=None):
        """Copied/adapted from LLMDataEngine — turn an answer into rich-content blocks."""
        if not getattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", False) or self.content_renderer is None:
            return answer, answer_type
        context = context or {}
        try:
            if answer_type == "dataframe":
                return self.content_renderer.analyze_and_render(
                    answer, context={"query": context.get("question"), "type": "data_response"}
                ), "rich_content"
            if answer_type == "string":
                return self.content_renderer.analyze_and_render(
                    answer, context={"query": context.get("question"), "type": "text_response"}
                ), "rich_content"
            if answer_type == "chart":
                return {"type": "rich_content",
                        "blocks": [{"type": "chart", "content": answer, "metadata": {"generated": True}}]}, "rich_content"
            return self.content_renderer._create_text_block(str(answer)), "rich_content"
        except Exception as e:
            logger.warning(f"[engine] rich render failed ({e}); returning plain")
            return answer, answer_type

    # ── OpenAI client (mirrors azureQuickPrompt / the P0 spike) ─────────
    def _ensure_client(self):
        if self._client is None:
            from api_keys_config import get_openai_config
            from AppUtils import _create_openai_client
            self._config = get_openai_config(use_alternate_api=True)
            self._client = _create_openai_client(self._config)
        return self._client

    def _base_kwargs(self):
        config = self._config
        default_model = config["model"] if config["api_type"] == "open_ai" else config["deployment_id"]
        model = getattr(cfg, "NLQ_AGENTIC_MODEL", "") or default_model
        kwargs = {"model": model}
        if config.get("reasoning_effort"):
            kwargs["reasoning_effort"] = config["reasoning_effort"]
            kwargs["temperature"] = 1.0
        else:
            kwargs["temperature"] = 0.0
        return kwargs

    # ── target database resolution (adapted from V2._set_target_database) ─
    def _set_target_database(self, agent_id):
        from DataUtils import (
            get_connection_string,
            query_app_database,
            get_enhanced_table_metadata_as_yaml,
            get_table_descriptions_as_yaml,
        )
        conn_str, connection_id, database_type = get_connection_string(agent_id)
        self.state.connection_string = conn_str
        self.state.connection_id = connection_id
        self.state.database_type = database_type or "SQL Server"

        try:
            rows = query_app_database(
                "SELECT table_name FROM llm_Tables WHERE connection_id = ?", (connection_id,)
            )
            self._all_table_names = [r["table_name"] for r in rows] if rows else []
        except Exception as e:
            logger.warning(f"[engine] table-name load failed: {e}")
            self._all_table_names = []

        catalog = ""
        try:
            catalog = get_enhanced_table_metadata_as_yaml(connection_id) or ""
        except Exception:
            catalog = ""
        if not catalog:
            try:
                catalog = get_table_descriptions_as_yaml(connection_id) or ""
            except Exception:
                catalog = ""
        self._table_catalog = catalog

    # ── system prompt ───────────────────────────────────────────────────
    def _build_system_prompt(self):
        db = self.state.database_type or "SQL Server"
        current_date = datetime.now().strftime("%Y-%m-%d")
        parts = [
            "You are a data analysis assistant for a business intelligence platform. You answer "
            f"business questions by querying a {db} database and explaining the results in plain, "
            "non-technical language.",
            f"\nCurrent date: {current_date}",
            "\nHOW TO WORK:",
            "- You can only READ data. Only a single SELECT runs; anything else is rejected by a gate.",
            "- Pick relevant tables from the catalog, call get_table_details to see their columns, "
            "semantic types, example values, synonyms, business rules, required filters, and any "
            "calculated metrics (virtual columns whose 'formula' you put directly into the SQL).",
            "- Apply required filters and business rules. Map business synonyms to the right columns. "
            f"Write {db}-compatible SQL.",
            "- For an ambiguous time period (e.g. a holiday with no year), use the most recent "
            "occurrence present in the data and state that assumption in your answer.",
            "- If a query errors, read the message, fix the SQL, and retry.",
            "- Finish by calling respond exactly once: answer_kind='table' with a dataset_ref to show "
            "rows, or 'text' for a single value, an explanation, or to ask for clarification.",
            "\nAVAILABLE TABLES (catalog):",
            self._table_catalog or "(no catalog available)",
        ]
        ds_summary = self.state.datasets_summary()
        if ds_summary:
            parts += ["\nDATASETS ALREADY AVAILABLE THIS SESSION (reuse instead of re-querying when they answer the question):", ds_summary]
        hist = self.state.recent_history_text()
        if hist:
            parts += ["\nRECENT CONVERSATION:", hist]
        return "\n".join(parts)

    # ── the public entry point ──────────────────────────────────────────
    def get_answer(self, agent_id, input_question, recursion_depth=0):
        trace = RequestTrace(agent_id, input_question)
        try:
            from nlq_engine_factory import agentic_breaker
            if agentic_breaker.is_open():
                return self._fallback(agent_id, input_question, trace, reason="circuit breaker open")
            result = self._run_agentic(agent_id, input_question, trace)
            agentic_breaker.record_success()
            return result
        except Exception as e:
            logger.error(f"[engine] agentic get_answer failed for agent {agent_id}: {e}", exc_info=True)
            trace.error = str(e)
            try:
                from nlq_engine_factory import agentic_breaker
                agentic_breaker.record_failure()
            except Exception:
                pass
            return self._fallback(agent_id, input_question, trace, reason=str(e))
        finally:
            try:
                trace.emit()
            except Exception:
                pass

    def _run_agentic(self, agent_id, input_question, trace):
        self._set_target_database(agent_id)
        self.state.agent_id = agent_id
        self.state.question_count += 1

        # Empty-schema guard (adapted from V2 get_answer): no documented schema →
        # honest message instead of a schema-blind loop.
        if not getattr(self, "_all_table_names", None) and not (self._table_catalog or "").strip():
            logger.warning(f"[engine] empty schema for agent {agent_id} (connection {self.state.connection_id})")
            msg = ("This agent's data source hasn't been set up for queries yet — no tables or columns "
                   "have been documented for its database connection. Ask an administrator to run schema "
                   "discovery (AI Discovery) on this connection, then try again.")
            return self._shape(msg, "string", input_question,
                               explain="Agent data source has no documented schema.")

        client = self._ensure_client()
        base_kwargs = self._base_kwargs()
        trace.model = base_kwargs.get("model")

        strict = getattr(cfg, "NLQ_AGENTIC_STRICT_TOOLS", True)
        tool_schemas = build_tool_schemas(strict=strict)
        ctx = ToolContext(self.state, row_cap=int(getattr(cfg, "NLQ_AGENTIC_SQL_ROW_CAP", 10000)))

        system_prompt = self._build_system_prompt()
        max_iters = int(getattr(cfg, "NLQ_AGENTIC_MAX_TOOL_ITERATIONS", 8))
        deadline = time.time() + int(getattr(cfg, "NLQ_AGENTIC_TIMEOUT_S", 90))

        loop_result = run_loop(
            client, base_kwargs, system_prompt, input_question, tool_schemas, ctx,
            trace=trace, max_iterations=max_iters, deadline=deadline,
        )

        result = contract.build_result(loop_result, self.state, input_question, self)
        trace.final_answer_type = self._answer_type_of(result)

        # Record the turn (best-effort; routes usually reseed history each request).
        self.state.add_message(input_question, is_user=True)
        self.state.add_message(self._history_note(result), is_user=False)
        return result

    # ── fallback to the trusted legacy engine ───────────────────────────
    def _fallback(self, agent_id, input_question, trace, reason):
        trace.fallback_used = True
        trace.fallback_reason = reason
        logger.error(f"[NLQ_AGENTIC_FALLBACK] agent={agent_id} reason={reason!r} — serving via legacy engine")

        if not getattr(cfg, "NLQ_AGENTIC_FALLBACK", True):
            return self._shape(cfg.DATA_AGENT_FALLBACK_RESPONSE, "string", input_question,
                               explain="Agentic engine failed and fallback is disabled.")
        try:
            from nlq_engine_factory import _construct_legacy
            legacy = _construct_legacy(enhance=False)
            legacy.clear_chat_hist()
            for entry in self.state.chat_history:
                legacy.add_message_to_hist(entry.get("content", ""), is_user=(entry.get("role") == "user"))
            return legacy.get_answer(agent_id, input_question)
        except Exception as e:
            logger.error(f"[NLQ_AGENTIC_FALLBACK] legacy fallback ALSO failed: {e}", exc_info=True)
            return self._shape(cfg.DATA_AGENT_FALLBACK_RESPONSE, "string", input_question,
                               explain="Both engines failed to answer.")

    # ── small helpers ───────────────────────────────────────────────────
    def _shape(self, answer, answer_type, input_question, explain="", special_message=""):
        """Build a legacy-shaped return for engine-level (non-loop) messages."""
        return_query = ""
        if getattr(cfg, "ENABLE_RICH_CONTENT_RENDERING", False):
            rich, _ = self.format_response_with_rich_content(
                answer, answer_type, {"question": input_question, "agent_id": self.state.agent_id})
            return {
                "answer": answer, "answer_type": answer_type, "rich_content": rich,
                "rich_content_enabled": rich is not None, "explain": explain, "clarify": "",
                "special_message": special_message, "query": return_query,
            }
        return (answer, explain, "", answer_type, special_message, input_question, "", return_query)

    @staticmethod
    def _answer_type_of(result):
        if isinstance(result, dict):
            return result.get("answer_type")
        try:
            return result[3]
        except Exception:
            return None

    @staticmethod
    def _history_note(result):
        """A compact assistant-turn note for internal history (no big DataFrames)."""
        atype = AgenticNLQEngine._answer_type_of(result)
        if atype == "dataframe":
            return "<See query result table above>"
        if isinstance(result, dict):
            return str(result.get("answer", ""))
        try:
            return str(result[0])
        except Exception:
            return ""
