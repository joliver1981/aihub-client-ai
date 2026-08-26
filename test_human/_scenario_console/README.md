# AI Hub Scenario Console — `http://localhost:7742`

A control room for the **day-in-the-life scenario packs**: every source a scenario depends on, live;
a one-button batch builder; a data explorer over the seeded book; and every pack action with its
output streamed back.

Built for three jobs:

1. **Run a scenario** without remembering six command lines.
2. **See the state** — what's up, what's seeded, what's sitting on each intake channel.
3. **Demo it** — one full screen that makes the whole setup legible to someone who has never seen it.

```bash
C:\src\aihub-client-ai-dev\test_human\_scenario_console\Start_Scenario_Console.bat
```

Also reachable from the **Demo Control Panel** (`:3100`) — resource *Scenario Console (:7742)*, with
a **Start** action and an **Open console** link. It opens as its own full page rather than inside the
panel, which is the point: this needs the screen real estate.

> **Port 7742** is deliberately clear of everything else on this box: the AI Hub range (5001–5111),
> the builder (8100), the demo control panel (3100), the Vantage supplier portal (3000), SFTP/FTP
> (2222/2121), SQL Server (1433), and the usual dev-server suspects (3000/5173/8000/8080).
> Override with `SCENARIO_CONSOLE_PORT`.

---

## What's on the screen

| Panel | What it gives you |
|---|---|
| **Sources & services** | Live state of all 10: main app, The Agent, Command Center, executor, vector API, builder, **ERPDB and AIRDB with live row counts**, the SFTP server, the Vantage supplier portal. Down services get a **Start** button that actually launches them  |
| **Scenario** | Who the persona is, which verticals it covers, and the pack's docs — readable in-page |
| **Pipeline** | A **live flow** of the whole lineage — book → ERP + documents → channels → process → outcome — with the real counts bound into it. See below |
| **Intake channels** | How many documents are sitting on **SFTP**, in **email**, and in the **watched folder** right now, with the newest timestamp and the real path |
| **Seeded book** | Anchor, seed, scale, and the shape of the batch — exceptions vs decoys vs clean, batch value, blocked value. **Warns when the book has gone stale** |
| **Batch builder** | Anchor + seed + scale → **Build a new batch**. Re-seeds the ERP, renders every document, distributes across all three channels |
| **Actions** | Every pack script as a button, output streamed back. *The output is the evidence — the reply is not* |
| **Data explorer** | The planted exception set, vendors, POs, goods receipts, posted history, AP ledger — straight from the live database |
| **Activity** | What has been run, when, and whether it worked |

---

## The pipeline flow

Not a picture of the architecture — **a live reading of it**. Every number in the diagram is what is
actually on disk or in the database at that moment, drawn fresh from `/api/sources` and
`/api/scenario/<id>/channels`:

```
THE BOOK ──seeds──→ ERPDB · CG*  (12 vendors · 120 POs · 342 goods receipts)
    │                    │
    └──renders──→ DOCUMENTS ──→ SFTP drop      139  ──┐
                                Vendor email    64  ──┼──→ THE PROCESS ──→  44 exceptions
                                Mailroom scans  37  ──┘   ingest/extract/    14 decoys
                                                          match/classify    182 clean
```

