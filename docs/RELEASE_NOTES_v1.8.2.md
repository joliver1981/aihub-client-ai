# AI Hub 1.8.2

A reliability release focused on speed, accuracy and smoother agent conversations.

## Highlights

**Excel exports are much faster.** A 1,000-row export now completes in about 2–3 minutes,
down from roughly 25. Larger exports benefit the most.

**Command Center understands your environment.** It now answers questions about your
connections, tables, columns, agents, workflows and schedules directly from your platform's
own configuration — so "what tables are in our ERP connection?" returns the real answer.

**Smarter agent selection.** Naming a connection routes you to the agent that actually queries
it, and referring to an agent by ID always takes precedence.

**Better conversations.** When an agent needs something it cannot find, it now tells you what
it is waiting on and offers clear options — proceed anyway, point it at the detail, try a
different approach, or stop — instead of repeating itself.

**The Builder knows your data.** When planning automations and workflows, it can now see your
documented tables and columns, so it builds against real schema instead of asking you for
details the platform already has. Generated queries are validated before they are saved.

## Improvements

- **Resilience to AI provider changes.** Model providers frequently change their API
  parameters; AI Hub now adapts automatically instead of failing.
- **Workflow validation accuracy.** Valid workflows are no longer flagged with configuration
  warnings, and a conditional step following its "false" path is reported correctly.
- **Attachments and artifacts** are preserved reliably when work is handed between agents.
- **SFTP uploads** are now built correctly into visual workflows using the File Transfer step.
- **Safer destructive requests.** Ambiguous instructions such as "delete it" prompt for
  confirmation rather than acting.
- **Model routing** corrected so requests reach the configured model.

## Installation

A new installer brings updated Command Center defaults to existing installations during
upgrade, while leaving your customised settings untouched.

## Demo and enablement

New Demo Control Panel with one-click environment checks, resets and guided playbooks, plus a
two-factor vendor-portal fixture for demonstrating browser automation.

## Quality

This release ships with substantially expanded automated testing, including new suites covering
workflow execution accuracy, scheduling, authorisation, and multi-turn agent conversations.
All suites passed with no regressions.

## Known issues

- Empty database values may appear as the text `None` in exported spreadsheets.
- Occasionally a build may not complete and leave an empty automation, which can be deleted.
- Two sample MCP server entries ship enabled but are not reachable; remove them if unused.
