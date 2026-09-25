# AI Hub v2.3

This release makes approvals easier to work (file previews, team routing, clearer review outcomes),
brings skills into workflows, and tightens who can open approval attachments.

---

## Approvals and My Work

- **Preview attachments.** Approval attachments in The Agent's My Work now have a **Preview** link
  beside Download. It opens the file in a new browser tab using the browser's built-in viewer (PDFs,
  images, text and CSV files, audio and video); closing the tab closes it. Office files remain
  download-only.
- **Regular users can open their attachments.** End Users can now download and preview the
  attachments on approvals routed to them or to their group, in both My Work and My Approvals.
- **Route work to a team.** The Agent can now send a My Work item to a **group** as well as to a
  person. Only that group's members see it; any member can claim it, which hides it from the rest of
  the group until it is released.
- **See what happened after a review.** When an automation finishes processing a reviewed item, the
  outcome (included, rejected, refused, and why) now appears on the item in My Approvals, as a
  "What happened" note and a status chip in the list. Multi-line review messages keep their
  formatting.
- **Pick from a list instead of typing.** Automations can offer a dropdown of valid values on a
  review item (for example, Document Type), in both My Approvals and My Work.
- **Asking about an item feels immediate.** Your question appears in the item's thread right away,
  with a thinking indicator while The Agent answers.

## Skills in workflows

- The **AI Extract** and **AI Action** workflow nodes can now apply a skill: choose a saved skill,
  paste skill text into the node, or both. Existing workflows are unchanged.
- Automations can read a skill with `aihub.skill(name)`.
- Skills can be created and edited directly on The Agent's **Skills** page.

## The Agent

- End Users no longer see the AI model label in the lower-left corner. Developers and
  administrators are unchanged.

---

## Security

- **Approval attachments follow the approval's routing.** An attachment can be opened only by the
  person or group the approval is routed to (Developers and administrators for approvals that are not
  routed to anyone). Previously any Developer or administrator could open any attachment by link.
  As a result, Mission Control's attachment links now open only for the approval's assignee.
- **My Work items can only be acted on by the people who can see them.** Claiming, answering or
  asking about an item now requires that it is routed to you or your group (or, for unrouted items,
  a Developer or administrator role).

---

## Upgrade notes

1. Run the v2.3 installer over your existing installation.
2. **Restart the AI Hub services and hard-refresh your browser** (Ctrl+Shift+R).
3. No database migration is required.

Your existing configuration, connections and customizations are preserved.
