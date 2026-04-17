"""
Builder Data — Graph Nodes
==============================
Each function is a LangGraph node that reads/writes DataAgentState.
"""

import json
import logging
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Set during graph creation
_connection_bridge = None
_pipeline_executor = None
_llm = None
_llm_mini = None


def init_nodes(connection_bridge, llm, llm_mini):
    global _connection_bridge, _llm, _llm_mini
    _connection_bridge = connection_bridge
    _llm = llm
    _llm_mini = llm_mini


# ─── classify_intent ─────────────────────────────────────────────────────

async def classify_intent(state: dict) -> dict:
    """Classify the user's intent using the mini LLM."""
    from builder_data_config import INTENT_CLASSIFICATION_PROMPT

    messages = state.get("messages", [])
    if not messages:
        return {"intent": "chat"}

    last_message = messages[-1]
    user_text = last_message.content if hasattr(last_message, "content") else str(last_message)

    has_pipeline = state.get("current_pipeline") is not None
    has_result = state.get("pipeline_result") is not None

    prompt = INTENT_CLASSIFICATION_PROMPT.format(
        has_pending_pipeline=has_pipeline and not has_result,
        has_pipeline_result=has_result,
    )

    response = await _llm_mini.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=user_text),
    ])

    intent = response.content.strip().strip('"').lower()

    # Validate intent
    valid_intents = {"pipeline", "quality", "chat", "confirm_yes", "confirm_no"}
    if intent not in valid_intents:
        intent = "chat"

    logger.info(f"Classified intent: '{intent}' for message: '{user_text[:60]}...'")
    return {"intent": intent}


# ─── converse ─────────────────────────────────────────────────────────────

async def converse(state: dict) -> dict:
    """General data Q&A with tool access."""
    from builder_data_config import DATA_AGENT_SYSTEM_PROMPT
    from ai.tools import get_data_tools

    messages = state.get("messages", [])
    tools = get_data_tools()

    # Build messages for the LLM
    llm_messages = [SystemMessage(content=DATA_AGENT_SYSTEM_PROMPT)]

    # Add connection context if available
    connections = state.get("available_connections")
    if connections:
        conn_summary = "\n".join([
            f"- {c.get('name', 'Unknown')} (ID: {c.get('id', '?')})"
            for c in connections
        ])
        llm_messages.append(SystemMessage(
            content=f"Available connections:\n{conn_summary}"
        ))

    llm_messages.extend(messages)

    # Bind tools and invoke
    llm_with_tools = _llm.bind_tools(tools)
    response = await llm_with_tools.ainvoke(llm_messages)

    # Handle tool calls if any
    if hasattr(response, "tool_calls") and response.tool_calls:
        from langchain_core.messages import ToolMessage

        tool_map = {t.name: t for t in tools}
        tool_results = []

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_fn = tool_map.get(tool_name)

            if tool_fn:
                try:
                    result = await tool_fn.ainvoke(tool_args)
                    tool_results.append(ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call["id"],
                    ))
                except Exception as e:
                    tool_results.append(ToolMessage(
                        content=f"Error: {e}",
                        tool_call_id=tool_call["id"],
                    ))

        # Get final response with tool results
        final_messages = llm_messages + [response] + tool_results
        final_response = await _llm.ainvoke(final_messages)
        return {"messages": [response] + tool_results + [final_response]}

    return {"messages": [response]}


# ─── design_pipeline ─────────────────────────────────────────────────────

