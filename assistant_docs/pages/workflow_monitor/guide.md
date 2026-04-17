# Workflow Monitor

The Workflow Monitor provides real-time visibility into workflow executions, performance metrics, and operational status.

## Purpose

Workflow Monitor helps you:
- Track active workflow executions
- Review historical run data
- Identify performance issues
- Debug failed workflows
- Monitor system health

## Page Layout

### Header Bar
- **Page Title**: Workflow Monitor
- **Designer Link**: Quick access to Workflow Designer

### Left Sidebar Navigation
Tabs for different views:
- **Dashboard**: Overview and metrics
- **Executions**: Individual run details
- **Schedules**: Upcoming scheduled runs
- **Alerts**: Error and warning notifications

### Main Content Area
Content changes based on selected tab:
- Summary cards
- Data tables
- Charts and graphs
- Detail panels

## Dashboard Tab

### Status Cards
Quick metrics overview:

| Card | Shows |
|------|-------|
| **Active** | Currently running workflows |
| **Completed Today** | Successful runs today |
| **Failed Today** | Failed runs today |
| **Pending Approval** | Awaiting human decision |

### Recent Activity
Timeline of recent events:
- Workflow starts
- Completions
- Failures
- Approvals

### Performance Chart
Visualizes:
- Executions over time
- Success vs failure rates
- Average duration trends

## Executions Tab

### Executions Table
List of all workflow runs:

| Column | Description |
|--------|-------------|
| **Workflow** | Workflow name |
| **Status** | Running, Completed, Failed, Pending |
| **Started** | Execution start time |
| **Duration** | How long it took/is taking |
| **Trigger** | What started it (Schedule, Manual, Webhook) |
| **Actions** | View details, Cancel, Retry |

### Filtering Executions
- **By Status**: Show only specific statuses
- **By Workflow**: Filter to specific workflow
- **By Date**: Date range picker
- **Search**: Find by ID or name

### Execution Details
Click an execution to see:
- Complete node execution log
- Input/output for each node
- Error messages (if failed)
- Duration breakdown
- Variables and data flow

## Execution Status

### Status Types

| Status | Icon | Meaning |
|--------|------|---------|
| **Running** | 🔵 | Currently executing |
| **Completed** | ✅ | Successfully finished |
| **Failed** | ❌ | Error occurred |
| **Pending Approval** | ⏳ | Waiting for human |
| **Cancelled** | 🚫 | Manually stopped |
| **Queued** | 📋 | Waiting to start |

### Status Transitions
```
Queued → Running → Completed
                → Failed
                → Pending Approval → Completed
                                  → Rejected
Running → Cancelled
```

## Node Execution Details

### Viewing Node History
For each execution, see every node:
- Node name and type
- Start/end timestamps
- Duration
- Input data received
- Output data produced
- Error details (if any)

### Node Status Indicators
- ✅ **Success**: Node completed normally
- ❌ **Failed**: Node encountered error
- ⏭️ **Skipped**: Node was bypassed (condition not met)
- 🔄 **Retried**: Node failed then succeeded on retry

## Schedules Tab

### Scheduled Workflows
View all scheduled workflows:
- Workflow name
- Schedule pattern
- Next run time
- Last run status
- Enable/disable toggle

### Upcoming Runs
Timeline showing:
- Next 24 hours of scheduled runs
- Which workflows will execute
- Expected trigger times

### Managing Schedules
- **Enable/Disable**: Toggle schedule on/off
- **Edit**: Modify schedule (opens Designer)
- **Run Now**: Execute immediately

## Alerts Tab

### Alert Types

| Type | Meaning |
|------|---------|
| **Error** | Workflow failed |
| **Warning** | Issue that may need attention |
| **Info** | Notable event occurred |

### Alert Information
Each alert shows:
- Workflow name
- Error/warning message
- Timestamp
- Affected node
- Stack trace (for errors)

### Alert Actions
- **Acknowledge**: Mark as seen
- **View Details**: Full error information
- **Retry**: Re-run failed workflow
- **Dismiss**: Remove from list

## Real-Time Updates

### Live Status
Monitor page updates automatically:
- Running workflow progress
- New executions appearing
- Status changes
- Completion notifications

### Auto-Refresh Settings
Configure update frequency:
- Every 5 seconds (default)
- Every 30 seconds
- Manual refresh only

## Analyzing Performance

### Duration Analysis
For each workflow:
- Average duration
- Minimum/maximum times
- Duration trend over time
- Slowest nodes

### Success Rate
Track reliability:
- Success percentage
- Common failure points
- Failure patterns

### Volume Metrics
Understand usage:
- Executions per day/week
- Peak usage times
- Trigger type distribution

## Troubleshooting Workflows

### Finding Failed Executions
1. Go to Executions tab
2. Filter by Status: "Failed"
3. Select time range
4. Click execution for details

### Understanding Failures
In execution details:
1. Find the failed node (❌)
2. Review input data
3. Read error message
4. Check stack trace

### Common Failure Causes

| Issue | Cause | Solution |
|-------|-------|----------|
| Connection timeout | External service slow | Increase timeout, add retry |
| Invalid data | Unexpected input format | Add validation, handle nulls |
| Permission denied | Credentials expired | Update credentials |
| Resource limit | Too much data processed | Add pagination, batching |

### Retrying Failed Workflows
1. Find failed execution
2. Click **Retry** action
3. Workflow re-runs from beginning
4. Or manually trigger from Designer

## Approval Queue

### Pending Approvals
View workflows waiting for human decision:
- Workflow name
- What's being approved
- Who can approve
- How long waiting
- Deadline (if set)

### Taking Action
As an approver:
1. Review the context provided
2. Click **Approve** or **Reject**
3. Add optional comment
4. Workflow continues based on decision

### Escalations
If approval not received in time:
- Auto-escalates to backup approver
- Or fails based on configuration

## Best Practices

### Regular Monitoring
- Check dashboard daily
- Review failed executions promptly
- Watch for patterns in failures

### Alert Management
- Don't ignore alerts
- Acknowledge after reviewing
- Fix underlying issues

### Performance Tracking
- Monitor duration trends
- Identify slow workflows
- Optimize bottlenecks

### Historical Analysis
- Review weekly summaries
- Track improvement over time
- Document lessons learned

## Common Tasks

### "Check if a workflow ran"
1. Go to Executions tab
2. Filter by workflow name
3. Check recent executions
4. Verify status and time

### "See why workflow failed"
1. Find failed execution
2. Click to view details
3. Find failed node
4. Read error message and data

### "Cancel a running workflow"
1. Go to Executions tab
2. Find running execution
3. Click **Cancel** action
4. Confirm cancellation

### "Check upcoming scheduled runs"
1. Go to Schedules tab
2. View upcoming runs section
3. See next 24 hours of runs

### "Approve a pending workflow"
1. Go to Dashboard or Approvals
2. Find pending approval
3. Review details
4. Click Approve or Reject

## Troubleshooting

### "Dashboard not updating"
- Check auto-refresh is enabled
- Try manual refresh button
- Verify network connection
- Clear browser cache

### "Can't find execution"
- Extend date range filter
- Check all status filters
- Try searching by ID
- Verify workflow name

### "Metrics seem wrong"
- Check date filter settings
- Verify timezone
- Allow time for data refresh
- Check for duplicate executions

### "Alerts not appearing"
- Check alert filter settings
- Verify you have permissions
- Check notification settings
- Alerts may be auto-dismissed
