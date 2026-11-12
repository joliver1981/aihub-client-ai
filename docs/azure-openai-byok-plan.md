# Client-owned Azure OpenAI / Azure AI Foundry ("Azure BYOK") — implementation plan

**Status:** DRAFT for review, 2026-10-16. No code has changed. All changes are additive and wait for approval.

## 1. Summary

- Adds a third Bring-Your-Own-Key option on **Settings → API Keys** (`/admin/api-keys`): the client's own Azure OpenAI or Azure AI Foundry resource. The admin enters the endpoint, key, API version and deployment names for chat, fast and embeddings, alongside today's OpenAI `sk-` key and Anthropic key.
- One small resolver with no Flask dependency decides on every call which provider serves OpenAI-family traffic. Every service uses it: main app, Agent API, Knowledge API, Executor, Command Center, both builders, Browser Use and the Vector API.
- Client traffic always goes straight to the client's resource. No process sends it through the AI Hub cloud API, including processes that never run `init_byok()`.
- Claude surfaces do not change.
- The same work closes two gaps in today's BYOK:
  1. OpenAI BYOK calls from the Agent API, Knowledge API and Executor still go through the cloud API.
  2. The BYOK on/off file is read relative to each service's working folder.

## 2. Today (verified in code, 2026-10-16)

| Area | Today | Where |
|---|---|---|
| BYOK slots | `USER_OPENAI_API_KEY` and `USER_ANTHROPIC_API_KEY` in local secrets. On/off flag in `data/byok_config.json`. | `api_keys_config.py:51-61` |
| OpenAI BYOK reach | api.openai.com only. The base URL is a literal. Save rejects keys without the `sk-` prefix. Test calls `/v1/models`. | `config.py:143`, `api_keys_config.py:841`, `:693` |
| Resolver | `get_openai_config()` order: BYOK OpenAI key, then system OpenAI, then the platform's Azure deployment (alternate or primary). | `api_keys_config.py:255-372` |
| Consumers | All branch on `api_type` and read `api_base`, `api_version`, `deployment_id`. None branches on `source`, so a new source needs no consumer edits. | `GeneralAgent.py:3345`, `command_center_service/cc_config.py:607`, `builder_service/builder_config.py:432,629`, `builder_data/builder_data_config.py:366`, `WorkflowAgent.py:167`, `AppUtils.py:180-513`, `nlq_agentic/engine.py:159`, `TextChunker_LLM.py:288`, `data_collection_agent/*`, `command_center_service/routes/upload.py:269`, `create_pandasai_llm` |
| Separate resolvers | Browser Use has its own order: BYOK OpenAI, system OpenAI, platform Azure. Embeddings read `cfg.AZURE_OPENAI_*` directly. | `browser_use_service/browser_use_config.py:331-381`, `LLMDocumentVectorEngine.py:111-162` |
| OpenAI-direct only | Command Center image generation, The Agent's image tool, Whisper transcription and DCA text-to-speech all call api.openai.com. | `command_center_service/graph/nodes.py:3841,9478`, `agent_service/image_tools.py:31`, `whisper_routes.py:41-50`, `data_collection_agent/voice/openai_tts.py:51` |
| Cloud relay | In the `aihub2.1` env, a local patch to the openai SDK sends requests through the AI Hub cloud API unless `BYPASS_PROXY=true`. Only the sync client is patched. Client installs set the relay URL. | `docs/dev_machine/DEV_MACHINE_PROVISIONING.md` (Appendix C) |
| Who sets `BYPASS_PROXY` | Only the main app, through `init_byok()` → `apply_byok_environment()` at startup and on save or toggle. | `app.py:14202`, `api_keys_config.py:183-199` |
| Processes on the patched SDK | Main app, Agent API, Knowledge API and Executor (all `aihub2.1`). Command Center and both builders (`aihubbuilder`), the Vector API (`aihubvector2`) and Browser Use run unpatched SDKs, so their calls always go direct. The Agent and the Documents API have no openai package. | build `.bat` steps 1-13, `shortcuts/00_Start-Restart_AIHub_Services_V3.bat` |
| BYOK on/off file path | `./data/byok_config.json`, relative to the working folder. `AIHUB_DATA_DIR` is set nowhere. **On dev (verified):** Command Center and both builders run from their subfolders, so they read a file that does not exist and never see BYOK. **On installs (likely, needs checking):** every frozen service runs from its own exe folder, the NSSM default since the `.iss` sets no `AppDirectory`. The Agent, Browser Use and the image tool anchor on `APP_ROOT` instead. | `api_keys_config.py:58-61`, v7 `.iss` service installs |
| Embeddings on client installs | The shipped `user_config.py` sets `VECTOR_EMBEDDING_MODEL='default'`, which means local ONNX embeddings. Azure embeddings are used only when an install sets `'openai'` or `'azure'`. | `config.py:409`, shipped `user_config.py` |
| Unused | `azureMiniQuickPromptV2` and `azureQuickPromptMini` (a cloud prompt endpoint) and `VectorManager.py` are never called. They stay untouched. | `AppUtils.py:515-620`, `VectorManager.py` |

