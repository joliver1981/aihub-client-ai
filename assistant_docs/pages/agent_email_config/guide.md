# Agent Email Configuration

The Agent Email Configuration page allows you to set up and manage email capabilities for an AI agent. Configure the agent's email address, inbound settings, auto-responses, workflow triggers, and safety limits.

## Overview

Email configuration enables agents to:
- Have a dedicated email address
- Receive inbound emails
- Automatically respond using AI
- Trigger workflows from emails
- Access inbox tools programmatically

## Page Layout

### Header
- **Email Configuration** title
- **Agent Name** with status badge (Active/Inactive)
- **View Inbox** button - Go to agent's inbox
- **Back** button - Return to previous page

### Main Configuration (Left - 67%)
- Email Address card
- Inbound Email Settings card

### Sidebar (Right - 33%)
- Safety & Limits card
- Actions card
- Information note

## Email Address Setup

### Email Prefix
- Enter a unique prefix for the agent's email
- Only lowercase letters, numbers, and hyphens allowed
- Maximum 50 characters
- Example: `sales-agent`, `support-bot`

### Display Name
- The name that appears as the sender
- Example: "Sales Assistant", "Customer Support"

### Full Email Preview
- Shows the complete email address
- Format: `prefix@yourdomain.com`
- Click copy button to copy to clipboard

### Email Enabled Toggle
- Master switch for email functionality
- When disabled, agent cannot send or receive emails

## Inbound Email Settings

### Receive Emails
Toggle to enable/disable receiving inbound emails.

When enabled:
- Emails sent to agent's address are stored
- Emails appear in the agent's inbox
- Triggers other configured actions

### AI Auto-Response
Automatically generate and send AI-powered responses.

| Setting | Description |
|---------|-------------|
| **Response Style** | Professional, Friendly, Formal, or Agent Default |
| **Special Instructions** | Custom instructions for responses |
| **Require Approval** | Human must approve before sending |

**Response Styles:**
- **Professional** - Business-appropriate tone
- **Friendly** - Warm, conversational tone
- **Formal** - Official, structured responses
- **Use Agent Default** - Uses agent's configured personality

### Trigger Workflow
Start a workflow automatically when emails are received.

| Setting | Description |
|---------|-------------|
| **Select Workflow** | Choose which workflow to trigger |
| **Filter Rules** | Optional conditions to filter which emails trigger |

**Filter Rules:**
Create conditions to control which emails trigger the workflow:
- **Field**: Subject, From, or Body
- **Operator**: Contains, Equals, or Starts with
- **Value**: Text to match

Example: Only trigger for emails where Subject contains "Order"

### Inbox Tools
Give the agent programmatic access to its inbox.

When enabled:
- Agent can check inbox via tools
- Agent can reply to emails programmatically
- Useful for conversational email handling

## Safety & Limits

### Max Auto-Responses Per Day
- Limit automatic responses to prevent spam
- Default: 50 per day
- Range: 1-500

### Cooldown Minutes
- Minimum time between responses to same sender
- Prevents rapid-fire responses
- Default: 15 minutes
- Range: 1-60 minutes

### Notifications

| Notification | Description |
|--------------|-------------|
| **Notify on new emails** | Alert when emails are received |
| **Notify on auto-replies** | Alert when auto-responses are sent |

## Actions

### Save Configuration
- Click to save all settings
- Validates required fields
- Shows success/error message

### Send Test Email
1. Click "Send Test Email"
2. Enter recipient email address
3. Click "Send"
4. Test email sent from agent's address

### Delete Configuration
- Removes email configuration
- Only appears for existing configurations
- Requires confirmation

## Status Indicators

### Status Badge
| Status | Meaning |
|--------|---------|
| **Active** (green) | Email is enabled and working |
| **Inactive** (red) | Email is disabled |

### Feature Toggles
- Each feature section has visual state
- Enabled sections have green background
- Options only show when feature is enabled

## Configuration Examples

### Basic Email Setup
1. Enter email prefix: `support`
2. Set display name: "Customer Support"
3. Enable email
4. Save configuration

### Auto-Response Setup
1. Enable "Receive Emails"
2. Enable "AI Auto-Response"
3. Select style: Professional
4. Add instructions: "Always thank the customer"
5. Enable "Require Approval" for safety
6. Save configuration

### Workflow Trigger Setup
1. Enable "Receive Emails"
2. Enable "Trigger Workflow"
3. Select target workflow
4. Add filter: Subject contains "Order"
5. Save configuration

## Best Practices

### Email Address
- Use descriptive, memorable prefixes
- Match the agent's purpose
- Keep it short and professional

### Auto-Response
- Start with approval required
- Test responses before removing approval
- Use specific instructions for better results
- Monitor response quality

### Safety
- Set reasonable daily limits
- Use cooldown to prevent spam
- Enable notifications initially
- Review auto-responses regularly

### Workflows
- Use filters to target specific emails
- Test workflow triggers thoroughly
- Monitor workflow execution
- Handle errors gracefully

## Troubleshooting

### Configuration Not Saving
- Check required fields (email prefix)
- Verify prefix format is valid
- Check for network errors

### Emails Not Being Received
- Verify "Receive Emails" is enabled
- Check "Email Enabled" is on
- Verify configuration is saved

### Auto-Responses Not Sending
- Check "AI Auto-Response" is enabled
- If approval required, check Approvals page
- Verify daily limit not exceeded
- Check cooldown period

### Workflow Not Triggering
- Verify "Trigger Workflow" is enabled
- Check workflow is selected
- Review filter rules
- Test with email that matches filters

### Test Email Not Received
- Check recipient address is correct
- Check spam/junk folder
- Verify configuration is saved first

## Related Pages

- **Agent Inbox** - View received emails
- **Workflows** - Create email-triggered workflows
- **Approvals** - Review pending auto-responses
- **Assistants** - Use agents
- **Agent Builder** - Configure agents
