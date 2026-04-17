# My Approvals

The Approvals page is your central hub for reviewing and acting on workflow items that require human decision-making.

## Purpose

Approvals enable human-in-the-loop processes where:
- Automated workflows pause for review
- You verify AI-generated outputs
- Critical decisions require human judgment
- Compliance requires oversight

## Page Layout

### Page Header
- **Title**: My Approvals
- **Timezone Indicator**: Shows your current timezone

### Statistics Cards
Quick overview metrics:
- **Pending**: Items awaiting your decision
- **Approved**: Items you've approved (today/period)
- **Rejected**: Items you've rejected
- **Overdue**: Items past their deadline

### Filters Section
- **Status Filter**: Pending, Approved, Rejected, All
- **Workflow Filter**: Filter by specific workflow
- **Date Filter**: Time period selection
- **Search**: Find specific approval items

### Approvals Table
Main list showing all approval items with:
- Request details
- Workflow source
- Submitted time
- Due date
- Status
- Actions

## Approval Status

| Status | Meaning | Action Needed |
|--------|---------|---------------|
| **Pending** | Awaiting decision | Review and decide |
| **Approved** | You approved | None - completed |
| **Rejected** | You rejected | None - completed |
| **Overdue** | Past deadline | Urgent - decide now |
| **Escalated** | Sent to backup | May need attention |

## Reviewing an Approval

### Opening the Review
1. Find pending item in list
2. Click the item row or **Review** button
3. Approval detail panel opens

### Information Provided
Each approval shows:
- **Summary**: Brief description of what needs approval
- **Workflow**: Which workflow generated this
- **Context**: Supporting information
- **Data**: Relevant data being processed
- **History**: Previous related approvals

### Making a Decision

#### To Approve
1. Review all provided information
2. Click **Approve** button
3. Optionally add a comment
4. Confirm approval

#### To Reject
1. Review the request
2. Click **Reject** button
3. Provide rejection reason (usually required)
4. Confirm rejection

#### To Request More Info
If you need clarification:
1. Click **Request Info** (if available)
2. Enter your question
3. Workflow owner is notified
4. Item remains pending

## Approval Types

### Document Approvals
Review AI-processed documents:
- Extracted data accuracy
- Classification correctness
- Compliance verification

Example:
```
Document: Invoice INV-2024-0542
Extracted vendor: Acme Corp
Extracted amount: $15,432.50
Confidence: 94%

[Approve if correct] [Reject if errors found]
```

### Action Approvals
Authorize actions before execution:
- Email sending
- Data modifications
- External API calls

Example:
```
Action: Send payment notification
Recipient: vendor@acme.com
Amount referenced: $15,432.50
Workflow: Invoice Processing

[Approve to send] [Reject to cancel]
```

### Decision Approvals
Make business decisions:
- Threshold exceeded alerts
- Exception handling
- Policy decisions

Example:
```
Decision needed: Order exceeds credit limit
Customer: Beta Corp
Order value: $50,000
Credit limit: $25,000
Recommendation: Request 50% deposit

[Approve recommendation] [Reject and cancel order]
```

## Deadlines and Escalation

### Due Dates
Approvals may have deadlines:
- Shown in the due date column
- Overdue items highlighted in red
- Urgent items may show countdown

### Escalation Rules
If you don't respond in time:
1. Item may escalate to backup approver
2. Or workflow may auto-reject
3. Or workflow may auto-approve
4. Depends on workflow configuration

### Managing Urgency
- Check Pending count regularly
- Address overdue items first
- Set up notifications for new approvals

## Filtering and Searching

### Status Filter
Quick access to:
- **Pending**: What needs attention now
- **Approved**: What you've approved
- **Rejected**: What you've rejected
- **All**: Complete history

### Workflow Filter
See approvals from specific workflows:
1. Select workflow from dropdown
2. Shows only items from that workflow
3. Useful for focused review

### Date Range
Filter by time period:
- Today
- Last 7 days
- Last 30 days
- Custom range

### Search
Find specific items by:
- Request ID
- Description text
- Customer/vendor name
- Related data

## Bulk Operations

### Selecting Multiple Items
1. Use checkboxes to select items
2. Or "Select All" for page

### Bulk Approve
For multiple similar items:
1. Select items to approve
2. Click **Bulk Approve**
3. Add optional comment
4. Confirm

⚠️ Use bulk approve carefully - review each item first.

### Bulk Reject
1. Select items to reject
2. Click **Bulk Reject**
3. Provide rejection reason
4. Confirm

## Approval History

### Viewing History
See past decisions:
1. Filter by Approved or Rejected status
2. Or select "All" to see everything
3. Click item to see decision details

### History Shows
- When you decided
- What you decided
- Any comments you added
- Workflow outcome after decision

### Audit Trail
Complete record maintained:
- Who approved/rejected
- Timestamp of decision
- Context at time of decision
- Subsequent workflow actions

## Notifications

### Email Notifications
You may receive emails for:
- New approval assigned
- Approaching deadline
- Escalation notice

### In-App Notifications
Bell icon shows:
- New pending items
- Overdue reminders
- System messages

### Configuring Notifications
Adjust in account settings:
- Email frequency
- Notification types
- Quiet hours

## Best Practices

### Timely Response
- Check approvals regularly
- Don't let items become overdue
- Set reminders if needed

### Thorough Review
- Read all provided context
- Check data accuracy
- Consider implications

### Clear Comments
- Add comments explaining decisions
- Especially for rejections
- Helps workflow owners improve

### Consistent Decisions
- Apply same criteria to similar items
- Document your decision criteria
- Ask if unsure about policy

## Troubleshooting

### "I don't see an expected approval"
- Check all status filters
- Expand date range
- Verify you're the assigned approver
- Item may have been escalated

### "Approval won't submit"
- Check required fields (rejection reason)
- Verify network connection
- Try refreshing page
- Check if item was already handled

### "Wrong person assigned"
- Contact workflow owner
- They can reassign approvals
- Or modify workflow configuration

### "Need more information to decide"
- Use "Request Info" if available
- Or contact workflow owner directly
- Don't approve if uncertain

## Common Tasks

### "Review all pending approvals"
1. Go to Approvals page
2. Filter by Status: Pending
3. Sort by due date
4. Review each item
5. Make decisions

### "Find a past approval"
1. Set Status filter to All
2. Use search with relevant terms
3. Or filter by date range
4. Click to view details

### "Quickly approve routine items"
1. Filter to specific workflow type
2. Select multiple similar items
3. Verify they're all correct
4. Use bulk approve

### "Delegate while away"
- Contact admin to reassign pending items
- Set up escalation rules in advance
- Notify backup approvers

## Understanding Context

### What to Look For
- Is the data accurate?
- Does the action make sense?
- Are there any red flags?
- Is this consistent with policy?

### When to Reject
- Data is clearly wrong
- Action would violate policy
- Missing critical information
- Suspicious activity detected

### When to Escalate
- Beyond your authority
- Need additional expertise
- Policy unclear
- High-risk situation