## 3. Design

### 3.1 One shared resolver: `byok_resolver.py` (new)

It uses only the standard library and `local_secrets`, so every env can import it: `aihub2.1`, `aihubbuilder`, `aihubvector2` and `aihub-browseruse`. It owns:

- **`byok_config_path()`:** `AIHUB_DATA_DIR`, else `APP_ROOT/data`, else the module folder's `data`.
  - When the anchored file does not exist, reads fall back to the old `./data/byok_config.json`. This is read-only: there is no migration at startup.
  - Writes always go to the anchored path.
- **`is_byok_enabled()` and `openai_provider()`:** the provider is `'openai'` (default when absent) or `'azure'`.
- **Azure settings:** loads and validates them. `azure_target(slot)` returns endpoint, key, api_version, deployment and detected model for `chat`, `mini` or `embedding`.
- **`active_openai_family()`:** returns `'byok_azure'`, `'byok'` or `'platform'`, for status and logs.
- **`use_direct_transport()`:** see §3.4.
- **`provider_fingerprint()`:** a short hash of source, endpoint, deployments and a hash of the key. It is used for cache checks and never contains the key.

`api_keys_config` keeps its public functions and delegates to the resolver. Browser Use and `LLMDocumentVectorEngine` import the resolver directly.

Kill switch: `BYOK_AZURE_ENABLED`, default `true`. Setting it to `false` hides the Azure card and makes the resolver ignore the Azure settings.

### 3.2 Storage

- **`USER_AZURE_OPENAI_API_KEY`:** the key. Stored in local secrets, category `llm_api_keys`, like the other BYOK keys.
- **`USER_AZURE_OPENAI_SETTINGS`:** one JSON value holding:
  - `endpoint` and `api_version`
  - `deployments`: `chat`, `mini`, `embedding`
  - the model id each deployment reported during Test, the embedding size, and the last Test result

  Keeping it in one value means a save is one atomic write.
- **Shared properties:** both values are encrypted and bound to the machine, survive upgrades, and every service can read them. The 5-second store cache means a save reaches all processes within seconds, with no restart.
- **Protection:** both names are added to `PLATFORM_MANAGED_SECRET_NAMES` (`local_secrets.py:89-96`). The Agent cannot overwrite them from chat. Admins can still see them on the Local Secrets page.
- **Provider choice:** `"openai_provider"` goes in `data/byok_config.json` next to `byok_enabled`; it is not sensitive. The endpoint never goes in that file, because the file is tracked in git and an endpoint carries the client's resource name.
- **Why not `.env` or `user_config.py`:**
  - The baked encrypted platform key beats a plain `.env` key (`config.py:122-123`).
  - The alternate and mini keys copy the primary at import.
  - Browser Use resolves keys in a different order.
  - Every change needs a restart.
  - `user_config.py` prints each override value, keys included, to stdout at startup (`config.py:1082`).

### 3.3 Precedence in `get_openai_config()`

1. **BYOK on, provider `azure`, kill switch on → `byok_azure`.**
   - `api_type='azure'` with the client's endpoint, key and api_version.
   - Deployment by slot: chat for the default and alternate slots; mini for `use_mini`, falling back to chat when mini is blank.
2. **BYOK on, provider `openai`, OpenAI key present → `byok`.** Unchanged.
3. **`USE_OPENAI_API` → `system_openai`.** Unchanged.
4. **Platform Azure, alternate or primary.** Unchanged.

Rules:

