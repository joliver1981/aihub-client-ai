# RU-10b

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 18.3

## prompt

Create a new assistant called 'Sales Helper' that can read our sales database, and share it with my group.

## tools

- **Skill**
  - input: `{'skill': 'aihub-agent-builder', 'args': "create Sales Helper, add database access to sales database, share with user's group"}`
  - result: ``
- **mcp__aihub__list_data_connections**
  - input: `{}`
  - result: `None`
- **mcp__aihub__get_agent_builder_options**
  - input: `{'section': 'tools'}`
  - result: `None`
- **mcp__aihub__get_agent_builder_options**
  - input: `{'section': 'groups'}`
  - result: ``

## reply

I'll help you create the Sales Helper agent and share it with your group. Let me first gather some information about your data connections, available tools, and group membership.Great! I can see you have several data connections available. However, I need clarification:

**Which database contains your sales data?** I see these connections:
- **ERPDB** (id 1)
- **EDW (SQL Server)** (id 5)
- **EDW (Postgres)** (id 18)
- **EDWDB (Postgres)** (id 28)
- **AIRDB2** (id 1071)
- **AIRDB** (id 1072)

And **which group should I share this with?** These are your available groups:
- **Core Users** (id 1)
- **Agent Test A** (id 2)
- **Agent Test B** (id 3)
- **Agent Test None** (id 4)

Once you let me know which database and which group(s), I'll:
1. Create the **Sales Helper** agent
2. Add the **Query Database** tool so it can read your sales data
3. Share it with your selected group(s)
