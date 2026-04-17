# Intelligent Jobs

The Jobs page allows you to configure automated tasks that run AI agents on a schedule.

## What is a Job?

A job is an automated task that:
- Runs an AI agent with specific instructions
- Executes on a defined schedule
- Requires no manual intervention
- Produces results or takes actions automatically

Jobs are ideal for:
- Recurring reports
- Data monitoring
- Scheduled notifications
- Automated data processing

## Page Layout

### Job Configuration Card

#### Job Selection
- **Job Name Dropdown**: Select existing job or create new
- Shows all configured jobs for your account

#### Agent Selection
- **Agent Dropdown**: Which AI agent runs this job
- Agent determines available capabilities

#### Agent Instructions
- **Text Area**: What to tell the agent
- The prompt that guides the agent's work

#### Job Controls
- **On/Off Toggle**: Enable or disable the job
- **Test Button**: Run job immediately for testing
- **Save Button**: Store configuration
- **Delete Button**: Remove the job

### Schedule Modal
Configure when the job runs:
- Frequency options
- Time settings
- Day selections

### Job History Modal
View past executions:
- Run timestamps
- Success/failure status
- Results and outputs

## Creating a Job

### Step 1: Create New Job
1. Click **New Job** button
2. Enter job name
3. Select an agent
4. Write instructions
5. Click **Save**

### Step 2: Configure Schedule
1. Click **Schedule Job**
2. Select job from dropdown
3. Choose frequency:
   - Hourly
   - Daily
   - Weekly
   - Monthly
   - Custom (cron)
4. Set specific time
5. Save schedule

### Step 3: Test the Job
1. Select the job
2. Click **Run** (Test)
3. Review output
4. Verify results are correct

### Step 4: Enable the Job
1. Toggle **On/Off** switch to On
2. Job will run at scheduled times
3. Monitor via Job History

## Writing Job Instructions

### Be Specific
Include all necessary context:

❌ Vague:
```
Check the data and send a report.
```

✅ Specific:
```
Query the orders table for orders placed yesterday.
Calculate total revenue, order count, and average order value.
Send an email summary to sales@company.com with subject "Daily Sales Report - [date]".
Include a breakdown by product category.
```

### Define Output Format
Specify how results should be formatted:

```
Format the results as:
1. Summary section with key metrics
2. Table of top 10 items
3. Any anomalies or concerns noted
```

### Include Error Handling
Tell the agent what to do if issues occur:

```
If no orders are found, send an email noting "No orders for [date]".
If database connection fails, retry once then send alert to admin@company.com.
```

## Schedule Options

### Frequency Settings

| Option | Description |
|--------|-------------|
| **Every X Minutes** | Run every 15, 30, 60 minutes |
| **Hourly** | Run at specific minute each hour |
| **Daily** | Run once per day at set time |
| **Weekly** | Run on specific days at set time |
| **Monthly** | Run on specific date each month |
| **Custom Cron** | Advanced scheduling expression |

### Cron Expression Examples

| Cron | Meaning |
|------|---------|
| `0 9 * * *` | Every day at 9:00 AM |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |
| `0 */2 * * *` | Every 2 hours |
| `0 9 1 * *` | First day of month at 9 AM |
| `0 9 * * 1` | Every Monday at 9:00 AM |

### Time Zone
Jobs run in your configured timezone. Verify your timezone in account settings.

## Viewing Job History

### Accessing History
Click **View Job History** to see:
- List of past executions
- Date and time of each run
- Status (Success/Failed)
- Duration
- Output/results

### Filtering History
- Filter by job name
- Filter by date range
- Filter by status
- Search within results

### Reviewing Details
Click on a history entry to see:
- Full agent instructions used
- Tool calls made
- Complete output
- Error messages (if failed)

## Job Examples

### Daily Sales Report
```
Job: Daily Sales Summary
Agent: Report Agent
Instructions:
- Query yesterday's orders from the sales database
- Calculate: total revenue, order count, average order value
- Compare to same day last week
- Generate summary email
- Send to: sales-team@company.com
Schedule: Daily at 8:00 AM
```

### Weekly Inventory Check
```
Job: Inventory Alert
Agent: Inventory Agent
Instructions:
- Query products where stock_quantity < reorder_point
- Generate list of items needing reorder
- Include supplier contact info
- Send to: purchasing@company.com
Schedule: Weekly, Monday at 7:00 AM
```

### Monthly Customer Analysis
```
Job: Customer Churn Report
Agent: Analytics Agent
Instructions:
- Identify customers with no orders in past 60 days
- Calculate their historical value
- Segment by risk level
- Generate re-engagement recommendations
- Send report to: customer-success@company.com
Schedule: Monthly, 1st at 9:00 AM
```

### Hourly Monitoring
```
Job: Error Monitor
Agent: Monitoring Agent
Instructions:
- Check error logs for past hour
- If error count > 10, send alert
- Include error types and counts
- Alert: ops-team@company.com
Schedule: Hourly at minute 0
```

## Testing Jobs

### Manual Test Run
1. Select the job
2. Click **Run** button
3. Watch for result notification
4. Check job history for details

### Test Considerations
- Test uses real data and connections
- Actions (like emails) really execute
- Use test email addresses when developing
- Verify outputs before enabling schedule

### Debugging Failed Jobs
1. Check job history for error message
2. Review agent instructions for issues
3. Verify agent has required tools
4. Test agent directly in Assistants page
5. Check data sources are accessible

## Managing Jobs

### Enable/Disable
Use On/Off toggle to:
- **Enable**: Job runs on schedule
- **Disable**: Job paused but configuration saved

### Edit Job
1. Select job from dropdown
2. Modify settings
3. Click Save
4. Changes apply to next run

### Delete Job
1. Select job
2. Click Delete
3. Confirm deletion
4. Schedule is also removed

### Copy Job
Currently, manually create new job and copy settings. Export feature planned.

## Best Practices

### Job Design
- One job = one purpose
- Keep instructions focused
- Include expected output format
- Plan for errors

### Scheduling
- Avoid scheduling during peak hours
- Stagger multiple jobs
- Consider data freshness needs
- Allow time for dependencies

### Monitoring
- Check job history regularly
- Set up failure alerts
- Review output quality periodically
- Adjust schedules based on patterns

### Instructions
- Be explicit about what to query
- Specify exact recipients
- Include fallback behavior
- Use clear, unambiguous language

## Troubleshooting

### "Job didn't run at scheduled time"
- Verify job is enabled (toggle On)
- Check schedule configuration
- Confirm timezone settings
- Review system status

### "Job failed with error"
- Check job history for details
- Verify agent has required permissions
- Test data source connectivity
- Review instructions for issues

### "Job ran but no output"
- Check agent's email tool configuration
- Verify recipients are correct
- Review instructions for output steps
- Test agent manually with same instructions

### "Job runs too slowly"
- Simplify query scope
- Add date range limits
- Consider breaking into multiple jobs
- Optimize agent tool selection

## Common Tasks

### "Create a daily report job"
1. Click New Job
2. Name: "Daily [Report Name]"
3. Select reporting agent
4. Write instructions including:
   - What data to gather
   - How to format
   - Where to send
5. Save
6. Schedule for daily at preferred time
7. Test
8. Enable

### "Pause a job temporarily"
1. Select the job
2. Toggle Off
3. Job won't run until toggled On

### "Check why a job failed"
1. Click View Job History
2. Find the failed run
3. Click to see details
4. Review error message
5. Fix issue and re-test
