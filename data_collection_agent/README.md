# Data Collection Agent — Integrator Quickstart

Reusable, schema-driven AI-guided data collection. Solution authors define a
JSON schema (sections, fields, validation, completion actions); end users have
a conversational chat experience that walks them through filling it out.

This README is for **integrators** — anyone calling the REST API from another
system (e.g. MER360 deep-linking into the runtime, or a backend service
listing schemas).

For solution authors who want to *create* schemas, use the in-app wizard at
`/data-collection/builder`.

---

## URLs at a glance

| Audience | URL | Purpose |
|---|---|---|
| End user | `/data-collection/` | Agent gallery — pick which agent to chat with |
| End user | `/data-collection/<config_id>` | Chat with a specific agent |
| Author | `/data-collection/builder` | Wizard to create new schemas |
| Author | `/data-collection/builder/<config_id>` | Wizard to edit an existing schema |
| Health | `/api/data-collection/health` | Service health + action types |
| Spec | [`openapi.yaml`](./openapi.yaml) | Full OpenAPI 3 spec for the runtime API |

The runtime page accepts an optional `?prefill=<JWT>` query parameter
(Phase 1.3 onwards) for deep-link launches. Without it, the page works as
described — direct URL access never requires a token.

---

## Quickstart with curl

Assumes the service is running at `http://127.0.0.1:5099`.

### 1. List available schemas

```bash
curl -s http://127.0.0.1:5099/api/data-collection/configs | jq
```

```json
{
  "status": "success",
  "configs": [
    { "id": "example", "name": "Example Intake Form", "version": "1.0",
      "description": "...", "section_count": 2, "action_count": 1 }
  ]
}
```

### 2. Inspect a schema

```bash
curl -s http://127.0.0.1:5099/api/data-collection/schema/example | jq .schema.sections
```

### 3. Start a session

The server runs the agent's opening greeting on creation, so the response
already contains the first assistant message in `chat_history`.

```bash
curl -s -X POST http://127.0.0.1:5099/api/data-collection/sessions \
     -H "Content-Type: application/json" \
     -d '{"config_id": "example"}' | jq
```

```json
{
  "status": "success",
  "session": {
    "session_id": "8b2ac5fa-…",
    "config_id": "example",
    "status": "in_progress",
    "current_section_id": "basics",
    "section_status": { "basics": "in_progress", "details": "not_started" },
    "collected_data": {},
    "chat_history": [
      { "role": "assistant", "content": "Hi! I'll help you complete..." }
    ],
    ...
  }
}
```

### 4. Send a message

```bash
SESSION=8b2ac5fa-...
curl -s -X POST http://127.0.0.1:5099/api/data-collection/message \
     -H "Content-Type: application/json" \
     -d "{\"session_id\": \"$SESSION\", \"message\": \"My name is James\"}" | jq
```

The response includes the agent's reply text and a `metadata` object with
the current section, collected data so far, validation errors, and any
rich content blocks (tables/cards) emitted by tools.

### 5. Set a field directly (skip the chat for prefill)

```bash
curl -s -X POST http://127.0.0.1:5099/api/data-collection/session/$SESSION/update-field \
     -H "Content-Type: application/json" \
     -d '{"section_id": "basics", "field_id": "submitter_email",
          "value": "james@example.com"}' | jq
```

Returns the updated session, or HTTP 400 with structured `validation_errors`
if the value fails the field's rules.

### 6. Navigate (back/edit a previous section)

```bash
curl -s -X POST http://127.0.0.1:5099/api/data-collection/session/$SESSION/navigate \
     -H "Content-Type: application/json" \
     -d '{"section_id": "basics"}' | jq
```

### 7. Submit — runs the completion-action pipeline

```bash
curl -s -X POST http://127.0.0.1:5099/api/data-collection/session/$SESSION/submit | jq
```

Response includes the per-action result list. HTTP 200 = all succeeded,
HTTP 207 = pipeline ran but at least one action failed.

### 8. Resume a previous session

```bash
# List the user's open sessions
curl -s "http://127.0.0.1:5099/api/data-collection/sessions?config_id=example" | jq

# Or load a specific one
curl -s http://127.0.0.1:5099/api/data-collection/session/$SESSION | jq
```

---

## Auth & Modes

### Test mode vs production mode

Set `DATA_COLLECTION_TEST_MODE=True` (env var, or in your `.env`, or in
`config.py`) to bypass identity / ownership / admin-role checks. Default is
**False** (production-strict). Use test mode for local development; switch
it off in any deployment that real users touch.

| Behavior | Test mode | Production mode |
|---|---|---|
| Identity required | No (everyone is "test_user") | Yes (JWT, platform login, or anon cookie) |
| Session ownership enforced | No | Yes — 403 across users |
| Builder reachable | Yes | Admin only (Developer or Admin role) |
| Admin pages reachable | Yes | Admin only |
| Anon cookie issued | No | Yes — stable per-browser |
| Gallery shows Edit/Builder buttons | Yes | Admin only |

### Identity sources (production mode)

Resolved in priority order:
1. JWT prefill claim (`sub`) — for MER360-style deep-links. JWT users are
   never granted admin access regardless of token contents.
2. Platform login (`flask_login.current_user.id`) with `current_user.role >= 2`
   gating admin features.
3. Anonymous browser cookie (`dca_anon_id=anon-…`) issued on first page load.
   Lets multi-user testing on the same instance without each user logging in.

### Token-based deep-links

The page route accepts an optional `?prefill=<JWT>` signed with
`DCA_PREFILL_SECRET`. The token is **never required** — direct URL access
continues to work without it. See the plan for the JWT payload contract.

---

## Branding

Three levels, most-specific wins:

1. **JWT prefill** `branding` claim (per-session)
2. **Schema's `branding` block** (per agent)
3. **App-level** — `configs/_app_branding.json` or `DCA_APP_*` env vars

Each level can set: `display_name`, `logo_url`, `primary_color`, `accent_color`,
`font_family`, `footer_text`, `favicon_url`, `support_url`. Unset values fall
through to the next level. See `configs/_app_branding.json.example` for a
template.

---

## Where to look in the code

| Area | File |
|---|---|
| Schema loading + lookup resolution | [`schema_loader.py`](./schema_loader.py) |
| Per-session state (JSON files) | [`state_manager.py`](./state_manager.py) |
| Validation engine (per-field, cross-field, conditional visibility) | [`validation_engine.py`](./validation_engine.py) |
| LangChain agent | [`agent.py`](./agent.py) |
| REST routes | [`routes.py`](./routes.py) |
| Completion actions pipeline | [`actions/`](./actions/) |
| Schema builder agent + wizard routes | [`builder/`](./builder/) |
| Branding resolver | [`branding.py`](./branding.py) |

---

## Spec

See [`openapi.yaml`](./openapi.yaml) for the full OpenAPI 3 spec. Drop it
into any Swagger Editor / Redoc / Postman to browse interactively.
