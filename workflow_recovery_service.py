"""
Workflow Recovery Service
Handles one-time recovery of workflows after application restart.
Runs ONCE at startup, then the regular WorkflowExecutionEngine handles everything.
"""

import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import pyodbc

logger = logging.getLogger("WorkflowRecovery")

# ---------------------------------------------------------------------------
# Approval-status vocabulary.
#
# These MUST match what the execution engine writes for in-process timeouts
# (workflow_execution.py, _execute_human_approval_node): 'Timeout-Approved' /
# 'Timeout-Rejected'. An earlier revision of this file wrote 'Timeout-Approve' /
# 'Timeout-Reject' (and even 'Timeout-Continue'), which nothing else in the
# platform recognises; APPROVED_STATUSES keeps the legacy spelling readable so
# rows written by that revision still resolve correctly.
# ---------------------------------------------------------------------------
STATUS_TIMEOUT_APPROVED = 'Timeout-Approved'
STATUS_TIMEOUT_REJECTED = 'Timeout-Rejected'
APPROVED_STATUSES = ('Approved', STATUS_TIMEOUT_APPROVED, 'Timeout-Approve')

# Engine + builder-UI default for a Human Approval node's ``timeoutAction``
# ('continue' = auto-approve on timeout; anything else, e.g. 'fail', = reject).
DEFAULT_TIMEOUT_ACTION = 'continue'
_APPROVE_ACTIONS = ('continue', 'approve', 'approved')


def parse_approval_data(raw: Any) -> Dict:
    """Best-effort parse of ``ApprovalRequests.approval_data`` into a dict.

    ``approval_data`` is free-form BY DESIGN ("data to show the approver"):
    the engine stores whatever the Human Approval node's ``approvalData``
    resolves to after ``${var}`` substitution -- LLM prose, pipe-delimited
    summaries, file paths, a bare marker like ``review-me`` -- and only yields
    JSON when the variable happened to be a dict/list.  On the live AIHUB DB
    (2026-08-21) 320 of 349 rows were not JSON.  Every other reader in the
    platform (app.py, agent_service, monitoring.js) already tolerates this;
    recovery used to be the lone strict ``json.loads`` and aborted the whole
    timeout batch on the first such row
    ("Expecting value: line 1 column 1 (char 0)").

    Returns ``{}`` for anything that is not a JSON *object*.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode('utf-8', errors='replace')
        except Exception:
            return {}
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def find_node_config(workflow_data: Any, node_id: Any) -> Optional[Dict]:
    """Locate ``config`` for ``node_id`` inside a stored workflow definition.

    ``workflow_data`` is the ``Workflows.workflow_data`` JSON (string or
    already-parsed dict) shaped ``{"nodes": [{"id": "node-16", "type": ...,
    "config": {...}}, ...], "connections": [...]}``.  Returns ``None`` when the
    definition is missing/unparseable or the node is not in it (the workflow
    may have been deleted or edited since the execution started).
    """
    if workflow_data is None or node_id is None:
        return None
    data = workflow_data
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode('utf-8', errors='replace')
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    nodes = data.get('nodes')
    if not isinstance(nodes, list) and isinstance(data.get('workflow'), dict):
        nodes = data['workflow'].get('nodes')
    if not isinstance(nodes, list):
        return None
    wanted = str(node_id)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get('id', node.get('node_id', node.get('nodeId')))
        if nid is not None and str(nid) == wanted:
            cfg = node.get('config')
            return cfg if isinstance(cfg, dict) else {}
    return None


def resolve_timeout_action(node_config: Optional[Dict], approval_data: Optional[Dict]) -> str:
    """Decide what a timed-out approval should do, mirroring the engine.

    Precedence:
      1. the node's own ``timeoutAction`` (authoritative -- this is the only
         thing the engine itself consults; default 'continue');
      2. if the definition could not be resolved at all, a legacy
         ``approval_data['timeout_action']`` hint (never written by the engine,
         but an earlier revision of this file looked for it);
      3. the engine default, 'continue'.
    """
    if node_config is not None:
        action = node_config.get('timeoutAction')
        return str(action).strip().lower() if action else DEFAULT_TIMEOUT_ACTION
    if isinstance(approval_data, dict):
        legacy = approval_data.get('timeout_action')
        if legacy:
            return str(legacy).strip().lower()
    return DEFAULT_TIMEOUT_ACTION


def timeout_status_for(action: str) -> str:
    """Map a timeoutAction to the engine's approval status vocabulary."""
    return STATUS_TIMEOUT_APPROVED if (action or '').lower() in _APPROVE_ACTIONS else STATUS_TIMEOUT_REJECTED


