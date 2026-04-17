import os
import pandas as pd
import config as cfg
from openai import OpenAI, AzureOpenAI
import pyodbc
import re
from datetime import date
from datetime import datetime
import uuid

#from langchain.agents import *
#from langchain.llms import OpenAI
#from langchain.sql_database import SQLDatabase
# from langchain.chat_models import AzureChatOpenAI
#from langchain.agents.agent_toolkits import SQLDatabaseToolkit
#from langchain.agents import create_sql_agent

import data_config as dcfg
from collections import defaultdict
import json
import datetime
import logging
from logging.handlers import WatchedFileHandler
import paramiko
import win32com.client
import winrm
import requests
from DataUtils import Get_Users, select_all_agents_and_connections, replace_connection_placeholders, get_database_connection_string, select_all_database_connections
from azure.communication.sms import SmsClient
import py_compile
import yaml

# Email communication
from azure.communication.email import EmailClient
from azure.core.exceptions import AzureError
from typing import List, Optional, Union, Dict, Any
from pathlib import Path

import csv
import io
import time
from request_tracking import RequestTracking

from CommonUtils import AnthropicProxyClient, rotate_logs_on_startup, get_log_path
from api_keys_config import get_openai_config
import fitz
import re


# =============================================================================
# ANTHROPIC CLAUDE SUPPORT  (via Cloud API proxy — no local anthropic SDK needed)
# =============================================================================
_anthropic_proxy = None


def _get_anthropic_proxy():
    """Lazy-initialize and return the shared AnthropicProxyClient."""
    global _anthropic_proxy
    if _anthropic_proxy is None:
        _anthropic_proxy = AnthropicProxyClient()
    return _anthropic_proxy


def _anthropic_quick_prompt(prompt, system="You are an assistant.", temp=0.0, model=None, max_tokens=None):
    """
    Internal helper: call the Anthropic Claude API via the Cloud API proxy
    (AnthropicProxyClient.messages_create) and return a plain string response.

    Uses the same proxy infrastructure as document processing — calls the
    Cloud API over HTTP with built-in retry logic.  No local anthropic SDK
    required.

    Mirrors the output contract of the OpenAI quick-prompt functions.
    """
    if model is None:
        model = cfg.ANTHROPIC_ADVANCED  # default to advanced model
    if max_tokens is None:
        max_tokens = int(cfg.ANTHROPIC_MAX_TOKENS or 4096)

    proxy = _get_anthropic_proxy()
    messages = [{"role": "user", "content": prompt}]

    response = proxy.messages_create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        temperature=temp
    )

    # Proxy returns a dict (Anthropic API JSON structure)
    if isinstance(response, dict):
        if "error" in response:
            raise RuntimeError(f"Anthropic proxy error: {response['error']}")
        response_text = response["content"][0]["text"]
    else:
        # Direct anthropic response object (unlikely in proxy mode, but safe)
        response_text = response.content[0].text

    response_text = str(response_text)
    response_text = response_text.replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')
    return response_text


# Patterns to mask in log messages - add new patterns here as needed
LOG_MASK_PATTERNS = [
    (r'api_key=[A-Fa-f0-9\-]{36}', 'api_key=***MASKED***'),
    (r'api_key=[A-Fa-f0-9\-]+', 'api_key=***MASKED***'),
    (r'API_KEY=[A-Fa-f0-9\-]+', 'API_KEY=***MASKED***'),
    (r'password=[^\s&]+', 'password=***MASKED***'),
    (r'pwd=[^\s&;]+', 'pwd=***MASKED***'),
    (r'PWD=[^\s&;]+', 'PWD=***MASKED***'),
]

def mask_sensitive_data(message):
    """Mask sensitive data in log messages."""
    if not message:
        return message
    for pattern, replacement in LOG_MASK_PATTERNS:
        message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)
    return message

# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("AppUtils")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('APP_UTILS_LOG', get_log_path('app_utils_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('APP_UTILS_LOG', get_log_path('app_utils_log.txt')))

logger = setup_logging()


# =============================================================================
# NOTIFICATION CLIENT INTEGRATION
# =============================================================================
# TODO: Eventually make this the only notification method
_USE_CLOUD_NOTIFICATIONS = bool(os.getenv('AI_HUB_API_URL'))
_CLOUD_NOTIFICATIONS_AVAILABLE = False

if _USE_CLOUD_NOTIFICATIONS:
    try:
        from notification_client import (
            send_email_notification as _cloud_send_email,
            sms_text_message_alert as _cloud_sms_alert,
            aihub_phone_call_alert as _cloud_phone_alert
        )
        _CLOUD_NOTIFICATIONS_AVAILABLE = True
        logging.info("Cloud notifications enabled")
    except ImportError as e:
        logging.warning(f"Cloud notification client not available: {e}")
# =============================================================================

# logging.basicConfig(filename=cfg.LOG_DIR, level=logging.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

AZURE_OPENAI_BASE_URL = cfg.AZURE_OPENAI_BASE_URL
AZURE_OPENAI_API_KEY = cfg.AZURE_OPENAI_API_KEY
AZURE_OPENAI_DEPLOYMENT_NAME = cfg.AZURE_OPENAI_DEPLOYMENT_NAME

db_user = cfg.DATABASE_UID
db_password = cfg.DATABASE_PWD
db_host = cfg.DATABASE_SERVER
db_name = cfg.DATABASE_NAME
db_driver = cfg.DB_DRIVER

database_server = cfg.DATABASE_SERVER
database_name = cfg.DATABASE_NAME
username = cfg.DATABASE_UID
password = cfg.DATABASE_PWD

# Azure OpenAI utilities — client factory for v1.x SDK
def _create_openai_client(config):
    """Create the appropriate OpenAI client based on config from get_openai_config()."""
    if config['api_type'] == 'open_ai':
        return OpenAI(api_key=config['api_key'])
    else:
        return AzureOpenAI(
            api_key=config['api_key'],
            api_version=config['api_version'],
            azure_endpoint=config['api_base']
        )


def set_user_request_id(module_name, request_id=None):
    try:
        # Generate or extract request ID
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Set in Flask's g object - this is globally accessible for this request only
        RequestTracking.set_tracking(request_id, module_name)
    except Exception as e:
        print(f"Error setting user request id: {str(e)}")

def get_db_connection():
    """Create and return a connection to the database"""
    return pyodbc.connect(f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}")

def get_db_connection_string():
    """Create and return a connection to the database"""
    return f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"

class CloudDatabaseConnection:
    def __init__(self):
        self.conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

    def execute_query(self, query, params=None):
        try:
            # Replace this with your actual database connection code
            connection = self.conn
            cursor = connection.cursor()

            # RLS
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            # If it's a SELECT query
            if query.strip().upper().startswith('SELECT'):
                result = cursor.fetchall()
            # If it's an UPDATE/INSERT/DELETE with RETURNING clause
            elif 'RETURNING' in query.upper():
                result = cursor.fetchall()
            else:
                result = []
            
            connection.commit()
            return result
            
        except Exception as e:
            print(f"Database error: {str(e)}")
            raise
        finally:
            if 'connection' in locals():
                connection.close()


class SQLLogHandler(logging.Handler):
    def __init__(self, job_id=0):
        super().__init__()
        self.job_id = job_id
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor()
        self.cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

    def set_job_id(self, job_id):
        self.job_id = job_id

    def _is_enabled(self):
        """Check TenantSettings for database_logging_enabled"""
        try:
            self.cursor.execute("""
                SELECT setting_value FROM TenantSettings 
                WHERE setting_key = 'database_logging_enabled'
            """)
            row = self.cursor.fetchone()
            return row and row[0].lower() in ('true', '1', 'yes')
        except:
            return False

    def emit(self, record):
        try:
            if self._is_enabled():
                if not self.conn:
                    self.conn = get_db_connection()
                    self.cursor = self.conn.cursor()
                    self.cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

                log_entry = mask_sensitive_data(self.format(record))
                self.cursor.execute('''
                    INSERT INTO app_log (created_at, level_name, message, module, func_name, line_no, job_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    ''', (record.created, record.levelname, log_entry, record.module, record.funcName, record.lineno, self.job_id))
                self.conn.commit()
        except:
            pass

    def close(self):
        self.conn.close()
        super().close()


def get_current_date_time():
    # Get the current date and time
    current_datetime = datetime.datetime.now()

    # Convert it to a string
    current_datetime_str = current_datetime.strftime("%Y-%m-%d %H:%M")

    return current_datetime_str


def get_current_date():
    return datetime.datetime.now().strftime('%Y-%m-%d')


def json_string_to_dict(json_string):
    try:
        # Use json.loads() to convert the JSON string to a dictionary
        data_dict = json.loads(json_string)
        return data_dict
    except json.JSONDecodeError as e:
        # Handle any JSON decoding errors
        print(f"Error decoding JSON: {e}")
        return json_string


def getUniqueID():
    return str(uuid.uuid1())


# Submit prompt to Azure OpenAI API
def azureChatPrompt(messages, use_alternate_api=False):
    """
    Submit messages to OpenAI/Azure OpenAI API.

    Supports BYOK (Bring Your Own Key), system OpenAI API, and Azure OpenAI.
    """
    config = get_openai_config(use_alternate_api=use_alternate_api)
    client = _create_openai_client(config)

    model = config['model'] if config['api_type'] == 'open_ai' else config['deployment_id']
    kwargs = {"messages": messages, "model": model}

    # Add reasoning for supported models
    if config.get('reasoning_effort'):
        kwargs["reasoning_effort"] = config['reasoning_effort']

    chat_completion = client.chat.completions.create(**kwargs)
    return chat_completion


def quickPrompt(prompt, system="You are an assistant.", use_alternate_api=False, temp=0.0, provider="openai"):
    """
    Quick single-prompt helper for OpenAI/Azure OpenAI API.

    Supports BYOK (Bring Your Own Key), system OpenAI API, and Azure OpenAI.

    Args:
        provider: "openai" (default) or "anthropic" to use Claude models.
                  When "anthropic", uses ANTHROPIC_ADVANCED model.
    """
    if provider == "anthropic":
        return _anthropic_quick_prompt(prompt, system=system, temp=temp, model=cfg.ANTHROPIC_ADVANCED)

    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    config = get_openai_config(use_alternate_api=use_alternate_api)
    client = _create_openai_client(config)

    model = config['model'] if config['api_type'] == 'open_ai' else config['deployment_id']
    kwargs = {"messages": messages, "model": model}

    # Reasoning models: use reasoning param and require temperature=1.0
    if config.get('reasoning_effort'):
        kwargs["reasoning_effort"] = config['reasoning_effort']
        kwargs["temperature"] = 1.0
    else:
        kwargs["temperature"] = temp

    chat_completion = client.chat.completions.create(**kwargs)
    response = str(chat_completion.choices[0].message.content)
    return response


def azureQuickPrompt(prompt, system="You are an assistant.", use_alternate_api=False, temp=0.0, provider="openai"):
    """
    Args:
        provider: "openai" (default) or "anthropic" to use Claude models.
                  When "anthropic", uses ANTHROPIC_ADVANCED model.
    """
    if provider == "anthropic":
        return _anthropic_quick_prompt(prompt, system=system, temp=temp, model=cfg.ANTHROPIC_ADVANCED)

    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    config = get_openai_config(use_alternate_api=use_alternate_api)
    client = _create_openai_client(config)

    model = config['model'] if config['api_type'] == 'open_ai' else config['deployment_id']
    kwargs = {"messages": messages, "model": model}

    if config.get('reasoning_effort'):
        kwargs["reasoning_effort"] = config['reasoning_effort']
        kwargs["temperature"] = 1.0
    else:
        kwargs["temperature"] = temp

    chat_completion = client.chat.completions.create(**kwargs)
    response = str(chat_completion.choices[0].message.content)
    response = response.replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')
    return response

def azureMiniQuickPrompt(prompt, system="You are an assistant.", temp=0.0, provider="openai"):
    """
    Args:
        provider: "openai" (default) or "anthropic" to use Claude models.
                  When "anthropic", uses ANTHROPIC_MINI (Sonnet) model.
    """
    if provider == "anthropic":
        return _anthropic_quick_prompt(prompt, system=system, temp=temp, model=cfg.ANTHROPIC_MINI)

    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    config = get_openai_config(use_alternate_api=False, use_mini=True)
    client = _create_openai_client(config)

    model = config['model'] if config['api_type'] == 'open_ai' else config['deployment_id']
    kwargs = {"messages": messages, "model": model}

    if config.get('reasoning_effort'):
        kwargs["reasoning_effort"] = config['reasoning_effort']
        kwargs["temperature"] = 1.0
    else:
        kwargs["temperature"] = temp

    chat_completion = client.chat.completions.create(**kwargs)
    response = str(chat_completion.choices[0].message.content)
    response = response.replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')
    return response

def azureMiniQuickPromptV2(prompt, system="You are an assistant.", temp=0.0):
    """
    Simplified client function to get responses from the mini model API.
    
    Args:
        prompt (str): The prompt to send to the model
        system (str, optional): The system message
        
    Returns:
        str: The AI's response or error message
    """
    try:
        # API endpoint
        api_url = os.getenv('AI_HUB_API_URL') + os.getenv('AI_HUB_PROMPT_MINI')
        
        # Get API key from environment
        api_key = os.getenv('API_KEY')
        
        if not api_key:
            return "Error: API key not found in environment variables"
        
        # Prepare the request
        request_data = {
            "prompt": prompt,
            "system": system,
            "api_key": api_key,
            "reasoning": {
                "effort": cfg.MINI_MODEL_REASONING_EFFORT,  # low, medium, or high
                "summary": "auto"
            }
        }
        print(f"Sending request to {api_url}")

        # Send the request
        response = requests.post(api_url, json=request_data)

        print(f"Raw response: {response}")
        
        # Check for HTTP errors
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}"
        
        print('Parsing response...')

        # Parse the response
        result = response.json()

        print(f'Raw response result: {result}')
        
        # Return just the response text or error
        if "error" in result:
            return f"Error: {result['error']}"
        
        return result["response"]
        
    except Exception as e:
        return f"Error: {str(e)}"

