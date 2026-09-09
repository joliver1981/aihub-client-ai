# RU-10c

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 7.4

## prompt

Show me how the assistant 'Test Agent Bravo' is configured -- its tools, objective and document access.

## tools

- **mcp__aihub__get_agent_config**
  - input: `{'agent': 'Test Agent Bravo'}`
  - result (FAILED): `Agent 10024 is not shared with any of your groups.`

## reply

I checked for the assistant **Test Agent Bravo**, but it's not shared with any of your groups, so you don't have access to view its configuration.

Here's what that means:
- The agent exists on the platform, but only developers/admins or members of specific groups can see it.
- To get access, an admin would need to share it with a group you're in (via the **Permissions** tab on the Agent Builder page).

**If you think you should have access**, reach out to a developer or admin on your platform — they can share it with your group and you'll then be able to see its tools, objective, and document access settings.

Is there a different agent you'd like to view, or would you like help creating a new one?
