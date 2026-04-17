# Document Search

The Document Search page provides advanced search capabilities across all your uploaded documents.

## Purpose

Document Search allows you to:
- Find specific information within documents
- Search by field values or natural language
- Filter by document type and attributes
- Locate documents based on extracted data

## Page Layout

### Left Sidebar

#### Document Types Panel
- List of all document categories
- Count of documents per type
- Click to filter by type
- "All Documents" shows everything

#### Search Options Panel
- **Maximum Results**: Limit returned results (5-50)
- **Minimum Score**: Relevance threshold (0.0-1.0)

#### Common Fields Panel
- Frequently searched fields
- Quick access to common filters
- Click to add to search

### Main Content Area

#### Search Tabs

**Field Search**
- Search specific document fields
- Use operators (equals, contains, etc.)
- Combine multiple criteria

**Attribute Search**
- Search document metadata
- Find by date, type, tags

**Language Search**
- Natural language queries
- Semantic search using AI
- Ask questions in plain English

#### Results Area
- Matching documents list
- Relevance scores
- Document previews
- Action buttons

## Search Methods

### Field Search

Search specific extracted fields:

1. Select field from dropdown
2. Choose operator:
   - **Equals**: Exact match
   - **Contains**: Partial match
   - **Starts With**: Beginning match
   - **Greater Than/Less Than**: Numeric comparison
3. Enter search value
4. Click Search

#### Adding Multiple Criteria
Click **Add Criteria** to search multiple fields:
```
Invoice Number EQUALS INV-2024-001
AND
Customer Name CONTAINS "Acme"
AND
Amount GREATER THAN 1000
```

### Attribute Search

Search by document properties:

| Attribute | Example |
|-----------|---------|
| Document Type | Invoice, Contract |
| Upload Date | Last 7 days, specific date |
| Status | Processed, Pending |
| Tags | High Priority, Reviewed |

### Language Search

Ask questions naturally:

```
Find invoices over $5000 from last month
Show contracts expiring in 2024
Which documents mention "warranty terms"?
Find all purchase orders from Acme Corp
```

The AI interprets your question and searches accordingly.

## Search Results

### Result Display
Each result shows:
- **Document name**: Original filename
- **Type**: Document category
- **Score**: Relevance percentage
- **Preview**: Snippet of matching content
- **Highlights**: Matched terms highlighted

### Sorting Results
- **Relevance**: Best matches first (default)
- **Date**: Newest or oldest first
- **Name**: Alphabetical order

### Result Actions
- **View**: Open full document
- **Download**: Get original file
- **Details**: See all metadata

## Relevance Scoring

### Understanding Scores
- **0.9-1.0**: Excellent match
- **0.7-0.9**: Good match
- **0.5-0.7**: Partial match
- **Below 0.5**: Weak match

### Minimum Score Threshold
Adjust the slider to filter by quality:
- Higher threshold = fewer, better results
- Lower threshold = more results, varying quality

## Common Fields

### Invoice Fields
- Invoice Number
- Vendor Name
- Invoice Date
- Total Amount
- Due Date
- Line Items

### Contract Fields
- Contract Number
- Party Names
- Effective Date
- Expiration Date
- Contract Value
- Terms

### General Fields
- Document Date
- Author
- Title
- Keywords
- Reference Numbers

## Search Tips

### Be Specific
More specific queries return better results:

❌ Vague: "invoice"
✅ Specific: "invoice from Acme Corp dated January 2024"

### Use Quotes for Exact Phrases
```
"purchase agreement" finds exact phrase
purchase agreement finds either word
```

### Combine Field and Language Search
1. Use field search for known values
2. Use language search for exploratory queries
3. Combine for best results

### Filter First
If you know the document type:
1. Click document type in sidebar
2. Then run your search
3. Faster, more relevant results

## Advanced Features

### Wildcard Search
Use * for partial matching in field search:
```
INV-2024-* matches INV-2024-001, INV-2024-002, etc.
```

### Date Range Search
For date fields:
- Select "Between" operator
- Enter start and end dates
- Finds documents in range

### Numeric Ranges
For amount/number fields:
- Greater Than / Less Than
- Between (specify range)
- Useful for filtering by value

### Multiple Value Search
Search for multiple values:
```
Customer IN ["Acme Corp", "Beta Inc", "Gamma LLC"]
```

## Working with Results

### Preview Documents
Click result to see preview:
- Shows matching page/section
- Highlighted search terms
- Context around match

### Export Results
Click **Export** to:
- Download as CSV
- Save result list
- Include key metadata

### Refine Search
If results aren't right:
1. Add more criteria
2. Adjust score threshold
3. Try different search method
4. Narrow document type

## Troubleshooting

### "No results found"
- Broaden search criteria
- Lower minimum score threshold
- Check spelling
- Try language search for flexibility

### "Too many irrelevant results"
- Increase minimum score threshold
- Add more search criteria
- Be more specific
- Filter by document type first

### "Document not appearing"
- Check document is processed
- Verify document type filter
- Try different search terms
- Check in Document Manager

### "Wrong field values extracted"
- Document may have extraction issues
- Try searching different fields
- Check document quality
- Reprocess in Document Manager

### "Language search not understanding"
- Rephrase the question
- Use simpler language
- Try field search instead
- Break complex queries into parts

## Common Tasks

### "Find a specific invoice"
1. Click "Invoice" in document types
2. Use Field Search
3. Set: Invoice Number EQUALS [number]
4. Search

### "Find all documents from a vendor"
1. Use Language Search
2. Enter: "documents from [vendor name]"
3. Or use Field Search: Vendor CONTAINS [name]

### "Find contracts expiring soon"
1. Click "Contract" in document types
2. Field Search: Expiration Date LESS THAN [date]
3. Sort by date

### "Search document content"
1. Use Language Search
2. Enter your question naturally
3. Review results with highlights

### "Find high-value invoices"
1. Click "Invoice" type
2. Field Search: Total Amount GREATER THAN [value]
3. Sort by amount descending

## Best Practices

### Organize Before Searching
- Ensure documents have correct types
- Add tags for categorization
- Keep filenames meaningful

### Save Common Searches
- Note successful search patterns
- Reuse for recurring needs
- Document search strategies

### Verify Results
- Check relevance scores
- Review matched content
- Confirm document is correct one

### Use Appropriate Method
- Field Search: Know exact values
- Attribute Search: Filter by properties
- Language Search: Exploratory queries
