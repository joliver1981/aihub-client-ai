# Agent Email Inbox

The Agent Email Inbox displays all emails received by an AI agent's dedicated email address. This page allows you to view, read, and reply to emails that have been sent to the agent.

## Overview

When email is enabled for an agent, it receives a unique email address. People can send emails to this address, and the agent can:
- View received emails in this inbox
- Automatically respond using AI
- Trigger workflows based on email content
- Allow users to manually reply

## Page Layout

### Header
- **Agent Name** and **Inbox** title
- **Email Address** - The agent's full email address
- **Statistics Badges**:
  - New count (unread emails)
  - Total count (all emails)
- **Action Buttons**:
  - Refresh (reload inbox)
  - Mark All Read
  - Settings (link to email config)

### Email List Panel (Left)
- **Filter Dropdown** - Filter by All, New Only, or With Attachments
- **Email List** - Scrollable list of received emails

### Email Detail Panel (Right)
- **Email Header** - Subject, From, To, Date
- **Email Body** - Full message content
- **Attachments Section** - If email has attachments
- **Reply Composer** - Write and send replies
- **Action Buttons** - Reply button

## Viewing Emails

### Email List
Each email in the list shows:
- **Avatar** - Initials of sender
- **Sender Name** - Who sent the email
- **Subject** - Email subject line
- **Time** - When received (relative time)
- **New Badge** - Blue badge if unread

### Unread Indicators
- Unread emails have a light blue background
- Sender name is bold for unread emails
- "New" badge appears on unread items

### Selecting an Email
1. Click on any email in the list
2. Selected email shows blue highlight and left border
3. Full email content loads in the detail panel

## Email Detail View

When an email is selected:

### Header Information
| Field | Description |
|-------|-------------|
| Subject | Email subject line |
| From | Sender name and email address |
| To | Recipient (agent's email address) |
| Date | Full date and time received |

### Email Body
- Full text content of the email
- Preserves formatting and line breaks
- Scrollable for long emails

### Attachments
If the email has attachments:
- Attachments section appears below the body
- Shows attachment count
- Each attachment displays:
  - File type icon
  - Filename
  - File size
- Click to download

## Replying to Emails

### Starting a Reply
1. Select an email from the list
2. Click the **Reply** button
3. Reply composer opens

### Writing a Reply
1. Type your message in the text area
2. The reply will be sent from the agent's email address
3. Subject automatically includes "RE:" prefix

### Sending the Reply
1. Click **Send Reply**
2. Email is sent to the original sender
3. Success message appears
4. Composer closes

### Canceling a Reply
- Click **Cancel** to close the composer
- Your draft will be discarded

## Filtering Emails

Use the filter dropdown to view specific emails:

| Filter | Shows |
|--------|-------|
| All | All emails in inbox |
| New Only | Only unread emails |
| With Attachments | Only emails that have attachments |

## Managing Emails

### Marking as Read
- **Individual**: Viewing an email marks it as read
- **All at Once**: Click "Mark All Read" button

### Refreshing the Inbox
- Click the refresh button (sync icon)
- Inbox automatically refreshes every 60 seconds

## Statistics

The header shows real-time counts:
- **New** - Number of unread emails
- **Total** - Total emails in inbox

## Email Retention

**Important:** Inbound emails are stored for 3 days. After this period, emails are automatically deleted. Configure workflows or auto-responses to process important emails before they expire.

## Empty States

### No Emails
When the inbox is empty:
- Information message displays
- May show storage notes

### No Selection
When no email is selected:
- "Select an email to view" message
- Click an email to see its content

### Loading Errors
If emails fail to load:
- Error message displays
- Retry button available
- Check network connection

## Best Practices

### Monitoring
- Check inbox regularly for new emails
- Process time-sensitive emails promptly
- Use filters to focus on unread items

### Replying
- Keep replies professional
- Consider enabling auto-response for faster handling
- Use workflows for repetitive tasks

### Organization
- Mark emails as read when processed
- Configure auto-responses for common queries
- Set up workflow triggers for automation

## Troubleshooting

### Emails Not Loading
- Check internet connection
- Verify agent email is configured
- Click refresh button
- Check browser console for errors

### Cannot Reply
- Ensure email is selected
- Check agent has send permissions
- Verify email configuration is active

### Attachments Not Downloading
- Check browser download settings
- Verify attachment exists
- Try refreshing the email

### Old Emails Missing
- Emails are deleted after 3 days
- This is by design for storage management
- Configure workflows to archive important data

## Related Pages

- **Email Configuration** - Configure email settings
- **Assistants** - Use agents
- **Workflows** - Automate email processing
- **Approvals** - Review pending auto-responses
