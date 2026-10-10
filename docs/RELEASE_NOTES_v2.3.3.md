# AI Hub v2.3.3

## Build workflows with The Agent

- **The Agent builds visual workflows** — describe a process and The Agent designs it, saves it as a normal workflow that opens in the Workflow Designer, runs a test, reads the results and fixes what didn't work. It reports what each test run actually did (files found, rows written, files moved) rather than just "completed". Requires the Developer role, like the Workflow Designer.
- **Test without touching real data** — The Agent can run a workflow against test folders by overriding its variables for that one run; the saved workflow is unchanged.
- **Schedule from chat** — "run it every weekday at 7am" schedules the workflow in your time zone and reports the next run as calculated by the scheduler. The Agent can also list schedules and cancel one (after you confirm). It never changes or removes an existing schedule: if the workflow already has one, it asks before adding another.
- **The right tool for each kind of "workflow"** — visual workflows, code flows, portal workflows and automations each have their own tools. Given a name that belongs to a different kind, The Agent is told which tool to use instead of acting on the wrong thing.

## AI Workflow Builder

- **Test run** — a Test run button (or type "test it") saves the workflow, runs it for real and passes a run report to the AI, which explains what went wrong and fixes it. No more copying results from the Debug Panel.
- **Run report** — each step's outcomes and real error messages, warnings, files found, loop items, rows written, the workflow's variable values and the last line an automation printed, with silent failures called out (no files found, every item sent to the failure path, a variable that doesn't exist).
- **Exact node settings** — the builder plans from each node's real settings, so it no longer invents settings the engine ignores; if it uses one, it is told and corrects it.
- **Fixed:** variables the builder created were saved without their values; after some replies the previous build was applied again, duplicating every node; the builder could not see failure connections and sometimes "fixed" correct ones.

## Workflow engine and designer

- **Conditional** — expressions over data that contains null, true or false values now evaluate correctly (they used to come out false without explanation), and the run log says why an expression was false or a value could not be resolved.
- **Folder Selector** — several file patterns can be separated by `|`, `,` or `;`; an empty pattern means all files; the pattern field shows for every selection mode; "All files" with nothing matching returns an empty list, so a following Loop runs zero times cleanly. The run log names the pattern that matched nothing.
- **Excel Export** can save its result (status, file, rows written) to an output variable, and both **Excel Export** and **AI Extract** can be set to continue on error.
- **Canvas** — the canvas scrolls again, nodes near the top-left edge stay reachable, and Fit to View and Scroll to Start work.

## Scheduling

- **Cron schedules run in your time zone** — a cron schedule created on the Workflow Monitor page now runs at that time in your browser's time zone (before, "0 0 * * *" ran at midnight UTC, which is 8 pm Eastern). The schedule list shows each cron schedule's time zone, and editing a schedule never changes it.
- **Code flow schedules** created from The Agent or Command Center chat also run in your time zone.

## Solutions

- **Overwrite upgrades an installed solution** — installing a newer version with Overwrite adds the new code as a new version of the existing automation (same automation, schedules and history), keeps your workflow variable values such as folders, and keeps workflow Automation nodes linked. The new version is promoted automatically only when the running version came from an earlier install of the same solution; otherwise it is left for you to dry-run and promote, and the install says which version keeps running.

## The Agent

- The welcome screen's example prompts and the Skills editor's example text now use generic wording.

## Security

- Tightened access to server file routes.

## Upgrade notes

Run the v2.3.3 installer, restart the AI Hub services and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.

Existing cron schedules are not changed and keep running in UTC. To run one in your local time zone, delete it on the Workflow Monitor page and create it again.
