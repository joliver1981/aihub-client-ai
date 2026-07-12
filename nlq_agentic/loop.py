"""OpenAI tool-calling loop for the agentic NLQ engine (plan §4/§6).

A plain openai-SDK loop (not LangChain) so we keep deterministic control of the
iteration cap, wall-clock budget, tracing, and the finalize contract. The wire
protocol is the one GeneralAgent already runs in production. Errors from tools
(including rejected/failed SQL) come back as tool messages, so the model
self-repairs within the loop rather than via a separate correction chain.

run_loop is client-agnostic: tests inject a fake client exposing
`chat.completions.create(...)`.
"""
import json
import logging
import time

from .tools import TERMINAL_TOOLS, execute_tool

logger = logging.getLogger("nlq_agentic.loop")


class LoopResult:
    __slots__ = ("terminal", "timed_out", "error", "iterations")

    def __init__(self, terminal=None, timed_out=False, error=None, iterations=0):
        # terminal: {"answer_kind", "text", "dataset_ref"} or None if unresolved
        self.terminal = terminal
        self.timed_out = timed_out
        self.error = error
        self.iterations = iterations

    def __repr__(self):
        return (f"LoopResult(terminal={self.terminal}, timed_out={self.timed_out}, "
                f"error={self.error!r}, iterations={self.iterations})")


def _assistant_msg_with_tool_calls(message):
    """Rebuild the assistant message dict (with tool_calls) to append to history."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ],
    }


def _parse_args(raw):
    try:
        return json.loads(raw) if raw else {}, None
    except Exception as e:
        return None, str(e)


def run_loop(client, base_kwargs, system_prompt, user_message, tool_schemas, ctx,
             trace=None, max_iterations=8, deadline=None):
    """Drive the tool loop until respond/ask_user, iteration cap, or deadline.

    Returns a LoopResult. A model that answers in plain content without calling
    respond is treated as a text answer (graceful). A model that calls a terminal
    tool has its args returned as the terminal dict.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for i in range(1, max_iterations + 1):
        if trace is not None:
            trace.iterations = i

        if deadline is not None and time.time() > deadline:
            logger.warning("[loop] wall-clock budget exceeded before iteration %d", i)
            return LoopResult(timed_out=True, iterations=i - 1)

        create_kwargs = dict(base_kwargs)
        create_kwargs["messages"] = messages
        create_kwargs["tools"] = tool_schemas
        create_kwargs["tool_choice"] = "auto"
        if deadline is not None:
            create_kwargs["timeout"] = max(1.0, deadline - time.time())

        try:
            response = client.chat.completions.create(**create_kwargs)
        except Exception as e:
            logger.error(f"[loop] completion call failed on iteration {i}: {e}")
            return LoopResult(error=str(e), iterations=i)

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        # No tool call → the model answered directly. Treat as a text answer.
        if not tool_calls:
            text = (message.content or "").strip()
            return LoopResult(
                terminal={"tool": "respond", "answer_kind": "text", "text": text, "dataset_ref": None},
                iterations=i,
            )

        messages.append(_assistant_msg_with_tool_calls(message))

        # Process tool calls in order; a terminal tool ends the loop.
        for tc in tool_calls:
            name = tc.function.name
            args, parse_err = _parse_args(tc.function.arguments)

            if name in TERMINAL_TOOLS:
                if parse_err is not None:
                    # Malformed terminal args — feed back and let it retry.
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": f"Could not parse arguments: {parse_err}. Resend valid JSON."})
                    if trace is not None:
                        trace.record_tool(name, 0.0, ok=False, error=parse_err)
                    continue
                if trace is not None:
                    trace.record_tool(name, 0.0, ok=True, args_digest=json.dumps(args, default=str)[:200])
                return LoopResult(terminal=_normalize_terminal(name, args), iterations=i)

            # Non-terminal tool.
            if parse_err is not None:
                result = f"Could not parse arguments for {name}: {parse_err}. Resend valid JSON."
                if trace is not None:
                    trace.record_tool(name, 0.0, ok=False, error=parse_err)
            else:
                result = execute_tool(ctx, name, args, trace=trace)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    logger.warning("[loop] hit max_iterations=%d without a final answer", max_iterations)
    return LoopResult(terminal=None, iterations=max_iterations)


def _normalize_terminal(tool_name, args):
    if tool_name == "ask_user":
        return {
            "tool": "ask_user",
            "answer_kind": "text",
            "text": args.get("question", "") or "",
            "dataset_ref": None,
        }
    kind = str(args.get("answer_kind", "text")).lower()
    if kind not in ("text", "table", "chart"):
        kind = "text"
    return {
        "tool": "respond",
        "answer_kind": kind,
        "text": args.get("text", "") or "",
        "dataset_ref": args.get("dataset_ref"),
    }
