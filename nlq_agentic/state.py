"""Session state for the agentic NLQ engine (plan §4).

Plain, picklable data — no LLM client, no wrappers. Doubles as the engine's
`environment` compatibility shim: the legacy entry points read
`engine.environment.chat_history` / `.question_count` on some paths, so this
object exposes those names too. Everything here survives a pickle round-trip
through the existing session stores unchanged.
"""


class AgentSessionState:
    def __init__(self):
        # Cross-turn conversation, OpenAI-ish role dicts: {"role": "user"|"assistant", "content": str}
        self.chat_history = []
        # Datasets produced this session: ref -> {df, sql, description, columns, row_count}
        self.datasets = {}
        self._dataset_counter = 0
        # Compatibility / bookkeeping fields the legacy surface reads.
        self.question_count = 0
        self.current_query = None
        self.confidence_score = 0.8   # legacy nlq_enhancements reads this; agentic never gates on it
        self.was_last_query_successful = False
        self.last_query_row_count = None
        # Target DB, resolved per get_answer call.
        self.agent_id = None
        self.connection_id = None
        self.connection_string = None
        self.database_type = None

    # ── conversation history ────────────────────────────────────────────
    def clear_history(self):
        self.chat_history = []

    def add_message(self, content, is_user=True):
        self.chat_history.append({
            "role": "user" if is_user else "assistant",
            "content": content,
        })

    def recent_history_text(self, max_turns=6):
        """Compact plain-text rendering of the last N turns for the system prompt."""
        recent = self.chat_history[-max_turns:]
        lines = []
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Assistant"
            content = str(entry.get("content", ""))
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ── datasets ────────────────────────────────────────────────────────
    def add_dataset(self, df, sql, description=""):
        self._dataset_counter += 1
        ref = f"dataset_{self._dataset_counter}"
        self.datasets[ref] = {
            "df": df,
            "sql": sql,
            "description": description,
            "columns": list(df.columns) if df is not None else [],
            "row_count": int(len(df)) if df is not None else 0,
        }
        return ref

    def get_dataset(self, ref):
        return self.datasets.get(ref)

    def datasets_summary(self):
        """One line per existing dataset for the system prompt (multi-turn context)."""
        if not self.datasets:
            return ""
        lines = []
        for ref, d in self.datasets.items():
            cols = ", ".join(str(c) for c in d.get("columns", [])[:12])
            desc = d.get("description") or ""
            lines.append(f"- {ref}: {d.get('row_count', 0)} rows; columns: {cols}"
                         + (f"; {desc}" if desc else ""))
        return "\n".join(lines)
