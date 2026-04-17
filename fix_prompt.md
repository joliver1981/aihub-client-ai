# Fix Instructions for Builder Agent

I'm testing the AI Hub Builder Agent. Fix these issues:

## ISSUE 1: Raw JSON rendering regression (CRITICAL)

When the builder agent returns a response, the frontend sometimes shows raw JSON like:
`[{"type":"text","content":"The workflow..."}]`
instead of rendering the content as formatted markdown.

### ROOT CAUSE

In `builder_service/static/js/chat.js`, the `repairUnescapedQuotes()` function fails to repair unescaped double quotes inside JSON string values. When the LLM outputs content like:

```
[{"type":"text","content":"The workflow "Test Database Check" (ID 391) was identified..."}]
```

The inner quotes around `Test Database Check` break `JSON.parse()`. The repair function has this check:

```javascript
if (fixed[pos] === '"') {
```

But `JSON.parse` error positions point to the character AFTER the unescaped quote (e.g., `T` in `Test`), NOT to the quote itself. So the check `fixed[pos] === '"'` fails and repair gives up.

### FIX NEEDED in repairUnescapedQuotes()

1. When `fixed[pos]` is NOT a quote, also check `fixed[pos-1]` — if THAT is a quote preceded by a non-structural char, escape it and continue the loop.

2. The regex replace block that matches `/"content"\s*:\s*"((?:[^"\\]|\\.)*)"/g` is a complete no-op — it captures content but returns `match` unchanged. Either remove it or make it actually useful.

3. A more robust approach: Instead of relying solely on error positions, add a preprocessing step that scans for the specific pattern where a quote appears between word/space characters inside a JSON string value context, and escapes those quotes preemptively. For example, after the value-opening quote (after `"content":"`), walk forward character by character, and if you encounter a `"` that is followed by a word character and preceded by a word/space character (and it's not at a structural boundary like `","` or `"}` or `"]`), escape it.

### Test cases that must work after fix

Input: `[{"type":"text","content":"The workflow "Test Database Check" was found"}]`
Should parse correctly, extracting content: `The workflow "Test Database Check" was found`

Input: `[{"type":"text","content":"Error: 'NoneType' has no attribute 'start_workflow'"}]`
Should parse correctly (single quotes are fine, no repair needed).

Input: `[{"type":"text","content":"Found results for "lease" documents in "contracts" folder"}]`
Should parse correctly with multiple pairs of inner quotes.

## ISSUE 2: Workflow execute uses deprecated route

File: `builder_agent/actions/platform_actions.py`

The `workflows.execute` capability was pointing to `/api/workflow/run-legacy` which uses `workflow_engine` (None when `USE_WORKFLOW_EXECUTOR_SERVICE=true`, the default). This has already been changed to `/api/workflow/run`. Just verify the change is in place.

## ISSUE 3: Dark mode (informational only, no change needed)

Dark mode is the default. The light mode issue was stale localStorage. No code change needed.