- **Azure selected but unreadable:** if the settings or key cannot be read (store unreadable, half-deleted), raise a clear error naming the problem. Traffic does not move to another provider (decision D3).
- **`model_override`:** keeps today's meaning, a deployment name on the active resource. The lanes that use it (document-search strategy, command generator) already fall back to the slot's deployment when that name fails.
- **Reasoning effort:** decided from the model id the Test recorded for that deployment. Without one, it falls back to today's rule, which reads the deployment name.
- **Return shape:** unchanged, plus `source='byok_azure'` and `detected_model`.

```python
if byok_resolver.openai_provider() == 'azure' and byok_resolver.azure_enabled():
    t = byok_resolver.azure_target('mini' if use_mini else 'chat')   # raises AzureByokUnavailable
    config = {'api_type': 'azure', 'api_key': t['key'], 'api_base': t['endpoint'],
              'api_version': t['api_version'], 'deployment_id': t['deployment'],
              'model': None, 'detected_model': t.get('model'), 'source': 'byok_azure'}
```

### 3.4 Transport: direct in every process

**Facts:**
- Only the sync openai client in `aihub2.1` is patched.
- The patch reads `BYPASS_PROXY` from `os.environ` on every request.
- Only the main app ever sets it.

**Design:**

- **Direct whenever BYOK is the source.** `get_openai_config()` calls `use_direct_transport()` whenever the source is `byok_azure` or `byok`, before it returns.
  - This sets `BYPASS_PROXY=true` in the calling process, so the client the consumer builds next goes direct.
  - It covers the Agent API, Knowledge API and Executor, which never run `init_byok()`.
  - It logs once per process.
- **Direct stays on until restart.** Once a process has served BYOK traffic, it stays direct for its lifetime.
  - The reason: the main app's per-user agent cache and NLQ engine sessions keep LLM clients built with the client's key.
  - If the process switched back to the relay as soon as BYOK was turned off, their next call would send the client's key and prompts through the cloud API.
  - So after BYOK is turned off, a service goes back to the relay only when it restarts (decision D1).
- **`apply_byok_environment()`:**
  - It counts `byok_azure` as BYOK.
  - When BYOK goes off, it keeps `BYPASS_PROXY=true` in a process that already went direct, and logs that it did.
  - Otherwise its behaviour is unchanged.
- **Test button:** the Azure Test route uses `requests`, never the patched SDK, so it is always direct.
- **Unpatched processes** (Command Center, builders, Vector API, Browser Use) are already direct. Nothing to change.
- **Bonus fix:** this also corrects OpenAI BYOK. Today, agent chats, workflow LLM steps and knowledge calls made with an OpenAI BYOK key still go through the cloud API.
- **Rejected for v1:** changing the SDK patch to relay only the platform's own hosts. That would be exact per request and need no restart, but the patch is a hand-maintained site-packages diff that lives outside git.
- **Depends on separate work:** how the platform's own model traffic reaches the cloud is being reviewed separately. If platform calls ever have to go through the cloud API to work, "stay direct until restart" could make them fail until the restart.
  - That design already covers it. In a process where `BYPASS_PROXY=true`, `get_openai_config()` points platform sources at an OpenAI-compatible cloud route by base URL. BYOK sources stay first and direct, and the route accepts only the tenant license key.
  - A later phase of that design builds every client against its own endpoint and removes the switch entirely.
  - In the main env, three call sites build their own clients and skip `get_openai_config()`: Whisper, DCA text-to-speech and the Automations AI endpoint. That design moves each of them to the cloud route explicitly. See D1.

### 3.5 Admin page (`templates/api_keys_config.html`)

**New card: "Azure OpenAI / Azure AI Foundry"**, between the OpenAI and Anthropic cards.

Fields:
- **Endpoint:** https only. A pasted "Target URI" (`…/openai/deployments/<name>/chat/completions?api-version=…`) is cut back to the resource URL, and its deployment name and version pre-fill the other fields. There is no host allowlist, so an API Management front door on a custom domain also works, as long as it accepts the `api-key` header.
- **Key:** a password field with a first-4/last-4 preview and Replace/Delete, like the other cards. An `sk-` or `sk-ant-` value is rejected, with a hint to use the matching card.
- **API version:** defaults to the platform's, `2024-12-01-preview`.
- **Deployment names:**
  - Chat: required.
  - Fast: optional; blank means Chat.
  - Embeddings: shown, and required, only when this install uses OpenAI embeddings.