def azureQuickPromptMini(prompt, system="You are an assistant."):
    """
    Simplified client function to get responses from the mini model API.
    
    Args:
        prompt (str): The prompt to send to the model
        system (str, optional): The system message
        
    Returns:
        str: The AI's response or error message
    """
    try:
        # API endpoint
        api_url = os.getenv('AI_HUB_API_URL') + os.getenv('AI_HUB_PROMPT_MINI')
        
        # Get API key from environment
        api_key = os.getenv('API_KEY')
        
        if not api_key:
            return "Error: API key not found in environment variables"
        
        # Prepare the request
        request_data = {
            "prompt": prompt,
            "system": system,
            "api_key": api_key,
            "reasoning": {
                "effort": cfg.MINI_MODEL_REASONING_EFFORT,  # low, medium, or high
                "summary": "auto"
            }
        }
        
        # Send the request
        response = requests.post(api_url, json=request_data)
        
        # Check for HTTP errors
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}"
        
        # Parse the response
        result = response.json()
        
        # Return just the response text or error
        if "error" in result:
            return f"Error: {result['error']}"
        
        return result["response"]
        
    except Exception as e:
        return f"Error: {str(e)}"


# Establish a connection to SQL Server
conn = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
)

connLLMDB = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={db_name};UID={username};PWD={password}"
)

# llm = AzureChatOpenAI(openai_api_base=AZURE_OPENAI_BASE_URL,
#                         openai_api_version="2023-05-15",
#                         deployment_name=AZURE_OPENAI_DEPLOYMENT_NAME,
#                         openai_api_key=AZURE_OPENAI_API_KEY,
#                         openai_api_type="azure")

def _set_target_database_connection(connection_string):
    try:
        print(86 * '-')
        print(86 * '*')
        print('Set Target Connection String with =-=-=-=->>>>', connection_string)
        print(86 * '*')
        print(86 * '-')

        global connLLMDB

        # Set the connection
        connLLMDB = pyodbc.connect(connection_string)
        return True
    except Exception as e:
        print(str(e))
        return False


############################################################################
##### WARNING: This is used by the NLQ engine to query local databases #####
############################################################################
def execute_sql_query(query, connection_string):
    try:
        # Set target connection
        success = _set_target_database_connection(connection_string)

        if not success:
            raise('Failed to set the target database connection...')
        else:
            print(86 * '=')
            print(86 * '=')
            print('Connection String:', connection_string)
            print('Executing query:', query)
            print(86 * '=')
            print(86 * '=')

        # Execute the SQL query and fetch the results into a Pandas DataFrame
        result_df = pd.read_sql_query(query, connLLMDB)

        # Close the database connection
        #connLLMDB.close()

        return result_df
    except Exception as e:
        print("Error:", str(e))
        return None
    
def execute_sql_query_v2_legacy(query, connection_string):
    """
    Execute a SQL query and return either the result DataFrame or an error tuple.
    
    Args:
        query (str): The SQL query to execute
        connection_string (str): The connection string for the database
        
    Returns:
        tuple: (result_df, error_message)
            - If successful: (pandas.DataFrame, None)
            - If failed: (None, str)
    """
    try:
        # Set target connection
        success = _set_target_database_connection(connection_string)

        if not success:
            print("Failed to set the target database connection")
            return None, "Failed to set the target database connection"
        else:
            print(86 * '=')
            print(86 * '=')
            print('Connection String:', connection_string)
            print('Executing query:', query)
            print(86 * '=')
            print(86 * '=')

        # Execute the SQL query and fetch the results into a Pandas DataFrame
        result_df = pd.read_sql_query(query, connLLMDB)

        # Return successful result with no error
        return result_df, None
        
    except Exception as e:
        error_message = str(e)
        print("Error in execute_sql_query_v2:", error_message)
        
        # Return no dataframe but include the error message
        return None, error_message
    
    
def execute_sql_query_v2(query, connection_string):
    """
    Execute a SQL query and return either the result DataFrame or an error tuple.
    
    Args:
        query (str): The SQL query to execute
        connection_string (str): The connection string for the database
        
    Returns:
        tuple: (result_df, error_message)
            - If successful: (pandas.DataFrame, None)
            - If failed: (None, str)
    """
    conn = None
    try:
        # Create a new connection for this query (no global variable)
        conn = pyodbc.connect(connection_string, timeout=30)
        
        # Execute the SQL query and fetch the results into a Pandas DataFrame
        result_df = pd.read_sql_query(query, conn)
        
        # Return successful result with no error
        return result_df, None
        
    except pyodbc.Error as e:
        error_msg = f"Database error: {str(e)}"
        print(error_msg)
        return None, error_msg
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(error_msg)
        return None, error_msg
        
    finally:
        # Always close the connection
        if conn:
            try:
                conn.close()
            except:
                pass


def ExecuteSQLServerQueryV2(sql_query):
    try:
        df = pd.read_sql_query(sql_query, conn)
        return df
    except Exception as e:
        print(f"Error: {str(e)}")
        logging.error('Error:' + str(e))
        return None
    finally:
        #conn.close()
        pass


def ExecuteSQLServerQueryV3(sql_query, connection=None):
    try:
        if connection is None:
            df = pd.read_sql_query(sql_query, conn)
        else:
            df = pd.read_sql_query(sql_query, connection)

        return df
    except Exception as e:
        print(f"Error: {str(e)}")
        logging.error('Error:' + str(e))
        return None
    finally:
        #conn.close()
        pass


def ExecuteSQLServerQuery(sql_query):
    try:
        # Create a cursor object to interact with the database
        cursor = conn.cursor()
        
        # Execute the SQL query
        cursor.execute(sql_query)
        
        # Fetch and return the results (if any)
        results = cursor.fetchall()
        
        return results

    except Exception as e:
        print(f"Error: {str(e)}")
        return None
    finally:
        # Close the cursor and connection
        cursor.close()
        #conn.close()


def ExecuteSQLServerQueryWithParams(sql_query, params):
    try:
        # Create a cursor object to interact with the database
        cursor = conn.cursor()
        
        # Execute the SQL query
        cursor.execute(sql_query, params)
        
        # Fetch and return the results (if any)
        results = cursor.fetchall()
        
        return results

    except Exception as e:
        print(f"Error: {str(e)}")
        return None
    finally:
        # Close the cursor and connection
        cursor.close()


def ExecuteSQLServerQueryWithNoResults(sql_query, server_creds=None):
    try:
        if server_creds is None:
            # Create a cursor object to interact with the default AI database
            cursor = conn.cursor()
            
            # Execute the SQL query
            cursor.execute(sql_query)

            conn.commit()

            cursor.close()
        else:
            # Create a new server connection
            # Establish a connection to SQL Server
            conn_temp = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={server_creds['DB_SERVER']};DATABASE={server_creds['DB_NAME']};UID={server_creds['DB_USER']};PWD={server_creds['DB_PWD']}"
            )

            cursor = conn_temp.cursor()
            
            # Execute the SQL query
            cursor.execute(sql_query)

            conn_temp.commit()

            cursor.close()

        return True
    except Exception as e:
        print(f"Error: {str(e)}")
        return False


def execute_stored_procedure(procedure_name, parameters):
    try:
        # Create a cursor to execute SQL commands
        cursor = conn.cursor()
        
        # Build the stored procedure call
        sql = f"EXEC {procedure_name} "
        #if parameters:
            # Add parameters to the SQL statement
            #sql += ", ".join(['@' + param for param in parameters.keys()])

        if parameters:
            sql += ", ".join(['@' + param + '=?' for param in parameters.keys()])
        
        print(86 * '=')
        print(sql)
        print(str(list(parameters.values())))
        print(86 * '=')
        # Execute the stored procedure
        if parameters:
            cursor.execute(sql, list(parameters.values()))
        else:
            cursor.execute(sql)
        
        # Commit the transaction
        conn.commit()
        
        # Close the cursor and connection
        cursor.close()
        conn.close()
        
        print(f"Stored procedure '{procedure_name}' executed successfully.")
    
    except pyodbc.Error as e:
        print(f"Error executing stored procedure: {str(e)}")


def ExecuteSQLJob(job_name):
    try:
        logging.info('Executing job: ' + str(job_name.strip()))
        sql = "EXEC RunJob_BI '" + job_name.strip() + "'"
        logging.info('SQL:' + str(sql))
        print(sql)
        ExecuteSQLServerQueryWithNoResults(sql, server_creds=cfg.BI_JOB_SQL_SERVER)
        logging.info('SQL Executed Successfully')
        return True
    except Exception as e:
        logging.error(str(e))
        print(str(e))
        return False
    

