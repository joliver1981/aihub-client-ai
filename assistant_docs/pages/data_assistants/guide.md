# Data Agent Chat

The Data Agent Chat page is where you interact with Data Agents using natural language to query your databases. Ask questions in plain English and receive data, tables, charts, and explanations without writing SQL.

## Overview

Data Agents are AI-powered assistants that understand your database structure and can:
- Answer questions about your data
- Generate and execute SQL queries
- Return formatted data tables
- Create visualizations
- Explain query results

## Page Layout

### Header
- **Data Agent Chat** title
- **Reset** button to start a new conversation

### Main Chat Area (Left - 75%)
- **Chat Window** - Scrollable area showing conversation history
- **Message Input** - Text field to type your questions
- **Send Button** - Submit your question
- **Explain Link** - Request explanation of the last response

### Sidebar (Right - 25%)
- **Agent Selection** - Choose which Data Agent to use
- **Agent Objective** - Shows the selected agent's purpose
- **Caution Level** - Adjust AI cautiousness (if enabled)

## Using Data Agents

### Selecting an Agent

1. Click the **Select Agent** dropdown
2. Choose an agent from the list
3. The agent's objective appears below
4. You can now start asking questions

Each agent is configured for specific data domains (sales, inventory, HR, etc.).

### Asking Questions

Type natural language questions in the input field:

**Good question examples:**
- "What were our total sales last month?"
- "Show me the top 10 customers by revenue"
- "How many orders were placed this week?"
- "What's the average order value by region?"
- "List all products with inventory below 100"

**Tips for better results:**
- Be specific about time periods ("last month", "Q3 2024")
- Mention specific columns or metrics when known
- Ask one question at a time
- Use business terms the agent understands

### Sending Messages

- Type your question in the input field
- Press **Enter** or click **Send**
- Wait for the response (loading spinner appears)
- Review the results

### Understanding Responses

Responses can include:

| Element | Description |
|---------|-------------|
| **Text** | Natural language explanation |
| **Data Table** | Formatted results from the query |
| **Chart** | Visual representation of data |
| **SQL Query** | The generated query (click "Show Query") |

### Data Tables

Tables in responses support:
- **Compact/Expand** toggle for display mode
- Sortable columns (if enabled)
- Horizontal scrolling for wide tables
- Striped rows for readability

### Requesting Explanations

After receiving a response:
1. Click "Want me to explain?" link
2. The agent explains how it interpreted your question
3. Shows the logic behind the query

## Conversation Features

### Chat History

- Conversations are maintained during your session
- Previous Q&A pairs remain visible
- Context is preserved for follow-up questions

### Follow-up Questions

You can ask follow-up questions that reference previous answers:
- "Break that down by month"
- "Show only the top 5"
- "What about last year?"
- "Exclude cancelled orders"

### Resetting Conversation

Click **Reset** in the header to:
- Clear the chat history
- Start a fresh conversation
- Reset conversation context

Use reset when:
- Changing topics completely
- The agent seems confused
- You want to start over

### Conversation Limits

A warning banner appears when you reach the maximum conversation length. Start a new conversation when this happens.

## Feedback System

After each response, you can provide feedback:

### Quick Feedback
- 👍 **Thumbs Up** - Response was helpful
- 👎 **Thumbs Down** - Response was not helpful

### Detailed Feedback
When clicking thumbs down:
1. Rate the response (1-5 stars)
2. Enter details about what was wrong
3. Submit feedback

Feedback helps improve agent accuracy over time.

## Caution Level Settings

If enabled, adjust how cautious the AI should be:

| Level | Behavior |
|-------|----------|
| **Low** | More assumptions, fewer clarifying questions |
| **Medium** | Balanced approach (default) |
| **High** | More verification, fewer assumptions |
| **Very High** | Maximum verification before acting |

Higher caution = more questions but safer results.

## Response Confidence

Some responses may show confidence indicators:
- **High Confidence** - Agent is certain about the interpretation
- **Low Confidence** - Agent made assumptions (yellow highlight)

Low confidence warnings indicate:
- Ambiguous question interpretation
- Missing data dictionary information
- Unusual query patterns

## Best Practices

### Getting Better Results

1. **Be Specific**
   - ❌ "Show me sales"
   - ✅ "Show me total sales by product category for Q4 2024"

2. **Use Known Terms**
   - Reference column names from the data dictionary
   - Use business terms the agent recognizes

3. **One Question at a Time**
   - ❌ "Show sales and inventory and customer count"
   - ✅ "Show total sales by region" (then follow up)

4. **Specify Time Ranges**
   - ❌ "Recent orders"
   - ✅ "Orders from the last 30 days"

### When Results Are Wrong

1. Provide feedback (thumbs down)
2. Try rephrasing the question
3. Be more specific about what you want
4. Check if the data dictionary needs updates
5. Reset and try again

## Troubleshooting

### Agent Not Loading
- Refresh the page
- Check if you have permission for the agent
- Verify your group has agent access

### No Results Returned
- Question may be too vague
- Data may not exist for the criteria
- Try broadening your search

### Wrong Data Returned
- Agent may have misinterpreted the question
- Rephrase with more specific terms
- Check the generated SQL (Show Query)

### Slow Responses
- Complex queries take longer
- Large result sets need processing time
- Database may be under heavy load

### Error Messages
- Note the error text
- Try simplifying your question
- Reset conversation and try again
- Contact admin if persistent

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send message |
| `Ctrl+Enter` | New line (if supported) |

## Related Pages

- **Data Agent Builder** - Create and configure Data Agents
- **Data Dictionary** - Define database schema for agents
- **Connections** - Set up database connections
- **Feedback Analysis** - Review feedback trends
