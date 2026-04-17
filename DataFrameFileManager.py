# dataframe_file_manager.py
import os
import pickle
import pandas as pd
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, List, Optional
import glob
from CommonUtils import rotate_logs_on_startup, get_log_path, get_app_path


# Configure logging
logger = logging.getLogger("DataFrameFileManager")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('DATAFRAME_FILE_MANAGER', get_log_path('dataframe_file_manager.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class DataFrameFileManager:
    def __init__(self, temp_dir: str = None):
        self.temp_dir = temp_dir or get_app_path('temp')
        # Create temp directory if it doesn't exist
        os.makedirs(self.temp_dir, exist_ok=True)
    
    def get_filepath(self, user_id: str, request_id: str = None, df_name: str = None) -> str:
        """Get the standard filepath for a user's DataFrame"""
        if not user_id:  # Likely a job and not from user interaction
            user_id = 0
            
        logger.info("Generating file name for DF...")
        try:
            parts = ["tmp_df", user_id]
            if request_id:
                parts.append(request_id)
            if df_name:
                parts.append(df_name)
            
            filename = "_".join(parts) + ".pkl"

            return os.path.join(self.temp_dir, filename)
        except Exception as e:
            print(f"Failed to generate file name - {str(e)}")
            logger.error(f"Failed to generate file name - {str(e)}")
            filename = "tmp_df_" + str(user_id)
            return os.path.join(self.temp_dir, filename)
    
    def save_dataframe(self, df: pd.DataFrame, user_id: str, request_id: str = None, 
                      df_name: str = None, cleanup_all: bool = False) -> str:
        """
        Save DataFrame to pickle file
        
        Args:
            df: DataFrame to save
            user_id: User identifier
            request_id: Optional request identifier
            df_name: Optional name for this specific DataFrame
            cleanup_all: If True, cleanup all user's DataFrames first
        """
        logger.info("Saving dataframe...")

        if not user_id:  # Likely a job and not from user interaction
            user_id = 0

        filepath = self.get_filepath(user_id, request_id, df_name)
        
        try:
            # Clean up all existing files first if requested
            if cleanup_all:
                self.cleanup(user_id, request_id)
            
            # Save the new DataFrame
            with open(filepath, 'wb') as f:
                pickle.dump(df, f)
            
            logger.info(f"Saved DataFrame to {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"Error saving DataFrame: {str(e)}")
            raise
    
    def load_and_delete(self, user_id: str, request_id: str = None, 
                       df_name: str = None) -> Optional[pd.DataFrame]:
        """Load DataFrame from pickle file and delete the file"""
        if not user_id:  # Likely a job and not from user interaction
            user_id = 0

        filepath = self.get_filepath(user_id, request_id, df_name)
        
        if not os.path.exists(filepath):
            return None
        
        try:
            # Load the DataFrame
            with open(filepath, 'rb') as f:
                df = pickle.load(f)
            
            # Delete the file immediately after loading
            os.remove(filepath)
            logger.info(f"Loaded and deleted DataFrame from {filepath}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error loading DataFrame: {str(e)}")
            return None
    
    def load_all_and_delete(self, user_id: str, request_id: str = None) -> Dict[str, pd.DataFrame]:
        """
        Load ALL DataFrames for a user/request and delete the files
        
        Returns:
            Dictionary with keys as df_names (or 'default' for unnamed) and values as DataFrames
        """
        if not user_id:  # Likely a job and not from user interaction
            user_id = 0

        dataframes = {}
        
        # Build pattern to match all relevant files
        if request_id:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}_{request_id}*.pkl")
        else:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}*.pkl")
        
        import glob
        for filepath in glob.glob(pattern):
            try:
                # Extract the DataFrame name from filepath
                filename = os.path.basename(filepath).replace('.pkl', '')
                parts = filename.split('_')
                
                # Determine the df_name
                if request_id:
                    # Format: tmp_df_userid_requestid_[dfname]
                    if len(parts) > 4:  # Has a df_name
                        df_name = '_'.join(parts[4:])
                    else:
                        df_name = 'default'
                else:
                    # Format: tmp_df_userid_[dfname]
                    if len(parts) > 3:  # Has a df_name
                        df_name = '_'.join(parts[3:])
                    else:
                        df_name = 'default'
                
                # Load the DataFrame
                with open(filepath, 'rb') as f:
                    df = pickle.load(f)
                
                dataframes[df_name] = df
                
                # Delete the file
                os.remove(filepath)
                logger.info(f"Loaded and deleted DataFrame '{df_name}' from {filepath}")
                
            except Exception as e:
                logger.error(f"Error loading {filepath}: {str(e)}")
                continue
        
        return dataframes
    
    def exists(self, user_id: str, request_id: str = None, df_name: str = None) -> bool:
        """Check if a DataFrame file exists"""
        filepath = self.get_filepath(user_id, request_id, df_name)
        return os.path.exists(filepath)
    
    def list_dataframes(self, user_id: str, request_id: str = None) -> List[str]:
        """List all DataFrame names for a user/request"""
        if not user_id:  # Likely a job and not from user interaction
            user_id = 0

        if request_id:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}_{request_id}*.pkl")
        else:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}*.pkl")
        
        import glob
        df_names = []
        for filepath in glob.glob(pattern):
            filename = os.path.basename(filepath).replace('.pkl', '')
            parts = filename.split('_')
            
            if request_id and len(parts) > 4:
                df_names.append('_'.join(parts[4:]))
            elif not request_id and len(parts) > 3:
                df_names.append('_'.join(parts[3:]))
            else:
                df_names.append('default')
        
        return df_names
    
    def cleanup(self, user_id: str, request_id: str = None):
        """Remove DataFrame files for a user/request"""
        if not user_id:  # Likely a job and not from user interaction
            user_id = 0

        if request_id:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}_{request_id}*.pkl")
        else:
            pattern = os.path.join(self.temp_dir, f"tmp_df_{user_id}*.pkl")
        
        import glob
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up DataFrame file: {filepath}")
            except Exception as e:
                logger.error(f"Error cleaning up file {filepath}: {str(e)}")
    
    def cleanup_all(self):
        """Clean up all temporary DataFrame files (useful for app startup)"""
        pattern = os.path.join(self.temp_dir, "tmp_df_*.pkl")
        import glob
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up: {filepath}")
            except Exception as e:
                logger.error(f"Error cleaning up {filepath}: {str(e)}")

# Create a global instance
#df_manager = DataFrameFileManager()