import json
import logging
import os
import yaml
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from datetime import datetime

from CommonUtils import get_db_connection, get_document_api_base_url
from AppUtils import populate_schema_with_claude_chunked
import config as cfg

logger = logging.getLogger("ComplianceEngine")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractedRequirement:
    category: str
    subcategory: str
    requirement_text: str
    specific_value: Optional[str] = None
    severity: Optional[str] = None
    source_page: Optional[int] = None
    confidence: Optional[float] = None


@dataclass
class ProcessingResult:
    version_id: int
    version_number: int
    retailer_id: int
    set_id: int
    document_id: str
    requirements: List[ExtractedRequirement]
    excel_path: Optional[str] = None
    change_summary: Optional[str] = None
    is_duplicate: bool = False
    error: Optional[str] = None
    extraction_diagnostics: Optional[Dict[str, Any]] = None
    # Older versions of the same manual taken out of the agent's knowledge by this
    # run: [{"document_id", "filename"}]. Empty when nothing was replaced.
    retired_documents: Optional[List[Dict[str, Any]]] = None
    # Set when this document was NOT added to the agent's knowledge because the agent
    # already holds a newer version of the same manual: {"document_id", "filename"}.
    superseded_by: Optional[Dict[str, Any]] = None
    # One line saying what the version check did (or why it could not run).
    version_check_note: Optional[str] = None


# ---------------------------------------------------------------------------
# Taxonomy loader
# ---------------------------------------------------------------------------

