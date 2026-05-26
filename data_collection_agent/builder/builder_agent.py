"""
SchemaBuilderAgent — conversational schema authoring.

Mirrors `WorkflowAgent.py`: phased conversation, dynamic system prompt rebuilt
on phase change, tools that mutate an in-progress schema dict held on the
agent instance.

Builder sessions are kept in-process by `builder_routes.py` (same pattern as
the workflow builder's session dict). Persistence to disk only happens when
the user clicks Save in the wizard.
"""

import json
import logging
import os
from datetime import datetime
from enum import Enum
from logging.handlers import WatchedFileHandler
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_classic.agents.format_scratchpad import format_to_tool_messages
from langchain_classic.agents.output_parsers import ToolsAgentOutputParser
from langchain_classic.agents import AgentExecutor

from CommonUtils import rotate_logs_on_startup, get_log_path

from ..actions import ActionRegistry
from .schema_validator import validate_schema


rotate_logs_on_startup(os.getenv('DCA_BUILDER_LOG', get_log_path('dca_builder_agent_log.txt')))

logger = logging.getLogger("SchemaBuilderAgent")
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'DEBUG'), logging.DEBUG)
logger.setLevel(log_level)
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_handler = WatchedFileHandler(
    filename=os.getenv('DCA_BUILDER_LOG', get_log_path('dca_builder_agent_log.txt')),
    encoding='utf-8',
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)


class BuilderPhase(Enum):
    DISCOVERY = "discovery"
    SECTIONS = "sections"
    FIELDS = "fields"
    LOOKUPS = "lookups"
    ACTIONS = "actions"
    REFINEMENT = "refinement"


def empty_schema() -> Dict:
    """Return a minimal new schema skeleton."""
    return {
        'id': '',
        'name': '',
        'version': '1.0',
        'description': '',
        'agent_guidelines': '',
        'sections': [],
        'lookup_data': {},
        'completion': {
            'confirmation_message': 'Your submission has been received.',
            'allow_save_draft': True,
            'actions': [],
        },
    }


