# Live Test Plan 35 — Data Collection Agent (DCA) full flow

End-to-end exercise of the Data Collection Agent: create a schema in
the builder → use it as an end user → admin review of the submitted
session.

## Pre-conditions

- Standalone DCA service running via `python run_dca.py` (or the
  `dca.bat` wrapper). Default port `5099`. Use `https://` if an HTTPS
  mode is configured (see `/data-collection/admin/https`).
- LLM credentials available via one of:
    - `OPENAI_API_KEY` env var (OpenAI direct)
    - `AZURE_OPENAI_*` envs (Azure)
    - Platform secure-config store (`secure_config.py`)
- A modern browser (Chrome / Edge / Firefox).
- For voice tests, mic + speakers (recent Chrome/Edge supports
  `MediaRecorder` + the Speech Synthesis API).

## Tabs / URLs to keep open

- `http://localhost:5099/healthz` — sanity ping
- `http://localhost:5099/data-collection/` — end-user gallery
- `http://localhost:5099/data-collection/builder` — schema builder
- `http://localhost:5099/data-collection/admin` — admin index
- `http://localhost:5099/data-collection/my-sessions` — per-user session list

## Steps

### 1. Healthz responds

- [ ] `curl http://localhost:5099/healthz` → `{"status":"ok","app":"data_collection_agent_standalone"}`
- [ ] `curl http://localhost:5099/api/data-collection/health` →
  `{"status":"ok","configs":...,"action_types":[...]}`

### 2. Builder: create a new schema

- [ ] Navigate to `/data-collection/builder`.
- [ ] Confirm the wizard chat opens. In DevTools see
  `POST /api/data-collection/builder/message` round-trips.
- [ ] In the chat: "Create a data collection form for venue research:
  collect venue name, address, capacity, parking availability (yes/no),
  notes." Send.
- [ ] After a few turns the agent should propose sections + fields.
- [ ] In the form editor at the right, verify a section appears with
  the requested fields, including a boolean for parking and text for
  notes.
- [ ] Click **Save**. Confirm `POST /api/data-collection/builder/save`
  returns `{"status":"success"}` with a `config_id`.

### 3. Gallery shows the new agent

- [ ] Navigate to `/data-collection/`.
- [ ] The just-saved schema appears as a tile.
- [ ] Click the tile.

### 4. Runtime conversation

- [ ] Confirm `POST /api/data-collection/sessions` fires with the
  config id, and the response includes a `session.session_id` plus an
  assistant greeting in `chat_history`.
- [ ] Send the answers, one per turn:
    - "Madison Square Garden"
    - "4 Pennsylvania Plaza, NY, NY"
    - "20,000"
    - "yes"
    - "Plenty of subway access; rooftop bar nearby."
- [ ] After each turn, watch `POST /api/data-collection/message` round
  trip. Verify the response `metadata.extraction_records` shows what
  was applied this turn.
- [ ] The progress panel on the right updates after each turn.

### 5. Inline edit from progress panel

- [ ] Click a saved field's value in the progress panel.
- [ ] Edit it (e.g. capacity 19,000) and confirm.
- [ ] `POST /api/data-collection/session/<id>/update-field` returns
  `{"status":"success"}` with the updated `display_value`.

### 6. Recap / submit

- [ ] In the chat ask "show me the recap" — verify a rich recap block
  renders showing each field with its display value.
- [ ] Say "looks good, submit". The agent should run the validation
  gate and (if configured) trigger completion actions.
- [ ] `POST /api/data-collection/session/<id>/submit` should return
  `{"status":"success"}` or `partial`, with `session.status` =
  `submitted`. `submitted_at` is set.

### 7. My-sessions resume

- [ ] Navigate to `/data-collection/my-sessions`.
- [ ] The submitted session should NOT appear (default filter excludes
  submitted). Append `?include_submitted=true` to the API path and
  confirm via DevTools.
- [ ] Start a NEW session for the same agent and abandon it mid-way
  (close the tab). Return to `/data-collection/my-sessions` — the
  in-progress session is listed with a percent-complete badge.
- [ ] Click Resume. The chat reopens at the right section.

### 8. Voice mode (optional)

- [ ] In a runtime page, click the voice toggle. Grant mic access.
- [ ] The page should swap to voice mode (mic button changes state).
- [ ] DevTools shows `POST /api/data-collection/session/<id>/voice-mode`
  with `{enabled: true}`.
- [ ] Speak an answer. The transcript reaches the agent; the agent's
  reply is read aloud via TTS (`POST /api/data-collection/tts` returns
  audio bytes, or `browser_fallback` JSON if browser TTS is
  configured).

### 9. Admin: HTTPS config

- [ ] Navigate to `/data-collection/admin/https`.
- [ ] Click "Generate self-signed". `POST .../generate-cert` returns
  cert info; the form refreshes with the new mode and paths.
- [ ] Save the form. Restart the standalone runner (`dca.bat
  restart`).
- [ ] Confirm the runner banner now says `Listening on https://...`
  and the browser shows the (untrusted) cert.

### 10. Admin: cert-info round trip

- [ ] `GET /api/data-collection/admin/https/cert-info?path=<cert>` →
  JSON with `subject`, `not_valid_after`, `days_remaining`, SAN
  entries.

## Tear-down

- [ ] Delete the test schema from the builder (`DELETE
  /api/data-collection/builder/<config_id>`).
- [ ] Optional: clear `data/_https_config.json` and remove generated
  certs from `data/dca_certs/` to return to plain HTTP.
- [ ] Stop the runner.

## Test data

Use `module35_dca_tests.json` (sibling to this file) for a scripted
payload reference — handy for `curl` / `httpie` smoke runs:

```
http POST :5099/api/data-collection/sessions \
  config_id="venue_research"
```