def list_sftp_files(server, username, password, folder):
    # Create a transport object
    transport = paramiko.Transport((server, 22))

    # Authenticate with the server
    transport.connect(username=username, password=password)

    # Create an SFTP client
    sftp = paramiko.SFTPClient.from_transport(transport)

    # Change to the specified directory
    sftp.chdir(folder)

    # Get the file attributes
    files = sftp.listdir_attr()

    file_info = []
    file_report = '| # | File Name | Size | Creation Date/Time | Age (in minutes) |' + '\n'
    for index, file in enumerate(files):
        info = {
            'filename': file.filename,
            'size': file.st_size,
            'creation_time': datetime.datetime.fromtimestamp(file.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'age_in_minutes': difference_in_minutes(str(datetime.datetime.fromtimestamp(file.st_mtime).strftime('%Y-%m-%d %H:%M')), get_current_date_time()),
            #'modification_time': datetime.datetime.fromtimestamp(file.st_mtime).strftime('%Y-%m-%d'),
            #'permissions': file.st_mode,
        }
        file_info.append(info)
        file_report += '| ' + str(index+1) + ' | ' + file.filename + ' | ' + str(file.st_size) + ' | ' + str(datetime.datetime.fromtimestamp(file.st_mtime).strftime('%Y-%m-%d %H:%M')) + ' | ' + str(difference_in_minutes(str(datetime.datetime.fromtimestamp(file.st_mtime).strftime('%Y-%m-%d %H:%M')), get_current_date_time())) + ' |' + '\n'

    # Close the SFTP client
    sftp.close()

    # Close the transport
    transport.close()

    return file_report
    

def GetSQLJobStatus(jobs):
    #report_string = 'Job Name' + '\t' + 'Status' + '\t' + 'Run Date' + '\t' + 'Run Time' + '\t' + 'Duration'
    df = None
    for job_name in jobs:
        sql = dcfg.SQL_SELECT_JOB_STATUS.replace('{job_name}', job_name)
        print(sql)
        df = ExecuteSQLServerQueryV2(sql)
        #for index, row in df.iterrows():
            #report_string += row['Job_Name'] + '\t' + row['Status'] + '\t' + row['Run_Date'] + '\t' + row['Run_Time'] + '\t' + row['Run_Duration']
    return df


def GetSQLJobStatusString(jobs):
    report_string = 'Job Name' + '\t' + 'Status' + '\t' + 'Run Date' + '\t' + 'Run Time' + '\t' + 'Duration' + '\n'
    df = None
    sql = dcfg.SQL_SELECT_JOB_STATUS_ALL.replace('{job_name}', jobs)
    print(sql)
    df = ExecuteSQLServerQueryV2(sql)
    for index, row in df.iterrows():
        report_string += row['Job_Name'] + '\t' + row['Status'] + '\t' + row['Run_Date'] + '\t' + row['Run_Time'] + '\t' + row['Run_Duration'] + '\n'
    return report_string


def GetBISQLJobStatusString():
    return GetSQLJobStatusString(dcfg.SQL_SELECT_IN_JOBS_BI)


def GetSQLJobStatusHTML(jobs):
    df = None
    final_html = f'Report Date/Time: {get_current_date_time()} <br><br>'
    logging.info('Executing GetSQLJobStatusHTML...')
    for job_name in jobs:
        sql = dcfg.SQL_SELECT_JOB_STATUS.replace('{job_name}', str(job_name).strip())
        print(sql)
        logging.info('SQL:' + str(sql))
        df = ExecuteSQLServerQueryV2(sql)
        html = df.to_html()
        final_html += job_name + ':<br>' + html + '<br><br>'

    return final_html


def list_to_string(input_list, separator=", "):
    """
    Converts a list of elements to a string with elements separated by the specified separator.
    
    Args:
        input_list (list): The list to be converted to a string.
        separator (str, optional): The separator to be used between list elements. Default is space (" ").
    
    Returns:
        str: The list elements as a single string.
    """
    # Use the join() method to concatenate list elements into a string
    try:
        result_string = separator.join(map(str, input_list))
    except Exception as e:
        print(str(e))
        return input_list
    return result_string


def GetExactJobNames(job_names):
    from system_prompts import AI_MONITOR_GETJOB_NAMES_SYSTEM, AI_MONITOR_GETJOB_NAMES_PROMPT
    system = AI_MONITOR_GETJOB_NAMES_SYSTEM.replace('{job_status}', GetBISQLJobStatusString())
    prompt = AI_MONITOR_GETJOB_NAMES_PROMPT.replace('{user_jobs}', list_to_string(job_names))
    response = azureQuickPrompt(prompt, system=system, use_alternate_api=False)
    return response


def query_folder(folder_path):
    file_list = []
    
    # List files in the specified folder
    files = os.listdir(folder_path)
    
    # Iterate through the files in the folder
    for filename in files:
        file_path = os.path.join(folder_path, filename)
        
        # Check if it's a file (not a directory)
        if os.path.isfile(file_path):
            # Get file attributes
            file_stat = os.stat(file_path)
            file_size = file_stat.st_size
            create_time = pd.to_datetime(file_stat.st_mtime, unit='s')
            modify_time = pd.to_datetime(file_stat.st_mtime, unit='s')
            
            # Append file attributes to the list
            file_list.append([filename, file_path, file_size, create_time, modify_time])
    
    # Create a Pandas DataFrame from the list
    df = pd.DataFrame(file_list, columns=['File Name', 'File Path', 'File Size (bytes)', 'Create Time', 'Modify Time'])
    
    return df


def search_text_files_deprecated(folder_path, search_string, extensions):
    found_files = []
    search_string_lower = search_string.lower()  # Convert search string to lowercase

    logging.debug('-----------------------------------------------------')
    logging.debug('Folder Path:' + str(folder_path))
    logging.debug('-----------------------------------------------------')

    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    # Loop through all files in the folder
    for filename in os.listdir(folder_path):
        if any(filename.endswith(ext) for ext in extensions):  # Check if the file has a valid extension
            file_path = os.path.join(folder_path, filename)
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read().lower()  # Convert file content to lowercase
                if search_string_lower in content:
                    print(filename)
                    found_files.append(filename)

    found_files_string = ''
    for index, file in enumerate(found_files):
        if index == 0:
            found_files_string += str(file)
        else:
            found_files_string += ',' + str(file)

    return found_files


def normalize_path(path):
    # Normalize the slashes by replacing multiple backslashes with a single backslash
    path = re.sub(r'\\+', r'\\', path)
    
    # If it's a network path starting with \\, ensure we preserve the leading double backslashes
    if path.startswith('\\\\'):
        # Remove leading backslashes to avoid them being lost during normalization
        path = path[2:]
        normalized_path = '\\\\' + os.path.normpath(path)
    # If it's a Windows drive letter path, ensure it handles it correctly
    elif re.match(r'^[a-zA-Z]:\\', path):
        drive, rest = path.split(':\\', 1)
        normalized_path = drive.upper() + ':\\' + os.path.normpath(rest)
    else:
        # Normalize the rest of the path
        normalized_path = os.path.normpath(path)

    #normalized_path = normalized_path.replace('\\', '\\\\')

    #if str(normalized_path).startswith('\\\\') and not str(normalized_path).startswith('\\\\\\\\'):
        #normalized_path = '\\\\' + normalized_path

    if str(normalized_path).startswith('\\') and not str(normalized_path).startswith('\\\\'):
        normalized_path = '\\' + normalized_path

    return normalized_path


def search_text_files(folder_path, search_text, extensions):
    results = {}

    print('Input path:', folder_path)
    folder_path = normalize_path(folder_path)
    print('Normalized path:', folder_path)

    # Walk through all files in the folder
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            # Check if the file has the right extension
            if any(file.endswith(ext) for ext in extensions):
                file_path = os.path.join(root, file)
                
                # Initialize counter for the occurrences in the current file
                occurrences = 0
                
                # Open and read the file
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            # Count occurrences of the search text in each line
                            occurrences += line.count(search_text)
                            
                    # If occurrences were found, add to results
                    if occurrences > 0:
                        file_date = datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')
                        results[file_path] = (occurrences, file_date)
                except Exception as e:
                    print(f"Could not read file {file_path}: {e}")

    # Generate the report
    report_lines = [f"Search results for '{search_text}' in folder '{folder_path}':\n"]
    if results:
        for file_path, (count, file_date) in results.items():
            report_lines.append(f"'{search_text}' found {count} times in {file_path} (Last modified: {file_date})")
    else:
        report_lines.append("No occurrences found.")
    
    report = "\n".join(report_lines)
    return report


def GetFolderStatusAsDF(folder_path):
    return query_folder(folder_path)


def GetFolderStatusAsString_OLD_SINGLE_FOLDER(folder_path):    
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    # Create a Pandas DataFrame from the list
    df = query_folder(folder_path)

    report_string = '| File Name' + ' | ' + 'File Path' + ' | ' + 'File Size (bytes)' + ' | ' + 'Create Time' + ' | ' + 'Modify Time' + ' |\n'
    #report_string += "|---|---|---|---|---|\n"
    for index, row in df.iterrows():
        report_string += '| ' + row['File Name'] + ' | ' + row['File Path'] + ' | ' + str(row['File Size (bytes)']) + ' | ' + str(row['Create Time']) + ' | ' + str(row['Modify Time']) + ' |\n'
    
    return report_string


def format_size(size_bytes):
    """Formats size in bytes to a more readable format (KB, MB, GB)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def get_folder_statistics(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    total_files = 0
    total_size = 0
    file_types_count = defaultdict(int)
    immediate_subfolders = set()
    
    for root, dirs, files in os.walk(folder_path):
        # Only process the specified folder and its immediate subfolders
        if root == folder_path:
            immediate_subfolders.update(dirs)  # Capture immediate subfolders
        elif os.path.dirname(root) == folder_path:
            for file_name in files:
                total_files += 1
                file_path = os.path.join(root, file_name)
                total_size += os.path.getsize(file_path)
                _, file_extension = os.path.splitext(file_name)
                file_types_count[file_extension.lower()] += 1
                
        # Do not dive deeper than the immediate subfolders
        dirs[:] = [d for d in dirs if os.path.dirname(os.path.join(root, d)) == folder_path]
    
    # Generate report
    report = f"Folder Path: {folder_path}\nTotal Files: {total_files}\nTotal Size: {format_size(total_size)}\nImmediate Subfolders: {len(immediate_subfolders)}\nFiles by Type:\n"
    for file_type, count in file_types_count.items():
        report += f"  {file_type if file_type else 'No Extension'}: {count}\n"
    
    if immediate_subfolders:
        report += "Subfolders List:\n"
        for subfolder in sorted(immediate_subfolders):
            report += f"  {subfolder}\n"
    
    return report


def get_folder_statistics_root_and_subfolders(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    subfolder_stats = defaultdict(lambda: {'files': 0, 'size': 0, 'types': defaultdict(int)})
    overall_stats = {'files': 0, 'size': 0, 'types': defaultdict(int)}
    
    # Initialize statistics for each immediate subfolder to ensure they are included in the report
    for entry in os.scandir(folder_path):
        if entry.is_dir():
            subfolder_stats[entry.name]

    for root, dirs, files in os.walk(folder_path, topdown=True):
        if root == folder_path:
            # Process files in the root folder
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_path)
                _, file_extension = os.path.splitext(file_name)
                overall_stats['files'] += 1
                overall_stats['size'] += file_size
                overall_stats['types'][file_extension.lower()] += 1
        elif os.path.dirname(root) == folder_path:
            # Process files in immediate subfolders
            subfolder_name = os.path.basename(root)
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_path)
                _, file_extension = os.path.splitext(file_name)
                subfolder_stats[subfolder_name]['files'] += 1
                subfolder_stats[subfolder_name]['size'] += file_size
                subfolder_stats[subfolder_name]['types'][file_extension.lower()] += 1
                
        # Limit to the immediate subfolders for further processing
        dirs[:] = [d for d in dirs if os.path.dirname(os.path.join(root, d)) == folder_path]

    # Generate report
    report = f"Folder Path: {folder_path}\n"
    report += f"Total Files in Root: {overall_stats['files']}\n"
    report += f"Total Size in Root: {format_size(overall_stats['size'])}\n"
    report += "Files by Type in Root:\n"
    for file_type, count in overall_stats['types'].items():
        report += f"  {file_type if file_type else 'No Extension'}: {count}\n"

    for subfolder, stats in subfolder_stats.items():
        report += f"\nSubfolder: {subfolder} - "
        report += f"Total Files: {stats['files']}, Total Size: {format_size(stats['size'])}, Files by Type:\n"
        if stats['files'] > 0:
            for file_type, count in stats['types'].items():
                report += f"    {file_type if file_type else 'No Extension'}: {count}\n"
        else:
            report += "    No files\n"

    return report


def get_folder_statistics_OLD(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    total_files = 0
    total_size = 0  # in kilobytes
    immediate_subfolders = set()

    for root, dirs, files in os.walk(folder_path):
        # Process only the specified folder and its immediate subfolders
        if root == folder_path or os.path.dirname(root) == folder_path:
            total_files += len(files)
            total_size += sum(os.path.getsize(os.path.join(root, file)) for file in files) / 1024
            immediate_subfolders.update(dirs)
        
        # Prevent diving deeper than immediate subfolders
        dirs.clear()

    report = (
        f"Folder Path: {folder_path}\n"
        f"Total Files: {total_files}\n"
        f"Total Size: {total_size:.2f} KB\n"
        f"Immediate Subfolders: {len(immediate_subfolders)}"
    )

    return report


def format_timestamp(ts):
    """Converts a timestamp into a human-readable format."""
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def GetFolderStatusAsString(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    report_lines = []
    report_string = '| File Name' + ' | ' + 'File Path' + ' | ' + 'File Size (KB)' + ' | ' + 'Create Time' + ' | ' + 'Modify Time' + ' |\n'
    for root, dirs, files in os.walk(folder_path):
        # Only process the specified folder and its immediate subfolders
        if root == folder_path or os.path.dirname(root) == folder_path:
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_path) / 1024 # Convert bytes to KB
                creation_time = format_timestamp(os.path.getctime(file_path))
                modify_time = format_timestamp(os.path.getmtime(file_path))
                
                # Append file info to the report
                report_lines.append(f"File Name: {file_name}, File Path: {file_path}, File Size: {file_size:.2f} KB, Creation Time: {creation_time}, Modify Time: {modify_time}")

                report_string += '| ' + file_name + ' | ' + file_path + ' | ' + str(file_size) + ' | ' + str(creation_time) + ' | ' + str(modify_time) + ' |\n'

        # Do not dive deeper than the immediate subfolders
        dirs[:] = [d for d in dirs if os.path.dirname(os.path.join(root, d)) == folder_path]

    return report_string


def GetFolderStatusAsStringSlim(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    report_lines = []
    report_string = '| File Name' + ' | ' + 'File Path' + ' | ' + 'File Size (KB)' + ' | ' + 'Create Date/Time' + ' |\n'
    for root, dirs, files in os.walk(folder_path):
        # Only process the specified folder and its immediate subfolders
        if root == folder_path or os.path.dirname(root) == folder_path:
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_path) / 1024 # Convert bytes to KB
                creation_time = format_timestamp(os.path.getctime(file_path))
                modify_time = format_timestamp(os.path.getmtime(file_path))
                
                # Append file info to the report
                report_lines.append(f"File Name: {file_name}, File Path: {file_path}, File Size: {file_size:.2f} KB, Creation Time: {creation_time}")

                report_string += '| ' + file_name + ' | ' + file_path + ' | ' + str(file_size) + ' | ' + str(creation_time) + ' |\n'

        # Do not dive deeper than the immediate subfolders
        dirs[:] = [d for d in dirs if os.path.dirname(os.path.join(root, d)) == folder_path]

    return report_string


def difference_in_minutes(date_str1, date_str2, date_format="%Y-%m-%d %H:%M"):
    """
    Calculates the difference between two dates in minutes, where the dates are given as strings.
    
    Parameters:
    - date_str1: A string representing the first date.
    - date_str2: A string representing the second date.
    - date_format: The format in which the date strings are provided. Default is "%Y-%m-%d %H:%M".
    
    Returns:
    - The difference between the dates in minutes as an integer.
    """
    # Parse the string dates into datetime objects
    date1 = datetime.datetime.strptime(date_str1, date_format)
    date2 = datetime.datetime.strptime(date_str2, date_format)
    
    # Calculate the difference between the two dates
    delta = date2 - date1
    
    # Convert the difference to minutes
    difference_minutes = delta.total_seconds() / 60
    
    return int(difference_minutes)


def GetSubfolderFilesAndAgeReport(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    report_lines = []
    report_string = '| File Name' + ' | ' + 'File Path' + ' | ' + 'Create Date/Time' + ' | ' + 'Age (minutes)' + ' |\n'
    for root, dirs, files in os.walk(folder_path):
        # Only process the specified folder and its immediate subfolders
        if root == folder_path or os.path.dirname(root) == folder_path:
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_size = os.path.getsize(file_path) / 1024 # Convert bytes to KB
                creation_time = format_timestamp(os.path.getctime(file_path))
                modify_time = format_timestamp(os.path.getmtime(file_path))
                age_of_file = difference_in_minutes(str(creation_time), get_current_date_time())
                
                # Append file info to the report
                report_lines.append(f"File Name: {file_name}, File Path: {file_path}, Creation Time: {creation_time}")

                report_string += '| ' + file_name + ' | ' + file_path + ' | ' + str(creation_time) + ' | ' + str(age_of_file) + ' |\n'

        # Do not dive deeper than the immediate subfolders
        dirs[:] = [d for d in dirs if os.path.dirname(os.path.join(root, d)) == folder_path]

    return report_string


def GetFilesAndAgeReport(folder_path):
    # FIX for network paths as the AI will modify the folder path and not allow "\\\\"
    if str(folder_path).startswith('\\') and not str(folder_path).startswith('\\\\'):
        folder_path = '\\' + folder_path

    report_string = '| File Name' + ' | ' + 'Create Date/Time' + ' | ' + 'Age (minutes)' + ' |\n'

    # Only process the specified folder
    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path) / 1024 # Convert bytes to KB
            creation_time = format_timestamp(os.path.getctime(file_path))
            modify_time = format_timestamp(os.path.getmtime(file_path))
            age_of_file = difference_in_minutes(str(creation_time), get_current_date_time())

            report_string += '| ' + file_name + ' | ' + str(creation_time) + ' | ' + str(age_of_file) + ' |\n'

    return report_string


def SendEmailAlert(email_to, subject, message):
    email_to = str(email_to).replace(",",";")
    subject = str(subject).replace("'",'"')
    message = str(message).replace("'",'"')
    sql_string = f"""EXEC msdb.dbo.sp_send_dbmail @profile_name = 'BI_ALERTS', @recipients = '{email_to}', @subject = '{subject}', @body = '{message}' 
                    """
    _ = ExecuteSQLServerQueryWithNoResults(sql_string)


def create_task_scheduler_folder(folder_name=cfg.WINTASK_FOLDER):
    try:
        # Connect to the Task Scheduler service
        print('Connecting to scheduler service...')
        scheduler = win32com.client.Dispatch('Schedule.Service')
        scheduler.Connect()

        # Get the root folder
        print('Getting root folder...')
        root_folder = scheduler.GetFolder('\\')

        # Check if the folder already exists
        try:
            print(f'Checking folder: {folder_name}')
            new_folder = root_folder.GetFolder(f'{folder_name}')
            print(f"Folder '{folder_name}' already exists.")
        except:
            # Create the new folder
            new_folder = root_folder.CreateFolder(f'{folder_name}')
            print(f"Folder '{folder_name}' created successfully.")
    except Exception as e:
        print(str(e))


def add_quickjob_task(job_id, task_name, start_time, frequency, enabled):
    try:
        print('Checking task folder...')
        create_task_scheduler_folder()
        
        print('Adding Windows task...')
        TASK_CREATE_OR_UPDATE = 6
        TASK_TIME_TRIGGER_DAILY = 2
        TASK_TIME_TRIGGER_HOURLY = 5
        TASK_TIME_TRIGGER_WEEKLY = 3

        print('Connecting to scheduler service...')
        scheduler = win32com.client.Dispatch('Schedule.Service')
        scheduler.Connect()

        print(f'Getting folder: {cfg.WINTASK_FOLDER}')
        root_folder = scheduler.GetFolder(cfg.WINTASK_FOLDER)

        print('New task...')
        task_def = scheduler.NewTask(0)

        print('Setting date/time...')
        # Convert string to datetime object
        date_format = "%Y-%m-%d %H:%M"
        datetime_object = datetime.datetime.strptime(start_time, date_format)

        print('Creating trigger...')
        # Create trigger
        frequency = str(frequency).lower()
        start_time = datetime_object.strftime('%Y-%m-%dT%H:%M:%S')
        if frequency == 'daily':
            trigger = task_def.Triggers.Create(TASK_TIME_TRIGGER_DAILY)
            trigger.DaysInterval = 1
        elif frequency == 'hourly':
            trigger = task_def.Triggers.Create(TASK_TIME_TRIGGER_DAILY)
            #trigger.MinutesInterval = 60
            trigger.DaysInterval = 1  # Every day
            trigger.Repetition.Interval = 'PT1H'  # Every hour
            trigger.Repetition.Duration = 'PT12H'  # Duration of 12 hours
        elif frequency == 'weekly':
            trigger = task_def.Triggers.Create(TASK_TIME_TRIGGER_DAILY)
            #trigger.WeeksInterval = 1
            trigger.DaysInterval = 7  # Every week
        trigger.StartBoundary = start_time

        print('Creating action...')
        # Create action
        action = task_def.Actions.Create(0)
        action.Path = f'"{cfg.QUICK_JOB_EXECUTION_SCRIPT}"'
        action.WorkingDirectory = os.getcwd()
        print('Action Path:', f'"{cfg.QUICK_JOB_EXECUTION_SCRIPT}"')

        #argument1 = cfg.QUICK_JOB_EXECUTION_SCRIPT
        argument2 = job_id
        #action.Arguments = f'{argument1} {argument2}'
        action.Arguments = f'{argument2}'

        print('Setting parameters...')
        # Set parameters
        task_def.RegistrationInfo.Description = 'Scheduled task for AI Monitor QuickJob'
        task_def.Settings.Enabled = enabled
        task_def.Settings.StopIfGoingOnBatteries = False
        task_def.Principal.LogonType = 1
        task_def.Principal.RunLevel = 1
        task_def.Settings.ExecutionTimeLimit = 'PT2M'
        #task_def.Settings.RunOnlyIfLoggedOn = False

        print('Registering task...', task_name, cfg.WINTASK_USER, cfg.WINTASK_PWD)
        # Register the task (create or update)
        root_folder.RegisterTaskDefinition(
            task_name,
            task_def,
            TASK_CREATE_OR_UPDATE,
            cfg.WINTASK_USER,  # No user
            cfg.WINTASK_PWD,  # No password
            1     # Logon type, 0 means a password does not need to be entered
        )
        print('Done.')
        return True
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        return False


def list_running_services(server, username = cfg.WINRM_USER, password = cfg.WINRM_PWD, domain = cfg.WINRM_DOMAIN):
    ps_script = """
    Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object -Property Name
    """
    session = winrm.Session(server, auth=(f'{domain}\\{username}', password), transport='ntlm')
    result = session.run_ps(ps_script)
    if result.status_code == 0:
        #print(result.std_out)
        # Get the stdout and convert it to a list
        services = result.std_out.decode().split('\r\n')
        report = '# | Name' + '\n'
        idx = 0
        for index, service in enumerate(services):
            if str(service).strip() != '' and not str(service).startswith('Name') and not str(service).startswith('----'):
                idx += 1
                report += str(idx) + ' | ' + str(service).strip() + '\n'
        return report
    else:
        print(f"Command failed with exit code {result.status_code}")
        print(result.std_err)
        return "Error: " + str(result.std_err)


def restart_service(host, service_name, username = cfg.WINRM_USER, password = cfg.WINRM_PWD, domain = cfg.WINRM_DOMAIN):
    try:
        session = winrm.Session(host, auth=(f'{domain}\\{username}', password), transport='ntlm')
        script = f"""
        $service = Get-Service -Name {service_name}
        if ($service.Status -eq 'Running') {{
            Restart-Service -Name {service_name}
        }} else {{
            Start-Service -Name {service_name}
        }}
        """
        result = session.run_ps(script)
        return True
    except Exception as e:
        print(str(e))
        return False
    #return result.status_code, result.std_out, result.std_err
    

def check_service_status(hostname, service_name, username = cfg.WINRM_USER, password = cfg.WINRM_PWD, domain = cfg.WINRM_DOMAIN):
    try:
        """
        Check if a service is running on a remote Windows host.
        
        :param hostname: The hostname or IP address of the remote Windows host.
        :param service_name: The name of the service to check.
        :return: True if the service is running, False otherwise.
        """
        # Prepare the PowerShell command to check the service status
        ps_script = f"Get-Service -Name {service_name} | Select-Object -ExpandProperty Status"
        
        # Set up the WinRM session
        # Note: This is using default authentication settings; you might need to specify credentials.
        session = winrm.Session(f'http://{hostname}:5985/wsman', auth=(f'{domain}\\{username}', password), transport='ntlm')
        
        # Execute the PowerShell command
        result = session.run_ps(ps_script)
        
        # Check the output and return True if the service is running, False otherwise
        if "Running" in result.std_out.decode('utf-8'):
            return "Running"
        else:
            return "Not running"
    except Exception as e:
        print(str(e))
        return "Error checking service"
    

def aihub_phone_call_alert_DEPRECATED(input_text, destination, voice_index=0):
    try:
        logging.info(f'AI Hub phone_call_alert - input_text: {input_text}')
        logging.info(f'AI Hub phone_call_alert - destination: {destination}')
            
        # Set API URL
        url = cfg.PHONE_BASE_URL
        api = cfg.PHONE_CALL_REQUEST
        url += api
        
        data = {
            "source": cfg.PHONE_SOURCE,
            "destination": destination,
            "displayName": cfg.PHONE_DISPLAY_NAME,
            "message": input_text
            }
        
        # API request
        response = requests.post(url, json=data)
        
        # Save temp speech audio file
        if response.status_code == 200:
            response = "Phone call succeeded"
        else:
            response = "Phone call failed"

        logging.info(str(response))
    except Exception as e:
        print(f'Error during AI Hub phone call API request: {e}')
        logging.error(f'Error during AI Hub phone call API request: {e}')
        response = "Error trying to make phone call"
    
    return response

def aihub_phone_call_alert(input_text, destination, voice_index=0):
    # Route through Cloud API if available
    if _CLOUD_NOTIFICATIONS_AVAILABLE:
        try:
            result = _cloud_phone_alert(
                to=str(destination),
                message=input_text,
                voice_index=voice_index
            )
            if result.get('success'):
                logging.info(f'Phone call succeeded via Cloud API to: {destination}')
                return "Phone call succeeded"
            elif result.get('blocked_by_limit'):
                logging.warning(f"Phone call blocked by limit: {result.get('message')}")
                return f"Phone call blocked: {result.get('message')}"
            else:
                logging.warning(f"Cloud phone call failed: {result.get('message')}, falling back to direct")
        except Exception as e:
            logging.warning(f"Cloud phone call error: {e}, falling back to direct")
    
    # Original implementation (fallback)
    try:
        logging.info(f'AI Hub phone_call_alert - input_text: {input_text}')
        logging.info(f'AI Hub phone_call_alert - destination: {destination}')
            
        url = cfg.PHONE_BASE_URL
        api = cfg.PHONE_CALL_REQUEST
        url += api
        
        data = {
            "source": cfg.PHONE_SOURCE,
            "destination": destination,
            "displayName": cfg.PHONE_DISPLAY_NAME,
            "message": input_text
        }
        
        response = requests.post(url, json=data)
        
        if response.status_code == 200:
            response = "Phone call succeeded"
        else:
            response = "Phone call failed"

        logging.info(str(response))
    except Exception as e:
        print(f'Error during AI Hub phone call API request: {e}')
        logging.error(f'Error during AI Hub phone call API request: {e}')
        response = "Error trying to make phone call"
    
    return response

def sms_text_message_alert_DEPRECATED(input_text, destination):
    try:
        logging.info(f'Sending SMS (via direct) text to: {destination}')
        print(f'Sending SMS (via direct) text to: {destination}')

        # Quickstart code goes here.
        sms_client = SmsClient.from_connection_string(cfg.API_AZURE_COMM_CONN_STR)
        
        # Call send() with SMS values.
        sms_responses = sms_client.send(
            from_=cfg.PHONE_SOURCE,
            to=str(destination),
            message=input_text,
            enable_delivery_report=True, # optional property
            tag="") # optional property
        
        print(sms_responses)
        response = "Text message succeeded"
        logging.info(str(sms_responses))
    except Exception as ex:
        print('SMS Exception:')
        print(ex)
        logging.error(str(ex))
        response = "Text message failed"
    
    return response

def sms_text_message_alert(input_text, destination):
    # Route through Cloud API if available
    if _CLOUD_NOTIFICATIONS_AVAILABLE:
        try:
            result = _cloud_sms_alert(
                to=str(destination),
                message=input_text
            )
            if result.get('success'):
                logging.info(f'SMS succeeded via Cloud API to: {destination}')
                return "Text message succeeded"
            elif result.get('blocked_by_limit'):
                logging.warning(f"SMS blocked by limit: {result.get('message')}")
                return f"Text message blocked: {result.get('message')}"
            else:
                logging.warning(f"Cloud SMS failed: {result.get('message')}, falling back to direct")
        except Exception as e:
            logging.warning(f"Cloud SMS error: {e}, falling back to direct")
    
    # Original implementation (fallback)
    try:
        logging.info(f'Sending SMS (via direct) text to: {destination}')
        print(f'Sending SMS (via direct) text to: {destination}')

        sms_client = SmsClient.from_connection_string(cfg.API_AZURE_COMM_CONN_STR)
        
        sms_responses = sms_client.send(
            from_=cfg.PHONE_SOURCE,
            to=str(destination),
            message=input_text,
            enable_delivery_report=True,
            tag="")
        
        print(sms_responses)
        response = "Text message succeeded"
        logging.info(str(sms_responses))
    except Exception as ex:
        print('SMS Exception:')
        print(ex)
        logging.error(str(ex))
        response = "Text message failed"
    
    return response

def get_user_info():
    try:
        report_data = ''
        df = Get_Users()
        if df is not None:
            # Iterate over the DataFrame rows
            for index, row in df.iterrows():
                if index == 0: # Add header
                    report_data += 'Name' + '\t' + 'User Name' + '\t' + 'Email' + '\t' + 'Phone' + '\n'

                report_data += row['name'] + '\t' + row['user_name'] + '\t' + row['email'] + '\t' + row['phone'] + '\n'
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        report_data = ''
    return report_data


def get_query_assistant_info():
    try:
        report_data = ''
        df = select_all_agents_and_connections()
        if df is not None:
            # Iterate over the DataFrame rows
            for index, row in df.iterrows():
                if index == 0: # Add header
                    report_data += 'Agent ID' + '\t' + 'Agent Name' + '\t' + 'Agent Objective' + '\t' + 'Database Connection ID' + '\n'

                report_data += str(row['agent_id']) + '\t' + row['agent_description'] + '\t' + row['agent_objective'] + '\t' + str(row['connection_id']) + '\n'
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        report_data = ''
    return report_data


def get_database_connection_information():
    try:
        report_data = ''
        df = select_all_database_connections()
        if df is not None:
            # Iterate over the DataFrame rows
            for index, row in df.iterrows():
                if index == 0: # Add header
                    report_data += 'Connection ID' + '\t' + 'Connection Name' + '\t' + 'Server' + '\t' + 'Database Name' + '\t' + 'Database Type' + '\n'

                report_data += str(row['connection_id']) + '\t' + row['connection_name'] + '\t' + row['server'] + '\t' + str(row['database_name']) + '\t' + str(row['database_type']) + '\n'
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        report_data = ''
    return report_data


def write_to_file(file_path: str, content: str, file_extension: str = ".txt") -> str:
    """
    Writes a string to a file with the optionally specified extension (default is .txt).

    Parameters:
    file_path (str): The full path to the file (without extension).
    content (str): The string content to write to the file.
    file_extension (str): The desired file extension (default is .txt).
    """
    # Ensure the file path ends with the specified extension
    if not file_path.endswith(file_extension):
        file_path += file_extension

    # Write the content to the file
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(content)
        return f"Content successfully written to {file_path}"
    except Exception as e:
        return f"An error occurred while writing to the file: {e}"
    

def load_from_file(file_path: str) -> str:
    """
    Loads the content of a text file and returns it as a string.

    Parameters:
    file_path (str): The full path to the file to be read.

    Returns:
    str: The content of the file as a string.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return content
    except Exception as e:
        return f"An error occurred while reading the file: {e}"
    

def load_custom_tool_by_name(tool_name):
    # Get full path to custom tool folder
    tool_folder = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
    if os.path.exists(tool_folder):
        config = None
        code = None
        # Check if 'config.json' is in the current directory
        if os.path.isfile(os.path.join(tool_folder, 'config.json')):
            # Construct full path to 'config.json'
            config_path = os.path.join(tool_folder, 'config.json')
            # Load JSON data from 'config.json'
            try:
                with open(config_path, 'r') as file:
                    config = json.load(file)
            except Exception as e:
                print(f"Failed to load JSON from {config_path}: {e}")
        
        # Check if 'code.py' is in the current directory
        code = ""
        if os.path.isfile(os.path.join(tool_folder, 'code.py')):
            # Construct full path to 'code.py'
            code_path = os.path.join(tool_folder, 'code.py')
            print(code_path)
            # Load text data from 'code.py'
            try:
                with open(code_path, 'r') as file:
                    for line in file:
                        code += '    ' + line
            except Exception as e:
                print(f"Failed to read from {code_path}: {e}")
        
    return config, code


def build_custom_tool_function_legacy(config, code):
    function_str = """
    @log_function_call
    def dynamically_created_function(x, y):
        return x * y
    """
    function_str = """"""

    # Add decorators
    for decorator in config['decorators']:
        function_str += '@'+ decorator + '\n'

    # Define function
    function_str += 'def ' + config['function_name']
    function_str += '('
    for idx, param in enumerate(config['parameters']):
        if idx == 0:
            function_str += param + ': ' + config['parameter_types'][idx]
        else:
            function_str += ',' + param + ': ' + config['parameter_types'][idx]
    function_str += ')' + ' -> ' + config['output_type'] + ':' + '\n'

    # Append lines of code with the initial indent
    function_str += '    ' + '"""' + config['description'] + '"""' + '\n'

    for module in config['modules']:
        function_str += '    ' + f'import {module}' + '\n'

    for line in str(code).split('\n'):
        function_str += '    ' + line

    return function_str

def _try_unwrap_def_body(code):
    """
    Safety net: if the LLM sends code wrapped in a 'def ...:' statement instead
    of just the function body, extract the body lines and dedent them.
    Returns the unwrapped body on success, or the original code if anything
    looks wrong (so existing behaviour is preserved).
    """
    try:
        stripped = code.strip()
        # Quick check: does it start with 'def '?
        if not stripped.startswith('def '):
            return code  # nothing to do

        lines = stripped.split('\n')
        if len(lines) < 2:
            return code  # single-line def — too risky to touch

        # Find the first line that ends with ':' (the def signature, possibly multi-line)
        body_start = None
        for i, line in enumerate(lines):
            if line.rstrip().endswith(':'):
                body_start = i + 1
                break

        if body_start is None or body_start >= len(lines):
            return code  # couldn't find end of def signature

        body_lines = lines[body_start:]

        # Skip docstrings (the builder wraps its own, and we add one too)
        if body_lines and body_lines[0].strip().startswith('"""'):
            # Find closing triple-quote
            if body_lines[0].strip().endswith('"""') and len(body_lines[0].strip()) > 3:
                body_lines = body_lines[1:]  # single-line docstring
            else:
                for j, bl in enumerate(body_lines[1:], start=1):
                    if '"""' in bl:
                        body_lines = body_lines[j + 1:]
                        break

        if not body_lines:
            return code  # nothing left after stripping — keep original

        # Determine the indent of the body and remove it
        first_body = body_lines[0]
        indent = len(first_body) - len(first_body.lstrip())
        if indent == 0:
            return code  # body isn't indented — probably not a real def wrapper

        unwrapped = []
        for line in body_lines:
            if line.strip() == '':
                unwrapped.append('')
            elif line[:indent].strip() == '':
                unwrapped.append(line[indent:])
            else:
                # Line has less indent than expected — unsafe, bail out
                return code
        
        result = '\n'.join(unwrapped).strip()
        if not result:
            return code

        print(f"[tool-code-safety] Detected def wrapper in tool code, extracted body ({len(lines)} lines -> {len(unwrapped)} lines)")
        return result
    except Exception as e:
        # Anything goes wrong → use the original code unchanged
        print(f"[tool-code-safety] Failed to unwrap def, using original code: {e}")
        return code


def build_custom_tool_function(config, code):
    # Safety net: strip def wrapper if the LLM included one
    code = _try_unwrap_def_body(code)
    function_str = ""
    print("Constucting function...")
    # Add decorators
    for decorator in config['decorators']:
        function_str += '@' + decorator + '\n'

    # Define function
    function_str += 'def ' + config['function_name'] + '('
    # Process parameters
    param_strings = []
    for idx, param in enumerate(config['parameters']):
        # Get the parameter type
        param_type = config['parameter_types'][idx]
        
        # Check if parameter is optional
        is_optional = config['parameter_optional'][idx] if 'parameter_optional' in config and idx < len(config['parameter_optional']) else False

        param_default = config['parameter_defaults'][idx] if 'parameter_defaults' in config and idx < len(config['parameter_defaults']) else None
        
        # Format the parameter
        if is_optional or param_default:
            # Add default value None for optional parameters
            if param_default:
                if param_type == 'str':
                    param_strings.append(f"{param}: {param_type} = '{param_default}'")
                else:
                    param_strings.append(f"{param}: {param_type} = {param_default}")
            else:
                param_strings.append(f"{param}: {param_type} = None")
        else:
            # Required parameter
            param_strings.append(f"{param}: {param_type}")
    # Join parameters with commas
    function_str += ", ".join(param_strings)
    
    # Complete function signature
    function_str += ') -> ' + config['output_type'] + ':' + '\n'

    # Append docstring with description
    function_str += '    ' + '"""' + config['description'] + '"""' + '\n'
    # Import modules
    for module in config['modules']:
        module = module.strip()
        
        # Skip empty modules
        if not module:
            continue
            
        # Handle different import formats
        if module.startswith('import ') or module.startswith('from '):
            # Module already has import statement
            function_str += '    ' + module + '\n'
        elif ' import ' in module:
            # Format like "datetime import datetime"
            function_str += '    from ' + module + '\n'
        else:
            # Simple module name
            function_str += '    import ' + module + '\n'
    # Add function body code with indentation
    for line in str(code).split('\n'):
        function_str += '    ' + line + '\n'
    #print("6", function_str)
    return function_str


def compile_python_script(source_file, bytecode_file):
    try:
        py_compile.compile(source_file, cfile=bytecode_file)
        return True
    except Exception as e:
        print(str(e))
        return False
        

def get_core_tool_details_legacy(yaml_file_path=cfg.CORE_TOOLS_FILE):
    with open(yaml_file_path, 'r') as file:
        tools_data = yaml.safe_load(file)
    return tools_data['tools']

def get_core_tool_details(yaml_file_path=cfg.CORE_TOOLS_FILE):
    """Enhanced version that respects tool visibility settings and includes display names"""
    import yaml
    from tool_dependency_manager import load_tool_dependencies
    
    # Load the core tools YAML file
    with open(yaml_file_path, 'r') as file:
        tools_data = yaml.safe_load(file)
    
    all_tools = tools_data.get('tools', [])
    
    # Load dependency manager
    manager = load_tool_dependencies()
    selectable_tools = manager.get_user_selectable_tools()
    
    # Filter to only include selectable tools
    filtered_tools = []
    for tool in all_tools:
        if tool['name'] in selectable_tools:
            # Enhance with category information
            tool_info = manager.get_tool_info(tool['name'])
            if tool_info:
                tool['category'] = tool_info.category
            
            # Ensure display_name exists
            if 'display_name' not in tool:
                tool['display_name'] = tool['name'].replace('_', ' ').title()
            
            filtered_tools.append(tool)
    
    return filtered_tools


def get_custom_tool_details():
    """Get custom tools with their descriptions"""
    import os
    import json
    
    custom_tools = []
    
    try:
        # List all directories in custom tools folder
        for tool_name in os.listdir(cfg.CUSTOM_TOOLS_FOLDER):
            tool_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
            
            # Skip if not a directory
            if not os.path.isdir(tool_path):
                continue
            
            # Try to load config.json
            config_path = os.path.join(tool_path, 'config.json')
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                    
                    custom_tools.append({
                        'name': tool_name,
                        'display_name': config.get('display_name', tool_name.replace('_', ' ').title()),
                        'description': config.get('description', 'No description available'),
                        'function_name': config.get('function_name', tool_name)
                    })
                except Exception as e:
                    # If config loading fails, add with minimal info
                    custom_tools.append({
                        'name': tool_name,
                        'display_name': tool_name.replace('_', ' ').title(),
                        'description': 'Error loading tool configuration',
                        'function_name': tool_name
                    })
    except Exception as e:
        print(f"Error loading custom tools: {e}")
    
    return custom_tools

def get_documents_by_order(order_number, document_type=None):
    """Fetch records from Documents table by order number and optional document type."""

    # SQL Server connection setup
    # server = 'your-server-name.database.windows.net'  # Your SQL Server name
    # database = 'your-database-name'  # Your database name
    # username = 'your-username'  # Your SQL Server username
    # password = 'your-password'  # Your SQL Server password
    # connection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
    
    try:
        # Establish connection to SQL Server
        #conn = pyodbc.connect(connection_string)
        #cursor = conn.cursor()
        
        # SQL query - handles the optional document_type
        if document_type:
            query = """
                SELECT DocumentID, DocumentType, OrderNumber, CustomerName, DocumentDate, TotalAmount, DocumentFilePath, DocumentFileName, Version
                FROM Documents
                WHERE OrderNumber = ? AND DocumentType = ?
            """
            params = (order_number, document_type)
        else:
            query = """
                SELECT DocumentID, DocumentType, OrderNumber, CustomerName, DocumentDate, TotalAmount, DocumentFilePath, DocumentFileName, Version
                FROM Documents
                WHERE OrderNumber = ?
            """
            params = (order_number,)
        
        # Execute query
        #cursor.execute(query, params)
        records = ExecuteSQLServerQueryWithParams(query, params)
        
        # Fetch all matching rows
        #records = cursor.fetchall()
        
        # If no records found
        if not records:
            return f"No documents found for order number: {order_number}"

        # Format the results as a string
        result_string = ""
        for row in records:
            document_link = f'<a href="{row.DocumentFilePath}">{row.DocumentFileName}</a>'
            result_string += (f"Document ID: {row.DocumentID}<br>"
                              f"Document Type: {row.DocumentType}<br>"
                              f"Order Number: {row.OrderNumber}<br>"
                              f"Customer Name: {row.CustomerName}<br>"
                              f"Document Date: {row.DocumentDate}<br>"
                              f"Total Amount: {row.TotalAmount}<br>"
                              f"Link to File: {document_link}<br>"  # Clickable HTML link
                              f"Version: {row.Version}<br>"
                              f"-----------------------------------------<br>")

        # Close the connection
        #cursor.close()
        #conn.close()

        return result_string

    except pyodbc.Error as e:
        return f"Error querying the database: {e}"

    # Example usage:
    # print(get_documents_by_order("11064"))
    # print(get_documents_by_order("ORD12345"))  # With no document_type


def send_email_notification_DEPRECATED(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    html_content: Optional[str] = None,
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
    connection_string: str = cfg.API_AZURE_EMAIL_CONN_STR,
    sender: str = cfg.SMTP_FROM or cfg.API_AZURE_EMAIL_SENDER
) -> bool:
    """
    Send an email using Azure Communication Services.
    
    Args:
        connection_string (str): Azure Communication Services connection string
        sender (str): Sender email address (must be verified domain)
        recipients (Union[str, List[str]]): Single recipient or list of recipients
        subject (str): Email subject
        body (str): Plain text email body
        html_content (Optional[str]): HTML version of the email body
        cc (Optional[Union[str, List[str]]]): Carbon copy recipients
        bcc (Optional[Union[str, List[str]]]): Blind carbon copy recipients
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    
    Raises:
        ValueError: If invalid parameters are provided
    """
    try:
        # Input validation
        if not all([connection_string, sender, recipients, subject, body]):
            raise ValueError("Missing required parameters")

        print(f"Sending email to {recipients} with subject {subject} and body {body} and sender {sender}")

        # Convert single recipient to list
        if isinstance(recipients, str):
            recipients = [{"address": recipients}]
        else:
            recipients = [{"address": email} for email in recipients]
        
        # Convert cc and bcc to lists of address objects
        if cc:
            if isinstance(cc, str):
                cc = [{"address": cc}]
            else:
                cc = [{"address": email} for email in cc]
                
        if bcc:
            if isinstance(bcc, str):
                bcc = [{"address": bcc}]
            else:
                bcc = [{"address": email} for email in bcc]
            
        # Initialize the email client
        email_client = EmailClient.from_connection_string(connection_string)

        # Create the message structure
        content = {
            "subject": subject,
            "plainText": body
        }

        # Only include HTML content when explicitly provided to avoid
        # collapsing newlines in clients that prioritize HTML parts
        if html_content:
            content["html"] = html_content

        message = {
            "content": content,
            "recipients": {
                "to": recipients,
                "cc": cc or [],
                "bcc": bcc or []
            },
            "senderAddress": sender
        }

        # Send the email
        poller = email_client.begin_send(message=message)
        # Wait for the operation to complete
        poller.wait()
        result = poller.result()
        print(result)
        logging.debug('Result:' + str(result))
        logging.info("Email sent successfully")
        return True

    except ValueError as e:
        logging.error(f"Invalid parameters: {str(e)}")
        raise
    except Exception as e:
        logging.error(f"Error while sending email: {str(e)}")
        return False
    
def send_email_notification(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    html_content: Optional[str] = None,
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
    connection_string: str = cfg.API_AZURE_EMAIL_CONN_STR,
    sender: str = cfg.SMTP_FROM or cfg.API_AZURE_EMAIL_SENDER
) -> bool:
    """
    Send an email using Cloud API or Azure Communication Services.
    """
    # Route through Cloud API if available
    if _CLOUD_NOTIFICATIONS_AVAILABLE:
        try:
            # Convert recipients to list
            if isinstance(recipients, str):
                to_list = [recipients]
            else:
                to_list = list(recipients)
            
            result = _cloud_send_email(
                to=to_list,
                subject=subject,
                body=body,
                html_body=html_content
            )
            
            if result.get('success'):
                logging.info(f"Email sent via Cloud API to: {to_list}")
                return True
            elif result.get('blocked_by_limit'):
                logging.warning(f"Email blocked by limit: {result.get('message')}")
                return False
            else:
                logging.warning(f"Cloud email failed: {result.get('message')}, falling back to direct")
        except Exception as e:
            logging.warning(f"Cloud email error: {e}, falling back to direct")
    
    # Original implementation (fallback)
    try:
        if not all([connection_string, sender, recipients, subject, body]):
            raise ValueError("Missing required parameters")

        print(f"Sending email to {recipients} with subject {subject} and body {body} and sender {sender}")

        if isinstance(recipients, str):
            recipients = [{"address": recipients}]
        else:
            recipients = [{"address": email} for email in recipients]
        
        if cc:
            if isinstance(cc, str):
                cc = [{"address": cc}]
            else:
                cc = [{"address": email} for email in cc]
                
        if bcc:
            if isinstance(bcc, str):
                bcc = [{"address": bcc}]
            else:
                bcc = [{"address": email} for email in bcc]
            
        email_client = EmailClient.from_connection_string(connection_string)

        content = {
            "subject": subject,
            "plainText": body
        }

        if html_content:
            content["html"] = html_content

        message = {
            "content": content,
            "recipients": {
                "to": recipients,
                "cc": cc or [],
                "bcc": bcc or []
            },
            "senderAddress": sender
        }

        poller = email_client.begin_send(message=message)
        poller.wait()
        result = poller.result()
        print(result)
        logging.debug('Result:' + str(result))
        logging.info("Email sent successfully")
        return True

    except ValueError as e:
        logging.error(f"Invalid parameters: {str(e)}")
        raise
    except Exception as e:
        logging.error(f"Error while sending email: {str(e)}")
        return False

def send_email_azure(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False
) -> bool:
    """
    Send an email using Azure Communication Services with optional attachment.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        connection_string = cfg.API_AZURE_EMAIL_CONN_STR
        sender = cfg.API_AZURE_EMAIL_SENDER

        # Initialize the email client
        email_client = EmailClient.from_connection_string(connection_string)
        
        # Prepare recipients list
        if isinstance(recipients, str):
            recipients = [recipients]
            
        # Prepare message content
        message = {
            "senderAddress": sender,
            "recipients": {
                "to": [{"address": recipient} for recipient in recipients]
            },
            "content": {
                "subject": subject,
                "plainText" if not html_content else "html": body
            }
        }
        
        # Add attachment if provided
        if attachment_path:
            if not os.path.exists(attachment_path):
                raise FileNotFoundError(f"Attachment not found: {attachment_path}")
                
            with open(attachment_path, 'rb') as file:
                file_content = file.read()
                
            attachment = {
                "name": Path(attachment_path).name,
                "contentType": "application/octet-stream",
                "contentInBase64": file_content
            }
            
            message["attachments"] = [attachment]
        
        # Send the email
        poller = email_client.begin_send(message)
        response = poller.result()
        
        return True
        
    except Exception as e:
        logging.error(f"Error sending Azure email: {str(e)}")
        return False

def replace_code_placeholders(code_string):
    # Replace all placeholder variables for runtime

    # 1. Replace connections
    code_string = replace_connection_placeholders(code_string)

    # Replace secret placeholders
    from local_secrets_integration import replace_secret_placeholders
    code_string = replace_secret_placeholders(code_string)

    return code_string


def query_a_database(connection_id: int, query: str) -> str:
    """
    Execute a SQL query against a specified database connection and return the results.
    Use this tool when you need to retrieve or manipulate data in a database.
    
    Args:
        connection_id: The ID of the database connection to use
        query: The SQL query to execute (SELECT, INSERT, UPDATE, etc.)
        
    Returns:
        The query results formatted as a table, or confirmation of success for non-SELECT queries
    """
    try:
        # Get connection information based on connection_id
        conn_str, conn_id, db_type = get_database_connection_string(connection_id)
        
        if not conn_str:
            return f"Error: No connection found with ID {connection_id}"
        
        # Connect to the database
        if db_type.lower() == 'excel' or 'excel' in conn_str.lower():
            conn = pyodbc.connect(conn_str, autocommit=True)
        else:
            conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # Execute the query
        cursor.execute(query)
        
        # Check if the query is a SELECT query (returns results)
        if query.strip().upper().startswith('SELECT'):
            # Fetch data and column names
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            
            # Convert to DataFrame for easy formatting
            df = pd.DataFrame.from_records(rows, columns=columns)
            
            # Handle empty results
            if df.empty:
                return "Query executed successfully but returned no results."
            
            # Check if result set is too large
            if len(df) > 100:
                result = df.head(100).to_string(index=False)
                return f"{result}\n\n[Showing first 100 rows of {len(df)} total rows]"
            else:
                return df.to_string(index=False)
        else:
            # For non-SELECT queries, commit changes and return affected rows
            conn.commit()
            row_count = cursor.rowcount
            return f"Query executed successfully. Rows affected: {row_count}"
    
    except Exception as e:
        logging.error(f"Error executing database query: {str(e)}")
        return f"Error executing query: {str(e)}"
    finally:
        if 'conn' in locals() and conn:
            conn.close()


def send_email_smtp(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False,
    smtp_host: str = cfg.SMTP_HOST,
    smtp_port: int = cfg.SMTP_PORT,
    smtp_user: str = cfg.SMTP_USER,
    smtp_password: str = cfg.SMTP_PASSWORD,
    smtp_use_tls: bool = cfg.SMTP_USE_TLS,
    smtp_from: str = cfg.SMTP_FROM
) -> bool:
    """
    Send an email using SMTP server with optional attachment.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username
        smtp_password: SMTP password
        smtp_use_tls: Whether to use TLS for SMTP connection
        smtp_from: Sender email address
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.application import MIMEApplication
        from email.utils import formatdate
        
        # Convert single recipient to list
        if isinstance(recipients, str):
            recipients = [recipients]
            
        # Create message container
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = ', '.join(recipients)
        msg['Date'] = formatdate(localtime=True)
        
        # Add body
        if html_content:
            msg.attach(MIMEText(body, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))
            
        # Add attachment if provided
        if attachment_path:
            if not os.path.exists(attachment_path):
                raise FileNotFoundError(f"Attachment not found: {attachment_path}")
                
            with open(attachment_path, 'rb') as file:
                part = MIMEApplication(file.read(), Name=os.path.basename(attachment_path))
                
            # Add header for attachment
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        
        # Connect to SMTP server
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_use_tls:
                server.starttls()
            
            # Login if credentials provided
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            
            # Send email
            server.send_message(msg)
            
        logging.info("SMTP email sent successfully")
        return True
        
    except Exception as e:
        logging.error(f"Error sending SMTP email: {str(e)}")
        return False


def send_email_wrapper(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False,
    email_provider: str = cfg.EMAIL_PROVIDER
) -> bool:
    """
    Wrapper function that sends email using the configured email provider.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
        email_provider: Email provider to use ('azure' or 'smtp')
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        if email_provider == 'smtp':
            return send_email_smtp(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
        else:  # Default to Azure
            return send_email_azure(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
    except Exception as e:
        logging.error(f"Error in send_email_wrapper: {str(e)}")
        return False


def send_email_DEPRECATED(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False
) -> bool:
    """
    Send an email using the configured email provider (Azure or SMTP).
    This is the main entry point for sending emails in the application.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Use SMTP if configured, otherwise use Azure
        if cfg.EMAIL_PROVIDER == 'smtp':
            return send_email_smtp(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
        else:  # Default to Azure
            return send_email_azure(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
    except Exception as e:
        logging.error(f"Error in send_email: {str(e)}")
        return False

def send_email(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False
) -> bool:
    """
    Send an email using Cloud API or configured provider (Azure/SMTP).
    """
    # Route through Cloud API if available
    if _CLOUD_NOTIFICATIONS_AVAILABLE:
        try:
            # Convert recipients to list
            if isinstance(recipients, str):
                to_list = [recipients]
            else:
                to_list = list(recipients)
            
            # Handle attachments
            attachments = None
            if attachment_path and os.path.exists(attachment_path):
                with open(attachment_path, 'rb') as f:
                    attachments = [{
                        'filename': os.path.basename(attachment_path),
                        'content': f.read(),
                        'content_type': 'application/octet-stream'
                    }]
            
            result = _cloud_send_email(
                to=to_list,
                subject=subject,
                body=body,
                html_body=body if html_content else None,
                attachments=attachments
            )
            
            if result.get('success'):
                logging.info(f"Email sent via Cloud API to: {to_list}")
                return True
            elif result.get('blocked_by_limit'):
                logging.warning(f"Email blocked by limit: {result.get('message')}")
                return False
            else:
                logging.warning(f"Cloud email failed: {result.get('message')}, falling back to direct")
        except Exception as e:
            logging.warning(f"Cloud email error: {e}, falling back to direct")
    
    # Original implementation (fallback)
    try:
        if cfg.EMAIL_PROVIDER == 'smtp':
            return send_email_smtp(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
        else:
            return send_email_azure(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
    except Exception as e:
        logging.error(f"Error in send_email: {str(e)}")
        return False

def sql_job_history_search(connection_id: int, job_name: str, hours_back: int = 24, status_filter: Optional[str] = None) -> str:
    """
    Search SQL Server job execution history.
    
    Parameters:
    - connection_id: Database connection ID
    - job_name: Name of the SQL job to search
    - hours_back: How many hours of history to search (default: 24)
    - status_filter: Optional filter for status ('failed', 'succeeded', 'all')
    """
    try:
        conn_str, _, _ = get_database_connection_string(connection_id)
        conn = pyodbc.connect(conn_str)
        
        query = """
        SELECT TOP 50
            j.name AS job_name,
            jh.step_name,
            jh.run_status,
            CASE jh.run_status
                WHEN 0 THEN 'Failed'
                WHEN 1 THEN 'Succeeded'
                WHEN 2 THEN 'Retry'
                WHEN 3 THEN 'Canceled'
                ELSE 'Unknown'
            END AS status_text,
            msdb.dbo.agent_datetime(jh.run_date, jh.run_time) AS run_datetime,
            ((jh.run_duration/10000) * 3600) + 
            (((jh.run_duration%10000)/100) * 60) + 
            (jh.run_duration%100) AS duration_seconds,
            jh.message
        FROM msdb.dbo.sysjobhistory jh
        INNER JOIN msdb.dbo.sysjobs j ON jh.job_id = j.job_id
        WHERE j.name LIKE ?
        AND msdb.dbo.agent_datetime(jh.run_date, jh.run_time) >= DATEADD(HOUR, -?, getutcdate())
        """
        
        params = [job_name, hours_back]
        
        # Add status filter if specified
        if status_filter:
            if status_filter.lower() == 'failed':
                query += " AND jh.run_status = 0"
            elif status_filter.lower() == 'succeeded':
                query += " AND jh.run_status = 1"
        
        query += " ORDER BY jh.run_date DESC, jh.run_time DESC"
        
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return f"No execution history found for job '{job_name}' in the last {hours_back} hours."
        
        # Format the results
        result = f"Execution History for '{job_name}' (Last {hours_back} hours):\n"
        result += f"Total Executions: {len(df)}\n"
        
        # Count by status
        status_counts = df['status_text'].value_counts()
        result += "\nStatus Summary:\n"
        for status, count in status_counts.items():
            result += f"  {status}: {count}\n"
        
        # Show recent executions
        result += "\nRecent Executions:\n"
        for idx, row in df.head(10).iterrows():
            result += f"\n{row['run_datetime']} - {row['status_text']}"
            if row['step_name'] != '(Job outcome)':
                result += f" - Step: {row['step_name']}"
            result += f"\n  Duration: {row['duration_seconds']} seconds"
            if row['run_status'] == 0 and row['message']:  # Failed
                message = str(row['message'])[:200]
                result += f"\n  Error: {message}..."
            result += "\n"
        
        return result
        
    except Exception as e:
        return f"Error searching job history: {str(e)}"


def get_agent_by_id(agent_id):
    try:
        # Establish the connection
        conn = get_db_connection()
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Query to select all agents and their tools
        cursor.execute("""
            SELECT 
                a.id as agent_id,
                a.description as agent_description,
                a.objective as agent_objective,
                a.enabled as agent_enabled,
                a.create_date as agent_create_date,
                t.tool_name,
                t.custom_tool
            FROM 
                [dbo].[Agents] a
            LEFT JOIN 
                [dbo].[AgentTools] t ON a.id = t.agent_id
            WHERE a.id = ?
            ORDER BY 
                a.id, t.tool_name
        """, agent_id)

        # Fetch all results
        rows = cursor.fetchall()

        # Process results into a list of dictionaries
        agents = defaultdict(lambda: {'agent_description': '', 'agent_enabled': '', 'agent_create_date': '', 'tool_names': [], 'custom_tool': []})

        for row in rows:
            agent_id = row.agent_id
            if row.agent_description not in agents[agent_id]:
                agents[agent_id]['agent_description'] = row.agent_description
                agents[agent_id]['agent_objective'] = row.agent_objective
                agents[agent_id]['agent_enabled'] = row.agent_enabled
                agents[agent_id]['agent_create_date'] = row.agent_create_date
            if row.tool_name:
                agents[agent_id]['tool_names'].append(row.tool_name)
                agents[agent_id]['custom_tool'].append(row.custom_tool)

        result = [{'agent_id': agent_id, **data} for agent_id, data in agents.items()]

        #print(result)

        return result
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        # Close the connection
        cursor.close()
        conn.close()



def get_all_agents():
    try:
        # Establish the connection
        conn = get_db_connection()
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Query to select all agents and their tools
        cursor.execute("""
            SELECT 
                a.description as agent_description
            FROM 
                [dbo].[Agents] a
        """)

        # Fetch all results
        rows = cursor.fetchall()

        # Process results into a list of dictionaries
        agents = []
        for row in rows:
            agents.append(row[0])

        #print(agents)

        return agents
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        # Close the connection
        cursor.close()
        conn.close()



def build_extraction_instructions(document_name: str, formatting_instructions: str = None) -> str:
    """
    Build the system instructions for document extraction.
    
    Args:
        document_name: Name of the document being processed
        formatting_instructions: Optional natural language formatting instructions
            If provided, AI will also return cell_formatting suggestions.
    
    Returns:
        System instruction string for the extraction prompt
    """
    
    base_instructions = f"""
You are an assistant that extracts structured data from documents.

You are given:
1) A PDF of guidelines/text (attached; its filename is "{document_name}").
2) A JSON schema mapping field keys to natural-language descriptions.

Your job:
- For each field key in the schema, read the guideline PDF and infer the best value.
- If you cannot confidently find a value, set "value": null and explain why in "assumptions".
- Be conservative; do not hallucinate. If the document is ambiguous, call that out.
- For each field, provide:
  - "value": a concise string or null
  - "confidence": your confidence level in the extracted value - must be one of: "HIGH", "MED", or "LOW"
    - HIGH: Value is explicitly and clearly stated in the document
    - MED: Value is reasonably inferred from context or partially stated
    - LOW: Value is uncertain, ambiguous, or based on weak evidence
  - "assumptions": an array of strings (may be empty)
  - "sources": an array of objects with:
      - "document": always the file name "{document_name}"
      - "pages": a list of integer page numbers where you found the information
      - "notes": a very short note (or null) explaining what you used from that location

IMPORTANT:
- Use page numbers based on the PDF pages as you see them.
- If you use multiple pages, include all of them in the "pages" array.
- If you derive a field from general knowledge or inference rather than directly from the PDF,
  set "pages": [] and explain that in "notes" and "assumptions".
"""

    # Base output format (without formatting)
    base_output_format = f"""
Output format (STRICT JSON ONLY, no markdown, no comments):
{{
  "fields": {{
    "<field_key>": {{
      "value": <string or null>,
      "confidence": "HIGH" | "MED" | "LOW",
      "assumptions": [<string>],
      "sources": [
        {{
          "document": "{document_name}",
          "pages": [<int>],
          "notes": <string or null>
        }}
      ]
    }},
    ...
  }},
  "global_assumptions": [<string>]
}}

Do NOT include any keys other than "fields" and "global_assumptions".
Do NOT wrap the JSON in backticks or any other formatting.
"""

    # If formatting instructions provided, add formatting section
    if formatting_instructions and formatting_instructions.strip():
        formatting_section = f"""

CELL FORMATTING REQUEST:
The user has requested intelligent cell formatting for Excel output.
Based on your analysis of the document and extracted values, suggest formatting for cells that match the user's criteria.

User's formatting instructions: "{formatting_instructions}"

For each field that should be formatted, add an entry to "cell_formatting".
Use your judgment based on:
- The extracted values and their context in the document
- Any assumptions or uncertainties you noted
- The user's formatting instructions
- Your understanding of what might be unusual, suspicious, or noteworthy

Formatting properties you can use (all optional):
- "fill": Background color as hex (e.g., "#FFCDD2" for light red, "#C8E6C9" for light green)
- "font_color": Text color as hex (e.g., "#B71C1C" for dark red)
- "bold": true/false
- "reason": Brief explanation of why this cell should be formatted

Common colors for reference:
- Light red background: "#FFCDD2"
- Light green background: "#C8E6C9" 
- Light yellow background: "#FFF9C4"
- Light blue background: "#BBDEFB"
- Light orange background: "#FFE0B2"
- Dark red text: "#B71C1C"
- Dark green text: "#1B5E20"

Only include fields in cell_formatting if they actually meet the user's criteria.
If no fields need formatting, return an empty object for cell_formatting.
"""

        output_format_with_formatting = f"""
Output format (STRICT JSON ONLY, no markdown, no comments):
{{
  "fields": {{
    "<field_key>": {{
      "value": <string or null>,
      "confidence": "HIGH" | "MED" | "LOW",
      "assumptions": [<string>],
      "sources": [
        {{
          "document": "{document_name}",
          "pages": [<int>],
          "notes": <string or null>
        }}
      ]
    }},
    ...
  }},
  "global_assumptions": [<string>],
  "cell_formatting": {{
    "<field_key>": {{
      "fill": "<hex color or null>",
      "font_color": "<hex color or null>",
      "bold": <true/false or null>,
      "reason": "<brief explanation>"
    }},
    ...
  }}
}}

Do NOT include any keys other than "fields", "global_assumptions", and "cell_formatting".
Do NOT wrap the JSON in backticks or any other formatting.
"""
        return base_instructions + formatting_section + output_format_with_formatting
    else:
        return base_instructions + base_output_format


def build_extraction_instructions_legacy(document_name: str) -> str:
    return f"""
You are an assistant that extracts structured data from retailer guideline PDFs.

You are given:
1) A PDF of guidelines (attached; its filename is "{document_name}").
2) A JSON schema mapping field keys to natural-language descriptions.

Your job:
- For each field key in the schema, read the guideline PDF and infer the best value.
- If you cannot confidently find a value, set "value": null and explain why in "assumptions".
- Be conservative; do not hallucinate. If the document is ambiguous, call that out.
- For each field, provide:
  - "value": a concise string or null
  - "assumptions": an array of strings (may be empty)
  - "sources": an array of objects with:
      - "document": always the file name "{document_name}"
      - "pages": a list of integer page numbers where you found the information
      - "notes": a very short note (or null) explaining what you used from that location

IMPORTANT:
- Use page numbers based on the PDF pages as you see them.
- If you use multiple pages, include all of them in the "pages" array.
- If you derive a field from general knowledge or inference rather than directly from the PDF,
  set "pages": [] and explain that in "notes" and "assumptions".

Output format (STRICT JSON ONLY, no markdown, no comments):
{{
  "fields": {{
    "<field_key>": {{
      "value": <string or null>,
      "assumptions": [<string>],
      "sources": [
        {{
          "document": "{document_name}",
          "pages": [<int>],
          "notes": <string or null>
        }}
      ]
    }},
    ...
  }},
  "global_assumptions": [<string>]
}}

Do NOT include any keys other than "fields" and "global_assumptions".
Do NOT wrap the JSON in backticks or any other formatting.
"""


def _is_streaming_recommended_error(error_message: str) -> bool:
    """Check if the error is the 'streaming strongly recommended' warning from Claude."""
    streaming_indicators = [
        "streaming is strongly recommended",
        "operations that may take longer than",
        "long-running",
        "timeout"
    ]
    error_lower = str(error_message).lower()
    return any(indicator in error_lower for indicator in streaming_indicators)

# ============================================================================
# Chunked Schema Extraction for Large PDFs (>100 pages)
# ============================================================================
def merge_schema_results(results: List[Dict[str, Any]], logger: Optional[logging.Logger] = None) -> Dict[str, Any]:
    """
    Intelligently merge schema extraction results from multiple PDF chunks.
    
    Merging Strategy:
    - For each field, prioritize non-null values
    - Combine assumptions from all chunks (deduplicate)
    - Merge sources, combining page references
    - Combine global assumptions (deduplicate)
    - Preserve confidence from the best value
    
    Parameters
    ----------
    results : List[Dict[str, Any]]
        List of schema extraction results from individual chunks.
        Each result has the format returned by populate_schema_with_claude.
    logger : logging.Logger, optional
        Logger instance for debugging information.
        
    Returns
    -------
    Dict[str, Any]
        Merged schema result with combined data from all chunks.
    """
    if logger is None:
        logger = logging.getLogger("merge_schema_results")
    
    if not results:
        logger.warning("No results to merge")
        return {
            "fields": {},
            "global_assumptions": []
        }
    
    if len(results) == 1:
        logger.info("Only one result, returning as-is")
        return results[0]
    
    logger.info(f"Merging {len(results)} schema extraction results")
    
    # Initialize merged result
    merged = {
        "fields": {},
        "global_assumptions": []
    }
    
    # Track all field keys across all chunks
    all_field_keys = set()
    for result in results:
        if "fields" in result and result["fields"]:
            all_field_keys.update(result["fields"].keys())
    
    logger.info(f"Found {len(all_field_keys)} unique fields across all chunks")
    
    # Merge each field
    for field_key in all_field_keys:
        merged_field = {
            "value": None,
            "confidence": None,
            "assumptions": [],
            "sources": []
        }
        
        # Collect all values, assumptions, and sources for this field across chunks
        values = []
        assumptions_set = set()
        sources_by_doc = {}  # Group sources by document name
        
        for chunk_idx, result in enumerate(results):
            if "fields" not in result or field_key not in result["fields"]:
                continue
                
            field_data = result["fields"][field_key]
            
            # Collect non-null values with their confidence
            if field_data.get("value") is not None and field_data["value"] != "":
                values.append({
                    "value": field_data["value"],
                    "confidence": field_data.get("confidence", "MED"),
                    "chunk_idx": chunk_idx,
                    "source_count": len(field_data.get("sources", []))
                })
            
            # Collect assumptions
            if "assumptions" in field_data and field_data["assumptions"]:
                for assumption in field_data["assumptions"]:
                    if assumption:  # Skip empty assumptions
                        assumptions_set.add(assumption)
            
            # Collect and group sources by document
            if "sources" in field_data and field_data["sources"]:
                for source in field_data["sources"]:
                    doc_name = source.get("document", "unknown")
                    if doc_name not in sources_by_doc:
                        sources_by_doc[doc_name] = {
                            "document": doc_name,
                            "pages": [],
                            "notes": None
                        }
                    
                    # Add pages
                    if "pages" in source and source["pages"]:
                        sources_by_doc[doc_name]["pages"].extend(source["pages"])
                    
                    # Combine notes (prefer non-null, combine if multiple)
                    if source.get("notes"):
                        if sources_by_doc[doc_name]["notes"]:
                            sources_by_doc[doc_name]["notes"] += f"; {source['notes']}"
                        else:
                            sources_by_doc[doc_name]["notes"] = source["notes"]
        
        # Determine best value and confidence
        if values:
            # Strategy: Prefer values from chunks with more source citations
            # This indicates Claude found more evidence for that value
            values_sorted = sorted(values, key=lambda x: x["source_count"], reverse=True)
            merged_field["value"] = values_sorted[0]["value"]
            merged_field["confidence"] = values_sorted[0]["confidence"]
            
            # Check for conflicting values across chunks
            # Use JSON serialization to handle unhashable types (lists, dicts)
            def make_hashable(val):
                """Convert value to a hashable representation for comparison."""
                if isinstance(val, (list, dict)):
                    return json.dumps(val, sort_keys=True)
                return val
            
            try:
                unique_value_keys = set(make_hashable(v["value"]) for v in values)
                if len(values) > 1 and len(unique_value_keys) > 1:
                    # Multiple different values found - log warning and note in assumptions
                    unique_values = []
                    seen = set()
                    for v in values:
                        key = make_hashable(v["value"])
                        if key not in seen:
                            seen.add(key)
                            unique_values.append(v["value"])
                    
                    logger.warning(
                        f"Field '{field_key}' has conflicting values across chunks: {unique_values}. "
                        f"Using value with most citations: {merged_field['value']}"
                    )
                    assumptions_set.add(
                        f"Multiple values found in different sections: {', '.join(str(v) for v in unique_values)}. "
                        f"Selected value with most supporting evidence."
                    )
            except Exception as e:
                # If comparison fails for any reason, just continue with the best value
                logger.debug(f"Could not compare values for field '{field_key}': {e}")
        
        # Set assumptions
        merged_field["assumptions"] = sorted(list(assumptions_set))
        
        # Set sources (deduplicate pages within each document)
        for doc_data in sources_by_doc.values():
            # Remove duplicate pages and sort
            doc_data["pages"] = sorted(list(set(doc_data["pages"])))
            merged["fields"][field_key] = merged_field
            merged["fields"][field_key]["sources"].append(doc_data)
    
    # Merge global assumptions
    global_assumptions_set = set()
    for result in results:
        if "global_assumptions" in result and result["global_assumptions"]:
            for assumption in result["global_assumptions"]:
                if assumption:  # Skip empty assumptions
                    global_assumptions_set.add(assumption)
    
    merged["global_assumptions"] = sorted(list(global_assumptions_set))
    
    logger.info(
        f"Merge complete: {len(merged['fields'])} fields, "
        f"{len(merged['global_assumptions'])} global assumptions"
    )
    
    return merged


def populate_schema_with_claude_chunked(
    pdf_path: str,
    schema_fields: Dict[str, str],
    client: Optional[AnthropicProxyClient] = None,
    model: Optional[str] = cfg.ANTHROPIC_MODEL,
    max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
    temperature: float = 0.0,
    module_name: str = "populate_schema_chunked",
    request_id: Optional[str] = str(uuid.uuid4()),
    use_streaming: bool = True,
    auto_fallback_to_streaming: bool = True,
    formatting_instructions: str = None,  # Match original function
    max_pages_per_chunk: Optional[int] = None,
    chunk_overlap_pages: int = 5
) -> Dict[str, Any]:
    """
    Process large PDFs for schema extraction by chunking when necessary.
    
    For PDFs under the configured page limit, uses the standard populate_schema_with_claude.
    For larger PDFs, splits into chunks, extracts schemas from each, and merges results.
    
    Parameters
    ----------
    pdf_path : str
        Local path to the PDF file to process.
    schema_fields : Dict[str, str]
        Mapping from field_key -> human-readable description of the field.
    client : AnthropicProxyClient, optional
        An initialized AnthropicProxyClient. If None, a new one is created.
    model : str, optional
        Claude model name to use. If None, uses cfg.ANTHROPIC_MODEL.
    max_tokens : int, optional
        Max tokens for Claude's response. If None, uses cfg.ANTHROPIC_MAX_TOKENS.
    temperature : float
        Sampling temperature (0–1).
    module_name : str
        Name used for tracking/logging.
    request_id : str, optional
        Optional external request ID for tracking.
    use_streaming : bool
        Whether to use streaming for the request. Default True.
    auto_fallback_to_streaming : bool
        If True, automatically retry with streaming on failure. Default True.
    max_pages_per_chunk : int, optional
        Maximum pages per chunk. If None, uses cfg.DOC_SCHEMA_EXTRACTION_MAX_PAGES.
        Default: 100 pages (Claude's API limit for PDF documents)
    auto_fallback_to_streaming : bool
        If True, automatically retry with streaming on failure. Default True.
    formatting_instructions : str, optional
        Natural language instructions for cell formatting. If provided, the AI will
        return a "cell_formatting" object with formatting suggestions.
        Passed through to populate_schema_with_claude.
    max_pages_per_chunk : int, optional
        Maximum pages per chunk. If None, uses cfg.DOC_SCHEMA_EXTRACTION_MAX_PAGES.
        Default: 100 pages (Claude's API limit for PDF documents)
    chunk_overlap_pages : int
        Number of pages to overlap between chunks to avoid missing context.
        Default: 5 pages
        
    Returns
    -------
    Dict[str, Any]
        Schema extraction result in the standard format:
        {
          "fields": {
            "<field_key>": {
              "value": <string or null>,
              "assumptions": [<string>],
              "sources": [{"document": <str>, "pages": [<int>], "notes": <str>}]
            }
          },
          "global_assumptions": [<string>],
          "chunked": <bool>,  # True if document was chunked
          "chunk_count": <int>,  # Number of chunks processed
          "total_pages": <int>  # Total pages in original document
        }
        
    Raises
    ------
    FileNotFoundError
        If the PDF file doesn't exist.
    ValueError
        If the PDF is empty or corrupted.
    """
    # Validate file exists
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    # Get max pages from config if not specified
    if max_pages_per_chunk is None:
        max_pages_per_chunk = getattr(cfg, 'DOC_SCHEMA_EXTRACTION_MAX_PAGES', 100)
    
    # Set default max_tokens if not provided
    if max_tokens is None:
        max_tokens = int(cfg.ANTHROPIC_MAX_TOKENS)
    
    # Read PDF to get page count
    try:
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count
        doc.close()
    except Exception as e:
        raise ValueError(f"Failed to read PDF file: {e}")
    
    if total_pages == 0:
        raise ValueError("PDF file is empty (0 pages)")
    
    logger.info(
        f"Processing PDF '{os.path.basename(pdf_path)}': "
        f"{total_pages} pages, max_per_chunk={max_pages_per_chunk}"
    )
    
    # If under the limit, use standard processing
    if total_pages <= max_pages_per_chunk:
        logger.info(f"PDF within limit, using standard processing")
        result = populate_schema_with_claude(
            pdf_path=pdf_path,
            schema_fields=schema_fields,
            client=client,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            module_name=module_name,
            request_id=request_id,
            use_streaming=use_streaming,
            auto_fallback_to_streaming=auto_fallback_to_streaming,
            formatting_instructions=formatting_instructions
        )
        
        # Add metadata
        result["chunked"] = False
        result["chunk_count"] = 1
        result["total_pages"] = total_pages
        return result
    
    # Need to chunk the PDF
    logger.info(f"PDF exceeds limit, chunking into ~{max_pages_per_chunk}-page segments")
    
    # Calculate chunk ranges with overlap
    chunk_ranges = []
    start_page = 0
    
    while start_page < total_pages:
        end_page = min(start_page + max_pages_per_chunk, total_pages)
        chunk_ranges.append((start_page, end_page))
        
        # Move start for next chunk, accounting for overlap
        # Don't overlap on the last chunk
        if end_page < total_pages:
            start_page = end_page - chunk_overlap_pages
        else:
            break
    
    logger.info(f"Created {len(chunk_ranges)} chunks with {chunk_overlap_pages}-page overlap")
    for idx, (start, end) in enumerate(chunk_ranges):
        logger.info(f"  Chunk {idx + 1}: pages {start + 1}-{end}")
    
    # Process each chunk
    chunk_results = []
    temp_files = []
    
    try:
        for chunk_idx, (start_page, end_page) in enumerate(chunk_ranges):
            logger.info(f"Processing chunk {chunk_idx + 1}/{len(chunk_ranges)}: pages {start_page + 1}-{end_page}")
            
            # Create temporary PDF with subset of pages
            temp_pdf_path = f"{pdf_path}.chunk_{start_page + 1}-{end_page}.tmp.pdf"
            temp_files.append(temp_pdf_path)
            
            # Read original PDF and write chunk using PyMuPDF
            source_doc = fitz.open(pdf_path)
            output_doc = fitz.open()  # Create new empty PDF
            
            # insert_pdf uses inclusive range, and to_page is inclusive
            # Our end_page is exclusive (Python range style), so subtract 1
            output_doc.insert_pdf(source_doc, from_page=start_page, to_page=end_page - 1)
            output_doc.save(temp_pdf_path)
            
            # Clean up document objects
            output_doc.close()
            source_doc.close()
            
            logger.info(f"Created temporary chunk file: {temp_pdf_path}")
            
            # Process chunk with standard function
            try:
                chunk_result = populate_schema_with_claude(
                    pdf_path=temp_pdf_path,
                    schema_fields=schema_fields,
                    client=client,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    module_name=f"{module_name}_chunk{chunk_idx + 1}",
                    request_id=f"{request_id}_chunk{chunk_idx + 1}" if request_id else None,
                    use_streaming=use_streaming,
                    auto_fallback_to_streaming=auto_fallback_to_streaming,
                    formatting_instructions=formatting_instructions
                )
                
                # Adjust page numbers in sources to reflect original document
                if "fields" in chunk_result:
                    for field_key, field_data in chunk_result["fields"].items():
                        if "sources" in field_data and field_data["sources"]:
                            for source in field_data["sources"]:
                                if "pages" in source and source["pages"]:
                                    # Add the start_page offset to all page numbers
                                    source["pages"] = [p + start_page for p in source["pages"]]
                
                chunk_results.append(chunk_result)
                logger.info(f"Chunk {chunk_idx + 1} processed successfully")
                
            except Exception as e:
                logger.error(f"Error processing chunk {chunk_idx + 1}: {e}")
                # Continue with other chunks even if one fails
                # Add an empty result to maintain chunk indexing
                chunk_results.append({
                    "fields": {},
                    "global_assumptions": [f"Chunk {chunk_idx + 1} processing failed: {str(e)}"]
                })
    
    finally:
        # Clean up temporary files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    logger.info(f"Cleaned up temporary file: {temp_file}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary file {temp_file}: {e}")
    
    # Merge results from all chunks
    logger.info(f"Merging results from {len(chunk_results)} chunks")
    merged_result = merge_schema_results(chunk_results, logger=logger)
    
    # Add metadata
    merged_result["chunked"] = True
    merged_result["chunk_count"] = len(chunk_ranges)
    merged_result["total_pages"] = total_pages
    
    logger.info(
        f"Chunked processing complete: {total_pages} pages processed in {len(chunk_ranges)} chunks, "
        f"{len(merged_result.get('fields', {}))} fields extracted"
    )
    
    return merged_result

def populate_schema_with_claude(
    pdf_path: str,
    schema_fields: Dict[str, str],
    client: Optional[AnthropicProxyClient] = None,
    model: Optional[str] = cfg.ANTHROPIC_MODEL,
    max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
    temperature: float = 0.0,
    module_name: str = "populate_schema_with_claude",
    request_id: Optional[str] = str(uuid.uuid4()),
    use_streaming: bool = True,
    auto_fallback_to_streaming: bool = True,
    formatting_instructions: str = None
) -> Dict[str, Any]:
    """
    Use AnthropicProxyClient to send a guidelines PDF + JSON schema to Claude
    and get back a populated JSON schema (values + assumptions + sources).

    Parameters
    ----------
    pdf_path : str
        Local path to the guidelines PDF.
    schema_fields : dict
        Mapping from field_key -> human-readable description of the field.
    client : AnthropicProxyClient, optional
        An initialized AnthropicProxyClient. If None, a new one is created.
    model : str, optional
        Claude model name to use. If None, uses the proxy default (cfg.ANTHROPIC_MODEL).
    max_tokens : int
        Max tokens for Claude's response.
    temperature : float
        Sampling temperature (0–1).
    module_name : str
        Name used for tracking/logging in the proxy.
    request_id : str, optional
        Optional external request ID for tracking; if None, a UUID is generated by the client.
    use_streaming : bool
        Whether to use streaming for the request. Default True.
    auto_fallback_to_streaming : bool
        If True and use_streaming is False, automatically retry with streaming
        if the non-streaming request fails due to timeout or size issues. Default True.
    formatting_instructions : str, optional  # NEW
        Natural language instructions for cell formatting. If provided, the AI will
        return a "cell_formatting" object with formatting suggestions.

    Returns
    -------
    dict
        Parsed JSON of the form:
        {
          "fields": {
            "<field_key>": {
              "value": <string or null>,
              "assumptions": [<string>],
              "sources": [
                {
                  "document": "<filename>",
                  "pages": [<int>],
                  "notes": <string or null>
                }
              ]
            },
            ...
          },
          "global_assumptions": [<string>],
          "cell_formatting": {  # Only present if formatting_instructions provided
            "<field_key>": {
              "fill": "<hex color>",
              "font_color": "<hex color>",
              "bold": <boolean>,
              "reason": "<explanation>"
            }
          }
        }
    """
    import logging
    logger = logging.getLogger("populate_schema_with_claude")
    
    # Instantiate client if not provided
    if client is None:
        client = AnthropicProxyClient()

    # Set tracking params for logging/observability in your proxy
    client._set_tracking_params(module_name=module_name, request_id=request_id)

    filename = os.path.basename(pdf_path)

    # System instructions: strict JSON, include assumptions & sources, etc.
    system_instructions = build_extraction_instructions(filename, formatting_instructions)

    # User text: just the schema description
    user_text = (
        "Here is the JSON schema mapping field keys to descriptions. "
        "Populate it according to the instructions.\n\n"
        + json.dumps(schema_fields, indent=2)
    )

    # Auto-detect: if file is large, prefer streaming
    file_size_mb = 0
    try:
        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if file_size_mb > int(cfg.WORKFLOW_DOC_STREAM_AT_SIZE) and not use_streaming:
            logger.info(f"Large file detected ({file_size_mb:.1f} MB), switching to streaming")
            use_streaming = True
    except Exception:
        pass  # Ignore file size check errors

    # ------------------------------------------------------------------
    # Internal function to make the API call
    # ------------------------------------------------------------------
    def _make_request(streaming: bool):
        if streaming:
            logger.info(f"Using streaming for document extraction: {filename}")
            return client.messages_with_document_stream(
                file_path=pdf_path,
                user_text=user_text,
                model=model or "",
                max_tokens=max_tokens,
                system=system_instructions,
                temperature=temperature,
            )
        else:
            logger.info(f"Using non-streaming for document extraction: {filename}")
            return client.messages_with_document(
                file_path=pdf_path,
                user_text=user_text,
                model=model or "",
                max_tokens=max_tokens,
                system=system_instructions,
                temperature=temperature,
            )

    # ------------------------------------------------------------------
    # Make the request (with optional fallback to streaming)
    # ------------------------------------------------------------------
    response = None
    used_streaming_fallback = False

    try:
        response = _make_request(streaming=use_streaming)
        
        # Check if response contains an error that suggests streaming should be used
        if isinstance(response, dict) and "error" in response:
            error_msg = str(response.get('error', '')) + ' ' + str(response.get('details', ''))
            
            if not use_streaming and auto_fallback_to_streaming and _is_streaming_recommended_error(error_msg):
                logger.warning(f"Non-streaming request failed with streaming recommendation. Retrying with streaming...")
                used_streaming_fallback = True
                response = _make_request(streaming=True)

            if use_streaming:
                logger.warning(f"Streaming request failed. Retrying with non-streaming...")
                response = _make_request(streaming=False)
    
    except Exception as e:
        error_str = str(e)
        
        # If non-streaming failed and fallback is enabled, try streaming
        if not use_streaming and auto_fallback_to_streaming and _is_streaming_recommended_error(error_str):
            logger.warning(f"Non-streaming request raised exception: {error_str}. Retrying with streaming...")
            used_streaming_fallback = True
            try:
                response = _make_request(streaming=True)
            except Exception as e2:
                raise RuntimeError(
                    f"Both non-streaming and streaming requests failed. "
                    f"Original error: {error_str}. Streaming error: {str(e2)}"
                ) from e2
        else:
            raise

    if used_streaming_fallback:
        logger.info("Successfully completed request using streaming fallback")

    # ------------------------------------------------------------------
    # Handle proxy-level error wrapper
    # ------------------------------------------------------------------
    if isinstance(response, dict) and "error" in response and "content" not in response:
        raise RuntimeError(
            f"Error from Anthropic proxy (populate_schema_with_claude): {response.get('error')} - {response.get('details')}"
        )

    # ------------------------------------------------------------------
    # Check for truncation
    # ------------------------------------------------------------------
    stop_reason = response.get('stop_reason')
    if stop_reason == 'max_tokens':
        usage = response.get('usage', {})
        logger.error(f"Response truncated! Stop reason: {stop_reason}, Usage: {usage}")
        raise RuntimeError(
            f"Claude response was truncated due to max_tokens limit ({max_tokens}). "
            f"Increase max_tokens or reduce output complexity."
        )

    # ------------------------------------------------------------------
    # Extract raw text output from the proxy response
    # ------------------------------------------------------------------
    output_text = None

    # Case 1: Anthropic-style: {"content": [{"type": "text", "text": "..."}], ...}
    if isinstance(response, dict) and "content" in response:
        content = response["content"]
        if isinstance(content, list):
            chunks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
                elif hasattr(block, "get") and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
            output_text = "".join(chunks).strip()
        elif isinstance(content, str):
            output_text = content.strip()

    # Case 2: Some proxies might put completion in "completion" or "text"
    if not output_text:
        if isinstance(response, dict):
            if "completion" in response and isinstance(response["completion"], str):
                output_text = response["completion"].strip()
            elif "text" in response and isinstance(response["text"], str):
                output_text = response["text"].strip()

    if not output_text:
        raise RuntimeError(
            f"Could not find text output in proxy response. Raw response: {response}"
        )

    # ------------------------------------------------------------------
    # Parse the model's JSON
    # ------------------------------------------------------------------
    try:
        if output_text.startswith("```json") or "```JSON" in output_text.upper():
            output_text = output_text.replace("```json", "").replace("```", "")
        populated = json.loads(output_text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed. Text length: {len(output_text)}")
        logger.error(f"Text ends with: ...{output_text[-500:] if len(output_text) > 500 else output_text}")
        raise RuntimeError(
            f"Claude output was not valid JSON. Raw output:\n{output_text}"
        ) from e

    return populated



def is_file_path(value: str) -> bool:
    """
    Determine if a string value looks like a file path.
    
    Args:
        value: String to check
        
    Returns:
        True if it looks like a file path, False otherwise
    """
    if not value or not isinstance(value, str):
        return False
    
    value = value.strip()
    
    # Check for common path indicators
    path_indicators = [
        value.startswith('/'),           # Unix absolute path
        value.startswith('\\\\'),        # UNC path
        value.startswith('\\'),          # Windows relative with backslash
        len(value) > 2 and value[1] == ':',  # Windows drive letter (C:\...)
        value.startswith('./'),          # Relative path
        value.startswith('../'),         # Parent relative path
    ]
    
    if any(path_indicators):
        return True
    
    # Check for file extensions
    file_extensions = ['.pdf', '.docx', '.doc', '.txt', '.xlsx', '.xls', '.csv', '.html', '.htm']
    lower_value = value.lower()
    if any(lower_value.endswith(ext) for ext in file_extensions):
        return True
    
    return False


