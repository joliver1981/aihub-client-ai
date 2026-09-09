# RU-20b (retest)

**seat:** ru_casey  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 8.9

## prompt

What skills do I have available? List them with their scope.

## tools

- **mcp__aihub__list_skills**
  - input: `{}`
  - result: ``

## reply

Here are your available skills, all at **product scope** (available to everyone on the platform):

| Skill | Purpose |
|-------|---------|
| **aihub-agent-builder** | Create, configure, share, or delete General Agents (chat assistants) |
| **aihub-document-ingestion** | Import and ingest documents (PDFs, scans, Office files) for searching and answering questions |
| **aihub-integrations** | Work with external systems like SharePoint, OneDrive, Shopify, Stripe, Azure Blob, and custom APIs |
| **aihub-platform-navigation** | Navigate AI Hub features — where things live, how to use them, which tool fits your task |
| **aihub-playbook-lifecycle** | Build, change, schedule, and debug automations and code flows |
| **aihub-portals** | Download from or upload to websites that need login (vendor/customer portals, statements, invoices) |
| **aihub-rich-output** | Create charts, KPI cards, maps, and generated images to make answers clearer |
| **aihub-secrets-and-credentials** | Safely store and reference API keys, passwords, tokens, and other credentials |
| **aihub-views-dashboards** | Create dashboards, recurring number pulses, and pin analyses you can revisit |

All of these are **product-level** skills, meaning they're available to all users on your AI Hub instance. You can invoke any of them by name when you need help with those topics.