async def design_pipeline(state: dict) -> dict:
    """Design a data pipeline from the user's natural language description."""
    from builder_data_config import PIPELINE_DESIGN_PROMPT, DATA_AGENT_SYSTEM_PROMPT

    messages = state.get("messages", [])

    # Fetch connections for context
    connections = []
    schemas = ""
    if _connection_bridge:
        try:
            connections = await _connection_bridge.list_connections()
        except Exception as e:
            logger.warning(f"Could not fetch connections: {e}")

    connections_text = "None available"
    if connections:
        connections_text = "\n".join([
            f"- ID: {c.get('id', c.get('connection_id', '?'))}, "
            f"Name: {c.get('name', c.get('connection_name', 'Unknown'))}, "
            f"Type: {c.get('type', c.get('database_type', ''))}"
            for c in connections
        ])

    # First: Generate pipeline JSON
    design_prompt = PIPELINE_DESIGN_PROMPT.format(
        connections=connections_text,
        schemas=schemas or "Not available — use connection IDs from the list above.",
    )

    llm_no_stream = _llm  # Use main model for design
    design_messages = [
        SystemMessage(content=design_prompt),
    ] + messages

    design_response = await llm_no_stream.ainvoke(design_messages)

    # Try to parse the pipeline JSON
    pipeline_dict = None
    try:
        content = design_response.content.strip()
        # Extract JSON from markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        pipeline_dict = json.loads(content)
    except (json.JSONDecodeError, IndexError) as e:
        logger.warning(f"Could not parse pipeline JSON: {e}")

    if pipeline_dict:
        # Generate a user-friendly summary
        summary_lines = [f"I've designed a pipeline: **{pipeline_dict.get('name', 'Data Pipeline')}**\n"]
        steps = pipeline_dict.get("steps", [])
        for i, step in enumerate(steps, 1):
            summary_lines.append(
                f"{i}. **{step.get('name', 'Step')}** ({step.get('step_type', '?')}): "
                f"{step.get('description', '')}"
            )
        summary_lines.append(f"\nShall I execute this pipeline?")
        summary = "\n".join(summary_lines)

        return {
            "messages": [AIMessage(content=summary)],
            "current_pipeline": pipeline_dict,
            "pipeline_result": None,  # Clear any previous result
            "available_connections": connections,
        }
    else:
        # Couldn't design a pipeline — fall back to conversation
        return {
            "messages": [AIMessage(
                content="I wasn't able to design a pipeline from that description. "
                        "Could you provide more details about:\n"
                        "- Which data sources to use (connection names/IDs)\n"
                        "- What transformations to apply\n"
                        "- Where the data should go"
            )],
        }


# ─── execute_pipeline ────────────────────────────────────────────────────

async def execute_pipeline(state: dict) -> dict:
    """Execute a confirmed pipeline."""
    from pipeline.models import PipelineDefinition
    from pipeline.engine import PipelineEngine

    pipeline_dict = state.get("current_pipeline")
    if not pipeline_dict:
        return {
            "messages": [AIMessage(content="No pipeline to execute. Please describe what you'd like to build.")],
        }

    try:
        pipeline = PipelineDefinition.from_dict(pipeline_dict)
        engine = PipelineEngine(_connection_bridge)

        result = await engine.execute(pipeline)

        return {
            "messages": [AIMessage(content=f"Pipeline execution complete. Status: **{result.status}**")],
            "pipeline_result": result.to_dict(),
        }
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        return {
            "messages": [AIMessage(content=f"Pipeline execution failed: {e}")],
            "pipeline_result": {"status": "failed", "error": str(e)},
        }


# ─── analyze_quality ─────────────────────────────────────────────────────

async def analyze_quality(state: dict) -> dict:
    """Analyze data quality based on the user's request."""
    from ai.prompts import QUALITY_ANALYSIS_PROMPT

    messages = state.get("messages", [])
    last_message = messages[-1].content if messages else ""

    # Fetch connections
    connections = []
    if _connection_bridge:
        try:
            connections = await _connection_bridge.list_connections()
        except Exception:
            pass

    connections_text = "\n".join([
        f"- ID: {c.get('id', '?')}, Name: {c.get('name', 'Unknown')}"
        for c in connections
    ]) if connections else "No connections available"

    prompt = QUALITY_ANALYSIS_PROMPT.format(
        connections=connections_text,
        user_request=last_message,
    )

    response = await _llm.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=last_message),
    ])

    # Try to parse and execute the quality operation
    try:
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        operation = json.loads(content)
        result = await _execute_quality_operation(operation)

        return {
            "messages": [AIMessage(content="Quality analysis complete.")],
            "quality_report": result,
            "available_connections": connections,
        }
    except Exception as e:
        logger.warning(f"Could not execute quality operation: {e}")
        # Fall back to conversational response
        return {
            "messages": [response],
            "available_connections": connections,
        }


