# Installed Smoke — 20260902_205921 (INSTALLED 10.0.0.6)

- Base: `http://10.0.0.6:5001`

## Verdict: **FAILURES** — 3 FAIL / 3 PASS

| area | check | status | evidence |
|---|---|---|---|
| Services | svc_health | ✅ PASS | {'app': 200, 'command_center': 200, 'agent_service': 200, 'builder': 200} |
| Data/NLQ | nlq_real_answer | ✅ PASS | agent 2 answered in 8.6s without the fallback; payload=2855b \| tried: agent 2: http=200 8.6s fallback=False no_schema=False |
| Command Center | cc_chat_turn | ✅ PASS | http=200, contains-75=True, tail=fd-0b5d-4176-94c5-6cd6ec99d884", "trace_id": "0719b137-b2cc-44f2-8b91-bb6f8e7ecbf9"}  event: done data: {"session_id": "1bea28fd-0b5d-4176-94c5-6cd6ec99d884"}   |
| Command Center | cc_delegation_endpoint | ❌ FAIL | FROZEN-BUILD PACKAGING DEFECT on agent 2: http=500 "No module named 'command_center.artifacts.data_export'" |
| Command Center | cc_data_agent_turn | ❌ FAIL | http=200, agent=2, number=True, delegation-failed=True, packaging=['No module named'], tail="Agent returned status 500: No module named 'command_center.artifacts.data_export'", "ts": "2026-09-02T20:59:15.539497"}]}]}  event: done data: {"session_id": "87485617-684b-4920-8582-2e064cf9119a"}   |
| The Agent | agent_service_turn | ❌ FAIL | health model=claude-opus-5, stream=364b, contains-75=False, tail=ata: {"type": "error", "error": "Claude Code returned an error result: success", "session_id": "b58dd776-804b-49db-a09a-45105bf6b472"}  data: {"type": "done"}   |
