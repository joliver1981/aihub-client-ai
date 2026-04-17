import logging
from logging.handlers import RotatingFileHandler
import os
import config as cfg

def setup_logger(name, log_file, level=logging.DEBUG):
    """Function to set up a logger with a specific file handler"""
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Check if logger already has handlers to avoid duplicates
    if logger.handlers:
        return logger
    
    # Create directory for log file if it doesn't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Create rotating file handler
    handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=int(cfg.LOG_MAX_BYTES),  # 10 MB per file
        backupCount=int(cfg.LOG_BACKUP_COUNT),          # Keep 5 backup files
        encoding='utf-8'
    )
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s')
    handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(handler)
    
    return logger

# Set up individual loggers for different components
main_logger = setup_logger('main', cfg.LOG_DIR)
data_logger = setup_logger('data_agent', cfg.LOG_DIR_DATA)
agent_logger = setup_logger('agent', cfg.LOG_DIR_AGENT)
doc_queue_logger = setup_logger('doc_queue', cfg.DOC_JOB_QUEUE_LOG)