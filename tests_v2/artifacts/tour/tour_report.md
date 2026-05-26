# Full Feature Tour Report

Generated: 2026-05-24 20:09:17
Mode: FULL (CRUD actions enabled)

## Summary

- Pages visited: **50** / 50 catalogued
- Passed: **46**
- Failed: **4**
- Skipped: **0**
- CRUD actions executed successfully: **8**

## Per-page results

| Status | Page | URL | Load (ms) | Status code | Primary ctrl | Console errors | Action |
|:--:|---|---|---:|---:|:--:|---:|---|
| ✅ | Dashboard | `/dashboard` | 1678 | 200 | ✅ | 0 | — |
| ✅ | Chat | `/chat` | 2531 | 200 | ✅ | 0 | passed: agent=748 uploads=[('01_helix_employee_handbook_2026.docx', 200), ('04_aurora_qu |
| ✅ | Landing | `/landing` | 726 | 200 | ✅ | 0 | — |
| ✅ | Index | `/index` | 2344 | 200 | ✅ | 0 | — |
| ✅ | API Check | `/api_check` | 552 | 200 | ✅ | 0 | — |
| ✅ | Custom Agent Builder | `/custom_agent_enhanced` | 2651 | 200 | ✅ | 0 | passed: agent_id=749 ui_visible=False api_delete=ok |
| ✅ | Custom Data Agent | `/custom_data_agent` | 2330 | 200 | ✅ | 0 | — |
| ✅ | Assistants list | `/assistants` | 2534 | 200 | ✅ | 0 | — |
| ✅ | Agent Dashboard | `/agent_dashboard` | 697 | 200 | ✅ | 0 | — |
| ✅ | Agent Communication | `/agent_communication` | 687 | 200 | ✅ | 0 | — |
| ✅ | Document Manager | `/document-manager` | 2549 | 200 | ✅ | 0 | — |
| ✅ | Document Processor | `/document_processor` | 2493 | 200 | ✅ | 0 | — |
| ✅ | New Document Job | `/document_processor/job/new` | 2319 | 200 | ✅ | 0 | — |
| ✅ | Document Scheduler | `/document_scheduler` | 2282 | 200 | ✅ | 0 | — |
| ✅ | Document Summarizer | `/document_summarizer` | 2335 | 200 | ✅ | 0 | — |
| ✅ | Data Assistants | `/data_assistants` | 2331 | 200 | ✅ | 0 | — |
| ✅ | Data Chat | `/data_chat` | 2509 | 200 | ✅ | 0 | passed: prompt_sent='List the first 3 tables. TOUR_probe' response_observed=True |
| 🔴 | Data Dictionary | `/data_dictionary` | 2378 | 200 | ✅ | 1 | — |
| ✅ | Data Explorer | `/data_explorer` | 2324 | 200 | ✅ | 0 | — |
| ✅ | Connections | `/connections` | 2389 | 200 | ✅ | 0 | — |
| ✅ | Workflow Builder | `/workflow_tool` | 1002 | 200 | ✅ | 0 | passed: wf_id=1198 ui_visible=True api_delete=ok |
| ✅ | Workflow Monitoring | `/monitoring` | 949 | 200 | ✅ | 0 | — |
| ✅ | Approvals | `/approvals` | 2324 | 200 | ✅ | 0 | — |
| ✅ | Scheduled Jobs | `/jobs` | 2352 | 200 | ✅ | 0 | passed: job_id=171 sched_id=271 ui_visible=False api_delete=ok |
| ✅ | Integrations | `/integrations` | 2379 | 200 | ✅ | 0 | passed: template=azure_blob_storage integration_id=45 ui_visible=False api_delete=ok |
| ✅ | MCP Servers (admin) | `/mcp_servers` | 2303 | 200 | ✅ | 0 | passed: mcp_id=43 ui_visible=True api_delete=ok |
| ✅ | MCP User Servers | `/mcp_user_servers` | 2333 | 200 | ✅ | 0 | — |
| ✅ | My Connections | `/my-connections` | 2342 | 200 | ✅ | 0 | — |
| ✅ | Custom Tools | `/custom_tool` | 2317 | 200 | ✅ | 0 | — |
| ✅ | Compliance Mgmt | `/compliance` | 2289 | 200 | ✅ | 0 | passed: retailer_id=121 ui_visible=True api_delete=ok |
| ✅ | Compliance Schemas | `/compliance/schemas` | 2332 | 200 | ✅ | 0 | — |
| ✅ | Users | `/users` | 2336 | 200 | ✅ | 0 | — |
| ✅ | Groups | `/groups` | 2471 | 200 | ✅ | 0 | — |
| ✅ | API Keys Config | `/admin/api-keys` | 2358 | 200 | ✅ | 0 | — |
| 🔴 | Identity Settings | `/admin/identity_settings` | 2344 | 404 | ❌ | 0 | — |
| ✅ | Tier Usage | `/admin/tier` | 887 | 200 | ✅ | 0 | — |
| ✅ | Caution Settings | `/admin/caution-settings` | 585 | 200 | ✅ | 0 | — |
| ✅ | Feedback Analysis | `/admin/feedback-analysis` | 2493 | 200 | ✅ | 0 | — |
| 🔴 | Summarization Dashboard | `/admin/summarization-dashboard` | 609 | 500 | ❌ | 1 | — |
| ✅ | Env Assignments | `/assignments/manage` | 2453 | 200 | ✅ | 0 | — |
| ✅ | Env Assignments (alt) | `/environments/assignments` | 2409 | 200 | ✅ | 0 | — |
| ✅ | Local Secrets | `/local-secrets` | 2333 | 200 | ✅ | 0 | — |
| ✅ | System Logs | `/system_logs` | 2320 | 200 | ✅ | 0 | — |
| 🔴 | Telemetry Settings | `/telemetry` | 2266 | 404 | ❌ | 0 | — |
| ✅ | LLM Unit Test | `/llm_unit_test` | 2299 | 200 | ✅ | 0 | — |
| ✅ | Solutions Gallery | `/solutions` | 2298 | 200 | ✅ | 0 | — |
| ✅ | Solutions Author | `/solutions/author` | 2287 | 200 | ✅ | 0 | — |
| ✅ | New Solution | `/solutions/author/new` | 2919 | 200 | ✅ | 0 | — |
| ✅ | Email Processing History | `/email-processing/history` | 2313 | 200 | ✅ | 0 | — |
| ✅ | User Preferences | `/preferences/` | 2293 | 200 | ✅ | 0 | — |

## Failure detail

### 🔴 Data Dictionary (`/data_dictionary`)

- **Console errors (1):**
   - `Error: TypeError: Failed to fetch
    at sendMessage (http://localhost:5001/data_chat:2128:9)
    at HTMLButtonElement.onclick (http://localhost:5001/data_chat:1821:89)`

### 🔴 Identity Settings (`/admin/identity_settings`)

- **Nav error:** `server returned 404`
- **Status code:** 404
- **Primary control not visible**

### 🔴 Summarization Dashboard (`/admin/summarization-dashboard`)

- **Nav error:** `server returned 500`
- **Status code:** 500
- **Primary control not visible**
- **Console errors (1):**
   - `Failed to load resource: the server responded with a status of 500 (INTERNAL SERVER ERROR)`

### 🔴 Telemetry Settings (`/telemetry`)

- **Nav error:** `server returned 404`
- **Status code:** 404
- **Primary control not visible**


## CRUD actions executed

- ✅ **Chat** (`/chat`) — agent=748 uploads=[('01_helix_employee_handbook_2026.docx', 200), ('04_aurora_quarterly_financials_q1_2026.xlsx', 200)] chat_status=200 grounded_fact_seen=True answer_preview='Helix Innovations “was f
- ✅ **Custom Agent Builder** (`/custom_agent_enhanced`) — agent_id=749 ui_visible=False api_delete=ok
- ✅ **Data Chat** (`/data_chat`) — prompt_sent='List the first 3 tables. TOUR_probe' response_observed=True
- ✅ **Workflow Builder** (`/workflow_tool`) — wf_id=1198 ui_visible=True api_delete=ok
- ✅ **Scheduled Jobs** (`/jobs`) — job_id=171 sched_id=271 ui_visible=False api_delete=ok
- ✅ **Integrations** (`/integrations`) — template=azure_blob_storage integration_id=45 ui_visible=False api_delete=ok
- ✅ **MCP Servers (admin)** (`/mcp_servers`) — mcp_id=43 ui_visible=True api_delete=ok
- ✅ **Compliance Mgmt** (`/compliance`) — retailer_id=121 ui_visible=True api_delete=ok