Buttons:
- **Test** calls each deployment directly: one tiny chat completion per chat deployment and one embedding call.
  - Per deployment it shows one of: OK, key rejected (401), no such deployment (404 `DeploymentNotFound`), rate-limited but valid (429), wrong model type, or timeout/DNS failure.
  - It records the model id each answer reports and the embedding size.
  - It warns when the embedding size differs from the existing index.
- **Save** stores the settings and then runs Test. A failed Test never blocks the save, since the firewall may not be open yet.

**Provider selector**, above the two OpenAI-family cards: "When BYOK is on, OpenAI models use: ◉ OpenAI API key ○ Your Azure resource".
- Azure can be selected only once its settings are saved.
- Selecting it asks for confirmation if the last Test failed.
- Deleting the Azure settings while Azure is selected resets the selector to OpenAI.

**Status and wording:**
- The status panel gains a row "OpenAI models: AI Hub / Your OpenAI key / Your Azure resource (host)". The header badge counts Azure.
- Copy fixes:
  - "Keys never leave this machine" becomes "Keys are only sent to their own provider".
  - The billing note names Microsoft Azure.

**Routes** (all reuse `require_admin`):
- `POST /api/api-keys/azure`
- `DELETE /api/api-keys/azure`
- `POST /api/api-keys/azure/test`
- `POST /api/api-keys/provider`
- `GET /status` gains the Azure fields. No response ever contains the key.

### 3.6 Browser Use

- **New step 0 in `resolve_openai_driver()`:** if Azure BYOK is active, use the client's endpoint, key and version. `portal_runner._openai_stack_llm` already builds `ChatAzureOpenAI` from exactly these fields.
- **Driver deployment under Azure BYOK:**
  - Use the admin-UI `browser_use_model` override if it is set and is not a `claude-*` model.
  - Otherwise use the client's Chat deployment.
  - The `.env` pin `BROWSER_USE_LLM_MODEL` (`gpt-5.4` in the shipped `.env`) and `AZURE_OPENAI_DEPLOYMENT_NAME` name platform deployments, so they are ignored here.
  - A `claude-*` choice keeps the Anthropic path.
- **Existing OpenAI-key step:** it now honours the provider selector and is skipped when the provider is `azure`.
- **Fail-soft import:** if `byok_resolver` cannot be imported, the service logs a WARNING and behaves as it does today.
- **Transport and timing:** no change needed, since the SDK is unpatched. Resolution happens per run, so no restart is needed.

### 3.7 Embeddings

This only matters when `VECTOR_EMBEDDING_MODEL` is `'openai'` or `'azure'`. The shipped client default is local embeddings.

- **Thin wrapper:** `LLMDocumentVectorEngine` wraps its OpenAI/Azure embedding function in a resolver-backed function. On each call it asks `byok_resolver` for the embedding target.
  - Azure BYOK active: use the client's embedding deployment, with the `AzureOpenAI` client cached per fingerprint.
  - Otherwise: hand the call to the existing function unchanged, so the platform path is byte-identical.
- **Index compatibility:** the same model on another resource (`text-embedding-3-small`) produces compatible vectors, so no re-index is needed. A different model with a different size needs re-indexing, and Test warns about that.
- **Missing deployment:** if Azure BYOK is on, embeddings are needed and no embedding deployment is set, Save refuses and calls fail with a clear error (decision D7).
- **Transport:** the Vector API runs an unpatched SDK, so these calls go direct.

### 3.8 Images and audio (OpenAI-direct features)

Command Center image generation, The Agent's image tool, Whisper transcription and DCA text-to-speech call api.openai.com only.

Recommendation (decision D4): under Azure BYOK they use the client's OpenAI key if one is saved. Otherwise they report "not available while your Azure resource is in use", instead of quietly using the platform's OpenAI key. Azure image and audio deployments are a follow-up.

### 3.9 Cached agents

- The main app caches per-user `GeneralAgent` instances, each with its LLM built at construction (`app.py:671-736`). The provider fingerprint gets added to that cache check, following the existing binding-signature pattern, so switching provider rebuilds them.
- The Agent API builds agents per request, so it needs nothing.

### 3.10 Model Overrides card

