# Agent Builder

The Agent Builder page is where Developers and Admins create and configure custom AI agents. Agents are AI-powered assistants that can answer questions, execute tools, and access knowledge bases.

## Access Requirements

This page is accessible to users with **Developer** (role 2) or **Admin** (role 3) permissions.

## Overview

Agents are the core AI assistants in the platform. Each agent has:
- A name and objective (purpose description)
- Core tools (built-in capabilities)
- Custom tools (user-created functions)
- Optional knowledge base (documents and data)

## Page Layout

### Header
- **Agent Builder** title

### Main Configuration Area (Left - 67%)

#### Agent Configuration Card
- **Agent Objective** - Describes what the agent does
- **Agent Name** - Unique identifier for the agent
- **Core Tools** - Built-in platform tools
- **Custom Tools** - User-created tools
- **Save Changes** / **Delete Agent** buttons

### Sidebar (Right - 33%)

#### Select Agent Card
- Dropdown to choose an existing agent to edit

#### Actions Card
- **Manage Knowledge** - Open knowledge base manager
- **Add New Agent** - Create a new agent
- **Export Agent** - Download agent as package
- **Import Agent** - Upload agent package

## Agent Configuration

### Agent Objective

The objective is a system prompt that defines the agent's behavior:

**Good objectives include:**
- Clear purpose: "You are a sales data analyst..."
- Specific domain: "...that helps users understand revenue trends"
- Behavioral guidelines: "Always provide data sources"
- Constraints: "Only answer questions about sales data"

**Example:**
```
You are a helpful sales analyst assistant. You help users 
understand sales trends, customer behavior, and revenue metrics. 
Always cite the data source when providing statistics.
```

### Agent Name

- Must be unique across all agents
- Use descriptive names: "Sales Analyst", "HR Assistant"
- Avoid special characters
- Keep it concise but meaningful

## Core Tools

Core tools are built-in platform capabilities:

| Tool | Description |
|------|-------------|
| **ask_question** | Query databases using natural language |
| **document_search** | Search uploaded documents |
| **web_search** | Search the internet (if enabled) |
| **calculator** | Perform calculations |
| **email_sender** | Send emails (if configured) |

### Selecting Core Tools

1. Find tools in the **Core Tools** list
2. Use the search box to filter
3. Check the box to enable a tool
4. Some tools are mandatory and cannot be disabled
5. Tool dependencies are automatically included

### Tool Dependencies

Some tools require other tools to function:
- Dependencies show in the **Tool Dependencies** panel
- Selecting a tool automatically enables its dependencies
- Dependencies are marked and cannot be unchecked

## Custom Tools

Custom tools are user-created functions that extend agent capabilities.

### Selecting Custom Tools

1. Browse the **Custom Tools** list
2. Use the search box to filter by name or description
3. Check tools to add them to the agent
4. Tools show name and description

### Tool Categories

Custom tools may be organized by category:
- Click category headers to expand/collapse
- Badge shows count of tools in each category

## Creating a New Agent

1. Click **Add New Agent** in the Actions card
2. A popup dialog appears with:
   - **Agent Name** field
   - **Agent Objective** textarea
   - **Core Tools** selection
   - **Custom Tools** selection
3. Fill in all required fields
4. Select desired tools
5. Click **Save Agent**

### Best Practices for New Agents

- Start with a clear, focused objective
- Select only necessary tools (less is more)
- Test with simple questions first
- Refine objective based on results

## Editing an Agent

1. Select an agent from the **Select Agent** dropdown
2. The configuration loads automatically
3. Modify objective, name, or tool selection
4. Click **Save Changes**

### What You Can Edit
- Agent objective (system prompt)
- Agent name
- Core tool selection
- Custom tool selection

### What You Cannot Edit
- Agent ID (system-assigned)
- Tool dependencies (auto-managed)

## Deleting an Agent

1. Select the agent to delete
2. Click **Delete Agent**
3. Confirm the deletion

**Warning:** Deleting an agent:
- Removes all group permissions for that agent
- Does not delete associated custom tools
- Does not delete knowledge base items
- Cannot be undone

## Managing Agent Knowledge

Click **Manage Knowledge** to open the knowledge base manager where you can:
- Upload documents for the agent to reference
- Add text snippets and data
- Organize knowledge into categories
- Delete outdated information

Knowledge enables agents to answer questions about specific documents and data.

## Exporting Agents

Export an agent as a portable package:

1. Select the agent to export
2. Click **Export Agent**
3. A `.zip` package downloads containing:
   - Agent configuration
   - Custom tools used
   - Knowledge base items
   - Core tool references

### Export Use Cases
- Backup agent configurations
- Share agents between environments
- Create templates for similar agents
- Version control agent changes

## Importing Agents

Import a previously exported agent:

1. Click **Import Agent**
2. Select the `.zip` package file
3. Click **Analyze** to scan contents
4. Review package information:
   - Agent name and version
   - Tools included
   - Knowledge items
5. Handle conflicts if detected:
   - Agent name already exists
   - Custom tools already exist
6. Configure options:
   - Overwrite existing tools
   - Rename if name exists
7. Click **Proceed** to import
8. Review import progress
9. Click **Complete** when finished

### Import Conflict Resolution

| Conflict | Options |
|----------|---------|
| Agent name exists | Rename automatically or overwrite |
| Custom tools exist | Skip or overwrite |

## Tool Search and Filtering

Both Core and Custom tool lists support:
- **Search box** - Filter by name or description
- **Clear button** (×) - Reset search filter
- **Category collapse** - Expand/collapse tool groups

## Best Practices

### Agent Design

1. **Single Purpose** - Each agent should do one thing well
2. **Clear Objectives** - Be specific about what the agent should do
3. **Minimal Tools** - Only include necessary tools
4. **Test Thoroughly** - Verify agent behavior before deployment

### Tool Selection

1. **Start Small** - Add tools incrementally
2. **Check Dependencies** - Understand what's auto-included
3. **Review Descriptions** - Ensure tools match your needs
4. **Custom over Core** - Use custom tools for specific business logic

### Security

1. **Limit Tool Access** - Don't give agents unnecessary capabilities
2. **Review Custom Tools** - Understand what custom tools do
3. **Test in Sandbox** - Verify behavior before production use
4. **Monitor Usage** - Review feedback for issues

## Troubleshooting

### Agent Not Saving
- Check all required fields are filled
- Verify name is unique
- Ensure you have edit permissions

### Tools Not Appearing
- Refresh the page
- Check if tools are enabled in system
- Verify custom tools are active

### Import Failing
- Verify package file is valid
- Check for version compatibility
- Review conflict resolution options

### Agent Not Working as Expected
- Review and refine the objective
- Check tool selection matches use case
- Verify knowledge base is populated
- Test with simpler questions

## Related Pages

- **Custom Tools** - Create tools for agents
- **Agent Knowledge** - Manage knowledge bases
- **Assistants** - Use agents (end-user view)
- **Data Agent Builder** - Create data-specific agents
- **Groups** - Assign agent permissions
