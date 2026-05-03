"""Adversarial training examples that explicitly teach validator invariants.

Each entry in ADVERSARIAL_CASES is a hand-authored pair where:
  - `plan` deliberately omits or confuses something the validator catches,
    phrased the way a real user would phrase it.
  - `commands` is the correct output the model should learn to produce.
  - `footgun` names the specific rule/category being taught.

These are the highest-leverage examples in the corpus: every one directly
encodes a known failure mode of frontier models on this task. They should be
over-sampled during training and stratified in eval.

Add more cases whenever a real user report or shadow-mode log reveals a new
failure pattern.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Dict, List

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.capture.extract_seeds import load_system_prompt_text

logger = logging.getLogger("synthesize.adversarial")


ADVERSARIAL_CASES: List[Dict] = [
    {
        "footgun": "trigger_node_skipped",
        "plan": (
            "1. Scheduled Trigger: Every day at 6am.\n"
            "2. Database node: Run SELECT * FROM sales WHERE date = CAST(GETDATE() AS DATE) "
            "on connection 3, store in variable rows.\n"
            "3. Alert node: Email rows to finance@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Database",
                    "label": "Today's Sales",
                    "config": {
                        "connection": "3",
                        "dbOperation": "query",
                        "query": "SELECT * FROM sales WHERE date = CAST(GETDATE() AS DATE)",
                        "saveToVariable": True,
                        "outputVariable": "rows",
                        "continueOnError": False,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": "Email Sales",
                    "config": {
                        "alertType": "email",
                        "recipients": "finance@somecompany.com",
                        "messageTemplate": "Today's sales:\n${rows}",
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "file_read_save_to_variable",
        "plan": (
            "1. Folder Selector: pick all *.txt files from E:\\notes into var files.\n"
            "2. Loop over files using variable currentFile.\n"
            "3. File node: read the content of the current file and keep it for the next step.\n"
            "4. AI Action (agent_id 5): summarize the file content into variable summary.\n"
            "5. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Folder Selector",
                    "label": "Select Notes",
                    "config": {
                        "folderPath": "E:\\notes",
                        "selectionMode": "all",
                        "filePattern": "*.txt",
                        "outputVariable": "files",
                        "failIfEmpty": True,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "Loop",
                    "label": "For each file",
                    "config": {
                        "sourceType": "auto",
                        "loopSource": "${files}",
                        "itemVariable": "currentFile",
                        "indexVariable": "fileIndex",
                        "maxIterations": "100",
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "File",
                    "label": "Read File",
                    "config": {
                        "operation": "read",
                        "filePath": "${currentFile}",
                        "saveToVariable": True,
                        "outputVariable": "fileContent",
                    },
                    "position": {"left": "420px", "top": "40px"},
                    "node_id": "node-2",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "AI Action",
                    "label": "Summarize",
                    "config": {
                        "agent_id": "5",
                        "prompt": "Summarize the following text:\n\n${fileContent}",
                        "outputVariable": "summary",
                        "continueOnError": False,
                    },
                    "position": {"left": "620px", "top": "40px"},
                    "node_id": "node-3",
                },
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "End Loop",
                    "label": "End Loop",
                    "config": {"loopNodeId": "node-1"},
                    "position": {"left": "820px", "top": "40px"},
                    "node_id": "node-4",
                },
                {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "loop_var_binding",
        "plan": (
            "1. Folder Selector: grab all *.pdf files in C:\\invoices into inputFiles.\n"
            "2. Loop over inputFiles, each iteration variable is invoice.\n"
            "3. Document node: extract text from inputFiles.\n"
            "4. AI Extract: extract vendor_name (text, required) and total (number, required).\n"
            "5. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Folder Selector",
                    "label": "Select Invoices",
                    "config": {
                        "folderPath": "C:\\invoices",
                        "selectionMode": "all",
                        "filePattern": "*.pdf",
                        "outputVariable": "inputFiles",
                        "failIfEmpty": True,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "Loop",
                    "label": "For each invoice",
                    "config": {
                        "sourceType": "auto",
                        "loopSource": "${inputFiles}",
                        "itemVariable": "invoice",
                        "indexVariable": "invoiceIndex",
                        "maxIterations": "500",
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "Document",
                    "label": "Extract Text",
                    "config": {
                        "documentAction": "process",
                        "sourceType": "variable",
                        "sourcePath": "${invoice}",
                        "outputType": "variable",
                        "outputPath": "invoiceText",
                        "outputFormat": "text",
                        "forceAiExtraction": True,
                    },
                    "position": {"left": "420px", "top": "40px"},
                    "node_id": "node-2",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "AI Extract",
                    "label": "Extract Fields",
                    "config": {
                        "inputSource": "auto",
                        "inputVariable": "${invoiceText}",
                        "outputVariable": "extractedData",
                        "failOnMissingRequired": True,
                        "fields": [
                            {"name": "vendor_name", "type": "text", "required": True, "description": "Vendor name"},
                            {"name": "total", "type": "number", "required": True, "description": "Total amount"},
                        ],
                    },
                    "position": {"left": "620px", "top": "40px"},
                    "node_id": "node-3",
                },
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "End Loop",
                    "label": "End Loop",
                    "config": {"loopNodeId": "node-1"},
                    "position": {"left": "820px", "top": "40px"},
                    "node_id": "node-4",
                },
                {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "set_variable_evaluate_as_expression",
        "plan": (
            "1. Set Variable: create a list of numbers from 1 to 10 in variable nums.\n"
            "2. Set Variable: compute the sum of nums into variable total.\n"
            "3. Alert: email total to user@example.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Set Variable",
                    "label": "Build nums",
                    "config": {
                        "variableName": "nums",
                        "valueSource": "direct",
                        "valueExpression": "list(range(1, 11))",
                        "evaluateAsExpression": True,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "Set Variable",
                    "label": "Sum nums",
                    "config": {
                        "variableName": "total",
                        "valueSource": "direct",
                        "valueExpression": "sum(${nums})",
                        "evaluateAsExpression": True,
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": "Email Total",
                    "config": {
                        "alertType": "email",
                        "recipients": "user@example.com",
                        "messageTemplate": "Total: ${total}",
                    },
                    "position": {"left": "420px", "top": "40px"},
                    "node_id": "node-2",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "ai_extract_missing_fields",
        "plan": (
            "1. Document node: extract text from C:\\inbox\\contract.pdf into variable contractText.\n"
            "2. AI Extract: pull out the key contract terms (party_a, party_b, effective_date, "
            "termination_date, total_value) from contractText and store in extractedTerms.\n"
            "3. Alert: email extractedTerms to legal@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Document",
                    "label": "Read Contract",
                    "config": {
                        "documentAction": "process",
                        "sourceType": "file",
                        "sourcePath": "C:\\inbox\\contract.pdf",
                        "outputType": "variable",
                        "outputPath": "contractText",
                        "outputFormat": "text",
                        "forceAiExtraction": True,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "AI Extract",
                    "label": "Extract Contract Terms",
                    "config": {
                        "inputSource": "auto",
                        "inputVariable": "${contractText}",
                        "outputVariable": "extractedTerms",
                        "failOnMissingRequired": True,
                        "fields": [
                            {"name": "party_a", "type": "text", "required": True, "description": "First contracting party"},
                            {"name": "party_b", "type": "text", "required": True, "description": "Second contracting party"},
                            {"name": "effective_date", "type": "text", "required": True, "description": "Effective date"},
                            {"name": "termination_date", "type": "text", "required": True, "description": "Termination date"},
                            {"name": "total_value", "type": "number", "required": True, "description": "Total contract value"},
                        ],
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": "Email Terms",
                    "config": {
                        "alertType": "email",
                        "recipients": "legal@somecompany.com",
                        "messageTemplate": "Contract terms:\n${extractedTerms}",
                    },
                    "position": {"left": "420px", "top": "40px"},
                    "node_id": "node-2",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "human_approval_assignee_id_string",
        "plan": (
            "1. Database (connection 4): SELECT id, customer, total FROM purchase_orders WHERE status = 'pending', save to var pending_pos.\n"
            "2. Loop over pending_pos as currentPo.\n"
            "3. Conditional: if currentPo.total > 50000.\n"
            "- If yes: Human Approval assigned to user 17, title 'PO Approval Required', priority 1, due 24h.\n"
            "- If no: Alert email to po-auto@somecompany.com with the PO details.\n"
            "4. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "Pending POs", "config": {"connection": "4", "dbOperation": "query", "query": "SELECT id, customer, total FROM purchase_orders WHERE status = 'pending'", "saveToVariable": True, "outputVariable": "pending_pos", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Loop", "label": "For each PO", "config": {"sourceType": "auto", "loopSource": "${pending_pos}", "itemVariable": "currentPo", "indexVariable": "poIdx", "maxIterations": "500"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Conditional", "label": "Over $50k?", "config": {"conditionType": "comparison", "leftValue": "${currentPo.total}", "operator": ">", "rightValue": "50000"}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Human Approval", "label": "PO Approval", "config": {"assigneeType": "user", "assigneeId": "17", "approvalTitle": "PO Approval Required", "approvalDescription": "Please review purchase order ${currentPo.id} for ${currentPo.customer} totaling ${currentPo.total}.", "approvalData": "${currentPo}", "priority": 1, "dueHours": 24, "timeoutAction": "fail"}, "position": {"left": "620px", "top": "20px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Auto-approve email", "config": {"alertType": "email", "recipients": "po-auto@somecompany.com", "messageTemplate": "Auto-approved PO ${currentPo.id} for ${currentPo.customer}: ${currentPo.total}"}, "position": {"left": "620px", "top": "100px"}, "node_id": "node-4"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-4", "connection_type": "fail"},
                {"type": "add_node", "node_type": "End Loop", "label": "End", "config": {"loopNodeId": "node-1"}, "position": {"left": "820px", "top": "40px"}, "node_id": "node-5"},
                {"type": "connect_nodes", "from": "node-3", "to": "node-5", "connection_type": "pass"},
                {"type": "connect_nodes", "from": "node-4", "to": "node-5", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "excel_export_append_template",
        "plan": (
            "1. Database (connection 7): SELECT region, total_sales, week_of FROM weekly_sales WHERE week_of = '2026-04-15', save to var weeklyData.\n"
            "2. Excel Export: append weeklyData rows to existing template C:\\reports\\sales_master.xlsx, "
            "AI-mapping fields region->REGION, total_sales->SALES, week_of->WEEK."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "Weekly Sales", "config": {"connection": "7", "dbOperation": "query", "query": "SELECT region, total_sales, week_of FROM weekly_sales WHERE week_of = '2026-04-15'", "saveToVariable": True, "outputVariable": "weeklyData", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Excel Export", "label": "Append to Master", "config": {"inputVariable": "${weeklyData}", "flattenArray": True, "excelOutputPath": "C:\\reports\\sales_master.xlsx", "excelOperation": "append", "excelTemplatePath": "C:\\reports\\sales_master.xlsx", "mappingMode": "ai", "aiMappingInstructions": "Map region to REGION, total_sales to SALES, week_of to WEEK"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "integration_node_basic",
        "plan": (
            "1. Integration (Stripe, integration_id 12): operation get_invoices with parameters "
            "{status: 'open', limit: 50}, save to var openInvoices.\n"
            "2. Set Variable: total = sum of openInvoices.amount_due.\n"
            "3. Alert: email finance@somecompany.com with the total."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Integration", "label": "Stripe Open Invoices", "config": {"integration_id": "12", "operation": "get_invoices", "parameters": {"status": "open", "limit": 50}, "outputVariable": "openInvoices", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Set Variable", "label": "Compute Total", "config": {"variableName": "total", "valueSource": "direct", "valueExpression": "sum(i['amount_due'] for i in ${openInvoices})", "evaluateAsExpression": True}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Email Total", "config": {"alertType": "email", "recipients": "finance@somecompany.com", "messageTemplate": "Total open invoices: ${total}"}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "execute_app_capture_output",
        "plan": (
            "1. Execute Application: run C:\\tools\\report_gen.exe with arguments '--mode=daily --date=${runDate}', "
            "capture stdout into reportOutput, success codes 0,2.\n"
            "2. Alert: email reportOutput to ops@somecompany.com with subject 'Daily Report'."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Execute Application", "label": "Run Report", "config": {"commandType": "executable", "executablePath": "C:\\tools\\report_gen.exe", "arguments": "--mode=daily --date=${runDate}", "timeout": 300, "captureOutput": True, "successCodes": "0,2", "failOnError": True, "outputParsing": "text", "outputVariable": "reportOutput", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Alert", "label": "Email Report", "config": {"alertType": "email", "recipients": "ops@somecompany.com", "messageTemplate": "Subject: Daily Report\n\n${reportOutput}"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "folder_selector_pattern_mode",
        "plan": (
            "1. Folder Selector: select all files matching 2026-*.pdf in \\\\fileserver\\reports using pattern mode, "
            "fail if empty, store in selectedReports.\n"
            "2. Alert: email selectedReports list to admin@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Folder Selector", "label": "Select 2026 Reports", "config": {"folderPath": "\\\\fileserver\\reports", "selectionMode": "pattern", "filePattern": "2026-*.pdf", "outputVariable": "selectedReports", "failIfEmpty": True}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Alert", "label": "Email List", "config": {"alertType": "email", "recipients": "admin@somecompany.com", "messageTemplate": "Selected reports:\n${selectedReports}"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "set_variable_from_output_path",
        "plan": (
            "1. Database (connection 1): SELECT * FROM kpi_summary, save to var kpiResult.\n"
            "2. Set Variable: extract revenue from previous-step output (kpiResult.data.revenue) into variable revenue, no expression eval.\n"
            "3. Alert: email revenue to ceo@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "KPI", "config": {"connection": "1", "dbOperation": "query", "query": "SELECT * FROM kpi_summary", "saveToVariable": True, "outputVariable": "kpiResult", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Set Variable", "label": "Pull Revenue", "config": {"variableName": "revenue", "valueSource": "output", "outputPath": "data.revenue", "evaluateAsExpression": False}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Email Revenue", "config": {"alertType": "email", "recipients": "ceo@somecompany.com", "messageTemplate": "Revenue: ${revenue}"}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "merge_after_conditional",
        "plan": (
            "1. AI Action (agent_id 8): classify the incoming ticket text in variable ticketBody as either 'urgent' or 'normal', save to classification.\n"
            "2. Conditional: classification == 'urgent'.\n"
            "- If yes: Alert email to oncall@somecompany.com.\n"
            "- If no: Alert email to support@somecompany.com.\n"
            "3. Database (connection 9): INSERT INTO ticket_audit (id, classification, processed_at) VALUES "
            "(${ticketId}, ${classification}, GETDATE()) — runs after either branch."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "AI Action", "label": "Classify", "config": {"agent_id": "8", "prompt": "Classify the following ticket as either 'urgent' or 'normal'. Reply with one word.\n\n${ticketBody}", "outputVariable": "classification", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Conditional", "label": "Urgent?", "config": {"conditionType": "comparison", "leftValue": "${classification}", "operator": "==", "rightValue": "urgent"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Page Oncall", "config": {"alertType": "email", "recipients": "oncall@somecompany.com", "messageTemplate": "Urgent ticket ${ticketId}:\n${ticketBody}"}, "position": {"left": "420px", "top": "20px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Notify Support", "config": {"alertType": "email", "recipients": "support@somecompany.com", "messageTemplate": "Ticket ${ticketId}:\n${ticketBody}"}, "position": {"left": "420px", "top": "100px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-3", "connection_type": "fail"},
                {"type": "add_node", "node_type": "Database", "label": "Audit", "config": {"connection": "9", "dbOperation": "query", "query": "INSERT INTO ticket_audit (id, classification, processed_at) VALUES (${ticketId}, '${classification}', GETDATE())", "saveToVariable": False, "continueOnError": False}, "position": {"left": "620px", "top": "60px"}, "node_id": "node-4"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-4", "connection_type": "pass"},
                {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "loop_empty_behavior",
        "plan": (
            "1. Database (connection 2): SELECT customer_id FROM churn_candidates WHERE risk_score > 0.8, save to var atRiskCustomers.\n"
            "2. Loop atRiskCustomers as cid; if empty, skip the body without failing.\n"
            "3. AI Action (agent_id 14): generate a retention email for customer ${cid}, save to retentionEmail.\n"
            "4. Alert: send retentionEmail.\n"
            "5. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "At-Risk Customers", "config": {"connection": "2", "dbOperation": "query", "query": "SELECT customer_id FROM churn_candidates WHERE risk_score > 0.8", "saveToVariable": True, "outputVariable": "atRiskCustomers", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Loop", "label": "For each", "config": {"sourceType": "auto", "loopSource": "${atRiskCustomers}", "itemVariable": "cid", "indexVariable": "ix", "maxIterations": "1000", "emptyBehavior": "skip"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "AI Action", "label": "Draft Retention", "config": {"agent_id": "14", "prompt": "Write a personalized retention email for customer id ${cid}. Keep it short and warm.", "outputVariable": "retentionEmail", "continueOnError": True}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Send Email", "config": {"alertType": "email", "recipients": "retention@somecompany.com", "messageTemplate": "${retentionEmail}"}, "position": {"left": "620px", "top": "40px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {"type": "add_node", "node_type": "End Loop", "label": "End", "config": {"loopNodeId": "node-1"}, "position": {"left": "820px", "top": "40px"}, "node_id": "node-4"},
                {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "ai_extract_repeated_group",
        "plan": (
            "1. Document: extract text from ${invoiceFile} into rawText.\n"
            "2. AI Extract from rawText into invoiceData with these fields: vendor (text, required); "
            "invoice_number (text, required); line_items (repeated_group, required) with children "
            "description (text, required), quantity (number, required), unit_price (number, required); "
            "total (number, required)."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Document", "label": "Extract Text", "config": {"documentAction": "process", "sourceType": "variable", "sourcePath": "${invoiceFile}", "outputType": "variable", "outputPath": "rawText", "outputFormat": "text", "forceAiExtraction": True}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "AI Extract", "label": "Extract Invoice", "config": {"inputSource": "auto", "inputVariable": "${rawText}", "outputVariable": "invoiceData", "failOnMissingRequired": True, "fields": [
                    {"name": "vendor", "type": "text", "required": True, "description": "Vendor name"},
                    {"name": "invoice_number", "type": "text", "required": True, "description": "Invoice number"},
                    {"name": "line_items", "type": "repeated_group", "required": True, "description": "Line items", "children": [
                        {"name": "description", "type": "text", "required": True},
                        {"name": "quantity", "type": "number", "required": True},
                        {"name": "unit_price", "type": "number", "required": True},
                    ]},
                    {"name": "total", "type": "number", "required": True, "description": "Total amount"},
                ]}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "stored_procedure_with_params",
        "plan": (
            "1. Database (connection 5): execute stored procedure usp_DailyRollup with parameters [${runDate}, 'East'], save to var rollupResult.\n"
            "2. Alert: email summary of rollupResult to data@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "Daily Rollup", "config": {"connection": "5", "dbOperation": "procedure", "procedure": "usp_DailyRollup", "parameters": ["${runDate}", "East"], "saveToVariable": True, "outputVariable": "rollupResult", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Alert", "label": "Email Summary", "config": {"alertType": "email", "recipients": "data@somecompany.com", "messageTemplate": "Daily rollup:\n${rollupResult}"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "ai_extract_excel_template_mapping",
        "plan": (
            "1. Folder Selector: pick all *.pdf from \\\\share\\onboarding\\new into newPackets.\n"
            "2. Loop newPackets as packet.\n"
            "3. AI Extract directly from ${packet}: company_name (text, req), tax_id (text, req), "
            "primary_contact (text, req); output to packetData; "
            "Excel output to template C:\\templates\\onboarding.xlsx, append mode, "
            "manual mapping {company_name: COMPANY, tax_id: TAX_ID, primary_contact: CONTACT}.\n"
            "4. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Folder Selector", "label": "New Packets", "config": {"folderPath": "\\\\share\\onboarding\\new", "selectionMode": "all", "filePattern": "*.pdf", "outputVariable": "newPackets", "failIfEmpty": True}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Loop", "label": "Each Packet", "config": {"sourceType": "auto", "loopSource": "${newPackets}", "itemVariable": "packet", "indexVariable": "pIdx", "maxIterations": "200"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "AI Extract", "label": "Extract + Excel", "config": {"inputSource": "auto", "inputVariable": "${packet}", "outputVariable": "packetData", "failOnMissingRequired": True, "fields": [
                    {"name": "company_name", "type": "text", "required": True, "description": "Company"},
                    {"name": "tax_id", "type": "text", "required": True, "description": "Tax ID"},
                    {"name": "primary_contact", "type": "text", "required": True, "description": "Primary contact"},
                ], "outputDestination": "excel_template", "excelOutputPath": "C:\\templates\\onboarding.xlsx", "excelTemplatePath": "C:\\templates\\onboarding.xlsx", "mappingMode": "manual", "fieldMapping": {"company_name": "COMPANY", "tax_id": "TAX_ID", "primary_contact": "CONTACT"}}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "End Loop", "label": "End", "config": {"loopNodeId": "node-1"}, "position": {"left": "620px", "top": "40px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "single_node_workflow",
        "plan": (
            "1. Alert: email a daily heartbeat 'system online' to admin@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Alert", "label": "Heartbeat", "config": {"alertType": "email", "recipients": "admin@somecompany.com", "messageTemplate": "system online"}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "file_write_from_variable",
        "plan": (
            "1. AI Action (agent_id 3): generate weekly summary into variable weeklyReport.\n"
            "2. File: write weeklyReport to C:\\reports\\weekly_${runDate}.txt."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "AI Action", "label": "Generate Report", "config": {"agent_id": "3", "prompt": "Produce the weekly executive summary in plain text.", "outputVariable": "weeklyReport", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "File", "label": "Write Report", "config": {"operation": "write", "filePath": "C:\\reports\\weekly_${runDate}.txt", "contentSource": "variable", "contentVariable": "weeklyReport"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "conditional_contains",
        "plan": (
            "1. AI Action (agent_id 6): summarize ${incomingEmail} into summary.\n"
            "2. Conditional: summary contains 'refund'.\n"
            "- If yes: Alert email to refunds@somecompany.com.\n"
            "- If no: Alert email to general@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "AI Action", "label": "Summarize", "config": {"agent_id": "6", "prompt": "Summarize the following customer email in one paragraph:\n\n${incomingEmail}", "outputVariable": "summary", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Conditional", "label": "Has refund?", "config": {"conditionType": "contains", "containsText": "${summary}", "searchText": "refund"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Refunds Team", "config": {"alertType": "email", "recipients": "refunds@somecompany.com", "messageTemplate": "Refund mention:\n${summary}"}, "position": {"left": "420px", "top": "20px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "General Team", "config": {"alertType": "email", "recipients": "general@somecompany.com", "messageTemplate": "${summary}"}, "position": {"left": "420px", "top": "100px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-3", "connection_type": "fail"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "exists_check_then_branch",
        "plan": (
            "1. Folder Selector: pick latest *.csv from C:\\drops into latestCsv (latest mode).\n"
            "2. Conditional: variable latestCsv exists.\n"
            "- If yes: File node read ${latestCsv} into csvText.\n"
            "- If no: Alert email to ops@somecompany.com 'no drop file found'."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Folder Selector", "label": "Latest CSV", "config": {"folderPath": "C:\\drops", "selectionMode": "latest", "filePattern": "*.csv", "outputVariable": "latestCsv", "failIfEmpty": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Conditional", "label": "Found?", "config": {"conditionType": "exists", "existsVariable": "latestCsv"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "File", "label": "Read CSV", "config": {"operation": "read", "filePath": "${latestCsv}", "saveToVariable": True, "outputVariable": "csvText"}, "position": {"left": "420px", "top": "20px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "No Drop", "config": {"alertType": "email", "recipients": "ops@somecompany.com", "messageTemplate": "no drop file found"}, "position": {"left": "420px", "top": "100px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-3", "connection_type": "fail"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "loop_split_delimiter",
        "plan": (
            "1. AI Action (agent_id 11): produce a comma-separated list of dept names into variable deptList.\n"
            "2. Loop deptList using split delimiter ','; each item bound to dept.\n"
            "3. Database (connection 3): UPDATE departments SET last_run = GETDATE() WHERE name = '${dept}'.\n"
            "4. End Loop."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "AI Action", "label": "Build Dept List", "config": {"agent_id": "11", "prompt": "List all active department names as a single comma-separated string.", "outputVariable": "deptList", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Loop", "label": "Each Dept", "config": {"sourceType": "split", "loopSource": "${deptList}", "splitDelimiter": ",", "itemVariable": "dept", "indexVariable": "dIdx", "maxIterations": "200"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Database", "label": "Mark Run", "config": {"connection": "3", "dbOperation": "query", "query": "UPDATE departments SET last_run = GETDATE() WHERE name = '${dept}'", "saveToVariable": False, "continueOnError": False}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {"type": "add_node", "node_type": "End Loop", "label": "End", "config": {"loopNodeId": "node-1"}, "position": {"left": "620px", "top": "40px"}, "node_id": "node-3"},
                {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "alert_with_variable_subject_and_body",
        "plan": (
            "1. Database (connection 1): SELECT customer, orderId, total FROM orders WHERE id = ${orderId}, save to orderRow.\n"
            "2. Alert: email ${orderRow.customer} with subject 'Order ${orderRow.orderId} confirmation' "
            "and body 'Thanks! Total: ${orderRow.total}'."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "Database", "label": "Order Row", "config": {"connection": "1", "dbOperation": "query", "query": "SELECT customer, orderId, total FROM orders WHERE id = ${orderId}", "saveToVariable": True, "outputVariable": "orderRow", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Alert", "label": "Confirm Email", "config": {"alertType": "email", "recipients": "${orderRow.customer}", "messageTemplate": "Subject: Order ${orderRow.orderId} confirmation\n\nThanks! Total: ${orderRow.total}"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "human_approval_complete_path",
        "plan": (
            "1. AI Action (agent_id 9): draft a press release into pressDraft.\n"
            "2. Human Approval: assigned to group 12, title 'Approve Press Release', priority 1, due 48 hours, "
            "data ${pressDraft}.\n"
            "3. Alert: regardless of approval outcome, log the approval result to comms-log@somecompany.com."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {"type": "add_node", "node_type": "AI Action", "label": "Draft Release", "config": {"agent_id": "9", "prompt": "Draft a one-page press release based on the latest product update.", "outputVariable": "pressDraft", "continueOnError": False}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
                {"type": "add_node", "node_type": "Human Approval", "label": "Approve Release", "config": {"assigneeType": "group", "assigneeId": "12", "approvalTitle": "Approve Press Release", "approvalDescription": "Please approve the attached press draft.", "approvalData": "${pressDraft}", "priority": 1, "dueHours": 48, "timeoutAction": "fail"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {"type": "add_node", "node_type": "Alert", "label": "Log Outcome", "config": {"alertType": "email", "recipients": "comms-log@somecompany.com", "messageTemplate": "Press release approval flow finished."}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "complete"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
    {
        "footgun": "conditional_pass_fail_branches",
        "plan": (
            "1. Database node (connection 2): SELECT COUNT(*) AS c FROM pending_orders, "
            "save to var ordersResult.\n"
            "2. Conditional: check if ordersResult[0].c > 0.\n"
            "- If yes: Alert email to ops@somecompany.com with subject 'Pending orders'.\n"
            "- If no: Alert email to ops@somecompany.com with subject 'All clear'."
        ),
        "commands": {
            "action": "build_workflow",
            "commands": [
                {
                    "type": "add_node",
                    "node_type": "Database",
                    "label": "Count Pending",
                    "config": {
                        "connection": "2",
                        "dbOperation": "query",
                        "query": "SELECT COUNT(*) AS c FROM pending_orders",
                        "saveToVariable": True,
                        "outputVariable": "ordersResult",
                        "continueOnError": False,
                    },
                    "position": {"left": "20px", "top": "40px"},
                    "node_id": "node-0",
                },
                {
                    "type": "add_node",
                    "node_type": "Conditional",
                    "label": "Any pending?",
                    "config": {
                        "conditionType": "expression",
                        "expression": "${ordersResult}[0]['c'] > 0",
                    },
                    "position": {"left": "220px", "top": "40px"},
                    "node_id": "node-1",
                },
                {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": "Email Pending",
                    "config": {
                        "alertType": "email",
                        "recipients": "ops@somecompany.com",
                        "messageTemplate": "Subject: Pending orders\n\nThere are pending orders.",
                    },
                    "position": {"left": "420px", "top": "20px"},
                    "node_id": "node-2",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
                {
                    "type": "add_node",
                    "node_type": "Alert",
                    "label": "Email All Clear",
                    "config": {
                        "alertType": "email",
                        "recipients": "ops@somecompany.com",
                        "messageTemplate": "Subject: All clear\n\nNo pending orders.",
                    },
                    "position": {"left": "420px", "top": "100px"},
                    "node_id": "node-3",
                },
                {"type": "connect_nodes", "from": "node-1", "to": "node-3", "connection_type": "fail"},
                {"type": "set_start_node", "node_id": "node-0"},
            ],
        },
    },
]


def write_adversarial(out_path: str) -> int:
    """Write ADVERSARIAL_CASES as OpenAI chat JSONL. Returns count written."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    system_prompt = load_system_prompt_text()
    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for case in ADVERSARIAL_CASES:
            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": case["plan"]},
                    {
                        "role": "assistant",
                        "content": "```json\n" + json.dumps(case["commands"], indent=2) + "\n```",
                    },
                ],
                "_meta": {
                    "source": "synth_adversarial",
                    "footgun": case["footgun"],
                    "n_commands": len(case["commands"]["commands"]),
                    "handcrafted": True,
                },
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="cmdgen", help="Task folder under training/data/")
    ap.add_argument(
        "--out",
        default=None,
        help="Override output path. Default: training/data/<task>/synthetic/adversarial.jsonl",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out_path = args.out or os.path.join(
        _REPO_ROOT, "training", "data", args.task, "synthetic", "adversarial.jsonl"
    )
    n = write_adversarial(out_path)
    logger.info("Wrote %d adversarial cases to %s", n, out_path)
