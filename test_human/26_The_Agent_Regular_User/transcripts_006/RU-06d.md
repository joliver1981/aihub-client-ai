# RU-06d

**seat:** ru_drew  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 9.0

## prompt

What documents can I search here?

## tools

- **mcp__aihub__list_documents**
  - input: `{'limit': 25}`
  - result: `You do not have access to any document categories — this is an access restriction, not an empty store. An administrator can grant access on the Groups page.`

## reply

You don't currently have access to any documents in AI Hub's searchable library. This is a permissions restriction — an administrator can grant you access to document categories through the Groups page.

Here's what this means:
- **Documents exist** in the platform, but they're restricted by document type (e.g. vendor invoices, compliance guides, contract templates).
- **Your admin controls access** — they assign document types to your user group, and once assigned, you'll see and search documents of those types.
- **When you get access**, you'll be able to use `search_documents` to answer questions across imported files, and `query_document_records` for structured data like invoice line items or compliance requirements.

**Next steps:**
1. **Ask your administrator** to add your group to the document categories you need.
2. **Once you have access**, come back and ask any question about those documents — I'll search and cite the sources for you.

If you have files you'd like to work with right now, you can also:
- **Upload them in chat** and I'll read/analyze them directly (no import needed)
- **Ask your admin about importing documents** into a shared folder for the team to search
