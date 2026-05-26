# Solutions Gallery (/solutions/gallery)

The Solutions Gallery is where you install pre-built *solutions* — bundles of agents, workflows, knowledge files, tools, and configuration that ship together as a single unit. Think of it as an app store for the platform: pick a solution, install it, and the included agents/workflows show up ready to use.

## What a Solution Is

A solution is a packaged bundle (a `.zip` file) that can include any combination of:
- One or more agents (custom agents, data agents)
- Workflows and their schedules
- Knowledge files and document sets
- Custom tools and integrations
- Sample data
- Configuration

Solutions exist so that a working capability — say, "Vendor Compliance" or "Customer Support Triage" — can be authored once and installed cleanly across many environments or customers, without manually rebuilding each piece.

## Page Layout

### Header
- **Title and subtitle** — "Solutions Gallery", with a short description.
- **Upload Solution** — pick a `.zip` solution bundle from disk to install.
- **Author** — open the Author page where you build your own solution from existing assets in this environment.

### Main Area
Three states:
- **Loading** — spinner while the gallery fetches the available solutions.
- **Empty** — a message telling you no solutions are available yet, with a hint to go to the Author page.
- **Populated** — a grid of solution cards. Each card shows the solution's name, a short description, its version, and whether it is already installed in this environment.

Click a card to open the install wizard (for solutions you haven't installed yet) or the details/upgrade view (for already-installed solutions).

## Common Tasks

### Install a solution from the gallery
1. Click the card of the solution you want.
2. The Install Wizard opens. Walk through the steps — typically:
   - Review what will be installed (agents, workflows, etc.).
   - Map any environment-specific values (connection IDs, credentials).
   - Confirm and install.
3. After install, the included agents and workflows are available on their respective pages (Custom Agents, Workflow Designer, etc.).

### Install a solution from a `.zip` file someone shared with you
1. Click **Upload Solution**.
2. Pick the `.zip` from disk. The install wizard opens with that bundle preloaded.
3. Walk through the wizard as above.

### Build your own solution
- Click **Author**. The Author page lets you pick existing agents, workflows, and assets in the current environment, bundle them into a solution, and export the `.zip` for use elsewhere.

### Upgrade an installed solution to a newer version
- If a newer version of an installed solution is in the gallery, the card indicates an upgrade is available. Click it to open the upgrade flow.

## Troubleshooting

- **Gallery stays in "Loading" state** — the solutions registry endpoint isn't responding. Reload the page. If it persists, check that the main app is healthy.
- **Upload rejects the file** — confirm it's a real solution bundle (`.zip` produced by the Author page). Random ZIPs won't be accepted.
- **Install wizard says a connection or credential is missing** — solutions can require environment-specific config that doesn't ship in the bundle. The wizard will tell you which connection/credential is missing; create it on the Connections page, then re-run the install.
- **Installed agents don't appear** — refresh the relevant page (Custom Agents, etc.). If they still don't show up, check the install wizard's final summary for errors.

## What This Page Is NOT

- It is **not** the Author page — building a new solution happens there.
- It is **not** where solution authors are listed — see Solutions Author for that.
- It is **not** the installed-software list — installed solutions are visible here (badged) but managing the individual pieces (the agents, the workflows) happens on each piece's own page.
