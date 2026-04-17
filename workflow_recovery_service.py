"""
Workflow Recovery Service
Handles one-time recovery of workflows after application restart.
Runs ONCE at startup, then the regular WorkflowExecutionEngine handles everything.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import pyodbc

logger = logging.getLogger("WorkflowRecovery")


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
        
        Returns:
            Number of timeouts processed
        """
        count = 0
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self._api_key)
            
            # Find approvals that have timed out but are still pending
            cursor.execute("""
                SELECT 
                    ar.request_id,
                    ar.approval_data,
                    se.execution_id,
                    se.step_execution_id,
                    se.node_id
                FROM ApprovalRequests ar
                JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
                JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
                WHERE ar.status = 'Pending'
                    AND ar.due_date IS NOT NULL
                    AND ar.due_date < GETUTCDATE()
                    AND we.status IN ('Running', 'Paused')
            """)
            
            timeouts = cursor.fetchall()
            
            for row in timeouts:
                request_id = row[0]
                approval_data = json.loads(row[1]) if row[1] else {}
                execution_id = row[2]
                step_execution_id = row[3]
                
                timeout_action = approval_data.get('timeout_action', 'reject')
                timeout_status = f'Timeout-{timeout_action.title()}'
                
                logger.info(f"Processing timeout for approval {request_id} -> {timeout_status}")
                
                # Update the approval request
                cursor.execute("""
                    UPDATE ApprovalRequests
                    SET status = ?,
                        response_at = GETUTCDATE(),
                        responded_by = 'System-Recovery',
                        comments = 'Automatically processed due to timeout during downtime'
                    WHERE request_id = ?
                """, timeout_status, request_id)
                
                count += 1
            
            conn.commit()
            cursor.close()
            conn.close()
            
            if count > 0:
                logger.info(f"Processed {count} approval timeouts")
                
        except Exception as e:
            logger.error(f"Error processing approval timeouts: {str(e)}")
        
        return count
    
    def _process_pending_approval_responses(self) -> int:
        """
        Process approval responses that came in while the app was down.
        Updates workflow status based on approval results.
        
        Returns:
            Number of approvals processed
        """
        count = 0
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
                
                # Determine step status based on approval
                is_approved = approval_status in ('Approved', 'Timeout-Approve')
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
                    error_data = json.dumps({
                        'error': f"Approval rejected by {responded_by}: {comments or 'No reason given'}",
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
                
                # Log the recovery action
                cursor.execute("""
                    INSERT INTO ExecutionLogs (
                        execution_id, timestamp, log_level, message, details
                    ) VALUES (?, GETUTCDATE(), 'info', ?, ?)
                """, execution_id,
                     f"Workflow recovered after restart with approval status: {approval_status}",
                     json.dumps({
                         'request_id': request_id, 
                         'responded_by': responded_by,
                         'recovery_time': datetime.utcnow().isoformat()
                     }))
                
                count += 1
            
            conn.commit()
            cursor.close()
            conn.close()
            
            if count > 0:
                logger.info(f"Processed {count} approval responses")
                
        except Exception as e:
            logger.error(f"Error processing approval responses: {str(e)}")
        
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
