# AI Hub v2.4.2

Includes everything released since v2.4.1.

The theme of this release: The Agent, the General Agent and Command Center now work with what a tool, a document, a query or an e-mail actually returned. Many places cut text or lists at a fixed length, some without saying so, and an answer built on the cut text could be wrong with nothing showing why. Those cuts are removed. Where a real limit remains, the result states it.

## The Agent

- **Full context window** — the current models run with their full 1,000,000-token context window. Before, The Agent worked with 200,000 tokens and summarised the older parts of a long conversation without saying so. The setting `AGENT_CONTEXT_1M=false` brings the smaller window back.
- **Tool results arrive whole** — every tool hands the model its complete result. Removed cuts include: Python output (18,000 characters), an automation's code (4,000), integration and My Connections results (2,500 each), a connection's schema (200 tables, 120 columns), an e-mail body (15,000), an attachment (100,000), web search snippets (400) and an agent's objective (600).
- **Long results come in parts** — one tool result carries at most 500,000 characters, a fixed limit of the runtime The Agent is built on. A longer result is now handed over in parts with nothing left out, and the model reads the following parts itself.
- **Document search returns whole pages** — a passage used to be the first 4,000 characters of its page, and a search showed 12 passages. A search now returns every matching passage whole, best first, sized by the model's context window, and the model can read on in the same ranked list. A file is read in parts in the same way.
- **Data queries show what the query returned** — when The Agent queries a connection it now sees every row that came back, every column and every cell whole. Before, it was shown the first 15 rows while the note said how many "were returned" (up to 50), a long cell was cut at 200 characters and columns after the 25th were missing.
  - It gets 50 rows unless it asks for more, up to 10,000 (`AGENT_QUERY_MAX_ROWS`).
  - "The query returns more than these" is said only when it is true, with what to do next: ask for more rows, let SQL do the counting, or export.
  - `AGENT_QUERY_SIZED=false` brings the earlier behaviour back.
- **Lists show everything** — agents (was the newest 60), an agent's knowledge documents (6 names), pending e-mails (10), matching tables per connection (40), a column's values (12), users (50), recent runs (50), files in a server folder (500) and document types (20) are now listed in full.
- **What an agent is for is shown whole** — The Agent picks which agent to ask from each agent's description. It was the first 220 characters of the agent's objective. `AGENT_ABOUT_CHARS` sets a length again for installs with very many agents.
- **Web search** — up to 20 results per search, 10 by default (was 5, and never more than 10).
- **Inbound e-mail is handed over whole** — the session that handles a mail sent to a user's agent address receives the whole body, the text of every attachment and the user's standing instructions. Before, it received the first 15,000 characters of the body, 10,000 of each attachment, the first ten attachments only, and 2,000 characters of the instructions.
  - One mail may take up to a quarter of the model's context window (about 650,000 characters). Anything beyond that is named in the session with the tool call that reads it.
  - The note filed in My Work carries everything the session said (was 2,000 characters).
- **E-mail attachments are read whole** — reading an attachment no longer stops at 500,000 characters. A text too long for the model to hold in one conversation says so and points to working on the saved file.
- **Automation runs** — after The Agent runs an automation it sees the run's whole output (was the last 800 characters).
- **Exports** — up to 150,000 rows per export (was 100,000). `AGENT_EXPORT_MAX_ROWS` changes it.

## Views

- **A tile keeps the rows its table has** — a table tile is paged on screen 15 rows at a time, with filter, sort and CSV, but the server stopped at 50 rows before paging saw them. A tile now keeps up to 10,000 rows, every column and every cell whole. `VIEW_TILE_ROW_CAP` changes the number (0 = no limit).
  - An automation tile's table no longer has to fit in about 1,900 characters of output.
  - The note that rows were left out appears only when the result really has more rows than the tile keeps. Before, it appeared on most SQL tiles.
  - Large tiles draw quickly: rows past the first page are built hidden.
- **Regular users can refresh a View that was shared with them** — a View's automation tiles could only be refreshed by a Developer or Admin. A regular user's refresh now runs on the standing of the person who saved the View, as long as that person is still a Developer or Admin. Nothing else opens to regular users. `VIEW_VIEWER_REFRESH=false` turns this off.
- **Fixed:** a regular user who opened a shared View saw a red "requires a Developer role" line on every tile, and the time of the saved numbers was shown in UTC. Saved numbers now show with their time in the viewer's own time zone and no error line.
- Two people refreshing the same View at once no longer produces an error on the tiles.
- Dashboard e-mails are unchanged: 15 rows and 8 columns per tile, because mail clients clip long messages. The mail says what it left out.

## Compliance Process node

