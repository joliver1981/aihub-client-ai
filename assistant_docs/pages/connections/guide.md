# Database Connections

The Connections page allows you to configure and manage database connections that your AI agents and data assistants use to query and interact with your data sources.

## Overview

Connections are the bridge between AI Hub and your data. Once configured, connections can be used by:
- **Data Agents** - AI assistants that query databases using natural language
- **Workflows** - Automated processes that read/write data
- **Data Dictionary** - Schema documentation and exploration
- **Custom Tools** - Agent tools that interact with databases

## Security Notice

**Your passwords are stored securely:**
- Encrypted and stored only on your local machine
- Never transmitted to AI Hub cloud servers
- Never visible to the AI Hub team
- Only used when your agents connect to databases

## Page Layout

### Header
- **Database Connections** title
- **Add Connection** button to create a new connection

### Main Form
- **Existing Connections** dropdown to select and edit saved connections
- **Connection Details** form with fields based on connection type
- **Connection String** section showing the generated string
- **Save/Delete** buttons for managing connections

## Connection Types

AI Hub supports multiple connection categories:

### Databases
| Type | Default Port | Driver |
|------|-------------|--------|
| SQL Server | 1433 | ODBC Driver 17 for SQL Server |
| PostgreSQL | 5432 | PostgreSQL UNICODE |
| MySQL | 3306 | MySQL ODBC 8.0 Driver |
| Oracle | 1521 | Oracle ODBC Driver |
| Snowflake | 443 | Snowflake ODBC Driver |

### ERP Systems
- **NetSuite** - Uses OAuth authentication (Consumer Key, Token ID, etc.)
- **SAP** - Enterprise resource planning connectivity
- **Dynamics 365** - Microsoft ERP/CRM platform

### CRM Systems
- **Salesforce** - Uses CData driver with username/password + security token
- **HubSpot** - Marketing and sales platform
- **Zoho CRM** - Customer relationship management

### Cloud Services
- **AWS** - Amazon Web Services data sources
- **Azure** - Microsoft Azure services
- **Google Cloud** - GCP data sources

### APIs
- **REST API** - Connect to any REST endpoint with various auth methods
- **GraphQL** - Query-based API connections

### Files
- **Excel** - Connect to Excel files (.xlsx, .xls)
- **CSV** - Connect to CSV file directories

## Adding a New Connection

1. Click **Add Connection** in the header
2. Select a connection type from the grid:
   - Use category tabs (All, Databases, ERP, CRM, Cloud, APIs) to filter
   - Click on the connection type card
3. Fill in the connection details:
   - **Connection Name** - A friendly name for this connection
   - Type-specific fields (server, credentials, etc.)
   - **ODBC Driver** - Select or use default
   - **Additional Parameters** - Optional extra settings
4. Click **Generate Connection String** to preview
5. Click **Test Connection** to verify connectivity
6. Click **Save Connection** to store

### Standard Database Fields

For most databases (SQL Server, PostgreSQL, MySQL):

| Field | Description | Example |
|-------|-------------|---------|
| Server | Database server hostname or IP | `db.company.com` or `192.168.1.100` |
| Port | Server port number | `1433` (SQL Server default) |
| Database | Database/catalog name | `ProductionDB` |
| Username | Database login username | `app_user` |
| Password | Database login password | `••••••••` |

### NetSuite Fields

| Field | Description | Where to Find |
|-------|-------------|---------------|
| Account ID | Your NetSuite account number | Setup > Company > Company Information |
| Consumer Key | OAuth integration key | Setup > Integration > Manage Integrations |
| Consumer Secret | OAuth integration secret | Created with integration |
| Token ID | Access token identifier | Setup > Users/Roles > Access Tokens |
| Token Secret | Access token secret | Created with access token |
| Sandbox | Check for sandbox accounts | Toggle on for testing |

### Salesforce Fields

