# RU-11a

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 9.1

## prompt

What external systems is AI Hub connected to that I can use?

## tools

- **mcp__aihub__list_integrations**
  - input: `{}`
  - result: `Integrations available to this user (1): | - id 1 — AI Hub SharePoint TestGOOD_ (SharePoint Online (Service Account), connected, groups [2]) | (Showing only integrations assigned to your groups.)`
- **mcp__aihub__list_mcp_servers**
  - input: `{}`
  - result (FAILED): `MCP server inspection is available to Developer/Admin users only.`

## reply

You have access to **one external system** right now:

**📁 SharePoint Online** (AI Hub SharePoint TestGOOD_)
- A Microsoft SharePoint instance you can search, download files from, and upload files to
- The platform handles all authentication server-side — you never need credentials

With this integration, you can:
- **Search files** in SharePoint document libraries
- **Download** files to bring them into AI Hub
- **Upload** files to SharePoint from AI Hub
- **Ingest SharePoint documents** into the searchable document repository

### How to use it

When you ask me to work with SharePoint — "download my invoice from SharePoint", "search for contracts in the Docs library", "upload this report to SharePoint" — I'll use this integration to fetch or deliver files. I can also import SharePoint documents so they become searchable across AI Hub (great for compliance guides, policies, vendor contracts, etc.).

**Want to know more?** I can walk you through:
- How to search and download files from SharePoint
- How to upload files there
- How to set up a standing pipeline to ingest new documents automatically
- How to share the integration with your team

Would any of those be helpful?
