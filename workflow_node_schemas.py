"""Per-node-type workflow config schemas — the shared contract between authoring
surfaces and the execution engine.

WHY THIS EXISTS (2026-07-30): the CC-native agent authored node configs with
GUESSED key names (Excel Export `dataVariable` instead of `inputVariable`,
Set Variable `useExpression`/`value` instead of `evaluateAsExpression`/
`valueExpression`, `excelOperation: "create"` which is not a valid operation).
Nothing validated the keys, and the engine silently defaults when its real key
is missing — so workflows saved "runnable", every step "Completed", and the
output was empty (0-byte files, junk Excel rows). See
test_human/11_Regression_Suite/TEST_RUN_2026-07-30.md §06 and the pack-14
config lint.

The tables below are derived from what each `_execute_*_node` in
workflow_execution.py ACTUALLY reads (plus keys observed in real
Designer-built workflows). Consumers:
  - command_center_service/graph/workflow_tools.py  (add_node / update_node —
    STRICT: reject errors so the authoring LLM self-corrects in the same turn)
  - workflow_deterministic_validator.py  (save-time rule: errors draft the
    workflow with per-node reasons; plain unknown keys surface as warnings)

Keep in sync with the engine: when an executor grows a config key, add it to
`known` here (and to system_prompts.NODE_DETAIL_REFERENCE so authoring LLMs
see it).
"""
from typing import Any, Dict, List, Optional

ERROR = "error"
WARNING = "warning"

# Keys many node types share (engine-generic or harmless bookkeeping).
COMMON_KEYS = {
    "continueOnError", "outputVariable", "saveToVariable", "timeout",
    "description", "notes",
}

