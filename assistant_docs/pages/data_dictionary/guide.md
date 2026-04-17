# Data Dictionary

The Data Dictionary is where you document and enrich your database schema with business context, making your data more understandable to AI agents and human users alike. Well-documented data dictionaries dramatically improve the accuracy of AI-generated SQL queries.

## Why Data Dictionary Matters

When users ask questions like "What were our sales last month?", the AI needs to know:
- Which table contains sales data
- What column represents the date
- What "sales" means (revenue? order count? units?)
- Any filters to apply (exclude cancelled orders?)

The Data Dictionary provides this context, transforming raw database schemas into business-ready knowledge.

## Page Overview

### Header Section
- **Database Connection** dropdown to select which database to document
- **Statistics badges** showing table count, column count, and enhancement percentage

### Tab Navigation
| Tab | Purpose |
|-----|---------|
| **Tables** | View and edit table metadata |
| **Columns** | View and edit column metadata |
| **AI Discovery** | Automatically discover and document tables using AI |
| **Bulk Operations** | Import/export and validate metadata |
| **Help** | Field documentation and guidance |

## Tables Tab

### Table List (Left Panel)
- Scrollable list of all tables in the selected connection
- Search filter to find specific tables
- Color-coded badges indicate enhancement status:
  - 🟢 **Green** - Fully enhanced with description and business name
  - 🟡 **Yellow** - Partially enhanced
  - 🔴 **Red** - No enhancement (needs attention)
- **Add New Table** button to manually add tables
- **Refresh** button to reload from database

### Table Details Form (Right Panel)

#### Basic Information
| Field | Description | Example |
|-------|-------------|---------|
| Table Name | Physical table name (read-only) | `orders` |
| Schema | Database schema | `dbo`, `public` |
| Table Type | Classification | `fact`, `dimension`, `lookup` |
| Business Name | User-friendly name | `Customer Orders` |
| Description | What this table contains | `All customer orders including online and in-store purchases` |

#### Table Types Explained
- **Fact** - Transactional data (orders, events, logs)
- **Dimension** - Descriptive data (customers, products, dates)
- **Lookup** - Reference data (status codes, categories)
- **Bridge** - Many-to-many relationships
- **Aggregate** - Pre-computed summaries

#### Keys & Relationships
| Field | Description |
|-------|-------------|
| Primary Keys | Columns that uniquely identify rows |
| Foreign Keys | References to other tables |
| Related Tables | Tables commonly joined with this one |

#### Business Context
| Field | Description |
|-------|-------------|
| Business Rules | Important constraints and defaults |
| Common Filters | Frequently used WHERE conditions |
| Aggregation Defaults | How to summarize this table |
| Synonyms | Alternative names users might use |
| Tags | Categories for organization |
| Data Quality Notes | Known issues or limitations |

## Columns Tab

### Column Selection
1. Select a table from the dropdown
2. View all columns in that table
3. Click a column to edit its metadata

### Column List (Left Panel)
- Shows column name and data type
- Badges indicate special properties:
  - **PK** - Primary Key
  - **FK** - Foreign Key
  - **Calc** - Calculated/derived field

### Column Details Form (Right Panel)

#### Basic Information
| Field | Description | Example |
|-------|-------------|---------|
| Column Name | Physical column name | `order_total` |
| Data Type | SQL data type | `DECIMAL(10,2)` |
| Business Name | User-friendly name | `Order Total Amount` |
| Description | What this column represents | `Total order value including tax and shipping` |

#### Column Properties
| Property | Description |
|----------|-------------|
| Is Primary Key | Part of the table's unique identifier |
| Is Foreign Key | References another table |
| Is Nullable | Can contain NULL values |
| Is Calculated | Virtual column with formula |
| Is Sensitive | Contains PII or confidential data |

#### Foreign Key Configuration
If the column is a foreign key:
- **Foreign Key Table** - The referenced table
- **Foreign Key Column** - The referenced column

