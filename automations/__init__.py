"""
On-the-fly Automations — persisted, versioned, AI-generated Python solutions.

An Automation = script + manifest + dedicated agent environment + connections
+ schedule + run history. The DB (Automations / AutomationRuns) is the
registry; the filesystem under automations/tenant_<id>/<automation_id>/ owns
the versioned code, mirroring the agent_environments layout.

See docs/on-the-fly-automations-plan.md.
"""
