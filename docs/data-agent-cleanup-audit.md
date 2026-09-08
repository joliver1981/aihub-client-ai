# Data Agent Cleanup Audit

Snapshot 2026-09-04 · tenant DB AIHUB on aihub.database.windows.net · live schemas checked on 10.0.0.6 · **read-only, no changes made**.

Test rounds since November 2025 left the tenant with **112 connections** pointing at only **15 real targets** (76 at AIRDB2 alone), **37 data agents** and **28 data dictionaries** (221 tables / 2,633 columns). Recommendation: one connection, one agent and one dictionary per real dataset.

## Summary

| Object | Total | Keep | Decide | Delete |
|---|---:|---:|---:|---:|
| Data agents | 37 | 5 | 1 | 31 |
| General agents | 273 | 27 | 28 | 218 |
| Connections | 112 | 4 | 6 | 102 |
| Dictionaries | 28 | 4 | 1 | 23 |
| Dictionary tables / columns | 221 / 2,633 | 67 / 941 | | 154 / 1,692 |

**What survives:** AIRDB2 → conn 59 + agents 281, 283 · AIRDB → conn 58 + agent 228 · ERPDB → conn 20 + agent 876 · Postgres edwdb → conn 28 + agent 14. Open decisions: agent 139 + conn 5 (LLMDB), conns 53/54 (Salesforce), 164 (R2TestDB), 168 (PHARMA), 135 (competency-test pin).

## Executed 2026-09-05

The cleanup was run in the order below on 2026-09-05 (10:02 to 10:20 local) through the app's own delete routes plus direct SQL for grants, workflow repoints and child-row hygiene. Every row touched was exported first to temp/cleanup_backup_20260905.json (2.1 MB) in the repo; the step log is temp/cleanup_log_20260905.txt. Tenant now: 34 agents (6 data, 28 general: the 27 keeps plus the re-seeded Regression Sim Agent 1028), 6 connections, 5 dictionaries (63 tables, 1,077 columns), no duplicate connection names, no orphan dictionary columns.

| Step | Result |
|---|---|
| 1. Grants | Analysts group granted on agents 281 and 876 (14 and 228 already had it). All four canonical data agents are now visible to the five Analysts users. |
| 2. Workflow bindings | Repointed 6 workflows from duplicate AIRDB2 connections to 59 (382, 384, 390, 393, 401, 1419) and workflow 429 from agent 469 to agent 14; one occurrence each, verified. Deleted test workflows 405, 383 and 1223 with their scheduler jobs. |
| 3. Data agents | 31 deleted, 0 failed; 4 knowledge items removed through the knowledge-delete route (disk + vector cleanup). |
| 4. General agents | 218 deleted, 0 failed; 236 knowledge items removed the same way. The 11 Regression Email Agent mailboxes with auto-respond are gone. |
| 5. Connections | 102 deleted, 0 failed (89 and 90 first). Their 154 dictionary tables and 1,692 columns cascaded; 3 pre-existing orphan columns and 7 orphan table questions removed. |
| 6. Open decisions | Decided 2026-09-05 10:22: connections 53 and 54 (Salesforce), 135 (competency-test pin) and 164 (R2TestDB) deleted (no agents, dictionaries or workflows referenced them; rows backed up to temp/cleanup_backup_20260905_decide_items.json). Kept: agent 139 with connection 5 (LLMDB) and connection 168 (PHARMA). Connections now: 5, 20, 28, 58, 59, 168. The 28 Decide general agents were then deleted at 12:47 (316 knowledge items removed through the knowledge route; agents 70 and 193 needed their two AgentCommunications history rows removed first because a foreign key blocks the delete). Backup: temp/cleanup_backup_20260905_decide_general.json. |
| 7. Dictionary refresh | ERPDB: CG_APPaymentRuns and CG_VendorInvoices added (39 tables, 702 columns); CG_VendorInvoiceLines saved only 14 columns because an AI-generated value exceeds a dictionary column width (failed twice, platform limit). AIRDB: TS.location_master refreshed (96 columns). EDWDB: all 9 live tables re-documented under public.* names with full enrichment (124 columns); the 7 old schema-less rows retired to temp/cleanup_backup_20260905_edwdb_old_dictionary.json. AIRDB2 left as it was. |
| 8. Fixtures and packs | Seed script: all fixtures resolve by name (connections 59/58, agents 281/283, secrets), Regression Sim Agent recreated as 1028. Pack 15 CLEAN 52 pass / 50 skip / 4 xfail, no regressions vs the 2026-09-03 baseline. Pack 16 CLEAN 8 pass / 16 skip (Tier A); resolver finds 281 and 283; landscape 62 = 56 general + 6 data. Pack 12 limited run 5/5 pass on agent 281 using the enhanced dictionary. |

