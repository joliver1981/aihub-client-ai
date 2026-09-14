# AI Hub v2.1

This release opens **The Agent** to everyone in your organisation, and sharpens how it handles
permissions — so what it tells you always matches what you're actually allowed to see and do.

---

## The Agent, for everyone

The Agent is no longer limited to Developers and Admins. Any user can now open it and work
conversationally — explore data, search documents, run portal downloads, schedule recurring tasks,
and send email through their own approval queue.

Access is controlled by your existing roles and groups. Nothing new is exposed: users see what they
have always been permitted to see.

## Answers that match your access

The Agent now describes **your** view of the platform rather than the platform as a whole.

- Ask about documents and it tells you what *you* can search — "you have access to 11 documents",
  not a platform-wide total.
- If your group hasn't been granted a document category, it says so plainly: the documents exist,
  your access is restricted, and an administrator can grant it on the Groups page. Previously this
  could read as though the platform were empty.
- Search results, document counts and category names are all scoped to the person asking.

## My Work respects your role

Approval queues are now filtered correctly for every role.

- Items **assigned to you** appear for you, as before.
- Items in the shared "anyone can pick this up" pool are visible to Developers and Admins only.
- This applies to agent requests, workflow approvals and automation reviews alike, and is enforced
  on the server for every screen and every tool that reads the queue.

## The Agent knows who it's talking to

Each conversation now carries the signed-in user's name and role. Ask "what's my role?" and you get
a direct answer, and The Agent will not accept claims about permissions made in conversation — your
role comes from your account, never from what someone types.

## Document category management

Administrators can now **unfile** a document type from its category on the Document Categories page.
An unfiled type becomes administrator-only until it is filed again, which makes it straightforward to
correct a mis-categorised type without having to move it somewhere else.

## Credential safeguards

Platform-critical secret names are now protected against accidental rename or replacement, and the
health check reports clearly when a required credential is missing or misconfigured.

---

## Upgrade notes

- **The Agent for all users** is controlled by `AGENT_ALLOW_ALL_USERS`. It ships **off**; set it to
  `true` to enable it for your whole organisation.
- **Document access is group-based.** A newly created group starts with **no** document categories
  granted, so its members see nothing until an administrator grants access on the **Groups** page
  (Document Categories → Access). This is deliberate: access fails closed.
- Regular users run a separate, lower-cost model by default. Administrators can change it in The
  Agent's Settings.
- No database migration is required for this release.