- **Faster processing** (new option, on by default) — the page-by-page field extraction is left out. It was one AI call per page and the largest part of the time a long manual takes. The agent answers from the page text, and its answers were the same with and without those fields in our tests.
- **Agent knowledge only** (new option, on by default) — a document that goes to an agent is stored as that agent's document from the start, and the general document library's search index is not written for it. This applies only when the run has an agent.
- With both options a 55-page manual went through the whole node in about 2 minutes, where the document step alone took about 18 minutes before.
- The run log states which way each document went ("Page fields: …", "Document library index: …").
- Both options can be unticked on the node. The platform defaults are `COMPLIANCE_SKIP_PAGE_FIELDS` and `COMPLIANCE_SKIP_LIBRARY_INDEX`.

## Document search index

- **Faster indexing** — when a document is added to the search index, its pages are split into chunks several at a time instead of one after another. On a 55-page manual the indexing step went from about 6.5 minutes to a little over 1 minute. A page whose AI call fails is retried once on its own, and anything that still could not be split by AI is counted and logged. `VECTOR_CHUNKING_PARALLEL` sets how many pages at a time (8; 1 = as before).

## General Agent and Command Center

- **Python output up to 500,000 characters** — both get back up to 500,000 characters of what the code they run prints (was 50,000). `CODE_INTERPRETER_MAX_OUTPUT_CHARS` still sets the number.
- **Fixed:** the General Agent's Python tool cut its result a second time at 18,000 characters, with nothing telling the model that the rest was missing.
- Error text from failed code is returned whole. Before, Command Center kept the last 15 lines and the General Agent the last 4,000 characters.
- Command Center shows everything the code printed when the code also produced a file (was the first 8,000 characters).

## Workflow Designer

- **Fixed:** in the Server, Alert, Human Approval, Loop and Set Variable dialogs, the variables button was placed inside a multi-line text box. Fields after it were nested one level too deep (in the Loop dialog, Output Mode only showed with "Use Default Value" selected), the box's default text was the button's markup, and Headers, Description, Approval Data and Default Array had no variables button.

## Automations

- A run can hand back its whole output to a caller that asks for it. Views and The Agent use this. Workflows, schedules and the Automations screen receive exactly what they received before.

## Upgrade notes

Run the v2.4.2 installer, restart the AI Hub services and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.

**Changes in behaviour after the upgrade**

- **Compliance Process node:** existing workflows get both new options without being edited, because a node saved before the options existed takes the platform setting. Documents processed through the node are faster, no longer get page-by-page fields, and are searchable through their agent rather than through the general document library. To keep the earlier behaviour, untick the options on the node, or set `COMPLIANCE_SKIP_PAGE_FIELDS=false` and `COMPLIANCE_SKIP_LIBRARY_INDEX=false` in `.env`.
- **Python output limit:** the installer sets `CODE_INTERPRETER_MAX_OUTPUT_CHARS=500000` in `.env` on upgrade, because every earlier install carries the value 50000 there. If you had chosen another value on purpose, set it again after the upgrade.
- **Cost:** larger results mean more text is sent to the model. A conversation that reads a very large result, a long mail or many rows uses more tokens than before, and for some models the price per token is higher on very long prompts.

**New optional settings** (none has to be added; each is shown with its default)

| Setting | Default | What it does |
|---|---|---|
| `AGENT_CONTEXT_1M` | `true` | The Agent's models use their full context window |
| `AGENT_QUERY_MAX_ROWS` | `10000` | Most rows one data query of The Agent returns (0 = no ceiling) |
| `AGENT_QUERY_SIZED` | `true` | `false` = The Agent's data query as in v2.4.1 |
| `AGENT_ABOUT_CHARS` | unset | Length of an agent's description in listings (unset = whole) |
| `AGENT_EXPORT_MAX_ROWS` | `150000` | Rows per export |
| `AGENT_EMAIL_PROMPT_CHARS` | unset | Characters of one inbound mail handed to its session (unset = by context window) |
| `AGENT_EMAIL_ATTACH_CHARS` | unset | Characters of one attachment handed to an inbound session |
| `AGENT_EMAIL_MAX_ATTACHMENTS` | unset | Attachments whose text is handed to an inbound session |
| `AGENT_EMAIL_ATTACH_MAX_CHARS` | unset | Most characters one attachment read returns |
| `VIEW_TILE_ROW_CAP` | `10000` | Rows a View tile keeps (0 = no limit) |
| `VIEW_TILE_MAX_COLS`, `VIEW_TILE_MAX_CELL` | `0` | Column and cell limits for a tile (0 = no limit) |
| `VIEW_VIEWER_REFRESH` | `true` | Regular users can refresh a View shared with them |
| `COMPLIANCE_SKIP_PAGE_FIELDS` | `true` | Compliance Process node: "Faster processing" |
| `COMPLIANCE_SKIP_LIBRARY_INDEX` | `true` | Compliance Process node: "Agent knowledge only" |
| `VECTOR_CHUNKING_PARALLEL` | `8` | Pages of one document chunked at the same time |

`AGENT_EMAIL_ATTACH_CHARS` and `AGENT_EMAIL_MAX_ATTACHMENTS` existed before with defaults of 10,000 and 10. An install that set them keeps its values, and whatever they leave out is now named in the session instead of dropped.
