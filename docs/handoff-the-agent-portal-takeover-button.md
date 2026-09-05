# Handoff: "Take over the browser" button for The Agent

**LOW priority, cosmetic.** Take-over already works end to end (live-proven,
pack 20 PT-13). CC shows a button on the 2FA pause; The Agent shows a plain
markdown link. Give it the button. That's the whole task.

## Change

The `needs_human` branch of `_poll_run` in `agent_service/portal_tools.py:407`
already returns the take-over link. Also emit an `aihub-action` rich block:

1. `agent_service/rich_blocks.py` — add an `action` kind:
   `{"action": "open_url", "url": ..., "label": "Take over the browser"}`.
2. `portal_tools.py:407` — append `rich_blocks.ref_fence(uid, "action", spec)`.
   Use `ref_fence`, not a raw fence — models reformat pasted block JSON, a ref
   can't be paraphrased. **Keep the existing plain-text link too** (headless and
   email turns have no UI).
3. `agent_service/static/index.html:704` — the fence allowlist takes only
   chart/kpi/map; add `action` and mount it as a button in the `.rblock-src`
   loop (`:1081`). `GET /api/blocks/<id>` is already generic.
4. Render the button ONLY for a same-origin `/portal-workflows/cobrowse/...`
   URL (else plain text), with `target=_blank rel="noopener noreferrer"`.

## Don't

- Don't add CC's live "waiting for you…" status — that needs the progress
  channel, which is parked on purpose (`docs/the-agent-streaming-progress-handoff.md`).
- Don't touch `portal_watch.py` or the hand-back bridge.

## Done when

Unit: the `needs_human` result still carries the link + `run_id`, plus the ref
fence (`tests_v2/unit/test_agent_portal_tools.py`). UI: pack 20 UI-1 — button
opens a new tab, conversation tab stays put. Then re-run PT-13 to confirm the
hand-back bridge still works.

## Implemented 2026-09-05 (the safe way)

Built as written with four amendments that came out of the risk pass — the
rule throughout is **strictly additive**: the plain link is the surface, the
button is decoration, and every failure renders exactly today's output.

1. **Same-origin was the wrong test.** The Agent UI lives on its own port
   (`/the-agent` redirects to HOST_PORT+110) while `cobrowse_link` builds an
   absolute URL on the MAIN app's origin — a same-origin rule would never
   match. The chat renders the button only for http(s) + the main app's port
   (`main_port` from `/api/me`) + the `/portal-workflows/cobrowse/<id>` path.
2. **Server side never raises.** `rich_blocks.action_fence()` returns "" on
   any failure (unwritable data dir, disk full); `portal_tools` appends the
   fence only when it got one. Headless / email turns get no fence at all —
   their prose lands in My Work summaries as raw markdown, and headless
   already raises its own take-over My Work item.
3. **UI failures render nothing.** For the `action` kind a bad fence, an
   unresolved / expired stored block, an unknown action, a rejected URL, or a
   render exception removes the placeholder silently — never the red
   "could not be rendered" box the chart/map kinds use.
4. **Stored references only.** An inline `aihub-action` spec (something a
   prompt-injected model could type, pointing anywhere) is dropped; the
   button's URL is always one the server built.

Known cosmetic edge: a browser still holding the OLD page after the service
upgrade shows the 3-line `{"ref": …}` code block under the link until reload.

Verification: `tests_v2/unit/test_agent_portal_tools.py` (spec resolves,
store failure keeps the link, headless has no fence), the pause test in
`test_agent_portal_watch.py`, and pack 20 UI-1 (`ui_smoke_links.py`: one
button, opens a new tab, gone/inline blocks render nothing).