#### Calculated Column
If marked as calculated:
- **Calculation Formula** - SQL expression to compute the value
- Example: `SUM(line_items.quantity * line_items.unit_price)`

#### Additional Metadata
| Field | Description |
|-------|-------------|
| Value Range | Expected min/max or valid values |
| Common Aggregations | How to aggregate (SUM, AVG, COUNT) |
| Units | Measurement units (USD, kg, etc.) |
| Synonyms | Alternative names |
| Examples | Sample values for context |

## AI Discovery Tab

AI Discovery automatically analyzes your database and generates comprehensive metadata.

### How It Works
1. Click **Discover Tables from Database** to scan available tables
2. Select tables you want to analyze (or "Select All")
3. Click **AI Auto-Populate Selected Tables**
4. AI analyzes:
   - Table structure and relationships
   - Sample data patterns
   - Column semantics
   - Business context

### What AI Generates
- Business-friendly names and descriptions
- Table type classification
- Primary/foreign key detection
- Relationship mapping
- Suggested synonyms
- Common filter recommendations

### Best Practices
- Start with your most important tables
- Review and refine AI suggestions
- Add business-specific context AI may miss
- Re-run periodically for new tables

## Bulk Operations Tab

### Export
Download your data dictionary for backup or documentation:
- **Export to CSV** - Spreadsheet-compatible format
- **Export to Excel** - Formatted workbook

### Import
Bulk update metadata from a file:
1. Download the template to see required format
2. Fill in your metadata
3. Upload and import

### Validate
Check for completeness and consistency:
- Missing descriptions
- Orphaned foreign keys
- Inconsistent naming
- Empty required fields

## Working with Business Rules

Business rules guide AI query generation. Access via the **Business Rules Builder** button.

### Rule Types

#### Rules
Constraints the AI should always follow:
```
Only include orders where status = 'Completed'
Exclude test accounts (customer_id < 1000)
Always join with customers table for customer info
```

#### Defaults
Default filter values:
```
date_range: last 30 days
status: Active
region: All
```

## Working with Common Filters

Common filters are pre-defined WHERE conditions. Access via **Common Filters Builder**.

### Recommended Filters
Filters users typically want:
```sql
status = 'Active'
created_date >= DATEADD(month, -1, GETDATE())
region IN ('US', 'CA')
```

### Required Filters
Filters that must always be applied:
```sql
is_deleted = 0
is_test = 0
```

## Enhancement Best Practices

### Priority Order
1. **Fact tables** - Most queried, highest impact
2. **Key dimensions** - Customer, Product, Date
3. **Lookup tables** - Status codes, categories
4. **Supporting tables** - As needed

### Writing Good Descriptions

**Bad:**
> "This is the orders table"

**Good:**
> "Contains all customer orders from all sales channels (web, mobile, in-store). Each row represents a single order with header-level information. Line items are in the order_items table. Excludes cancelled orders (see orders_cancelled for those)."

### Effective Synonyms
Think about how users might refer to the data:
- `revenue` → sales, income, earnings
- `customer` → client, account, buyer
- `order_date` → purchase date, transaction date

### Useful Business Rules
```
When querying revenue, always exclude refunded orders
Default time range is current fiscal year
Customer names should come from customers.display_name, not customers.legal_name
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+S` | Save current form |
| `Esc` | Cancel editing |
| `/` | Focus search input |

## Troubleshooting

### Tables Not Loading
- Verify the connection is working (test on Connections page)
- Check if you have permission to query metadata
- Try the Refresh button

### AI Discovery Fails
- Ensure connection has SELECT permissions
- Check if tables have sample data
- Try selecting fewer tables at once

### Changes Not Saving
- Check for validation errors (red borders)
- Ensure required fields are filled
- Verify connection is still active

## Related Pages

- **Connections** - Set up database connections
- **Data Agents** - Create AI agents that use this dictionary
- **Data Assistants** - Query data using natural language
