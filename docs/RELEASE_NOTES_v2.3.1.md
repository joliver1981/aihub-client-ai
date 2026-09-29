# AI Hub v2.3.1

## My Work

- **History** — a History button on My Work shows what you decided, most recent first, across agent items, workflow approvals and automation review items. Each entry shows the decision, who made it and when, your comment, the corrections you entered, and what happened next (for example, filed in Dayforce). The Agent can be asked about a decided item just like an open one.
- **Search** — a search box filters the list on any word on an item: title, text, source, decision, comments, corrections, outcome and field values. It works in both the open list and History.
- **Decide many at once** — tick items (or "Select all shown", which follows the chips and the search), then Approve selected or Reject selected. A confirmation lists what will happen to each kind of item and what is skipped; sending email drafts asks for an explicit second confirmation. Each item is decided exactly as its own button would, so every check still applies.
- **Asking The Agent about an item** — the question now carries the item's full text and, for a decided item, the decision, corrections and outcome, so the answer reflects the whole item.
- **Ask The Agent for your history** — "what did I approve this week" or "show my rejections" lists your decided items with comments, corrections and outcomes.

## Review items

- **A field for every part** — a review item can carry as many editable fields as it needs; the earlier limit of eight is gone, so a split scan offers a Document Type dropdown for every one of its parts.
- **Full item text** — the text of a review item is stored whole. A limit can be set with `AUTOMATIONS_REVIEW_MESSAGE_MAX_CHARS` (0 or unset = no limit); when one is set, a cut is visible on the item and noted in the app log, never silent.

## Upgrade notes

Run the v2.3.1 installer and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.
