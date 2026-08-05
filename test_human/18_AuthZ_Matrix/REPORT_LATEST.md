# AuthZ Matrix - 20260804_094951

- Tier: A+B | Baseline: `results_20260803_163127.json`

## Verdict: **CLEAN** - 11 PASS / 6 XFAIL

## Matrix

| check | status | evidence |
|---|---|---|
| a1_global_auth_middleware_wired | XFAIL | middleware-module-present=True, init_auth_middleware called by=NOBODY, anon GET /api/scheduler/jobs -> 200 32317b (reachable=True) |
| a2_allowlist_contract | PASS | allowlist=11 (pinned 11); ADDED=none; removed=none |
| a3_dry_run_disabled | XFAIL | env='true', .env=unset (must not be true) |
| a4_login_rejects_bad_password | PASS | authenticated-with-wrong-password=False (must be False) |
| a5_anonymous_browser_redirects | PASS | landed=http://localhost:5001/login?next=%2Fusers, http=200 |
| a6_anonymous_api_401 | PASS | http=401 (want 401/403) |
| a7_session_cookie_flags | PASS | HttpOnly=True, SameSite=False, hdr='session=.eJwlzjGOAzEIheG7uN4CjA04lxmBwUq00q40k1RR7p5RUr6_ePqeZVt7Htdyue-P_CnbLcqlVBoSAWoRI' |
| a8_logout_invalidates_session | PASS | pre-logout http=200, post-logout http=401, still-readable=False |
| a9_role1_blocked_from_admin | PASS | blocked={'users_page': True, 'save_workflow': True, 'automations_create': True} |
| a10_bad_api_key_rejected | PASS | http=401 (want 401/403) |
| a11_anonymous_get_sweep_ratchet | PASS | probed=229, ANONYMOUSLY REACHABLE=29 vs pinned 29 (ratchet: must not grow - PASS is NOT 'no problem'), errors=0, cleaned-up-minted-rows=0; first: /, /admin/caution-settings, /api/available-icons, /api/caution/level, /api/caution/l |
| b1_role1_cannot_escalate_own_role | PASS | role after self-escalation attempt=1 (want 1) |
| b2_no_horizontal_agent_access | PASS | A reading B's agent 902 -> http=404, leaked=False |
| b3_anonymous_write_actually_persists | XFAIL | anon POST http=201, id=372, visible to an authenticated reader=True |
| b4_unauth_sensitive_reads | XFAIL | /get/users=401/122b/leak=False; /get/connections=401/122b/leak=False; /get/agents=302/241b/leak=False; /api/scheduler/jobs=200/32317b/leak=True; /api/workflow/approvals=200/313966b/leak=True; LEAKING=2 |
| b5_role1_write_sweep | XFAIL | add_agent=http200/created=True; scheduler_job=http201/created=True; add_connection=http403/created=None; PRIVILEGED WRITES THAT LANDED=2 |
| b6_sweep_reachable_are_harmless | XFAIL | reachable=29, sensitive-looking=3: /admin/caution-settings, /api/caution/user, /api/connection-types |

## Anonymously reachable routes (a11)

| route | response |
|---|---|
| `/` | 200 20459b |
| `/admin/caution-settings` | 200 24974b |
| `/api/available-icons` | 200 587b |
| `/api/caution/level` | 200 72b |
| `/api/caution/levels` | 200 73b |
| `/api/caution/user` | 200 94b |
| `/api/connection-types` | 200 3438b |
| `/api/setup/status` | 200 133b |
| `/api/workflow/analytics` | 200 24644b |
| `/api/workflow/approvals` | 200 313966b |
| `/api/workflow/assistant/history` | 200 142b |
| `/api/workflow/builder/training-stats` | 200 129b |
| `/api/workflow/logs` | 200 14742b |
| `/api/workflow/stats/counts` | 200 167b |
| `/api_check` | 200 557b |
| `/chat/data` | 200 199b |
| `/chat/data/explain` | 200 19b |
| `/chat/email` | 200 93b |
| `/document/config` | 200 83b |
| `/export_results` | 200 3b |
| `/get/odbc_drivers` | 200 467b |
| `/get_results` | 200 44b |
| `/index` | 200 55569b |
| `/landing` | 200 20459b |
| `/login` | 200 56525b |
| `/test` | 200 32b |
| `/test-bare` | 200 44b |
| `/test-crash` | 200 21b |
| `/test-template` | 200 53185b |