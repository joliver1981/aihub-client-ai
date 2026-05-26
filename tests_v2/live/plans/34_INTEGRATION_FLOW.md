# Module 34: Integration Flow — Live Smoke
**Purpose:** Exercise the universal integration manager and its REST API against a running AI Hub instance. Covers adding, listing, fetching, executing, and deleting an integration, including credential-masking verification.

**Time estimate:** 20–30 minutes
**Prerequisites:**
  - AI Hub running on `http://10.0.0.7:5001` (or your `HOST_PORT`)
  - Developer-role login OR valid `X-API-Key`
  - Built-in templates present on disk under `integrations/builtin/` (verify via `GET /api/integrations/templates/storage-info`)
  - For OAuth-based tests: a Microsoft account that can sign in to Azure AD with at least `Files.Read` (OneDrive) or `Sites.Read.All` (SharePoint)

---

## INT-0: Pre-flight — confirm templates are loaded
**Action:** `GET /api/integrations/templates` then `GET /api/integrations/templates/storage-info`.
**Expected:** Non-empty `templates` array; `builtin_count` ≥ 5 (we ship at least quickbooks, shopify, slack, onedrive, sharepoint).
**Pass criteria:** :green_circle: The expected built-in template keys (`quickbooks_online`, `shopify`, `slack`, `onedrive`, `sharepoint_online`, `sharepoint_online_app`) all appear.

---

## INT-1: Create an API-key integration with a dummy secret
**Action:** `POST /api/integrations` with:
```json
{
  "template_key": "shopify",
  "integration_name": "Live-Test Shopify",
  "credentials": {"api_key": "PLAINTEXT-WILL-BE-SHREDDED-12345"},
  "instance_config": {"shop_domain": "example.myshopify.com"}
}
```
**Expected:** 200; response contains `integration_id` (int). The plaintext secret value is **NOT** echoed back in the response body.
**Pass criteria:** :green_circle: the substring `PLAINTEXT-WILL-BE-SHREDDED-12345` does not appear in the response text.

---

## INT-2: List integrations and find the new one
**Action:** `GET /api/integrations`.
**Expected:** 200; the new integration appears in the list. No `credentials_reference` or other secret-bearing field is leaked in the JSON.
**Pass criteria:** :green_circle: integration is listed; response contains no `{{LOCAL_SECRET:` strings.

---

## INT-3: Fetch the integration detail page
**Action:** `GET /api/integrations/{integration_id}`.
**Expected:** 200; the returned `integration` payload has the safe shape: `integration_id`, `integration_name`, `template_key`, `platform_name`, `auth_type`, `is_connected`, `operations`. **No** `credentials_reference`, no plaintext secret, no `{{LOCAL_SECRET:…}}` strings.
**Pass criteria:** :green_circle: response body does not contain any of: `PLAINTEXT-WILL-BE-SHREDDED`, `{{LOCAL_SECRET:`, `credentials_reference`.

---

## INT-4: Verify the secret was stored under the encrypted store
**Action:** On the host, examine `data/secrets/secrets.json.enc` (encrypted at rest). The plaintext must not appear when grepping the encrypted file.
**Expected:** `grep "PLAINTEXT-WILL-BE-SHREDDED-12345" data/secrets/secrets.json.enc` returns no matches.
**Pass criteria:** :green_circle: plaintext is not visible in the encrypted file.

---

## INT-5: Test connection (will fail — that's fine)
**Action:** `POST /api/integrations/{integration_id}/test`.
**Expected:** Returns a structured response indicating the test failed (the dummy `api_key` won't actually authenticate against Shopify). The response shape is intact: `success` is false, `error` is a non-empty string. No 5xx.
**Pass criteria:** :green_circle: returns a structured failure (HTTP 200 with `success: false` is fine).

---

## INT-6: Update the integration
**Action:** `PUT /api/integrations/{integration_id}` with `{"integration_name": "Live-Test Shopify Renamed"}`.
**Expected:** 200; the listing shows the new name.
**Pass criteria:** :green_circle: rename succeeds.

---

## INT-7: Rotate the credential
**Action:** `PUT /api/integrations/{integration_id}` with `{"credentials": {"api_key": "ROTATED-VALUE-67890"}}`.
**Expected:** 200; the response body does **NOT** contain the new plaintext value. A subsequent `GET /api/integrations/{integration_id}` also does not contain the new plaintext.
**Pass criteria:** :green_circle: rotated value is never echoed.

---

## INT-8: Execute a no-op operation (if the template defines one)
**Action:** `POST /api/integrations/{integration_id}/execute` with `{"operation": "health_check"}` (templates that have it; otherwise skip).
**Expected:** 200; structured `success`/`error` response. For an unauthenticated test integration this will likely return `success: false` — that's acceptable here, we're testing wiring not auth.
**Pass criteria:** :green_circle: structured response returned within 30 seconds.

---

## INT-9: Delete the integration (soft delete)
**Action:** `DELETE /api/integrations/{integration_id}`.
**Expected:** 200; subsequent `GET /api/integrations` does not include the deleted integration. Querying it directly returns 404 (or it returns an `is_active=0` row when admin-listing).
**Pass criteria:** :green_circle: integration disappears from the default listing.

---

## INT-10: Credential aftermath
**Action:** Inspect the encrypted secrets file again — the deleted integration's secret may still be present (intentional, for restore), but no decryption occurs without an active integration row.
**Expected:** No errors; the secret blob may persist but the deleted integration cannot be exercised.
**Pass criteria:** :green_circle: subsequent execute against the deleted ID returns a clean 404 or "integration not found" error.

---

## INT-11: Cleanup
**Action:** None required (soft-deletion is the cleanup).
**Pass criteria:** :green_circle: tenant state is acceptable.
