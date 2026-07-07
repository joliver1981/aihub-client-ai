# Routing Command Center (and browser-use) LLM traffic through the aihub-api proxy

Decision-grade analysis + implementation plan. Produced 2026-07-07 from a multi-agent
read of both repos (`aihub-client-ai-dev` + `aihub-api`) with an adversarial verification
pass. Where the first-pass synthesis and the adversarial pass disagreed, this document
states the **verified** conclusion.

---

## 1. Why this is not "just an easy change"

"We already call Azure directly" and "route through the proxy" are **two different wire
protocols**, not two values of one setting.

- **Today:** CC builds a LangChain `AzureChatOpenAI`/`ChatOpenAI` (`command_center_service/cc_config.py:534-566`)
  that speaks the **native Azure Chat Completions protocol** straight to
  `https://<resource>.openai.azure.com/openai/deployments/{dep}/chat/completions?api-version=…`
  with the platform Azure key in an `api-key` header.
- **The proxy** (`aihub-api/project/api/views.py:487` `openai_proxy_request_v3`) is **not an
  OpenAI-compatible endpoint.** It's a base64 **envelope relay**: it expects a JSON body of
  `{abs_url, method, data (base64 of the real body), headers, timeout}` and authenticates
  with the tenant **license key**, not an OpenAI key.

So the "obvious" change — point `base_url` at the proxy — **cannot work**: the OpenAI SDK
POSTs a bare completions body with no `abs_url`, the relay throws `KeyError` → 500. And the
relay hard-codes `stream=False` and buffers the whole response (`views.py:441,615`), so it
can't stream tokens. To bridge the two protocols you must either wrap every request in the
envelope on the client, or add a new OpenAI-shaped route on the cloud. That's the work.

**Correction to a tempting assumption:** the OpenAI v3 relay forwards the request body
**verbatim** (`data=decoded_data`, `views.py:439`), so `tools`/`tool_choice`/`tool_calls`
**do survive** it. The tool-dropping problem is specific to the *Anthropic* proxy
(`anthropic_utils.py:68` builds from a fixed whitelist). Do not copy that whitelist habit
onto the OpenAI route.

---

## 2. What routing through the proxy actually buys

Today's direct-to-Azure path is a **billing/governance blind spot**, and the machinery to
close it already exists on the cloud side — it just never sees this traffic.

| Capability | Direct-to-Azure (today) | Through the proxy |
|---|---|---|
| Per-tenant metering / billing | **None** — no usage row is written for CC or browser-use; likely the highest-token traffic on the platform goes uncounted | tenant-keyed row per call (`TenantId`, `TokensUsed`, `ModelUsed`, `ModuleName`) via `log_api_usage_v2` (`views.py:620`) |
| Key hiding | **Shared platform Azure key baked into every client** (`_build_config`), decryptable on every box; revocation = rotate for everyone | client sends only its license key; real Azure key stays in the cloud (`swap_api_key_in_headers`, `views.py:304`) |
| Rate limit / quota | **None** — a trial tenant can drive unlimited agent spend | `@rate_limit` (`views.py:489`): per-minute + monthly quota, overage flags |
| Central model governance | each client resolves model/endpoint locally | single chokepoint to pin/substitute models |

This was the **original design intent**: `get_openai_config`'s Azure branch is literally
labelled *"Azure OpenAI (proxy)"* (`api_keys_config.py:220`), `.env.template` ships
`AI_HUB_PROXY_OPENAI=openai_proxy_request_v3/`, and BYOK writes a `BYPASS_PROXY` flag on the
theory that default traffic *should* be proxied. The reality is the **inverse** — the default
path bypasses the proxy and `BYPASS_PROXY` is read nowhere (dead scaffolding). So this is
closing a hole the platform already meant to close, not adding a new feature.

---

## 3. Options

