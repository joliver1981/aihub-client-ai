# Builder Agent Detail Lookup Fix

## Problem
When users asked for details about a specific resource (e.g., "Show me details of agent X" or "Show me the node structure of workflow Y"), the builder agent could only provide summary information (ID, name, enabled status). Detailed fields like system_prompt, tool assignments, node structures, etc. were not available.

## Root Cause
The query flow worked like this:
1. User asks a query (e.g., "Show me details of agent Test Support Agent")
2. `nodes.py::query_and_respond()` calls `ContextGatherer.gather_context()`
3. ContextGatherer fetches ONLY summary listings (agents.list, connections.list, workflows.list)
4. These summaries contain only basic fields - NOT detailed information
5. The LLM gets these summaries in its system prompt and can only report what's there
6. Result: "objective not available in system resource listing"

## Solution
Enhanced the query flow to detect when a user is asking about a SPECIFIC resource and fetch detailed information for that resource.

## Changes Made

### 1. Added Detail Fetch Methods to ContextGatherer
**File**: `builder_service/context_gatherer.py`

Added three new methods:
- `fetch_agent_detail(agent_id)` → calls agents.get action
- `fetch_connection_detail(connection_id)` → calls connections.get action
- `fetch_workflow_detail(workflow_id)` → calls workflows.get action

These methods use the existing action definitions that were already in the system but not being used by the query flow.

### 2. Enhanced query_and_respond Function
**File**: `builder_service/graph/nodes.py`

After gathering the base context (resource lists), the function now:
1. Checks if the user's message mentions any specific resource by name
2. If a match is found, fetches the detailed info for that resource
3. Appends the detailed info to the dynamic_context_str in JSON format
4. The LLM can now see and use the detailed information

The detection logic:
- Loops through resources in the gathered context
- Checks if the resource name appears in the user's message (case-insensitive)
- On first match, fetches details and breaks (one detail fetch per query)

### 3. Fixed Test Data Issue
**File**: `builder_service/tests/test_context_gatherer.py`

- Fixed `test_fetch_agents_parses_response` to use correct API field names (`agent_id`, `agent_name`)
- Added 5 new tests for the detail fetch methods

## Action Definitions (Already Existed)
The following action definitions were already in place in `builder_agent/actions/platform_actions.py`:
- `agents.get` (line 271) → GET /get/agent_info?agent_id=X
- `connections.get` (line 1013) → GET /api/connections/&lt;connection_id&gt;
- `workflows.get` (line 568) → GET /get/workflow/&lt;workflow_id&gt;

These actions are now being utilized by the query flow.

## Test Results
All 77 tests in `test_context_gatherer.py` pass, including:
- 5 new tests for detail fetch methods
- All existing context gathering and resolution tests

## Example Usage
**Before Fix**:
```
User: Show me the system prompt for agent Test Support Agent
Builder: The agent exists (ID 1, enabled), but the objective/system prompt is not available in the system resource listing.
```

**After Fix**:
```
User: Show me the system prompt for agent Test Support Agent
Builder: Here is the system prompt for Test Support Agent:
"You are a helpful customer support assistant. Help users with their inquiries..."
```

## Files Modified
1. `builder_service/context_gatherer.py` - Added detail fetch methods
2. `builder_service/graph/nodes.py` - Enhanced query_and_respond
3. `builder_service/tests/test_context_gatherer.py` - Added tests and fixed existing test

## No Breaking Changes
- All existing functionality remains intact
- Only adds new capability when specific resources are mentioned
- Falls back gracefully if detail fetch fails
- No changes to action definitions or executor logic
