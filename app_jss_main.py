#!/usr/bin/env python3
"""
Main entry point for the Job Scheduler Service.
This script starts the scheduler service that manages scheduled jobs.
"""
import os
import sys
import time
import signal
import argparse
import logging
from logging.handlers import WatchedFileHandler
from job_scheduler import JobSchedulerService

from CommonUtils import get_db_connection_string, get_base_url, get_log_path


# Configure logging
logger = logging.getLogger("SchedulerService")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('JOB_SCHEDULER_LOG', get_log_path('job_scheduler_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


# Global reference to the scheduler service
scheduler_service = None

def signal_handler(sig, frame):
    """Handle termination signals"""
    global scheduler_service
    logger.info(f"Signal {sig} received, shutting down...")
    if scheduler_service:
        scheduler_service.stop()
    sys.exit(0)

def main():
    """Main entry point for the scheduler service"""
    parser = argparse.ArgumentParser(description='Job Scheduler Service')
    
    # Database connection
    parser.add_argument('--connection-string', type=str, 
                        help='SQL Server connection string')
    
    # API settings
    parser.add_argument('--api-base-url', type=str, 
                        help='Base URL for the application API')
    
    # Tenant settings
    parser.add_argument('--tenant-id', type=str,
                        help='Tenant ID for multi-tenant environments')
    
    # Scheduler settings
    parser.add_argument('--poll-interval', type=int, default=60,
                        help='How often to check for new/modified jobs (seconds)')
    parser.add_argument('--thread-pool-size', type=int, default=20,
                        help='Size of the thread pool for job execution')
    parser.add_argument('--process-pool-size', type=int, default=5,
                        help='Size of the process pool for job execution')
    
    # Parse arguments
    args = parser.parse_args()
    
    # Get connection string from environment if not provided
    connection_string = args.connection_string or get_db_connection_string() # os.environ.get('DB_CONNECTION_STRING')
    if not connection_string:
        logger.error("Database connection string must be provided via --connection-string or DB_CONNECTION_STRING environment variable")
        sys.exit(1)
    
    # Get API base URL from environment if not provided
    api_base_url = args.api_base_url or get_base_url() # os.environ.get('API_BASE_URL', 'http://localhost:5000')
    
    # Get tenant ID from environment if not provided
    tenant_id = args.tenant_id or os.getenv('API_KEY')
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Create and start the scheduler service
        global scheduler_service
        scheduler_service = JobSchedulerService(
            db_connection_string=connection_string,
            api_base_url=api_base_url,
            tenant_id=tenant_id,
            poll_interval=args.poll_interval,
            thread_pool_size=args.thread_pool_size,
            process_pool_size=args.process_pool_size
        )
        
        logger.info(f"Starting scheduler service with poll interval of {args.poll_interval} seconds")
        scheduler_service.start()
        
    except Exception as e:
        logger.error(f"Error running scheduler service: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()