| # | Approach | Effort | Repos | Tools | Streaming | Verdict |
|---|---|---|---|---|---|---|
| i | `base_url` → proxy v3 + license key | S | client | — | — | **Reject** — raw body has no `abs_url` → 500. Non-functional. |
| ii | Custom `httpx` transport wraps each request into the v3 envelope, unwraps the buffered response | M | **client only** | ✅ verbatim | ❌ (buffered) — harmless, CC doesn't stream | **Stopgap** — no cloud deploy, but ships hand-rolled URL reconstruction + sync/async `httpx.Response` synthesis to every install |
| iii | New **OpenAI-compatible** route in aihub-api; CC just sets `base_url` + license key | L | **mostly aihub-api** (~80-150 lines) + trivial client | ✅ verbatim | ✅ (net-new SSE code, not today) | **Recommended** |
| iv | Replace CC's LangChain client with `CommonUtils.AnthropicProxyClient` | L-XL | client (massive) | ❌ no `bind_tools` | ❌ | **Reject** — Anthropic-shaped, no LangGraph tool-calling |

**Key de-risking fact (verified):** CC consumes **buffered** responses only — every
`get_llm()` graph call site passes `streaming=False` and there is no `.astream()`/`.stream()`
anywhere in the client. The user-facing SSE endpoint (`routes/chat.py`) runs `ainvoke()` and
emits progress + one final buffered `response` event; it never streamed model tokens. So the
proxy's inability to stream is **irrelevant to CC correctness today** — it only matters as
future-proofing and for browser-use.

---

## 4. Recommendation — Option (iii), with mandatory guardrails

Build a thin **OpenAI-compatible passthrough route** in aihub-api, then repoint CC and
browser-use at it with a few lines of config. It keeps CC's code essentially unchanged, is
OpenAI-shaped (so it also unblocks embeddings / future streaming / any OpenAI-SDK caller),
and reuses proven cloud primitives (license auth, key-swap, tenant logging).

The first-pass plan said "forward the body verbatim." That is **correct for tools** but the
adversarial pass found it is **wrong for `temperature`** — and that distinction is the whole
ballgame. The guardrails below are **not optional**:

1. **Model-aware param gate (BLOCKER).** CC hard-sends `temperature=0.3/0.0` on every client
   (`cc_config.py:551,565`). Reasoning models (gpt-5.x / o-series / opus-4.7+) return **HTTP
   400 on `temperature`** — this is exactly why the Anthropic proxy strips it
   (`anthropic_utils.py:64-75`). The new route must strip `temperature`/`top_p` for reasoning
   models on the **effective** model (or the client must stop sending them). "Verbatim for
   tools, gated for sampling params" is the correct rule.
2. **OpenAI-shaped error envelopes.** Today the proxy returns non-OpenAI bodies: 429 custom
   JSON (`rate_limiter.py:569`), 401 plain text (`views.py:664`), outer errors 400
   `{'error': str(e)}` (`views.py:682`). The OpenAI SDK expects `{"error":{"message","type","code"}}`;
   otherwise it mis-surfaces failures as opaque exceptions (the "fluent-but-failed" class).
   Also set the client's `max_retries=0` so a quota 429 doesn't trigger 3× upstream load.
3. **Client-side direct-Azure fallback (new SPOF).** After cutover, if aihub-api is down /
   cold-starting / deploying, CC and browser-use are **fully down**, not degraded — and a
   single CC turn is up to 6 sequential proxy round-trips. Keep the direct-Azure path as a
   circuit-breaker on proxy 5xx/timeout, and require App Service min-instances / always-on.
4. **Nail the license-header contract.** `AzureChatOpenAI(api_key=…)` puts the credential in
   `api-key`; `ChatOpenAI(api_key=…)` uses `Authorization: Bearer`; the route reads the
   license from `X-API-Key`. Pass the license via `default_headers={'X-API-Key': …}`, pass a
   dummy `api_key` to satisfy the SDK, and ensure the route strips/replaces it so the dummy
   never reaches Azure (→ 401). Verify with a live request; don't assume.
