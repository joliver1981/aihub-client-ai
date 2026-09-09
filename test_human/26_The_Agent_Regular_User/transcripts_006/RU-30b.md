# RU-30b

**seat:** ru_drew  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 20.1

## prompt

What can you actually do for me? Give me a list.

## tools

_(none captured; names seen: [])_

## reply

# What I Can Do For You

## **Explore & Query Data**
- List your data connections and inspect their schemas (tables, columns, sample values)
- Run read-only probe queries to spot-check data before committing to bigger work
- Search across every connection at once to find which one holds a table you need
- Ask AI Hub's configured agents questions (they have their own data access and knowledge)

## **Read Your Personal Accounts** *(if you connect them)*
- Access your own Outlook/Microsoft 365 inbox and calendar
- List recent emails, upcoming meetings, your profile info
- Build recurring tasks that check your calendar or inbox

## **Search & Work with Documents**
- Search the internet for current information (news, prices, releases, documentation, etc.)
- **Import documents** into AI Hub (PDFs, Word, Excel, images, scans) so they become searchable
- **Answer questions** about your imported documents by searching them
- Extract structured data from documents (a guide's requirements, an invoice's line items, etc.)

## **Sign Into Web Portals & Download/Upload Files**
- Log into vendor/customer portals with a real browser and download files
- Upload files to portals
- Save portal logins so I never ask for credentials again
- Run recorded portal workflows on a schedule (deterministic replays)

## **Build Automations & Code Flows**
- Write **single-script automations** (Python) that do one repeatable task
- Build **multi-step code flows** (playbooks) that chain steps together
- **Dry-run** them first with real credentials and live data to prove they work
- **Promote** proven versions and **schedule** them to run automatically
- Edit, fix, or delete automations after dry-running

## **Send & Receive Email**
- Send emails from my agent address to anyone
- Check email sent TO me (my agent mailbox)
- Open emails, read them, extract attachment text, download attachments
- Embed live dashboards/Views in emails

## **Create AI Agents** *(the chat assistants on your Assistants screen)*
- Create new General Agents with specific objectives
- Give them tools (web search, document access, calculation, etc.)
- Connect them to specific documents or knowledge files
- Share them with groups so teammates can use them

## **Build & Share Dashboards**
- Save analyses as **Views** — pinned, deterministic dashboards
- Schedule Views to refresh automatically or email on a cadence
- Share them with groups
- Embed live Views in emails (the numbers refresh at send time)

## **Analyze Data Right Now**
- Run Python immediately for computations, charts, data cleaning, reshaping
- Work with CSV/Excel files you upload in chat
- Generate images from text descriptions
- Export results to Excel, CSV, PDF, or JSON

## **Manage Credentials & Preferences**
- Store API keys, passwords, and tokens securely — I refer to them by name only, never in code
- Save **skills** — procedural know-how that future sessions use (processes, gotchas, models)
- Save **standing preferences** — your defaults (timezone, names, where to send reports, etc.)

---

**What I won't do:**
- Hard-code credentials (the system rejects them)
- Guess at data or make claims I can't ground in a tool result
- Modify your data without a human checkpoint in place
- Schedule something without testing it first

**Start with what's on your mind.** What data do you need to see? What's repetitive work you'd automate? What information do you want to track? I'll dig in and build it for you.
