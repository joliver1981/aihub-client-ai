# RU-30a

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 23.6

## prompt

What can you actually do for me? Give me a list.

## tools

_(none captured; names seen: [])_

## reply

# What I Can Do For You

## **Explore & Analyze Your Data**
- **Query databases**: Run read-only probes against any connection (SQL Server, Snowflake, etc.) to verify assumptions, answer questions, and spot patterns
- **Find data**: Search across EVERY data connection at once to locate a table you need (search_tables)
- **Inspect schemas**: Explore table structures, column types, and sample values before you query
- **Ask existing agents**: Delegate questions to your organization's SQL-bound Data Agents or other General Agents
- **Search documents**: Find answers across imported PDFs, Word docs, Excel files, and scans you've stored in AI Hub

## **Build Automations & Workflows**
- **Automations**: Write Python scripts that run on a schedule with live database access, email sending, human checkpoints (someone reviews and approves in My Work), and API calls — zero tokens per run after it's proven
- **Code flows**: Multi-step playbooks where steps can pass data to each other; fix failing steps in place rather than rebuilding
- **Schedule them**: Cron or simple intervals; recurring or one-off; bounded repeats ("every 10 min for the next hour"); absolute times ("tomorrow at 3pm") in YOUR timezone
- **Prove it first**: Always dry-run against real data with real credentials before promoting to production

## **Work with Files**
- **Import documents**: Bulk-import folders of PDFs, scans, Word, Excel, CSV — they become searchable and AI-readable; idempotent (re-run safely)
- **Read any file**: Open a PDF, Word doc, Excel sheet, image, or text file in one call — no storing, no indexing, just "what's in this file?"
- **Download from the web**: Log into vendor/customer portals with a real browser and fetch files deterministically (RPA)
- **Upload to portals**: Send files through web forms where you need to authenticate
- **Save portal logins**: Remember a portal's URL and credentials so you only give them once
- **Export data**: Turn query results or chat attachments into Excel, CSV, JSON, or PDF
- **Manipulate PDFs**: Extract pages, split, rotate, merge multiple PDFs

## **Send & Receive Email**
- **Send from your agent address**: Emails to any recipient; they're auto-sent if you're a developer, or filed in My Work for regular users to approve. Attach files or embed a live dashboard (View) that refreshes at send time
- **Receive at your agent address**: People email YOU at your personal agent address; those messages land as sessions in your My Work
- **Read your inbox**: Open inbound mail, read attachments (with OCR for scans), save them to the server
- **Read YOUR personal Outlook/Microsoft 365**: After you connect it, I can check your real inbox, calendar, meetings (with your permission as yourself)

## **Search the Web**
- **Current information**: News, weather, prices, product releases, documentation, company facts — anything time-sensitive or beyond my training data
- **Cite sources**: Results come with links so you can verify

## **Create & Configure Agents**
- **Build General Agents**: Chat assistants with their own objectives, tools (web search, data queries, document reading, etc.), and knowledge documents
- **Add tools**: Give an agent access to web search, data connections, specific integrations, email, or custom tools
- **Upload knowledge**: Attach a specific PDF, doc, or scanned invoice so an agent can answer about it
- **Restrict document access**: An agent can read only certain document types (e.g., invoices only)
- **Share with groups**: Make an agent available to your team or department

## **Integrate External Systems**
- **SharePoint**: List, search, and download files; upload documents
- **Shopify, Stripe, custom APIs**: Execute operations the platform has configured (e.g., look up orders, fetch transaction data)
- **Admin sharing**: Restrict integrations to specific user groups

## **Recurring Work & Dashboards**
- **Views (dashboards)**: Pin a SQL query or a promoted automation as a deterministic tile. Refresh it manually or on a schedule; email it to stakeholders on a cadence (all zero AI per refresh — no surprises)
- **Scheduled agent tasks**: Run a prompt as you every morning/evening/interval, results land in My Work and this conversation
- **Scheduled portal workflows**: Replay a recorded browser sequence (login + download/upload) on a schedule, outputs land as FYIs in My Work

## **Store & Use Secrets Safely**
- **API keys & passwords**: Hand me a credential once; it's encrypted server-side and never echoed back. Automations reference it by name (e.g., SENDGRID_API_KEY) and the server injects it at runtime — no hard-coding, no leaks

## **Save Your Know-How**
- **Skills**: Document a process you've solved (a data model's quirks, a client convention, a workflow) so future conversations start from that know-how instead of rediscovery
- **Preferences**: Save standing defaults ("always answer in Eastern time", "send my reports to ops@...", "my team is the Analysts group") and they stick across all your conversations and scheduled runs

---

**The rule I follow**: Ground everything in tool results from this conversation. Never invent data, schema, schedule IDs, or outcomes. Report exactly what happened — "awaiting approval" not "sent", "still running" not "done", "not found in connections A, B" not "doesn't exist".

**What do you want to do?**
