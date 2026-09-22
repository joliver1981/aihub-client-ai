# AI Hub v2.2

This release adds **Microsoft 365 (OAuth2)** delivery for system email, makes approvals easier to
work from The Agent, and fixes a handful of issues reported since v2.1.

---

## Microsoft 365 email for system notifications

Outbound **system notifications** — workflow alerts, approval and review reminders, the
notification routes — can now be sent through **Microsoft 365 via the Graph API** using an Entra
app registration (OAuth2 client credentials, application permission `Mail.Send`).

- No SMTP AUTH and no mailbox password, so it is unaffected by Microsoft's retirement of Basic
  authentication for SMTP.
- Choose **Microsoft 365 (OAuth2)** on the Email Settings page. Your SMTP relay settings become the
  fallback and are used automatically if a Microsoft 365 send fails (on by default, switchable).
- **Send test** runs the real delivery path and reports the exact Microsoft error when something is
  misconfigured — and says so when the fallback carried the message.
- Per-user agent mailboxes are unaffected.

## Approvals in The Agent's My Work

- **Attachments are downloadable.** Files an automation attaches to an approval or review item —
  the report it is about to send, the document it flagged — now appear as download links in My
  Work, exactly as they do in My Approvals.
- **Routing by username works.** Automations that address an approval to a person by username now
  reach that person. Previously the request quietly went to whoever started the run.
- **Interrupted runs no longer leave orphaned approvals.** When a run is cut short by a service
  restart, its open approval and review items are closed out automatically instead of waiting
  forever for a decision that can no longer take effect.

## Results from scheduled and background work

- The "results were added to your conversations" popup is gone. Conversations that received a
  result while you were away are marked **unread** on the History list, with a count on the History
  button; opening the conversation clears it.
- Scheduled runs and portal updates are shown as what they are when a conversation is reopened,
  rather than as ordinary messages from you.

## Administration

- **Config Health** (administrators only, in the navigation) shows at a glance whether your
  `user_config.py` overrides are being applied, and pinpoints the exact line at fault when a single
  bad entry was silently discarding all of them.
- **Email Settings honour `.env` again.** On installs that configured SMTP only in `.env` and never
  saved the Email Settings page, the page showed blank values and some notifications fell back to
  the vendor relay or failed. The `.env` values are read correctly again (a v2.0 regression).

---

## Security

- **Data connections in The Agent are now scoped for regular users.** An End User (role 1) can
  list, inspect and query only the connections behind the Data Assistants shared with one of their
  groups — the same rule the classic Data Assistants page has always applied. Developers and
  administrators are unchanged. To give a regular user data access in The Agent, share a Data
  Assistant that uses that connection with their group on the **Groups** page. A connection that
  is not shared is reported as an access restriction, never as "no such database".
  `CONNECTION_ACL_ENFORCE=false` in `.env` restores the previous tenant-wide behaviour.

---

## Upgrade notes

1. Run the v2.2 installer over your existing installation.
2. **Restart the AI Hub services and hard-refresh your browser** (Ctrl+Shift+R).
3. To use Microsoft 365 email, register an Entra application with the `Mail.Send` application
   permission and enter the tenant, client id, client secret and sender mailbox on the
   **Email Settings** page — or set `EMAIL_PROVIDER=graph` with the `GRAPH_MAIL_*` keys in `.env`.
   Existing SMTP and Azure configurations keep working unchanged.
4. No database migration is required.

Your existing configuration, connections and customizations are preserved.