async def _execute_quality_operation(operation: dict) -> dict:
    """Execute a parsed quality operation."""
    from quality.comparator import DataComparator
    from quality.deduplicator import Deduplicator, DeduplicationStrategy
    from quality.cleanser import DataCleanser, CleanseRule
    from quality.report import QualityReport

    op_type = operation.get("operation", "")
    params = operation.get("params", {})

    if op_type == "profile":
        conn_id = params["connection_id"]
        query = params.get("query", params.get("table_name", ""))
        if not query.upper().startswith("SELECT"):
            query = f"SELECT * FROM {query}"

        conn_str, _, _ = await _connection_bridge.get_connection_string(conn_id)
        df, error = _connection_bridge.execute_query_sync(query, conn_str)
        if error:
            return {"error": error}

        comparator = DataComparator()
        profile = comparator.profile(df)
        report_gen = QualityReport()
        report = report_gen.generate(df, profile=profile)
        return report.to_dict()

    elif op_type == "compare":
        src_a = params["source_a"]
        src_b = params["source_b"]

        conn_str_a, _, _ = await _connection_bridge.get_connection_string(src_a["connection_id"])
        df_a, err_a = _connection_bridge.execute_query_sync(src_a["query"], conn_str_a)
        if err_a:
            return {"error": f"Source A: {err_a}"}

        conn_str_b, _, _ = await _connection_bridge.get_connection_string(src_b["connection_id"])
        df_b, err_b = _connection_bridge.execute_query_sync(src_b["query"], conn_str_b)
        if err_b:
            return {"error": f"Source B: {err_b}"}

        comparator = DataComparator()
        result = comparator.compare(
            df_a, df_b,
            key_columns=params["key_columns"],
            compare_columns=params.get("compare_columns"),
        )
        return result.to_dict()

    elif op_type == "deduplicate":
        conn_id = params["connection_id"]
        query = params.get("query", "")
        conn_str, _, _ = await _connection_bridge.get_connection_string(conn_id)
        df, error = _connection_bridge.execute_query_sync(query, conn_str)
        if error:
            return {"error": error}

        deduplicator = Deduplicator()
        result = deduplicator.deduplicate(
            df,
            key_columns=params["key_columns"],
            strategy=DeduplicationStrategy(params.get("strategy", "exact")),
            fuzzy_threshold=params.get("fuzzy_threshold", 0.85),
        )
        return result.to_dict()

    else:
        return {"error": f"Unknown operation: {op_type}"}


# ─── present_results ─────────────────────────────────────────────────────

async def present_results(state: dict) -> dict:
    """Format pipeline/quality results into user-friendly markdown."""
    from ai.prompts import RESULTS_PRESENTATION_PROMPT

    messages = state.get("messages", [])
    pipeline_result = state.get("pipeline_result")
    quality_report = state.get("quality_report")

    results = pipeline_result or quality_report or {}
    user_request = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            user_request = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            user_request = msg.get("content", "")
            break

    prompt = RESULTS_PRESENTATION_PROMPT.format(
        results_json=json.dumps(results, indent=2, default=str),
        user_request=user_request,
    )

    response = await _llm.ainvoke([
        SystemMessage(content=prompt),
    ])

    return {"messages": [response]}


# ─── handle_rejection ────────────────────────────────────────────────────

async def handle_rejection(state: dict) -> dict:
    """Handle user rejecting a proposed pipeline."""
    return {
        "messages": [AIMessage(
            content="No problem. What would you like to change about the pipeline? "
                    "You can describe modifications or start fresh with a new description."
        )],
        "current_pipeline": None,
        "pipeline_result": None,
    }
