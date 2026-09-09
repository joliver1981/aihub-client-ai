# RU-30b (retest)

**seat:** ru_drew  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 16.3

## prompt

What can you actually do for me? Give me a list.

## tools

_(none)_

## reply

# What I Can Do

## **EXPLORE YOUR DATA**
- List your data connections and see what databases are available
- Find which connections hold a specific table (search all at once)
- Inspect database schemas — see tables, columns, sample values
- Run read-only probe queries to verify assumptions before you build
- Ask AI Hub's other agents (Data Assistants) questions about their databases

## **WORK WITH DOCUMENTS**
- Import PDFs, Word, Excel, scans, and images into AI Hub so they're searchable
- Answer questions about your imported documents by searching them
- Extract structured data from documents (invoices, requirements, line items, etc.)
- Read any single file you point me at and summarize or compute over it

## **BUILD AUTOMATION & WORKFLOWS**
- **Automations**: Single Python scripts that run on a schedule with live credentials
- **Code Flows**: Multi-step Python playbooks where steps connect in sequence
- **Portal Workflows**: Record yourself logging into a web portal and downloading/uploading files, then replay it on a schedule deterministically
- Full lifecycle: draft → dry-run (real execution) → promote → schedule
- Human checkpoints: pause a run for you to approve/deny before it proceeds

## **SCHEDULE RECURRING WORK**
- Schedule automations to run on a cadence (cron, hourly, daily, etc.)
- Schedule code flows to run automatically
- Schedule Views (dashboards) to refresh and email you on a schedule
- One-shot delayed tasks ("check my email in 2 hours", "run this at 3pm tomorrow")
- Bounded repetition ("every 5 minutes for the next hour", "12 times")

## **DASHBOARDS & REPORTING**
- Save Views (deterministic dashboards) with SQL tiles or automation tiles
- Refresh them manually or on a schedule
- Email Views as live dashboards to yourself or others
- Share Views with your team or company-wide

## **SEND & RECEIVE EMAIL**
- Set up your personal agent email address (people can mail you there)
- Read and search your agent inbox
- Open email attachments, extract their text, or save them to the server
- Send emails to people or groups — with or without dashboards, files, or links

## **READ YOUR OWN ACCOUNTS** (if connected)
- Check YOUR Outlook inbox, search your mail
- See YOUR calendar and upcoming meetings
- Schedule recurring tasks that check your inbox/calendar on a cadence

## **INTEGRATIONS**
- Work with SharePoint, OneDrive, Shopify, Stripe, Azure, and other connected systems
- Search, download, or upload files through them
- Ingest data from APIs and external platforms

## **WEB & PORTALS**
- Search the internet for current information (news, prices, documentation, etc.)
- Log into web portals with a real browser — download or upload files
- Save portal logins so you only share them once
- Recorded portal workflows replay a saved login sequence automatically

## **CREATE & SHARE AGENTS**
- Build General Agents (chat assistants on the Assistants screen)
- Give them specific tools, restrict document access, upload knowledge
- Share them with your team or entire company

## **REMEMBER & OPTIMIZE**
- Save your standing preferences ("call me Jim", "always use Eastern time", "send reports to ops@...")
- Save procedural knowledge as skills so I start from know-how, not rediscovery
- Store credentials safely in encrypted secrets

---

**The rule**: Everything I build is deterministic and repeatable — if you like an analysis, we save it as a View; if you need something to happen on a schedule, we build an automation. Zero guessing, everything verified.

What would you like to do?
