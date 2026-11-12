# AI Hub v2.3.4

## Workflow file drops

- **Files in, results out, from a browser** — a workflow can be set up with its own managed folders (Waiting, Results, Failed, Processed). People drag and drop files into the new **Workflow Files** page, then download the results, retry a failed file or remove a waiting one. A run can start right away or as soon as files arrive, and recent runs are listed with their results. The Workflow Designer has a new **Files** button.
- **Set-up and sharing (Developers)** — setting a workflow up points its folder variables at the managed folders, saved like a Designer edit. Developers share a workflow with people and groups and can choose a SharePoint folder for copies of the results. Regular users see only the workflows shared with them.
- **Notices in My Work** — when a run ends, whoever started it and the people whose files it processed get a My Work notice with download links. The owner hears about runs nobody started. Runs requested while one is going start next, so one run at a time works through the inbox.
- **Email attachments** — when an email agent's address triggers a workflow with file drops, the attachments are saved into its Waiting folder before the run. Mail that arrives during a run is picked up by the next run.
- **Run report** — a new Files section, with warnings for files left waiting and files taken with nothing produced.
- The folders can live on another drive or a share (`WORKFLOW_FILES_ROOT`). Kill switch: `WORKFLOW_FILE_DROPS_ENABLED`.

## The Agent

- **Run attached files through a workflow** — "run these invoices through the invoice workflow" with attachments adds them to the workflow's Waiting folder, runs it and returns the results as download links in the chat. If the wait runs out, the results arrive in My Work. When The Agent builds a workflow that reads a folder of files, it sets up file drops instead of asking for a server path. A new **Files** entry in the menu mirrors the Files page. Kill switch: `AGENT_WORKFLOW_FILE_TOOLS`.
- **Optional model code names** — the new `AGENT_MODEL_CODENAMES` setting (comma-separated `real-id:code-name` pairs) shows a code name wherever The Agent showed a model id, for Admins too. Only the display changes: API calls, saved settings and logs keep the real ids. Set it to `off` to show the real ids.
- **Product skills for admins only** — the built-in platform skills are listed on the Skills page only for Admins. Everyone else sees their own skills, their groups' skills and the tenant's skills. The Agent still uses the product skills in every session.
- **Fixed:** after a page reload, the next message went into the previous conversation while the welcome screen was showing, so two chats became one. The page now shows the conversation it continues.
- **Fixed:** replayed messages with attachments showed an internal note with server paths. They now show the user's words and the file names.
- **Fixed:** chat downloads were sometimes saved as "download" with no extension. They now keep the file's real name.

## Agent Chat

- **Fixed:** the paperclip opened the file picker but never uploaded the files you picked. Drag and drop was not affected.

## Bring your own key (BYOK)

- **Every service uses your own keys directly** — with BYOK on, every service calls OpenAI and Anthropic with your keys and nothing goes through the cloud relay. Before, some OpenAI calls from the Agent API, Executor and Knowledge API went through the relay. Document search, category assignment, workflow document extraction and quick prompts used the cloud proxy. Voice transcription, text-to-speech and the Automations AI step used the platform's keys.
- **One BYOK switch for every service** — all services read the switch from the same data folder. Command Center and the builders now see it. An upgraded install that already had BYOK on keeps it in every service.
- **Fixed:** Command Center chats failed with "I ran into a problem handling that request" on a BYOK OpenAI key.
- With BYOK off nothing changes. After turning BYOK on or off, restart the AI Hub services.

## Automations

- **Review-only runs don't block the next run** — a run can mark that its intake is done and it is only waiting for review decisions (`aihub.waiting_for_decisions()`). The next scheduled run then starts instead of being skipped. Runs that don't mark this keep the old skip-if-running behaviour.
- **Fixed:** a review batch of more than 200 items never saw its decisions.

## Workflow engine

- **Excel Export** — the first append to a workbook that doesn't exist yet creates it (from the template if one is set) instead of failing with "Cannot append - file does not exist".

## Documents

- **Fixed:** an upload could fail with "String or binary data would be truncated" when the classifier answered with a sentence instead of a document type. Such a document is now stored as an unknown type.

## Models

- **Newer OpenAI models** — calls to models that accept only their default temperature (such as gpt-6-luna and gpt-6.1-sol) are retried once without it, and later calls leave it out. Kill switch: `OPENAI_DROP_REJECTED_TEMPERATURE=false`.
- gpt-5.6-sol is no longer offered in the model override dropdowns, because it failed every call.

## Security

- Sign-in is now enforced on every route for fresh and upgraded installs.
- Users can no longer read or delete another group's skills.
- The login page no longer loads scripts meant for signed-in users.

## Other

- The assistant button no longer covers the Data Explorer send button.

## Upgrade notes

Run the v2.3.4 installer, restart the AI Hub services and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.

The installer adds `AGENT_MODEL_CODENAMES` to an existing `.env` when it is missing. A box that already has its own value keeps it.
