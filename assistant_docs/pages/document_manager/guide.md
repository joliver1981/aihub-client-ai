# Document Manager

The Document Manager provides a centralized view of all documents loaded into AI Hub for AI agent access and search.

## Purpose

Document Manager helps you:
- View all uploaded and processed documents
- Monitor document processing status
- Manage document metadata
- Maintain the vector database
- Search and filter documents

## Page Layout

### Statistics Cards
Overview metrics at the top:
- **Total Documents**: Count of all documents
- **Total Pages**: Sum of all document pages
- **Document Types**: Number of distinct types
- **Last Updated**: Most recent document change

### Filters & Actions Section

#### Filter Options
- **Document Type**: Filter by category
- **Date Range**: Filter by upload/update date
- **Search**: Find documents by filename

#### Filter Actions
- **Apply Filters**: Execute filter criteria
- **Reset**: Clear all filters

### Vector Database Maintenance
Administrative tools for managing the vector database:
- **Reprocess Selected**: Re-embed specific documents
- **Force Rebuild**: Complete vector database rebuild

### Document Table
List of all documents with columns:
- Filename
- Document Type
- Page Count
- Upload Date
- Status
- Actions

## Document Types

Documents are categorized by type:

| Type | Description |
|------|-------------|
| **Invoice** | Bills, invoices, statements |
| **Contract** | Legal agreements, contracts |
| **Report** | Business reports, analyses |
| **Manual** | User guides, documentation |
| **Correspondence** | Emails, letters |
| **Form** | Filled forms, applications |
| **Other** | Uncategorized documents |

## Document Status

| Status | Meaning |
|--------|---------|
| **Processed** | Successfully vectorized and searchable |
| **Processing** | Currently being analyzed |
| **Pending** | Queued for processing |
| **Failed** | Processing error occurred |
| **Archived** | Removed from active search |

## Filtering Documents

### By Type
1. Select type from dropdown
2. Click Apply Filters
3. Table shows only matching documents

### By Date Range
1. Select preset (Today, Last 7 Days, etc.)
2. Or select Custom Range for specific dates
3. Click Apply Filters

### By Search
1. Enter search term in Search box
2. Matches against filename
3. Click Apply Filters

### Combining Filters
All filters work together:
- Select type AND date range AND search
- Apply to narrow results

## Vector Database

### What is the Vector Database?
The vector database stores document embeddings that enable:
- Semantic search (search by meaning)
- AI agent document retrieval
- Similar document finding

### When to Reprocess

Reprocess documents when:
- Document content was updated
- Search results seem incorrect
- After system upgrade
- Troubleshooting retrieval issues

### Reprocess Selected Documents
1. Click **Reprocess Selected Documents**
2. Choose selection method:
   - By document type
   - By date range
   - Specific documents
3. Click **Start Reprocessing**
4. Monitor progress

### Force Rebuild Vectors
⚠️ **Use with caution** - this deletes and recreates all vectors.

1. Click **Force Rebuild Vectors**
2. Confirm you understand the impact
3. All documents are re-embedded
4. May take significant time

### Reprocessing Considerations
- Documents unavailable during reprocessing
- Large collections take time
- Schedule during low-usage periods
- Verify search works after completion

## Document Actions

### View Document
1. Click **View** action
2. Opens document viewer
3. See extracted text and metadata
4. Navigate pages (for multi-page docs)

### Edit Metadata
1. Click **Edit** action
2. Modify document type or tags
3. Update description
4. Save changes

### Reprocess Single Document
1. Click **Reprocess** action
2. Re-extracts text and creates new vectors
3. Useful after noticing issues with one document

### Delete Document
1. Click **Delete** action
2. Confirm deletion
3. Removes document and its vectors
4. Cannot be undone

## Bulk Operations

### Select Multiple Documents
1. Use checkboxes to select documents
2. Or use "Select All" in header
3. Apply bulk action

### Bulk Delete
1. Select documents
2. Click Bulk Delete
3. Confirm
4. All selected documents removed

### Bulk Reprocess
1. Select documents
2. Click Bulk Reprocess
3. All selected re-vectorized

### Bulk Change Type
1. Select documents
2. Click Change Type
3. Select new type
4. Apply to all selected

## Search Behavior

### How Document Search Works
1. User query converted to vector
2. Vector database finds similar document sections
3. Most relevant sections returned
4. AI agent uses these for responses

### Improving Search Results
- Add descriptive filenames
- Set correct document types
- Add tags and descriptions
- Ensure quality document uploads (clear text)

## Best Practices

### Document Organization
- Use consistent naming conventions
- Set appropriate document types
- Add descriptions for complex documents
- Remove outdated documents

### Maintenance Schedule
- Review document list monthly
- Remove obsolete documents
- Reprocess after major updates
- Monitor processing failures

### Upload Quality
- Use searchable PDFs (not scanned images)
- Ensure text is extractable
- Clean documents work better
- Large documents may need splitting

## Troubleshooting

### "Document not appearing in search"
- Check document status is "Processed"
- Verify document type is searchable
- Try reprocessing the document
- Check if document content is extractable

### "Processing stuck"
- Check system resources
- Large documents take longer
- Try reprocessing
- Contact admin if persistent

### "Wrong content extracted"
- Document may be image-based (needs OCR)
- PDF might be corrupted
- Try re-uploading cleaner version
- Check original document quality

### "Search returns irrelevant results"
- Vector database may need rebuild
- Document descriptions may be misleading
- Try more specific queries
- Review document categorization

### "Reprocessing failed"
- Check document is accessible
- Verify file isn't corrupted
- Review error message in logs
- Try deleting and re-uploading

## Common Tasks

### "Find a specific document"
1. Enter filename in Search box
2. Click Apply Filters
3. Or browse the document list
4. Use type filter to narrow down

### "See all documents uploaded this week"
1. Set Date Range to "Last 7 Days"
2. Click Apply Filters
3. Review the filtered list

### "Fix a document that won't search"
1. Find document in list
2. Check status
3. If "Failed", click Reprocess
4. If still failing, delete and re-upload

### "Clean up old documents"
1. Set Date Range to older period
2. Review documents
3. Select obsolete ones
4. Bulk delete

### "Rebuild search after issues"
1. Go to Vector Database Maintenance
2. Click Force Rebuild (if necessary)
3. Wait for completion
4. Test search functionality

## Document Viewer

### Accessing the Viewer
Click **View** on any document to open the viewer.

### Viewer Features
- Full extracted text display
- Page navigation (multi-page docs)
- Metadata panel
- Download original option

### Text Extraction Preview
See exactly what text was extracted:
- Verify accuracy
- Identify extraction issues
- Compare with original
