# scheduler_routes.py
"""
Flask routes for the Job Scheduler API.
This module provides endpoints for managing scheduled jobs.
"""

import os
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
import pyodbc
from croniter import croniter
import json
import requests
from CommonUtils import get_db_connection
from role_decorators import api_key_or_session_required
import re


# Create blueprint
scheduler_bp = Blueprint('scheduler', __name__, url_prefix='/api/scheduler')

# Configure logging
logger = logging.getLogger(__name__)

# Helper function to get database connection
# def get_db_connection():
#     try:
#         # Get connection string from app config
#         connection_string = current_app.config.get('SQLALCHEMY_DATABASE_URI')
#         if not connection_string:
#             # Fallback to environment variable
#             connection_string = os.getenv('DB_CONNECTION_STRING')
        
#         # Create connection
#         conn = pyodbc.connect(connection_string)
#         return conn
#     except Exception as e:
#         logger.error(f"Error connecting to database: {str(e)}")
#         raise

# Helper function to set tenant context
def set_tenant_context(cursor, tenant_id=None):
    """Set tenant context for multi-tenant environments"""
    try:
        tenant_id = tenant_id or os.getenv('API_KEY')
        if tenant_id:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
    except Exception as e:
        logger.error(f"Error setting tenant context: {str(e)}")
        raise

# Helper function to validate cron expression
def is_valid_cron(cron_expression):
    """Validate a cron expression"""
    try:
        # Check if croniter can parse it
        croniter(cron_expression)
        return True
    except Exception:
        return False

#######################
# API Routes
#######################

