# RU-20b

**seat:** ru_casey  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 8.2

## prompt

What skills do I have available? List them with their scope.

## tools

- **mcp__aihub__list_skills**
  - input: `{}`
  - result: ``

## reply

Here are your available skills with their scopes:

**Product scope** (available platform-wide):
- **aihub-agent-builder** — Create, configure, share, or delete General Agents on the Assistants screen
- **aihub-document-ingestion** — Import/ingest documents (PDFs, scans, Office files) for search and Q&A
- **aihub-integrations** — Work with external systems (SharePoint, OneDrive, Shopify, Stripe, APIs)
- **aihub-platform-navigation** — Navigate AI Hub's interface and understand which tool fits your task
- **aihub-playbook-lifecycle** — Build, change, schedule, or debug automations and code flows
- **aihub-portals** — Download/upload files from web portals, save logins, run recorded portal workflows
- **aihub-rich-output** — Create charts, KPI cards, maps, inline pictures, and generated images
- **aihub-secrets-and-credentials** — Handle API keys, passwords, tokens, and secure credential storage
- **aihub-views-dashboards** — Build dashboards, recurring numbers, pulses, and scheduled refresh

**Tenant scope** (your organization):
- **ru-pack-fixture** — Fixture skill for the regular-user test pack (RU-21)

The product skills are available to you always; the tenant skill is specific to your organization's test setup.