def load_compliance_taxonomy() -> Dict:
    schema_path = os.path.join(os.path.dirname(__file__), "schemas", "retailer_compliance.yaml")
    with open(schema_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def taxonomy_to_schema_fields(taxonomy: Dict) -> Dict[str, str]:
    """Convert the compliance taxonomy YAML into schema_fields for populate_schema_with_claude_chunked.

    Returns a dict of ``{"category.subcategory.field": "description"}`` suitable
    for the chunked extraction pipeline.
    """
    schema_fields: Dict[str, str] = {}
    for cat_key, cat_val in taxonomy.get("categories", {}).items():
        for sub_key, sub_val in cat_val.get("subcategories", {}).items():
            for fld in sub_val.get("fields", []):
                key = f"{cat_key}.{sub_key}.{fld['name']}"
                schema_fields[key] = (
                    f"{fld['description']}. Extract the full requirement text "
                    f"including any specific values, thresholds, or deadlines. "
                    f"Return null if not mentioned in the document."
                )
    return schema_fields


def _custom_fields_to_schema_fields(fields: List[Dict]) -> Dict[str, str]:
    """Convert UI field definitions (from AIExtractNode) to schema_fields dict.

    Same structure as WorkflowExecutionEngine._build_schema_fields_for_document_extraction.
    """
    schema_fields: Dict[str, str] = {}
    for fld in fields:
        name = fld.get("name", "")
        desc = fld.get("description", "")
        ftype = fld.get("type", "text")
        required = fld.get("required", False)
        children = fld.get("children", [])
        if not name:
            continue

        if ftype == "repeated_group" and children:
            child_desc = _describe_children(children)
            parts = [desc] if desc else []
            parts.append(
                f"Return as an ARRAY of objects. Each object should contain: {child_desc}. "
                f"Extract ALL matching items. If none found, return []."
            )
            if required:
                parts.append("(REQUIRED)")
            schema_fields[name] = " ".join(parts)
        elif ftype == "group" and children:
            child_desc = _describe_children(children)
            parts = [desc] if desc else []
            parts.append(f"Return as an object containing: {child_desc}.")
            if required:
                parts.append("(REQUIRED)")
            schema_fields[name] = " ".join(parts)
        else:
            parts = [desc] if desc else []
            parts.append(f"(type: {ftype})")
            if required:
                parts.append("(REQUIRED)")
            schema_fields[name] = " ".join(parts)

    return schema_fields


def _describe_children(children: List[Dict]) -> str:
    parts = []
    for child in children:
        name = child.get("name", "")
        desc = child.get("description", "")
        ctype = child.get("type", "text")
        req = child.get("required", False)
        grandchildren = child.get("children", [])
        if not name:
            continue
        if ctype == "repeated_group" and grandchildren:
            nested = _describe_children(grandchildren)
            s = f'"{name}" (array of objects with: {nested})'
        elif ctype == "group" and grandchildren:
            nested = _describe_children(grandchildren)
            s = f'"{name}" (object with: {nested})'
        else:
            s = f'"{name}"'
            if desc:
                s += f" ({desc})"
            s += f" [{ctype}]"
        if req:
            s += " REQUIRED"
        parts.append(s)
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ComplianceEngine:
    """Processes retailer compliance documents through the full pipeline."""

    def __init__(self):
        self._taxonomy = load_compliance_taxonomy()
        self._schema_fields = taxonomy_to_schema_fields(self._taxonomy)

    # -- public entry point --------------------------------------------------

    def process_compliance_document(
        self,
        file_path: str,
        retailer_id: int,
        set_id: int,
        uploaded_by: int = 1,
        agent_id: Optional[int] = None,
        excel_template_path: Optional[str] = None,
        retire_superseded: Optional[bool] = None,
        knowledge_attributes: Optional[Dict[str, Any]] = None,
    ) -> ProcessingResult:
        """Full pipeline: ingest -> extract -> version -> knowledge -> excel.

        retire_superseded: when the document is a newer version of a manual the agent
        already holds for this retailer, take the older version out of the agent's
        knowledge (and do not add a document that is OLDER than one the agent holds).
        None = the platform setting COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS (on by default).

        knowledge_attributes: optional name/value pairs attached to the document
        (region, department, ...). The retailer is always attached as "customer" —
        from the run, never from the file name — so the agent's knowledge search can
        be limited to the customer a question is about (knowledge_scope.py).
        """
        try:
            doc_hash = self._file_hash(file_path)
            dup_version = self._check_duplicate(set_id, doc_hash)
            if dup_version:
                return ProcessingResult(
                    version_id=dup_version["version_id"],
                    version_number=dup_version["version_number"],
                    retailer_id=retailer_id,
                    set_id=set_id,
                    document_id=dup_version["document_id"],
                    requirements=[],
                    is_duplicate=True,
                    error="Duplicate document — identical file already uploaded as version "
                          f"{dup_version['version_number']}",
                    # Nothing is added for a duplicate; say whether the agent this run
                    # targets actually holds the file, so "duplicate" is never mistaken
                    # for "the agent has it".
                    version_check_note=self._duplicate_knowledge_note(
                        agent_id, dup_version["document_id"]
                    ) if agent_id else None,
                )

            document_id = self._ingest_document(file_path)
            # The duplicate check above compares against this stored hash. Nothing else
            # writes it, so without this line no file was ever recognised as a
            # duplicate: every re-sent manual became a new version.
            self._store_document_hash(document_id, doc_hash)
            self._attach_attributes(document_id, retailer_id, knowledge_attributes)
            version_id, version_number = self._create_version(
                set_id, document_id, uploaded_by, doc_hash
            )

            set_info = self.get_document_set(set_id) or {}
            requirements = self._extract_requirements(file_path, set_info)
            self._store_requirements(version_id, requirements)

            retired_documents: List[Dict[str, Any]] = []
            superseded_by = None
            version_check_note = None
            if agent_id:
                if retire_superseded is None:
                    retire_superseded = bool(
                        getattr(cfg, "COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS", True)
                    )
                check = None
                if retire_superseded:
                    check = self._check_manual_versions(agent_id, retailer_id, document_id)
                    version_check_note = check.get("note")
                    superseded_by = check.get("newer_existing")
                if superseded_by:
                    # The agent already answers from a newer edition of this manual:
                    # adding this one would put superseded text back into its answers.
                    logger.info(
                        "Compliance document %s NOT added to agent %s knowledge: the agent "
                        "holds a newer version of the same manual (%s)",
                        document_id, agent_id, superseded_by.get("filename"),
                    )
                    self._record_not_added(agent_id, document_id, superseded_by.get("filename"))
                else:
                    self._index_to_knowledge(agent_id, document_id, uploaded_by)
                # Older editions leave the agent's knowledge either way (when a newer
                # one is already held they should have left when it arrived).
                current_name = (superseded_by or {}).get("filename") \
                    or (check or {}).get("new_filename")
                for old in (check or {}).get("older_existing", []):
                    if self._retire_knowledge_for_document(
                            agent_id, old["document_id"], replaced_by=current_name):
                        retired_documents.append(old)

            excel_path = None
            if excel_template_path:
                excel_path = self._populate_excel(
                    requirements, retailer_id, excel_template_path
                )

            change_summary = None
            if version_number > 1:
                change_summary = self._generate_change_summary(set_id, version_id)

            return ProcessingResult(
                version_id=version_id,
                version_number=version_number,
                retailer_id=retailer_id,
                set_id=set_id,
                document_id=document_id,
                requirements=requirements,
                excel_path=excel_path,
                change_summary=change_summary,
                extraction_diagnostics=getattr(self, "_last_extraction_diagnostics", None),
                retired_documents=retired_documents,
                superseded_by=superseded_by,
                version_check_note=version_check_note,
            )
        except Exception as e:
            logger.exception("Compliance processing failed")
            return ProcessingResult(
                version_id=0,
                version_number=0,
                retailer_id=retailer_id,
                set_id=set_id,
                document_id="",
                requirements=[],
                error=str(e),
                extraction_diagnostics=getattr(self, "_last_extraction_diagnostics", None),
            )

    # -- document ingestion --------------------------------------------------

    def _ingest_document(self, file_path: str) -> str:
        """Ingest a PDF via the document processing API and return document_id."""
        import requests

        doc_api_url = get_document_api_base_url()
        process_url = f"{doc_api_url}/document/process"

        form_data = {
            "filePath": file_path,
            "force_ai_extraction": "true",
            "is_knowledge_document": "false",
            "extract_fields": "true",
            "detect_document_type": "true",
        }

        doc_timeout = getattr(cfg, "DOC_PROCESSING_TIMEOUT_MINUTES", 30) * 60
        response = requests.post(process_url, data=form_data, timeout=doc_timeout)
        response.raise_for_status()

        result = response.json()
        if result.get("status") != "success" or "document_id" not in result:
            raise RuntimeError(
                f"Document ingestion failed: {result.get('error', 'unknown error')}"
            )

        logger.info(
            "Document ingested: %s (%d pages)",
            result["document_id"],
            result.get("page_count", 0),
        )
        return result["document_id"]

    # -- requirement extraction ----------------------------------------------

    def _extract_requirements(
        self, file_path: str, set_info: Dict[str, Any]
    ) -> List[ExtractedRequirement]:
        """Extract requirements using the priority chain: workflow > linked schema > YAML default.

        Stores diagnostic info on self._last_extraction_diagnostics so callers
        (e.g. the upload job tracker) can surface it in the UI.
        """
        self._last_extraction_diagnostics = {}
        workflow_id = set_info.get("extraction_workflow_id")
        schema_id = set_info.get("extraction_schema_id")

        if workflow_id:
            return self._extract_via_workflow(workflow_id, file_path, set_info)

        # Resolve which schema_fields dict to use, with logging at each fork
        schema_fields = None
        source = None
        if schema_id:
            schema = ComplianceEngine.get_schema(schema_id)
            if schema and schema.get("fields"):
                fields = schema["fields"]
                if isinstance(fields, str):
                    try:
                        fields = json.loads(fields)
                    except (json.JSONDecodeError, TypeError):
                        logger.error(
                            "Linked schema %s has unparseable fields JSON; "
                            "falling back to default taxonomy",
                            schema_id,
                        )
                        fields = []
                schema_fields = _custom_fields_to_schema_fields(fields or [])
                if not schema_fields:
                    logger.warning(
                        "Linked schema '%s' (id=%s) produced 0 extraction fields "
                        "— the schema's 'fields' JSON is empty or malformed. "
                        "Falling back to default taxonomy.",
                        schema.get("name", schema_id), schema_id,
                    )
                    schema_fields = self._schema_fields
                    source = (
                        f"default taxonomy (schema '{schema.get('name', schema_id)}' "
                        f"was empty/malformed)"
                    )
                else:
                    source = f"schema '{schema.get('name', schema_id)}'"
            else:
                logger.warning(
                    "Set references schema_id=%s but schema not found; "
                    "falling back to default taxonomy",
                    schema_id,
                )
                schema_fields = self._schema_fields
                source = "default taxonomy (linked schema not found)"
        else:
            schema_fields = self._schema_fields
            source = "default taxonomy"

        logger.info(
            "Extraction starting: source=%s, fields_requested=%d, file=%s",
            source, len(schema_fields), file_path,
        )

        if not schema_fields:
            msg = (
                f"Extraction skipped: schema_fields is empty (source={source}). "
                f"This means no fields were requested from the LLM."
            )
            logger.error(msg)
            self._last_extraction_diagnostics = {
                "source": source,
                "fields_requested": 0,
                "fields_returned": 0,
                "fields_with_value": 0,
                "requirements_built": 0,
                "warning": msg,
            }
            return []

        try:
            result = populate_schema_with_claude_chunked(
                pdf_path=file_path,
                schema_fields=schema_fields,
                model=getattr(cfg, "ANTHROPIC_ADVANCED", None),
                temperature=0.0,
                module_name="compliance_extraction",
            )
            requirements, conv_diag = self._convert_schema_result(
                result, return_diagnostics=True
            )
            self._last_extraction_diagnostics = {
                "source": source,
                "fields_requested": len(schema_fields),
                "total_pages": result.get("total_pages", 0),
                "chunk_count": result.get("chunk_count", 1),
                **conv_diag,
            }
            logger.info(
                "Extraction complete via %s: pages=%d, chunks=%d, "
                "fields_returned=%d, fields_with_value=%d, requirements_built=%d",
                source,
                result.get("total_pages", 0),
                result.get("chunk_count", 1),
                conv_diag.get("fields_returned", 0),
                conv_diag.get("fields_with_value", 0),
                conv_diag.get("requirements_built", 0),
            )
            if not requirements:
                # Provide a sample of what came back to help diagnose
                fields_sample = list((result.get("fields") or {}).items())[:5]
                logger.warning(
                    "Extraction produced 0 requirements. Sample of returned fields: %s",
                    [(k, (v or {}).get("value")) for k, v in fields_sample],
                )
            return requirements
        except Exception as e:
            logger.exception("Requirement extraction failed: %s", e)
            self._last_extraction_diagnostics = {
                "source": source,
                "fields_requested": len(schema_fields),
                "error": str(e),
            }
            return []

    def _extract_via_workflow(
        self, workflow_id: int, file_path: str, set_info: Dict[str, Any]
    ) -> List[ExtractedRequirement]:
        """Run a linked workflow for extraction and convert results."""
        import time
        from workflow_api_client import WorkflowAPIClient

        try:
            client = WorkflowAPIClient()
            resp = client.start_workflow(
                workflow_id=workflow_id,
                initiator="compliance_engine",
                workflow_data={
                    "variables": {
                        "document_path": file_path,
                        "document_set_id": str(set_info.get("set_id", "")),
                    }
                },
            )
            execution_id = resp.get("execution_id")
            if not execution_id:
                raise RuntimeError(f"Workflow did not return execution_id: {resp}")

            timeout = getattr(cfg, "DOC_PROCESSING_TIMEOUT_MINUTES", 120) * 60
            poll_interval = 5
            elapsed = 0
            while elapsed < timeout:
                status = client.get_status(execution_id)
                state = status.get("status", "")
                if state == "completed":
                    return self._convert_workflow_result(status)
                if state in ("failed", "cancelled", "error"):
                    raise RuntimeError(
                        f"Workflow {workflow_id} {state}: "
                        f"{status.get('error', 'unknown error')}"
                    )
                time.sleep(poll_interval)
                elapsed += poll_interval

            raise RuntimeError(
                f"Workflow {workflow_id} timed out after {timeout}s"
            )
        except Exception as e:
            logger.error("Workflow extraction failed: %s", e)
            return []

    def _convert_workflow_result(
        self, status: Dict[str, Any]
    ) -> List[ExtractedRequirement]:
        """Convert workflow execution output to ExtractedRequirement list."""
        variables = status.get("variables", {})
        extracted = variables.get("extraction_result") or variables.get("extracted") or {}
        if isinstance(extracted, str):
            extracted = json.loads(extracted)

        if "fields" in extracted:
            return self._convert_schema_result(extracted)

        if isinstance(extracted, list):
            requirements = []
            for item in extracted:
                requirements.append(
                    ExtractedRequirement(
                        category=item.get("category", "general"),
                        subcategory=item.get("subcategory", "general"),
                        requirement_text=item.get("requirement_text", item.get("value", "")),
                        specific_value=item.get("specific_value"),
                        severity=item.get("severity"),
                        source_page=item.get("source_page"),
                        confidence=item.get("confidence"),
                    )
                )
            return requirements
        return []

    def _convert_schema_result(
        self,
        result: Dict[str, Any],
        return_diagnostics: bool = False,
    ):
        """Convert populate_schema_with_claude_chunked output to ExtractedRequirement list.

        If return_diagnostics is True, returns (requirements, diagnostics_dict).
        Otherwise returns just requirements (legacy callers).
        """
        fields = result.get("fields", {}) or {}
        valid_categories = set(self._taxonomy.get("categories", {}).keys())
        requirements: List[ExtractedRequirement] = []

        fields_returned = len(fields)
        fields_with_value = 0

        for field_key, field_data in fields.items():
            field_data = field_data or {}
            value = field_data.get("value")
            # Treat None and empty string as "no value", but allow legitimate
            # falsy values like False / 0 to flow through.
            if value is None or value == "":
                continue
            fields_with_value += 1

            parts = field_key.split(".", 2)
            if len(parts) == 3:
                cat, sub, field_name = parts
            else:
                cat, sub, field_name = "general", "general", None

            if cat not in valid_categories:
                cat = "general"
            else:
                valid_subs = set(
                    self._taxonomy["categories"][cat].get("subcategories", {}).keys()
                )
                if sub not in valid_subs:
                    sub = "general"

            sources = field_data.get("sources") or []
            source_page = None
            if sources:
                pages = (sources[0] or {}).get("pages") or []
                if pages:
                    source_page = pages[0]

            # ----- Repeated-group expansion -----
            # If the LLM returned a list (a repeated_group field), explode each
            # item into its own ExtractedRequirement so the user sees one row
            # per note instead of a single row containing JSON-serialized chaos.
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        # Edge case: list of strings — flatten as text rows
                        requirements.append(
                            ExtractedRequirement(
                                category=cat,
                                subcategory=field_key,
                                requirement_text=str(item),
                                source_page=source_page,
                                confidence=1.0,
                            )
                        )
                        continue
                    # Map common keys (topic, requirement, value, ...) to
                    # ExtractedRequirement fields. Fall back to dumping the
                    # whole item as JSON if no recognizable structure.
                    item_topic = (
                        item.get("topic")
                        or item.get("category")
                        or item.get("section")
                        or cat
                    )
                    item_requirement = (
                        item.get("requirement")
                        or item.get("subcategory")
                        or item.get("name")
                        or field_key
                    )
                    item_value = item.get("value") or item.get("requirement_text")
                    if not item_value:
                        # Couldn't find an obvious "main text" field — fall back
                        # to JSON of the whole item so nothing is lost.
                        item_value = json.dumps(item, ensure_ascii=False)
                    item_confidence = item.get("confidence")
                    # Translate string confidence ("LOW"/"MED"/"HIGH") to float
                    if isinstance(item_confidence, str):
                        item_confidence = {
                            "high": 0.95, "med": 0.75, "medium": 0.75,
                            "low": 0.5,
                        }.get(item_confidence.lower())
                    requirements.append(
                        ExtractedRequirement(
                            category=str(item_topic)[:100],
                            subcategory=str(item_requirement)[:100],
                            requirement_text=str(item_value),
                            specific_value=(
                                str(item.get("excerpt"))[:255]
                                if item.get("excerpt") else None
                            ),
                            source_page=source_page,
                            confidence=item_confidence,
                        )
                    )
                continue

            # Dict value (single-instance group field) — JSON-serialize it
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)

            requirements.append(
                ExtractedRequirement(
                    category=cat,
                    subcategory=sub,
                    requirement_text=str(value),
                    specific_value=field_name,
                    source_page=source_page,
                    confidence=1.0,
                )
            )

        if return_diagnostics:
            return requirements, {
                "fields_returned": fields_returned,
                "fields_with_value": fields_with_value,
                "requirements_built": len(requirements),
            }
        return requirements

    # -- version management --------------------------------------------------

    def _create_version(
        self, set_id: int, document_id: str, uploaded_by: int, doc_hash: str
    ) -> tuple:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))

            cursor.execute(
                """
                SELECT ISNULL(MAX(version_number), 0)
                FROM RetailerDocumentVersions
                WHERE set_id = ?
                """,
                set_id,
            )
            max_version = cursor.fetchone()[0]
            new_version = max_version + 1

            if max_version > 0:
                cursor.execute(
                    """
                    UPDATE RetailerDocumentVersions
                    SET is_current = 0
                    WHERE set_id = ? AND is_current = 1
                    """,
                    set_id,
                )

            cursor.execute(
                """
                INSERT INTO RetailerDocumentVersions
                    (set_id, document_id, version_number, is_current, uploaded_by)
                OUTPUT INSERTED.version_id
                VALUES (?, ?, ?, 1, ?)
                """,
                set_id,
                document_id,
                new_version,
                uploaded_by,
            )
            version_id = cursor.fetchone()[0]
            conn.commit()

            logger.info(
                "Created version %d (id=%d) for set %d",
                new_version,
                version_id,
                set_id,
            )
            return version_id, new_version
        finally:
            cursor.close()
            conn.close()

    def _check_duplicate(self, set_id: int, doc_hash: str) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT v.version_id, v.version_number, v.document_id
                FROM RetailerDocumentVersions v
                JOIN Documents d ON d.document_id = v.document_id
                WHERE v.set_id = ? AND d.hash_value = ?
                """,
                set_id,
                doc_hash,
            )
            row = cursor.fetchone()
            if row:
                return {
                    "version_id": row.version_id,
                    "version_number": row.version_number,
                    "document_id": row.document_id,
                }
            return None
        finally:
            cursor.close()
            conn.close()

    def _store_document_hash(self, document_id: str, doc_hash: str) -> bool:
        """Record the file's hash on its Documents row — the value _check_duplicate
        compares against. Only a row without a hash is written. Returns True when it
        was stored.

        The row is written by the document service; if it is not visible yet the
        update is retried briefly. Failing to store is not fatal (the document is
        still processed) but that file will not be recognised as a duplicate later,
        so it is logged as a warning."""
        if not document_id or not doc_hash:
            return False
        import time as _time
        last_error = None
        for attempt in range(3):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
                    cursor.execute(
                        """
                        UPDATE Documents SET hash_value = ?
                        WHERE document_id = ? AND hash_value IS NULL
                        """,
                        doc_hash,
                        document_id,
                    )
                    stored = cursor.rowcount > 0
                    conn.commit()
                finally:
                    cursor.close()
                    conn.close()
                if stored:
                    return True
                last_error = "document row not found (or it already has a hash)"
            except Exception as e:
                last_error = str(e)
            if attempt < 2:
                _time.sleep(0.5)
        logger.warning(
            "File hash not stored for document %s (%s) — a later copy of this file "
            "will not be recognised as a duplicate", document_id, last_error,
        )
        return False

    def _duplicate_knowledge_note(self, agent_id: int, document_id: str) -> str:
        """For a file that turned out to be a duplicate: what the agent this run
        targets holds of it. A duplicate adds nothing, so the caller must be able to
        tell 'already there' from 'recorded, but this agent does not have it'."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
                cursor.execute(
                    """
                    SELECT agent_id, is_active, description
                    FROM AgentKnowledge WHERE document_id = ?
                    """,
                    document_id,
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            return f"duplicate: could not read the agent's knowledge ({e})"
        mine = [r for r in rows if str(r[0]) == str(agent_id)]
        if any(r[1] for r in mine):
            return "duplicate: this file is already in the agent's knowledge — nothing was added"
        if mine:
            # Deliberately out of the agent's knowledge: superseded by a newer version,
            # not added because a newer one was already held, or removed by a person.
            why = next((str(r[2]) for r in mine if r[2]), "")
            return ("duplicate: this file was left out of the agent's knowledge earlier"
                    + (f" ({why})" if why else "") + " and was not added again")
        others = sorted({str(r[0]) for r in rows if r[1]})
        return ("duplicate: this file is already recorded as a version but it is NOT in "
                "this agent's knowledge"
                + (f" (agent {', '.join(others)} holds it)" if others else "")
                + " — nothing was added")

    @staticmethod
    def _record_not_added(agent_id: int, document_id: str, newer_filename: str) -> bool:
        """Leave a trace of a document that was NOT added to the agent's knowledge
        because the agent already holds a newer version of the same manual: an
        INACTIVE knowledge row whose description says so. Inactive rows are never
        searched or listed (they are what a removed document leaves behind), so this
        changes no answer; it lets a later run on the same file say why the agent does
        not hold it. Best effort — returns False and logs when it cannot be written."""
        note = (f"Not added {datetime.now():%Y-%m-%d}: the agent holds a newer version"
                + (f" ({newer_filename})" if newer_filename else ""))[:200]
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
                cursor.execute(
                    """
                    INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active)
                    VALUES (?, ?, ?, getutcdate(), 0)
                    """,
                    agent_id, document_id, note,
                )
                conn.commit()
            finally:
                cursor.close()
                conn.close()
            return True
        except Exception as e:
            logger.warning("Could not record that document %s was not added to agent %s: %s",
                           document_id, agent_id, e)
            return False

    # -- requirement storage -------------------------------------------------

    def _store_requirements(
        self, version_id: int, requirements: List[ExtractedRequirement]
    ):
        if not requirements:
            return

        def _to_db_text(v, max_len=None):
            """Coerce arbitrary values to a SQL-safe string. None passes through."""
            if v is None:
                return None
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            elif not isinstance(v, str):
                v = str(v)
            if max_len is not None:
                v = v[:max_len]
            return v

        def _to_int_or_none(v):
            try:
                return int(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                return None

        def _to_float_or_none(v):
            try:
                return float(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                return None

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            for req in requirements:
                cursor.execute(
                    """
                    INSERT INTO ExtractedRequirements
                        (version_id, category, subcategory, requirement_text,
                         specific_value, severity, source_page, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    int(version_id),
                    _to_db_text(req.category, 100),
                    _to_db_text(req.subcategory, 100),
                    _to_db_text(req.requirement_text),
                    _to_db_text(req.specific_value, 255),
                    _to_db_text(req.severity, 50),
                    _to_int_or_none(req.source_page),
                    _to_float_or_none(req.confidence),
                )
            conn.commit()
            logger.info(
                "Stored %d requirements for version %d", len(requirements), version_id
            )
        finally:
            cursor.close()
            conn.close()

    # -- knowledge indexing --------------------------------------------------

    def _attach_attributes(self, document_id: str, retailer_id: int,
                           extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
        """Attach the retailer as the document's "customer", plus any attributes the
        run supplied. The customer comes from the run (the workflow knows whose folder
        the file came from), so it does not depend on what the file is called or on
        what its pages say. Non-fatal: a document that cannot be tagged is still
        processed, and its customer can still be read from the compliance records.
        An attribute the run supplies never overrides the customer."""
        try:
            import knowledge_scope
            attrs = knowledge_scope.clean_attributes(extra)
            retailer = self.get_retailer(retailer_id) or {}
            name = " ".join(str(retailer.get("name") or "").split())
            if name:
                attrs["customer"] = name
            if not attrs:
                return None
            stored = knowledge_scope.set_document_attributes(document_id, attrs)
            if stored is None:
                logger.warning("Attributes NOT stored for compliance document %s", document_id)
            else:
                logger.info("Attributes on compliance document %s: %s", document_id, stored)
            return stored
        except Exception as e:
            logger.warning("Attaching attributes to %s failed (non-fatal): %s", document_id, e)
            return None

    def _index_to_knowledge(self, agent_id: int, document_id: str, uploaded_by: int):
        """Index an already-ingested compliance document into the agent's
        SHARED knowledge base.

        IMPORTANT: this method DOES NOT call /document/process again. The
        compliance document was already ingested by _ingest_document, which
        produced ``document_id``. Re-ingesting via process_document_as_knowledge
        would create a SECOND Documents row pointing to the same file, breaking
        the link between the version and its AgentKnowledge entry (so version
        deletes can never clean up the knowledge entry, leading to orphans).

        Instead we directly:
          1. Insert an AgentKnowledge row with ``added_by = NULL`` (shared).
          2. Queue vector indexing for the document with user_id=None
             (the indexer will write the 'SHARED' sentinel into vector metadata).
        """
        try:
            # Import from agent_knowledge_routes (not app.py) — agent_knowledge_routes
            # has a more robust version of add_agent_knowledge with document-existence
            # retry logic, AND avoids pulling in the full app.py import chain. This
            # matters in the executor-service bundle, which doesn't bundle app.py's
            # dynamic spec_from_file_location loads (e.g. routes/data_explorer.py) —
            # importing app would trigger FileNotFoundError on those, get swallowed
            # by this try/except, and silently skip the entire knowledge indexing.
            from agent_knowledge_routes import add_agent_knowledge
            from agent_knowledge_integration import queue_knowledge_indexing

            knowledge_id = add_agent_knowledge(
                agent_id=agent_id,
                document_id=document_id,
                description="Retailer compliance document",
                user_id=None,  # shared — see SQL/vector visibility rules
            )
            if not knowledge_id:
                logger.warning(
                    "add_agent_knowledge returned no id (agent=%s, doc=%s)",
                    agent_id, document_id,
                )
                return

            queue_knowledge_indexing(
                document_id=document_id,
                agent_id=agent_id,
                user_id=None,  # → 'SHARED' sentinel in vector metadata
            )
            logger.info(
                "Indexed compliance document as SHARED agent knowledge "
                "(agent=%d, doc=%s, knowledge=%s, uploader=%s)",
                agent_id, document_id, knowledge_id, uploaded_by,
            )
        except Exception as e:
            logger.warning("Knowledge indexing failed (non-fatal): %s", e)

    # -- versions of one manual in an agent's knowledge ----------------------
    #
    # An agent that answers questions from compliance manuals must answer from the
    # CURRENT edition. Without this step every edition a retailer ever sent stays in
    # the agent's knowledge side by side, the search returns passages from all of
    # them, and nothing marks which is current (measured 2026-11-20: half of the
    # passages retrieved came from superseded editions, and answers were silently
    # taken from them).
    #
    # "Same manual" cannot be read from the compliance set: one set often holds
    # several different manuals of a retailer (a workflow that files everything under
    # one category), so the set's version numbers are NOT editions of one document.
    # It is decided from the documents themselves, by a small model that sees the
    # file names and the first pages (title page, date, version line, contents).

    _VERSION_PROFILE_CHARS = 1800       # of the first pages, per document
    _VERSION_PROMPT_BATCH_CHARS = 60000  # existing documents per model call (batching, not a limit)

    _VERSION_CHECK_SYSTEM = (
        "You compare ONE new document with other documents of the same company and "
        "decide, for each of the others, whether it is a different EDITION OF THE SAME "
        "MANUAL: the same document re-issued (same title, subject and scope), usually "
        "with a different date, year, version or revision number. Documents that belong "
        "together but cover different things are NOT editions of each other: different "
        "sections or parts of one agreement (Section 1 Legal and Section 2 Compliance), "
        "different topics (packaging and routing), different programs or regions "
        "(domestic and import), a manual and a form or template. A wrong 'true' removes "
        "a manual that is still valid, so when the evidence is not clear answer "
        "same_manual false or confidence low. Reply with STRICT JSON only, no prose, no "
        "markdown fences."
    )

    def _document_profile(self, cursor, document_id: str) -> Optional[Dict[str, Any]]:
        """File name, page count and the start of the first pages of a stored document."""
        cursor.execute(
            "SELECT filename, page_count FROM Documents WHERE document_id = ?",
            document_id,
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            """
            SELECT TOP 3 page_number, full_text
            FROM DocumentPages
            WHERE document_id = ? AND full_text IS NOT NULL AND LEN(full_text) > 0
            ORDER BY page_number
            """,
            document_id,
        )
        parts, used = [], 0
        for page in cursor.fetchall():
            text = " ".join(str(page[1] or "").split())
            if not text:
                continue
            room = self._VERSION_PROFILE_CHARS - used
            if room <= 0:
                break
            parts.append(f"[page {page[0]}] {text[:room]}")
            used += min(len(text), room)
        return {
            "document_id": str(document_id),
            "filename": str(row[0] or ""),
            "page_count": row[1],
            "first_pages": "\n".join(parts),
        }

    def _check_manual_versions(
        self, agent_id: int, retailer_id: int, new_document_id: str
    ) -> Dict[str, Any]:
        """Which documents in the agent's knowledge are other editions of the manual
        that just arrived.

        Returns a dict:
            older_existing : [{"document_id", "filename"}] editions the new one replaces
            newer_existing : {"document_id", "filename"} or None — an edition LATER than
                             the new document that the agent already holds
            note           : one line for the log / the workflow result
        Never raises: when the check cannot run nothing is replaced and the note says why.
        """
        out: Dict[str, Any] = {"older_existing": [], "newer_existing": None, "note": None}
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
                cursor.execute(
                    """
                    SELECT DISTINCT k.document_id
                    FROM AgentKnowledge k
                    JOIN RetailerDocumentVersions v ON v.document_id = k.document_id
                    JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                    WHERE k.agent_id = ? AND k.is_active = 1
                      AND s.retailer_id = ? AND k.document_id <> ?
                    """,
                    agent_id, retailer_id, new_document_id,
                )
                candidate_ids = [str(r[0]) for r in cursor.fetchall() if r[0]]
                if not candidate_ids:
                    out["note"] = ("version check: the agent holds no other document of "
                                   "this retailer")
                    return out
                new_doc = self._document_profile(cursor, new_document_id)
                candidates = [p for p in (self._document_profile(cursor, c)
                                          for c in candidate_ids) if p]
            finally:
                cursor.close()
                conn.close()
            if not new_doc or not candidates:
                out["note"] = ("version check could not run (document text not found) — "
                               "nothing was replaced")
                return out
            return self._judge_manual_versions(new_doc, candidates)
        except Exception as e:
            logger.warning("Version check failed (nothing replaced): %s", e)
            return {"older_existing": [], "newer_existing": None,
                    "note": f"version check could not run ({e}) — nothing was replaced"}

    def _judge_manual_versions(
        self, new_doc: Dict[str, Any], candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Decide which of ``candidates`` are other editions of the manual ``new_doc``
        (profiles from _document_profile). Same return shape as _check_manual_versions,
        plus new_filename. Never raises."""
        out: Dict[str, Any] = {"older_existing": [], "newer_existing": None, "note": None,
                               "new_filename": new_doc.get("filename")}
        try:
            from agent_knowledge_integration import _haiku_call_with_fallback

            def describe(label, d):
                return (f"[{label}] file name: {d['filename']} | pages: {d['page_count']}\n"
                        f"first pages:\n<<<\n{d['first_pages']}\n>>>")

            verdicts: Dict[str, Dict[str, Any]] = {}
            batch, batch_chars, batches = [], 0, []
            for i, cand in enumerate(candidates, start=1):
                block = describe(f"D{i}", cand)
                if batch and batch_chars + len(block) > self._VERSION_PROMPT_BATCH_CHARS:
                    batches.append(batch)
                    batch, batch_chars = [], 0
                batch.append((f"D{i}", cand, block))
                batch_chars += len(block)
            if batch:
                batches.append(batch)

            for batch in batches:
                prompt = (
                    "NEW DOCUMENT\n" + describe("NEW", new_doc) + "\n\n"
                    "EXISTING DOCUMENTS OF THE SAME COMPANY\n"
                    + "\n\n".join(block for _l, _c, block in batch) + "\n\n"
                    "For EVERY existing document reply in this exact JSON shape:\n"
                    '{"results": [{"id": "D1", "reason": "<one short sentence>", '
                    '"same_manual": true, "confidence": "high"}]}\n'
                    "- same_manual: true only when the existing document is another edition "
                    "of the SAME manual as the NEW document.\n"
                    '- confidence: "high" or "low".'
                )
                raw = _haiku_call_with_fallback(
                    prompt, self._VERSION_CHECK_SYSTEM, max_tokens=2000, temp=0.0
                )
                if not raw:
                    out["note"] = ("version check could not run (no reply from the model) — "
                                   "nothing was replaced")
                    return out
                text = raw.strip()
                start, end = text.find("{"), text.rfind("}")
                data = json.loads(text[start:end + 1]) if start != -1 and end > start else {}
                by_label = {label: cand for label, cand, _b in batch}
                for item in data.get("results") or []:
                    label = str(item.get("id") or "").strip().strip("[]")
                    if label in by_label:
                        verdicts[by_label[label]["document_id"]] = item

            same = [
                (c, verdicts[c["document_id"]]) for c in candidates
                if c["document_id"] in verdicts
                and verdicts[c["document_id"]].get("same_manual") is True
                and str(verdicts[c["document_id"]].get("confidence", "")).lower() == "high"
            ]
            if not same:
                out["note"] = (f"version check: not another version of any of the "
                               f"{len(candidates)} other document(s) the agent holds for "
                               f"this retailer")
                return out
            undated, unclear = [], []
            for cand, _verdict in same:
                ref = {"document_id": cand["document_id"], "filename": cand["filename"]}
                later = self._more_recent_edition(new_doc, cand, _haiku_call_with_fallback)
                if later == "existing":
                    out["newer_existing"] = out["newer_existing"] or ref
                elif later == "new":
                    out["older_existing"].append(ref)
                elif later == "unknown":
                    # Neither document shows a date or a version: the one that arrived
                    # later is taken as the current one.
                    out["older_existing"].append(ref)
                    undated.append(cand["filename"])
                else:
                    # The two readings disagree — replacing either could remove the
                    # current manual, so both stay and the note says so.
                    unclear.append(cand["filename"])
            bits = []
            if out["newer_existing"]:
                bits.append("NOT added to the agent's knowledge: the agent holds a newer "
                            f"version of this manual ({out['newer_existing']['filename']})")
            if out["older_existing"]:
                bits.append("older version(s) taken out of the agent's knowledge: "
                            + ", ".join(o["filename"] for o in out["older_existing"]))
            if undated:
                bits.append("the documents show the same date and version, or none, for "
                            + ", ".join(undated)
                            + " — the one that arrived later was taken as current")
            if unclear:
                bits.append("could not run for " + ", ".join(unclear)
                            + " (same manual, but which version is newer is unclear) — "
                            "both versions are in the agent's knowledge, remove the old one "
                            "by hand")
            out["note"] = "version check: " + "; ".join(bits)
            return out
        except Exception as e:
            logger.warning("Version check failed (nothing replaced): %s", e)
            return {"older_existing": [], "newer_existing": None,
                    "note": f"version check could not run ({e}) — nothing was replaced"}

    _EDITION_ORDER_SYSTEM = (
        "You are given two editions of the same manual, DOCUMENT A and DOCUMENT B. Decide "
        "which is the MORE RECENT edition, from the dates, years, version numbers and "
        "revision numbers in their file names and first pages. Reply with STRICT JSON "
        'only, no prose, no markdown fences: {"a_edition": "<the date or version you '
        'found for A, or none>", "b_edition": "<the date or version you found for B, or '
        'none>", "more_recent": "A"}. more_recent is "A", "B" or "unknown"; answer '
        '"unknown" when neither shows a date or version, or both show the same one.'
    )

    def _more_recent_edition(self, new_doc: Dict[str, Any], existing: Dict[str, Any],
                             llm_call) -> str:
        """Which of two editions of one manual is the later one: 'new', 'existing',
        'unknown' (nothing in either shows it) or 'conflict'.

        Taking the wrong document out of an agent's knowledge removes the CURRENT
        manual, so the question is asked twice with the two documents in swapped
        positions and only an answer that survives the swap is used. (A single reply
        with a relative word — "older"/"newer" — was wrong in testing while its own
        explanation was right; a named winner plus the swap check is what holds.)"""
        def describe(label, d):
            return (f"DOCUMENT {label}\nfile name: {d['filename']} | pages: {d['page_count']}\n"
                    f"first pages:\n<<<\n{d['first_pages']}\n>>>")

        def ask(first, second):
            raw = llm_call(describe("A", first) + "\n\n" + describe("B", second),
                           self._EDITION_ORDER_SYSTEM, max_tokens=300, temp=0.0)
            text = (raw or "").strip()
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                return None
            answer = str(json.loads(text[start:end + 1]).get("more_recent", "")).strip().upper()
            return answer if answer in ("A", "B", "UNKNOWN") else None

        try:
            straight = ask(new_doc, existing)      # A = new, B = existing
            swapped = ask(existing, new_doc)       # A = existing, B = new
        except Exception as e:
            logger.warning("Edition-order check failed: %s", e)
            return "conflict"
        first = {"A": "new", "B": "existing", "UNKNOWN": "unknown"}.get(straight)
        second = {"A": "existing", "B": "new", "UNKNOWN": "unknown"}.get(swapped)
        if first and first == second:
            return first
        return "conflict"

    @staticmethod
    def _retire_knowledge_for_document(agent_id: int, document_id: str,
                                       replaced_by: Optional[str] = None) -> bool:
        """Take a document out of ONE agent's knowledge (soft delete). The document,
        its pages and its compliance version record stay, and the knowledge row says
        when and by what it was superseded. The vectors are removed unless another
        agent still holds the document. Returns True when a knowledge row was retired.

        Reversible without the original file: set the row's is_active back to 1 and
        call queue_knowledge_indexing(document_id, agent_id) — the index is rebuilt
        from the stored pages."""
        if not document_id:
            return False
        note = f"Superseded {datetime.now():%Y-%m-%d}"
        if replaced_by:
            note += f": replaced by {replaced_by}"
        note = note[:200]
        conn = get_db_connection()
        cursor = conn.cursor()
        retired = False
        still_held = True
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            try:
                cursor.execute(
                    """
                    UPDATE AgentKnowledge SET is_active = 0, description = ?
                    WHERE document_id = ? AND agent_id = ? AND is_active = 1
                    """,
                    note, document_id, agent_id,
                )
            except Exception as note_err:
                # The retirement matters, the note is a courtesy: a description column
                # that cannot take the text must not leave the old version answering.
                logger.warning("Superseded note not stored for doc %s (%s) — retiring "
                               "without it", document_id, note_err)
                cursor.execute(
                    """
                    UPDATE AgentKnowledge SET is_active = 0
                    WHERE document_id = ? AND agent_id = ? AND is_active = 1
                    """,
                    document_id, agent_id,
                )
            retired = cursor.rowcount > 0
            conn.commit()
            cursor.execute(
                "SELECT COUNT(*) FROM AgentKnowledge WHERE document_id = ? AND is_active = 1",
                document_id,
            )
            row = cursor.fetchone()
            still_held = bool(row and row[0])
        except Exception as e:
            logger.warning("Retiring knowledge for doc %s (agent %s) failed: %s",
                           document_id, agent_id, e)
            return False
        finally:
            cursor.close()
            conn.close()

        if retired:
            logger.info("Retired superseded document %s from agent %s knowledge",
                        document_id, agent_id)
        if retired and not still_held:
            try:
                from agent_knowledge_integration import queue_knowledge_vector_delete
                queue_knowledge_vector_delete(document_id)
            except Exception as e:
                logger.warning("Vector cleanup queue failed for doc %s: %s", document_id, e)
        return retired

    # -- excel population ----------------------------------------------------

    def _populate_excel(
        self,
        requirements: List[ExtractedRequirement],
        retailer_id: int,
        template_path: str,
    ) -> Optional[str]:
        try:
            from excel_utils import populate_excel, detect_template_schema

            schema = detect_template_schema(template_path)
            rows = []
            for req in requirements:
                rows.append(
                    {
                        "Category": req.category,
                        "Subcategory": req.subcategory,
                        "Requirement": req.requirement_text,
                        "Value": req.specific_value or "",
                        "Severity": req.severity or "",
                        "Source Page": req.source_page or "",
                        "Confidence": req.confidence or "",
                    }
                )
            mapped_data = {"rows": rows}

            output_dir = os.path.join(
                os.getenv("APP_ROOT", "."), "data", "compliance_exports"
            )
            os.makedirs(output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(
                output_dir, f"compliance_r{retailer_id}_{ts}.xlsx"
            )

            result = populate_excel(
                output_path=output_path,
                mapped_data=mapped_data,
                schema=schema,
                template_path=template_path,
                operation="append",
            )
            if result.get("success"):
                return result["file_path"]
            return None
        except Exception as e:
            logger.warning("Excel population failed (non-fatal): %s", e)
            return None

    # -- change summary generation -------------------------------------------

    def _generate_change_summary(self, set_id: int, current_version_id: int) -> Optional[str]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT version_id FROM RetailerDocumentVersions
                WHERE set_id = ? AND version_id < ? AND is_current = 0
                ORDER BY version_number DESC
                """,
                set_id,
                current_version_id,
            )
            row = cursor.fetchone()
            if not row:
                return None
            prev_version_id = row.version_id
        finally:
            cursor.close()
            conn.close()

        from compliance_comparison import ComplianceComparison

        comparison = ComplianceComparison()
        result = comparison.compare_versions(prev_version_id, current_version_id)
        if result and result.get("summary"):
            meaningful = sum(
                s.get("meaningful_changes", 0) for s in result["summary"]
            )
            total = sum(s.get("total", 0) for s in result["summary"])
            summary = f"{meaningful} meaningful changes out of {total} total differences detected."
            self._update_change_summary(current_version_id, summary)
            return summary
        return None

    def _update_change_summary(self, version_id: int, summary: str):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                UPDATE RetailerDocumentVersions
                SET change_summary = ?
                WHERE version_id = ?
                """,
                summary,
                version_id,
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    # -- utilities -----------------------------------------------------------

    @staticmethod
    def _file_hash(file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    # -- CRUD helpers (used by routes) ---------------------------------------

    @staticmethod
    def create_retailer(name: str, notes: str = "", created_by: int = 1) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                INSERT INTO Retailers (name, notes, created_by)
                OUTPUT INSERTED.retailer_id
                VALUES (?, ?, ?)
                """,
                name,
                notes,
                created_by,
            )
            retailer_id = cursor.fetchone()[0]
            conn.commit()
            return retailer_id
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_retailer_by_name(name: str) -> Optional[Dict]:
        """Case-insensitive retailer lookup by exact name."""
        if not name:
            return None
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "SELECT retailer_id, name, notes FROM Retailers WHERE LOWER(name) = LOWER(?)",
                name.strip(),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {"retailer_id": row.retailer_id, "name": row.name, "notes": row.notes}
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_set_by_category(retailer_id: int, category: str) -> Optional[Dict]:
        """Case-insensitive set lookup by (retailer_id, category)."""
        if not category:
            return None
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT set_id, retailer_id, category, description, agent_id
                FROM RetailerDocumentSets
                WHERE retailer_id = ? AND LOWER(category) = LOWER(?)
                """,
                retailer_id,
                category.strip(),
            )
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, row))
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def resolve_set(
        retailer_name: str,
        set_category: str,
        on_missing: str = "error",
        created_by: int = 1,
        auto_create_agent: bool = False,
        agent_mode: str = "per_set",
        agent_objective_template: str = "",
        retailer_agent_override_id: Optional[int] = None,
    ) -> Dict:
        """Find or (optionally) create the retailer+set pair from names.

        Args:
            retailer_name: Retailer name (case-insensitive match)
            set_category: Set category (case-insensitive match within retailer)
            on_missing: "error" (raise if not found) or "auto_create"
            created_by: User id for auto-create
            auto_create_agent: When True AND a new set is auto-created,
                also create a dedicated compliance agent and associate it
                with the set. Ignored when the set already exists or
                on_missing != "auto_create".
            agent_mode: How to handle agent creation when auto_create_agent=True:
                "per_set" — each new set gets its own agent
                    (e.g. "Compliance - Walmart (DI)")
                "per_retailer" — one agent shared across all sets for the
                    retailer (e.g. "Compliance - Walmart"). Detection cascade
                    (in priority order):
                      Layer 3: retailer_agent_override_id (if provided)
                      Layer 1: agent named "Compliance - {retailer_name}"
                      Layer 2: agent already shared across 2+ sets
                      Layer 4: create new agent
            agent_objective_template: Custom objective for auto-created agents.
                Supports {{retailer_name}} and {{set_category}} placeholders.
                If empty, a generic default is used.
            retailer_agent_override_id: Optional explicit agent id to use as
                the retailer-level agent when agent_mode="per_retailer". Wins
                over name/shared-usage detection. Use this when you have a
                manually-named agent that the heuristics can't auto-detect.
                Ignored in per_set mode.

        Returns: dict with set_id, retailer_id, category, agent_id (may be None)
        Raises: ValueError if missing and on_missing == "error"
        """
        retailer = ComplianceEngine.get_retailer_by_name(retailer_name)
        if not retailer:
            if on_missing == "auto_create":
                rid = ComplianceEngine.create_retailer(
                    name=retailer_name.strip(), notes="", created_by=created_by
                )
                retailer = {"retailer_id": rid, "name": retailer_name.strip()}
            else:
                raise ValueError(
                    f"Retailer not found: '{retailer_name}'. Pre-create it in the "
                    f"compliance UI or set onMissing='auto_create'."
                )

        doc_set = ComplianceEngine.get_set_by_category(
            retailer["retailer_id"], set_category
        )
        if not doc_set:
            if on_missing == "auto_create":
                # Optionally create/reuse an agent for this set
                agent_id = None
                if auto_create_agent:
                    if agent_mode == "per_retailer":
                        agent_id = None

                        # Layer 3: explicit override wins
                        if retailer_agent_override_id:
                            try:
                                override = int(retailer_agent_override_id)
                                if override > 0:
                                    agent_id = override
                                    logger.info(
                                        f"resolve_set: using explicit retailer "
                                        f"agent override {agent_id} for "
                                        f"'{retailer['name']}' (per_retailer mode)"
                                    )
                            except (TypeError, ValueError):
                                logger.warning(
                                    f"resolve_set: invalid retailer_agent_override_id "
                                    f"value={retailer_agent_override_id!r}, ignoring"
                                )

                        # Layers 1 & 2: name match then shared-usage detection
                        if agent_id is None:
                            agent_id = ComplianceEngine._find_retailer_agent(
                                retailer["retailer_id"],
                                retailer["name"],
                            )
                            if agent_id:
                                logger.info(
                                    f"resolve_set: reusing existing agent {agent_id} "
                                    f"for retailer '{retailer['name']}' (per_retailer mode)"
                                )

                        # Layer 4: nothing found — create new
                        if agent_id is None:
                            agent_id = ComplianceEngine._auto_create_agent(
                                retailer_name=retailer["name"],
                                set_category="",  # no category in name for per_retailer
                                objective_template=agent_objective_template,
                            )
                            logger.info(
                                f"resolve_set: created retailer-level agent {agent_id} "
                                f"for '{retailer['name']}' (per_retailer mode)"
                            )
                    else:
                        # per_set mode — each set gets its own agent
                        agent_id = ComplianceEngine._auto_create_agent(
                            retailer_name=retailer["name"],
                            set_category=set_category.strip(),
                            objective_template=agent_objective_template,
                        )
                        logger.info(
                            f"resolve_set: created set-level agent {agent_id} for "
                            f"'{retailer['name']}' / '{set_category}' (per_set mode)"
                        )

                sid = ComplianceEngine.create_document_set(
                    retailer_id=retailer["retailer_id"],
                    category=set_category.strip(),
                    description="",
                    agent_id=agent_id,
                )
                doc_set = {
                    "set_id": sid,
                    "retailer_id": retailer["retailer_id"],
                    "category": set_category.strip(),
                    "agent_id": agent_id,
                }
            else:
                raise ValueError(
                    f"Document set not found: retailer='{retailer['name']}', "
                    f"category='{set_category}'. Pre-create it in the compliance UI "
                    f"or set onMissing='auto_create'."
                )

        return doc_set

    @staticmethod
    def _find_retailer_agent(retailer_id: int, retailer_name: str) -> Optional[int]:
        """Find an existing agent suitable for retailer-level reuse.

        Used by per_retailer agent mode to avoid creating duplicate agents
        for a retailer. Detection cascades through two layers:

          Layer 1 (name match) — find an agent named exactly
            "Compliance - {retailer_name}" (case-insensitive). This catches:
              • Auto-created retailer-level agents (always named this way)
              • Manually-created agents that follow the naming convention.
            Set-level agents are named "Compliance - X (Category)" with a
            paren suffix, so they are correctly skipped here.

          Layer 2 (shared usage) — find an agent that is already linked to
            two or more sets for this retailer. If a human linked the same
            agent to multiple sets, their intent is clearly "shared/retailer
            level," regardless of what they named it.

        If neither layer finds an agent, returns None and the caller will
        create a new agent. Callers wanting hard control should use the
        Retailer Agent Override on the workflow node config (handled
        upstream in resolve_set, not here).

        Args:
            retailer_id: The retailer to scope the search to.
            retailer_name: Used to build the canonical name for Layer 1.

        Returns:
            agent_id of a suitable existing agent, or None.
        """
        if not retailer_name:
            return None
        canonical_name = f"Compliance - {retailer_name.strip()}"

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))

            # ---- Layer 1: exact name match (case-insensitive) ----
            # Restricted to agents already linked to a set for this retailer
            # so we don't accidentally adopt a same-named agent that has
            # nothing to do with this retailer's compliance flow.
            cursor.execute(
                """
                SELECT TOP 1 a.id
                FROM Agents a
                INNER JOIN RetailerDocumentSets rds
                    ON rds.agent_id = a.id
                WHERE rds.retailer_id = ?
                  AND LOWER(a.description) = LOWER(?)
                ORDER BY a.id ASC
                """,
                retailer_id,
                canonical_name,
            )
            row = cursor.fetchone()
            if row:
                logger.info(
                    f"_find_retailer_agent: Layer 1 (name match) → "
                    f"agent_id={row[0]} for retailer '{retailer_name}'"
                )
                return row[0]

            # ---- Layer 2: agent already shared across 2+ sets ----
            cursor.execute(
                """
                SELECT TOP 1 agent_id
                FROM RetailerDocumentSets
                WHERE retailer_id = ? AND agent_id IS NOT NULL
                GROUP BY agent_id
                HAVING COUNT(*) > 1
                ORDER BY agent_id ASC
                """,
                retailer_id,
            )
            row = cursor.fetchone()
            if row:
                logger.info(
                    f"_find_retailer_agent: Layer 2 (shared across sets) → "
                    f"agent_id={row[0]} for retailer '{retailer_name}'"
                )
                return row[0]

            # ---- No match — caller will create new ----
            return None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _auto_create_agent(
        retailer_name: str,
        set_category: str,
        objective_template: str = "",
    ) -> Optional[int]:
        """Create a dedicated agent for a retailer/category.

        The agent is pre-configured with the search_agent_knowledge core
        tool so that documents indexed to it are immediately queryable via chat.

        Args:
            retailer_name: Used in the agent name and objective.
            set_category: Used in agent name suffix (if non-empty).
            objective_template: Optional custom objective. Supports placeholders:
                {{retailer_name}}, {{set_category}}.
                If empty, a generic default is used.

        Returns the new agent_id, or None on failure.
        """
        try:
            from DataUtils import insert_agent_with_tools
        except ImportError:
            logger.error("_auto_create_agent: DataUtils not available")
            return None

        agent_name = f"Compliance - {retailer_name}"
        if set_category:
            agent_name += f" ({set_category})"

        # Build objective from template or generic default
        if objective_template and objective_template.strip():
            objective = objective_template.replace(
                "{{retailer_name}}", retailer_name
            ).replace(
                "{{set_category}}", set_category or ""
            )
        else:
            objective = (
                f"You are a document knowledge assistant for {retailer_name}. "
                f"Answer questions using the documents in your knowledge base. "
                f"Always cite the specific document and section when answering."
            )

        core_tools = ["search_agent_knowledge"]
        custom_tools = []  # can be extended later

        agent_id = insert_agent_with_tools(
            agent_description=agent_name,
            agent_objective=objective,
            agent_enabled=True,
            tool_names=custom_tools,
            core_tool_names=core_tools,
        )

        if agent_id:
            logger.info(
                f"_auto_create_agent: created agent '{agent_name}' "
                f"(agent_id={agent_id})"
            )
        else:
            logger.error(
                f"_auto_create_agent: failed to create agent '{agent_name}'"
            )
        return agent_id

    @staticmethod
    def get_retailers() -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT r.retailer_id, r.name, r.notes, r.created_at, r.updated_at,
                       (SELECT COUNT(*) FROM RetailerDocumentSets WHERE retailer_id = r.retailer_id) AS set_count,
                       (SELECT COUNT(*) FROM RetailerDocumentVersions v
                        JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                        WHERE s.retailer_id = r.retailer_id) AS version_count,
                       (SELECT MAX(v.uploaded_at) FROM RetailerDocumentVersions v
                        JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                        WHERE s.retailer_id = r.retailer_id) AS last_upload
                FROM Retailers r
                ORDER BY r.name
                """
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_retailer(retailer_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "SELECT retailer_id, name, notes, created_at, updated_at FROM Retailers WHERE retailer_id = ?",
                retailer_id,
            )
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, row))
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def update_retailer(retailer_id: int, name: str, notes: str = "") -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                UPDATE Retailers SET name = ?, notes = ?, updated_at = GETDATE()
                WHERE retailer_id = ?
                """,
                name,
                notes,
                retailer_id,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_retailer(retailer_id: int) -> bool:
        # Clean up agent knowledge for every version under this retailer first.
        ComplianceEngine._cleanup_knowledge_for_retailer(retailer_id)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "DELETE FROM Retailers WHERE retailer_id = ?", retailer_id
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    # -- delete helpers ------------------------------------------------------

    @staticmethod
    def share_existing_compliance_knowledge() -> Dict[str, Any]:
        """One-shot admin helper: convert pre-existing compliance docs to SHARED.

        Before the visibility fix, compliance docs were inserted with
        ``AgentKnowledge.added_by = '<uploader id>'`` (user-specific) and indexed
        in ChromaDB with that same user_id in the vector metadata.

        This helper:
          1. Finds all AgentKnowledge rows whose ``description`` starts with
             ``'Retailer compliance document'``.
          2. Sets ``added_by = NULL`` on those rows (SQL-side: shared).
          3. For each affected document, re-indexes the vectors using the
             current SHARED-aware indexer (which writes ``user_id='SHARED'``
             into the vector metadata).

        Returns a dict with counts so the caller can display a summary.
        """
        # 1. SQL-side: flip added_by to NULL
        conn = get_db_connection()
        cursor = conn.cursor()
        affected_documents: List[Dict[str, Any]] = []
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT ak.knowledge_id, ak.document_id, ak.agent_id
                FROM AgentKnowledge ak
                WHERE ak.is_active = 1
                  AND ak.description LIKE 'Retailer compliance document%'
                  AND ak.added_by IS NOT NULL
                """
            )
            for row in cursor.fetchall():
                affected_documents.append({
                    "knowledge_id": row.knowledge_id,
                    "document_id": row.document_id,
                    "agent_id": row.agent_id,
                })

            if affected_documents:
                cursor.execute(
                    """
                    UPDATE AgentKnowledge
                    SET added_by = NULL
                    WHERE is_active = 1
                      AND description LIKE 'Retailer compliance document%'
                      AND added_by IS NOT NULL
                    """
                )
                conn.commit()
        finally:
            cursor.close()
            conn.close()

        # 2. Vector-side: re-queue indexing so vector metadata gets 'SHARED'.
        # The indexer is idempotent — it deletes prior vectors before re-indexing.
        reindexed = 0
        failed = 0
        try:
            from agent_knowledge_integration import (
                queue_knowledge_vector_delete, queue_knowledge_indexing,
            )
            for d in affected_documents:
                try:
                    # Delete old user-scoped vectors first, then re-index as SHARED.
                    queue_knowledge_vector_delete(d["document_id"])
                    queue_knowledge_indexing(
                        document_id=d["document_id"],
                        agent_id=d["agent_id"],
                        user_id=None,  # → 'SHARED' sentinel in vector metadata
                    )
                    reindexed += 1
                except Exception as e:
                    logger.warning(
                        "Re-index queue failed for doc %s: %s", d["document_id"], e
                    )
                    failed += 1
        except Exception as e:
            logger.exception("Re-index orchestration failed: %s", e)

        return {
            "rows_updated": len(affected_documents),
            "reindexed": reindexed,
            "reindex_failed": failed,
            "documents": [d["document_id"] for d in affected_documents],
        }

    @staticmethod
    def cleanup_orphaned_compliance_knowledge() -> Dict[str, Any]:
        """One-shot admin helper: remove orphaned compliance knowledge entries.

        Before the double-ingestion fix, the compliance engine called
        ``/document/process`` twice per upload — once via _ingest_document
        (used by RetailerDocumentVersions) and once via process_document_as_knowledge
        (used by AgentKnowledge). The two paths produced DIFFERENT document_ids
        for the same source file. When a version was deleted, the cleanup looked
        for AgentKnowledge by the version's document_id and never found the
        knowledge entry (which had a different document_id), leaving an orphan.

        This helper finds AgentKnowledge rows whose:
          - description starts with 'Retailer compliance document', AND
          - is_active = 1, AND
          - document_id is NOT referenced by any RetailerDocumentVersions row,

        soft-deletes them, and queues vector cleanup. Idempotent.
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        orphans: List[Dict[str, Any]] = []
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT ak.knowledge_id, ak.document_id, ak.agent_id, d.filename
                FROM AgentKnowledge ak
                LEFT JOIN Documents d ON d.document_id = ak.document_id
                WHERE ak.is_active = 1
                  AND ak.description LIKE 'Retailer compliance document%'
                  AND NOT EXISTS (
                      SELECT 1 FROM RetailerDocumentVersions v
                      WHERE v.document_id = ak.document_id
                  )
                """
            )
            for row in cursor.fetchall():
                orphans.append({
                    "knowledge_id": row.knowledge_id,
                    "document_id": row.document_id,
                    "agent_id": row.agent_id,
                    "filename": row.filename,
                })

            for o in orphans:
                cursor.execute(
                    "UPDATE AgentKnowledge SET is_active = 0 WHERE knowledge_id = ?",
                    o["knowledge_id"],
                )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        # Queue vector cleanup for each orphan's document
        try:
            from agent_knowledge_integration import queue_knowledge_vector_delete
            for o in orphans:
                try:
                    queue_knowledge_vector_delete(o["document_id"])
                except Exception as e:
                    logger.warning(
                        "Vector cleanup queue failed for orphan doc %s: %s",
                        o["document_id"], e,
                    )
        except Exception as e:
            logger.exception("Orphan vector cleanup orchestration failed: %s", e)

        return {
            "orphans_removed": len(orphans),
            "orphans": orphans,
        }

    @staticmethod
    def _cleanup_knowledge_for_document(document_id: str):
        """Soft-delete AgentKnowledge entries for this document and queue vector cleanup."""
        if not document_id:
            return
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                UPDATE AgentKnowledge SET is_active = 0
                WHERE document_id = ? AND is_active = 1
                """,
                document_id,
            )
            conn.commit()
        except Exception as e:
            logger.warning("Knowledge soft-delete failed for doc %s: %s", document_id, e)
        finally:
            cursor.close()
            conn.close()

        try:
            from agent_knowledge_integration import queue_knowledge_vector_delete
            queue_knowledge_vector_delete(document_id)
        except Exception as e:
            logger.warning("Vector cleanup queue failed for doc %s: %s", document_id, e)

    @staticmethod
    def _cleanup_knowledge_for_set(set_id: int):
        """Clean up AgentKnowledge for every version of the given set."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "SELECT document_id FROM RetailerDocumentVersions WHERE set_id = ?",
                set_id,
            )
            doc_ids = [row[0] for row in cursor.fetchall() if row[0]]
        finally:
            cursor.close()
            conn.close()

        for did in doc_ids:
            ComplianceEngine._cleanup_knowledge_for_document(did)

    @staticmethod
    def _cleanup_knowledge_for_retailer(retailer_id: int):
        """Clean up AgentKnowledge for every version under every set of this retailer."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT v.document_id
                FROM RetailerDocumentVersions v
                JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                WHERE s.retailer_id = ?
                """,
                retailer_id,
            )
            doc_ids = [row[0] for row in cursor.fetchall() if row[0]]
        finally:
            cursor.close()
            conn.close()

        for did in doc_ids:
            ComplianceEngine._cleanup_knowledge_for_document(did)

    @staticmethod
    def delete_version(version_id: int) -> Dict[str, Any]:
        """Delete a single version. Returns details about what happened.

        Cleans up agent knowledge for the underlying document, deletes the
        version row (cascading ExtractedRequirements via FK), and if the
        deleted version was current, promotes the next-highest version_number
        in the same set to is_current=1.

        Returns: { deleted: bool, was_current: bool, promoted_version_id: int|None,
                   set_id: int, document_id: str }
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT version_id, set_id, document_id, is_current, version_number
                FROM RetailerDocumentVersions WHERE version_id = ?
                """,
                version_id,
            )
            row = cursor.fetchone()
            if not row:
                return {"deleted": False, "error": "Version not found"}
            set_id = row.set_id
            document_id = row.document_id
            was_current = bool(row.is_current)
        finally:
            cursor.close()
            conn.close()

        # Clean up agent knowledge for this document (best-effort)
        ComplianceEngine._cleanup_knowledge_for_document(document_id)

        # Delete the version (cascades ExtractedRequirements via FK)
        conn = get_db_connection()
        cursor = conn.cursor()
        promoted_id = None
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "DELETE FROM RetailerDocumentVersions WHERE version_id = ?",
                version_id,
            )
            deleted = cursor.rowcount > 0

            if deleted and was_current:
                # Promote the highest remaining version_number to current
                cursor.execute(
                    """
                    SELECT TOP 1 version_id FROM RetailerDocumentVersions
                    WHERE set_id = ?
                    ORDER BY version_number DESC
                    """,
                    set_id,
                )
                next_row = cursor.fetchone()
                if next_row:
                    promoted_id = next_row.version_id
                    cursor.execute(
                        "UPDATE RetailerDocumentVersions SET is_current = 1 WHERE version_id = ?",
                        promoted_id,
                    )

            conn.commit()
            return {
                "deleted": deleted,
                "was_current": was_current,
                "promoted_version_id": promoted_id,
                "set_id": set_id,
                "document_id": document_id,
            }
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_set(set_id: int) -> bool:
        """Delete a document set and all versions under it. Cleans agent knowledge."""
        ComplianceEngine._cleanup_knowledge_for_set(set_id)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "DELETE FROM RetailerDocumentSets WHERE set_id = ?",
                set_id,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def create_document_set(
        retailer_id: int,
        category: str,
        description: str = "",
        agent_id: Optional[int] = None,
    ) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                INSERT INTO RetailerDocumentSets (retailer_id, category, description, agent_id)
                OUTPUT INSERTED.set_id
                VALUES (?, ?, ?, ?)
                """,
                retailer_id,
                category,
                description,
                agent_id,
            )
            set_id = cursor.fetchone()[0]
            conn.commit()
            return set_id
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def update_document_set(
        set_id: int,
        description: Optional[str] = None,
        agent_id: Optional[int] = None,
        clear_agent: bool = False,
        extraction_schema_id: Optional[int] = None,
        clear_extraction_schema: bool = False,
        extraction_workflow_id: Optional[int] = None,
        clear_extraction_workflow: bool = False,
    ) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            updates = []
            params = []
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if clear_agent:
                updates.append("agent_id = NULL")
            elif agent_id is not None:
                updates.append("agent_id = ?")
                params.append(agent_id)
            if clear_extraction_schema:
                updates.append("extraction_schema_id = NULL")
            elif extraction_schema_id is not None:
                updates.append("extraction_schema_id = ?")
                params.append(extraction_schema_id)
            if clear_extraction_workflow:
                updates.append("extraction_workflow_id = NULL")
            elif extraction_workflow_id is not None:
                updates.append("extraction_workflow_id = ?")
                params.append(extraction_workflow_id)
            if not updates:
                return False
            updates.append("updated_at = GETDATE()")
            params.append(set_id)
            cursor.execute(
                f"UPDATE RetailerDocumentSets SET {', '.join(updates)} WHERE set_id = ?",
                *params,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_document_sets(retailer_id: int) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT s.set_id, s.retailer_id, s.category, s.description, s.agent_id,
                       s.extraction_schema_id, s.extraction_workflow_id,
                       cs.name AS extraction_schema_name,
                       a.description AS agent_name,
                       s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM RetailerDocumentVersions WHERE set_id = s.set_id) AS version_count,
                       (SELECT MAX(uploaded_at) FROM RetailerDocumentVersions WHERE set_id = s.set_id) AS last_upload,
                       (SELECT version_number FROM RetailerDocumentVersions
                        WHERE set_id = s.set_id AND is_current = 1) AS current_version
                FROM RetailerDocumentSets s
                LEFT JOIN Agents a ON a.id = s.agent_id
                LEFT JOIN ComplianceSchemas cs ON cs.schema_id = s.extraction_schema_id
                WHERE s.retailer_id = ?
                ORDER BY s.category
                """,
                retailer_id,
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_document_set(set_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT set_id, retailer_id, category, description, agent_id,
                       extraction_schema_id, extraction_workflow_id
                FROM RetailerDocumentSets WHERE set_id = ?
                """,
                set_id,
            )
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, row))
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_versions(set_id: int) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT v.version_id, v.set_id, v.document_id, v.version_number,
                       v.is_current, v.change_summary, v.uploaded_by, v.uploaded_at,
                       d.filename, d.page_count
                FROM RetailerDocumentVersions v
                LEFT JOIN Documents d ON d.document_id = v.document_id
                WHERE v.set_id = ?
                ORDER BY v.version_number DESC
                """,
                set_id,
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_requirements(version_id: int) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT requirement_id, version_id, category, subcategory,
                       requirement_text, specific_value, severity, source_page, confidence
                FROM ExtractedRequirements
                WHERE version_id = ?
                ORDER BY category, subcategory
                """,
                version_id,
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_current_requirements_for_retailer(
        retailer_id: int, category_filter: str = None
    ) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            query = """
                SELECT er.requirement_id, er.version_id, er.category, er.subcategory,
                       er.requirement_text, er.specific_value, er.severity,
                       er.source_page, er.confidence,
                       s.category AS set_category
                FROM ExtractedRequirements er
                JOIN RetailerDocumentVersions v ON v.version_id = er.version_id
                JOIN RetailerDocumentSets s ON s.set_id = v.set_id
                WHERE s.retailer_id = ? AND v.is_current = 1
            """
            params = [retailer_id]
            if category_filter:
                query += " AND er.category = ?"
                params.append(category_filter)
            query += " ORDER BY er.category, er.subcategory"

            cursor.execute(query, *params)
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    # -- schemas CRUD --------------------------------------------------------

    @staticmethod
    def list_schemas() -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT s.schema_id, s.name, s.description, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM RetailerDocumentSets
                        WHERE extraction_schema_id = s.schema_id) AS used_by_count
                FROM ComplianceSchemas s
                ORDER BY s.name
                """
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_schema(schema_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT schema_id, name, description, fields, created_at, updated_at
                FROM ComplianceSchemas WHERE schema_id = ?
                """,
                schema_id,
            )
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]
            data = dict(zip(columns, row))
            if isinstance(data.get("fields"), str):
                try:
                    data["fields"] = json.loads(data["fields"])
                except (json.JSONDecodeError, TypeError):
                    data["fields"] = []
            return data
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def create_schema(
        name: str,
        fields: List[Dict],
        description: str = "",
        created_by: Optional[int] = None,
    ) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                INSERT INTO ComplianceSchemas (name, description, fields, created_by)
                OUTPUT INSERTED.schema_id
                VALUES (?, ?, ?, ?)
                """,
                name,
                description,
                json.dumps(fields or []),
                created_by,
            )
            schema_id = cursor.fetchone()[0]
            conn.commit()
            return schema_id
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def update_schema(
        schema_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[List[Dict]] = None,
    ) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            updates = []
            params = []
            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if fields is not None:
                updates.append("fields = ?")
                params.append(json.dumps(fields))
            if not updates:
                return False
            updates.append("updated_at = GETDATE()")
            params.append(schema_id)
            cursor.execute(
                f"UPDATE ComplianceSchemas SET {', '.join(updates)} WHERE schema_id = ?",
                *params,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_schema(schema_id: int) -> bool:
        """Delete a schema. Sets referencing it have their FK set to NULL."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                "UPDATE RetailerDocumentSets SET extraction_schema_id = NULL "
                "WHERE extraction_schema_id = ?",
                schema_id,
            )
            cursor.execute(
                "DELETE FROM ComplianceSchemas WHERE schema_id = ?",
                schema_id,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def seed_default_schema_if_empty(taxonomy: Dict) -> Optional[int]:
        """Seed a 'Default Retailer Compliance' schema from the YAML taxonomy
        if no schemas exist yet for this tenant. Returns the new schema_id, or
        None if schemas already exist."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute("SELECT COUNT(*) FROM ComplianceSchemas")
            if cursor.fetchone()[0] > 0:
                return None

            fields = []
            for cat_key, cat_val in taxonomy.get("categories", {}).items():
                for sub_key, sub_val in cat_val.get("subcategories", {}).items():
                    for fld in sub_val.get("fields", []):
                        fields.append({
                            "name": f"{cat_key}.{sub_key}.{fld['name']}",
                            "description": fld["description"],
                            "type": "text",
                            "required": False,
                        })

            cursor.execute(
                """
                INSERT INTO ComplianceSchemas (name, description, fields)
                OUTPUT INSERTED.schema_id
                VALUES (?, ?, ?)
                """,
                "Default Retailer Compliance",
                taxonomy.get("description", "Standard taxonomy seeded from retailer_compliance.yaml"),
                json.dumps(fields),
            )
            new_id = cursor.fetchone()[0]
            conn.commit()
            return new_id
        finally:
            cursor.close()
            conn.close()
