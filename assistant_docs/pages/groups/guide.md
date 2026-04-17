# Group Security

The Group Security page allows administrators to manage user groups, assign users to groups, and configure agent permissions for each group. This is the central hub for controlling access to AI agents within the platform.

## Access Requirements

This page is only accessible to users with **Admin** role (role level 3).

## Overview

Groups are used to:
- Organize users into logical collections (departments, teams, projects)
- Control which AI agents each group can access
- Simplify permission management across multiple users

## Page Layout

### Header
- **Group Security** title
- Displays management interface for the selected group

### Group Management Card
- **Select Group** dropdown to choose an existing group
- **Add Group** button to create a new group
- **Save Group** / **Cancel** buttons appear when adding a new group

### User Management Card (Left Side)
Two-panel user assignment interface:

| Panel | Description |
|-------|-------------|
| **Unassigned Users** | Users not in the selected group |
| **Assigned Users** | Users currently in the selected group |

Transfer buttons between panels:
- **→** (Right arrow) - Add selected users to group
- **←** (Left arrow) - Remove selected users from group

### Agent Permissions Card (Right Side)
- Checkbox list of all available AI agents
- Check/uncheck to grant/revoke agent access for the group
- Search filter to find specific agents

### Actions Card
- **Save Changes** - Persist all user assignments and permission changes
- **Delete Group** - Remove the group entirely

## Creating a New Group

1. Click **Add Group** button
2. The dropdown is replaced with a text input
3. Enter the new group name
4. Click **Save Group** to create
5. Or click **Cancel** to abort

## Assigning Users to a Group

1. Select a group from the dropdown
2. In the **Unassigned Users** panel:
   - Click on users to select them (highlighted in blue)
   - Use the search box to filter the list
3. Click the **→** button to move selected users to **Assigned Users**
4. Click **Save Changes** to persist

## Removing Users from a Group

1. Select the group
2. In the **Assigned Users** panel:
   - Click on users to select them
   - Use the search box to filter
3. Click the **←** button to move them to **Unassigned Users**
4. Click **Save Changes** to persist

## Managing Agent Permissions

1. Select a group from the dropdown
2. The **Agent Permissions** panel shows all available agents
3. Check the box next to agents the group should access
4. Uncheck to revoke access
5. Use the search filter to find specific agents
6. Click **Save Changes** to persist

### Permission Display
Each agent shows:
- Agent name
- Agent objective/description (truncated if long)
- Checkbox to toggle permission

## Deleting a Group

1. Select the group to delete
2. Click **Delete Group**
3. Confirm the deletion in the prompt

**Warning:** Deleting a group:
- Removes all user assignments
- Removes all agent permissions
- Cannot be undone

## Search Functionality

All three panels have search boxes:
- **Unassigned Users** - Filter by user name
- **Assigned Users** - Filter by user name  
- **Permissions** - Filter by agent name or objective

Click the **×** button to clear each search.

## User Roles Reference

| Role | Level | Description |
|------|-------|-------------|
| End User | 1 | Can use assigned agents only |
| Developer | 2 | Can create and modify agents |
| Admin | 3 | Full system access including user/group management |

## Best Practices

### Group Organization
- Create groups based on departments or functions
- Use descriptive names: "Sales Team", "IT Support", "Executive"
- Keep groups focused on specific use cases

### Permission Management
- Start with minimal permissions
- Add agents as needed
- Review permissions periodically
- Remove unused group memberships

### Security Considerations
- Admins should be in a separate admin-only group
- Don't give all agents to all groups
- Document why each group has specific permissions

## Troubleshooting

### Changes Not Saving
- Ensure you click **Save Changes** after modifications
- Check for success/error toast notification
- Verify you have admin permissions

### Users Not Appearing
- Check if users exist in the system (Users page)
- Refresh the page to reload user list
- Verify users aren't already assigned

### Permissions Not Working
- Confirm changes were saved
- User may need to log out and back in
- Check if agent itself is enabled

## Related Pages

- **Users** - Manage individual user accounts
- **Custom Agent Builder** - Create and configure AI agents
- **Assistants** - Use AI agents (end-user view)