# Per node type:
#   required:        keys that MUST be present and non-empty (engine-fatal or
#                    silent-empty when missing)
#   requires_one_of: groups where at least one key must be present/non-empty
#   enums:           allowed values per key (checked only when the key is set)
#   enum_aliases:    unambiguous wrong VALUES -> the intended value (rejected
#                    with a did-you-mean; never silently rewritten)
#   aliases:         unambiguous wrong KEYS -> the real key (rejected with a
#                    did-you-mean — a config carrying one of these means the
#                    intended value will NOT be read by the engine)
#   known:           every key the executor reads or the Designer writes;
#                    anything else (and not in COMMON_KEYS/aliases) is an
#                    "engine ignores this" warning
NODE_CONFIG_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "Set Variable": {
        "required": ["variableName"],
        "requires_one_of": [("valueExpression", "outputPath")],
        "enums": {"valueSource": {"direct", "output"}},
        "aliases": {
            "value": "valueExpression",
            "expression": "valueExpression",
            "useExpression": "evaluateAsExpression",
            "varName": "variableName",
            "variable": "variableName",
        },
        "known": {"variableName", "valueSource", "valueExpression", "outputPath",
                  "evaluateAsExpression"},
    },
    "Excel Export": {
        "required": ["inputVariable", "excelOutputPath"],
        # Engine op map is {new, template, append} (unknown -> silent append
        # fallback). 'update' is tolerated here because legacy workflows carry
        # it and detect_excel_export_config_errors already governs its
        # template-path requirement — do NOT let two rules fight.
        "enums": {"excelOperation": {"new", "template", "append", "update"}},
        "enum_aliases": {"excelOperation": {"create": "new"}},
        "aliases": {
            "dataVariable": "inputVariable",
            "sourceVariable": "inputVariable",
            "operation": "excelOperation",
            "outputPath": "excelOutputPath",
            "filePath": "excelOutputPath",
            "templatePath": "excelTemplatePath",
            "sheetName": "excelSheetName",
        },
        "known": {"inputVariable", "excelOperation", "excelOutputPath",
                  "excelTemplatePath", "excelSheetName", "flattenArray",
                  "mappingMode", "fieldMapping", "fields", "extractionType",
                  "aiMappingInstructions", "allowPartialExtraction",
                  "carryForwardFields", "failOnMissingRequired",
                  "formattingInstructions", "includeAssumptions",
                  "includeConfidence", "includeSources", "specialInstructions"},
    },
    "Database": {
        # Required/op-specific fields (connection, query for dbOperation=query,
        # etc.) are owned by detect_database_config_errors — this entry only
        # contributes key-name hygiene.
        "required": [],
        "aliases": {"connectionId": "connection", "connectionName": "connection",
                    "sql": "query", "sqlQuery": "query"},
        "known": {"connection", "dbOperation", "query", "parameters", "procedure",
                  "tableName", "columns", "whereClause", "data", "dataPath",
                  "dataSource", "dataVariable"},
    },
    "File": {
        # filePath/operation presence is owned by detect_file_node_config_errors.
        "required": [],
        "enums": {"operation": {"write", "append", "read", "check", "delete"}},
        "aliases": {"path": "filePath", "fileOperation": "operation",
                    "fileName": "filePath", "text": "content"},
        "known": {"filePath", "operation", "content", "contentSource", "encoding",
                  "overwrite"},
    },
    "Loop": {
        "enums": {"sourceType": {"auto", "variable", "split", "path", "folderFiles"}},
        # sourceType 'auto' IGNORES loopSource entirely (engine ~7247) — when a
        # loopSource is given the author almost always wants 'variable'.
        "requires_when": {"sourceType": {
            "variable": ["loopSource"], "split": ["loopSource"], "path": ["loopSource"]}},
        "aliases": {"listVariable": "loopSource", "sourceVariable": "loopSource",
                    "loopVariable": "itemVariable"},
        "known": {"sourceType", "loopSource", "itemVariable", "indexVariable",
                  "maxIterations", "emptyBehavior", "splitDelimiter", "outputMode",
                  "arrayInfoVariable"},
    },
    "End Loop": {
        "known": {"loopNodeId"},
        "aliases": {"loopId": "loopNodeId", "loopNode": "loopNodeId"},
    },
    "Conditional": {
        "enums": {"conditionType": {"comparison", "contains", "exists", "empty",
                                    "expression"}},
        "aliases": {"left": "leftValue", "right": "rightValue",
                    "conditionExpression": "expression"},
        "known": {"conditionType", "leftValue", "rightValue", "operator",
                  "expression", "containsText", "searchText", "existsVariable",
                  "emptyVariable", "alertType"},
    },
    "Alert": {
        "aliases": {"recipient": "recipients", "subject": "emailSubject",
                    "message": "messageTemplate", "body": "messageTemplate",
                    "emailBody": "messageTemplate"},
        "known": {"alertType", "recipients", "emailSubject", "messageTemplate",
                  "attachmentPath"},
    },
    "Human Approval": {
        "aliases": {"assignedTo": "assigneeId", "title": "approvalTitle",
                    "message": "approvalDescription"},
        "known": {"approvalTitle", "approvalDescription", "approvalData",
                  "assignee", "assigneeId", "assigneeType", "dueHours", "priority",
                  "timeoutAction", "timeoutMinutes"},
    },
    "Folder Selector": {
        "required": ["folderPath"],
        "aliases": {"path": "folderPath", "pattern": "filePattern"},
        "known": {"folderPath", "filePattern", "selectionMode", "failIfEmpty"},
    },
    "File Transfer": {
        "required": ["protocol", "operation", "host"],
        "enums": {"protocol": {"sftp", "ftp", "ftps"},
                  "operation": {"upload", "download"}},
        "aliases": {"server": "host", "user": "username", "secret": "secretName",
                    "remoteDir": "remotePath", "localDir": "localPath"},
        "known": {"protocol", "host", "port", "username", "secretName",
                  "operation", "localPath", "remotePath", "overwrite",
                  "zeroMatchPolicy", "newestOnly", "filesVariable"},
    },
    "Portal": {
        "required": ["portalWorkflowSlug"],
        "aliases": {"workflowSlug": "portalWorkflowSlug",
                    "workflow_slug": "portalWorkflowSlug",
                    "slug": "portalWorkflowSlug"},
        "known": {"portalWorkflowSlug", "ownerUserId", "owner_user_id",
                  "filesVariable", "agentFallback", "uploadFilesVariable"},
    },
}


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def suggest_key(node_type: str, key: str) -> Optional[str]:
    """The real key an unrecognized key most likely meant, or None."""
    schema = NODE_CONFIG_SCHEMAS.get(node_type) or {}
    return (schema.get("aliases") or {}).get(key)