- Its status gains `active_openai_source`.
- Under Azure BYOK, the `openai_primary`, `openai_mini` and `openai_embedding` rows read "your Azure resource — deployment X (set in the Azure card)". Overrides for those roles do not apply to the client's resource.
- The live per-lane knobs (Browser Use driver, document-search strategy) take a deployment name on the client's resource, and their help text says so.
- The "overriding while using system keys" warning treats Azure BYOK as BYOK.

### 3.11 Claude-only surfaces stay unchanged

- **Unchanged surfaces:**
  - The Agent: Claude Agent SDK; `ensure_anthropic_key()` order is BYOK, relay, env, encrypted.
  - The Documents API and doc job queue, which have no openai package.
  - `claudeQuickPrompt`, `get_anthropic_config()` and `create_anthropic_client()`, including the hand-off to the Documents API.
  - The re-ranker, knowledge summaries and router, and Claude Vision for Command Center uploads.
- **Why they are unaffected:** none of them reads `get_openai_config()`, the Azure settings or `BYPASS_PROXY`. The Anthropic SDK is unpatched everywhere, and Anthropic proxying is app configuration.
- **Intended exception:** platform tools The Agent calls on the main app (data questions, workflows) follow the active OpenAI provider.

## 4. Change list (all additive)

| File | Change |
|---|---|
| `byok_resolver.py` (new) | §3.1 |
| `api_keys_config.py` | Azure step in `get_openai_config()`, `use_direct_transport()` call, reasoning effort from the detected model, `is_byok_enabled()` and `_get_config_file_path()` delegate to the resolver, new `get_byok_status()` fields, `apply_byok_environment()`, four routes, `test_azure_deployments()` |
| `local_secrets.py` | Two names added to `PLATFORM_MANAGED_SECRET_NAMES` |
| `templates/api_keys_config.html` | Azure card, provider selector, status row, copy fixes, Model Overrides labels |
| `model_overrides.py` | `active_openai_source` and effective values under Azure BYOK |
| `app.py` | Provider fingerprint in the `get_agent_for_user()` cache check |
| `browser_use_service/browser_use_config.py`, `main.py` | Azure step 0, provider selector, driver deployment rule, startup log of source and host (never the key) |
| `LLMDocumentVectorEngine.py` | Resolver-backed embedding wrapper |
| `command_center_service/graph/nodes.py`, `agent_service/image_tools.py`, `whisper_routes.py`, `data_collection_agent/voice/openai_tts.py` (the DCA runs as a main-app blueprint, `app.py:14187-14188`) | Decision D4 rule |
| `AIHub_Setup_Script_v7_OneDir_Dev.iss` | Ship `byok_resolver.py` loose to `{app}` next to `local_secrets.py`, for the source-run Browser Use service |
| Command Center, builder and builder-data `.spec` | `'byok_resolver'` hidden import next to `'api_keys_config'` |