| Field | Description | Notes |
|-------|-------------|-------|
| Username | Salesforce login email | `user@company.com` |
| Password | Salesforce password | Your login password |
| Security Token | API security token | Optional if IP whitelisted |
| Instance URL | Custom domain URL | Usually auto-detected |
| Sandbox | Check for sandbox orgs | Toggle on for testing |

### REST API Fields

| Field | Description | Options |
|-------|-------------|---------|
| API URL | Endpoint URL | `https://api.example.com/v1` |
| Authentication Type | Auth method | None, API Key, Bearer, Basic, OAuth |
| Data Format | Response format | JSON, XML, CSV |

## Editing an Existing Connection

1. Select the connection from **Existing Connections** dropdown
2. The form populates with current settings
3. Toggle **View/Edit Connection String Directly** to edit raw string
4. Modify fields as needed
5. Click **Generate Connection String** to update
6. Click **Test Connection** to verify changes
7. Click **Save Connection** to update

## Testing Connections

Before saving, always test your connection:

1. Fill in all required fields
2. Click **Generate Connection String**
3. Click **Test Connection**
4. Wait for result:
   - ✅ **Success** - Connection works, safe to save
   - ❌ **Failed** - Check error message for details

### Common Test Failures

| Error | Likely Cause | Solution |
|-------|--------------|----------|
| Login failed | Wrong credentials | Verify username/password |
| Server not found | Wrong server address | Check hostname/IP |
| Connection timeout | Network/firewall issue | Verify network access |
| Driver not found | Missing ODBC driver | Install required driver |

## Deleting a Connection

1. Select the connection from the dropdown
2. Click **Delete Connection**
3. Confirm the deletion

**Warning:** Deleting a connection will affect any agents or workflows using it.

## Connection Strings

### Viewing the Connection String
The generated connection string appears in the **Connection String** section. It combines all your settings into the format required by the ODBC driver.

### Direct Editing
Toggle **View/Edit Connection String Directly** to:
- See the full connection string
- Make manual edits
- Paste an existing connection string

### Connection String Format
```
Driver={ODBC Driver 17 for SQL Server};Server=myserver.com,1433;Database=MyDB;Uid=myuser;Pwd=mypassword;
```

## ODBC Drivers

AI Hub uses ODBC drivers to connect to data sources. The available drivers depend on what's installed on your system.

### Checking Available Drivers
The ODBC Driver dropdown shows all drivers installed on your machine.

### Installing Additional Drivers
Contact your IT administrator to install drivers for:
- CData drivers (Salesforce, NetSuite, REST, etc.)
- Database-specific drivers (Oracle, MySQL, etc.)

## Additional Parameters

Use the **Additional Parameters** field for extra connection options:
```
Encrypt=yes;TrustServerCertificate=true;Connection Timeout=30
```

Common parameters:
- `Encrypt=yes` - Enable SSL/TLS encryption
- `TrustServerCertificate=true` - Trust self-signed certificates
- `Connection Timeout=30` - Set connection timeout in seconds

## Best Practices

### Security
- Use dedicated service accounts, not personal credentials
- Grant minimum required permissions
- Use read-only accounts when possible
- Rotate credentials periodically

### Naming Conventions
- Use descriptive names: `Production_Sales_DB`, `Dev_Inventory`
- Include environment: `Prod_`, `Dev_`, `Test_`
- Include purpose: `_ReadOnly`, `_Reporting`

### Testing
- Always test before saving
- Test after any password changes
- Verify after network changes

## Troubleshooting

### Connection Won't Save
- Ensure all required fields are filled
- Verify the connection test passes
- Check for special characters that need escaping

### Password Not Working
- Passwords are stored locally and encrypted
- If issues persist, re-enter the password
- Check if password was recently changed

### Driver Issues
- Verify driver is installed (check ODBC Driver dropdown)
- Try selecting a different driver version
- Contact IT for driver installation

## Related Pages

- **Data Agent Builder** - Create agents that use connections
- **Data Assistants** - Query data using natural language
- **Data Dictionary** - View database schemas
- **Local Secrets** - Manage encrypted credentials
