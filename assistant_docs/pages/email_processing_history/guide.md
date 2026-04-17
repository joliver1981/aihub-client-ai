# Email Processing History

## Page Overview

This page displays the history of all emails received and processed by your AI agents. It provides visibility into email automation activity, helps troubleshoot issues, and tracks processing performance.

**Page URL:** `/email-processing-history`

## Email Dispatcher Status

The header shows the email dispatcher service status:

| Indicator | Meaning |
|-----------|---------|
| **Green pulsing dot** | Dispatcher is running and processing emails |
| **Red dot** | Dispatcher is stopped |

Click the **power button** to start or stop the dispatcher. When running, it displays the total number of emails processed in the current session.

> **Note:** If the dispatcher is stopped, no new emails will be processed until it's restarted.

## Statistics Dashboard

The top of the page shows four key metrics:

| Stat | Description |
|------|-------------|
| **Total Processed** | Total number of emails processed across all agents |
| **Completed** | Emails that were successfully processed |
| **Failed** | Emails that encountered errors during processing |
| **Avg Processing Time** | Average time to process each email |

## Agent Breakdown

When multiple agents have email processing enabled, a visual breakdown shows which agents are handling the most emails. Each bar displays completed (green) vs failed (red) processing for that agent. Only the top 5 agents by volume are shown.

## Filters

Use the filter bar to narrow down the history:

| Filter | Options |
|--------|---------|
| **Agent** | Filter by a specific agent |
| **Status** | Completed, Failed, Skipped, Pending, Pending Approval |
| **Type** | Auto Response, Workflow Trigger, Pending Approval, Skipped, Received Only |
| **Time Period** | Last 24 Hours, Last 7 Days, Last 30 Days |

Click **Refresh** to reload all data with current filters.

## History Table Columns

| Column | Description |
|--------|-------------|
| **Time** | Date and time the email was processed (shown in your local timezone) |
| **Agent** | The agent that processed the email (click to filter by agent) |
| **From** | Email sender's name and address |
| **Subject** | Email subject line |
| **Type** | How the email was processed |
| **Status** | Processing result |
| **Duration** | How long processing took (in milliseconds) |
| **Actions** | View workflow monitoring or error details |

## Processing Types

| Type | Description |
|------|-------------|
| **Auto Response** | AI generated and sent a reply |
| **Workflow Trigger** | Email triggered a workflow execution |
| **Pending Approval** | Response awaiting human approval before sending |
| **Skipped** | Email didn't match filter rules configured for the agent |
| **Received Only** | Email received but no action was configured |

## Status Values

| Status | Description |
|--------|-------------|
| **Completed** | Processing finished successfully |
| **Failed** | An error occurred (click the error icon to see details) |
| **Skipped** | Email filtered out by rules |
| **Pending** | Email is queued for processing |
| **Pending Approval** | Awaiting human review |

## Actions Column

| Icon | Action |
|------|--------|
| **Error Icon** (red triangle) | Click to view error message and optionally retry processing |
| **Workflow Icon** (blue) | Click to view workflow execution in the Monitoring page |

## Common Questions

### Why is an email showing as "Skipped"?
The email didn't match the filter rules configured in the agent's email settings. Check the agent's email configuration to review or adjust the filter rules.

### Why is an email showing as "Failed"?
Click the red error icon in the Actions column to see the specific error message. Common causes include:
- Agent API service not running
- Workflow execution errors
- Email sending failures

You can click **Retry Processing** in the error modal to attempt processing again.

### Why don't I see any emails?
- Verify the dispatcher is running (green pulsing dot in the header)
- Check that the agent has email processing enabled
- Confirm emails are being sent to the correct agent email address
- Adjust the time period filter to show older records

### What does "Received Only" mean?
The agent received the email but has neither workflow triggers nor auto-responses enabled. The email was logged but no action was taken.

### How do I see workflow execution details?
Click the blue workflow icon in the Actions column to navigate to the workflow monitoring page.

### Why are timestamps in the wrong timezone?
Timestamps are automatically converted from UTC to your browser's local timezone. If they appear incorrect, check your system's timezone settings.

### How do I monitor a specific agent?
Use the Agent dropdown filter, or click any agent name in the table to filter the view to that agent's history only.

## Tips

- The dispatcher status auto-refreshes every 30 seconds
- Agent names in the table are clickable links to filter by that agent
- Failed records can be retried directly from the error modal
- Use shorter time periods for faster loading when troubleshooting recent issues

## Related Pages

- **Agent Email Configuration**: Configure email settings for each agent (`/agent-email-config/{agent_id}`)
- **Agent Inbox**: View and manage emails in an agent's inbox (`/agent-inbox/{agent_id}`)
- **Workflow Monitoring**: View workflow execution details (`/monitoring`)
- **Approvals**: Review and approve pending email responses (`/approvals`)
