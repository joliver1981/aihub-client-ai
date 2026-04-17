# AI Assistants

The Assistants page is your interactive chat interface for communicating with AI agents.

## Purpose

This is where you:
- Have conversations with configured AI agents
- Ask questions and get intelligent responses
- Execute tasks through natural language
- View agent tool usage in real-time
- Access conversation history

## Page Layout

### Left Sidebar

#### Agent Selection
- **Agent Dropdown**: Choose which agent to chat with
- Shows agent name and brief description
- Agents have different capabilities based on their tools

#### Configuration Sections (Collapsible)

**Chat Settings**
- Temperature: Controls response creativity (0=focused, 1=creative)
- Max tokens: Response length limit
- Show tool calls: Display when agent uses tools

**Session Info**
- Current session ID
- Message count
- Session duration

**Quick Actions**
- Clear conversation
- Export chat history
- Start new session

### Main Chat Area

#### Message Display
- **User messages**: Right-aligned, blue bubbles
- **Agent messages**: Left-aligned, gray bubbles
- **Tool usage**: Expandable sections showing tool calls
- **Timestamps**: When each message was sent

#### Input Area
- Text input for your messages
- Send button
- File attachment (for agents with document tools)
- Voice input (if enabled)

## Chatting with Agents

### Starting a Conversation
1. Select an agent from the dropdown
2. Type your message in the input box
3. Press Enter or click Send
4. Wait for the agent to respond

### Message Types

#### Questions
Ask for information:
```
What were our total sales last month?
Can you explain the return policy?
Who are our top 10 customers by revenue?
```

#### Instructions
Request actions:
```
Generate a report of overdue invoices
Send a summary email to the team
Create a chart showing monthly trends
```

#### Follow-ups
Continue the conversation:
```
Can you break that down by region?
What about the previous quarter?
Show me more details on the first item
```

### Understanding Responses

#### Text Responses
The agent's main reply appears as a chat bubble.

#### Tool Usage Indicators
When the agent uses tools, you'll see:
- Tool name being called
- Input parameters
- Tool output (expandable)

Example:
```
🔧 Using: SQL Query Tool
   Query: SELECT SUM(total) FROM orders WHERE...
   Result: $125,432.50
```

#### Formatted Content
Agents can return:
- Tables of data
- Bulleted lists
- Code snippets
- Charts (if visualization tools enabled)

## Agent Capabilities

### What Agents Can Do
Depends on their configured tools:

| Tool Category | Capabilities |
|---------------|-------------|
| **Database** | Query data, look up records |
| **Documents** | Search files, extract information |
| **Email** | Send messages, read inbox |
| **Calculations** | Perform math, financial formulas |
| **External APIs** | Fetch data from other systems |

### What Agents Cannot Do
- Access tools not configured for them
- Modify data without appropriate permissions
- Remember information across sessions (unless knowledge is added)

## Conversation Management

### Clear Conversation
Click **Clear** to:
- Remove all messages from display
- Start fresh with the agent
- Previous context is forgotten

### Export Chat
Click **Export** to:
- Download conversation as text/JSON
- Save for records or sharing
- Include tool usage details

### Session Continuity
- Conversations persist during your session
- Closing the browser ends the session
- Use Export to save important conversations

## Working with Data

### Asking Data Questions
Be specific for better results:

❌ Vague: "Show me some data"
✅ Specific: "Show me the top 5 products by sales volume this quarter"

### Interpreting Results
Agents explain their work:
- What query they ran
- How they calculated values
- Any assumptions made

### Requesting Formats
Ask for specific output:
```
Show that as a table
Give me just the numbers
Format as a bulleted list
Export to CSV
```

## Tips for Better Conversations

### Be Specific
Include relevant details:
- Date ranges
- Specific metrics
- Filter criteria

### Provide Context
Reference previous messages:
```
Using the customer list from before, which ones are in California?
```

### Ask for Clarification
If the response isn't right:
```
That's not quite what I meant. I'm looking for...
Can you explain how you got that number?
```

### Break Down Complex Requests
Instead of one huge ask, step through:
1. First, get the raw data
2. Then, apply filters
3. Finally, summarize

## Tool Usage Visibility

### Show/Hide Tool Calls
Toggle "Show tool calls" in settings to:
- **On**: See every tool the agent uses
- **Off**: See only final responses

### Understanding Tool Calls
When visible, tool calls show:
- **Tool name**: Which capability was used
- **Input**: What parameters were sent
- **Output**: What the tool returned
- **Duration**: How long it took

### Debugging with Tool Calls
If results seem wrong:
1. Enable tool call visibility
2. Repeat your question
3. Review what tools were called
4. Check if inputs look correct
5. Verify outputs make sense

## Advanced Features

### Multi-Turn Reasoning
Agents remember conversation context:
```
You: What's our revenue this year?
Agent: Revenue is $1.2M for 2024.
You: How does that compare to last year?
Agent: That's up 15% from $1.04M in 2023.
```

### File Attachments
For agents with document tools:
1. Click attachment icon
2. Select file
3. Agent can read and analyze content

### Voice Input
If enabled:
1. Click microphone icon
2. Speak your message
3. Text appears in input box
4. Send as normal

## Troubleshooting

### "Agent not responding"
- Check internet connection
- Verify agent is selected
- Try refreshing the page
- Check if agent service is running

### "Agent says it can't do something"
- The required tool may not be enabled
- Check agent's configured capabilities
- Request may be outside agent's scope

### "Results seem wrong"
- Enable tool calls to see what happened
- Ask agent to explain its reasoning
- Verify source data is correct
- Try rephrasing your question

### "Response is cut off"
- Increase max tokens in settings
- Ask agent to continue
- Break question into smaller parts

## Common Tasks

### "Get a quick data summary"
```
Give me a summary of [metric] for [time period]
```

### "Look up specific record"
```
Find the customer with email [email]
Show me order number [order_id]
```

### "Generate a report"
```
Create a report showing [metrics] by [dimension] for [period]
```

### "Compare values"
```
Compare [metric A] vs [metric B] for [time period]
How does [this month] compare to [last month]?
```

## Best Practices

### Start Simple
Begin with basic questions, then add complexity.

### Use Natural Language
Talk to the agent like a knowledgeable colleague.

### Verify Important Data
For critical decisions, double-check numbers through other means.

### Provide Feedback
If responses aren't helpful, explain what you expected. This helps improve future interactions.

### Save Important Conversations
Export chats you might need to reference later.
