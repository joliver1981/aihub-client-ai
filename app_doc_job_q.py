import os
import sys
import json
import time
import logging
from logging.handlers import WatchedFileHandler
import pyodbc
import argparse
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union
import requests

# Import the document processor class
from LLMDocumentEngine import LLMDocumentProcessor
import config as cfg
from CommonUtils import get_base_url


# Configure logging
logger = logging.getLogger("DocumentJobQueueProcessor")
log_level_name = getattr(cfg, 'LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=cfg.DOC_JOB_QUEUE_LOG, encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class DocumentJobQueueProcessor:
    """
    A service that polls the DocumentJobExecutions table for pending jobs 
    and processes them using the LLMDocumentProcessor.
    """
    def __init__(
        self,
        db_connection_string: str,
        vector_db_path: str = "./chroma_db",
        schema_dir: str = "./schemas",
        tenant_id: Optional[int] = os.getenv('API_KEY'),
        poll_interval: int = 30
    ):
        """
        Initialize the document job queue processor.
        
        Args:
            db_connection_string: SQL Server connection string
            vector_db_path: Path to store ChromaDB
            schema_dir: Directory containing document schemas
            tenant_id: Optional tenant ID to filter jobs
            poll_interval: Polling interval in seconds
        """
        self.db_connection_string = db_connection_string
        self.vector_db_path = vector_db_path
        self.schema_dir = schema_dir
        self.tenant_id = tenant_id
        self.poll_interval = poll_interval
        self.db_conn = None
        self.doc_processor = None
        self.is_running = False
        self.current_job_id = None
        self.current_execution_id = None
        
        # Connect to database
        self._connect_db()
        
        # Initialize document processor
        self._init_document_processor()
        
    def _connect_db(self):
        """Connect to the SQL Server database"""
        try:
            self.db_conn = pyodbc.connect(self.db_connection_string)
            logger.info("Connected to SQL Server database")
        except Exception as e:
            logger.error(f"Failed to connect to SQL database: {str(e)}")
            raise
            
    def _init_document_processor(self):
        """Initialize the document processor"""
        try:
            self.doc_processor = LLMDocumentProcessor(
                vector_db_path=self.vector_db_path,
                schema_dir=self.schema_dir,
                sql_connection_string=self.db_connection_string
            )
            logger.info("Document processor initialized")
        except Exception as e:
            logger.error(f"Failed to initialize document processor: {str(e)}")
            raise
            
    def start(self):
        """Start the job processor service"""
        if self.is_running:
            logger.warning("Job processor is already running")
            return
            
        self.is_running = True
        logger.info("Starting document job queue processor")
        
        try:
            # Main processing loop
            while self.is_running:
                try:
                    # Check if database connection is alive
                    if not self.db_conn or not self._is_connection_alive():
                        logger.info("Database connection lost, reconnecting...")
                        self._connect_db()
                    
                    # Get next job to process
                    job = self._get_next_job_from_queue()
                    
                    if job:
                        print(f"Processing job: {job['JobName']} (ID: {job['JobID']}, Execution: {job['ExecutionID']})")
                        logger.info(f"Processing job: {job['JobName']} (ID: {job['JobID']}, Execution: {job['ExecutionID']})")
                        self._process_job(job)
                        
                    # Sleep before next poll
                    time.sleep(self.poll_interval)
                    
                except Exception as e:
                    print(f"Error in processing loop: {str(e)}")
                    logger.error(f"Error in processing loop: {str(e)}")
                    logger.error(traceback.format_exc())
                    
                    # If we're in the middle of a job, mark it as failed
                    if self.current_job_id and self.current_execution_id:
                        self._update_job_execution(
                            self.current_execution_id,
                            "Failed",
                            error_message=str(e)
                        )
                        
                        self.current_job_id = None
                        self.current_execution_id = None
                        
                    # Sleep before retry
                    time.sleep(60)  # longer sleep on error
                    
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the job processor"""
        logger.info("Stopping document job queue processor")
        self.is_running = False
        
        # Close connections
        if self.doc_processor:
            self.doc_processor.close()
            
        if self.db_conn:
            self.db_conn.close()
            
        logger.info("Document job queue processor stopped")
    
    def _is_connection_alive(self) -> bool:
        """Check if the database connection is still alive"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except:
            return False
            
    def _ensure_db_connection(self):
        """Ensure database connection is alive, reconnect if necessary"""
        try:
            if not self.db_conn or not self._is_connection_alive():
                logger.info("Database connection lost or dead, reconnecting...")
                if self.db_conn:
                    try:
                        self.db_conn.close()
                    except:
                        pass
                self._connect_db()
        except Exception as e:
            logger.error(f"Error ensuring database connection: {str(e)}")
            raise
            
    def _get_next_job_from_queue(self) -> Optional[Dict[str, Any]]:
        """
        Get the next pending job execution from the queue.
        
        Returns:
            Dictionary with job details or None if no jobs are pending
        """
        try:
            print('Getting next job from queue...')
            self._ensure_db_connection()
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                print('Setting context...', self.tenant_id)
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get details for next pending execution
            query = """
            SELECT e.ExecutionID, e.JobID, j.JobName, j.Description, 
                   j.InputDirectory, j.ArchiveDirectory, j.FilePattern, 
                   j.ProcessSubdirectories, j.DefaultDocumentType,
                   j.ForceAIExtraction, j.UseBatchProcessing, j.BatchSize,
                   j.NotifyOnCompletion, j.NotificationEmail,
                   j.AdditionalSettings
            FROM DocumentJobExecutions e
            JOIN DocumentJobs j ON e.JobID = j.JobID
            WHERE e.Status = 'QUEUED'
            AND j.IsActive = 1
            """

            print('Running Query:', query)
            cursor.execute(query)

            print('Running Query:', query)
                
            row = cursor.fetchone()

            print('Query Result:', row)
            
            if not row:
                return None
                
            # Convert row to dictionary
            columns = [column[0] for column in cursor.description]
            job = dict(zip(columns, row))
            
            # Update execution status to In Progress
            cursor.execute(
                "UPDATE DocumentJobExecutions SET Status = 'RUNNING' WHERE ExecutionID = ?", 
                (job['ExecutionID'],)
            )
            self.db_conn.commit()
            
            return job
            
        except Exception as e:
            print(f"Error getting next job: {str(e)}")
            logger.error(f"Error getting next job: {str(e)}")
            return None
            
    def _process_job(self, job: Dict[str, Any]):
        """
        Process a document job.
        
        Args:
            job: Dictionary with job details
        """
        start_time = datetime.now()
        
        # Set current job tracking
        self.current_job_id = job['JobID']
        self.current_execution_id = job['ExecutionID']
        
        try:
            # Ensure input directory exists
            input_dir = job['InputDirectory']
            if not os.path.exists(input_dir):
                raise ValueError(f"Input directory does not exist: {input_dir}")
                
            # Get job parameters
            archive_dir = job['ArchiveDirectory'] or "processed_archive"
            file_pattern = job['FilePattern'] or "*.pdf"
            recursive = bool(job['ProcessSubdirectories'])
            document_type = job['DefaultDocumentType']
            force_ai = bool(job['ForceAIExtraction'])
            use_batch = bool(job['UseBatchProcessing'])
            batch_size = job['BatchSize'] or 3
            
            # Process the directory
            logger.info(f"Processing directory: {input_dir} with pattern: {file_pattern}")
            results = self.doc_processor.process_directory(
                directory_path=input_dir,
                archive_dir=archive_dir,
                file_pattern=file_pattern,
                recursive=recursive,
                document_type=document_type,
                execution_id=self.current_execution_id
            )
            
            # Calculate job stats
            documents_processed = len(results)
            documents_succeeded = len(results[results['status'] == 'success']) if 'status' in results.columns else 0
            documents_failed = documents_processed - documents_succeeded
            
            # Get total pages processed
            total_pages = 0
            if 'page_count' in results.columns:
                total_pages = int(results['page_count'].sum())
            
            # Calculate execution duration
            end_time = datetime.now()
            duration_seconds = int((end_time - start_time).total_seconds())
            
            # Update job execution record
            self._update_job_execution(
                job['ExecutionID'],
                "COMPLETED",
                documents_processed=documents_processed,
                documents_succeeded=documents_succeeded,
                documents_failed=documents_failed,
                total_pages=total_pages,
                duration_seconds=duration_seconds
            )
            
            # Send notification email if enabled
            if job.get('NotifyOnCompletion') and job.get('NotificationEmail'):
                try:
                    subject = f"Document Job Completed: {job['JobName']}"
                    message = f"""
                    Document Job '{job['JobName']}' has completed processing.
                    
                    Job Details:
                    - Documents Processed: {documents_processed}
                    - Documents Succeeded: {documents_succeeded}
                    - Documents Failed: {documents_failed}
                    - Total Pages: {total_pages}
                    - Duration: {duration_seconds} seconds
                    
                    Job ID: {job['JobID']}
                    Execution ID: {job['ExecutionID']}
                    """
                    
                    send_email_via_api(
                        recipients=job['NotificationEmail'],
                        subject=subject,
                        body=message
                    )
                    print(f"Sent completion notification email for job {job['JobName']}")
                    logger.info(f"Sent completion notification email for job {job['JobName']}")
                except Exception as e:
                    print(f"Failed to send completion notification email: {str(e)}")
                    logger.error(f"Failed to send completion notification email: {str(e)}")
            
            print(f"Job {job['JobName']} (ID: {job['JobID']}) completed successfully")
            logger.info(f"Job {job['JobName']} (ID: {job['JobID']}) completed successfully")
            
        except Exception as e:
            # Update job execution as failed
            print(f"Error processing job {job['JobName']} (ID: {job['JobID']}): {str(e)}")
            logger.error(f"Error processing job {job['JobName']} (ID: {job['JobID']}): {str(e)}")
            logger.error(traceback.format_exc())
            
            self._update_job_execution(
                job['ExecutionID'],
                "FAILED",
                error_message=str(e)
            )
                
        finally:
            # Clear current job tracking
            self.current_job_id = None
            self.current_execution_id = None
    
    def _update_job_execution(
        self,
        execution_id: int,
        status: str,
        documents_processed: int = 0,
        documents_succeeded: int = 0,
        documents_failed: int = 0,
        total_pages: int = 0,
        duration_seconds: Optional[int] = None,
        error_message: Optional[str] = None
    ):
        """Update a job execution record with results"""
        try:
            self._ensure_db_connection()
            cursor = self.db_conn.cursor()
            
            # Set tenant context if needed
            if self.tenant_id:
                cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
                
            # Build update query
            query = """
                UPDATE DocumentJobExecutions
                SET Status = ?,
                    DocumentsProcessed = ?,
                    DocumentsSucceeded = ?,
                    DocumentsFailed = ?,
                    TotalPages = ?,
            """
            
            params = [
                status,
                documents_processed,
                documents_succeeded,
                documents_failed,
                total_pages
            ]
            
            # Add completed time if job is finished
            if status in ('COMPLETED', 'FAILED'):
                query += " CompletedAt = getutcdate(),"
                
            # Add duration if provided
            if duration_seconds is not None:
                query += " ExecutionDurationSeconds = ?,"
                params.append(duration_seconds)
                
            # Add error message if provided
            if error_message is not None:
                query += " ErrorMessage = ?,"
                params.append(error_message)
                
            # Remove trailing comma and add WHERE clause
            query = query.rstrip(',') + " WHERE ExecutionID = ?"
            params.append(execution_id)

            print('Running Query:', query)
            print('Params:', params)
            
            # Execute update
            cursor.execute(query, params)
            
            # Also update LastRunAt in the DocumentJobs table
            cursor.execute("""
                UPDATE DocumentJobs
                SET LastRunAt = getutcdate(),
                    LastModifiedAt = getutcdate()
                WHERE JobID = (
                    SELECT JobID FROM DocumentJobExecutions WHERE ExecutionID = ?
                )
            """, (execution_id,))
            
            self.db_conn.commit()
            
        except Exception as e:
            logger.error(f"Error updating job execution: {str(e)}")
            self.db_conn.rollback()


def send_email_via_api(recipients: Union[str, List[str]], subject: str, body: str, html_content: bool = False) -> bool:
    """
    Send an email by calling the API endpoint.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        html_content: Boolean indicating if body contains HTML (default False)
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Convert single recipient to list
        if isinstance(recipients, str):
            recipients = [recipients]
            
        # Prepare the request payload
        payload = {
            "recipients": recipients,
            "subject": subject,
            "body": body,
            "html_content": html_content
        }
        
        # Make the API request
        response = requests.post(
            f"{get_base_url()}/api/send_email",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("status") == "success"
        else:
            logger.error(f"Failed to send email via API. Status code: {response.status_code}, Response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending email via API: {str(e)}")
        return False


def main():
    """Main entry point for the document job queue processor"""
    parser = argparse.ArgumentParser(description='Document Job Queue Processor')
    parser.add_argument('--connection-string', type=str, help='SQL Server connection string')
    parser.add_argument('--vector-db-path', type=str, default='./chroma_db', help='Path to ChromaDB storage')
    parser.add_argument('--schema-dir', type=str, default='./schemas', help='Path to document schemas')
    parser.add_argument('--tenant-id', type=int, help='Tenant ID to filter jobs')
    parser.add_argument('--poll-interval', type=int, default=60, help='Polling interval in seconds')
    args = parser.parse_args()
    
    # Use environment variables if arguments not provided
    connection_string = args.connection_string or cfg.CONNECTION_STRING
    if not connection_string:
        print("Error: Database connection string must be provided via --connection-string or DB_CONNECTION_STRING environment variable")
        sys.exit(1)
        
    vector_db_path = args.vector_db_path or os.environ.get('VECTOR_DB_PATH', './chroma_db')
    schema_dir = args.schema_dir or os.environ.get('SCHEMA_DIR', './schemas')
    tenant_id = args.tenant_id or os.getenv('API_KEY')
    poll_interval = args.poll_interval or int(os.environ.get('POLL_INTERVAL', 60))
    
    try:
        # Initialize and start processor
        processor = DocumentJobQueueProcessor(
            db_connection_string=connection_string,
            vector_db_path=vector_db_path,
            schema_dir=schema_dir,
            tenant_id=tenant_id,
            poll_interval=poll_interval
        )
        
        logger.info(f"Starting job queue processor with poll interval of {poll_interval} seconds")
        processor.start()
            
    except Exception as e:
        logger.error(f"Error running document job queue processor: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()