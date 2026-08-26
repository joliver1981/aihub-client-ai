# AI Hub v1.8.1

> Maintenance release · July 31, 2026
> Focus: reliability and security fixes on top of the July Automations & Approvals update, plus a new File Transfer workflow node.

---

## ✨ New

- **File Transfer workflow node (SFTP / FTP / FTPS).** Upload and download files from remote servers as a native workflow step — server, folder, and file patterns configured in the node, credentials supplied through platform secrets (never stored in the workflow itself).
- **Build with AI from the workflow designer.** The Automation node now has an inline drawer to describe and build a new automation without leaving the canvas — same guided pipeline (dry-run, then promote) as Command Center.
- **System Prompts admin screen.** Administrators can view, search, and edit every AI prompt the platform uses from Settings → System Prompts. Edits are stored as overrides on top of the shipped defaults and apply after a service restart.
- **Automations can send email.** Automation scripts can notify people directly via the platform's mail service — no SMTP credentials or mail code inside the script.

## 🛠️ Improvements

- **Document search quality.** The retrieval engine behind agent document search has been overhauled and is now the default: semantic search finds meaning (not just keywords), results are re-ranked for relevance, audit-style questions sweep the full document set, and lookups return citation-ready sources. Verified on a 120-document corpus with measurably better recall than the previous engine.
- **Solutions Author exports automations.** Solution bundles can now include automations end-to-end; workflow references stay portable and are remapped automatically on install.
- **Automation review flow is decisive.** Runs waiting on a review decision act on it promptly, and no run can linger unresolved past 7 days.
- **Workflow debugger honesty.** A failed Automation node shows the script's actual error output instead of just "exit code 1", with a one-click **Fix with AI** to repair and re-try.
- **Command Center routing.** Referencing an agent by its ID ("use agent 281") now reliably routes to that exact agent — with an honest error if the ID doesn't exist.

## 🐞 Fixes

- **Workflow Database node: real error messages.** Database failures no longer surface as a generic "Unknown database error" — the node reports the actual database error so it can be fixed on sight.
- **"Test Connection works but the workflow fails."** Editing a database connection without re-typing the password could corrupt its stored connection string, so tests passed while workflows and agents failed to authenticate. Saving now repairs the stored string automatically, and previously affected connections self-heal on their next use — no re-entry needed.
- **Database → Excel Export.** Pairing a Database node with Excel Export now works; the export accepts the Database node's result format.
- **Excel append.** Appending rows to an existing sheet no longer leaves a blank row under the header.
- **AI Extract.** Repeated groups and array/object fields survive extraction instead of being dropped; malformed model responses are recovered where possible, and skipped sections are reported instead of silently missing.
- **Document ingestion.** Pages without embedded images are no longer stored blank during ingestion — previously a silent gap in search coverage.
- **Command Center workflow authoring.** Workflows built through chat no longer produce silently empty node outputs due to a configuration naming mismatch.
- **Workflow designer.** Node configuration dialogs no longer break when a config value contains quotes.

## 🔒 Security

- **Connection strings no longer expose passwords.** The Connections screen previously received stored connection strings verbatim — including embedded passwords on older connections. Password values are now masked before leaving the server, and newly saved connections keep a secure secret reference at rest instead of the password itself.

---

## Upgrade notes

- Restart the AI Hub services after updating and hard-refresh open browser tabs (Ctrl+Shift+R).
- The installer now bundles the automation runtime SDK and SFTP/FTP support, so automations and the File Transfer node work out of the box on installed clients.
- No action needed for connections affected by the masked-password issue — they repair themselves on next save or next use.