5. **"Copy the Anthropic SSE streamer" is a rewrite, not a copy.** That generator wraps the
   Anthropic *SDK* and inherits its tool-dropping whitelist. If/when you want OpenAI
   streaming, it's net-new `requests.post(..., stream=True)` + raw SSE re-yield code. Descope
   streaming for the first cut (CC doesn't need it) and say so.

---

## 5. Concrete plan

### Phase A — aihub-api: new route
1. Add `POST /openai/v1/chat/completions` in `views.py`, modeled on `openai_proxy_request_v3`
   (`:487`) but accepting a **standard** OpenAI/Azure body (no envelope).
2. Auth: reuse `is_key_valid_v2` (`:538`) reading `X-API-Key`; resolve tenant via
   `get_tenant_id_from_api_key`; keep `@rate_limit` (`:489`).
3. Inject the real upstream key via `swap_api_key_in_headers` / `API_KEY_MAPPINGS`
   (`:304-375`) or `cfg.AZURE_OPENAI_API_KEY`; strip client transport headers.
4. Forward the body **verbatim EXCEPT the model-aware param gate** (guardrail 1): pass
   `tools`/`tool_choice`/`response_format` untouched; strip `temperature`/`top_p` for
   reasoning models. Accept `reasoning_effort` (browser-use sends `medium`) without 400.
5. Support both URL shapes (Azure deployment-in-path + `api-version`, and OpenAI `/v1`).
6. Emit **OpenAI-shaped error envelopes** for 400/401/429/500 (guardrail 2).
7. Metering: `log_api_usage_v2` on success + error + auth-fail (mirror the Anthropic pattern
   at `:2609/2705/2726`). If streaming is ever added, force `stream_options.include_usage=true`
   so token counts still arrive.
8. Deploy — the route is additive, breaks nothing existing.

### Phase B — aihub-client-ai-dev: repoint callers
9. CC (`api_keys_config.py:213-303` Azure branch `api_base` @298, `cc_config.py:534-566`): set
   endpoint = `<api-host>/openai/v1/…`, `api_key` = license key, `default_headers={'X-API-Key': license}`.
   Pin `streaming=False` on the proxied path explicitly (don't rely on call-site discipline).
10. browser-use (`browser_use_config.py:resolve_openai_driver`): same repoint; keep `reasoning_effort`.
11. **BYOK:** branch on `source` in `get_openai_config` directly — `byok`/`system_openai` →
    **direct** with the user's own key (unproxied, unmetered — their spend); `azure` → the new
    proxy with the license key. Do **not** resurrect the dead `BYPASS_PROXY` env flag as the
    switch (race with `apply_byok_environment`); the branch logic *is* the switch.

### Phase C — gate cutover on these tests (all must pass)
12. **Tool-calling:** one CC `converse` turn through the route returns populated
    `response.tool_calls` and a tool actually executes (not garbled `to=functions.*` text).
13. **Reasoning-model param:** a `temperature`-bearing request to a gpt-5.x model succeeds
    (proves the param gate works) — this is the request that fails if guardrail 1 is skipped.
14. **Metering:** a proxied call writes a usage row with the right `TenantId` and non-zero tokens.
15. **BYOK:** a BYOK user's traffic goes direct with **no** usage row; a default user's is proxied+metered.
16. **Fallback:** with the proxy forced to 503, CC still answers via direct-Azure fallback.

---

## 6. Open questions for the owner
- **Billing granularity:** the ledger bills on `COUNT(DISTINCT RequestId)` (request quotas),
  not input/output token cost. Sufficient, or is true token-cost pass-through needed? (latter
  = ledger changes, out of scope for the route.)
- **Fail-open metering:** rate-limit + usage logging currently **fail open**
  (`rate_limiter.py:429`, `log_utils.py:222`). Acceptable for billable agent spend, or should
  it fail closed?
- **Model substitution:** if the chokepoint pins/substitutes models, it must be
  capability-aware (tools, structured output, temperature) or disabled for CC traffic.
- **browser-use Anthropic 2FA classifier** hits `/v1/messages` directly — out of scope here;
  separate decision if you want it metered via the Anthropic proxy.

**Bottom line:** not easy because the proxy is an envelope relay, not an Azure-shaped
endpoint. The right fix is a thin OpenAI-compatible route on the cloud (Option iii) + a few
lines of client config, closing a real per-tenant billing/governance gap. Tools survive the
verbatim path; the thing that will bite is `temperature` on reasoning models — gate cutover
on both a tool-call test and a reasoning-model test.