Not changed: `config.py` (the `AZURE_OPENAI_*` settings stay the platform's) and any `.env` keys. The kill switch defaults to on, so the installer's upgrade ensure-list needs nothing.

## 5. Tests

Run with pytest in `aihub2.1`. Force-add the files, because `.gitignore` hides `test*.py`.

- **`tests_v2/unit/test_byok_azure_resolver.py`:**
  - The precedence matrix: BYOK off/on × provider openai/azure × key and settings present/absent × kill switch.
  - Slot mapping: default and alternate go to chat; mini goes to mini, or chat when mini is blank.
  - `model_override`.
  - Reasoning effort from the detected model versus from the deployment name.
  - Azure selected but unreadable raises the error.
  - Status never contains the key.
- **`tests_v2/unit/test_byok_transport.py`:**
  - `BYPASS_PROXY` is set for `byok` and `byok_azure` but not for platform sources.
  - It stays set after BYOK goes off.
  - `apply_byok_environment()` follows the new rules.
  - End-to-end against the real patched SDK, in a subprocess so each case starts clean. The relay URL is set, and the SDK's relay `post` is replaced by a function that fails the test.
    - A BYOK `AzureOpenAI` call to a local stub server must reach the stub directly.
    - A platform call with BYOK off must reach the relay mock.
- **`tests_v2/unit/test_byok_config_path.py`:**
  - `APP_ROOT` anchoring, and `AIHUB_DATA_DIR` winning over it.
  - The read-only fallback to the old relative path.
  - Writes go to the anchored path.
  - Importing creates no file.
- **`tests_v2/unit/test_api_keys_azure_routes.py`** (Flask test client with an admin stub):
  - Save validation: https only, Target-URI normalisation, `sk-`/`sk-ant-` rejected with a hint, embedding deployment required when OpenAI embeddings are on.
  - Test-route outcomes with `requests` mocked: 200 plus model id, 401, 404 `DeploymentNotFound`, 429, timeout.
  - Delete resets the provider.
  - The provider route refuses `azure` while nothing is saved.
  - No response echoes the key.
- **`local_secrets`:** the two new names are refused on the platform-service write path.
- **`browser_use_service/tests/test_driver_model_resolution.py`:**
  - Azure step 0 works.
  - The shipped `.env` pin is ignored, and the admin override is honoured.
  - A `claude-*` pin is unchanged.
  - Provider `azure` skips the OpenAI key.
  - Parity test: `get_openai_config()` and `resolve_openai_driver()` give the same endpoint and deployment for the same state.
- **Embeddings:**
  - When Azure BYOK is inactive, the wrapper hands the call to the original function object.
  - When active, it uses the client's deployment.
  - A missing embedding deployment produces a clear error.
- **`tests/unit/test_model_overrides.py`:** `active_openai_source` and the effective values under Azure BYOK.
- **Per-user agent cache:** a fingerprint change evicts the cached agent.
- **Images and audio:** key resolution under the decision D4 rule.

## 6. Live verification

Do this after the unit tests, first on the dev box and then on an installed box. It needs a second Azure OpenAI resource that we own, not the platform's (decision D12).

1. Enter the resource on `/admin/api-keys`. Test should pass with model ids shown. Select Azure and turn BYOK on.
2. Drive each lane. Confirm the traffic shows up in the test resource's metrics and never reaches the cloud API: the patched SDK prints no relay lines and the cloud records no new usage rows for the window. Lanes to drive:
   - GA chat, both a per-user cached agent in the main app and the Agent API path
   - An NLQ question
   - A workflow LLM step (Executor)
   - Command Center chat and a builder
   - A Browser Use portal run
   - Document ingest and search with `VECTOR_EMBEDDING_MODEL='openai'`
3. Confirm Claude lanes behave as before: document processing and a turn in The Agent.
4. With a wrong key, Test reports "key rejected". With a deleted deployment, calls fail loudly and name the deployment. Nothing falls back to AI Hub.
5. Turn BYOK off and restart the services. The relay resumes and usage rows reappear.
6. On an installed box, check the BYOK on/off file path first (build step 0), then repeat steps 1-2.

## 7. Build order

0. Read-only check on an installed box: where `byok_config.json` is written by the main app and where each service reads it.
1. `byok_resolver.py`, path anchoring and the `local_secrets` names, with the resolver, transport and path tests.
2. `api_keys_config` wiring and routes, with their tests.
3. The admin page and the Model Overrides labels.
4. Browser Use, embeddings, the image and audio rule, and the agent-cache fingerprint, each with tests.
5. The `.iss` and `.spec` lines, and the `dist` mirror for the loose module.
6. Live verification, release notes, and the client checklist (§8) as a client-facing page.

## 8. Client checklist: what to create on the client's resource

1. **Resource.** Use an Azure OpenAI resource or an Azure AI Foundry (AI Services) resource, in any region. From its **Keys and Endpoint** page, note the Azure OpenAI endpoint (normally `https://<resource>.openai.azure.com/`) and one of the two keys.
2. **Deployments.** Use Standard or Global Standard. Name each deployment exactly like its model, so AI Hub's model-specific handling applies.

   | Deployment | Needed? | Model (AI Hub default first) | Used for |
   |---|---|---|---|
   | Chat | Required | `gpt-5.6-terra`, or `gpt-5.4` | Agents, data questions, workflows, Command Center, builders, portal automation |
   | Fast | Recommended | `gpt-5.6-luna`, or `gpt-5.4-mini` | Short internal steps: routing, classification, extraction. When omitted, Chat is used, which is slower and costs more. |
   | Embeddings | Only if the Azure card asks for it | `text-embedding-3-small` | Document search on installs that use OpenAI embeddings. Another embedding model means re-indexing documents. |

   Not supported yet: gpt-6 models (tool calling needs a newer API than AI Hub uses), non-OpenAI models from the Foundry catalog, and image or audio models.
3. **Capacity.** Set tokens-per-minute to match expected use. AI Hub runs some steps in parallel, such as document sweeps, so a low quota shows up as rate-limit (429) errors.
4. **API version.** AI Hub uses `2024-12-01-preview`. Change it only if the resource requires another version.
5. **Network.** The AI Hub server must reach the endpoint over HTTPS (port 443). If the resource is behind a firewall or a private endpoint, allow the AI Hub server through.
6. **Authentication.** Version 1 supports API keys only. If key authentication is disabled on the resource, AI Hub cannot connect.
7. **Content filters.** Azure's content filtering applies to AI Hub traffic, and prompts that contain document text can trip it. If `content_filter` errors appear, adjust the filter policy.
8. **In AI Hub.** Go to Settings → API Keys → Azure card. Enter the endpoint, key and deployment names, click Test, then Save. Select "Your Azure resource" and turn BYOK on.
9. **Claude.** Document processing and The Agent still use Claude: either the client's Anthropic key (BYOK) or AI Hub's default.

## 9. Open decisions

| # | Decision | Recommendation | Alternatives |
|---|---|---|---|
| D1 | What happens after BYOK is turned off | A process that served BYOK traffic stays direct until it restarts, so no client key ever reaches the cloud API. Until then, platform calls from that process skip the cloud API's usage logging and rate limits. The separate design for platform traffic keeps this safe if platform calls ever need the cloud API (see §3.4). | Switch back to the relay at once, but a cached client could then send the client's key through the cloud API. Or change the SDK patch to relay only the platform's hosts: exact, but the patch lives outside git. Or per-client endpoints (platform clients on an OpenAI-compatible cloud route, BYOK clients on their own endpoint), which removes the switch entirely. |
| D2 | How to choose between the OpenAI key and Azure | Explicit selector | Azure wins whenever it is configured |
| D3 | Azure is selected but its settings can't be read | Fail with a clear error | Fall back to AI Hub's default, as The Agent's BYOK does for an empty slot. That silently moves the client's data and cost. |
| D4 | Images, transcription and text-to-speech under Azure BYOK | Use the client's OpenAI key if one is saved; otherwise "not available" | Keep using the platform's OpenAI key; add Azure image and audio deployments later |
| D5 | Authentication | API key only in v1 | Entra ID service principal later. It needs `azure-identity` in every env. |
| D6 | Per-lane model knobs under Azure BYOK (document-search strategy, command generator, Browser Use override) | Treat them as deployment names on the client's resource. They already fall back on failure. | Ignore them under Azure BYOK |
| D7 | Embeddings needed but no embedding deployment set | Save refuses, and calls fail with a clear error | Keep embeddings on AI Hub's resource |
| D8 | Anchoring the BYOK on/off file (prerequisite) | Anchor on `APP_ROOT` with a read-only fallback to the old path; check an installed box first | Leave it, in which case Azure BYOK would not reach most services |
| D9 | Fix the existing OpenAI BYOK relay gap in the same change | Yes. It is the same code path. | Separate change |
| D10 | Kill switch | `BYOK_AZURE_ENABLED`, default on | Default off until verified on a client |
| D11 | Where the Azure settings live | Local secrets | A new data file, which would have to be added to `.gitignore` |
| D12 | Test resource for live verification | A second Azure OpenAI resource, separate from the platform's | — |

## 10. Risks

- **Model handling by name.** Deployments named unlike their model (for example `prod-chat`) lose model-specific handling. Three things soften this: the Test records the real model id, the checklist asks for model-named deployments, and the existing guards catch most parameter drift (dropping a rejected temperature, unsupported parameters and unknown keyword arguments). One residual gap: the Command Center image-upload fallback to OpenAI Vision still sends `max_tokens`.
- **Content filters.** Azure content filtering on a client's resource can reject prompts. These show up as provider errors.
- **Usage pages.** Tier & Usage will not show OpenAI-family usage under BYOK, because that traffic never reaches the cloud.
- **Path anchoring on existing installs.** Changing where BYOK on/off is read affects existing installs. The read-only fallback and the step-0 check cover this.
- **API versions.** Supported API versions differ by resource and region. Test reports a mismatch.
