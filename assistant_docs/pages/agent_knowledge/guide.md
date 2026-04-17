# Agent Knowledge

The Agent Knowledge page allows you to manage documents and information that a specific AI agent can reference when responding to questions.

## Purpose

Agent Knowledge provides:
- Document storage specific to each agent
- Information the agent can search and cite
- Context that improves agent responses
- Reference material for specialized tasks

## How Knowledge Works

When you add knowledge to an agent:
1. Documents are processed and indexed
2. Agent can search this information when needed
3. Responses are grounded in your actual documents
4. Citations reference source material

## Page Layout

### Page Header
- **Title**: Agent Knowledge Management
- **Back Button**: Return to agent configuration

### Left Panel - Knowledge Documents
- List of all uploaded documents
- Document names and descriptions
- Status indicators
- Action buttons (Edit, Delete)

### Right Panel

#### Agent Information
- Agent name (read-only)
- Agent objective (read-only)
- Shows which agent you're managing

#### Add Knowledge Section
- Description field
- File upload
- Supported format list
- Add button

## Supported File Types

| Format | Extension | Best For |
|--------|-----------|----------|
| **PDF** | .pdf | Reports, manuals, documents |
| **Word** | .docx | Documents, procedures |
| **Excel** | .xlsx | Data tables, lists |
| **Text** | .txt | Plain text, notes |
| **Images** | .jpg, .png | Photos, diagrams (with text extraction) |

## Adding Knowledge

### Step 1: Write Description
Describe what the document contains:
```
Product catalog with pricing and specifications for 2024 product line.
```

Good descriptions help the agent find relevant information.

### Step 2: Select File
1. Click **Choose file**
2. Select document from your computer
3. Verify correct file is selected

### Step 3: Upload
1. Click **Add Document**
2. Wait for processing
3. Document appears in list when ready

### Processing Status
- **Processing**: Document being analyzed
- **Ready**: Available for agent queries
- **Failed**: Error occurred (check document quality)

## Managing Knowledge

### Edit Description
1. Click **Edit** button on document
2. Update description text
3. Save changes

Better descriptions improve search accuracy.

### Delete Document
1. Click **Delete** button
2. Confirm deletion
3. Document removed from agent's knowledge

⚠️ Deletion cannot be undone.

### View Document
1. Click document name
2. Opens document preview
3. See extracted content

## Knowledge Best Practices

### Document Selection
- Add documents directly relevant to agent's purpose
- Quality over quantity
- Keep information current

### Good Documents for Knowledge
- Product manuals
- Policy documents
- Procedure guides
- FAQ compilations
- Reference tables
- Training materials

### Document Quality
- Clear, readable text
- Searchable PDFs (not scanned images)
- Well-organized content
- Current information

### Descriptions Matter
Write descriptions that:
- Explain document contents
- Include key terms
- Note date/version if relevant

❌ Poor: "Manual"
✅ Good: "HR Policy Manual v2.3 covering PTO, benefits, and employee conduct guidelines - Updated January 2024"

## How Agents Use Knowledge

### Retrieval Process
1. User asks question
2. Agent searches knowledge for relevant sections
3. Most relevant chunks retrieved
4. Agent uses this context to respond
5. Citations reference source documents

### What Gets Retrieved
- Text chunks most similar to the question
- Usually 3-5 relevant sections
- Based on semantic similarity

### Citations
Agent responses may include:
```
Based on the Product Manual (page 15), the warranty period is 2 years...
```

## Knowledge vs. Built-in Information

| Knowledge Base | Agent's Training |
|----------------|------------------|
| Your specific documents | General knowledge |
| Current and updateable | Fixed at training time |
| Company-specific info | Public information |
| Authoritative for your org | May be outdated |

### When to Add Knowledge
- Company-specific information
- Proprietary procedures
- Current pricing/catalogs
- Internal policies
- Specialized domain info

### When Not Needed
- Common knowledge questions
- General reasoning tasks
- Creative writing
- Calculations

## Organizing Knowledge

### By Topic
Group related documents:
- All HR policies together
- All product documents together
- All technical specs together

### By Agent Purpose
Tailor knowledge to agent's role:
- Customer Service Agent: FAQs, policies, product info
- Technical Agent: Specs, troubleshooting guides
- HR Agent: Policies, benefits, procedures

### Keep It Focused
- Don't overload with irrelevant documents
- More isn't always better
- Relevant content improves accuracy

## Troubleshooting

### "Document won't upload"
- Check file size (may have limits)
- Verify supported format
- Try different browser
- Check file isn't corrupted

### "Document processing failed"
- PDF may be image-based (need OCR version)
- File may be password protected
- Document may be corrupted
- Try uploading cleaner version

### "Agent doesn't find information"
- Check document contains expected content
- Improve description with key terms
- Verify document status is "Ready"
- Information may be in image (not searchable)

### "Agent cites wrong information"
- Document may have outdated info
- Multiple documents may conflict
- Description may be misleading
- Consider removing conflicting docs

### "Knowledge seems ignored"
- Agent may have found info irrelevant
- Try asking more specifically
- Verify document is processed
- Check agent has knowledge tool enabled

## Common Tasks

### "Add a product manual"
1. Go to Agent Knowledge for relevant agent
2. Write description: "Product manual for [Product] including specifications, usage, and troubleshooting"
3. Upload the PDF
4. Verify processing completes

### "Update outdated document"
1. Delete old version
2. Upload new version
3. Update description if needed
4. Agent now uses current info

### "Check what knowledge agent has"
1. Go to Agent Knowledge page
2. Review document list
3. Note descriptions and dates
4. Remove outdated items

### "Add FAQ document"
1. Compile FAQs into document
2. Format clearly with Q: and A:
3. Upload to agent
4. Description: "Frequently asked questions about [topic]"

## Document Preparation Tips

### For PDFs
- Use searchable/text PDFs
- Avoid scanned image PDFs
- Ensure text is selectable

### For Excel Files
- Use clear headers
- Avoid merged cells
- Include column descriptions

### For Word Documents
- Use headings for structure
- Avoid complex formatting
- Include table of contents for long docs

### For All Documents
- Remove irrelevant pages
- Ensure information is current
- Check for sensitive data before uploading

## Knowledge Limits

### Size Considerations
- Very large documents may take longer to process
- Consider splitting extremely long documents
- Quality of search may decrease with huge volumes

### Number of Documents
- No hard limit, but performance matters
- Focus on most relevant documents
- Review and prune periodically

### Refresh Needs
- Knowledge doesn't auto-update
- Schedule periodic reviews
- Remove and re-add when documents change significantly