@scheduler_bp.route('/jobs', methods=['GET'])
def get_jobs():
    """Get all scheduled jobs"""
    try:
        print('Getting scheduled jobs...')
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Query jobs
        query = """
        SELECT j.ScheduledJobId, j.JobName, j.JobType, j.TargetId, j.Description,
               j.CreatedBy, j.CreatedAt, j.ModifiedBy, j.ModifiedAt, j.IsActive
        FROM ScheduledJobs j
        ORDER BY j.CreatedAt DESC
        """
        
        cursor.execute(query)
        
        # Process results
        jobs = []
        for row in cursor.fetchall():
            job = {
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'target_id': row[3],
                'description': row[4],
                'created_by': row[5],
                'created_at': row[6].isoformat() if row[6] else None,
                'modified_by': row[7],
                'modified_at': row[8].isoformat() if row[8] else None,
                'is_active': bool(row[9])
            }
            jobs.append(job)
        
        cursor.close()
        conn.close()
        print('Jobs:', jobs)
        
        return jsonify(jobs)
        
    except Exception as e:
        logger.error(f"Error getting jobs: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>', methods=['GET'])
def get_job(job_id):
    """Get details of a specific job"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Query job
        query = """
        SELECT j.ScheduledJobId, j.JobName, j.JobType, j.TargetId, j.Description,
               j.CreatedBy, j.CreatedAt, j.ModifiedBy, j.ModifiedAt, j.IsActive
        FROM ScheduledJobs j
        WHERE j.ScheduledJobId = ?
        """
        
        cursor.execute(query, job_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Job not found'}), 404
        
        job = {
            'id': row[0],
            'name': row[1],
            'type': row[2],
            'target_id': row[3],
            'description': row[4],
            'created_by': row[5],
            'created_at': row[6].isoformat() if row[6] else None,
            'modified_by': row[7],
            'modified_at': row[8].isoformat() if row[8] else None,
            'is_active': bool(row[9])
        }
        
        # Get job schedules
        schedules_query = """
        SELECT s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive
        FROM ScheduleDefinitions s
        WHERE s.ScheduledJobId = ?
        """
        
        cursor.execute(schedules_query, job_id)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = {
                'id': row[0],
                'type': row[1],
                'interval_seconds': row[2],
                'interval_minutes': row[3],
                'interval_hours': row[4],
                'interval_days': row[5],
                'interval_weeks': row[6],
                'cron_expression': row[7],
                'start_date': row[8].isoformat() if row[8] else None,
                'end_date': row[9].isoformat() if row[9] else None,
                'next_run_time': row[10].isoformat() if row[10] else None,
                'last_run_time': row[11].isoformat() if row[11] else None,
                'max_runs': row[12],
                'current_runs': row[13],
                'is_active': bool(row[14])
            }
            schedules.append(schedule)
        
        job['schedules'] = schedules
        
        # Get job parameters
        params_query = """
        SELECT ParameterName, ParameterValue, ParameterType
        FROM ScheduledJobParameters
        WHERE ScheduledJobId = ?
        """
        
        cursor.execute(params_query, job_id)
        
        parameters = {}
        for row in cursor.fetchall():
            param_name = row[0]
            param_value = row[1]
            param_type = row[2]
            
            parameters[param_name] = {
                'value': param_value,
                'type': param_type
            }
        
        job['parameters'] = parameters
        
        cursor.close()
        conn.close()
        
        return jsonify(job)
        
    except Exception as e:
        logger.error(f"Error getting job: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs', methods=['POST'])
def create_job():
    """Create a new scheduled job"""
    try:
        # Get request data
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Required fields
        job_name = data.get('name')
        job_type = data.get('type')
        target_id = data.get('target_id')
        
        if not job_name or not job_type or not target_id:
            return jsonify({'error': 'Missing required fields (name, type, target_id)'}), 400
        
        # Optional fields
        description = data.get('description', '')
        created_by = data.get('created_by', 'system')
        is_active = data.get('is_active', True)
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Create job
        job_query = """
        INSERT INTO ScheduledJobs (
            JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
        )
        VALUES (?, ?, ?, ?, ?, getutcdate(), ?)
        """
        
        cursor.execute(job_query, job_name, job_type, target_id, description, created_by, is_active)
        conn.commit()
        
        # Get the ID of the created job
        cursor.execute("SELECT @@IDENTITY")
        job_id = cursor.fetchone()[0]
        
        # Create parameters if provided
        parameters = data.get('parameters', {})
        if parameters:
            params_query = """
            INSERT INTO ScheduledJobParameters (
                ScheduledJobId, ParameterName, ParameterValue, ParameterType
            )
            VALUES (?, ?, ?, ?)
            """
            
            for param_name, param_data in parameters.items():
                param_value = param_data.get('value', '')
                param_type = param_data.get('type', 'string')
                
                cursor.execute(params_query, job_id, param_name, param_value, param_type)
            
            conn.commit()
        
        # Create schedule if provided
        schedule = data.get('schedule')
        if schedule:
            schedule_id = _create_schedule(cursor, job_id, schedule)
            
            if not schedule_id:
                return jsonify({'error': 'Failed to create schedule'}), 500
        
        # Return the created job
        cursor.close()
        conn.close()
        
        return jsonify({
            'id': job_id,
            'name': job_name,
            'type': job_type,
            'target_id': target_id,
            'description': description,
            'created_by': created_by,
            'is_active': is_active
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating job: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>', methods=['PUT'])
def update_job(job_id):
    """Update a scheduled job"""
    try:
        # Get request data
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if job exists
        cursor.execute("SELECT 1 FROM ScheduledJobs WHERE ScheduledJobId = ?", job_id)
        if not cursor.fetchone():
            return jsonify({'error': 'Job not found'}), 404
        
        # Fields to update
        job_name = data.get('name')
        description = data.get('description')
        is_active = data.get('is_active')
        
        # Build update query
        update_fields = []
        params = []
        
        if job_name is not None:
            update_fields.append("JobName = ?")
            params.append(job_name)
        
        if description is not None:
            update_fields.append("Description = ?")
            params.append(description)
        
        if is_active is not None:
            update_fields.append("IsActive = ?")
            params.append(is_active)
        
        # Add modified fields
        update_fields.append("ModifiedBy = ?")
        params.append(data.get('modified_by', 'system'))
        
        update_fields.append("ModifiedAt = getutcdate()")
        
        # Add job_id to params
        params.append(job_id)
        
        # Update job if fields provided
        if update_fields:
            query = f"""
            UPDATE ScheduledJobs
            SET {', '.join(update_fields)}
            WHERE ScheduledJobId = ?
            """
            
            cursor.execute(query, *params)
            conn.commit()
        
        # Update parameters if provided
        parameters = data.get('parameters')
        if parameters is not None:
            # First delete existing parameters
            cursor.execute("DELETE FROM ScheduledJobParameters WHERE ScheduledJobId = ?", job_id)
            
            # Then insert new parameters
            params_query = """
            INSERT INTO ScheduledJobParameters (
                ScheduledJobId, ParameterName, ParameterValue, ParameterType
            )
            VALUES (?, ?, ?, ?)
            """
            
            for param_name, param_data in parameters.items():
                param_value = param_data.get('value', '')
                param_type = param_data.get('type', 'string')
                
                cursor.execute(params_query, job_id, param_name, param_value, param_type)
            
            conn.commit()
        
        # Return success
        cursor.close()
        conn.close()
        
        return jsonify({'message': f'Job {job_id} updated successfully'})
        
    except Exception as e:
        logger.error(f"Error updating job: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>', methods=['DELETE'])
def delete_job(job_id):
    """Delete a scheduled job"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if job exists
        cursor.execute("SELECT 1 FROM ScheduledJobs WHERE ScheduledJobId = ?", job_id)
        if not cursor.fetchone():
            return jsonify({'error': 'Job not found'}), 404
        
        # Delete the job (will cascade to schedules and parameters due to foreign key constraints)
        cursor.execute("DELETE FROM ScheduledJobs WHERE ScheduledJobId = ?", job_id)
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({'message': f'Job {job_id} deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting job: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>/schedules', methods=['GET'])
def get_job_schedules(job_id):
    """Get schedules for a specific job"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if a scheduler job exists for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = 'document' AND TargetId = ?", job_id)
        row = cursor.fetchone()
        
        # If no scheduler job exists, create one
        if not row:
            print(f'Creating scheduler job for document job {job_id}')
            
            # Get document job info to use for scheduler job name
            document_job_name = f"Document Job {job_id}"
            try:
                cursor.execute("SELECT JobName FROM DocumentJobs WHERE JobID = ?", job_id)
                doc_job_row = cursor.fetchone()
                if doc_job_row and doc_job_row[0]:
                    document_job_name = doc_job_row[0]
            except:
                # If we can't get the name, just use the default
                pass
                
            # Create a scheduler job for this document job
            cursor.execute("""
                INSERT INTO ScheduledJobs (
                    JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
                )
                VALUES (?, 'document', ?, 'Auto-created scheduler job for document processing', 'system', getutcdate(), 1)
            """, document_job_name, job_id)
            conn.commit()
            
            # Get the ID of the newly created scheduler job
            cursor.execute("SELECT @@IDENTITY")
            scheduled_job_id = cursor.fetchone()[0]
        else:
            scheduled_job_id = row[0]
        
        # Query schedules for this scheduler job
        query = """
        SELECT s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive
        FROM ScheduleDefinitions s
        WHERE s.ScheduledJobId = ?
        """
        
        cursor.execute(query, scheduled_job_id)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = {
                'id': row[0],
                'type': row[1],
                'interval_seconds': row[2],
                'interval_minutes': row[3],
                'interval_hours': row[4],
                'interval_days': row[5],
                'interval_weeks': row[6],
                'cron_expression': row[7],
                'start_date': row[8].isoformat() if row[8] else None,
                'end_date': row[9].isoformat() if row[9] else None,
                'next_run_time': row[10].isoformat() if row[10] else None,
                'last_run_time': row[11].isoformat() if row[11] else None,
                'max_runs': row[12],
                'current_runs': row[13],
                'is_active': bool(row[14])
            }
            schedules.append(schedule)
        
        cursor.close()
        conn.close()
        
        return jsonify(schedules)  # Return empty list if no schedules
        
    except Exception as e:
        logger.error(f"Error getting job schedules: {str(e)}")
        return jsonify({'error': str(e)}), 500
    

# TODO: Eventually migrate document scheduler code to these generic routes
@scheduler_bp.route('/types/<string:job_type>/schedules', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_all_schedules_by_type(job_type):
    """Get all workflow schedules"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Query for all workflow jobs and their schedules
        query = """
        SELECT j.ScheduledJobId, j.JobName, j.JobType, j.TargetId, 
               s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive,
               w.workflow_name
        FROM ScheduledJobs j
        JOIN ScheduleDefinitions s ON j.ScheduledJobId = s.ScheduledJobId
        LEFT JOIN Workflows w ON j.TargetId = w.id
        WHERE j.JobType = ?
        ORDER BY w.workflow_name, s.StartDate DESC
        """
        
        cursor.execute(query, job_type)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = {
                'id': row[4],                     # ScheduleId
                'workflow_id': row[3],            # TargetId
                'scheduled_job_id': row[0],       # ScheduledJobId
                'workflow_name': row[19] or f"Workflow {row[3]}",  # workflow_name or fallback
                'type': row[5],                   # ScheduleType
                'interval_seconds': row[6],
                'interval_minutes': row[7],
                'interval_hours': row[8],
                'interval_days': row[9],
                'interval_weeks': row[10],
                'cron_expression': row[11],
                'start_date': row[12].isoformat() if row[12] else None,
                'end_date': row[13].isoformat() if row[13] else None,
                'next_run_time': row[14].isoformat() if row[14] else None,
                'last_run_time': row[15].isoformat() if row[15] else None,
                'max_runs': row[16],
                'current_runs': row[17],
                'is_active': bool(row[18])
            }
            schedules.append(schedule)
        
        cursor.close()
        conn.close()
        
        return jsonify(schedules)
        
    except Exception as e:
        logger.error(f"Error getting workflow schedules: {str(e)}")
        return jsonify({'error': str(e)}), 500

    

# TODO: Eventually migrate document scheduler code to these generic routes
@scheduler_bp.route('/jobs/<int:job_id>/types/<string:job_type>/schedules', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_job_schedules_by_type_and_target_id(job_id, job_type):
    """Get schedules for a specific job"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if a scheduler job exists for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = ? AND TargetId = ?", job_type, job_id)
        row = cursor.fetchone()
        
        # If no scheduler job exists, create one
        if not row:
            print(f'Creating scheduler job for type {job_type} job {job_id}')
            
            # Get document job info to use for scheduler job name
            document_job_name = f"{job_type} Job {job_id}"
            try:
                if str(job_type).lower() == 'document':
                    cursor.execute("SELECT JobName FROM DocumentJobs WHERE JobID = ?", job_id)
                elif str(job_type).lower() == 'workflow':
                    cursor.execute("SELECT workflow_name FROM Workflows WHERE id = ?", job_id)
                else:
                    cursor.execute("SELECT ?", document_job_name)
                doc_job_row = cursor.fetchone()
                if doc_job_row and doc_job_row[0]:
                    document_job_name = doc_job_row[0]
            except:
                # If we can't get the name, just use the default
                pass
                
            # Create a scheduler job for this document job
            cursor.execute("""
                INSERT INTO ScheduledJobs (
                    JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
                )
                VALUES (?, ?, ?, 'Auto-created scheduler job', 'system', getutcdate(), 1)
            """, document_job_name, job_type, job_id)
            conn.commit()
            
            # Get the ID of the newly created scheduler job
            cursor.execute("SELECT @@IDENTITY")
            scheduled_job_id = cursor.fetchone()[0]
        else:
            scheduled_job_id = row[0]
        
        # Query schedules for this scheduler job
        query = """
        SELECT s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive
        FROM ScheduleDefinitions s
        WHERE s.ScheduledJobId = ?
        """
        
        cursor.execute(query, scheduled_job_id)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = {
                'id': row[0],
                'type': row[1],
                'interval_seconds': row[2],
                'interval_minutes': row[3],
                'interval_hours': row[4],
                'interval_days': row[5],
                'interval_weeks': row[6],
                'cron_expression': row[7],
                'start_date': row[8].isoformat() if row[8] else None,
                'end_date': row[9].isoformat() if row[9] else None,
                'next_run_time': row[10].isoformat() if row[10] else None,
                'last_run_time': row[11].isoformat() if row[11] else None,
                'max_runs': row[12],
                'current_runs': row[13],
                'is_active': bool(row[14])
            }
            schedules.append(schedule)
        
        cursor.close()
        conn.close()
        
        return jsonify(schedules)  # Return empty list if no schedules
        
    except Exception as e:
        logger.error(f"Error getting job schedules: {str(e)}")
        return jsonify({'error': str(e)}), 500

    
@scheduler_bp.route('/jobs/<int:job_id>/schedules/<int:schedule_id>', methods=['GET'])
def get_job_schedule(job_id, schedule_id):
    """Get details of a specific schedule"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = 'document' AND TargetId = ?", job_id)
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'No scheduler job found for this document job'}), 404
        
        scheduled_job_id = row[0]
        
        # Query for the specific schedule
        query = """
        SELECT s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive
        FROM ScheduleDefinitions s
        WHERE s.ScheduleId = ? AND s.ScheduledJobId = ?
        """
        
        cursor.execute(query, schedule_id, scheduled_job_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Schedule not found'}), 404
        
        schedule = {
            'id': row[0],
            'type': row[1],
            'interval_seconds': row[2],
            'interval_minutes': row[3],
            'interval_hours': row[4],
            'interval_days': row[5],
            'interval_weeks': row[6],
            'cron_expression': row[7],
            'start_date': row[8].isoformat() if row[8] else None,
            'end_date': row[9].isoformat() if row[9] else None,
            'next_run_time': row[10].isoformat() if row[10] else None,
            'last_run_time': row[11].isoformat() if row[11] else None,
            'max_runs': row[12],
            'current_runs': row[13],
            'is_active': bool(row[14])
        }
        
        cursor.close()
        conn.close()
        
        return jsonify(schedule)
        
    except Exception as e:
        logger.error(f"Error getting schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500
    

@scheduler_bp.route('/jobs/<int:job_id>/types/<string:job_type>/schedules/<int:schedule_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_job_schedule_by_type(job_id, job_type, schedule_id):
    """Get details of a specific schedule"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = ? AND TargetId = ?", job_type, job_id)
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'No scheduler job found for this job'}), 404
        
        scheduled_job_id = row[0]
        
        # Query for the specific schedule
        query = """
        SELECT s.ScheduleId, s.ScheduleType, 
               s.IntervalSeconds, s.IntervalMinutes, s.IntervalHours, s.IntervalDays, s.IntervalWeeks,
               s.CronExpression, s.StartDate, s.EndDate, s.NextRunTime, s.LastRunTime, s.MaxRuns, s.CurrentRuns,
               s.IsActive
        FROM ScheduleDefinitions s
        WHERE s.ScheduleId = ? AND s.ScheduledJobId = ?
        """
        
        cursor.execute(query, schedule_id, scheduled_job_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Schedule not found'}), 404
        
        schedule = {
            'id': row[0],
            'type': row[1],
            'interval_seconds': row[2],
            'interval_minutes': row[3],
            'interval_hours': row[4],
            'interval_days': row[5],
            'interval_weeks': row[6],
            'cron_expression': row[7],
            'start_date': row[8].isoformat() if row[8] else None,
            'end_date': row[9].isoformat() if row[9] else None,
            'next_run_time': row[10].isoformat() if row[10] else None,
            'last_run_time': row[11].isoformat() if row[11] else None,
            'max_runs': row[12],
            'current_runs': row[13],
            'is_active': bool(row[14])
        }
        
        cursor.close()
        conn.close()
        
        return jsonify(schedule)
        
    except Exception as e:
        logger.error(f"Error getting schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>/schedules', methods=['POST'])
def create_job_schedule(job_id):
    """Create a new schedule for a job"""
    try:
        # Get request data
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        print('Create job schedule data:', data)
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if a scheduler job exists for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = 'document' AND TargetId = ?", job_id)
        row = cursor.fetchone()
        
        # If no scheduler job exists, create one
        if not row:
            print(f'Creating scheduler job for document job {job_id}')
            
            # Get document job info to use for scheduler job name
            document_job_name = f"Document Job {job_id}"
            try:
                cursor.execute("SELECT JobName FROM DocumentJobs WHERE JobID = ?", job_id)
                doc_job_row = cursor.fetchone()
                if doc_job_row and doc_job_row[0]:
                    document_job_name = doc_job_row[0]
            except:
                # If we can't get the name, just use the default
                pass
                
            # Create a scheduler job for this document job
            cursor.execute("""
                INSERT INTO ScheduledJobs (
                    JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
                )
                VALUES (?, 'document', ?, 'Auto-created scheduler job for document processing', 'system', getutcdate(), 1)
            """, document_job_name, job_id)
            conn.commit()
            
            # Get the ID of the newly created scheduler job
            cursor.execute("SELECT @@IDENTITY")
            scheduled_job_id = cursor.fetchone()[0]
        else:
            scheduled_job_id = row[0]
        
        # Create schedule for the scheduled_job_id (not the document job_id)
        schedule_id = _create_schedule(cursor, scheduled_job_id, data)
        
        if not schedule_id:
            return jsonify({'error': 'Failed to create schedule'}), 500
        
        # Commit changes
        conn.commit()
        
        # Return the created schedule
        cursor.close()
        conn.close()
        
        return jsonify({
            'id': schedule_id,
            'job_id': job_id,  # Return the document job ID for consistency in the API
            'scheduled_job_id': scheduled_job_id,  # Also return the scheduled job ID for reference
            'type': data.get('type'),
            'is_active': data.get('is_active', True)
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500
    

@scheduler_bp.route('/jobs/<int:job_id>/types/<string:job_type>/schedules', methods=['POST'])
@api_key_or_session_required(min_role=2)
def create_job_schedule_by_type(job_id, job_type):
    """Create a new schedule for a job"""
    try:
        # Get request data
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Check if a scheduler job exists for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = ? AND TargetId = ?", job_type, job_id)
        row = cursor.fetchone()
        
        # If no scheduler job exists, create one
        if not row:
            print(f'Creating scheduler job for {job_type} job {job_id}')
            
            # Get document job info to use for scheduler job name
            document_job_name = f"Document Job {job_id}"
            try:
                if str(job_type).lower() == 'document':
                    cursor.execute("SELECT JobName FROM DocumentJobs WHERE JobID = ?", job_id)
                elif str(job_type).lower() == 'workflow':
                    cursor.execute("SELECT workflow_name FROM Workflows WHERE id = ?", job_id)
                else:
                    cursor.execute("SELECT ?", document_job_name)
                doc_job_row = cursor.fetchone()
                if doc_job_row and doc_job_row[0]:
                    document_job_name = doc_job_row[0]
            except:
                # If we can't get the name, just use the default
                pass
                
            # Create a scheduler job for this document job
            cursor.execute("""
                INSERT INTO ScheduledJobs (
                    JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
                )
                VALUES (?, ?, ?, 'Auto-created scheduler job', 'system', getutcdate(), 1)
            """, document_job_name, job_type, job_id)
            conn.commit()
            
            # Get the ID of the newly created scheduler job
            cursor.execute("SELECT @@IDENTITY")
            scheduled_job_id = cursor.fetchone()[0]
        else:
            scheduled_job_id = row[0]
        
        # Create schedule for the scheduled_job_id (not the document job_id)
        schedule_id = _create_schedule(cursor, scheduled_job_id, data)
        
        if not schedule_id:
            return jsonify({'error': 'Failed to create schedule'}), 500
        
        # Commit changes
        conn.commit()
        
        # Return the created schedule
        cursor.close()
        conn.close()
        
        return jsonify({
            'id': schedule_id,
            'job_id': job_id,  # Return the document job ID for consistency in the API
            'scheduled_job_id': scheduled_job_id,  # Also return the scheduled job ID for reference
            'type': data.get('type'),
            'is_active': data.get('is_active', True)
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>/schedules/<int:schedule_id>', methods=['PUT'])
def update_job_schedule(job_id, schedule_id):
    """Update a job schedule"""
    try:
        print('Updating job schedule...', job_id, schedule_id)
        # Get request data
        data = request.json
        if not data:
            print('No data provided')
            return jsonify({'error': 'No data provided'}), 400
        
        print('Data:', data)
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = 'document' AND TargetId = ?", job_id)
        row = cursor.fetchone()
        if not row:
            print('No scheduler job found for this document job')
            return jsonify({'error': 'No scheduler job found for this document job'}), 404
        
        scheduled_job_id = row[0]
        
        # Check if schedule exists and belongs to the job
        cursor.execute("""
        SELECT 1 FROM ScheduleDefinitions
        WHERE ScheduleId = ? AND ScheduledJobId = ?
        """, schedule_id, scheduled_job_id)
        
        if not cursor.fetchone():
            print('Schedule not found or does not belong to the specified job')
            return jsonify({'error': 'Schedule not found or does not belong to the specified job'}), 404
        
        # Build update query
        update_fields = []
        params = []
        
        # Interval parameters
        interval_mapping = {
            'IntervalSeconds': 'interval_seconds',
            'IntervalMinutes': 'interval_minutes', 
            'IntervalHours': 'interval_hours',
            'IntervalDays': 'interval_days',
            'IntervalWeeks': 'interval_weeks'
        }

        for db_column, json_key in interval_mapping.items():
            interval_value = data.get(json_key)
            if interval_value is not None:
                update_fields.append(f"{db_column} = ?")
                params.append(interval_value)
        
        # Cron expression
        cron_expression = data.get('cron_expression')
        if cron_expression:
            update_fields.append("CronExpression = ?")
            params.append(cron_expression)
        
        # Start and end dates
        start_date = data.get('start_date')
        if start_date:
            update_fields.append("StartDate = ?")
            params.append(start_date)
        
        end_date = data.get('end_date')
        if end_date:
            update_fields.append("EndDate = ?")
            params.append(end_date)
        
        # Max runs
        max_runs = data.get('max_runs')
        if max_runs is not None:
            update_fields.append("MaxRuns = ?")
            params.append(max_runs)
        
        # Is active
        is_active = data.get('is_active')
        if is_active is not None:
            update_fields.append("IsActive = ?")
            params.append(is_active)
        
        # Add schedule_id to params
        params.append(schedule_id)
        
        # Update schedule if fields provided
        if update_fields:
            query = f"""
            UPDATE ScheduleDefinitions
            SET {', '.join(update_fields)}
            WHERE ScheduleId = ?
            """
            print(f'Running update query: {query}')
            cursor.execute(query, *params)
            conn.commit()
        
        # Return success
        cursor.close()
        conn.close()
        print(f'Schedule {schedule_id} updated successfully')
        return jsonify({'message': f'Schedule {schedule_id} updated successfully'})
        
    except Exception as e:
        print(f"Error updating job schedule: {str(e)}")
        logger.error(f"Error updating job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500
    

@scheduler_bp.route('/jobs/<int:job_id>/types/<string:job_type>/schedules/<int:schedule_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
def update_job_schedule_by_type(job_id, job_type, schedule_id):
    """Update a job schedule"""
    try:
        print('Updating job schedule...', job_id, job_type, schedule_id)
        # Get request data
        data = request.json
        if not data:
            print('No data provided')
            return jsonify({'error': 'No data provided'}), 400
        
        print('Data:', data)
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = ? AND TargetId = ?", job_type, job_id)
        row = cursor.fetchone()
        if not row:
            print(f'No scheduler job found for this {job_type} job')
            return jsonify({'error': f'No scheduler job found for this {job_type} job'}), 404
        
        scheduled_job_id = row[0]
        
        # Check if schedule exists and belongs to the job
        cursor.execute("""
        SELECT 1 FROM ScheduleDefinitions
        WHERE ScheduleId = ? AND ScheduledJobId = ?
        """, schedule_id, scheduled_job_id)
        
        if not cursor.fetchone():
            print('Schedule not found or does not belong to the specified job')
            return jsonify({'error': 'Schedule not found or does not belong to the specified job'}), 404
        
        # Build update query
        update_fields = []
        params = []
        
        # Interval parameters
        interval_mapping = {
            'IntervalSeconds': 'interval_seconds',
            'IntervalMinutes': 'interval_minutes', 
            'IntervalHours': 'interval_hours',
            'IntervalDays': 'interval_days',
            'IntervalWeeks': 'interval_weeks'
        }

        for db_column, json_key in interval_mapping.items():
            interval_value = data.get(json_key)
            if interval_value is not None:
                update_fields.append(f"{db_column} = ?")
                params.append(interval_value)
        
        # Schedule type (interval, cron, date)
        schedule_type = data.get('schedule_type') or data.get('type')
        if schedule_type and schedule_type in ['interval', 'cron', 'date']:
            update_fields.append("ScheduleType = ?")
            params.append(schedule_type)
        
        # Cron expression
        cron_expression = data.get('cron_expression')
        if cron_expression:
            update_fields.append("CronExpression = ?")
            params.append(cron_expression)
        
        # Start and end dates
        start_date = data.get('start_date')
        if start_date:
            update_fields.append("StartDate = ?")
            params.append(start_date)
        
        end_date = data.get('end_date')
        if end_date:
            update_fields.append("EndDate = ?")
            params.append(end_date)
        
        # Max runs
        max_runs = data.get('max_runs')
        if max_runs is not None:
            update_fields.append("MaxRuns = ?")
            params.append(max_runs)
        
        # Is active
        is_active = data.get('is_active')
        if is_active is not None:
            update_fields.append("IsActive = ?")
            params.append(is_active)
        
        # Add schedule_id to params
        params.append(schedule_id)
        
        # Update schedule if fields provided
        if update_fields:
            query = f"""
            UPDATE ScheduleDefinitions
            SET {', '.join(update_fields)}
            WHERE ScheduleId = ?
            """
            print(f'Running update query: {query}')
            cursor.execute(query, *params)
            conn.commit()
        
        # Return success
        cursor.close()
        conn.close()
        print(f'Schedule {schedule_id} updated successfully')
        return jsonify({'message': f'Schedule {schedule_id} updated successfully'})
        
    except Exception as e:
        print(f"Error updating job schedule: {str(e)}")
        logger.error(f"Error updating job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/jobs/<int:job_id>/schedules/<int:schedule_id>', methods=['DELETE'])
def delete_job_schedule(job_id, schedule_id):
    """Delete a job schedule"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = 'document' AND TargetId = ?", job_id)
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'No scheduler job found for this document job'}), 404
        
        scheduled_job_id = row[0]
        
        # Check if schedule exists and belongs to the job
        cursor.execute("""
        SELECT 1 FROM ScheduleDefinitions
        WHERE ScheduleId = ? AND ScheduledJobId = ?
        """, schedule_id, scheduled_job_id)
        
        if not cursor.fetchone():
            return jsonify({'error': 'Schedule not found or does not belong to the specified job'}), 404
        
        # Delete the schedule from the database
        cursor.execute("DELETE FROM ScheduleDefinitions WHERE ScheduleId = ?", schedule_id)
        conn.commit()
        
        cursor.close()
        conn.close()
        
        # Note: The APScheduler job will be cleaned up on the next sync cycle
        # (orphan cleanup removes jobs with no matching DB record)
        
        return jsonify({'message': f'Schedule {schedule_id} deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500
    

@scheduler_bp.route('/jobs/<int:job_id>/types/<string:job_type>/schedules/<int:schedule_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_job_schedule_by_type(job_id, job_type, schedule_id):
    """Delete a job schedule"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get the actual scheduler job ID for this document job
        cursor.execute("SELECT ScheduledJobId FROM ScheduledJobs WHERE JobType = ? AND TargetId = ?", job_type, job_id)
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': f'No scheduler job found for this {job_type} job'}), 404
        
        scheduled_job_id = row[0]
        
        # Check if schedule exists and belongs to the job
        cursor.execute("""
        SELECT 1 FROM ScheduleDefinitions
        WHERE ScheduleId = ? AND ScheduledJobId = ?
        """, schedule_id, scheduled_job_id)
        
        if not cursor.fetchone():
            return jsonify({'error': 'Schedule not found or does not belong to the specified job'}), 404
        
        # Delete the schedule from the database
        cursor.execute("DELETE FROM ScheduleDefinitions WHERE ScheduleId = ?", schedule_id)
        conn.commit()
        
        cursor.close()
        conn.close()
        
        # Note: The APScheduler job will be cleaned up on the next sync cycle
        # (orphan cleanup removes jobs with no matching DB record)
        
        return jsonify({'message': f'Schedule {schedule_id} deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting job schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/executions', methods=['GET'])
def get_executions():
    """Get execution history"""
    try:
        # Get query parameters
        job_id = request.args.get('job_id')
        schedule_id = request.args.get('schedule_id')
        status = request.args.get('status')
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Build query with filters
        query = """
        SELECT e.ExecutionId, e.ScheduleId, e.ScheduledJobId, e.StartTime, e.EndTime, e.Status,
               e.ResultMessage, e.ErrorDetails,
               j.JobName, j.JobType, j.TargetId
        FROM ScheduleExecutionHistory e
        JOIN ScheduledJobs j ON e.ScheduledJobId = j.ScheduledJobId
        WHERE 1=1
        """
        
        params = []
        
        if job_id:
            query += " AND e.ScheduledJobId = ?"
            params.append(job_id)
        
        if schedule_id:
            query += " AND e.ScheduleId = ?"
            params.append(schedule_id)
        
        if status:
            query += " AND e.Status = ?"
            params.append(status)
        
        # Add ordering and pagination
        query += " ORDER BY e.StartTime DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        params.extend([offset, limit])
        
        cursor.execute(query, *params)
        
        executions = []
        for row in cursor.fetchall():
            execution = {
                'id': row[0],
                'schedule_id': row[1],
                'job_id': row[2],
                'start_time': row[3].isoformat() if row[3] else None,
                'end_time': row[4].isoformat() if row[4] else None,
                'status': row[5],
                'result_message': row[6],
                'error_details': row[7],
                'job_name': row[8],
                'job_type': row[9],
                'target_id': row[10]
            }
            executions.append(execution)
        
        cursor.close()
        conn.close()
        
        return jsonify(executions)
        
    except Exception as e:
        logger.error(f"Error getting executions: {str(e)}")
        return jsonify({'error': str(e)}), 500

@scheduler_bp.route('/run/<int:job_id>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def run_job_now(job_id):
    """Run a job immediately.
    
    Accepts either a ScheduledJobId or a ScheduleDefinitions ID.
    First tries to find by ScheduledJobId; if not found, looks up
    the ScheduledJobId from the ScheduleDefinitions table.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        # Get job details - first try by ScheduledJobId
        query = """
        SELECT j.JobType, j.TargetId, j.JobName
        FROM ScheduledJobs j
        WHERE j.ScheduledJobId = ?
        """
        
        cursor.execute(query, job_id)
        
        row = cursor.fetchone()
        if not row:
            # Not found by ScheduledJobId — try looking up via ScheduleDefinitions ID
            # The caller may have passed a ScheduleDefinitions ID instead
            schedule_lookup_query = """
            SELECT j.ScheduledJobId, j.JobType, j.TargetId, j.JobName
            FROM ScheduleDefinitions sd
            JOIN ScheduledJobs j ON j.ScheduledJobId = sd.ScheduledJobId
            WHERE sd.ScheduleId = ?
            """
            cursor.execute(schedule_lookup_query, job_id)
            schedule_row = cursor.fetchone()
            if not schedule_row:
                return jsonify({'error': 'Job not found'}), 404
            # Use the resolved ScheduledJobId for the rest of the function
            job_id = schedule_row[0]
            job_type, target_id, job_name = schedule_row[1], schedule_row[2], schedule_row[3]
        else:
            job_type, target_id, job_name = row
        
        # Get job parameters
        params_query = """
        SELECT ParameterName, ParameterValue, ParameterType
        FROM ScheduledJobParameters
        WHERE ScheduledJobId = ?
        """
        
        cursor.execute(params_query, job_id)
        
        parameters = {}
        for row in cursor.fetchall():
            param_name, param_value, param_type = row
            
            # Convert parameter value based on type
            if param_type == 'int':
                param_value = int(param_value) if param_value else None
            elif param_type == 'float':
                param_value = float(param_value) if param_value else None
            elif param_type == 'bool':
                param_value = param_value.lower() in ('true', '1', 'yes') if param_value else False
            elif param_type == 'json':
                param_value = json.loads(param_value) if param_value else None
            
            parameters[param_name] = param_value
        
        # Create a one-time schedule for immediate execution
        schedule_query = """
        INSERT INTO ScheduleDefinitions (
            ScheduledJobId, ScheduleType, StartDate, IsActive
        )
        VALUES (?, 'date', getutcdate(), 1)
        """
        
        cursor.execute(schedule_query, job_id)
        conn.commit()
        
        # Get the ID of the created schedule
        cursor.execute("SELECT @@IDENTITY")
        schedule_id = cursor.fetchone()[0]
        
        # Create execution record
        execution_query = """
        INSERT INTO ScheduleExecutionHistory (
            ScheduleId, ScheduledJobId, StartTime, Status
        )
        VALUES (?, ?, getutcdate(), 'pending')
        """
        
        cursor.execute(execution_query, schedule_id, job_id)
        conn.commit()
        
        # Get the ID of the created execution record
        cursor.execute("SELECT @@IDENTITY")
        execution_id = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        # Call the appropriate API endpoint based on job type
        api_base_url = current_app.config.get('API_BASE_URL', f"http://localhost:{os.getenv('HOST_PORT', '5001')}")
        
        auth_headers = {'X-API-Key': os.getenv('API_KEY', '')}

        if job_type == 'document':
            # Document job
            api_url = f"{api_base_url}/api/document_processor/job/{target_id}/run"
            response = requests.post(api_url, headers=auth_headers)

        elif job_type == 'agent':
            # Agent job
            api_url = f"{api_base_url}/chat/general"
            prompt = parameters.get('prompt', 'Run scheduled task')
            payload = {
                'agent_id': target_id,
                'prompt': prompt,
                'hist': '[]'  # Empty history for scheduled runs
            }
            response = requests.post(api_url, json=payload, headers=auth_headers)

        elif job_type == 'workflow':
            # Workflow job
            api_url = f"{api_base_url}/api/workflow/run"
            payload = {
                'workflow_id': target_id,
                'initiator': 'api',
                'variables': parameters
            }
            response = requests.post(api_url, json=payload, headers=auth_headers)
            
        else:
            return jsonify({'error': f'Unsupported job type: {job_type}'}), 400
        
        # Update execution record
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        set_tenant_context(cursor)
        
        update_query = """
        UPDATE ScheduleExecutionHistory
        SET Status = ?, EndTime = getutcdate(), ResultMessage = ?
        WHERE ExecutionId = ?
        """
        
        if response.status_code == 200:
            status = 'completed'
            result_message = f"Job executed successfully. Response: {response.text[:500]}..."
        else:
            status = 'failed'
            result_message = f"Job execution failed. Response: {response.status_code} - {response.text[:500]}..."
        
        cursor.execute(update_query, status, result_message, execution_id)
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'message': f'Job {job_id} ({job_name}) executed successfully',
            'execution_id': execution_id,
            'response': response.json() if response.status_code == 200 else None
        })
        
    except Exception as e:
        logger.error(f"Error running job: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Helper functions

def _parse_datetime_string(datetime_str):
    """
    Parse a datetime string with multiple format support.
    
    Args:
        datetime_str: String representation of datetime
        
    Returns:
        datetime object or None if parsing fails
    """
    if not datetime_str:
        return None
        
    # Common datetime formats to try
    formats = [
        '%Y-%m-%dT%H:%M:%S',      # ISO format: 2025-06-27T20:05:00
        '%Y-%m-%dT%H:%M',         # ISO format without seconds: 2025-06-27T20:05
        '%Y-%m-%d %H:%M:%S',      # Space separated with seconds: 2025-06-27 20:05:00
        '%Y-%m-%d %H:%M',         # Space separated: 2025-06-27 20:05
        '%Y-%m-%dT%H:%M:%S.%f',   # ISO with microseconds: 2025-06-27T20:05:00.123456
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    
    # If all formats fail, log the error and return None
    logger.error(f"Unable to parse datetime string: {datetime_str}")
    return None

def _create_schedule(cursor, job_id, schedule_data):
    """
    Create a schedule in the database.
    
    Args:
        cursor: Database cursor
        job_id: ID of the job
        schedule_data: Dictionary with schedule details
        
    Returns:
        ID of the created schedule or None if failed
    """
    print('Creating job schedule...', job_id)
    print('Schedule Data:', schedule_data)
    schedule_type = schedule_data.get('type')
    if not schedule_type or schedule_type not in ['interval', 'cron', 'date']:
        logger.error(f"Invalid schedule type: {schedule_type}")
        return None
    
    # Schedule fields
    fields = ['ScheduledJobId', 'ScheduleType']
    values = [job_id, schedule_type]
    
    # Interval parameters
    if schedule_type == 'interval':
        interval_seconds = schedule_data.get('interval_seconds')
        if interval_seconds is not None:
            fields.append('IntervalSeconds')
            values.append(interval_seconds)
        
        interval_minutes = schedule_data.get('interval_minutes')
        if interval_minutes is not None:
            fields.append('IntervalMinutes')
            values.append(interval_minutes)
        
        interval_hours = schedule_data.get('interval_hours')
        if interval_hours is not None:
            fields.append('IntervalHours')
            values.append(interval_hours)
        
        interval_days = schedule_data.get('interval_days')
        if interval_days is not None:
            fields.append('IntervalDays')
            values.append(interval_days)
        
        interval_weeks = schedule_data.get('interval_weeks')
        if interval_weeks is not None:
            fields.append('IntervalWeeks')
            values.append(interval_weeks)
    
    # Cron parameters
    elif schedule_type == 'cron':
        cron_expression = schedule_data.get('cron_expression')
        if cron_expression and is_valid_cron(cron_expression):
            fields.append('CronExpression')
            values.append(cron_expression)
        else:
            logger.error(f"Invalid cron expression: {cron_expression}")
            return None
        
    start_date = None

    # If datetime strings and timezone offset are provided
    if 'start_date' in schedule_data and schedule_data.get('start_date'):
        # Parse the local datetime string
        start_date_str = schedule_data.get('start_date')
        
        try:
            # If it's already a datetime object, convert to string
            if isinstance(start_date_str, datetime):
                start_date = start_date_str.strftime('%Y-%m-%d %H:%M:%S')
            else:
                # Parse datetime string using the helper function
                local_dt = _parse_datetime_string(start_date_str)
                
                if local_dt is None:
                    logger.error(f"Failed to parse start_date: {start_date_str}")
                    start_date = start_date_str  # Use as-is if parsing fails
                else:
                    if 'timezone_offset' in schedule_data:
                        # Convert to UTC by adding the offset (which is negative for positive timezone differences)
                        utc_dt = local_dt + timedelta(minutes=schedule_data.get('timezone_offset', 0))
                        start_date = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # Use as-is if no timezone provided
                        start_date = local_dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error processing start date: {str(e)}")
            start_date = start_date_str  # Use as-is if parsing fails

        fields.append('StartDate')
        values.append(start_date)

    end_date = None

    # If datetime strings and timezone offset are provided
    if 'end_date' in schedule_data and schedule_data.get('end_date'):
        end_date_str = schedule_data.get('end_date')
        
        try:
            # If it's already a datetime object, convert to string
            if isinstance(end_date_str, datetime):
                end_date = end_date_str.strftime('%Y-%m-%d %H:%M:%S')
            else:
                # Parse datetime string using the helper function
                local_dt = _parse_datetime_string(end_date_str)
                
                if local_dt is None:
                    logger.error(f"Failed to parse end_date: {end_date_str}")
                    end_date = end_date_str  # Use as-is if parsing fails
                else:
                    if 'timezone_offset' in schedule_data:
                        # Convert to UTC by adding the offset
                        utc_dt = local_dt + timedelta(minutes=schedule_data.get('timezone_offset', 0))
                        end_date = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # Use as-is if no timezone provided
                        end_date = local_dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error processing end date: {str(e)}")
            end_date = end_date_str  # Use as-is if parsing fails
        
        fields.append('EndDate')
        values.append(end_date)
    
    # Max runs
    max_runs = schedule_data.get('max_runs')
    if max_runs is not None:
        fields.append('MaxRuns')
        values.append(max_runs)
    
    # Is active
    is_active = schedule_data.get('is_active', True)
    fields.append('IsActive')
    values.append(is_active)
    
    # Create schedule
    query = f"""
    INSERT INTO ScheduleDefinitions (
        {', '.join(fields)}
    )
    VALUES ({', '.join(['?'] * len(values))})
    """
    print(query, *values)
    cursor.execute(query, *values)
    cursor.execute("SELECT @@IDENTITY")
    schedule_id = cursor.fetchone()[0]
    
    return schedule_id

def _update_schedule(cursor, schedule_id, schedule_data):
    """
    Update a schedule in the database.
    
    Args:
        cursor: Database cursor
        schedule_id: ID of the schedule to update
        schedule_data: Dictionary with schedule details
        
    Returns:
        True if successful, False if failed
    """
    print('Updating schedule...', schedule_id)
    print('Schedule Data:', schedule_data)
    
    # Check if schedule exists
    cursor.execute("SELECT ScheduleType FROM ScheduleDefinitions WHERE ScheduleId = ?", schedule_id)
    existing_row = cursor.fetchone()
    if not existing_row:
        logger.error(f"Schedule {schedule_id} not found")
        return False
    
    current_schedule_type = existing_row[0]
    
    # Get the new schedule type, defaulting to current if not provided
    schedule_type = schedule_data.get('type', current_schedule_type)
    if schedule_type not in ['interval', 'cron', 'date']:
        logger.error(f"Invalid schedule type: {schedule_type}")
        return False
    
    # Build update fields and values
    update_fields = []
    values = []
    
    # Update schedule type if provided
    if 'type' in schedule_data:
        update_fields.append('ScheduleType = ?')
        values.append(schedule_type)
    
    # Clear interval fields when not interval type
    if schedule_type != 'interval':
        # Clear all interval fields if schedule type is not interval
        update_fields.extend([
            'IntervalSeconds = NULL',
            'IntervalMinutes = NULL', 
            'IntervalHours = NULL',
            'IntervalDays = NULL',
            'IntervalWeeks = NULL'
        ])
    
    # Clear cron field if not cron type
    if schedule_type != 'cron':
        update_fields.append('CronExpression = NULL')
    
    # Interval parameters - clear unused interval fields and set the ones provided
    if schedule_type == 'interval':
        # Check if any interval fields are being updated
        interval_fields_being_updated = [key for key in schedule_data.keys() if key.startswith('interval_')]
        
        if interval_fields_being_updated:
            # Clear interval fields that are NOT being updated to ensure old values are removed
            interval_field_mapping = {
                'interval_seconds': 'IntervalSeconds',
                'interval_minutes': 'IntervalMinutes',
                'interval_hours': 'IntervalHours', 
                'interval_days': 'IntervalDays',
                'interval_weeks': 'IntervalWeeks'
            }
            
            for json_key, db_column in interval_field_mapping.items():
                if json_key not in schedule_data:
                    # Clear fields that are not being updated
                    update_fields.append(f'{db_column} = NULL')
        
        # Set the interval fields that are provided
        interval_seconds = schedule_data.get('interval_seconds')
        if interval_seconds is not None:
            update_fields.append('IntervalSeconds = ?')
            values.append(interval_seconds)
        
        interval_minutes = schedule_data.get('interval_minutes')
        if interval_minutes is not None:
            update_fields.append('IntervalMinutes = ?')
            values.append(interval_minutes)
        
        interval_hours = schedule_data.get('interval_hours')
        if interval_hours is not None:
            update_fields.append('IntervalHours = ?')
            values.append(interval_hours)
        
        interval_days = schedule_data.get('interval_days')
        if interval_days is not None:
            update_fields.append('IntervalDays = ?')
            values.append(interval_days)
        
        interval_weeks = schedule_data.get('interval_weeks')
        if interval_weeks is not None:
            update_fields.append('IntervalWeeks = ?')
            values.append(interval_weeks)
    
    # Cron parameters
    elif schedule_type == 'cron':
        cron_expression = schedule_data.get('cron_expression')
        if cron_expression and is_valid_cron(cron_expression):
            update_fields.append('CronExpression = ?')
            values.append(cron_expression)
        elif cron_expression:  # Only fail if cron_expression was provided but invalid
            logger.error(f"Invalid cron expression: {cron_expression}")
            return False
    
    # Start date handling
    if 'start_date' in schedule_data:
        start_date = None
        start_date_str = schedule_data.get('start_date')
        
        if start_date_str:
            try:
                # If it's already a datetime object, convert to string
                if isinstance(start_date_str, datetime):
                    start_date = start_date_str.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    # Parse datetime string using the helper function
                    local_dt = _parse_datetime_string(start_date_str)
                    
                    if local_dt is None:
                        logger.error(f"Failed to parse start_date: {start_date_str}")
                        start_date = start_date_str  # Use as-is if parsing fails
                    else:
                        if 'timezone_offset' in schedule_data:
                            # Convert to UTC by adding the offset (which is negative for positive timezone differences)
                            utc_dt = local_dt + timedelta(minutes=schedule_data.get('timezone_offset', 0))
                            start_date = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            # Use as-is if no timezone provided
                            start_date = local_dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logger.error(f"Error processing start date: {str(e)}")
                start_date = start_date_str  # Use as-is if parsing fails
        
        update_fields.append('StartDate = ?')
        values.append(start_date)
    
    # End date handling
    if 'end_date' in schedule_data:
        end_date = None
        end_date_str = schedule_data.get('end_date')
        
        if end_date_str:
            try:
                # If it's already a datetime object, convert to string
                if isinstance(end_date_str, datetime):
                    end_date = end_date_str.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    # Parse datetime string using the helper function
                    local_dt = _parse_datetime_string(end_date_str)
                    
                    if local_dt is None:
                        logger.error(f"Failed to parse end_date: {end_date_str}")
                        end_date = end_date_str  # Use as-is if parsing fails
                    else:
                        if 'timezone_offset' in schedule_data:
                            # Convert to UTC by adding the offset
                            utc_dt = local_dt + timedelta(minutes=schedule_data.get('timezone_offset', 0))
                            end_date = utc_dt.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            # Use as-is if no timezone provided
                            end_date = local_dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logger.error(f"Error processing end date: {str(e)}")
                end_date = end_date_str  # Use as-is if parsing fails
        
        update_fields.append('EndDate = ?')
        values.append(end_date)
    
    # Max runs
    if 'max_runs' in schedule_data:
        max_runs = schedule_data.get('max_runs')
        update_fields.append('MaxRuns = ?')
        values.append(max_runs)
    
    # Is active
    if 'is_active' in schedule_data:
        is_active = schedule_data.get('is_active')
        update_fields.append('IsActive = ?')
        values.append(is_active)
    
    # Add schedule_id to values for WHERE clause
    values.append(schedule_id)
    
    # Update schedule if there are fields to update
    if update_fields:
        query = f"""
        UPDATE ScheduleDefinitions
        SET {', '.join(update_fields)}
        WHERE ScheduleId = ?
        """
        print(f'Update query: {query}')
        print(f'Values: {values}')
        cursor.execute(query, *values)
        return True
    else:
        logger.warning(f"No fields to update for schedule {schedule_id}")
        return True  # Consider no updates as success