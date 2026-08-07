# Pack 20 — The Agent (A0 read-only gate)

**Run:** 2026-08-07T16:15:56  
**Target:** http://127.0.0.1:5111  
**Result: 7/7 PASS**

| # | Check | Result | Evidence |
|---|---|---|---|
| A0-1 | health endpoint up, correct service/model | ✅ PASS | {"status": "ok", "service": "agent_service", "host": "0.0.0.0", "port": 5111, "model": "claude-opus-5", "app_root": "C:\\src\\aihub-client-ai-dev", "main_app": "http://127.0.0.1:5001", "allow_all_users": false, "anthropic_key_present": true} |
| A0-2 | chat without token is rejected (401) | ✅ PASS | HTTP 401 |
| A0-3 | signed platform JWT accepted | ✅ PASS | {"user":{"username":"pack20-runner","name":"Pack 20 Runner","role":3},"model":"claude-opus-5"} |
| A0-4 | lists connections via the tool (grounded, not invented) | ✅ PASS | tools=['list_data_connections'] text="I'll pull the list of configured connections.\nHere are the data connections configured in AI Hub — 113 in total. Grouping them makes the list readable:\n\n**Core / named systems**\n\| id \| Name \| Database" |
| A0-5 | inspects schema via the tool in a continued session | ✅ PASS | tools=['get_connection_schema', 'get_connection_schema', 'get_connection_schema'] text="I'll pick **PHARMA (id 168)** — it's a distinctly named, non-test connection.\nPHARMA has 7 tables. Let me look inside two of them.\n**PHARMA (id 168)** — a pharma speaker-bureau database, 7 tables in t" |
| A0-6 | declines mutations honestly (read-only preview, no fake success) | ✅ PASS | tools=['list_playbooks'] text="I can't build that — authoring is disabled in this read-only preview. No automation was created. Let me at least check whether something similar already exists.\n**Nothing was created.** Authoring is disabled in this read-only preview — I can inspect and query, but I can't build workflows, automation" |
| A0-7 | answers run-history from execution rows | ✅ PASS | tools=['list_recent_runs', 'list_playbooks'] text="I'll check the recent execution history.\nLet me map those workflow IDs to names.\n## Recent activity — yes, there are failures\n\n**Today (2026-08-07) — 4 of 7 scheduled runs failed:**\n\n\| Time \| Workflow" |
