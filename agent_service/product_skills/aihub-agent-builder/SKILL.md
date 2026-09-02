---
name: aihub-agent-builder
description: Use when a user wants to create, configure, share or delete one
  of AI Hub's General Agents (the chat agents on the Assistants screen) —
  "create an agent named X", "give agent X the web search tool", "restrict
  agent X to invoices", "add this PDF to agent X", "share agent X with the
  Analysts group" — the Agent Builder page as conversation.
---

# Agent Builder — General Agents by conversation

A General Agent is a classic chat agent: a name, an objective (its system
prompt), a checklist of CORE tools (platform-provided) and CUSTOM tools
(installed per tenant), an optional document-TYPE allow list for its document
tools, its own KNOWLEDGE documents, and the user GROUPS it is shared with.
The tools here call the same platform routes the Agent Builder page calls.

These are NOT automations or code flows (deterministic scripts) and NOT data
agents (SQL-bound assistants configured on the Data Assistants page — the
tools refuse to edit those).

## Procedure

1. `list_agents` — resolve the name to an id; see what exists. Developers and
   admins see every agent; regular users only agents shared with their groups.
2. `get_agent_config(agent)` — read the CURRENT configuration before changing
   anything, so you can say exactly what will change.
3. Create: `create_general_agent(name, objective?, core_tools?, custom_tools?,
   allowed_document_types?)`. A NAME ALONE IS ENOUGH — create immediately with
   the platform's default objective, report the objective you used, and offer
   to refine it / add tools. Duplicate names are refused until the user
   confirms (allow_duplicate_name=true).
4. Tools: `get_agent_builder_options` (section=tools) for EXACT names, then
   `set_agent_tools(agent, core_tools, custom_tools, mode=add|remove|replace)`.
5. Document access: `set_agent_document_types(agent, types, mode)` restricts
   the agent's document tools to those TYPES (empty list = unrestricted);
   `add_agent_knowledge(agent, path)` gives it ONE specific file;
   `delete_agent_knowledge(knowledge_id)` removes one.
6. Sharing (admin): `assign_agent_groups(agent, group_ids)` — ask which groups;
   the full list replaces; empty = developers/admins only.
7. Rename / objective / enable / disable: `update_general_agent`.
8. Delete: `delete_general_agent` — two steps; confirmed=true only after the
   user says yes.

## Gotchas (honesty)

- The platform AUTO-ADDS mandatory tools (e.g. the date/time and wait tools)
  and each tool's required dependencies. The final tool set is what the
  read-back shows — relay THAT, never the list you requested.
- Saves are replace-all under the hood; the tools re-post the full current
  configuration for partial edits, so nothing else changes. If a tool says
  UNVERIFIED, say so — do not claim the change landed.
- Tool and document-type names are exact and case-sensitive. A typo returns
  "Nothing saved" with suggestions; fix the name, don't retry blindly.
- Knowledge uploads run the document pipeline synchronously; big PDFs take
  minutes, and a BUSY reply means a queue (retry once after the suggested
  wait), not a failure and not a missing file.
- A brand-new agent is invisible to regular users until an admin shares it
  with a group. Say this when you create one.
- Developer role is required to build; admin role to share with groups.
  If the tool refuses on role, say who can do it and where (Agent Builder /
  Permissions pages) instead of working around it.