def validate_node_config(node_type: str, config: Dict[str, Any]) -> List[Dict[str, str]]:
    """Validate a node's config against the engine contract.

    Returns a list of issues: {"severity": "error"|"warning", "key": ..., "message": ...}.
    Node types without a schema entry return no issues (fail-open — new node
    types must not be blocked by a stale table).
    """
    schema = NODE_CONFIG_SCHEMAS.get(node_type)
    if not schema or not isinstance(config, dict):
        return []
    issues: List[Dict[str, str]] = []
    aliases = schema.get("aliases") or {}
    known = (schema.get("known") or set()) | COMMON_KEYS

    # 1. Wrong-key names (the silent-empty killer): the intended value will
    #    never be read by the engine -> ERROR with a did-you-mean.
    for key in config:
        if key in aliases:
            issues.append({
                "severity": ERROR, "key": key, "kind": "wrong_key",
                "message": (f"'{key}' is not a {node_type} config key the engine reads — "
                            f"use '{aliases[key]}' instead."),
            })
        elif key not in known:
            issues.append({
                "severity": WARNING, "key": key, "kind": "unknown_key",
                "message": (f"'{key}' is not a recognized {node_type} config key — "
                            f"the engine will ignore it."),
            })

    # 2. Required keys.
    for key in schema.get("required", []):
        if _is_empty(config.get(key)):
            hint = ""
            wrong = [w for w, right in aliases.items() if right == key and w in config]
            if wrong:
                hint = f" (the value currently under '{wrong[0]}' probably belongs here)"
            issues.append({
                "severity": ERROR, "key": key, "kind": "missing",
                "message": f"{node_type} requires config key '{key}' and it is missing or empty{hint}.",
            })

    # 3. At-least-one-of groups.
    for group in schema.get("requires_one_of", []):
        if all(_is_empty(config.get(k)) for k in group):
            wrong = [w for w, right in aliases.items() if right in group and w in config]
            hint = f" (the value currently under '{wrong[0]}' probably belongs in '{aliases[wrong[0]]}')" if wrong else ""
            issues.append({
                "severity": ERROR, "key": group[0], "kind": "missing",
                "message": (f"{node_type} requires one of {list(group)} and none is set{hint}."),
            })

    # 4. Conditionally-required keys (e.g. Loop sourceType=variable -> loopSource).
    for switch_key, cases in (schema.get("requires_when") or {}).items():
        val = config.get(switch_key)
        for needed in cases.get(val, []) if isinstance(val, str) else []:
            if _is_empty(config.get(needed)):
                issues.append({
                    "severity": ERROR, "key": needed, "kind": "missing",
                    "message": (f"{node_type} with {switch_key}='{val}' requires "
                                f"'{needed}' and it is missing or empty."),
                })

    # 5. Enum values.
    for key, allowed in (schema.get("enums") or {}).items():
        val = config.get(key)
        if val is None or _is_empty(val):
            continue
        sval = str(val)
        if sval not in allowed:
            alias_map = (schema.get("enum_aliases") or {}).get(key) or {}
            if sval in alias_map:
                issues.append({
                    "severity": ERROR, "key": key, "kind": "enum",
                    "message": (f"{node_type} {key}='{sval}' is not a valid value — "
                                f"use '{alias_map[sval]}'. Valid values: {sorted(allowed)}."),
                })
            else:
                issues.append({
                    "severity": ERROR, "key": key, "kind": "enum",
                    "message": (f"{node_type} {key}='{sval}' is not a valid value. "
                                f"Valid values: {sorted(allowed)} (the engine silently "
                                f"falls back otherwise, which hides the mistake)."),
                })
    return issues


def config_errors(node_type: str, config: Dict[str, Any]) -> List[str]:
    """Just the ERROR messages (convenience for strict rejection paths)."""
    return [i["message"] for i in validate_node_config(node_type, config)
            if i["severity"] == ERROR]


def config_warnings(node_type: str, config: Dict[str, Any]) -> List[str]:
    return [i["message"] for i in validate_node_config(node_type, config)
            if i["severity"] == WARNING]


def authoring_errors(node_type: str, config: Dict[str, Any]) -> List[str]:
    """Errors an AUTHORING tool should hard-reject: wrong key names and invalid
    enum values — the engine silently misreads both, which is the silent-empty
    defect class. Missing-required is deliberately NOT included: draft-first
    authoring (add the node now, configure it later) is legitimate, and the
    save-time validator drafts those with per-node reasons instead."""
    return [i["message"] for i in validate_node_config(node_type, config)
            if i["severity"] == ERROR and i.get("kind") in ("wrong_key", "enum")]


def authoring_notes(node_type: str, config: Dict[str, Any]) -> List[str]:
    """Non-blocking heads-ups for the authoring tool result: unknown keys the
    engine will ignore + required keys still missing (draft state)."""
    return [i["message"] for i in validate_node_config(node_type, config)
            if i["severity"] == WARNING or i.get("kind") == "missing"]
