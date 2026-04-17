# Data Agent Builder

The Data Agent Builder allows you to create AI agents that can query and interact with your database connections using natural language. Data Agents are specialized agents designed specifically for database operations.

## What is a Data Agent?

A Data Agent is an AI assistant that:
- Connects to a specific database through your configured connections
- Understands your database schema and tables
- Translates natural language questions into SQL queries
- Returns results in a user-friendly format
- Can perform data analysis and reporting tasks

## Page Overview

### Agent Selection
- **Select Agent**: Dropdown to choose an existing data agent to view or edit
- **New Agent**: Button in the header to create a new data agent

### Configuration Fields

| Field | Description | Required |
|-------|-------------|----------|
| Agent Name | A descriptive name for your data agent (only shown when creating new) | Yes |
| Agent Objective | Instructions describing what the agent should do and how it should interact with the database | Yes |
| Database Connection | The database connection this agent will use for queries | Yes |

## Creating a New Data Agent

1. Click the **New Agent** button in the header
2. Enter an **Agent Name** (e.g., "Sales Data Assistant", "Inventory Query Agent")
3. Write a clear **Agent Objective** describing:
   - What types of questions the agent should answer
   - Any specific tables or data it should focus on
   - How it should format responses
   - Any restrictions or guidelines
4. Select the **Database Connection** the agent will use
5. Click **Save**

### Example Objectives

**Sales Analysis Agent:**
```
You are a sales data analyst. Help users query sales data, generate reports, 
and analyze trends. Focus on the sales, orders, and customers tables. 
Always format currency values with dollar signs and two decimal places.
When showing results, limit to 20 rows unless the user asks for more.
```

**Inventory Assistant:**
```
You help warehouse staff check inventory levels and product information.
Query the products, inventory, and suppliers tables. Alert users when 
stock levels are below the reorder point. Format quantities as whole numbers.
```

**HR Data Agent:**
```
You assist HR staff with employee data queries. You can access employee 
records, department information, and attendance data. Never display 
salary information unless specifically requested by authorized users.
Protect sensitive personal information.
```

## Editing an Existing Agent

1. Select the agent from the **Select Agent** dropdown
2. The form will populate with the agent's current configuration
3. Modify the **Agent Objective** or **Database Connection** as needed
4. Click **Save** to update

## Deleting an Agent

1. Select the agent you want to delete from the dropdown
2. Click the **Delete** button
3. Confirm the deletion when prompted

**Warning:** Deleting an agent is permanent and cannot be undone.

## Best Practices

### Writing Effective Objectives

1. **Be Specific**: Clearly describe what data the agent should access
2. **Set Boundaries**: Define what the agent should and shouldn't do
3. **Format Guidelines**: Specify how results should be presented
4. **Include Context**: Mention the business domain and terminology

### Database Connection Considerations

- Ensure the connection has appropriate read permissions
- Consider using read-only connections for safety
- Test the connection before assigning it to an agent
- Use connections with access only to necessary tables

### Security Tips

- Don't give agents access to sensitive data unless required
- Include restrictions in the objective for confidential information
- Use separate agents for different security levels
- Review and audit agent queries periodically

## Troubleshooting

### Agent Not Appearing in Dropdown
- Refresh the page to reload the agent list
- Check if the agent was saved successfully
- Verify you have permission to view data agents

### Connection Not Available
- Go to Connections page and verify the connection exists
- Check if the connection is enabled
- Ensure you have access to the connection

### Agent Not Responding Correctly
- Review and refine the Agent Objective
- Make the instructions more specific
- Test with simple queries first
- Check if the database connection is working

## Related Pages

- **Connections**: Set up database connections for your agents
- **Data Assistants**: Use your data agents to query databases
- **Data Dictionary**: View and document your database schema

## Using Your Data Agent

Once created, your Data Agent can be used in the **Data Assistants** page where you can:
- Select your agent from the available assistants
- Ask natural language questions about your data
- Get SQL queries generated and executed automatically
- View results in formatted tables
- Export data as needed
