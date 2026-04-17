import os
import sys
import time
import json
import logging
import argparse
import pyodbc
import traceback
from datetime import datetime, timedelta
import importlib
import threading
import uuid
import signal
import subprocess
import requests
from typing import Dict, List, Any, Optional, Union, Tuple
import decimal

# Import APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from CommonUtils import get_db_connection_string, get_base_url, safe_log_string, rotate_logs_on_startup, get_log_path
from config import JOB_SCHEDULER_TIMEZONE

rotate_logs_on_startup(log_file=os.getenv('JOB_SCHEDULER_SERVICE_LOG', get_log_path('job_scheduler_service_log.txt')))

# Configure logging — reconfigure stdout for UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.getenv('JOB_SCHEDULER_SERVICE_LOG', get_log_path('job_scheduler_service_log.txt')), encoding='utf-8')
    ]
)
logger = logging.getLogger("JobSchedulerService")


# Get DB connection string from environment or config
DEFAULT_CONNECTION_STRING = get_db_connection_string()
DEFAULT_API_BASE_URL = get_base_url()

class JobSchedulerService:
    """
    Service that manages the scheduling and execution of jobs.
    This includes document processor jobs, agent jobs, and workflow jobs.
    """
    
    def __init__(
        self,
        db_connection_string: str,
        api_base_url: str,
        tenant_id: Optional[str] = None,
        poll_interval: int = 60,
        thread_pool_size: int = 20,
        process_pool_size: int = 5
    ):
        """
        Initialize the job scheduler service.
        
        Args:
            db_connection_string: SQL Server connection string
            api_base_url: Base URL for the application API
            tenant_id: Optional tenant ID for multi-tenant environments
            poll_interval: How often to check for new/modified jobs in seconds
            thread_pool_size: Size of the thread pool for job execution
            process_pool_size: Size of the process pool for job execution
        """
        self.db_connection_string = db_connection_string
        self.api_base_url = api_base_url.rstrip('/')
        self.tenant_id = tenant_id
        self.poll_interval = poll_interval
        self.thread_pool_size = thread_pool_size
        self.process_pool_size = process_pool_size
        
        self.db_conn = None
        self.is_running = False
        self.scheduler = None
        self.job_types = {
            'document': self._execute_document_job,
            'agent': self._execute_agent_job,
            'workflow': self._execute_workflow_job
        }

        print('API Base URL:', self.api_base_url)
        
        # Connect to database
        self._connect_db()
        
        # Initialize the scheduler
        self._init_scheduler()
        
    def _connect_db(self):
        """Connect to the SQL Server database"""
        try:
            self.db_conn = pyodbc.connect(self.db_connection_string)
            logger.info("Connected to SQL Server database")
        except Exception as e:
            logger.error(f"Failed to connect to SQL database: {str(e)}")
            raise
            
    def _init_scheduler(self):
        """Initialize the APScheduler"""
        try:
            # Configure the scheduler
            jobstores = {
                #'default': SQLAlchemyJobStore(url=f'sqlite:///scheduler_jobs.sqlite')
                'default': MemoryJobStore()
            }
            
            executors = {
                'default': ThreadPoolExecutor(self.thread_pool_size),
                'processpool': ProcessPoolExecutor(self.process_pool_size)
            }
            
            job_defaults = {
                'coalesce': True,
                'max_instances': 3,
                'misfire_grace_time': 60
            }
            
            # Create the scheduler
            self.scheduler = BackgroundScheduler(
                jobstores=jobstores,
                executors=executors,
                job_defaults=job_defaults
            )
            
            logger.info("APScheduler initialized")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {str(e)}")
            raise
    
    def start(self):
        """Start the scheduler service"""
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return
            
        self.is_running = True
        logger.info("Starting job scheduler service")
        
        try:
            # Start the APScheduler
            self.scheduler.start()
            logger.info("APScheduler started")
            
            # Add a recurring job to check for new/updated schedules in the database
            self.scheduler.add_job(
                self._update_schedules_from_db,
                'interval',
                seconds=self.poll_interval,
                id='update_schedules',
                replace_existing=True
            )
            
            # Wait for termination signal
            while self.is_running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
        except Exception as e:
            logger.error(f"Error in scheduler service: {str(e)}")
            logger.error(traceback.format_exc())
        finally:
            self.stop()
    
    def stop(self):
        """Stop the scheduler service"""
        logger.info("Stopping job scheduler service")
        self.is_running = False
        
        # Shutdown the scheduler
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler stopped")
            
        # Close database connection
        if self.db_conn:
            self.db_conn.close()
            logger.info("Database connection closed")
            
        logger.info("Job scheduler service stopped")
    
    def _is_connection_alive(self) -> bool:
        """Check if the database connection is still alive"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except:
            return False

    def _update_schedules_from_db(self):
        """
        Poll the database for new or updated schedules and update the scheduler accordingly.
        This runs periodically to keep the scheduler in sync with the database.
        """
        try:
            # Check if DB connection is alive
            if not self.db_conn or not self._is_connection_alive():
                logger.info("Database connection lost, reconnecting...")
                self._connect_db()
                
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get all active scheduled jobs and their definitions
            query = """
            SELECT j.ScheduledJobId, j.JobName, j.JobType, j.TargetId, j.Description,
                   s.ScheduleId, s.ScheduleType, 
                   s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
                   s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.MaxRuns, s.CurrentRuns, s.IsActive
            FROM ScheduledJobs j
            JOIN ScheduleDefinitions s ON j.ScheduledJobId = s.ScheduledJobId
            WHERE j.IsActive = 1 AND s.IsActive = 1
            """
            
            cursor.execute(query)
            
            # Process each schedule
            for row in cursor.fetchall():
                job_id = row[0]
                job_name = row[1]
                job_type = row[2]
                target_id = row[3]
                description = row[4]
                schedule_id = row[5]
                schedule_type = row[6]
                interval_seconds = row[7]
                interval_minutes = row[8]
                interval_hours = row[9]
                interval_days = row[10]
                interval_weeks = row[11]
                cron_expression = row[12]
                start_date = row[13]
                end_date = row[14]
                next_run_time = row[15]
                max_runs = row[16]
                current_runs = row[17]
                is_active = row[18]
                
                # Create a unique ID for this schedule in APScheduler
                apscheduler_job_id = f"{job_type}_{job_id}_{schedule_id}"
                
                # Check if this job already exists in the scheduler
                existing_job = self.scheduler.get_job(apscheduler_job_id)
                
                # If the job exists and is not active, remove it
                if existing_job and not is_active:
                    self.scheduler.remove_job(apscheduler_job_id)
                    logger.info(f"Removed inactive job: {apscheduler_job_id}")
                    continue
                
                # If the job has reached its maximum runs, mark it as inactive
                if max_runs is not None and current_runs >= max_runs:
                    self._update_schedule_status(schedule_id, is_active=False)
                    if existing_job:
                        self.scheduler.remove_job(apscheduler_job_id)
                    logger.info(f"Job {apscheduler_job_id} has reached maximum runs ({max_runs}), marked as inactive")
                    continue

                # For one-time jobs, check if there's already a pending execution
                if schedule_type == 'date':
                    cursor.execute("""
                    SELECT COUNT(*) 
                    FROM ScheduleExecutionHistory 
                    WHERE ScheduleId = ? 
                    AND Status = 'pending'
                    """, schedule_id)
                    pending_count = cursor.fetchone()[0]
                    if pending_count > 0:
                        logger.info(f"One-time job {apscheduler_job_id} already has a pending execution, skipping")
                        continue
                
                # Skip if job type is not supported
                if job_type not in self.job_types:
                    logger.warning(f"Unsupported job type: {job_type}")
                    continue
                
                # Create or update the job in the scheduler
                trigger = self._create_trigger(
                    schedule_type, 
                    interval_seconds, interval_minutes, interval_hours, interval_days, interval_weeks,
                    cron_expression, start_date, end_date
                )
                
                if trigger:
                    job_func = self.job_types[job_type]
                    
                    # Get job parameters
                    params = self._get_job_parameters(job_id)
                    
                    # Job data to pass to the executor
                    job_data = {
                        'scheduled_job_id': job_id,
                        'schedule_id': schedule_id,
                        'job_name': job_name,
                        'job_type': job_type,
                        'target_id': target_id,
                        'description': description,
                        'parameters': params
                    }
                    
                    # Add or update the job in the scheduler
                    if existing_job:
                        # Update existing job
                        self.scheduler.reschedule_job(
                            apscheduler_job_id,
                            trigger=trigger
                        )
                        self.scheduler.modify_job(
                            apscheduler_job_id,
                            args=[job_data]
                        )
                        logger.info(f"Updated job in scheduler: {apscheduler_job_id}")
                    else:
                        # Add new job
                        self.scheduler.add_job(
                            job_func,
                            trigger=trigger,
                            id=apscheduler_job_id,
                            args=[job_data],
                            replace_existing=True
                        )
                        logger.info(f"Added new job to scheduler: {apscheduler_job_id}")

            # Get deactivated schedules to remove
            inactive_query = """
            SELECT j.JobType, j.ScheduledJobId, s.ScheduleId
            FROM ScheduledJobs j
            JOIN ScheduleDefinitions s ON j.ScheduledJobId = s.ScheduledJobId
            WHERE s.IsActive = 0 
            """
            
            cursor.execute(inactive_query)
            
            # Remove inactive schedules from APScheduler
            for row in cursor.fetchall():
                job_type = row[0]
                job_id = row[1]
                schedule_id = row[2]
                
                apscheduler_job_id = f"{job_type}_{job_id}_{schedule_id}"
                
                # Check if this job exists in the scheduler and remove it
                existing_job = self.scheduler.get_job(apscheduler_job_id)
                if existing_job:
                    self.scheduler.remove_job(apscheduler_job_id)
                    logger.info(f"Removed inactive schedule from scheduler: {apscheduler_job_id}")
            
            # ── Orphan cleanup: remove APScheduler jobs with no matching DB record ──
            # This handles the case where a schedule was deleted from the DB
            # but the APScheduler job was still running in memory.
            try:
                # Get all active schedule IDs from the DB (pattern: {type}_{jobid}_{scheduleid})
                cursor2 = self.db_conn.cursor()
                if self.tenant_id:
                    cursor2.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
                cursor2.execute("""
                    SELECT j.JobType, j.ScheduledJobId, s.ScheduleId
                    FROM ScheduledJobs j
                    JOIN ScheduleDefinitions s ON j.ScheduledJobId = s.ScheduledJobId
                """)
                valid_job_ids = set()
                for row in cursor2.fetchall():
                    valid_job_ids.add(f"{row[0]}_{row[1]}_{row[2]}")
                cursor2.close()
                
                # Check all APScheduler jobs against valid DB records
                all_scheduler_jobs = self.scheduler.get_jobs()
                for job in all_scheduler_jobs:
                    # Skip the internal sync job itself
                    if job.id == 'update_schedules':
                        continue
                    # If this job ID doesn't match any DB record, it's orphaned
                    if job.id not in valid_job_ids:
                        self.scheduler.remove_job(job.id)
                        logger.info(f"Removed orphaned APScheduler job (no DB record): {job.id}")
            except Exception as orphan_err:
                logger.warning(f"Error during orphan cleanup: {orphan_err}")
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error updating schedules from database: {str(e)}")
            logger.error(traceback.format_exc())
    
    def _create_trigger(
        self, 
        schedule_type: str,
        interval_seconds: Optional[int] = None,
        interval_minutes: Optional[int] = None,
        interval_hours: Optional[int] = None,
        interval_days: Optional[int] = None,
        interval_weeks: Optional[int] = None,
        cron_expression: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ):
        """
        Create an APScheduler trigger based on schedule parameters.
        
        Args:
            schedule_type: Type of schedule ('interval', 'cron', 'date')
            interval_*: Interval parameters for 'interval' schedules
            cron_expression: Cron expression for 'cron' schedules
            start_date: Start date for all schedules
            end_date: End date for all schedules
            
        Returns:
            APScheduler trigger object
        """
        try:
            # Log UTC times for debugging
            if start_date:
                logger.info(f"Creating trigger with UTC start_date: {start_date}")
            if end_date:
                logger.info(f"Creating trigger with UTC end_date: {end_date}")
                
            if schedule_type == 'interval':
                # Create interval trigger
                interval_kwargs = {}
                
                if interval_seconds:
                    interval_kwargs['seconds'] = interval_seconds
                if interval_minutes:
                    interval_kwargs['minutes'] = interval_minutes
                if interval_hours:
                    interval_kwargs['hours'] = interval_hours
                if interval_days:
                    interval_kwargs['days'] = interval_days
                if interval_weeks:
                    interval_kwargs['weeks'] = interval_weeks
                
                if not interval_kwargs:
                    logger.warning(f"No interval specified for interval schedule")
                    return None
                
                trigger = IntervalTrigger(
                    **interval_kwargs,
                    start_date=start_date,
                    end_date=end_date,
                    timezone=JOB_SCHEDULER_TIMEZONE
                )
                
            elif schedule_type == 'cron':
                # Create cron trigger
                if not cron_expression:
                    logger.warning(f"No cron expression specified for cron schedule")
                    return None
                
                trigger = CronTrigger.from_crontab(
                    cron_expression,
                    timezone=JOB_SCHEDULER_TIMEZONE
                )
                if start_date:
                    trigger.start_date = start_date
                if end_date:
                    trigger.end_date = end_date
                
            elif schedule_type == 'date':
                # Create date trigger (one-time execution)
                if not start_date:
                    logger.warning(f"No start date specified for date schedule")
                    return None
                
                trigger = DateTrigger(
                    run_date=start_date,
                    timezone=JOB_SCHEDULER_TIMEZONE
                )
                
            else:
                logger.warning(f"Unsupported schedule type: {schedule_type}")
                return None
            
            return trigger
            
        except Exception as e:
            logger.error(f"Error creating trigger: {str(e)}")
            return None
    
    def _get_job_parameters(self, job_id: int) -> Dict[str, Any]:
        """
        Get parameters for a scheduled job.
        
        Args:
            job_id: ID of the scheduled job
            
        Returns:
            Dictionary of parameter name-value pairs
        """
        params = {}
        
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get parameters for this job
            query = """
            SELECT ParameterName, ParameterValue, ParameterType
            FROM ScheduledJobParameters
            WHERE ScheduledJobId = ?
            """
            
            cursor.execute(query, job_id)
            
            for row in cursor.fetchall():
                param_name = row[0]
                param_value = row[1]
                param_type = row[2]
                
                # Convert parameter value based on type
                if param_type == 'int':
                    param_value = int(param_value) if param_value else None
                elif param_type == 'float':
                    param_value = float(param_value) if param_value else None
                elif param_type == 'bool':
                    param_value = param_value.lower() in ('true', '1', 'yes') if param_value else False
                elif param_type == 'json':
                    param_value = json.loads(param_value) if param_value else None
                
                params[param_name] = param_value
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error getting job parameters: {str(e)}")
        
        return params
    
    def _update_schedule_status(self, schedule_id: int, is_active: bool = True):
        """
        Update the status of a schedule in the database.
        
        Args:
            schedule_id: ID of the schedule
            is_active: Whether the schedule is active
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Update schedule status
            query = """
            UPDATE ScheduleDefinitions
            SET IsActive = ?
            WHERE ScheduleId = ?
            """
            
            cursor.execute(query, is_active, schedule_id)
            self.db_conn.commit()
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error updating schedule status: {str(e)}")
    
    def _update_next_run_time(self, schedule_id: int, next_run_time: Optional[datetime] = None):
        """
        Update the next run time of a schedule.
        
        Args:
            schedule_id: ID of the schedule
            next_run_time: Next scheduled run time (None if no future runs)
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Update next run time
            query = """
            UPDATE ScheduleDefinitions
            SET NextRunTime = ?
            WHERE ScheduleId = ?
            """
            
            cursor.execute(query, next_run_time, schedule_id)
            self.db_conn.commit()
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error updating next run time: {str(e)}")
    
    def _increment_run_count(self, schedule_id: int):
        """
        Increment the run count for a schedule.
        
        Args:
            schedule_id: ID of the schedule
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Increment run count
            query = """
            UPDATE ScheduleDefinitions
            SET CurrentRuns = CurrentRuns + 1
            WHERE ScheduleId = ?
            """
            
            cursor.execute(query, schedule_id)
            self.db_conn.commit()
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error incrementing run count: {str(e)}")
    
    def _update_last_run_time(self, schedule_id: int, last_run_time: datetime):
        """
        Update the last run time of a schedule.
        
        Args:
            schedule_id: ID of the schedule
            last_run_time: Last run time
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Update last run time
            query = """
            UPDATE ScheduleDefinitions
            SET LastRunTime = ?
            WHERE ScheduleId = ?
            """
            
            cursor.execute(query, last_run_time, schedule_id)
            self.db_conn.commit()
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error updating last run time: {str(e)}")
    
    def _create_execution_record(self, scheduled_job_id: int, schedule_id: int) -> int:
        """
        Create a record in the execution history table.
        
        Args:
            scheduled_job_id: ID of the scheduled job
            schedule_id: ID of the schedule
            
        Returns:
            ID of the created execution record
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Create execution record
            query = """
            INSERT INTO ScheduleExecutionHistory (
                ScheduleId, ScheduledJobId, StartTime, Status
            )
            VALUES (?, ?, getutcdate(), 'pending')
            """
            
            cursor.execute(query, schedule_id, scheduled_job_id)
            self.db_conn.commit()
            
            # Get the ID of the created record
            cursor.execute("SELECT @@IDENTITY")
            execution_id = cursor.fetchone()[0]
            
            cursor.close()
            
            return execution_id
            
        except Exception as e:
            logger.error(f"Error creating execution record: {str(e)}")
            return None
    
    def _update_execution_record(
        self, 
        execution_id: int, 
        status: str,
        result_message: Optional[str] = None,
        error_details: Optional[str] = None
    ):
        """
        Update an execution record with results.
        
        Args:
            execution_id: ID of the execution record
            status: Status of the execution ('completed', 'failed', 'skipped')
            result_message: Message with execution results
            error_details: Details of any error that occurred
        """
        try:
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Update execution record
            query = """
            UPDATE ScheduleExecutionHistory
            SET Status = ?, EndTime = getutcdate(), ResultMessage = ?, ErrorDetails = ?
            WHERE ExecutionId = ?
            """
            
            cursor.execute(query, status, result_message, error_details, execution_id)
            self.db_conn.commit()
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"Error updating execution record: {str(e)}")
    
    def _convert_decimal_to_float(self, obj):
        """
        Recursively convert Decimal objects to float in a dictionary or list.
        
        Args:
            obj: Object to convert (dict, list, or other)
            
        Returns:
            Object with Decimal values converted to float
        """
        if isinstance(obj, dict):
            return {k: self._convert_decimal_to_float(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_decimal_to_float(v) for v in obj]
        elif isinstance(obj, decimal.Decimal):
            return float(obj)
        return obj

    def _execute_document_job(self, job_data: Dict[str, Any]):
        """
        Execute a document processor job.
        
        Args:
            job_data: Dictionary with job details
        """
        scheduled_job_id = job_data['scheduled_job_id']
        schedule_id = job_data['schedule_id']
        job_name = job_data['job_name']
        target_id = job_data['target_id']
        parameters = job_data.get('parameters', {})
        
        logger.info(f"Executing document job: {job_name} (ID: {scheduled_job_id}, Target: {target_id})")
        
        # Create execution record
        execution_id = self._create_execution_record(scheduled_job_id, schedule_id)
        
        try:
            # Update execution status
            self._update_execution_record(execution_id, 'running')
            
            # First, we need to get an authenticated session
            # Let's create a special API endpoint that doesn't require authentication
            print(f"=====>>>>> Calling execute_document_job: {job_name} (ID: {scheduled_job_id}, Target: {target_id})")
            api_url = f"{self.api_base_url}/api/scheduler/execute_document_job/{target_id}"
            
            # Make API call to run the job
            response = requests.post(api_url, json={
                'api_key': os.getenv('API_KEY', ''),  # Pass API key for authentication
                'scheduled_by': 'scheduler',
                'execution_id': int(execution_id)  # Pass the execution ID to prevent duplicate creation
            })
            
            # Check if the request was successful
            if response.status_code == 200:
                result_message = f"Document job execution triggered successfully."
                status = 'completed'
            else:
                result_message = f"Failed to trigger document job. Status code: {response.status_code}"
                status = 'failed'
            
            # Update execution record
            self._update_execution_record(
                execution_id, 
                status, 
                result_message=result_message
            )
            
            # Update schedule metadata
            self._increment_run_count(schedule_id)
            self._update_last_run_time(schedule_id, datetime.now())
            
            logger.info(f"Document job execution completed: {job_name} with status {status}")
            
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"Error executing document job {job_name}: {str(e)}")
            logger.error(error_details)
            
            # Update execution record
            self._update_execution_record(
                execution_id, 
                'failed', 
                result_message=f"Error executing document job: {str(e)}",
                error_details=error_details
            )

    def _get_quickjob_data(self, target_id: int):
        """
        Get QuickJob data from the database.
        """
        try:
            agent_id = None
            ai_system = None
            description = None

            # Check if DB connection is alive
            if not self.db_conn or not self._is_connection_alive():
                logger.info("Database connection lost, reconnecting...")
                self._connect_db()
                
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get quick job details
            cursor.execute("""
                SELECT agent_id, ai_system, description 
                FROM QuickJob 
                WHERE id = ?
            """, target_id)
            
            row = cursor.fetchone()
            if not row:
                raise Exception(f"Quick job {target_id} not found")
            
            agent_id = row[0]
            ai_system = row[1]
            description = row[2]
            
            cursor.close()

            return agent_id, ai_system, description
        except Exception as e:
            logger.error(f"Error getting quick job data: {str(e)}")
            return None, None, None

    def _format_string_for_insert(self, input_string):
        output_string = "'" + str(input_string).replace("'", "''") + "'"
        return output_string
        
    def _execute_sql_no_results(self, sql_query):
        try:
            #logging.debug('Function: _execute_sql_no_results')
            #logging.debug('SQL Statement: ' + str(sql_query))
            # Establish a connection to SQL Server
            conn = pyodbc.connect(get_db_connection_string())

            # Create a cursor object to interact with the database
            cursor = conn.cursor()

            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Execute the SQL query
            cursor.execute(sql_query)

            conn.commit()

            cursor.close()
            
            return True
        except Exception as e:
            print(f"Error: {str(e)}")
            return False

    def _log_agent_job(self, job_id, message):
        try:
            import data_config as dcfg

            # Clean string to remove special characters and emojis
            message = safe_log_string(message, 'replace')

            insert_sql = dcfg.SQL_INSERT_QUICK_JOB_LOG.replace('{job_id}', str(job_id)).replace('{message}', self._format_string_for_insert(message))
            
            logger.debug(86 * '-')
            logger.debug(insert_sql)
            logger.debug(86 * '-')

            return self._execute_sql_no_results(insert_sql)
        except Exception as e:
            logger.error(f"Error logging agent job: {str(e)}")
            return False
    
    def _execute_agent_job(self, job_data: Dict[str, Any]):
        """
        Execute an agent job.
        
        Args:
            job_data: Dictionary with job details
        """
        scheduled_job_id = job_data['scheduled_job_id']
        schedule_id = job_data['schedule_id']
        job_name = job_data['job_name']
        target_id = job_data['target_id']
        parameters = job_data.get('parameters', {})
        
        logger.info(f"Executing agent job: {job_name} (ID: {scheduled_job_id}, Target: {target_id})")
        
        # Create execution record
        execution_id = self._create_execution_record(scheduled_job_id, schedule_id)
        
        try:
            # Update execution status
            self._update_execution_record(execution_id, 'running')

            _ = self._log_agent_job(target_id, f'Executing agent job {job_name} (ID: {scheduled_job_id}, Target: {target_id})')

            # Get QuickJob data
            agent_id, ai_system, description = self._get_quickjob_data(target_id)

            _ = self._log_agent_job(target_id, f'Calling agent {description} (ID: {agent_id})')
            _ = self._log_agent_job(target_id, f'Prompt: {ai_system}')
            
            # Call the API to run the agent job
            api_url = f"{self.api_base_url}/chat/general_system"
            
            # Get prompt from parameters
            prompt = ai_system
            
            # Prepare payload for the API call
            payload = {
                'agent_id': agent_id,
                'prompt': prompt,
                'hist': '[]'  # Empty history for scheduled runs
            }
            
            # Make API call to run the agent job
            headers = {'X-API-Key': os.getenv('API_KEY', '')}
            response = requests.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            response_data = response.json()

            # Update execution record
            self._update_execution_record(
                execution_id,
                'completed', 
                result_message=f"Agent job completed successfully. Response: {response_data.get('response', '')[:500]}..."
            )
            
            # Update schedule metadata
            self._increment_run_count(schedule_id)
            self._update_last_run_time(schedule_id, datetime.now())

            _ = self._log_agent_job(target_id, f"Agent job completed successfully. Response: {response_data.get('response', '')[:3000]}...")
            
            logger.info(f"Agent job execution completed: {job_name}")
            
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"Error executing agent job {job_name}: {str(e)}")
            logger.error(error_details)
            
            # Update execution record
            self._update_execution_record(
                execution_id, 
                'failed', 
                result_message=f"Error executing agent job: {str(e)}",
                error_details=error_details
            )

            _ = self._log_agent_job(target_id, f'Error executing agent job: {str(e)}')
    
    def _execute_workflow_job(self, job_data: Dict[str, Any]):
        """
        Execute a workflow job.
        
        Args:
            job_data: Dictionary with job details
        """
        scheduled_job_id = job_data['scheduled_job_id']
        schedule_id = job_data['schedule_id']
        job_name = job_data['job_name']
        target_id = job_data['target_id']
        parameters = job_data.get('parameters', {})
        
        logger.info(f"Executing workflow job: {job_name} (ID: {scheduled_job_id}, Target: {target_id})")
        
        # Create execution record
        execution_id = self._create_execution_record(scheduled_job_id, schedule_id)
        
        try:
            # Update execution status
            self._update_execution_record(execution_id, 'running')
            
            # Call the API to run the workflow
            api_url = f"{self.api_base_url}/api/workflow/run"
            
            # Prepare payload for the API call
            payload = {
                'workflow_id': target_id,
                'initiator': 'scheduler'
            }
            
            # Add any additional parameters
            if parameters:
                payload['variables'] = parameters
            
            # Make API call to run the workflow
            headers = {'X-API-Key': os.getenv('API_KEY', '')}
            response = requests.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            response_data = response.json()

            # Update execution record
            self._update_execution_record(
                execution_id,
                'completed', 
                result_message=f"Workflow started successfully. Execution ID: {response_data.get('execution_id', 'Unknown')}"
            )
            
            # Update schedule metadata
            self._increment_run_count(schedule_id)
            self._update_last_run_time(schedule_id, datetime.now())
            
            logger.info(f"Workflow job execution completed: {job_name}")
            
        except Exception as e:
            error_details = traceback.format_exc()
            logger.error(f"Error executing workflow job {job_name}: {str(e)}")
            logger.error(error_details)
            
            # Update execution record
            self._update_execution_record(
                execution_id, 
                'failed', 
                result_message=f"Error executing workflow job: {str(e)}",
                error_details=error_details
            )