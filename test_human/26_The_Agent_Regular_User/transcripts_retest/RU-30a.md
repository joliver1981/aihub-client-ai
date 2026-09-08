# RU-30a (retest)

**seat:** ru_alex  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 22.4

## prompt

What can you actually do for me? Give me a list.

## tools

_(none)_

## reply

# Here's what I can actually do for you

## **Explore Your Data**
- List your data connections and find which ones hold a table (one call across every connection)
- Inspect database schemas and run read-only probe queries
- Ask AI Hub's other agents questions and get their answers
- Search the internet for current information

## **Email & Communication**
- **Your agent inbox**: receive mail at your personal AI Hub address, read messages, open attachments, download files from emails
- **Send/draft email**: compose new messages, attach files, embed saved dashboards (Views) as live tiles
- Set up and manage your personal agent email address

## **Access Your Personal Accounts**
- Read YOUR OWN Outlook / Microsoft 365 inbox and calendar (once you connect them at /my-connections)
- List your recent emails, upcoming meetings, your profile

## **Web Portals (Sign In & Automate)**
- Log into vendor/customer web portals with a real browser — download or upload files
- Save portal logins so I never ask you for them again
- Record portal workflows and replay them automatically on a schedule

## **Build Automations & Workflows**
- Create Python automations (single scripts) — write once, schedule to run automatically
- Build code flows (multi-step playbooks) — wire steps together, handle errors
- Dry-run automations against live data/credentials to prove they work before scheduling
- Add human gates (checkpoints) where decisions are needed
- Schedule automations, code flows, and portal workflows on any cadence (daily, every 10 minutes, specific times, etc.)

## **Documents**
- Import documents (PDFs, Word, Excel, scans, images, CSV, TXT) into AI Hub so they're searchable
- Search your document library for answers
- Extract structured records from repeating content (invoice line items, contract requirements, etc.)
- Read and analyze individual files
- Import documents from folders on the server or from integrations

## **Integrations (SharePoint, Stripe, Shopify, APIs)**
- Work with connected external systems — search files, download, upload, execute operations
- (Admins can share integrations with groups)

## **Dashboards & Recurring Numbers**
- Save Views (frozen dashboards with SQL tiles and automation-powered tiles)
- Schedule Views to refresh automatically or email to you on a cadence
- Pin analyses for your team or the whole tenant

## **General Agents (AI Assistants)**
- Create new AI agents with specific tools, knowledge documents, and objectives
- Configure what documents/tools an agent can access
- Share agents with your team
- (Developers/admins can manage agent configuration)

## **Scheduling & Recurring Tasks**
- Schedule one-shot or recurring headless agent sessions (I run a prompt as you, report to My Work)
- Bounded repetition ("every 5 minutes, 12 times" or "for the next hour")
- Schedule anything from absolute times ("tomorrow at 9am") or relative ("in 20 minutes")

## **File Operations**
- Export data to Excel, CSV, JSON, or PDF
- Split, rotate, merge, or extract pages from PDFs
- Download files the portal or an automation produced

## **Procedural Memory**
- Save standing preferences ("always report in Eastern time", "send my summaries to ops@example.com")
- Save skills — procedures and know-how so future sessions start from what you learned

## **One-Offs & Computation**
- Run Python now against a file you uploaded (pandas, numpy, matplotlib included)
- Compute totals, averages, group-bys, deduplication, reshaping — never by eyeball, always with actual code
- Generate images from text descriptions

---

**What I won't do:**
- Guess at live facts when I can search the web
- Run code without proving it first (dry-run before schedule)
- Hard-code credentials (secrets go to the encrypted store)
- Schedule something that hasn't dry-run successfully unless you explicitly insist
- Make destructive changes without your confirmation

**What requires setup:**
- Personal account access (you connect at /my-connections)
- Portal logins (you give them once, I save them)
- API keys / secrets (you paste them, I store them encrypted)
- Agent email address (I can set it up with your permission)

Ready to build something, explore data, or automate work?
