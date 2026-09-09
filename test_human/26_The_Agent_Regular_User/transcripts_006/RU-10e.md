# RU-10e

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 16.2

## prompt

Create a new General Agent named Sales Helper with the objective 'Answer sales questions'. Share it with my group. Create it now, do not ask questions.

## tools

- **mcp__aihub__create_general_agent**
  - input: `{'name': 'Sales Helper', 'objective': 'Answer sales questions'}`
  - result (FAILED): `Creating an agent requires a Developer (or admin) role — this user can't do it through The Agent. An admin can do it on the Agent Builder page.`
- **mcp__aihub__get_agent_builder_options**
  - input: `{'section': 'groups'}`
  - result: `GROUPS (for assign_agent_groups): Agent Test A (id 2), Agent Test B (id 3), Agent Test None (id 4), Core Users (id 1)`

## reply

I cannot create the agent — your account is an End User role, and only Developers and admins can create General Agents through this chat interface. 

An admin needs to create it on the **Agent Builder page** in the AI Hub interface. Once created, I can then share it with your group (Core Users or another group of your choice).

Would you like to ask an admin to create the agent, or is there something else I can help with?