class WorkflowRecoveryService:
    """
    One-time recovery service for workflows interrupted by application restart.

    This service runs at startup to:
    1. Clean up stale executions that can't be recovered
    2. Process approval responses that came in while the app was down
    3. Handle any approvals that timed out during downtime

    After startup recovery, the regular WorkflowExecutionEngine handles all
    new executions and approval monitoring.
    """

    def __init__(self, workflow_executor, connection_string: str):
        """
        Initialize the recovery service.

        Args:
            workflow_executor: The WorkflowExecutionEngine instance
            connection_string: Database connection string
        """
        self.workflow_executor = workflow_executor
        self.connection_string = connection_string
        self._api_key = os.getenv('API_KEY')

    def run_recovery(self) -> Dict:
        """
        Run the one-time startup recovery process.

        Returns:
            Dict with recovery statistics
        """
        if not self._api_key:
            logger.error("API_KEY not set - skipping workflow recovery")
            return {'error': 'API_KEY not set', 'recovered': 0, 'failed': 0}

        logger.info("=" * 60)
        logger.info("Starting one-time workflow recovery...")
        logger.info("=" * 60)

        stats = {
            'stale_cleaned': 0,
            'approvals_processed': 0,
            'timeouts_handled': 0,
            'errors': []
        }

        try:
            # Step 1: Handle approval timeouts first
            stats['timeouts_handled'] = self._process_approval_timeouts()

            # Step 2: Process approval responses that came in during downtime
            stats['approvals_processed'] = self._process_pending_approval_responses()

            # Step 3: Clean up truly stale executions (no pending approvals)
            stats['stale_cleaned'] = self._cleanup_stale_executions()

            logger.info("=" * 60)
            logger.info(f"Recovery complete: {stats['timeouts_handled']} timeouts, "
                       f"{stats['approvals_processed']} approvals, "
                       f"{stats['stale_cleaned']} stale cleaned")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Error during recovery: {str(e)}")
            stats['errors'].append(str(e))

        return stats

    def _get_db_connection(self):
        """Create database connection"""
        return pyodbc.connect(self.connection_string)

    def _process_approval_timeouts(self) -> int:
        """
        Process any approvals that timed out while the app was down.

        Each timed-out approval is resolved the way the engine would have
        resolved it in-process: the node's ``timeoutAction`` ('continue' =>
        'Timeout-Approved', anything else => 'Timeout-Rejected').  Rows are
        processed and committed independently so one malformed row (free-text
        ``approval_data``, a deleted workflow, a transient DB error) can never
        abort the batch -- that failure mode used to leave every overdue
        approval Pending and its execution Paused forever.

        Returns:
            Number of timeouts processed
        """
        count = 0
        skipped = 0
        conn = None
        cursor = None
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self._api_key)

            # Find approvals that have timed out but are still pending.
            # The stored definition is joined in so the node's real
            # timeoutAction can be honoured (the workflow may since have been
            # edited or deleted -- LEFT JOIN, resolved best-effort below).
            cursor.execute("""
                SELECT
                    ar.request_id,
                    ar.approval_data,
                    se.execution_id,
                    se.step_execution_id,
                    se.node_id,
                    we.workflow_id,
                    w.workflow_data
                FROM ApprovalRequests ar
                JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
                JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
                LEFT JOIN Workflows w ON w.id = we.workflow_id
                WHERE ar.status = 'Pending'
                    AND ar.due_date IS NOT NULL
                    AND ar.due_date < GETUTCDATE()
                    AND we.status IN ('Running', 'Paused')
            """)

            timeouts = cursor.fetchall()
            definitions: Dict[Any, Any] = {}   # workflow_id -> parsed definition (or None)

            for row in timeouts:
                request_id = row[0]
                try:
                    approval_data = parse_approval_data(row[1])
                    execution_id = row[2]
                    step_execution_id = row[3]
                    node_id = row[4]
                    workflow_id = row[5] if len(row) > 5 else None
                    workflow_data = row[6] if len(row) > 6 else None

                    node_config = self._resolve_node_config(
                        definitions, workflow_id, workflow_data, node_id)
                    timeout_action = resolve_timeout_action(node_config, approval_data)
                    timeout_status = timeout_status_for(timeout_action)
                    approved = timeout_status == STATUS_TIMEOUT_APPROVED
                    source = 'node config' if node_config is not None else 'default (definition not resolvable)'

                    logger.info(
                        f"Processing timeout for approval {request_id} "
                        f"(execution {execution_id}, node {node_id}): "
                        f"timeoutAction={timeout_action!r} [{source}] -> {timeout_status}")

                    comments = (
                        f"Auto-{'approved' if approved else 'rejected'} by startup recovery: "
                        f"approval timed out while the executor was down "
                        f"(timeoutAction={timeout_action})")

                    # Update the approval request
                    cursor.execute("""
                        UPDATE ApprovalRequests
                        SET status = ?,
                            response_at = GETUTCDATE(),
                            responded_by = 'System-Recovery',
                            comments = ?
                        WHERE request_id = ?
                    """, timeout_status, comments, request_id)
                    conn.commit()
                    count += 1
                except Exception as row_err:
                    skipped += 1
                    logger.error(
                        f"Skipping approval {request_id} during timeout recovery: {row_err}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass

            if count > 0 or skipped > 0:
                logger.info(f"Processed {count} approval timeouts"
                            + (f" ({skipped} skipped)" if skipped else ""))

        except Exception as e:
            logger.error(f"Error processing approval timeouts: {str(e)}")
        finally:
            for closable in (cursor, conn):
                try:
                    if closable is not None:
                        closable.close()
                except Exception:
                    pass

        return count

    @staticmethod
    def _resolve_node_config(cache: Dict, workflow_id: Any, workflow_data: Any,
                             node_id: Any) -> Optional[Dict]:
        """Find the node's config in the stored definition (memoised per workflow_id)."""
        key = workflow_id if workflow_id is not None else id(workflow_data)
        if key not in cache:
            parsed = None
            if workflow_data is not None:
                try:
                    parsed = json.loads(workflow_data) if isinstance(workflow_data, (str, bytes, bytearray)) else workflow_data
                except (ValueError, TypeError):
                    logger.warning(f"Workflow {workflow_id}: stored definition is not valid JSON; "
                                   f"using default timeoutAction for its approvals")
                    parsed = None
            cache[key] = parsed
        cfg = find_node_config(cache[key], node_id)
        if cfg is None:
            logger.warning(f"Workflow {workflow_id}: node {node_id} not resolvable from the stored "
                           f"definition (deleted/edited?); using default timeoutAction "
                           f"'{DEFAULT_TIMEOUT_ACTION}'")
        return cfg

    def _process_pending_approval_responses(self) -> int:
        """
        Process approval responses that came in while the app was down.
        Updates workflow status based on approval results.

        Returns:
            Number of approvals processed
        """
        count = 0
        conn = None
        cursor = None
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self._api_key)

            # Find approvals that have been responded to but workflow is still paused
            cursor.execute("""
                SELECT
                    ar.request_id,
                    ar.status,
                    ar.responded_by,
                    ar.comments,
                    se.execution_id,
                    se.step_execution_id,
                    se.node_id,
                    we.workflow_name
                FROM ApprovalRequests ar
                JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
                JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
                WHERE ar.status NOT IN ('Pending', 'Cancelled')
                    AND se.status = 'Paused'
                    AND we.status IN ('Running', 'Paused')
            """)

            responses = cursor.fetchall()

            for row in responses:
                request_id = row[0]
                approval_status = row[1]
                responded_by = row[2]
                comments = row[3]
                execution_id = row[4]
                step_execution_id = row[5]
                node_id = row[6]
                workflow_name = row[7]

                logger.info(f"Processing approval response for workflow '{workflow_name}': {approval_status}")

                # Determine step status based on approval (engine vocabulary +
                # the legacy 'Timeout-Approve' spelling this file once wrote)
                is_approved = approval_status in APPROVED_STATUSES
                is_timeout = str(approval_status or '').startswith('Timeout-')
                step_status = 'Completed' if is_approved else 'Failed'

                # Update step execution
                cursor.execute("""
                    UPDATE StepExecutions
                    SET status = ?,
                        completed_at = GETUTCDATE()
                    WHERE step_execution_id = ?
                """, step_status, step_execution_id)

                # For recovery, we mark the workflow based on the approval result
                # (Full continuation would require re-establishing execution context)
                if is_approved:
                    # Mark workflow as completed (simplified recovery)
                    cursor.execute("""
                        UPDATE WorkflowExecutions
                        SET status = 'Completed',
                            completed_at = GETUTCDATE()
                        WHERE execution_id = ?
                    """, execution_id)
                    logger.info(f"  -> Workflow marked Completed")
                else:
                    # Mark workflow as failed (store error in execution_data as JSON)
                    if is_timeout:
                        error_text = (f"Approval timed out while the executor was down and was "
                                      f"auto-rejected per the node's timeoutAction "
                                      f"({approval_status}): {comments or 'No details'}")
                    else:
                        error_text = f"Approval rejected by {responded_by}: {comments or 'No reason given'}"
                    error_data = json.dumps({
                        'error': error_text,
                        'recovery_time': datetime.utcnow().isoformat()
                    })
                    cursor.execute("""
                        UPDATE WorkflowExecutions
                        SET status = 'Failed',
                            completed_at = GETUTCDATE(),
                            execution_data = ?
                        WHERE execution_id = ?
                    """, error_data, execution_id)
                    logger.info(f"  -> Workflow marked Failed")

                # Log the recovery action (be explicit that recovery does not
                # resume downstream nodes -- the execution context is gone)
                message = (f"Workflow recovered after restart with approval status: {approval_status}"
                           + (" (timed out during downtime)" if is_timeout else "")
                           + ("; downstream steps were NOT resumed by recovery" if is_approved else ""))
                cursor.execute("""
                    INSERT INTO ExecutionLogs (
                        execution_id, timestamp, log_level, message, details
                    ) VALUES (?, GETUTCDATE(), 'info', ?, ?)
                """, execution_id,
                     message,
                     json.dumps({
                         'request_id': str(request_id),
                         'responded_by': responded_by,
                         'approval_status': approval_status,
                         'downstream_resumed': False,
                         'recovery_time': datetime.utcnow().isoformat()
                     }))

                count += 1

            conn.commit()

            if count > 0:
                logger.info(f"Processed {count} approval responses")

        except Exception as e:
            logger.error(f"Error processing approval responses: {str(e)}")
        finally:
            for closable in (cursor, conn):
                try:
                    if closable is not None:
                        closable.close()
                except Exception:
                    pass

        return count

    def _cleanup_stale_executions(self) -> int:
        """
        Clean up executions that are stuck without recoverable state.

        Returns:
            Number of stale executions cleaned up
        """
        count = 0
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self._api_key)

            # Find executions still marked as Running/Paused with no pending approvals
            cursor.execute("""
                SELECT
                    we.execution_id,
                    we.workflow_name,
                    we.started_at,
                    DATEDIFF(MINUTE, we.started_at, GETUTCDATE()) as minutes_old
                FROM WorkflowExecutions we
                WHERE we.status IN ('Running', 'Paused')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM StepExecutions se
                        JOIN ApprovalRequests ar ON se.step_execution_id = ar.step_execution_id
                        WHERE se.execution_id = we.execution_id
                            AND ar.status = 'Pending'
                    )
            """)

            stale = cursor.fetchall()

            for row in stale:
                execution_id = row[0]
                workflow_name = row[1]
                started_at = row[2]
                minutes_old = row[3]

                logger.warning(f"Marking stale workflow '{workflow_name}' ({execution_id}) as Failed "
                             f"- was running for {minutes_old} minutes")

                # Mark as failed (store error in execution_data as JSON)
                error_data = json.dumps({
                    'error': 'Workflow interrupted by application restart - no recoverable state',
                    'minutes_running': minutes_old,
                    'recovery_time': datetime.utcnow().isoformat()
                })
                cursor.execute("""
                    UPDATE WorkflowExecutions
                    SET status = 'Failed',
                        completed_at = GETUTCDATE(),
                        execution_data = ?
                    WHERE execution_id = ?
                """, error_data, execution_id)

                # Update any running/paused steps
                cursor.execute("""
                    UPDATE StepExecutions
                    SET status = 'Failed',
                        completed_at = GETUTCDATE()
                    WHERE execution_id = ?
                        AND status IN ('Running', 'Paused')
                """, execution_id)

                # Log the cleanup
                cursor.execute("""
                    INSERT INTO ExecutionLogs (
                        execution_id, timestamp, log_level, message, details
                    ) VALUES (?, GETUTCDATE(), 'warning', ?, ?)
                """, execution_id,
                     "Workflow marked as failed during startup recovery - unrecoverable state",
                     json.dumps({'minutes_running': minutes_old}))

                count += 1

            conn.commit()
            cursor.close()
            conn.close()

            if count > 0:
                logger.info(f"Cleaned up {count} stale executions")

        except Exception as e:
            logger.error(f"Error cleaning up stale executions: {str(e)}")

        return count


def initialize_recovery_service(app, workflow_executor):
    """
    Initialize and run the recovery service at startup.

    This runs ONCE and does not start any background threads.

    Args:
        app: Flask application instance
        workflow_executor: WorkflowExecutionEngine instance
    """
    try:
        # Get connection string from the workflow executor
        connection_string = workflow_executor.connection_string

        # Create and run recovery
        recovery_service = WorkflowRecoveryService(workflow_executor, connection_string)
        stats = recovery_service.run_recovery()

        # Store stats in app config for debugging if needed
        app.config['WORKFLOW_RECOVERY_STATS'] = stats

        return recovery_service

    except Exception as e:
        logger.error(f"Failed to initialize recovery service: {str(e)}")
        return None