- **Competency suite env.** tests_v2/competency/test_competency_workflow_execution.py defaults COMPETENCY_WORKFLOW_CONN to the deleted connection 135; run it with COMPETENCY_WORKFLOW_CONN=59 (same AIRDB2 database, same login).
- **Left as found.** The pre-existing orphans listed under other clutter (AgentGroups row for a missing agent, AgentEmailAddresses for deleted agents 440/376/496, an AgentMCPServers row for agent 232, agent 1's binding to missing connection 1), the backup tables, inactive schedules, soft-deleted automations and empty groups were not part of the ordered steps and are untouched.
- **One partial result.** ERPDB dbo.CG_VendorInvoiceLines has a table row and 14 of its columns; the batch save aborts on a string-truncation error in the llm metadata tables each time. Widening that column or trimming the AI output is a code change, so it was left partial.
- **Restore path.** Both backup files hold complete rows (Agents, child tables, Connections, llm_Tables, llm_Columns, Workflows before repoint), so any single object can be re-inserted by id if it turns out to be needed.

## Where the connections point

| Type | Server | Database | Conns | Data agents | Dicts | Keep | Decide | Delete |
|---|---|---|---:|---:|---:|---:|---:|---:|
| SQL Server | 10.0.0.6 | AIRDB2 | 76 | 18 | 14 | 1 | 1 | 74 |
| Postgres | 10.0.0.6 | edwdb | 9 | 3 | 7 | 1 | 0 | 8 |
| CData | none | None | 5 | 2 | 0 | 0 | 2 | 3 |
| SQL Server | 10.0.0.6 | AIRDB | 4 | 2 | 2 | 1 | 0 | 3 |
| SQL Server | 10.99.99.99 | FakeDB | 4 | 1 | 0 | 0 | 0 | 4 |
| SQL Server | 10.0.0.6 | LLMDB | 3 | 1 | 1 | 0 | 1 | 2 |
| SQL Server | 10.0.0.6 | ERPDB | 2 | 2 | 2 | 1 | 0 | 1 |
| PostgreSQL | 10.0.0.6 | edwdb | 2 | 0 | 0 | 0 | 0 | 2 |
| Postgres | 10.0.0.6 | edw | 1 | 0 | 1 | 0 | 0 | 1 |
| SQL Server | test | db1 | 1 | 0 | 1 | 0 | 0 | 1 |
| SQL Server | 999.999.999.999 | FAKEDB | 1 | 1 | 0 | 0 | 0 | 1 |
| SQL Server | localhost | Primary Business Database | 1 | 0 | 0 | 0 | 0 | 1 |
| SQL Server | 10.0.0.6 | R2TestDB | 1 | 0 | 0 | 0 | 1 | 0 |
| SQL Server | 10.0.0.6 | PHARMA | 1 | 0 | 0 | 0 | 1 | 0 |
| SQL Server | 10.0.0.250 | TESTDB | 1 | 0 | 0 | 0 | 0 | 1 |

## Data agents

| ID | Agent | Created | Connection → target | Signals | Verdict | Why |
|---:|---|---|---|---|---|---|
| 14 | EDW Postgres Agent | 2024-06-23 | 28 EDWDB (Postgres) → edwdb | feedback 66 (last 2026-01-21); groups Analysts, Ops, Demo, Maxnet | **Keep** | The one Postgres (edwdb) agent. Four group grants, 66 feedback rows, workflow 80. Keeps Postgres discovery testable. |
| 139 | EDW SQL Agent | 2025-08-20 | 5 EDW (SQL Server) → LLMDB | feedback 7 (last 2025-09-23); groups Analysts, Maxnet | **Decide** | Legacy LLMDB warehouse (Date / Inventory / ItemMaster / Sales; dictionary from 2024-25). Grants to Analysts and Maxnet; two old AI-generated workflows (25, 74) use connection 5. No test pack depends on it. Lean delete unless a demo still needs LLMDB. |
| 173 | Demo Data Assistant (TEST) | 2025-09-22 | 44 Demo Connection → ERPDB | groups Analysts | **Delete** | ERPDB through connection 44 'Demo Connection', whose dictionary is one junk row named 'table_1'. Move its Analysts grant to 876. |
| 228 | Inventory Data Assistant | 2025-10-24 | 58 AIRDB → AIRDB | groups Analysts | **Keep** | The one AIRDB (10-store) agent. Analysts can see it; connection 58 is used by 27 workflow fixtures and the seed script. |
| 231 | Data Retail Assistant 2 | 2025-10-29 | 59 AIRDB2 → AIRDB2 | CC routes 9 (last 2026-06-05); groups Analysts | **Delete** | Duplicate of 281 on the same connection 59. Its only value is the Analysts grant, so grant Analysts on 281 first. |
| 260 | AIRDB5_Wizard | 2025-12-16 | 77 AIRDB12_PW → AIRDB2 | CC routes 2 (last 2026-08-04) | **Delete** | Wizard experiment from Dec 2025 on throwaway connection 77 (AIRDB12_PW). |
| 281 | Retail Demo - AIRDB2 (15 stores) | 2026-01-12 | 59 AIRDB2 → AIRDB2 | CC routes 27 (last 2026-08-04) | **Keep** | Canonical AIRDB2 agent. Packs 12, 15 and 16 resolve it by this exact name; most-routed data agent in Command Center (27 routes, last 2026-08-04). |
| 282 | Demo AirDB Agent 3 | 2026-01-12 | 59 AIRDB2 → AIRDB2 | none | **Delete** | Unreferenced sibling of 283 on connection 59. |
| 283 | Demo AirDB Agent 4 | 2026-01-12 | 59 AIRDB2 → AIRDB2 | none | **Keep** | Pack 16 fixture (second AIRDB2 agent for the route-by-name check). The seed script recreates it if missing, so deleting it gains nothing. |
| 299 | My Sales Report Agent | 2026-02-17 | 50 Sales Excel Files → None | none | **Delete** | CData Excel experiment (connection 50), Feb 2026. |
| 347 | PO Processor | 2026-03-09 | 0 (missing) | none | **Delete** | Flagged as a data agent but bound to connection 0 (none) and carrying 11 tools: a misflagged general agent. Name duplicated four times among general agents. |
| 352 | Inventory Query Agent | 2026-03-14 | 90 AIRDB2 → AIRDB2 | CC routes 12 (last 2026-05-12) | **Delete** | Round-2 Command Center test agent on duplicate connection 90. Workflow 382 uses that connection, not the agent. |
| 354 | Broken Test 7291 | 2026-03-15 | 95 Broken Test 7291 Connectio → FAKEDB | CC routes 1 (last 2026-05-12) | **Delete** | Deliberately broken fixture (server 999.999.999.999). |
| 367 | Sales Reporter | 2026-03-18 | 111 Sales Reporter Connection → FakeDB | none | **Delete** | Fake server 10.99.99.99 with user bad_user; four identical connections 109-112. |
| 381 | Regression Data Agent | 2026-03-19 | 119 Regression Data Agent Conn → AIRDB2 | none | **Delete** | March 2026 regression leftover. Packs now resolve the oracle by name, never this agent. |
| 385 | Retail Data Agent | 2026-03-20 | 122 AIRDB2_Retail_Connection → AIRDB2 | none | **Delete** | 'Retail Data Agent' duplicate of 281 on connection 122. |
| 389 | Client Reporting Data Agent | 2026-03-20 | 124 Client Reporting AIRDB2 → AIRDB2 | none | **Delete** | 'Client Reporting Data Agent' copy #1 (connection 124) from the March 2026 export/import tests. |
| 390 | Test Data Agent | 2026-03-20 | 125 AIRDB2_Test_AI_User → AIRDB2 | none | **Delete** | Test agent on connection 125 whose dictionary documents one table. |
| 391 | Retail Data Agent | 2026-03-21 | 127 AIRDB2 Retail Operations → AIRDB2 | CC routes 1 (last 2026-05-12) | **Delete** | Second 'Retail Data Agent' duplicate of 281 on connection 127. |
| 393 | Client Reporting Data Agent | 2026-03-21 | 128 AIRDB2_SQL_Connection → AIRDB2 | none | **Delete** | 'Client Reporting Data Agent' copy #2 (connection 128). |
| 394 | Client Insights AI | 2026-03-21 | 128 AIRDB2_SQL_Connection → AIRDB2 | none | **Delete** | Same client-reporting family, connection 128. |
| 395 | reporting agent | 2026-03-21 | 129 AIRDB2_Client_Reporting → AIRDB2 | none | **Delete** | Same client-reporting family, connection 129 (no dictionary at all). |
| 396 | Client Reporting Data Agent | 2026-03-21 | 130 AIRDB2 Client Reporting → AIRDB2 | none | **Delete** | 'Client Reporting Data Agent' copy #3 (connection 130). Workflow 401 uses the connection, not the agent. |
| 425 | Regression Data Agent | 2026-03-23 | 143 Regression Data Agent Conn → AIRDB2 | none | **Delete** | Second 'Regression Data Agent' (connection 143). Same story as 381. |
| 469 | Sales Data Agent | 2026-04-11 | 154 Postgres EDWDB → None | CC routes 7 (last 2026-08-04); workflow 429 | **Delete** | Bound to connection 154, saved as CData with no server, so it cannot connect. Workflow 429 'Daily Yesterday Revenue Email' references this agent: repoint it to 14 first. |
| 471 | EDWDB Sales Data Agent | 2026-04-11 | 156 EDWDB_Postgres → edwdb | CC routes 1 (last 2026-04-18) | **Delete** | April 2026 Postgres-discovery test agent; 1-table dictionary on connection 156. |
| 478 | EDWDB Sales Data Agent (Postgres) | 2026-04-12 | 163 EDWDB_Postgres_Sales_AI → edwdb | CC routes 11 (last 2026-05-12) | **Delete** | Second Postgres-discovery test agent; 1-table dictionary on connection 163. |
| 493 | BU2 Sales Agent | 2026-04-14 | 89 AIRDB2 → AIRDB2 | CC routes 16 (last 2026-07-29) | **Delete** | Round-2 test agent on duplicate connection 89. Workflow 393 'Test Database Check' uses that connection. |
| 498 | FreshDB Sales Agent (Round2 Test) | 2026-04-15 | 59 AIRDB2 → AIRDB2 | CC routes 54 (last 2026-05-12) | **Delete** | Most-routed agent in Command Center history (54 routes) but every route came from the May 2026 test rounds. Duplicate of 281 on connection 59. |
| 813 | test-AIHUB0015-SalesAgent | 2026-07-10 | none | none | **Delete** | AIHUB-0015 fixture, no connection. |
| 816 | test-AIHUB0015-live-agent | 2026-07-11 | 199 (missing) | none | **Delete** | AIHUB-0015 fixture; its connection 199 no longer exists. |
| 820 | test-AIHUB0015-final7-agent | 2026-07-11 | none | none | **Delete** | AIHUB-0015 fixture, no connection. |
| 822 | test-AIHUB0015-salesagent | 2026-07-17 | none | none | **Delete** | AIHUB-0015 fixture, no connection. |
| 823 | test-AIHUB0015-agent2 | 2026-07-17 | none | none | **Delete** | AIHUB-0015 fixture, no connection. |
| 824 | test-AIHUB0015-SalesAgent2 | 2026-07-17 | none | none | **Delete** | AIHUB-0015 fixture, no connection. |
| 825 | test-AIHUB0047-agent | 2026-07-18 | 222 test-AIHUB0047-conn → AIRDB | none | **Delete** | AIHUB-0047 fixture on connection 222 (1-table dictionary). |
| 876 | AR Collections Assistant | 2026-08-02 | 20 ERPDB → ERPDB | CC routes 4 (last 2026-08-04) | **Keep** | The one ERPDB agent. Connection 20 carries the richest dictionary on the platform (36 tables) and 7 workflows. |

## General agents

| ID | Agent | Created | Tools | Signals | Verdict | Why |
|---:|---|---|---:|---|---|---|
| 1 | Folder Monitoring Agent edited-4694 edited-5476 edited-6071 edited-2241 | 2024-06-01 | 11 | groups Admins, Analysts, Ops, Demo, Maxnet; 11 workflows (106, 107, 246, 384, ...); quick jobs 0 enabled / 35; mailbox (inbound, auto-respond); 1 processed emails; 9 email approvals; 18 knowledge docs | **Keep** | Original folder-monitoring demo: 11 workflows, 35 quick jobs, mailbox, 18 knowledge docs, four group grants. Rename it: the test suffixes ('edited-4694 edited-5476 ...') are noise. |
| 2 | Server Health Keeper | 2024-06-01 | 7 | groups Admins, Ops, Demo; quick jobs 3 enabled / 4 | **Keep** | Server Health Keeper: three enabled quick jobs, Admins/Ops/Demo groups. |
| 3 | Network Reliability Agent | 2024-06-01 | 1 | groups Admins | **Decide** | Network Reliability Agent (2024, Admins group, 1 tool). Legacy; keep only if the original ops demo is still shown. |
| 5 | Warehouse Query Assistant | 2024-06-02 | 8 | groups Admins, Analysts, Maxnet; quick jobs 0 enabled / 2 | **Keep** | Warehouse Query Assistant: Admins/Analysts/Maxnet groups, 8 tools. |
| 11 | Data File Monitoring Agent | 2024-06-09 | 8 | groups General Group, Admins, superadmins7, Maxnet; quick jobs 1 enabled / 1 | **Keep** | Data File Monitoring Agent: enabled quick job, three group grants. |
| 18 | demo_restart_agent | 2024-10-18 | 2 | groups Ops | **Delete** | Throwaway test or demo-draft agent. |
| 19 | Document Search Assistant | 2024-10-19 | 8 | quick jobs 0 enabled / 1 | **Decide** | Document Search Assistant (2024, no group, one disabled quick job). Superseded by 210. |
| 31 | Agent 31 | 2025-01-23 | 3 | groups Analysts, Maxnet; 76 workflows (30, 40, 74, 78, ...); mailbox (inbound, auto-respond, wf-trigger 30); 10 processed emails; 9 knowledge docs; agent-to-agent; CC routes 10 | **Keep** | The inbound-email demo agent: 76 workflows reference it, mailbox with workflow trigger (workflow 30), 10 processed emails, 9 knowledge docs. Give it a real name. |
| 33 | AP Research Assistant | 2025-03-19 | 13 | groups AP Clerks, Demo 2; 2 workflows (280, 281) | **Keep** | AP Research Assistant: workflows 280/281 (Invoice Recon), AP Clerks + Demo 2 groups. |
| 34 | AR Research Assistant | 2025-03-22 | 15 | groups AP Clerks, Demo 2; 1 workflow (1341) | **Keep** | AR Research Assistant: workflow 1341 (AR Risk Review), AP Clerks + Demo 2 groups. |
| 35 | HR Agent | 2025-04-13 | 1 | none | **Delete** | Throwaway test or demo-draft agent. |
| 36 | File Analyzer | 2025-04-14 | 3 | groups Maxnet; 3 workflows (128, 130, 225); pinned: pack 11 run notes | **Keep** | File Analyzer: three workflows (128, 130, 225), Maxnet group. |
| 41 | HR Agent Timeout Test 1 | 2025-04-16 | 1 | none | **Delete** | Throwaway test or demo-draft agent. |
| 50 | HR Agent Benefits PNG | 2025-04-27 | 1 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 53 | HR Benefits Agent Excel | 2025-04-27 | 1 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 54 | HR Agent 0000 | 2025-04-29 | 1 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 55 | UHC Policy Assistant | 2025-04-29 | 1 | groups Maxnet; 1 knowledge docs | **Keep** | UHC Policy Assistant: Maxnet group with a knowledge doc (the policy demo). |
| 56 | UHC Policy Assistant Demo | 2025-04-29 | 1 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 58 | Build v1.2.10.P Test | 2025-05-12 | 1 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 59 | Monitoring agent for James | 2025-05-13 | 8 | none | **Decide** | 'Monitoring agent for James' (May 2025, 8 tools, no group). Yours to call. |
| 61 | Lease Assistant | 2025-05-18 | 6 | groups Maxnet; 3 knowledge docs | **Keep** | Lease Assistant: Maxnet group, 3 knowledge docs (the lease demo). |
| 62 | Lease Assistant (Intelligent) | 2025-05-21 | 6 | groups Maxnet | **Decide** | Lease Assistant (Intelligent): Maxnet group but no knowledge; looks like a superseded version of 61. |
| 63 | IT Incident Response Agent | 2025-05-26 | 9 | groups Maxnet; quick jobs 1 enabled / 5; 1 knowledge docs | **Keep** | IT Incident Response Agent: Maxnet group, enabled quick job, knowledge doc. |
| 70 | Alert Agent | 2025-06-08 | 6 | agent-to-agent | **Decide** | Alert Agent: part of the agent-to-agent demo with 31 (AgentCommunications 2025-06). Keep if that demo is still shown. |
| 84 | Master Agent | 2025-06-11 | 11 | groups Maxnet; pinned: pack 19 (TIERC sim target) | **Keep** | Master Agent: pack 19 resolves it by name as the Tier-C sim target; Maxnet group. |
| 89 | Database Monitoring Agent | 2025-06-18 | 8 | groups Analysts, Maxnet; quick jobs 0 enabled / 1 | **Keep** | Database Monitoring Agent: Analysts + Maxnet groups. |
| 90 | DEMO | 2025-06-18 | 3 | 6 knowledge docs | **Decide** | 'DEMO' (6 knowledge docs, no group). Unclear purpose; check the knowledge docs before deleting. |
| 91 | Demo Test 123 | 2025-06-18 | 3 | groups Maxnet; 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 92 | Delete this agent | 2025-06-18 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 93 | Demo Agent 11 | 2025-06-23 | 2 | groups Maxnet; 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 95 | Agent for monitoring | 2025-06-23 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 96 | Demo agent 123 | 2025-06-23 | 2 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 127 | Integration Monitor Agent | 2025-07-19 | 7 | groups Maxnet, Demo 2 | **Keep** | Integration Monitor Agent: Maxnet + Demo 2 groups. |
| 128 | Order Risk Monitor Agent | 2025-07-20 | 7 | groups Analysts, Maxnet; quick jobs 0 enabled / 2; 1 knowledge docs; agent-to-agent | **Keep** | Order Risk Monitor Agent: Analysts + Maxnet groups, quick jobs, agent-to-agent history. The original the seven '_imported' copies were cloned from. |
| 132 | Lease Assistant (New) | 2025-07-24 | 7 | groups Maxnet | **Decide** | Lease Assistant (New): Maxnet group, third lease-assistant version. Keep one of 61/62/132. |
| 140 | Resume Agent | 2025-08-21 | 6 | groups Analysts; 6 knowledge docs | **Keep** | Resume Agent: Analysts group, 6 knowledge docs, 6 tools (the resume demo). |
| 152 | Order Risk Monitor Agent_imported_20250902_102700 | 2025-09-02 | 7 | groups Analysts; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 153 | Order Risk Monitor Agent_imported_20250902_144025 | 2025-09-02 | 8 | groups Demo 2; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 159 | Resume Agent 2 | 2025-09-09 | 2 | groups Analysts; 10 knowledge docs | **Delete** | Extra copy of Resume Agent 140. (10 knowledge docs go with it.) |
| 180 | Sales Analysis Bot | 2025-09-24 | 2 | groups Analysts; code env | **Keep** | Sales Analysis Bot: Analysts group and an active code-interpreter environment. |
| 186 | QR Code Helper | 2025-09-25 | 5 | groups Analysts | **Decide** | QR Code Helper: Analysts group, custom-tool toy demo (Sept 2025). |
| 187 | Emoji Assistant | 2025-09-25 | 6 | groups Analysts; code env | **Decide** | Emoji Assistant: Analysts group, custom-tool toy demo with a code-interpreter environment. |
| 188 | Order Risk Monitor Agent_imported_20250929_171149 | 2025-09-29 | 7 | 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 189 | Payment Process Monitoring Agent | 2025-09-29 | 7 | 1 knowledge docs | **Decide** | Payment Process Monitoring Agent: 7 tools, 1 knowledge doc, no group (Sept 2025). |
| 190 | Resume Agent (v1.4 Test) | 2025-09-29 | 2 | groups Analysts; 5 knowledge docs | **Delete** | Extra copy of Resume Agent 140. (5 knowledge docs go with it.) |
| 193 | Resume Agent (v1.4 Volume Test) | 2025-09-30 | 1 | groups Analysts; 45 knowledge docs; agent-to-agent | **Decide** | Resume Agent (v1.4 Volume Test): 45 knowledge docs. Expensive to rebuild if a volume test is ever repeated; otherwise delete with the other Resume copies. |
| 195 | Check String Length Assistant | 2025-09-30 | 3 | groups Analysts | **Decide** | Check String Length Assistant: Analysts group, custom-tool toy demo. |
| 198 | Query Agent | 2025-10-02 | 8 | groups Analysts | **Keep** | Query Agent: Analysts group, 8 tools. |
| 200 | Excel File Query Assistant | 2025-10-05 | 8 | groups Analysts | **Keep** | Excel File Query Assistant: Analysts group, 8 tools (the Excel demo). |
| 207 | Web Search Agent | 2025-10-06 | 7 | groups Analysts | **Keep** | Web Search Agent: Analysts group, 7 tools. |
| 208 | Retail Trend Scout | 2025-10-06 | 7 | none | **Delete** | Throwaway test or demo-draft agent. |
| 209 | Product Trend Watcher | 2025-10-07 | 6 | groups Analysts | **Decide** | Product Trend Watcher: Analysts group; overlaps Web Search Agent 207. |
| 210 | Document Search Agent | 2025-10-08 | 12 | groups Analysts | **Keep** | Document Search Agent: Analysts group, 12 tools (the document-search demo). |
| 213 | Salesforce Query Assistant | 2025-10-11 | 4 | groups Analysts | **Decide** | Salesforce Query Assistant: Analysts group; follows the Salesforce connection (53/54) decision. |
| 220 | Order Risk Monitor Agent_imported_20251015_191753 | 2025-10-15 | 7 | groups Analysts; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 221 | Order Risk Monitor Agent_imported_20251015_193211 | 2025-10-15 | 7 | groups Analysts; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 222 | Order Risk Monitor Agent_imported_20251015_194543 | 2025-10-15 | 7 | groups Analysts; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 223 | Order Risk Monitor Agent_imported_20251015_195822 | 2025-10-15 | 7 | groups Analysts; 1 knowledge docs | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 225 | Resume Agent 3 | 2025-10-15 | 2 | groups Analysts; 3 knowledge docs | **Delete** | Extra copy of Resume Agent 140. |
| 237 | Order Agent Assistant | 2025-11-08 | 7 | groups Analysts; quick jobs 0 enabled / 1 | **Decide** | Order Agent Assistant: Analysts group, one disabled quick job (Nov 2025). |
| 248 | AD Agent | 2025-11-24 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 267 | Order Risk Monitor Agent_imported_20251015_200620 | 2025-12-23 | 8 | 1 knowledge docs; code env | **Delete** | Solutions Author import copy of Order Risk Monitor Agent 128. |
| 269 | Playwright Test Agent 1767661933 | 2026-01-05 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 270 | Resume Agent 4 | 2026-01-05 | 2 | 1 knowledge docs | **Delete** | Extra copy of Resume Agent 140. |
| 271 | Resume Agent 5 | 2026-01-05 | 2 | 1 knowledge docs | **Delete** | Extra copy of Resume Agent 140. |
| 272 | Resume Agent 6 | 2026-01-05 | 2 | 5 knowledge docs | **Delete** | Extra copy of Resume Agent 140. (5 knowledge docs go with it.) |
| 273 | Playwright Test Agent 1767664356 | 2026-01-05 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 274 | Playwright Test Agent 1767665953 | 2026-01-05 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 275 | Playwright Test Agent 1767754269 | 2026-01-06 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 276 | Playwright Test Agent 1767800592 | 2026-01-07 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 278 | Playwright Test Agent 1768011598 | 2026-01-09 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 279 | Playwright Test Agent 1768093556 | 2026-01-10 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 280 | Playwright Test Agent 1768256031 | 2026-01-12 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 284 | Playwright Test Agent 1768949678 | 2026-01-20 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 285 | Playwright Test Agent 1768957625 | 2026-01-20 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 286 | Playwright Test Agent 1769090837 | 2026-01-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 287 | Playwright Test Agent 1769224155 | 2026-01-23 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 288 | Test Email Agent 12345 | 2026-02-05 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 289 | Test Email Agent 999 | 2026-02-05 | 3 | none | **Delete** | Throwaway test or demo-draft agent. |
| 290 | Test Email Agent 888 | 2026-02-06 | 3 | none | **Delete** | Throwaway test or demo-draft agent. |
| 291 | Test Email Agent 777 | 2026-02-06 | 3 | none | **Delete** | Throwaway test or demo-draft agent. |
| 292 | Simple Agent 2 | 2026-02-06 | 4 | 51 knowledge docs | **Decide** | Simple Agent 2: 51 knowledge docs from the Feb 2026 GA tests. Delete unless that corpus is still wanted. |
| 293 | Simple Agent 3 | 2026-02-06 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 294 | Simple Agent 4 | 2026-02-06 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 295 | Simple Agent 5 | 2026-02-06 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 297 | Playwright Test Agent 1771004250 | 2026-02-13 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 298 | E2E Test Agent 1771037116 | 2026-02-13 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 300 | General Agent 2 | 2026-02-18 | 2 | 8 knowledge docs | **Decide** | General Agent 2: 8 knowledge docs (Feb 2026 CSV/tabular tests). |
| 303 | General Agent 3 | 2026-02-19 | 2 | 9 knowledge docs | **Decide** | General Agent 3: 9 knowledge docs (Feb 2026 CSV/tabular tests). |
| 304 | GP Agent 007 | 2026-02-21 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 305 | Playwright Test Agent 1771789703 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 306 | E2E Test Agent 1771789746 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 307 | Playwright Test Agent 1771803577 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 308 | E2E Test Agent 1771803620 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 309 | Playwright Test Agent 1771804599 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 310 | E2E Test Agent 1771804658 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 311 | Playwright Test Agent 1771810974 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 312 | E2E Test Agent 1771811036 | 2026-02-22 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 315 | Playwright Test Agent 1771895382 | 2026-02-23 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 316 | E2E Test Agent 1771895441 | 2026-02-23 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 320 | Gen Agent 008 | 2026-02-24 | 2 | 1 workflow (405) | **Delete** | Numbered agent-builder test agent. Test workflow 405 'Stripe Failed Payment Monito' reference it: delete those first. |
| 321 | Gen Agent 009 | 2026-02-24 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 322 | Gen Agent 010 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 323 | Gen Agent 011 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 324 | Gen Agent 012 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 325 | Gen Agent 013 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 326 | Gen Agent 014 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 327 | Gen Agent 015 | 2026-02-24 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 328 | Gen Agent 016 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 329 | Gen Agent 017 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 330 | Gen Agent 018 | 2026-02-24 | 2 | none | **Delete** | Numbered agent-builder test agent. |
| 331 | Gen Agent 019 | 2026-02-24 | 2 | 1 workflow (383); 3 knowledge docs | **Delete** | Numbered agent-builder test agent. Test workflow 383 'Content Reviewer' reference it: delete those first. |
| 332 | Gen Agent 020 | 2026-02-25 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 333 | Playwright Test Agent 1772236112 | 2026-02-27 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 334 | E2E Test Agent 1772236158 | 2026-02-27 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 337 | Test Bot | 2026-03-05 | 3 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 338 | Email Bot | 2026-03-05 | 4 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 341 | Test Runner | 2026-03-06 | 6 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 342 | Email Bot | 2026-03-06 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 343 | Support Helper | 2026-03-06 | 5 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 345 | PO Processor | 2026-03-07 | 7 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 346 | PO Processor | 2026-03-08 | 6 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 348 | PO Processor | 2026-03-12 | 10 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 349 | PO Processor | 2026-03-12 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 351 | Test Support Agent | 2026-03-14 | 2 | mailbox (idle); 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 356 | New Agent | 2026-03-15 | 2 | CC routes 2 | **Delete** | Unnamed agent created by a Command Center test turn. |
| 359 | New Agent | 2026-03-15 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 360 | Inventory Reporting Assistant | 2026-03-16 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 361 | Inventory Reporting Assistant | 2026-03-16 | 5 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 364 | Inventory Report Formatter | 2026-03-16 | 4 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 365 | HR Policy Assistant | 2026-03-17 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 366 | HR Policy Assistant | 2026-03-17 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 368 | HR Policy Assistant | 2026-03-18 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 369 | HR Policy Assistant for ACME Corporation | 2026-03-18 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 370 | HR Policy Assistant | 2026-03-18 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 371 | HR Policy Assistant | 2026-03-18 | 2 | 1 knowledge docs | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 372 | Agent 372 | 2026-03-18 | 5 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 373 | Customer Support Agent | 2026-03-18 | 5 | mailbox (inbound, auto-respond) | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 374 | Agent 374 | 2026-03-19 | 5 | mailbox (inbound, auto-respond) | **Delete** | Unnamed agent created by a Command Center test turn. |
| 375 | Customer Support Agent | 2026-03-19 | 2 | mailbox (inbound, auto-respond) | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 378 | Enhanced test agent | 2026-03-19 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 379 | Regression Email Agent | 2026-03-19 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 380 | Regression Email Agent v2 | 2026-03-19 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 383 | Enhanced test agent | 2026-03-20 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 384 | Regression Email Agent | 2026-03-20 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 386 | Retail Operations Manager | 2026-03-20 | 7 | 1 workflow (400); mailbox (inbound, auto-respond); 1 knowledge docs | **Keep** | Retail Operations Manager: workflow 400 'Daily Low Stock Report' references it and its mailbox is live. Keep this one, drop the copies 388/392. |
| 388 | Retail Operations Manager | 2026-03-20 | 8 | mailbox (inbound, auto-respond); 1 knowledge docs | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 392 | Retail Operations Manager | 2026-03-21 | 7 | mailbox (inbound, auto-respond); 1 knowledge docs | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 397 | AI Client Success Reporting Agent | 2026-03-21 | 7 | mailbox (idle) | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 399 | New Agent | 2026-03-21 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 405 | Agent 405 | 2026-03-22 | 5 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 407 | Regression Email Agent | 2026-03-22 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 409 | Regression KB Agent | 2026-03-22 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 410 | Regression Email Agent | 2026-03-22 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 413 | Regression KB Agent | 2026-03-22 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 414 | Regression Email Agent | 2026-03-22 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 415 | Agent 415 | 2026-03-22 | 4 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 416 | Agent 416 | 2026-03-22 | 3 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 418 | Regression KB Agent | 2026-03-22 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 419 | Regression Email Agent | 2026-03-22 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 420 | Agent 420 | 2026-03-22 | 3 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 422 | Regression KB Agent | 2026-03-22 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 423 | Regression Email Agent | 2026-03-22 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 424 | Agent 424 | 2026-03-23 | 5 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 426 | Regression KB Agent | 2026-03-23 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 427 | Regression Email Agent | 2026-03-23 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 428 | Agent 428 | 2026-03-23 | 3 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 430 | Regression KB Agent | 2026-03-23 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 431 | Regression Email Agent | 2026-03-23 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 432 | Agent 432 | 2026-03-23 | 3 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 434 | Regression KB Agent | 2026-03-23 | 2 | 1 knowledge docs | **Delete** | Pack fixture copy with one knowledge doc. |
| 435 | Regression Email Agent | 2026-03-23 | 4 | mailbox (inbound, auto-respond) | **Delete** | Pack fixture copy. Its mailbox is still inbound-enabled with auto-respond on: a live mail-loop risk. |
| 439 | New Agent | 2026-03-23 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 441 | New Agent | 2026-03-23 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 442 | New Agent | 2026-03-23 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 443 | Stripe Morning Briefing Agent | 2026-03-24 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 444 | New Agent | 2026-03-24 | 2 | none | **Delete** | Unnamed agent created by a Command Center test turn. |
| 445 | Stripe AI Assistant | 2026-03-24 | 8 | none | **Decide** | Stripe AI Assistant: 8 tools (Mar 2026 Stripe MCP demo?). Pairs with Stripe Morning Briefing Agent 443, which is a delete. |
| 456 | This is a basic agent 999 | 2026-03-27 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 457 | This is a basic agent | 2026-03-27 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 458 | Test Agent | 2026-03-28 | 2 | mailbox (inbound) | **Delete** | Throwaway test or demo-draft agent. |
| 459 | This is a basic agent XY | 2026-03-28 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 460 | This is a basic agent XY123 | 2026-03-29 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 461 | demo for mark | 2026-03-31 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 462 | E2E Test Agent | 2026-04-02 | 2 | mailbox (idle); 2 knowledge docs | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 463 | Test Agent "Quotes" & <Brackets> ??? | 2026-04-02 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 466 | Fedex Invoice Agent | 2026-04-05 | 2 | 1 knowledge docs | **Delete** | Throwaway test or demo-draft agent. |
| 467 | Leasing Agent 2.0 | 2026-04-05 | 6 | none | **Decide** | Leasing Agent 2.0: 6 tools, no group (Apr 2026). Fourth lease agent. |
| 468 | Meeting Notes Helper | 2026-04-07 | 2 | none | **Delete** | Command Center agent-builder test copy (Mar 2026). |
| 480 | Agent Pytest Result Check 20260413 | 2026-04-13 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 481 | Playwright Test Agent 1776092204 | 2026-04-13 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 482 | E2E Test Agent 1776092212 | 2026-04-13 | 2 | none | **Delete** | Playwright / E2E UI-test fixture (timestamp-named). |
| 486 | Test Agent 992 Test | 2026-04-14 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 487 | Test Agent 9975 Test | 2026-04-14 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 488 | Test Agent 99766 Test | 2026-04-14 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 489 | BU1 Email Test | 2026-04-14 | 4 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 490 | BU3 Helper | 2026-04-14 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 491 | BU6 Quick | 2026-04-14 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 492 | Agent 492 | 2026-04-14 | 5 | mailbox (idle) | **Delete** | Unnamed agent created by a Command Center test turn. |
| 494 | BU4 Delayed Use | 2026-04-14 | 4 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 495 | BU5 Agent Alpha | 2026-04-14 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 497 | Prod Readiness DB Tester | 2026-04-15 | 4 | none | **Delete** | Throwaway test or demo-draft agent. |
| 499 | Round2 Test Agent | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 500 | Round2 Test Agent 2 | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 501 | R2 BU1 Email Test | 2026-04-18 | 4 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 502 | R2 BU3 Helper | 2026-04-18 | 2 | CC routes 2 | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 503 | R2 BI1 Simple | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 504 | Paradox Agent | 2026-04-18 | 2 | none | **Delete** | Throwaway test or demo-draft agent. |
| 505 | R2 BU4 Delayed Use | 2026-04-18 | 2 | mailbox (idle) | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 506 | R2 BI4 Rejected for testing | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 507 | R2 IR3 Test Agent | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 508 | CT14 Built Via Converse | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 509 | R2 BU5 Agent Alpha | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 510 | R2 BU5 Agent Beta | 2026-04-18 | 2 | mailbox (idle); CC routes 2 | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 511 | R2 BI9 Transition | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 512 | R2 BU6 Quick | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 513 | R2 BU7 Tracked | 2026-04-18 | 5 | mailbox (idle) | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 514 | R2v BI4 Rejected for testing | 2026-04-18 | 2 | none | **Delete** | Round-2 Command Center test round (Apr 2026). |
| 517 | Compliance Agent | 2026-05-08 | 4 | quick jobs 0 enabled / 2 | **Keep** | Compliance Agent: named in tests_v2/api/test_compliance_routes.py; the compliance feature's demo agent. |
| 518 | Vendor Compliance | 2026-05-09 | 4 | 2 knowledge docs; MCP server 29 | **Keep** | Vendor Compliance: MCP server 29 bound, 2 knowledge docs, used in the pack 21 scenario transcripts. |
| 521 | Compliance - MegaMart | 2026-05-17 | 1 | none | **Decide** | Compliance - MegaMart: retailer compliance demo (May 2026), 1 tool. |
| 522 | Compliance - Dollar General | 2026-05-17 | 1 | quick jobs 0 enabled / 1 | **Decide** | Compliance - Dollar General: retailer compliance demo (May 2026), one disabled quick job. |
| 614 | General Agent 00009 | 2026-05-20 | 2 | groups devs, Regs; 2 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 671 | Tour knowledge test | 2026-05-21 | 2 | 2 knowledge docs | **Delete** | Pack / journey fixture; the pack recreates it. |
| 672 | Created by full-feature tour | 2026-05-21 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 688 | Vector indexing probe | 2026-05-24 | 2 | 1 knowledge docs | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 729 | large_invoice_reports competency test agent | 2026-05-24 | 2 | 3 knowledge docs | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 733 | Isolated competency test agent for 02_fedex_invoice_megaretail_q1_2026.pdf | 2026-05-24 | 2 | 1 knowledge docs | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 734 | large_invoice_reports competency test agent | 2026-05-24 | 2 | 2 knowledge docs | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 736 | large_invoice_reports competency test agent | 2026-05-24 | 2 | 8 knowledge docs | **Delete** | tests_v2 competency fixture; the suite recreates it. (8 knowledge docs go with it.) |
| 778 | General Purpose Agent 001 | 2026-05-25 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 779 | Competency-test agent for file-creation tools | 2026-05-27 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 780 | Competency-test agent for file-creation tools | 2026-05-27 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 781 | probe | 2026-05-27 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 782 | Competency-test agent for file-creation tools | 2026-05-27 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 783 | Competency-test agent for file-creation tools | 2026-05-27 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 784 | model probe | 2026-05-28 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 785 | Competency-test agent for file-creation tools | 2026-05-28 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 786 | Competency-test agent for file-creation tools | 2026-05-28 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 787 | Competency-test agent for file-creation tools | 2026-05-28 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 788 | probe | 2026-05-28 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 789 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 790 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 791 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 792 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 793 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 794 | Competency-test agent for file-creation tools | 2026-05-29 | 2 | none | **Delete** | tests_v2 competency fixture; the suite recreates it. |
| 795 | User Agent 00009 | 2026-06-01 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 800 | Fin Agent 001 | 2026-06-18 | 2 | 3 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 811 | Pricing Download Process | 2026-06-22 | 9 | mailbox (idle) | **Decide** | Pricing Download Process: 9 tools and a mailbox (Jun 2026); looks like the portal-download demo agent. |
| 812 | Gen Agent 00010 | 2026-06-30 | 4 | mailbox (inbound, auto-respond); 2 processed emails; 1 email approvals; 1 knowledge docs | **Decide** | Gen Agent 00010: the mailbox used to verify the rebuilt email-approval gate (2 processed emails, 1 approval, Jul 2026). Its inbound + auto-respond flags are still on. |
| 814 | test-AIHUB0021-Control | 2026-07-10 | 2 | mailbox (idle) | **Delete** | AIHUB-NNNN bug fixture; the bug is signed off. |
| 826 | test-AIHUB0047-a1 | 2026-07-18 | 2 | none | **Delete** | AIHUB-NNNN bug fixture; the bug is signed off. |
| 827 | test-AIHUB0047-a2 | 2026-07-18 | 2 | none | **Delete** | AIHUB-NNNN bug fixture; the bug is signed off. |
| 834 | DCT13 Repo Search Agent | 2026-07-25 | 13 | none | **Decide** | DCT13 Repo Search Agent: pack 13 fixture (13 tools). The battery recreates it, but it was left on purpose after the last run. |
| 835 | DCT13 Knowledge Lease Agent | 2026-07-25 | 2 | 7 knowledge docs | **Decide** | DCT13 Knowledge Lease Agent: pack 13 fixture with 7 knowledge docs; same as 834. |
| 836 | DCT13 Scale Lease Agent | 2026-07-26 | 2 | 120 knowledge docs; pinned: pack 13 load_scale_corpus.py | **Keep** | DCT13 Scale Lease Agent: 120-document scale corpus (hours of ingest); load_scale_corpus.py targets it by name. |
| 842 | Finance Library | 2026-07-31 | 2 | 3 knowledge docs; pinned: pack 21 scenario 04 + demo control panel | **Keep** | Finance Library: pack 21 scenario 04 and the demo control panel registry reference it by name. |
| 859 | b-owned | 2026-08-02 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 860 | x | 2026-08-02 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 861 | b-owned | 2026-08-02 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 862 | x | 2026-08-02 | 2 | none | **Delete** | Pack / journey fixture; the pack recreates it. |
| 896 | Monthly Exception Reporting Assistant | 2026-08-03 | 8 | none | **Decide** | Monthly Exception Reporting Assistant: 8 tools (Aug 2026, Views / HTML-email work). |
| 904 | Weekly Store Sales Assistant | 2026-08-04 | 2 | none | **Decide** | Weekly Store Sales Assistant (Aug 2026); likely pairs with workflow 1419 'Weekly Store Sales Report'. |
| 915 | Gen Agent 1001 | 2026-08-25 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 916 | Gen Agent 1002 | 2026-08-25 | 6 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 917 | Gen Agent 1003 | 2026-08-25 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 956 | Pack 22 GA code interpreter competency | 2026-08-26 | 3 | 1 knowledge docs | **Delete** | Pack / journey fixture; the pack recreates it. |
| 968 | Gen Agent 1004 | 2026-08-27 | 2 | 1 knowledge docs | **Delete** | Numbered agent-builder test agent. |
| 969 | GA capability battery (48) | 2026-08-27 | 3 | 5 knowledge docs | **Delete** | Pack / journey fixture; the pack recreates it. (5 knowledge docs go with it.) |
| 998 | Pack 22 GA code interpreter competency | 2026-08-29 | 3 | 2 knowledge docs | **Delete** | Pack / journey fixture; the pack recreates it. |
| 1007 | Doc Agent 1001 | 2026-08-31 | 7 | pinned: The Agent doc-ACL smoke (pack 20 tool_smoke_g3) | **Keep** | Doc Agent 1001: the only AgentDocumentTypes binding (lease_agreement); The Agent document-ACL smoke uses it. |
| 1015 | Pack 22 GA code interpreter competency | 2026-09-03 | 3 | 5 knowledge docs | **Delete** | Pack / journey fixture; the pack recreates it. (5 knowledge docs go with it.) |

## Connections

| ID | Name | Type | Target | Login | Refs | Verdict | Why |
|---:|---|---|---|---|---|---|---|
| 5 | EDW (SQL Server) | SQL Server | LLMDB @ 10.0.0.6 | ai_user | agents 139; dictionary 4 tables; 2 workflows (25 RCM Process, 74 AI Gen Iter 2) | **Decide** | Follows the agent 139 decision (legacy LLMDB). Workflows 25 and 74 reference it. |
| 18 | EDW (Postgres) | Postgres | edw @ 10.0.0.6 | postgres | dictionary 6 tables | **Delete** | Points at database 'edw' (not edwdb); stale 2024-25 dictionary, no agent. |
| 19 | dummy connection (do not use) | SQL Server | db1 @ test | test | dictionary 6 tables | **Delete** | Named 'dummy connection (do not use)'; 2024 dictionary of fake tables. |
| 20 | ERPDB | SQL Server | ERPDB @ 10.0.0.6 | ai_user | agents 876; dictionary 36 tables; 7 workflows (40 Chargeback Dispute, 279 Invoice Matching, 381 Daily Inventory Low St, ...) | **Keep** | Canonical ERPDB connection: agent 876, 36-table dictionary, 7 workflows. |
| 22 | EDW SQL Test | SQL Server | LLMDB @ 10.0.0.6 | ai_user | none | **Delete** | Unused LLMDB duplicate of 5. |
| 28 | EDWDB (Postgres) | Postgres | edwdb @ 10.0.0.6 | postgres | agents 14; dictionary 7 tables; 1 workflow (80 AI Generated Flow v5) | **Keep** | Canonical Postgres connection: agent 14, 7-table dictionary, workflow 80. |
| 44 | Demo Connection | SQL Server | ERPDB @ 10.0.0.6 | ai_user | agents 173; dictionary 1 tables | **Delete** | ERPDB duplicate of 20 with a one-row junk dictionary ('table_1'); agent 173. |
| 49 | PostgreSQL (1.4 Test) | PostgreSQL | edwdb @ 10.0.0.6 | postgres | none | **Delete** | Duplicate of connection 28 (same server, database and login). |
| 50 | Sales Excel Files | CData | None @ none | None | agents 299 | **Delete** | CData Excel experiment; agent 299. |
| 52 | EDWDB | PostgreSQL | edwdb @ 10.0.0.6 | postgres | none | **Delete** | Duplicate of connection 28 (same server, database and login). |
| 53 | Salesforce | CData | None @ none | None | none | **Decide** | CData Salesforce. No agent, no dictionary. Keep only if the Salesforce demo is still wanted. |
| 54 | Salesforce_CData | CData | None @ none | aihub_dev@maxnet-tech.com | none | **Decide** | CData Salesforce (second copy). Same question as 53. |
| 58 | AIRDB | SQL Server | AIRDB @ 10.0.0.6 | ai_user | agents 228; dictionary 10 tables; 27 workflows (1249 test-0034-wf, 1250 test-0034-creds, 1251 test-0034r2b, ...) | **Keep** | Canonical AIRDB connection: agent 228, 27 workflow fixtures, seed script. |
| 59 | AIRDB2 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 231, 281, 282, 283, 498; dictionary 10 tables | **Keep** | Canonical AIRDB2 connection: agents 281/283, the seed script resolves 'AIRDB2' by name. |
| 62 | AIRDB3 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | dictionary 10 tables | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 68 | AIRDB4_Wizard | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | dictionary 10 tables | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 69 | AIRDB5_Wizard | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | dictionary 5 tables | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 70 | AIRDB5_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 71 | AIRDB6_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 72 | AIRDB7_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 73 | AIRDB8_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 74 | AIRDB9_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 75 | AIRDB10_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 77 | AIRDB12_PW | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 260; dictionary 10 tables | **Delete** | Duplicate of connection 59 (same server, database and login). AIRDB3..12 wizard / password-flow experiments (Nov-Dec 2025). |
| 78 | My Sales Report | CData | None @ none | None | none | **Delete** | CData Excel experiment, unreferenced. |
| 81 | AIRDB2_SQL_Server | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 82 | AIRDB2_SQL_Server | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 83 | AIRDB2_SQL_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 84 | AIRDB2_SQL_Server | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 85 | AIRDB2_SQL_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 86 | (blank) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 87 | (blank) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 88 | (blank) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 89 | AIRDB2 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 493; dictionary 13 tables; 1 workflow (393 Test Database Check) | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 90 | AIRDB2 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 352; dictionary 13 tables; 1 workflow (382 Inventory Record Count) | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 91 | AIRDB2_Inventory_ReadOnly | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 92 | AIRDB2_WeeklySales | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | 1 workflow (384 Weekly Sales Summary R) | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 93 | AIRDB2_DQ_Check | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 94 | AIRDB2_DQ_AutoCheck | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 95 | Broken Test 7291 Connection | SQL Server | FAKEDB @ 999.999.999.999 | fake | agents 354 | **Delete** | Deliberately broken (999.999.999.999); agent 354. |
| 96 | AIRDB2_DQ_4829 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 97 | AIRDB2_Inventory_ReadOnly_6471 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 98 | AIRDB2_WeeklySales_7392 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 99 | AIRDB2_Inventory_ReadOnly_8341 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 100 | AIRDB2_WeeklySales_2947 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 101 | AIRDB2_Inventory_ReadOnly_3719 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 102 | AIRDB2_Sales_Report_4158 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 103 | AIRDB2_DQ_5826 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | 1 workflow (390 DQ Audit 5826) | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 104 | AIRDB2_Inventory_AI | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 105 | AIRDB2_Inventory_AI_User | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 106 | AIRDB2_Weekly_Order_AI | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 107 | AIRDB2_Inventory_AI_User | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 108 | AIRDB2_Inventory_AI_User | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 109 | Sales Reporter Connection | SQL Server | FakeDB @ 10.99.99.99 | bad_user | none | **Delete** | Fake server 10.99.99.99 / bad_user (Sales Reporter fixtures). |
| 110 | Sales Reporter Connection | SQL Server | FakeDB @ 10.99.99.99 | bad_user | none | **Delete** | Fake server 10.99.99.99 / bad_user (Sales Reporter fixtures). |
| 111 | Sales Reporter Connection | SQL Server | FakeDB @ 10.99.99.99 | bad_user | agents 367 | **Delete** | Fake server 10.99.99.99 / bad_user (Sales Reporter fixtures). |
| 112 | Sales Reporter Connection | SQL Server | FakeDB @ 10.99.99.99 | bad_user | none | **Delete** | Fake server 10.99.99.99 / bad_user (Sales Reporter fixtures). |
| 113 | AIRDB2_Expense_Approval | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 114 | AIRDB2_Inventory_AI_Assistant | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 115 | AIRDB2_Inventory_AI_User | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). Round-2 Command Center test round (spring 2026). |
| 116 | Regression Data Agent Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 117 | AIRDB2 Regression | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 118 | Regression Data Agent Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 119 | Regression Data Agent Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 381; dictionary 13 tables | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 120 | Regression Data Agent - AIRDB2 (10.0.0.6) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 121 | Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 122 | AIRDB2_Retail_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 385; dictionary 10 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 123 | AIRDB2 Retail Operations | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | dictionary 10 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 124 | Client Reporting AIRDB2 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 389; dictionary 13 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 125 | AIRDB2_Test_AI_User | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 390; dictionary 1 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 126 | Retail AIRDB2 Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | 1 workflow (1419 Weekly Store Sales Rep) | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 127 | AIRDB2 Retail Operations | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 391; dictionary 13 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 128 | AIRDB2_SQL_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 393, 394; dictionary 13 tables | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 129 | AIRDB2_Client_Reporting | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 395 | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 130 | AIRDB2 Client Reporting | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 396; 1 workflow (401 Weekly AI Client Succe) | **Delete** | Duplicate of connection 59 (same server, database and login). |
| 131 | Regression Data Agent Connection 20260322 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 132 | AIRDB2_Regression_Alert | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 133 | Regression Data Agent - AIRDB2 (10.0.0.6) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 134 | AIRDB2_Regression_Alert_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 135 | Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Decide** | Pinned by tests_v2/competency/test_competency_workflow_execution.py (default COMPETENCY_WORKFLOW_CONN=135). Delete it and run that suite with COMPETENCY_WORKFLOW_CONN=59, or keep it. |
| 136 | AIRDB2_Regression_Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 137 | Regression Data Agent Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 138 | Regression Alert AIRDB2 Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 139 | Regression Data Agent - AIRDB2 (10.0.0.6 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 140 | AIRDB2 Regression Alert Connection | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 141 | Regression Data Agent - AIRDB2 (10.0.0.6 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 142 | AIRDB2_Regression_LowStock | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 143 | Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | agents 425 | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 144 | AIRDB2_Regression_LowStock_New | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 145 | Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 146 | AIRDB2_Regression_New | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 147 | Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 148 | AIRDB2_Regression_Alert_New | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 149 | Regression Data Agent - AIRDB2 (10.0.0.6 ai_user) | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 150 | Regression Data Agent Connection 20260323 | SQL Server | AIRDB2 @ 10.0.0.6 | ai_user | none | **Delete** | Duplicate of connection 59 (same server, database and login). March 2026 regression-run leftover. |
| 154 | Postgres EDWDB | CData | None @ none | None | agents 469 | **Delete** | Saved as CData with no server or database; agent 469 cannot connect through it. |
| 155 | EDWDB Sales Postgres Connection | Postgres | edwdb @ 10.0.0.6 | postgres | dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 156 | EDWDB_Postgres | Postgres | edwdb @ 10.0.0.6 | postgres | agents 471; dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 157 | EDWDB_Postgres | Postgres | edwdb @ 10.0.0.6 | postgres | none | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 158 | EDWDB PostgreSQL Connection | Postgres | edwdb @ 10.0.0.6 | postgres | none | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 159 | EDWDB_Postgres | Postgres | edwdb @ 10.0.0.6 | postgres | dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 160 | EDWDB Sales Postgres (10.0.0.6) | Postgres | edwdb @ 10.0.0.6 | postgres | dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 161 | EDWDB_Postgres_Sales | Postgres | edwdb @ 10.0.0.6 | postgres | dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 162 | Primary Business Database | SQL Server | Primary Business Database @ localhost | None | none | **Delete** | localhost 'Primary Business Database', no user: junk row. |
| 163 | EDWDB_Postgres_Sales_AI | Postgres | edwdb @ 10.0.0.6 | postgres | agents 478; dictionary 1 tables | **Delete** | Duplicate of connection 28 (same server, database and login). April 2026 Postgres-discovery test copies. |
| 164 | R2TestDB Connection | SQL Server | R2TestDB @ 10.0.0.6 | dbuser | none | **Decide** | R2TestDB on 10.0.0.6 (user dbuser). Round-2 test database; delete if that database is gone. |
| 167 | connection_5_test | SQL Server | LLMDB @ 10.0.0.6 | ai_user | none | **Delete** | Unused LLMDB duplicate of 5 ('connection_5_test'). |
| 168 | PHARMA | SQL Server | PHARMA @ 10.0.0.6 | ai_user | none | **Decide** | PHARMA on 10.0.0.6. A separate dataset with no agent or dictionary; keep only if a pharma demo is planned. |
| 212 | test-AIHUB0022-badconn | SQL Server | TESTDB @ 10.0.0.250 | baduser | none | **Delete** | AIHUB-0022 bad-connection fixture (10.0.0.250). |
| 216 | AIRDB Sales Analysis | SQL Server | AIRDB @ 10.0.0.6 | ai_user | none | **Delete** | Unreferenced AIRDB duplicate of 58. |
| 221 | test-AIHUB0021-badpw | SQL Server | AIRDB @ 10.0.0.6 | ai_user | none | **Delete** | AIHUB-0021 bad-password fixture. |
| 222 | test-AIHUB0047-conn | SQL Server | AIRDB @ 10.0.0.6 | ai_user | agents 825; dictionary 1 tables | **Delete** | AIHUB-0047 fixture; agent 825; 1-table dictionary. |

## Data dictionaries

| Conn | Connection | Target | Tables | Columns | Documented | Verdict | Note |
|---:|---|---|---:|---:|---|---|---|
| 20 | ERPDB | ERPDB @ 10.0.0.6 | 36 | 642 | 2026-08-04 to 2026-08-04 | **Keep** | Keep and refresh: 36 of 42 live tables documented; 78 live columns undocumented; 203 derived metrics. |
| 89 | AIRDB2 | AIRDB2 @ 10.0.0.6 | 13 | 148 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 89. |
| 90 | AIRDB2 | AIRDB2 @ 10.0.0.6 | 13 | 152 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 90. |
| 119 | Regression Data Agent Connection | AIRDB2 @ 10.0.0.6 | 13 | 151 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 119. |
| 124 | Client Reporting AIRDB2 | AIRDB2 @ 10.0.0.6 | 13 | 154 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 124. |
| 127 | AIRDB2 Retail Operations | AIRDB2 @ 10.0.0.6 | 13 | 148 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 127. |
| 128 | AIRDB2_SQL_Connection | AIRDB2 @ 10.0.0.6 | 13 | 149 | 2026-08-04 to 2026-08-04 | **Delete** | Goes with connection 128. |
| 58 | AIRDB | AIRDB @ 10.0.0.6 | 10 | 90 | 2025-10-25 to 2025-10-25 | **Keep** | Keep and refresh: 66 of 67 live columns documented (one column added since 2025-10-25 is missing). |
| 59 | AIRDB2 | AIRDB2 @ 10.0.0.6 | 10 | 94 | 2025-10-29 to 2025-10-29 | **Keep** | Keep. 10 TS tables, all 70 live columns matched, 24 derived metrics, no _bak noise. Consider a refresh only if you want the richer derived metrics of the March copies. |
| 62 | AIRDB3 | AIRDB2 @ 10.0.0.6 | 10 | 93 | 2025-11-08 to 2025-11-08 | **Delete** | Goes with connection 62. |
| 68 | AIRDB4_Wizard | AIRDB2 @ 10.0.0.6 | 10 | 93 | 2025-12-16 to 2025-12-16 | **Delete** | Goes with connection 68. |
| 77 | AIRDB12_PW | AIRDB2 @ 10.0.0.6 | 10 | 93 | 2025-12-17 to 2025-12-17 | **Delete** | Goes with connection 77. |
| 122 | AIRDB2_Retail_Connection | AIRDB2 @ 10.0.0.6 | 10 | 139 | 2026-03-20 to 2026-07-01 | **Delete** | Goes with connection 122. |
| 123 | AIRDB2 Retail Operations | AIRDB2 @ 10.0.0.6 | 10 | 142 | 2026-03-20 to 2026-07-01 | **Delete** | Goes with connection 123. |
| 28 | EDWDB (Postgres) | edwdb @ 10.0.0.6 | 7 | 54 | 2024-06-23 to 2025-06-14 | **Keep** | Keep and refresh via AI Discovery: 7 tables but only 1 enriched column (older manual dictionary). |
| 18 | EDW (Postgres) | edw @ 10.0.0.6 | 6 | 36 | 2024-06-23 to 2025-04-19 | **Delete** | Goes with connection 18. |
| 19 | dummy connection (do not use) | db1 @ test | 6 | 51 | 2024-01-13 to 2024-07-26 | **Delete** | Goes with connection 19. |
| 69 | AIRDB5_Wizard | AIRDB2 @ 10.0.0.6 | 5 | 57 | 2025-12-16 to 2025-12-16 | **Delete** | Goes with connection 69. |
| 5 | EDW (SQL Server) | LLMDB @ 10.0.0.6 | 4 | 61 | 2024-01-13 to 2025-04-10 | **Decide** | Follows the agent 139 decision. |
| 44 | Demo Connection | ERPDB @ 10.0.0.6 | 1 | 1 | 2025-09-23 to 2025-09-23 | **Delete** | Goes with connection 44. Documents a single table. |
| 125 | AIRDB2_Test_AI_User | AIRDB2 @ 10.0.0.6 | 1 | 12 | 2026-03-21 to 2026-03-21 | **Delete** | Goes with connection 125. Documents a single table. |
| 155 | EDWDB Sales Postgres Connection | edwdb @ 10.0.0.6 | 1 | 9 | 2026-04-11 to 2026-04-11 | **Delete** | Goes with connection 155. Documents a single table. |
| 156 | EDWDB_Postgres | edwdb @ 10.0.0.6 | 1 | 9 | 2026-04-11 to 2026-04-11 | **Delete** | Goes with connection 156. Documents a single table. |
| 159 | EDWDB_Postgres | edwdb @ 10.0.0.6 | 1 | 10 | 2026-04-12 to 2026-04-12 | **Delete** | Goes with connection 159. Documents a single table. |
| 160 | EDWDB Sales Postgres (10.0.0.6) | edwdb @ 10.0.0.6 | 1 | 11 | 2026-04-12 to 2026-04-12 | **Delete** | Goes with connection 160. Documents a single table. |
| 161 | EDWDB_Postgres_Sales | edwdb @ 10.0.0.6 | 1 | 9 | 2026-04-12 to 2026-04-12 | **Delete** | Goes with connection 161. Documents a single table. |
| 163 | EDWDB_Postgres_Sales_AI | edwdb @ 10.0.0.6 | 1 | 9 | 2026-04-12 to 2026-04-12 | **Delete** | Goes with connection 163. Documents a single table. |
| 222 | test-AIHUB0047-conn | AIRDB @ 10.0.0.6 | 1 | 16 | 2026-07-18 to 2026-07-18 | **Delete** | Goes with connection 222. Documents a single table. |

## Order of operations

1. Grant the Analysts group on agents 281, 876, 14 and 228 (today only 231, 173, 228, 14 and 139 carry group grants).
2. Repoint or delete the workflows that bind to delete-candidate connections and agents (list below); confirm with the seed script dry-run that the oracle still resolves.
3. Delete the 31 data agents marked Delete.
4. Delete the 218 general agents marked Delete, starting with the 11 Regression Email Agent copies (mailboxes still inbound + auto-respond) and the 26 Playwright/E2E timestamp agents.
5. Delete the connections marked Delete (their dictionaries go with them). Do the two extra rows named 'AIRDB2' (89, 90) first: name resolution takes the first match.
6. Decide the six open items: agent 139 + conn 5, conns 53/54, 164, 168, 135.
7. Refresh the four kept dictionaries with AI Discovery (ERPDB missing 6 tables / 78 columns; AIRDB missing 1 column; EDWDB almost no enrichment).
8. Re-run the seed script and packs 12, 15, 16.

### Bindings to repoint before deleting

- Workflow 25 'RCM Process' uses connection 5 (EDW (SQL Server)) → repoint to a kept connection or delete the workflow.
- Workflow 74 'AI Gen Iter 2' uses connection 5 (EDW (SQL Server)) → repoint to a kept connection or delete the workflow.
- Workflow 393 'Test Database Check' uses connection 89 (AIRDB2) → repoint to 59 or delete the workflow.
- Workflow 382 'Inventory Record Count' uses connection 90 (AIRDB2) → repoint to 59 or delete the workflow.
- Workflow 384 'Weekly Sales Summary Report' uses connection 92 (AIRDB2_WeeklySales) → repoint to 59 or delete the workflow.
- Workflow 390 'DQ Audit 5826' uses connection 103 (AIRDB2_DQ_5826) → repoint to 59 or delete the workflow.
- Workflow 1419 'Weekly Store Sales Report' uses connection 126 (Retail AIRDB2 Connection) → repoint to 59 or delete the workflow.
- Workflow 401 'Weekly AI Client Success Report' uses connection 130 (AIRDB2 Client Reporting) → repoint to 59 or delete the workflow.
- Workflow 429 'Daily Yesterday Revenue Email' references agent 469 → repoint to agent 14 or delete the workflow.
- Workflow 405 'Stripe Failed Payment Monitor' references general agent 320 (Gen Agent 008) and workflow 383 'Content Reviewer' references 331 (Gen Agent 019): both are test workflows, delete them with the agents.
- Agent 814 (test-AIHUB0021-Control) has a mailbox with workflow trigger 1223 'test-AIHUB0016-InvoiceExport': delete that workflow with the agent.

## Other clutter worth clearing

- **Duplicate names are a live hazard.** Three connections are named 'AIRDB2' (59, 89, 90); 'Regression Data Agent Connection (10.0.0.6 AIRDB2 ai_user)' exists five times; 'AIRDB2_Inventory_AI_User' four times; three connections have a blank name. Anything that resolves by name (seed script, pack runners, The Agent's ask-by-name) picks the first hit.
- **General agents are classified above.** 27 keep, 28 decide, 218 delete. The keep set is the curated demo persona (Maxnet / Analysts / Demo groups), the workflow- and quick-job-referenced agents, and the pack-pinned names.
- **Workflows: all 267 are active.** 55 are 'Guided Workflow vN', 14 'AI Gen*', 14 'test-*', 4 'truth-test*', 4 'New Workflow <hash>'. 90 were created in Nov 2025 and 87 in July 2026 (pack runs). Deactivate or delete the test families; the packs recreate what they need.
- **Scheduled jobs pointing at nothing.** All 8 inactive 'agent' jobs target agents that no longer exist (68, 73, 79, 80, 81, 86, 87, 90). 46 inactive automation jobs sit beside 6 active ones. 344 of 377 Automations rows are status 'deleted' but still in the table.
- **Orphan rows.** AgentConnections: agent 1 → connection 1, agent 347 → connection 0, agent 816 → connection 199 (all missing). AgentEmailAddresses for deleted agents 440, 376, 496. One AgentGroups row and three llm_Columns rows with no parent.
- **Ad-hoc backup tables in the tenant database.** Agents_20251002, llm_Tables_20251023, llm_Tables_bak, llm_Columns_20251023, llm_Columns_bak and PlatformUsageLog_BAK_20251209 (112k rows). Drop once you are sure nothing reads them (nothing in the code does).
- **Groups.** Developers, End Users, superadmins4, superadmins5 and Engineering have zero users and zero agents. Analysts (5 users) is the realistic end-user group; it is what the kept agents should be granted to.
- **Code-interpreter environments.** 98 AgentEnvironments rows with 614 package rows, but only 3 active agent assignments.
- **AIRDB2 itself carries three _bak_* tables** left by the 2026-07-27 transaction_id repair. AI Discovery documents them as if they were business tables. Drop them on 10.0.0.6 once the repair is final, or every future discovery picks them up again.
- **Make fixtures self-cleaning.** Connections 116-150 are 35 rows left by March 2026 regression runs that created but never deleted their fixtures. That is a test-harness change for later, not part of this cleanup.

## Method and caveats

Inventory read from Agents, AgentConnections, Connections, AgentGroups, AgentTools, AgentKnowledge, AgentEmailAddresses, ScheduledJobs, Workflows (workflow_data scanned for agent_id / connection_id), llm_Tables, llm_Columns, cc_RouteMemory, ai_feedback and QuickJob. Each dictionary was scored against the live INFORMATION_SCHEMA of AIRDB2, AIRDB and ERPDB; the 'phantom' columns are all is_calculated derived metrics, not corruption. PlatformUsageLog has no per-agent attribution; The Agent's SQLite store references no data agent; automations code holds no numeric connection ids. Test-pack dependencies: test_human/_scripts/seed_target_fixtures.py, pack runners 12/15/16/19, tests_v2/competency.