class SchemaBuilderAgent:
    """
    Builder agent that drives a conversational schema-authoring experience.

    Each instance corresponds to one wizard session. Tools mutate self.schema
    (an in-memory dict). The frontend re-renders the form editor each turn
    from the agent's metadata.
    """

    def __init__(self, session_id: str, initial_schema: Optional[Dict] = None):
        self.session_id = session_id
        self.schema: Dict = initial_schema or empty_schema()
        # If we got a non-empty schema, start in REFINEMENT
        is_existing = bool(self.schema.get('sections'))
        self.phase = BuilderPhase.REFINEMENT if is_existing else BuilderPhase.DISCOVERY
        self.chat_history: List = []
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = self.created_at

        self._initialize_llm()
        self._set_system_prompt()
        self._register_tools()
        self._build_agent_executor()

        logger.info(
            f"SchemaBuilderAgent initialized (session={session_id}, "
            f"phase={self.phase.value}, existing={is_existing})"
        )

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
    def update_phase(self, new_phase: BuilderPhase):
        if new_phase == self.phase:
            return
        logger.info(f"Builder phase {self.phase.value} -> {new_phase.value}")
        self.phase = new_phase
        self._set_system_prompt()
        self._build_agent_executor()

    def _set_system_prompt(self):
        action_types = sorted(ActionRegistry.list_types() or [
            'email', 'workflow', 'api', 'webhook', 'agent'
        ])

        phase_guidance = {
            BuilderPhase.DISCOVERY: """
PHASE: DISCOVERY

Your job in this phase: understand the use case.
Ask the user what they want to collect, who fills it in, and what should happen
when it's complete. Once you have enough to propose a schema id and name, call
set_schema_metadata. Don't proceed to sections until you understand the basics.
""",
            BuilderPhase.SECTIONS: """
PHASE: SECTIONS

Break the data into 2-6 logical sections. Use add_section for each. Brief, plain titles.
After the sections feel right, transition naturally into fields by calling add_field
or by asking the user what should go in the first section.
""",
            BuilderPhase.FIELDS: """
PHASE: FIELDS

For each section, define its fields. Use add_field. Choose appropriate types
(text, number, date, boolean, select, multi_select, lookup, email, phone, textarea).
Mark fields required when the data is meaningless without them.

For select / lookup fields, decide whether the options are inline or come from
a lookup_data reference. If lookup_data is needed, call add_lookup.

Pay attention to validation: future_date for upcoming dates, min/max for numbers,
min_length/max_length for text. Add these in the field's validation block.

When a section's fields are complete, briefly review them with the user and move on.
""",
            BuilderPhase.LOOKUPS: """
PHASE: LOOKUPS

Help the user define reference data: lists of options, items, etc. Use add_lookup.
Inline values for short lists; file references for long ones.
""",
            BuilderPhase.ACTIONS: """
PHASE: ACTIONS

Determine what should happen when the user finishes filling out the form.

Available action types:
""" + '\n'.join(f"  - {t}" for t in action_types) + """

For each action the user wants:
- Use add_completion_action with the right config.
- Ask follow-up questions until you have everything required for that action type.

Most use cases want at least one action. Common patterns:
  - email: notify a team
  - workflow: kick off platform automation
  - api: integrate with an external system
  - webhook: send to Zapier / Make / a hosted automation
  - agent: hand off to another AI agent for review
""",
            BuilderPhase.REFINEMENT: """
PHASE: REFINEMENT

The schema already has structure. Help the user refine it.
Use update_section / update_field / delete_section / delete_field to make precise edits.
Validate frequently with validate_schema and surface any issues conversationally.
""",
        }

        schema_state = json.dumps(self.schema, default=str, indent=2)

        self.SYSTEM = f"""You are an AI assistant helping a non-technical user build a Data Collection Schema.

A schema is a JSON definition of a guided data collection experience: it has
sections, fields, lookup data, agent guidelines, and completion actions. Once
saved, end users will fill it out conversationally with another AI agent.

YOUR JOB:
- Drive the conversation through phases (DISCOVERY → SECTIONS → FIELDS → LOOKUPS → ACTIONS → REFINEMENT)
- Use your tools to mutate the schema as you go (don't just describe changes — make them)
- Ask focused questions; don't overwhelm
- Validate periodically and explain any issues plainly

CURRENT SCHEMA STATE:
{schema_state}

{phase_guidance[self.phase]}

GENERAL RULES:
1. Use tools to make changes. Saying "I've added X" without calling a tool is a hallucination.
2. Use ID values that are short, lowercase, and snake_case (e.g. 'event_basics', 'first_name').
3. Don't invent fields the user didn't ask for. If you think they're missing something, ask.
4. Keep the user's intent — adapt the structure to fit what they describe, not the other way around.
5. After substantive changes, call validate_schema and report issues briefly.
"""

    # ------------------------------------------------------------------
    def _register_tools(self):
        agent = self

        @tool
        def set_schema_metadata(
            id: Optional[str] = None,
            name: Optional[str] = None,
            description: Optional[str] = None,
            agent_guidelines: Optional[str] = None,
            version: Optional[str] = None,
        ) -> str:
            """Set top-level schema properties.

            Args:
                id: A snake_case identifier for the schema (used as the URL slug).
                name: Human-readable name shown in the header.
                description: Optional one-line description.
                agent_guidelines: Instructions for the runtime agent — tone, conversation style, constraints.
                version: Defaults to '1.0'.
            """
            if id is not None:
                agent.schema['id'] = id.strip()
            if name is not None:
                agent.schema['name'] = name.strip()
            if description is not None:
                agent.schema['description'] = description
            if agent_guidelines is not None:
                agent.schema['agent_guidelines'] = agent_guidelines
            if version is not None:
                agent.schema['version'] = version
            return f"Updated schema metadata. Current id='{agent.schema.get('id')}', name='{agent.schema.get('name')}'."

        @tool
        def add_section(section_id: str, title: str,
                        description: Optional[str] = None,
                        order: Optional[int] = None) -> str:
            """Append a new section to the schema.

            Args:
                section_id: snake_case id (must be unique within the schema).
                title: human-readable title shown in the UI.
                description: optional short blurb.
                order: optional integer (defaults to next available).
            """
            if any(s.get('id') == section_id for s in agent.schema.get('sections') or []):
                return f"ERROR: section_id '{section_id}' already exists."
            if order is None:
                order = len(agent.schema.get('sections') or []) + 1
            section = {
                'id': section_id,
                'title': title,
                'description': description or '',
                'order': order,
                'fields': [],
            }
            agent.schema.setdefault('sections', []).append(section)
            return f"Added section '{section_id}' (order {order})."

        @tool
        def update_section(section_id: str,
                           title: Optional[str] = None,
                           description: Optional[str] = None,
                           order: Optional[int] = None) -> str:
            """Update an existing section's metadata."""
            for section in agent.schema.get('sections') or []:
                if section.get('id') == section_id:
                    if title is not None:
                        section['title'] = title
                    if description is not None:
                        section['description'] = description
                    if order is not None:
                        section['order'] = order
                    return f"Updated section '{section_id}'."
            return f"ERROR: no section with id '{section_id}'."

        @tool
        def delete_section(section_id: str) -> str:
            """Remove a section (and its fields) from the schema."""
            sections = agent.schema.get('sections') or []
            new_sections = [s for s in sections if s.get('id') != section_id]
            if len(new_sections) == len(sections):
                return f"ERROR: no section with id '{section_id}'."
            agent.schema['sections'] = new_sections
            return f"Deleted section '{section_id}'."

        @tool
        def add_field(
            section_id: str,
            field_id: str,
            label: str,
            type: str,
            required: bool = False,
            prompt_hint: Optional[str] = None,
            validation_json: Optional[str] = None,
            options_json: Optional[str] = None,
            options_ref: Optional[str] = None,
            lookup_ref: Optional[str] = None,
            display_as: Optional[str] = None,
        ) -> str:
            """Add a field to a section.

            Args:
                section_id: section to add it to.
                field_id: snake_case id (unique within the section).
                label: user-facing label.
                type: one of text, textarea, number, date, boolean, select, multi_select, lookup, email, phone, file.
                required: whether the field must be filled.
                prompt_hint: optional natural-language hint for what the AI should ask.
                validation_json: JSON string of validation config, e.g. '{"rule":"future_date","min_days_ahead":7}'.
                options_json: JSON array string of inline options, e.g. '[{"id":"a","label":"A"}]' (for type=select).
                options_ref: name of a lookup_data entry to pull options from (alternative to options_json).
                lookup_ref: name of a lookup_data entry (for type=lookup).
                display_as: 'table'|'cards'|'list' (for lookup-type fields).
            """
            section = next((s for s in agent.schema.get('sections') or [] if s.get('id') == section_id), None)
            if not section:
                return f"ERROR: no section with id '{section_id}'."
            if any(f.get('id') == field_id for f in section.get('fields') or []):
                return f"ERROR: field '{field_id}' already exists in section '{section_id}'."

            field = {
                'id': field_id,
                'label': label,
                'type': type,
                'required': bool(required),
            }
            if prompt_hint:
                field['prompt_hint'] = prompt_hint
            if validation_json:
                try:
                    field['validation'] = json.loads(validation_json)
                except json.JSONDecodeError as e:
                    return f"ERROR: validation_json is not valid JSON: {e}"
            if options_json:
                try:
                    field['options'] = json.loads(options_json)
                except json.JSONDecodeError as e:
                    return f"ERROR: options_json is not valid JSON: {e}"
            if options_ref:
                field['options_ref'] = options_ref
            if lookup_ref:
                field['lookup_ref'] = lookup_ref
            if display_as:
                field['display_as'] = display_as

            section.setdefault('fields', []).append(field)
            return f"Added field '{field_id}' to section '{section_id}'."

        @tool
        def update_field(
            section_id: str,
            field_id: str,
            label: Optional[str] = None,
            type: Optional[str] = None,
            required: Optional[bool] = None,
            prompt_hint: Optional[str] = None,
            validation_json: Optional[str] = None,
            options_json: Optional[str] = None,
            options_ref: Optional[str] = None,
            lookup_ref: Optional[str] = None,
            display_as: Optional[str] = None,
        ) -> str:
            """Update an existing field's properties."""
            section = next((s for s in agent.schema.get('sections') or [] if s.get('id') == section_id), None)
            if not section:
                return f"ERROR: no section with id '{section_id}'."
            field = next((f for f in section.get('fields') or [] if f.get('id') == field_id), None)
            if not field:
                return f"ERROR: no field '{field_id}' in section '{section_id}'."
            if label is not None: field['label'] = label
            if type is not None: field['type'] = type
            if required is not None: field['required'] = bool(required)
            if prompt_hint is not None: field['prompt_hint'] = prompt_hint
            if validation_json is not None:
                try:
                    field['validation'] = json.loads(validation_json) if validation_json else None
                except json.JSONDecodeError as e:
                    return f"ERROR: validation_json is not valid JSON: {e}"
            if options_json is not None:
                try:
                    field['options'] = json.loads(options_json) if options_json else None
                except json.JSONDecodeError as e:
                    return f"ERROR: options_json is not valid JSON: {e}"
            if options_ref is not None:
                field['options_ref'] = options_ref or None
            if lookup_ref is not None:
                field['lookup_ref'] = lookup_ref or None
            if display_as is not None:
                field['display_as'] = display_as or None
            return f"Updated field '{field_id}' in section '{section_id}'."

        @tool
        def delete_field(section_id: str, field_id: str) -> str:
            """Remove a field from a section."""
            section = next((s for s in agent.schema.get('sections') or [] if s.get('id') == section_id), None)
            if not section:
                return f"ERROR: no section with id '{section_id}'."
            before = len(section.get('fields') or [])
            section['fields'] = [f for f in section.get('fields') or [] if f.get('id') != field_id]
            if len(section['fields']) == before:
                return f"ERROR: no field '{field_id}' in section '{section_id}'."
            return f"Deleted field '{field_id}' from section '{section_id}'."

        @tool
        def add_lookup(
            lookup_ref: str,
            source: str = 'inline',
            values_json: Optional[str] = None,
            file: Optional[str] = None,
        ) -> str:
            """Define a lookup data source.

            Args:
                lookup_ref: snake_case id; field options_ref / lookup_ref must match this.
                source: 'inline' (values listed here) or 'file' (loaded from configs/{file}).
                values_json: JSON array, e.g. '[{"id":"a","label":"A"}, {"id":"b","label":"B"}]'.
                file: filename within configs/ if source='file'.
            """
            lookup_data = agent.schema.setdefault('lookup_data', {})
            entry: Dict[str, Any] = {'source': source}
            if source == 'inline':
                if not values_json:
                    return "ERROR: source='inline' requires values_json."
                try:
                    entry['values'] = json.loads(values_json)
                except json.JSONDecodeError as e:
                    return f"ERROR: values_json is not valid JSON: {e}"
            elif source == 'file':
                if not file:
                    return "ERROR: source='file' requires a file name."
                entry['file'] = file
            else:
                return f"ERROR: unknown source '{source}' (must be 'inline' or 'file')."
            lookup_data[lookup_ref] = entry
            return f"Added lookup '{lookup_ref}' (source={source})."

        @tool
        def add_completion_action(action_type: str, config_json: str) -> str:
            """Append a completion action.

            Args:
                action_type: one of: email, workflow, api, webhook, agent.
                config_json: JSON string of the action's full config (including type-specific fields).
                             The 'type' key inside the JSON will be overridden with action_type.
            """
            try:
                config = json.loads(config_json)
            except json.JSONDecodeError as e:
                return f"ERROR: config_json is not valid JSON: {e}"
            if not isinstance(config, dict):
                return "ERROR: config_json must be a JSON object."
            config['type'] = action_type
            agent.schema.setdefault('completion', {}).setdefault('actions', []).append(config)
            return f"Added '{action_type}' completion action."

        @tool
        def delete_completion_action(index: int) -> str:
            """Remove the action at the given index (0-based)."""
            actions = (agent.schema.get('completion') or {}).get('actions') or []
            if index < 0 or index >= len(actions):
                return f"ERROR: index {index} out of range (have {len(actions)} actions)."
            removed = actions.pop(index)
            return f"Deleted action at index {index} (type={removed.get('type')})."

        @tool
        def set_completion_message(confirmation_message: str) -> str:
            """Set the success message shown after submission."""
            agent.schema.setdefault('completion', {})['confirmation_message'] = confirmation_message
            return "Updated confirmation_message."

        @tool
        def validate_schema_now() -> str:
            """Run validation against the current schema and return any errors/warnings."""
            result = validate_schema(agent.schema)
            return json.dumps(result, default=str, indent=2)

        @tool
        def preview_schema() -> str:
            """Return the current schema as formatted JSON (for user review)."""
            return json.dumps(agent.schema, default=str, indent=2)

        @tool
        def set_phase(phase: str) -> str:
            """Move to a specific builder phase.

            Args:
                phase: discovery | sections | fields | lookups | actions | refinement
            """
            try:
                agent.update_phase(BuilderPhase(phase))
                return f"Now in phase '{phase}'."
            except ValueError:
                return f"ERROR: unknown phase '{phase}'."

        self.tools = [
            set_schema_metadata, add_section, update_section, delete_section,
            add_field, update_field, delete_field,
            add_lookup,
            add_completion_action, delete_completion_action, set_completion_message,
            validate_schema_now, preview_schema, set_phase,
        ]

    # ------------------------------------------------------------------
    def _build_agent_executor(self):
        if not getattr(self, 'tools', None):
            self._register_tools()

        tools_formatted = [convert_to_openai_tool(t) for t in self.tools]
        safe_system = self.SYSTEM.replace('{', '{{').replace('}', '}}')

        prompt = ChatPromptTemplate.from_messages([
            ("system", safe_system),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        llm_with_tools = self.llm.bind(tools=tools_formatted)
        agent_runner = (
            {
                "input": lambda x: x["input"],
                "chat_history": lambda x: x.get("chat_history", []),
                "agent_scratchpad": lambda x: format_to_tool_messages(x["intermediate_steps"]),
            }
            | prompt
            | llm_with_tools
            | ToolsAgentOutputParser()
        )
        self.agent_executor = AgentExecutor(
            agent=agent_runner,
            tools=self.tools,
            verbose=False,
            handle_parsing_errors=True,
            max_iterations=10,
        )

    # ------------------------------------------------------------------
    def process_message(self, message: str) -> Tuple[str, Dict]:
        """Process a builder message; returns (response_text, metadata)."""
        try:
            result = self.agent_executor.invoke({
                "input": message,
                "chat_history": self.chat_history,
            })
            response = result.get("output", "") or ""
        except Exception as e:
            logger.error(f"Builder agent error: {e}", exc_info=True)
            response = f"Sorry — I hit an error: {e}"

        self.chat_history.extend([
            HumanMessage(content=message),
            AIMessage(content=response),
        ])
        self.updated_at = datetime.utcnow().isoformat()

        # Re-set the system prompt so the next turn sees the updated schema
        self._set_system_prompt()
        self._build_agent_executor()

        validation = validate_schema(self.schema)
        return response, {
            'phase': self.phase.value,
            'session_id': self.session_id,
            'schema': self.schema,
            'validation': validation,
        }