**The email node is a real mailbox, not a folder.** It reads The Agent's own inbound ledger
(`data\agent\mywork.db`, read-only) and shows how many of the batch's messages have actually been
*received and processed* — `0 received of 10 due` until you send them. Hovering it gives the mailbox
address and warns when the address has a non-zero cooldown, because that throttles delivery
(james's own address is set to 30 min, which would take four hours to drain ten emails).

Three things it does that a static diagram can't:

- **It reconciles.** Channel nodes show *invoices*, not files, because one `.eml` can carry three of
  them — the email node reads `52 files carrying 64`. The three channels sum to the batch total, so
  a viewer can check the arithmetic on screen.
- **It shows state.** Node borders take the live source colour, so a database that's down or a
  channel that's empty is visible in the flow itself, not just in the tiles above.
- **It animates while work is happening.** Press *Build a new batch* and the connectors flow until
  the job finishes, then the whole diagram redraws with the new seed and the new counts. That is the
  demo moment: one button, and the picture changes to match reality.

The flow is built from the scenario's own `channels` list, so a scenario with one channel draws
correctly without any code change. Scenarios that aren't built yet render "nothing flowing".

Hovering a node gives the real filesystem path and the newest file's timestamp.

---

## The batch builder

The button that matters. One press does the whole chain:

```
seed_ap_book.py  --anchor <date> --seed <n> --scale <n>      re-seed ERPDB
make_fixtures.py --anchor <date> --seed <n> --scale <n> --distribute
                                                              render + fan out
```

| Control | Effect |
|---|---|
| **Seed** | `random` mints a **genuinely different batch** — different vendors on different POs, different invoices carrying the exceptions. The *shape* is invariant: always 44 exceptions, 14 decoys. Type a number to reproduce a past batch exactly |
| **Anchor** | The date the book is a snapshot of. Seed on the day you run — discount windows and aging slide as it ages |
| **Scale** | 1 = 240 documents. 8 = ~1,920, for a stress run |
| **Re-render current batch** | Same seed, rebuild the documents — use after clearing a channel by hand |

A scale-1 build takes about **40 seconds**, most of it rendering the 38 scanned image PDFs.

---

## Adding a scenario

`scenarios.json` is the whole configuration — no code changes.

```json
{
  "id": "deductions",
  "name": "Deduction & Chargeback Desk",
  "role": "Deduction Analyst",
  "verticals": ["Wholesale"],
  "status": "built",
  "pack":    "test_human\\17_Business_Role_Scenarios\\Deductions",
  "scripts": "test_human\\17_Business_Role_Scenarios\\Deductions\\_scripts",
  "docs":     [{ "label": "Setup", "path": "00_Setup.md" }],
  "channels": [{ "id": "email", "name": "Retailer claims", "kind": "dir",
                 "path": "data\\deduction_intake", "glob": "*.pdf" }],
  "actions":  [{ "id": "check", "label": "Run checks", "tone": "quiet",
                 "script": "check.py", "args": ["all"] }],
  "explorer": [{ "id": "claims", "label": "Open claims", "database": "ERPDB",
                 "sql": "SELECT TOP 200 * FROM dbo.CG_Deductions" }],
  "batch_builder": { "enabled": true, "steps": [ ... ] }
}
```

Source `kind` is one of `http` · `tcp` · `dir` · `sql`. A `sql` source can carry `metrics` — labelled
scalar queries that render as live counts on its tile. Restart the console to pick up changes.

### Making a source startable

A **Start** button appears on a down source only when it has a `start` block the backend can
actually run — not merely a `fix` hint. Two forms:

```json
"start": { "command": "C:\\src\\aihub-test-portal\\start.cmd" }
"start": { "python": "testftp", "script": "test_human\\_sftp_test_server\\run_all.py" }
```

`.cmd` / `.bat` launchers go through `cmd.exe /c`; anything else is executed directly. Either way
the server gets its own console window, and the console re-checks the source at 3s, 7s and 12s. A
missing launcher fails loudly rather than pretending to start.

> **Two portals both want port 3000.** `C:\src\aihub-test-portal` is the Node **Vantage Supplier
> Portal** (the one wired here — any username/password, 2FA `123456`).
> `test_human\_portal_test_server` is an older Python "Meridian" portal on the same port. Run one or
> the other, never both. The Demo Control Panel's own `svc-meridian` resource still points at the
> Python one.

Scenarios with `"status": "planned"` show as cards with no actions — useful for showing where the
roadmap goes without pretending it exists.

---

## Notes

- **Read-mostly.** The console runs the packs' own scripts; it has no SQL write path of its own.
  Destructive actions (teardown, clean) are marked and ask for confirmation.
- **Credentials** live in `scenarios.json` under `settings.sql` — the same on-prem test credentials
  every other tool on this box uses. Local test databases; nothing sensitive.
- **Bind address is `127.0.0.1`.** It is a local ops console, not a service.
- **Jobs cap at 30 minutes.** A scale-8 build takes about five.
