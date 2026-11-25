# AI Hub v2.4.1

Includes everything released since v2.4.0.

## The Agent

- **My Connections screen** — The Agent's left rail has a new **My Connections** entry for every user. It lists the accounts an Admin has made available (for example Microsoft 365), shows whether yours is connected and what the sign-in lets AI Hub do, and has **Connect**, **Reconnect** and **Disconnect** buttons. Until now a user could only connect an account from a page in the classic app.
  - **Connect** opens AI Hub's sign-in for that account in a separate window. Close it without finishing and the screen says the account is not connected.
  - The screen states what the assistant may do with a connected account: it reads through it and cannot send or change anything as you, unless an Admin has allowed named tools. It also says this is not Agent Email.
  - If a different user is signed in to AI Hub in the same browser, Connect stops with a message and connects nothing, so an account cannot be attached to the wrong person.
  - When no account type has been made available, the screen says so. Admins get a link to **MCP Servers**, where one is published.
  - When the assistant needs an account that is not connected, it now gives an **Open My Connections** link that opens the screen.
  - The screen follows the existing switch `AGENT_MY_CONNECTIONS` (on by default). With it off, the rail entry is hidden and the Platform menu keeps its link to the classic page.
- **Sign out** — the rail has a **Sign out** button under "signed in as". It clears what The Agent holds in the browser (the sign-in, the open conversation and any unsent draft) and signs you out of AI Hub, for local, directory and single sign-on accounts alike. Other open tabs of The Agent are signed out with it, and reload, Back or a direct visit stop on a "You're signed out" screen until you sign in again.
- **Logout in the classic app signs The Agent out too** — before, The Agent went on working in the same browser until its sign-in expired.
- Both sign-outs clear the browser. A sign-in token that was copied out of the browser beforehand stays valid until it expires.
- **Fixed:** on installed copies the version badge in The Agent's header was blank. It now shows the platform version.

## Workflow Designer

- **Zoom and pan the canvas** — zoom buttons at the top right of the canvas (zoom out, the current percentage, which resets to 100%, and zoom in), and **Ctrl + scroll** zooms around the cursor. Dragging the empty background pans. Dragging nodes, drawing connections and dropping a node from the toolbox work at any zoom. Saved workflows are unchanged.
- **Fit to View** now zooms out until every node is in view, allowing for the part of the canvas the debug panel covers.
- **Fixed:** in the **Compliance Process** node, the settings below the Agent Objective Template (Agent Override, Replace older versions, Document Attributes, Excel Template Path and Output Variable) were hidden whenever Routing Mode was **Fixed**.

## Workflows

- **Fixed:** a **Conditional** step that came out FALSE with nothing connected on its false side was marked Failed and failed the run. The condition was evaluated, so the step is now Completed and that branch ends there. A condition that cannot be evaluated still fails.

## Automations

- **AI calls from automations take the same route as the rest of the platform** — the customer's own Anthropic key when one is switched on under API Keys, otherwise the AI Hub cloud service, like AI Extract and the agents. Before, `aihub.llm` and `aihub.ai_extract` went straight to Anthropic with the platform's key, so an automation's AI step could stop with an Anthropic account error while every other AI feature kept working.
  - Each call's line in the app log names the route it took (`cloud service`, `direct, customer key` or `direct, platform key`).
  - When the cloud service cannot be reached the step fails with a message that says so. No other key is tried.
  - The setting `AUTOMATIONS_AI_ROUTE=direct` in `.env` brings the earlier behaviour back.
- **Fixed:** when the AI's reply to an automation could not be read as JSON, the question was asked again from scratch. The second reply could be shorter and still valid, so items were dropped without an error. The unreadable reply is now sent back to the model to be re-sent as valid JSON with nothing dropped, and a reply written as several separate JSON objects is joined into one. The response field `json_repair` and the app log say when this happened.

## Email (Microsoft 365)

- **Fixed:** after an Admin granted consent in Microsoft Entra, sends kept failing with 403 for up to about 90 minutes or until a restart. The saved token is now replaced once when Microsoft refuses it, so sends work straight after consent.
- **Fixed:** a changed client secret was not used until the saved token expired. It is now used on the next send.
- **Email Settings** — Save and Revert drop the saved token, and **Send test** always starts from a fresh one, so the test proves the settings as they are now.

## My Connections (classic page)

- The page now says when your sign-in is used: for what you ask an agent to do, including routines you scheduled. It used to say "only while you're logged in".

## Upgrade notes

Run the v2.4.1 installer, restart the AI Hub services and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.

No setting has to be added for an existing install. After the upgrade every user of The Agent sees **My Connections** in the rail. It shows "Nothing to connect yet" until an Admin makes an account type available to users.

Automations that use AI now reach it through the AI Hub cloud service unless the customer's own Anthropic key is switched on. The cloud service address is the one AI Extract already uses (`AI_HUB_DOCUMENT_API_URL` and `AI_HUB_PROXY_ANTHROPIC_MESSAGES` in `.env`), so an install where AI Extract works needs no change.
