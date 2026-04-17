# User Management

The User Management page allows administrators to create, edit, and delete user accounts in the AI Hub platform. This is where you control who can access the system and what level of access they have.

## Access Requirements

This page is only accessible to users with **Admin** role (role level 3).

## Overview

User accounts are required for:
- Logging into the AI Hub platform
- Accessing AI agents and assistants
- Being assigned to security groups
- Tracking activity and audit trails

## Page Layout

### Header
- **User Management** title
- **Add User** button to create a new account

### User Accounts Table

| Column | Description |
|--------|-------------|
| ID | Unique user identifier |
| Name | User's full display name |
| Username | Login username |
| Email | User's email address |
| Phone | Contact phone number |
| Role | Access level badge (Admin, Developer, End User) |
| Actions | Edit and Delete buttons |

## User Roles

| Role | Level | Capabilities |
|------|-------|--------------|
| **End User** | 1 | Use assigned agents, view own data |
| **Developer** | 2 | Create/edit agents, tools, workflows |
| **Admin** | 3 | Full system access, user management, group security |

### Role Badges
- 🔴 **Admin** - Red badge
- 🔵 **Developer** - Blue badge
- 🟢 **End User** - Teal badge

## Adding a New User

1. Click **Add User** in the header
2. Fill in the user details modal:

| Field | Required | Description |
|-------|----------|-------------|
| Full Name | Yes | Display name shown in the UI |
| Username | Yes | Login credential (must be unique) |
| Email | Yes | User's email address |
| Phone | Yes | Contact phone number |
| Role | Yes | Select access level |
| Password | Yes (new) | Initial login password |

3. Click **Save User** to create the account

## Editing a User

1. Find the user in the table
2. Click the **Edit** button (pencil icon)
3. The modal opens with current values pre-filled
4. Modify any fields as needed
5. Leave **Password** blank to keep existing password
6. Click **Save User** to update

### What Can Be Changed
- Full Name
- Username (if not in use)
- Email
- Phone
- Role
- Password

## Deleting a User

1. Find the user in the table
2. Click the **Delete** button (trash icon)
3. Confirm the deletion when prompted

**Warning:** Deleting a user:
- Removes their access immediately
- Removes them from all groups
- Cannot be undone
- Does not delete their created content (agents, workflows)

## User Fields Reference

### Full Name
- The display name shown throughout the application
- Appears in group assignments
- Used in audit logs
- Example: "John Smith"

### Username
- Used for login
- Must be unique across the system
- Case-sensitive
- No spaces allowed
- Example: "jsmith"

### Email
- Used for notifications (if configured)
- Should be a valid email format
- Example: "jsmith@company.com"

### Phone
- Contact number
- Any format accepted
- Example: "(555) 123-4567"

### Role
Controls what the user can do:

**End User (Level 1)**
- Access assigned AI agents
- Use Data Assistants
- View dashboards
- Cannot create or modify agents

**Developer (Level 2)**
- All End User capabilities
- Create/edit AI agents
- Create/edit tools
- Build workflows
- Manage connections
- Edit data dictionaries

**Admin (Level 3)**
- All Developer capabilities
- User management (this page)
- Group security management
- System configuration
- Full platform access

### Password
- Required for new users
- Leave blank when editing to keep current password
- Should meet security requirements:
  - Minimum length recommended
  - Mix of characters recommended

## Best Practices

### Account Creation
- Use consistent username formats (e.g., first initial + last name)
- Always fill in email for password recovery
- Start users with minimum necessary role
- Document account creation for audit purposes

### Security
- Regularly review user accounts
- Remove accounts for departed employees immediately
- Audit admin accounts quarterly
- Use strong passwords

### Role Assignment
- Most users should be End Users
- Limit Developer access to those who need it
- Minimize Admin accounts (principle of least privilege)

## Troubleshooting

### Cannot Add User
- Verify username is unique
- Check all required fields are filled
- Ensure email format is valid

### User Cannot Login
- Verify account exists
- Reset password if needed
- Check role allows access to desired features
- Verify user is in appropriate groups

### Cannot Delete User
- Check if you have Admin role
- User may be the last admin (system protection)
- Try refreshing the page

### Changes Not Appearing
- Click Save to persist changes
- Check for error notifications
- Refresh the user table

## Related Pages

- **Groups** - Assign users to security groups
- **Custom Agent Builder** - Create AI agents (Developer+)
- **Assistants** - Use AI agents (all users)
