from langchain_openai import AzureChatOpenAI, ChatOpenAI
from api_keys_config import get_openai_config
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_classic.agents.format_scratchpad import format_to_tool_messages
from langchain_classic.agents.output_parsers import ToolsAgentOutputParser
from langchain_classic.agents import AgentExecutor
from langchain_classic.callbacks import FileCallbackHandler

import config as cfg
from AppUtils import *

import logging
from logging.handlers import WatchedFileHandler
import os
import json
import pyodbc
from datetime import datetime
from LLMDataEngineV2 import LLMDataEngine
from DocUtils import *
from agent_knowledge_integration import KnowledgeTool, get_agent_knowledge_documents
from AppUtils import send_email, send_email_wrapper, sms_text_message_alert, aihub_phone_call_alert
from tool_dependency_manager import get_tools_for_agent, load_tool_dependencies
import system_prompts as system_prompts
from request_tracking import RequestTracking
from SmartContentRenderer import SmartContentRenderer
from DataFrameFileManager import DataFrameFileManager
from RichContentManager import RichContentManager
import uuid

# Module-level context storage for the current agent execution
import threading
_current_agent_context = threading.local()

from CommonUtils import rotate_logs_on_startup, get_log_path

rotate_logs_on_startup(os.getenv('GENERAL_AGENT_LOG', get_log_path('general_agent_log.txt')))

# Configure logging
logger = logging.getLogger("GeneralAgent")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('GENERAL_AGENT_LOG', get_log_path('general_agent_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)



# Import agent communication tools
from agent_communication_tool import (
    communicate_with_agent,
    broadcast_to_agents,
    delegate_task_to_best_agent,
    get_active_agents,
    create_agent_workflow,
    register_agent,
    unregister_agent
)

# Import agent email tools
from agent_email_tools import (
    create_email_inbox_tools,
    set_email_tool_context,
    clear_email_tool_context,
    get_email_tools_system_prompt_addition
)

# Import local secrets tools
from local_secrets import get_local_secret, has_local_secret

# Add agent communication & secrets tools to globals
globals().update({
    'communicate_with_agent': communicate_with_agent,
    'broadcast_to_agents': broadcast_to_agents,
    'delegate_task_to_best_agent': delegate_task_to_best_agent,
    'get_active_agents': get_active_agents,
    'create_agent_workflow': create_agent_workflow,
    'get_local_secret': get_local_secret,
    'has_local_secret': has_local_secret
})

# Create global instances
df_manager = DataFrameFileManager()
rich_content_manager = RichContentManager()

#########################
# DECORATORS
#########################
# Custom function decorator
def log_function_call(func):
    def wrapper(*args, **kwargs):
        logger.info(f"Calling custom function {func.__name__} with arguments {args} and keyword arguments {kwargs}")
        print(f"Calling custom function {func.__name__} with arguments {args} and keyword arguments {kwargs}")
        result = func(*args, **kwargs)
        print(f"Function {func.__name__} returned {result}")
        logger.info(f"Function {func.__name__} returned {result}")
        return result
    return wrapper


#########################
# HELPERS
#########################
def load_custom_tool(tool_folder, indent_code=False):
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
                        if indent_code:
                            code += '    ' + line
                        else:
                            code += line
            except Exception as e:
                print(f"Failed to read from {code_path}: {e}")

    return config, code

def build_custom_tool_legacy(config, code):
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

    function_str += '    ' + '"""' + config['description'] + '"""' + '\n'

    function_str += code

    print(86 * '@')
    print("Custom Function String:")
    print(function_str)
    print(86 * '@')

    return function_str

def build_custom_tool(config, code):
    return build_custom_tool_function(config, code)

def create_agent_knowledge_tools(agent_instance):
    """Create knowledge management tools bound to a specific agent instance"""

    @tool
    def add_to_agent_knowledge(knowledge_item: str) -> str:
        """
        Add new knowledge to your internal knowledge base that will persist throughout the conversation.
        Use this tool when you learn something important from the user or conversation that you want to remember and use later.

        Examples of what to store:
        - User preferences (e.g., "User prefers email over phone calls")
        - User context (e.g., "User works in accounting department")
        - Important facts learned (e.g., "Company fiscal year ends in March")
        - Process details (e.g., "Customer approval required for orders over $5000")

        Args:
            knowledge_item: A concise piece of information to add to your knowledge

        Returns:
            Confirmation that the knowledge was added
        """
        return agent_instance.add_knowledge(knowledge_item)

    @tool
    def view_agent_knowledge() -> str:
        """
        View all current knowledge items stored in your knowledge base.
        Use this tool to see what you already know before adding duplicate information.

        Returns:
            List of all stored knowledge items with timestamps
        """
        return agent_instance.get_knowledge()

    @tool
    def clear_agent_knowledge() -> str:
        """
        Clear all knowledge items from your knowledge base.
        Use this tool if you need to start fresh with a clean knowledge state.

        Returns:
            Confirmation that all knowledge was cleared
        """
        return agent_instance.clear_knowledge()

    return [add_to_agent_knowledge, view_agent_knowledge, clear_agent_knowledge]

def load_custom_tools(agent_tools, root_folder=cfg.CUSTOM_TOOLS_FOLDER):
    try:
        custom_tools = []
        logger.info('Loading custom tools...' + str(root_folder))
        for dirpath, dirnames, filenames in os.walk(root_folder):
            # Skip the root folder itself, process only subfolders
            logger.debug('Iterating ' + str(dirpath))
            tool_name = os.path.basename(dirpath)
            if dirpath != root_folder and not os.path.isfile(dirpath) and tool_name in agent_tools:
                logger.debug('Loading tool: ' + str(dirpath))
                print('Loading tool: ' + str(dirpath))
                tool_config, tool_code = load_custom_tool(dirpath)

                print('Building custom tool...')
                function_str = build_custom_tool(tool_config, tool_code)
                function_str = replace_code_placeholders(function_str)
                exec(function_str, globals())  # globals() is necessary for the custom tools to be recognized by the agents

                custom_tools.append(tool_config['function_name'])

        print(custom_tools)
        logger.info('Custom Tools: ' + str(custom_tools))
        logger.info('Successfully loaded custom tools')
    except Exception as e:
        print(str(e))
        logger.error(str(e))

    return custom_tools



def get_agent_knowledge_for_user(agent_id, user_id=None):
    """Get knowledge items associated with an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Format results
        knowledge_items = []

        try:
            # Get knowledge items for this user if available
            cursor.execute("""
                SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date,
                    d.filename, d.document_type, d.page_count, d.batch_id
                FROM AgentKnowledge ak
                JOIN Documents d ON ak.document_id = d.document_id
                WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ak.added_by = ?
                ORDER BY ak.added_date DESC
            """, agent_id, str(user_id))

            for row in cursor.fetchall():
                knowledge_items.append({
                    'knowledge_id': row[0],
                    'agent_id': row[1],
                    'document_id': row[2],
                    'description': row[3],
                    'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                    'filename': row[5],
                    'document_type': row[6],
                    'page_count': row[7],
                    'batch_id': row[8] if row[8] else ''
                })
        except Exception as e:
            print(f"Error getting user specific agent knowledge by user: {str(e)}")
            logger.error(f"Error getting user specific agent knowledge by user: {str(e)}")

        cursor.close()
        conn.close()

        return knowledge_items
    except Exception as e:
        logger.error(f"Error getting user specific agent knowledge by user: {str(e)}")
        return []


#########################
# TOOLS
#########################
@tool
def get_user_contact_info() -> str:
    """Retrieves a user's contact details (name, email address, and phone number) so the agent can notify them via email, text, or call. Use this tool whenever the agent needs to send a message but doesn't yet have the user's contact information."""
    report_data = get_user_info()
    return report_data

@tool
def text_message_alert(message: str, destination_phone: str) -> str:
    """Sends a text message alert to the destination phone number and informs them of the content in the message.
    The function retuns 'Text message succeeded' if the call was successful and 'Text message failed' if it failed."""
    return sms_text_message_alert(message, destination_phone)

@tool
def phone_call_alert(message: str, destination_phone: str) -> str:
    """Makes a phone call alert to the destination phone number and informs them of the content in the message.
    The function retuns 'Phone call succeeded' if the call was successful and 'Phone call failed' if it failed."""
    return aihub_phone_call_alert(message, destination_phone)

@tool
def check_windows_service(hostname: str, service: str) -> str:
    """Returns the status of a Windows service on a specific server. Returns the status as 'Running', 'Not running'.
     If the function encounters an error, it will return 'Error checking status'. """
    return check_service_status(hostname, service)

@tool
def restart_windows_service(hostname: str, service: str) -> str:
    """Restarts a windows service on a remote computer."""
    result = restart_service(hostname, service)
    if result:
        return "Successfully restarted the service"
    else:
        return "Failed to restart the service"

@tool
def list_running_windows_services(hostname: str) -> str:
    """Generates a list of the running services on a remote computer."""
    report_string = list_running_services(hostname)
    return report_string

@tool
def sftp_file_report(hostname: str, username: str, password: str, folder_path: str) -> str:
    """Generates a report of files in a folder on the FTP/SFTP server with their age in minutes, file name, file path, and creation date."""
    files = list_sftp_files(hostname, username, password, folder_path)
    return str(files)

@tool
def sql_job_report() -> str:
    """Generates a report showing the status of SQL Server jobs that ran on the current day."""
    report_string = GetSQLJobStatusString(dcfg.SQL_SELECT_IN_JOBS_BI)
    return report_string

@tool
def sql_job_history_report(connection_id: int, job_name: Optional[str] = '%', hours_back: int = 24, status_filter: Optional[str] = None) -> str:
    """
    Search SQL Server job execution history.

    Parameters:
    - connection_id: Database connection ID (obtained using the get_database_connection_info tool)
    - job_name: Name of the SQL job to search
    - hours_back: How many hours of history to search (default: 24)
    - status_filter: Optional filter for status ('failed', 'succeeded', 'all')
    """
    report_string = sql_job_history_search(connection_id, job_name, hours_back, status_filter)
    return report_string

@tool
def sql_job_executor(job_name: str) -> str:
    """Executes a SQL Server job"""
    result = ExecuteSQLJob(job_name)
    if result:
        return "Job executed successfully"
    else:
        return "Job execution failed"

@tool
def sql_job_name_lookup(job_name: str) -> str:
    """Looks up the exact name of a SQL Server job using the name that was provided by a user, so the correct job is executed."""
    exact_job_name = GetExactJobNames(job_name)
    return exact_job_name

@tool
def folder_report(folder_path: str) -> str:
    """Generates a report of files in a folder with their file name, file path, and creation date."""
    report_string = GetFolderStatusAsStringSlim(folder_path)
    return report_string

@tool
def check_age_of_files_in_folder(folder_path: str) -> str:
    """Generates a report of files in a network folder (excluding FTP/SFTP folders) with their age in minutes, file name, file path, and creation date."""
    report_string = GetFilesAndAgeReport(folder_path)
    return report_string

@tool
def check_age_of_files_in_subfolders(folder_path: str) -> str:
    """Generates a report of files in a network folder and subfolders (excluding FTP/SFTP folders) with their age in minutes, file name, file path, and creation date. Only use this function if the user has requested to include subfolders."""
    report_string = GetSubfolderFilesAndAgeReport(folder_path)
    return report_string

@tool
def folder_statistics_report(folder_path: str) -> str:
    """Generates a report summarizing folder statistics including total files, total files by type, total size, for the root folder and its immediate subfolders."""
    report_string = get_folder_statistics_root_and_subfolders(folder_path)
    return report_string

@tool
def search_in_text_files(folder_path: str, search_string: str) -> str:
    """Searches for and locates specific strings within a collection of files. Returns the names of the files that contained the search string as a comma separated list."""
    list_of_files = search_text_files(folder_path, search_string, cfg.TEXT_FILE_EXTENSIONS)
    return list_of_files

# @tool
# def send_email_message(email_to: str, subject: str, message: str):
#     """Sends an email message to a specified email address."""
#     SendEmailAlert(email_to, subject, message)

@tool
def send_email_message(email_to: str, subject: str, message: str, attachment_file_path: Optional[str] = None, is_html: Optional[bool] = False) -> str:
    """Sends an email message to specified email addresses with optional file attachment."""
    email_list = [email.strip() for email in email_to.split(',')]
    result = send_email(email_list, subject, message, attachment_file_path, is_html)
    if result:
        return 'Successfully sent the email'
    else:
        return 'Failed to send the email'

@tool
def get_the_current_date() -> str:
    """Returns the current date."""
    return get_current_date()

@tool
def get_the_current_date_and_time() -> str:
    """Returns the current date and time."""
    return get_current_date_time()

@tool
def wait_seconds(seconds: float) -> str:
    """
    Pauses execution for a specified number of seconds.
    Useful for timing operations, rate limiting, or creating delays between actions.

    Args:
        seconds: Number of seconds to wait (can be fractional, e.g., 0.5 for half a second)

    Returns:
        A message confirming the wait duration
    """
    import time

    try:
        # Validate input
        if seconds < 0:
            return "Error: Cannot wait for negative seconds"

        if seconds > 300:  # 5 minute maximum to prevent excessive delays
            return "Error: Maximum wait time is 300 seconds (5 minutes)"

        # Log the wait action
        logger.info(f"Waiting for {seconds} seconds...")

        # Perform the wait
        start_time = time.time()
        time.sleep(seconds)
        actual_wait_time = time.time() - start_time

        # Return confirmation message
        return f"Successfully waited for {actual_wait_time:.2f} seconds"
    except Exception as e:
        logger.error(f"Error in wait_seconds tool: {str(e)}")
        return f"Error while waiting: {str(e)}"

@tool
def get_word_length(word: str) -> int:
    """Returns the length of a word."""
    return len(word)

@tool
def get_query_agent_info() -> str:
    """Returns a list of available natural language query agents along with their unique agent id, name, objective, and database connection id."""
    report_string = get_query_assistant_info()
    return report_string

@tool
def get_database_connection_info() -> str:
    """Returns a list of available database connections along with their unique connection id, connection name (description), database name, and database type."""
    report_string = get_database_connection_information()
    return report_string

@tool
def create_text_file(file_path: str, content: str, file_extension: str = ".txt") -> str:
    """
    Writes a string to a file with the optionally specified extension (default is .txt).

    Parameters:
    file_path (str): The full path to the file (without extension).
    content (str): The string content to write to the file.
    file_extension (str): The desired file extension (default is .txt).
    """
    return write_to_file(file_path, content, file_extension)

@tool
def load_text_file(file_path: str) -> str:
    """
    Loads the content of a text file and returns it as a string.

    Parameters:
    file_path (str): The full path to the file to be read.

    Returns:
    str: The content of the file as a string.
    """
    return load_from_file(file_path)


def dataframe_to_markdown(df):
    """Convert DataFrame to LLM-friendly format"""
    if df.empty:
        return "Empty DataFrame"

    return df.to_markdown(index=False)

def dataframe_to_csv(df):
    """Convert DataFrame to CSV format for LLM"""
    if not isinstance(df, pd.DataFrame):
        return f"Error: Expected DataFrame, got {type(df).__name__}"

    if df.empty:
        return "Empty DataFrame"

    return df.to_csv(index=False)

def dataframe_to_table_dict(df):
    """Convert DataFrame to nested dict with headers and rows structure"""
    return {
        "type": "table",
        "content": {
            "headers": df.columns.tolist(),
            "rows": df.values.tolist()
        }
    }

@tool
def ask_query_agent_a_question(agent_id: int, question: str) -> str:
    """Sends a question to a natural language query AI assistant and returns the reply from the assistant."""
    # Get user_id from thread-local context (set by GeneralAgent.run)
    user_id = getattr(_current_agent_context, 'user_id', None)

    # Fallback to RequestTracking if not in thread-local
    if user_id is None:
        user_id = RequestTracking.get_user_id()

    engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
    result = engine.get_answer(agent_id, question)

    # Handle both return formats: dict (when rich content enabled) and tuple (legacy)
    if isinstance(result, dict):
        # Rich content format - extract values from dictionary
        answer = result.get('answer', cfg.DATA_AGENT_FALLBACK_RESPONSE)
        answer_type = result.get('answer_type', 'string')
        special_message = result.get('special_message', '')
        explain = result.get('explain', '')
    else:
        # Legacy tuple format
        answer, explain, _, answer_type, special_message, _, _, _ = result

    try:
        print(86 * '@')
        print('========== ASK QUERY AGENT RESPONSE ==========')
        print(answer)
        print(86 * '@')

        final_answer = answer

        # Process the response...
        if answer_type == "dataframe":
            logger.warning("Detected dataframe...")
            # Ensure answer is a pandas DataFrame
            if not isinstance(answer, pd.DataFrame):
                print("Nonstandard dataframe detected, attempting conversion...")
                # Save the column headers from the nonstandard dataframe
                headers = answer.columns if hasattr(answer, 'columns') else None

                # Convert answer to a pandas DataFrame
                answer = pd.DataFrame(answer)

                # Manually set the headers if they were saved
                if headers is not None:
                    answer.columns = headers

            print("Returning raw DF...")
            try:
                #final_answer = dataframe_to_markdown(answer)
                final_answer = answer

                # Save the DataFrame
                print(f"Saving dataframe for user {str(user_id)} for request id {str(RequestTracking.get_user_request_id())}")
                df_manager.save_dataframe(answer, str(user_id), RequestTracking.get_user_request_id(), str(uuid.uuid4()))
            except:
                logger.warning("Encountered an issue converting dataframe to markdown, attempting CSV format instead...")
                print("Encountered an issue converting dataframe to markdown, attempting CSV format instead...")
                final_answer = dataframe_to_csv(answer)
            print('========== ASK QUERY AGENT RESPONSE (FINAL RESPONSE) ==========')
            print(final_answer)
            print(86 * '@')
        elif answer_type == "multi_dataframe":
            logger.warning("Detected multiple dataframes...")
            print('Detected multiple dataframes... ')
            try:
                final_answer = dataframe_to_markdown(answer)
                # Save the DataFrame
                print(f"Saving dataframe for user {str(user_id)} for request id {str(RequestTracking.get_user_request_id())}")
                df_manager.save_dataframe(answer, str(user_id), RequestTracking.get_user_request_id(), str(uuid.uuid4()))
            except:
                logger.warning("Encountered an issue converting multi-dataframe to markdown, attempting CSV format instead...")
                print("Encountered an issue converting multi-dataframe to markdown, attempting CSV format instead...")
                final_answer = dataframe_to_csv(answer)
            print('========== ASK QUERY AGENT RESPONSE (FINAL RESPONSE) ==========')
            print(final_answer)
            print(86 * '@')
    except Exception as e:
        print(f"Failed to process response from query agent - {str(e)}")
        logger.error(f"Failed to process response from query agent - {str(e)}")

    return final_answer

@tool
def search_documents_by_order(order_number: str, document_type: str = None) -> str:
    """Fetch information for various types of documents such as invoices, purchase orders, and bill of ladings to assist users with research. The function searches by order number and optional document type."""
    document_report = get_documents_by_order(order_number, document_type)
    return document_report

@tool
def list_document_types() -> str:
    """
    Returns a list of all available document types in the system.

    Call this function when you need to know what document types exist in the system
    before using other document search or metadata functions. This ensures you're using exact,
    valid document type strings that the system recognizes.

    Returns:
        str: JSON string with document types
    """
    return get_document_types()

@tool
def list_document_fields(document_types: Optional[List[str]] = None) -> str:
    """
    Returns a list of all available document fields in the database with sample values.

    Call this function when you need to know what document fields exist in the system
    before using other document search functions. This ensures you're using exact,
    valid document field name strings that the system recognizes.

    The response includes up to 3 sample values for each field (configurable via DOC_FIELD_SAMPLE_VALUES_COUNT).

    Args:
        document_types (Optional[List[str]], optional): List of document types to filter by. Defaults to None for all types.

    Returns:
        str: JSON string with document types and their associated fields with sample values
    """
    return get_document_fields(document_types)

@tool
def search_documents(user_question: str, field_filters: List[Dict[str, str]], document_type: Optional[str] = None) -> str:
    """
    Search documents with flexible field filtering and return results as JSON string.
    Designed for AI agent analysis of document data.

    ### Parameters:
    user_question: Users original question for context
    field_filters: List of field filters in the format (preferred search method):
        [
            {
                'field_name': 'total_amount',
                'operator': 'equals',  # One of: equals, contains, starts_with, ends_with
                'value': '500.00'
            },
            # Additional filters...
        ]
    document_type: Filter by specific document type

    ### Returns: JSON string containing:
        - results: List of document results
        - available_fields: Available fields for search
        - document_types: List of available document types
        - document_counts: Document count by type
    """
    conn_str = get_db_connection_string()
    include_metadata = False
    if document_type and field_filters == [] and user_question:
        include_metadata = True
    return document_search(conn_str, document_type=document_type, field_filters=field_filters, include_metadata=include_metadata, max_results=cfg.DOC_SEARCH_LIMIT, user_question=user_question, check_completeness=cfg.DOC_CHECK_COMPLETENESS)

@tool
def document_super_search(user_question: str) -> str:
    """
    Search documents and return results as JSON string.
    Designed for AI agent analysis of document data.

    ### Parameters:
    user_question: Users original question for context

    ### Returns: JSON string containing document search results and metadata
    """
    conn_str = get_db_connection_string()
    return document_search_super_enhanced_debug(conn_str, user_question=user_question, max_results=cfg.DOC_SEARCH_LIMIT, check_completeness=cfg.DOC_CHECK_COMPLETENESS)

@tool
def search_documents_meaning(document_type: Optional[str] = None, search_query: Optional[str] = None) -> str:
    """
    Searches documents based on the meaning of the text within the search query and return results as JSON string.
    Designed for AI agent analysis of document data.

    ### Parameters:
    document_type:  Filter by specific document type
    search_query: Text to search for in documents

    ### Returns: JSON string containing:
        - results: List of document results
        - available_fields: Available fields for search
        - document_types: List of available document types
        - document_counts: Document count by type
    """
    conn_str = get_db_connection_string()
    return document_search(conn_str, document_type=document_type, search_query=search_query, include_metadata=False, max_results=cfg.DOC_SEARCH_LIMIT)

@tool
def get_document_universe_metadata(document_types: Optional[List[str]] = None) -> str:
    """
    Provides comprehensive metadata about the document universe to help AI understand
    document types, fields, relationships, and usage patterns.

    This function is designed to be called before search operations to give the AI
    context about the document ecosystem.

    ### Parameters:
    document_types : List of specific document types to get metadata for. If None, metadata for all documents is returned

    ### Returns: JSON string containing:
        - document_types: List of all document types with counts and descriptions
        - field_metadata: Detailed information about all fields
        - common_field_combinations: Frequently co-occurring fields
        - search_recommendations: Suggestions for effective search combinations
        - field_value_examples: Sample values for key fields to aid in pattern recognition
    """
    #document_types: Optional[List[str]] = None
    #document_types = None
    conn_str = get_db_connection_string()
    return get_document_universe(conn_str, document_types=document_types)

@tool
def get_next_document_page(page_id: str) -> str:
    """
    Retrieve the next page in a document based on the current page ID.

    Parameters:
    -----------
    page_id : The ID of the current page

    Returns:
    --------
    str
        JSON string containing the next page data or an error message
    """
    return get_next_document_page_util(get_db_connection_string(), page_id)

@tool
def get_previous_document_page(page_id: str) -> str:
    """
    Retrieve the previous page in a document based on the current page ID.

    Parameters:
    -----------
    page_id : The ID of the current page

    Returns:
    --------
    str
        JSON string containing the previous page data or an error message
    """
    return get_previous_document_page_util(get_db_connection_string(), page_id)

@tool
def get_document_pages_legacy(document_id: Optional[str] = None, filename: Optional[str] = None) -> str:
    """
    Retrieve all pages from a specific document using either document ID or filename.

    Parameters:
    -----------
    document_id : Optional[str] = None
        The ID of the document
    filename : Optional[str] = None
        The filename to search for

    Returns:
    --------
    str
        JSON string containing the pages data or an error message
    """
    import json

    # Validate input parameters
    if not document_id and not filename:
        return json.dumps({"error": "Either document_id or filename must be provided"})

    # If filename is provided, get the document_id first
    if filename and not document_id:
        doc_id_result = get_document_id_by_filename(filename)
        try:
            doc_id_data = json.loads(doc_id_result)
            if "error" in doc_id_data:
                return doc_id_result  # Return the error from filename lookup

            # The function returns a results array, so we need to extract from there
            results = doc_id_data.get("results", [])
            if not results:
                return json.dumps({"error": f"No documents found with filename: {filename}"})

            # If multiple results, take the first one (or could return all matches)
            if len(results) > 1:
                # Return info about multiple matches for user to choose
                return json.dumps({
                    "error": f"Multiple documents found with filename pattern '{filename}'",
                    "matches": [{"document_id": r["document_id"], "filename": r["filename"]} for r in results],
                    "suggestion": "Please use document_id parameter with specific document ID, or use a more specific filename"
                })

            document_id = results[0].get("document_id")
            if not document_id:
                return json.dumps({"error": f"No document ID found for filename: {filename}"})
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid response when looking up filename: {filename}"})

    # Now get the document pages using the document_id
    return get_document_by_id(get_db_connection_string(), document_id=document_id)

@tool
def get_document_page_by_number(document_id: str, page_number: int) -> str:
    """
    Retrieve a specific page from a document by its number.

    Parameters:
    -----------
    document_id : The ID of the document
    page_number : The page number to retrieve

    Returns:
    --------
    str
        JSON string containing the page data or an error message
    """
    return get_document_page_by_number_util(get_db_connection_string(), document_id=document_id, page_number=page_number)

@tool
def get_document_pages(document_ids: Optional[List[str]] = None, filenames: Optional[List[str]] = None) -> str:
    """
    Retrieve all pages from multiple documents using either document IDs or filenames.

    Parameters:
    -----------
    document_ids : Optional[List[str]] = None
        List of document IDs to retrieve
    filenames : Optional[List[str]] = None
        List of filenames to search for

    Returns:
    --------
    str
        JSON string containing the pages data for all documents or an error message
        Format: {
            "documents": {
                "document_id_1": {"pages": [...], "error": None},
                "document_id_2": {"pages": [...], "error": None},
                ...
            },
            "summary": {...},
            "error": None
        }
    """
    import json

    # Validate input parameters
    if not document_ids and not filenames:
        return json.dumps({"error": "Either document_ids or filenames must be provided"})

    final_document_ids = []

    # If filenames are provided, get the document IDs first
    if filenames:
        filenames_result = get_document_ids_by_filenames(filenames)
        try:
            filenames_data = json.loads(filenames_result)
            if "error" in filenames_data:
                return filenames_result  # Return the error from filename lookup

            # Extract document IDs from the results
            results = filenames_data.get("results", [])
            if not results:
                return json.dumps({
                    "error": f"No documents found with filenames: {filenames}",
                    "summary": {
                        "total_requested_filenames": len(filenames),
                        "found": 0,
                        "not_found": len(filenames),
                        "not_found_filenames": filenames
                    }
                })

            # Collect all document IDs found
            for result in results:
                doc_id = result.get("document_id")
                if doc_id:
                    final_document_ids.append(doc_id)

            if not final_document_ids:
                return json.dumps({
                    "error": f"No valid document IDs found for filenames: {filenames}",
                    "filename_search_results": results
                })

        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid response when looking up filenames: {filenames}"})
    else:
        # Use provided document IDs
        final_document_ids = document_ids

    # Now get all document pages using the document IDs
    documents_result = get_documents_by_ids(get_db_connection_string(), final_document_ids)

    try:
        # Parse the result to add additional metadata if needed
        result_data = json.loads(documents_result)

        # If we used filenames, add filename mapping information
        if filenames:
            result_data["filename_mapping"] = {
                "original_filenames": filenames,
                "resolved_document_ids": final_document_ids,
                "filename_search_performed": True
            }

        # Add operation metadata
        result_data["operation"] = {
            "type": "multiple_document_pages",
            "input_type": "filenames" if filenames else "document_ids",
            "input_count": len(filenames) if filenames else len(document_ids),
            "resolved_document_ids": final_document_ids
        }

        return json.dumps(result_data, default=str)

    except json.JSONDecodeError:
        # If parsing fails, return the original result
        return documents_result

@tool
def find_potential_search_fields(original_field: str, document_type: str) -> List[str]:
    """
    Suggests alternative fields to search when original field returns no results.

    Uses AI to identify relevant field alternatives when terminology mismatches occur
    between user queries and database field names. Call this when document searches
    return empty results to find better fields to search on.

    Parameters:
    -----------
    original_field : Original field that returned no results
    document_type : Type of document to search

    Returns:
    --------
    List[str]
        Alternative fields to try, ordered by relevance
    """
    return ask_ai_for_best_field(original_field, document_type)

@tool
def find_potential_search_fields_deprecated(original_field: str, document_type: str, user_question: Optional[str] = None) -> List[str]:
    """
    Suggests alternative fields to search when original field returns no results.

    Uses AI to identify relevant field alternatives when terminology mismatches occur
    between user queries and database field names. Call this when document searches
    return empty results to find better fields to search on.

    Parameters:
    -----------
    original_field : Original field that returned no results
    document_type : Type of document to search
    user_question : User's original question for context

    Returns:
    --------
    List[str]
        Alternative fields to try, ordered by relevance
    """
    return ask_ai_for_best_field(original_field, document_type, user_question)

@tool
def run_python_code(code: str) -> str:
    """
    This function accepts a string of Python code, executes it, and captures any output or errors as a string.
    This function will allow you to execute code dynamically if you do not have a specific tool to fulfill a users request.

    Parameters: Python code as a string.
    Returns: The output of the code execution as a string, or any exceptions raised.
    """
    import sys
    from io import StringIO

    # Redirect stdout to capture print statements
    old_stdout = sys.stdout
    redirected_output = StringIO()
    sys.stdout = redirected_output

    try:
        # Execute the code
        exec(code, {}, {})
        # Retrieve the output and force it to string
        result = redirected_output.getvalue()
    except Exception as e:
        # Catch and return exceptions as a string
        result = f"Error: {e}"
    finally:
        # Restore the original stdout
        sys.stdout = old_stdout

    return str(result)

# Database Query Tool
@tool
def query_database(connection_id: int, query: str) -> str:
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
        return query_a_database(connection_id=connection_id, query=query)
    except Exception as e:
        logger.error(f"Error executing database query: {str(e)}")
        return f"Error executing query: {str(e)}"


# CSV Processor Tool
@tool
def process_csv(file_path: str, operation: str, parameters: Optional[Dict[str, Any]] = None) -> str:
    """
    Process a CSV file with various operations like summarize, filter, transform, etc.
    Use this tool when you need to analyze or manipulate data in CSV files.

    Args:
        file_path: The path to the CSV file
        operation: The operation to perform (summarize, filter, transform, etc.)
        parameters: Optional parameters specific to the operation
            - For 'summarize': 'columns' (list of column names to include)
            - For 'filter': 'condition' (e.g., "column1 > 10")
            - For 'transform': 'transformations' (dict mapping columns to expressions)

    Returns:
        The result of the operation as a formatted string
    """
    try:
        # Check if file exists
        if not os.path.isfile(file_path):
            return f"Error: File not found at {file_path}"

        # Read the CSV file
        df = pd.read_csv(file_path)

        # Initialize parameters if not provided
        if parameters is None:
            parameters = {}

        # Process based on operation
        operation = operation.lower()

        if operation == 'summarize':
            # Get summary statistics
            columns = parameters.get('columns')
            if columns:
                # Filter to specified columns if provided
                df = df[columns]

            # Get different statistics
            count = len(df)
            summary = df.describe(include='all').to_string()
            null_counts = df.isnull().sum().to_string()

            return f"CSV Summary ({file_path}):\n\nTotal Rows: {count}\n\nStatistics:\n{summary}\n\nNull Values:\n{null_counts}"

        elif operation == 'filter':
            # Filter data based on condition
            condition = parameters.get('condition')
            if not condition:
                return "Error: No filter condition provided. Example: column1 > 10"

            try:
                # Use pandas query to filter
                filtered_df = df.query(condition)

                if filtered_df.empty:
                    return f"No rows matched the filter condition: {condition}"

                # Return filtered data
                if len(filtered_df) > 50:
                    result = filtered_df.head(50).to_string(index=False)
                    return f"{result}\n\n[Showing first 50 rows of {len(filtered_df)} filtered rows]"
                else:
                    return filtered_df.to_string(index=False)
            except Exception as e:
                return f"Error applying filter: {str(e)}"

        elif operation == 'transform':
            # Apply transformations to columns
            transformations = parameters.get('transformations', {})
            if not transformations:
                return "Error: No transformations provided. Example: {'price': 'price * 0.9'}"

            try:
                # Apply each transformation
                transformed_df = df.copy()
                for col, expr in transformations.items():
                    transformed_df[col] = transformed_df.eval(expr)

                # Return sample of transformed data
                if len(transformed_df) > 50:
                    result = transformed_df.head(50).to_string(index=False)
                    return f"{result}\n\n[Showing first 50 rows of {len(transformed_df)} transformed rows]"
                else:
                    return transformed_df.to_string(index=False)
            except Exception as e:
                return f"Error applying transformations: {str(e)}"

        elif operation == 'columns':
            # List columns and their types
            columns_info = []
            for col in df.columns:
                dtype = str(df[col].dtype)
                sample = str(df[col].iloc[0]) if not df.empty else "N/A"
                if len(sample) > 50:
                    sample = sample[:50] + "..."
                columns_info.append(f"{col} ({dtype}) - Sample: {sample}")

            return "CSV Columns:\n" + "\n".join(columns_info)

        elif operation == 'head':
            # Show first few rows
            rows = parameters.get('rows', 10)
            return df.head(rows).to_string(index=False)

        elif operation == 'aggregate':
            # Perform aggregation
            group_by = parameters.get('group_by')
            agg_func = parameters.get('aggregation', 'count')

            if not group_by:
                return "Error: No 'group_by' column provided"

            try:
                grouped = df.groupby(group_by).agg(agg_func)
                return grouped.to_string()
            except Exception as e:
                return f"Error performing aggregation: {str(e)}"

        else:
            return f"Error: Unsupported operation '{operation}'. Supported operations are: summarize, filter, transform, columns, head, aggregate"

    except Exception as e:
        logger.error(f"Error processing CSV file: {str(e)}")
        return f"Error processing CSV file: {str(e)}"

@tool
def show_csv(file_path: str, rows: int = 20) -> str:
    """
    Display the contents of a CSV file in a readable format.
    Use this tool when a user wants to see what's inside a CSV file.

    Args:
        file_path: The path to the CSV file
        rows: Number of rows to show (default: 20)

    Returns:
        The contents of the CSV file as a formatted table
    """
    try:
        # Check if file exists
        if not os.path.isfile(file_path):
            return f"Error: File not found at {file_path}"

        # Read the CSV file
        df = pd.read_csv(file_path)

        # Get basic file info
        total_rows = len(df)
        total_columns = len(df.columns)

        # Display data
        if total_rows <= rows:
            result = df.to_string(index=False)
            return f"CSV File: {file_path}\nTotal Rows: {total_rows}, Total Columns: {total_columns}\n\n{result}"
        else:
            result = df.head(rows).to_string(index=False)
            return f"CSV File: {file_path}\nTotal Rows: {total_rows}, Total Columns: {total_columns}\n(Showing first {rows} rows)\n\n{result}"

    except Exception as e:
        logger.error(f"Error displaying CSV file: {str(e)}")
        return f"Error reading CSV file: {str(e)}"

# API Connector Tool
@tool
def call_external_api(endpoint: str, method: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, body: Optional[Union[Dict[str, Any], List[Any], str]] = None) -> str:
    """
    Make requests to external APIs and return the response.
    Use this tool when you need to fetch data from external services or integrate with other systems.

    Args:
        endpoint: The API endpoint URL
        method: The HTTP method (GET, POST, PUT, DELETE, etc.)
        params: Optional query parameters as a dictionary
        headers: Optional HTTP headers as a dictionary
        body: Optional request body (for POST/PUT) as a dictionary, list, or string

    Returns:
        The API response formatted as a string
    """
    try:
        # Initialize parameters if not provided
        if params is None:
            params = {}
        if headers is None:
            headers = {}

        # Set default Content-Type header for POST/PUT requests with dict/list body
        if method.upper() in ['POST', 'PUT'] and body and isinstance(body, (dict, list)) and 'Content-Type' not in headers:
            headers['Content-Type'] = 'application/json'

        # Convert body to JSON string if it's a dict or list
        if body and isinstance(body, (dict, list)):
            body = json.dumps(body)

        # Make the API request
        method = method.upper()
        response = None

        if method == 'GET':
            response = requests.get(endpoint, params=params, headers=headers)
        elif method == 'POST':
            response = requests.post(endpoint, params=params, headers=headers, data=body)
        elif method == 'PUT':
            response = requests.put(endpoint, params=params, headers=headers, data=body)
        elif method == 'DELETE':
            response = requests.delete(endpoint, params=params, headers=headers)
        elif method == 'PATCH':
            response = requests.patch(endpoint, params=params, headers=headers, data=body)
        else:
            return f"Error: Unsupported HTTP method '{method}'"

        # Check if the request was successful
        response.raise_for_status()

        # Try to parse JSON response
        try:
            json_response = response.json()
            # Format JSON response for better readability
            formatted_response = json.dumps(json_response, indent=2)

            # Truncate if too long
            if len(formatted_response) > 2000:
                formatted_response = formatted_response[:2000] + "\n...[Response truncated due to length]"

            return formatted_response
        except ValueError:
            # Return text response if not JSON
            text_response = response.text

            # Truncate if too long
            if len(text_response) > 2000:
                text_response = text_response[:2000] + "\n...[Response truncated due to length]"

            return text_response

    except requests.exceptions.RequestException as e:
        logger.error(f"API request error: {str(e)}")

        # Extract more details from the error
        error_details = str(e)
        if hasattr(e, 'response') and e.response is not None:
            status_code = e.response.status_code
            try:
                error_body = e.response.json()
                error_details = f"Status Code: {status_code}, Error: {json.dumps(error_body, indent=2)}"
            except:
                error_details = f"Status Code: {status_code}, Error: {e.response.text}"

        return f"Error making API request: {error_details}"
    except Exception as e:
        logger.error(f"Unexpected error in API connector: {str(e)}")
        return f"Unexpected error: {str(e)}"

# Tavily (default, with fallback if error)
from WebSearch import WebSearch
@tool
def web_search_basic(query: str, default_engine: str = cfg.DEFAULT_INTERNET_SEARCH, api_key: str = cfg.DEFAULT_INTERNET_SEARCH_KEY) -> str:
    """Search the web for real-time information. Uses Tavily (if API key is set) or falls back to DuckDuckGo."""
    searcher = WebSearch(api_key=api_key, default_engine=default_engine)
    results = searcher.search(query)

    formatted_results = []

    for r in results:
        # Handle AI answer format (likely from Tavily)
        if 'ai_answer' in r and not all(k in r for k in ['title', 'link', 'snippet']):
            # Skip AI answers or format them differently
            formatted_results.append(f"AI Summary: {r['ai_answer']}")

        # Handle standard search result format
        elif all(k in r for k in ['title', 'link', 'snippet']):
            formatted_results.append(
                f"{r['title']}\n{r['link']}\n{r['snippet']}"
            )

        # Handle partial results (missing some keys)
        else:
            parts = []
            if 'title' in r:
                parts.append(r['title'])
            if 'link' in r:
                parts.append(r['link'])
            if 'snippet' in r:
                parts.append(r['snippet'])
            if parts:  # Only add if we have at least some content
                formatted_results.append('\n'.join(parts))

    return "\n\n".join(formatted_results)

@tool
def web_search(queries: Union[str, List[str]], default_engine: str = cfg.DEFAULT_INTERNET_SEARCH, api_key: str = cfg.DEFAULT_INTERNET_SEARCH_KEY) -> str:
    """
    Search the web for real-time information. Accepts a single query or a list of queries.
    Uses Tavily (if API key is set) or falls back to DuckDuckGo.

    Args:
        queries: A single search query string or a list of search query strings
        default_engine: The search engine to use (default from config)
        api_key: API key for the search service (default from config)

    Returns:
        Formatted search results, organized by query if multiple queries provided
    """
    # Convert single query to list for uniform processing
    if isinstance(queries, str):
        queries = [queries]

    searcher = WebSearch(api_key=api_key, default_engine=default_engine)
    all_results = []

    for query in queries:
        # Add query header if processing multiple queries
        if len(queries) > 1:
            all_results.append(f"=== Results for: '{query}' ===")

        try:
            results = searcher.search(query)
            formatted_results = []

            for r in results:
                # Handle AI answer format (likely from Tavily)
                if 'ai_answer' in r and not all(k in r for k in ['title', 'link', 'snippet']):
                    # Skip AI answers or format them differently
                    formatted_results.append(f"AI Summary: {r['ai_answer']}")

                # Handle standard search result format
                elif all(k in r for k in ['title', 'link', 'snippet']):
                    formatted_results.append(
                        f"{r['title']}\n{r['link']}\n{r['snippet']}"
                    )

                # Handle partial results (missing some keys)
                else:
                    parts = []
                    if 'title' in r:
                        parts.append(r['title'])
                    if 'link' in r:
                        parts.append(r['link'])
                    if 'snippet' in r:
                        parts.append(r['snippet'])
                    if parts:  # Only add if we have at least some content
                        formatted_results.append('\n'.join(parts))

            if formatted_results:
                all_results.append("\n\n".join(formatted_results))
            else:
                all_results.append(f"No results found for query: '{query}'")

        except Exception as e:
            all_results.append(f"Error searching for '{query}': {str(e)}")

        # Add separator between different query results if multiple queries
        if len(queries) > 1 and query != queries[-1]:
            all_results.append("\n" + "="*50 + "\n")

    return "\n\n".join(all_results)


#internet_search_tool = get_web_search_tool(api_key=cfg.DEFAULT_INTERNET_SEARCH_KEY, default_engine=cfg.DEFAULT_INTERNET_SEARCH)


############################
##### ENHANCED DOCS    #####
############################
#from DocUtilsEnhanced import *

# @tool
# def document_intelligent_search(user_question: str, max_results: int = 50, force_strategy: Optional[str] = None) -> str:
#     """
#     Intelligent document search that automatically adapts response based on result size and question type.

#     The function analyzes the ACTUAL results returned and decides how to present them:
#     - Small result sets: Returns all results
#     - Large result sets: Returns summaries with drill-down options
#     - Token-heavy results: Automatically summarizes to fit context

#     ### Parameters:
#     user_question: User's question for context
#     max_results: How many results you want to retrieve (you decide based on your needs)
#     force_strategy: Force a specific presentation - 'full_results', 'smart_summary', 'clustered_summary', 'progressive_disclosure'

#     ### Returns:
#     JSON with intelligent formatting based on actual result characteristics
#     """
#     conn_str = get_db_connection_string()
#     return document_search_super_enhanced_with_intelligent_sizing(
#         conn_string=conn_str,
#         user_question=user_question,
#         max_results=max_results,  # AI specifies this
#         check_completeness=cfg.DOC_CHECK_COMPLETENESS,
#         force_strategy=force_strategy
#     )

@tool
def document_intelligent_search(user_question: str, max_results: int = 50, force_strategy: Optional[str] = None) -> str:
    """
    Intelligent document search that automatically adapts response based on result size and question type.
    NOW WITH AI POST-PROCESSING for better result relevance.

    The function analyzes the ACTUAL results returned and decides how to present them:
    - Small result sets: Returns all results (filtered for relevance)
    - Large result sets: Returns summaries with drill-down options
    - Token-heavy results: Automatically summarizes to fit context
    - AI Filtering: Ensures results actually match user's intent (active vs inactive, etc.)

    ### Parameters:
    user_question: User's question for context
    max_results: How many results you want to retrieve (you decide based on your needs)
    force_strategy: Force a specific presentation - 'full_results', 'smart_summary', 'clustered_summary', 'progressive_disclosure'

    ### Returns:
    JSON with intelligent formatting AND AI-filtered results based on actual result characteristics
    """
    conn_str = get_db_connection_string()

    # Get the original intelligent search results
    original_response = document_search_super_enhanced_with_intelligent_sizing(
        conn_string=conn_str,
        user_question=user_question,
        max_results=max_results,
        check_completeness=cfg.DOC_CHECK_COMPLETENESS,
        force_strategy=force_strategy
    )

    # Apply AI post-processing if enabled
    if not cfg.AI_FILTER_ENABLE_BY_DEFAULT:
        return original_response

    try:
        response_data = json.loads(original_response)

        # Apply AI post-processing to results if present
        if response_data.get("results"):
            original_count = len(response_data["results"])

            # Apply AI filtering to the results
            filtered_results = ai_post_process_intelligent_search_results(
                search_results=response_data["results"],
                user_question=user_question,
                max_results_to_analyze=cfg.AI_FILTER_MAX_RESULTS_TO_ANALYZE
            )

            # Update the response
            response_data["results"] = filtered_results

            # Add AI post-processing metadata
            response_data["ai_post_processing"] = {
                "applied": True,
                "original_count": original_count,
                "filtered_count": len(filtered_results),
                "filtering_reason": "AI relevance analysis"
            }

            # Update any count-related metadata
            if "response_strategy" in response_data:
                response_data["response_strategy"]["post_ai_filtering"] = {
                    "original_count": original_count,
                    "filtered_count": len(filtered_results)
                }
        else:
            response_data["ai_post_processing"] = {
                "applied": False,
                "reason": "No results to filter"
            }

        return json.dumps(response_data, default=str)

    except Exception as e:
        print(f"Error in AI post-processing: {str(e)}")
        return original_response  # Return original results if enhancement fails

@tool
def drill_down_document_type(document_type: str, original_question: str, max_results: int = 20) -> str:
    """
    Get detailed results for a specific document type.

    ### Parameters:
    document_type: The specific document type to focus on
    original_question: The original user question for context
    max_results: How many detailed results you want (you decide)

    ### Returns:
    JSON string with detailed document results for the specified type
    """
    conn_str = get_db_connection_string()
    return drill_down_by_document_type(
        conn_string=conn_str,
        document_type=document_type,
        user_question=original_question,
        max_results=max_results  # AI specifies this
    )

@tool
def drill_down_by_field(field_name: str, field_value: str, max_results: int = 20, document_type: Optional[str] = None) -> str:
    """
    Get detailed results for documents with specific field values.

    ### Parameters:
    field_name: Name of the field to filter by
    field_value: Specific value to look for
    max_results: How many results you want (you decide)
    document_type: Optional document type to limit search

    ### Returns:
    JSON string with documents matching the field criteria
    """
    conn_str = get_db_connection_string()
    return drill_down_by_field_value(
        conn_string=conn_str,
        field_name=field_name,
        field_value=field_value,
        max_results=max_results,  # AI specifies this
        document_type=document_type
    )

@tool
def get_document_page(user_question: str, page: int, page_size: int, document_type: Optional[str] = None) -> str:
    """
    Get paginated document search results.

    ### Parameters:
    user_question: Original search question
    page: Page number to retrieve (1-based)
    page_size: Number of results per page (you decide based on context)
    document_type: Optional filter by document type

    ### Returns:
    JSON string with paginated results and pagination metadata
    """
    conn_str = get_db_connection_string()
    return get_paginated_results(
        conn_string=conn_str,
        user_question=user_question,
        page=page,
        page_size=page_size,  # AI specifies this
        document_type=document_type
    )

@tool
def analyze_document_result_strategy(user_question: str, sample_results: str) -> str:
    """
    Analyze what strategy would be best for presenting document search results.

    Use this to understand how to best present results to users based on their question type.

    ### Parameters:
    user_question: The user's original question
    sample_results: JSON string of sample search results

    ### Returns:
    Analysis of the best presentation strategy and reasoning
    """
    try:
        results = json.loads(sample_results) if isinstance(sample_results, str) else sample_results
        if isinstance(results, dict) and "results" in results:
            results = results["results"]

        strategy = determine_response_strategy(user_question, results)

        return json.dumps({
            "recommended_strategy": strategy,
            "result_analysis": calculate_result_set_size(results),
            "suggestions": [
                "Use drill_down_document_type for specific document types",
                "Use drill_down_by_field for specific field values",
                "Use get_document_page for pagination",
                "Use document_intelligent_search with force_strategy parameter"
            ]
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Failed to analyze results: {str(e)}"})


############################################################################
###################### DOCUMENT LINK EXTRACTION TOOLS ######################
############################################################################
@tool
def get_document_paths(search_results: Union[str, Dict, List[Dict]], format_as_links: bool = True) -> str:
    """
    Extract document paths/links from search results or document data.

    This tool helps AI agents quickly get file paths and formatted links to documents
    from various search result formats (JSON strings, dictionaries, or lists).

    ### Parameters:
    search_results: Search results from document search tools (can be JSON string, dict, or list)
    format_as_links: If True, formats paths as clickable links for the UI (default: True)

    ### Returns:
    Formatted string containing document information with paths/links

    ### Example Usage:
    - After using document_super_search: get_document_paths(search_results)
    - From search_documents results: get_document_paths(results, format_as_links=True)
    - From a specific document dict: get_document_paths(document_dict)
    """
    try:
        # Parse input if it's a JSON string
        if isinstance(search_results, str):
            try:
                data = json.loads(search_results)
            except json.JSONDecodeError:
                return "Error: Invalid JSON string provided"
        else:
            data = search_results

        # Extract results list from various formats
        results = []
        if isinstance(data, dict):
            if 'results' in data:
                results = data['results']
            elif 'top_results' in data:
                results = data['top_results']
            elif 'page' in data and isinstance(data['page'], dict):
                # Handle single page result
                results = [data['page']]
            elif 'document_id' in data:
                # Single document dict
                results = [data]
            else:
                # Check if it's a response with clusters
                if 'clusters' in data:
                    for cluster in data['clusters']:
                        if 'sample_documents' in cluster:
                            results.extend(cluster['sample_documents'])
        elif isinstance(data, list):
            results = data

        if not results:
            return "No documents found in the provided data"

        # Build formatted output
        output_lines = []
        output_lines.append(f"Found {len(results)} document(s) with paths:\n")

        for idx, doc in enumerate(results, 1):
            # Extract key information
            doc_id = doc.get('document_id', 'Unknown')
            filename = doc.get('filename', 'Unknown')
            doc_type = doc.get('document_type', 'Unknown')
            page_num = doc.get('page_number', '')
            page_count = doc.get('page_count', '')

            # Get the path - check various possible field names
            path = (doc.get('archived_path') or
                   doc.get('link_to_document') or
                   doc.get('path_to_document') or
                   doc.get('document_url') or
                   '')

            # Format document info
            output_lines.append(f"\n{idx}. {filename}")
            output_lines.append(f"   Type: {doc_type}")
            output_lines.append(f"   Document ID: {doc_id}")

            if page_num:
                output_lines.append(f"   Page: {page_num}{f' of {page_count}' if page_count else ''}")

            # Add reference number if available
            ref_num = doc.get('reference_number')
            if ref_num:
                output_lines.append(f"   Reference: {ref_num}")

            # Format the path/link
            if path:
                if format_as_links:
                    # Format as a clickable link that will work in the UI
                    # Ensure proper formatting for the document/serve endpoint
                    if not path.startswith('/document/serve/'):
                        # Replace backslashes with forward slashes
                        formatted_path = path.replace('\\', '/')
                        # Add the serve endpoint prefix
                        if not formatted_path.startswith('/'):
                            formatted_path = f"/document/serve/{formatted_path}"
                        else:
                            formatted_path = f"/document/serve{formatted_path}"
                    else:
                        formatted_path = path

                    output_lines.append(f"   Path: {formatted_path}")
                else:
                    # Just show the raw path
                    output_lines.append(f"   Path: {path}")
            else:
                output_lines.append("   Path: Not available")

            # Add any additional metadata that might be useful
            customer_id = doc.get('customer_id')
            vendor_id = doc.get('vendor_id')
            doc_date = doc.get('document_date')

            if customer_id:
                output_lines.append(f"   Customer: {customer_id}")
            if vendor_id:
                output_lines.append(f"   Vendor: {vendor_id}")
            if doc_date:
                output_lines.append(f"   Date: {doc_date}")

        # Add summary at the end
        output_lines.append(f"\n{'='*60}")
        output_lines.append("Document paths are formatted for direct access.")
        if format_as_links:
            output_lines.append("Links will be clickable in the chat interface.")

        return '\n'.join(output_lines)

    except Exception as e:
        logger.error(f"Error extracting document paths: {str(e)}")
        return f"Error extracting document paths: {str(e)}"


@tool
def get_document_direct_links(document_ids: List[str], include_all_pages: bool = False) -> str:
    """
    Generate direct access links for specific documents by their IDs.

    Use this when you have document IDs and need to generate direct links,
    or when you want to provide links to all pages of a document.

    ### Parameters:
    document_ids: List of document IDs to get links for
    include_all_pages: If True, generates links for all pages of multi-page documents

    ### Returns:
    Formatted string with direct links to the documents
    """
    try:
        conn_str = get_db_connection_string()
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        output_lines = []
        output_lines.append(f"Direct links for {len(document_ids)} document(s):\n")

        for doc_id in document_ids:
            # Get document info
            cursor.execute("""
                SELECT document_id, filename, document_type, page_count,
                       archived_path, reference_number, customer_id, vendor_id
                FROM Documents
                WHERE document_id = ?
            """, doc_id)

            doc_info = cursor.fetchone()
            if not doc_info:
                output_lines.append(f"\n❌ Document ID '{doc_id}' not found")
                continue

            # Extract document details
            _, filename, doc_type, page_count, archived_path, ref_num, customer_id, vendor_id = doc_info

            output_lines.append(f"\n📄 {filename}")
            output_lines.append(f"   Type: {doc_type}")
            output_lines.append(f"   Pages: {page_count}")

            if ref_num:
                output_lines.append(f"   Reference: {ref_num}")
            if customer_id:
                output_lines.append(f"   Customer: {customer_id}")
            if vendor_id:
                output_lines.append(f"   Vendor: {vendor_id}")

            # Generate the main document link
            if archived_path:
                formatted_path = archived_path.replace('\\', '/')
                if not formatted_path.startswith('/document/serve/'):
                    if not formatted_path.startswith('/'):
                        formatted_path = f"/document/serve/{formatted_path}"
                    else:
                        formatted_path = f"/document/serve{formatted_path}"

                output_lines.append(f"   Full Document: {formatted_path}")

                # If requested, generate links for individual pages
                if include_all_pages and page_count > 1:
                    output_lines.append("   Individual Pages:")
                    for page_num in range(1, page_count + 1):
                        # You might need to adjust this based on how your document viewer handles page parameters
                        page_link = f"{formatted_path}#page={page_num}"
                        output_lines.append(f"     - Page {page_num}: {page_link}")
            else:
                output_lines.append("   ⚠️  No file path available")

        conn.close()

        output_lines.append(f"\n{'='*60}")
        output_lines.append("Links are formatted for direct access in the chat interface.")

        return '\n'.join(output_lines)

    except Exception as e:
        logger.error(f"Error generating document links: {str(e)}")
        return f"Error generating document links: {str(e)}"


@tool
def extract_document_paths_from_conversation(conversation_history: List[Dict[str, str]],
                                           return_unique_only: bool = True) -> str:
    """
    Extract all document paths/links mentioned in the conversation history.

    Useful for summarizing all documents that have been discussed or for
    creating a reference list of all documents mentioned in the conversation.

    ### Parameters:
    conversation_history: List of conversation messages with 'role' and 'content'
    return_unique_only: If True, returns only unique documents (default: True)

    ### Returns:
    Formatted string with all document paths found in the conversation
    """
    try:
        import re

        # Patterns to find document references
        patterns = [
            r'/document/serve/[^\s"\'<>]+',  # Direct serve links
            r'\\\\[^\s"\'<>]+\.[a-zA-Z]{3,4}',  # UNC paths with file extensions
            r'document_id["\s:]+([a-zA-Z0-9-]+)',  # Document IDs
            r'filename["\s:]+([^"\'<>,\s]+\.[a-zA-Z]{3,4})',  # Filenames
        ]

        found_documents = {}  # Use dict to track unique docs with their details

        # Search through all messages
        for message in conversation_history:
            content = message.get('content', '')

            # Find document serve links
            for match in re.findall(patterns[0], content):
                # Clean up the path
                clean_path = match.strip()
                if clean_path not in found_documents:
                    found_documents[clean_path] = {
                        'type': 'link',
                        'path': clean_path,
                        'source': message.get('role', 'unknown')
                    }

            # Find UNC paths
            for match in re.findall(patterns[1], content):
                clean_path = match.strip()
                if clean_path not in found_documents:
                    # Convert to serve link format
                    formatted_path = clean_path.replace('\\', '/')
                    serve_link = f"/document/serve/{formatted_path}"
                    found_documents[serve_link] = {
                        'type': 'unc_path',
                        'path': serve_link,
                        'original': clean_path,
                        'source': message.get('role', 'unknown')
                    }

            # Find document IDs and filenames for context
            doc_ids = re.findall(patterns[2], content, re.IGNORECASE)
            filenames = re.findall(patterns[3], content, re.IGNORECASE)

            # Store these for reference (but don't create links without paths)
            for doc_id in doc_ids:
                key = f"doc_id:{doc_id}"
                if key not in found_documents:
                    found_documents[key] = {
                        'type': 'reference',
                        'doc_id': doc_id,
                        'source': message.get('role', 'unknown')
                    }

        if not found_documents:
            return "No document paths or links found in the conversation history."

        # Format output
        output_lines = []
        output_lines.append(f"Found {len(found_documents)} document reference(s) in conversation:\n")

        # Separate by type
        links = [d for d in found_documents.values() if d['type'] in ['link', 'unc_path']]
        references = [d for d in found_documents.values() if d['type'] == 'reference']

        if links:
            output_lines.append("📎 Document Links:")
            for idx, doc in enumerate(links, 1):
                output_lines.append(f"\n{idx}. {doc['path']}")
                if doc.get('original'):
                    output_lines.append(f"   Original path: {doc['original']}")
                output_lines.append(f"   Found in: {doc['source']} message")

        if references:
            output_lines.append("\n\n📋 Document IDs Referenced (without paths):")
            for ref in references:
                output_lines.append(f"   - Document ID: {ref['doc_id']}")

        output_lines.append(f"\n{'='*60}")
        output_lines.append("Use get_document_direct_links() with document IDs to generate links for referenced documents.")

        return '\n'.join(output_lines)

    except Exception as e:
        logger.error(f"Error extracting paths from conversation: {str(e)}")
        return f"Error extracting document paths: {str(e)}"

############################
##### END ENHANCED DOCS ####
############################



class GeneralAgent():
    def __init__(self, agent_id, user_id=None) -> None:
        logger.info(f"Initializing agent {agent_id}")
        self.agent_id = agent_id
        self.user_id = None
        if user_id:
            self.user_id = user_id
        # Load agent config
        self.agent_config = self._get_agent_config(agent_id)
        if self.agent_config is not None and len(self.agent_config) > 0:
            self.agent_config = self.agent_config[0]  # Take the first and only config
        else:
            raise ValueError(f'Invalid or missing config for agent {agent_id}')

        # Initialize agent knowledge
        self.agent_knowledge = []  # Store knowledge items as a list
        self.knowledge_enabled = cfg.ENABLE_AGENT_KNOWLEDGE_MANAGEMENT  # Flag to enable/disable knowledge management

        self.TEMPERATURE = 0.0

        self.AGENT_NAME = self.agent_config['agent_description']
        self.SYSTEM = self.agent_config['agent_objective']

        # 1. Load the language model (handles BYOK, direct OpenAI, and Azure)
        self.llm = self._create_llm(temperature=self.TEMPERATURE)

        # Store config for reference/logging
        self._llm_config = get_openai_config(use_alternate_api=True)
        logger.info(f"Agent {agent_id} using LLM source: {self._llm_config['source']}")

        # Initialize the smart content renderer
        self.smart_renderer = SmartContentRenderer()
        self.smart_renderer_df = None

        #########################
        # Set core tools
        #########################
        try:
            agent_core_tools = []
            agent_custom_tools = []
            knowledge_tool_selected = False
            for idx, tool in enumerate(self.agent_config['tool_names']):
                #print(tool)
                # Skip manage_knowledge tool (added separately later with dependency and updated objective)
                if tool == 'manage_knowledge':
                    knowledge_tool_selected = True
                    continue

                if self.agent_config['custom_tool'][idx]:
                    agent_custom_tools.append(tool)
                else:
                    agent_core_tools.append(tool)

            # Build agent configuration for conditional dependencies
            agent_config_for_deps = {
                'has_knowledge': False,  # Will be set later if knowledge docs exist
                'workflow_enabled': 'create_agent_workflow' in agent_core_tools,
            }

            # Add individual tool checks for conditional dependencies
            for tool in agent_core_tools:
                agent_config_for_deps[f'has_tool:{tool}'] = True

            # Check for tools that might trigger conditional dependencies
            if any(tool in agent_core_tools for tool in ['search_documents', 'document_super_search']):
                agent_config_for_deps['has_any_tool:search_documents,document_super_search'] = True

            if any(tool in agent_core_tools for tool in ['check_windows_service', 'sql_job_report']):
                agent_config_for_deps['has_any_tool:check_windows_service,sql_job_report'] = True

            # Resolve dependencies and get final tool list
            final_core_tools = get_tools_for_agent(
                agent_core_tools,
                include_optional_deps=False,  # Set to True if you want optional dependencies
                agent_config=agent_config_for_deps
            )

            # Log what was added
            added_dependencies = set(final_core_tools) - set(agent_core_tools)
            if added_dependencies:
                logger.debug(f"Added tool dependencies for agent {agent_id}: {added_dependencies}")
                #print(f"Added tool dependencies: {added_dependencies}")

            # Log mandatory tools
            manager = load_tool_dependencies()
            mandatory = manager.get_mandatory_tools()
            if mandatory:
                logger.debug(f"Including mandatory tools for agent {agent_id}: {mandatory}")
                #print(f"Including mandatory tools: {mandatory}")

            # Load the tools
            self.tools = []
            for core_tool in final_core_tools:
                tool_func = globals().get(core_tool)
                if tool_func:
                    self.tools.append(tool_func)
                else:
                    logger.warning(f"Tool '{core_tool}' not found in globals")
                    print(f"Warning: Tool '{core_tool}' not found")

            # Apply routing hints from core_tools.yaml to tool descriptions
            try:
                tool_configs = {t['name']: t for t in manager.config.get('tools', [])}
                for tool_obj in self.tools:
                    hint = tool_configs.get(tool_obj.name, {}).get('routing_hint')
                    if hint:
                        tool_obj.description = tool_obj.description.rstrip() + f"\n\n[ROUTING]: {hint}"
            except Exception as e:
                logger.debug(f"Could not apply routing hints: {e}")

        except Exception as e:
            print(str(e))
            raise(f'Failed to load core tools for agent {agent_id}')

        #########################
        # CUSTOM TOOLS
        #########################
        CUSTOM_TOOLS = load_custom_tools(agent_custom_tools)
        function_objects = [globals()[name] for name in CUSTOM_TOOLS if name in globals()]
        logger.debug('Function objects:')
        logger.debug(str(function_objects))
        if len(function_objects) > 0:
            self.tools.extend(function_objects)
            print('Tools added:', len(function_objects))
        else:
            print('No tools added - ', len(function_objects))

        #########################
        # AGENT KNOWLEDGE TOOLS
        #########################
        # Add agent knowledge management tools
        if self.knowledge_enabled:
            knowledge_tools = create_agent_knowledge_tools(self)
            self.tools.extend(knowledge_tools)
            print(f'Knowledge management tools added: {len(knowledge_tools)}')
            logger.info(f"Agent {self.agent_id} loaded {len(knowledge_tools)} knowledge management tools")

        #########################
        # KNOWLEDGE TOOLS
        #########################
        # Check if agent has knowledge documents
        agent_excel_docs = []  # Populated below; used after user-knowledge section
        knowledge_docs = get_agent_knowledge_documents(agent_id, user_id=user_id)
        if knowledge_docs or knowledge_tool_selected:
            # Add knowledge search tool
            if 'agent_config_for_deps' in locals():
                agent_config_for_deps['has_knowledge'] = True
            knowledge_tool = KnowledgeTool(agent_id, user_id=user_id)
            self.tools.append(knowledge_tool.get_knowledge_tool())
            print(f'Knowledge tool added (found {len(knowledge_docs)} documents)')

            # Enhance system prompt to mention knowledge
            doc_descriptions = []
            for doc in knowledge_docs:
                desc = doc.get('description') or doc.get('filename', 'Untitled')
                doc_descriptions.append(f"- {doc['filename']}: {desc}")
            if doc_descriptions:
                knowledge_prompt = "\n\n[Agent Knowledge Documents]\n"
                knowledge_prompt += "You have access to the following knowledge documents:\n"
                knowledge_prompt += "\n".join(doc_descriptions)
                doc_search_tools = {'search_documents', 'document_super_search', 'document_intelligent_search', 'search_documents_meaning'}
                has_doc_search = any(t.name in doc_search_tools for t in self.tools)
                if has_doc_search:
                    knowledge_prompt += "\n\nIMPORTANT: Always use the search_agent_knowledge tool FIRST for any questions that could be answered by the knowledge documents listed above. These documents contain specialized information provided to you. Only fall back to document repository search tools (search_documents, etc.) if search_agent_knowledge returns no relevant results."
                else:
                    knowledge_prompt += "\n\nIMPORTANT: Use the search_agent_knowledge tool to access text content from these documents when relevant to the user's query."
                knowledge_prompt += "\n\nGROUNDING RULES: When answering from knowledge documents, cite specific details exactly as they appear in the document. Never fabricate names, numbers, dates, or facts. If you cannot find specific information in the documents, say so rather than guessing. Always prefer quoting the document directly over paraphrasing or inferring."
                self.SYSTEM += knowledge_prompt

            # Add knowledge management tool (NOTE: Knowledge must be initialized for an agent manually via user adding a document to knowledge)
            self.tools.append(knowledge_tool.get_knowledge_management_tool())
            print('Knowledge management tool added.')

            #########################
            # EXCEL QUERY TOOLS (collect agent-level Excel docs; combined with
            # user-specific Excel docs in the ExcelTool creation block below)
            #########################
            if knowledge_docs:
                agent_excel_docs = [
                    doc for doc in knowledge_docs
                    if doc.get('filename', '').lower().endswith(('.xlsx', '.xls'))
                    and doc.get('original_path')
                ]

        #########################
        # EXCEL QUERY TOOLS (combined agent-level + user-specific)
        # NOTE: This MUST come BEFORE user-specific knowledge injection so that
        # _base_system_prompt (captured inside inject_user_knowledge) includes
        # Excel tool instructions but does NOT include user-specific content.
        #########################
        try:
            from agent_excel_tools import ExcelTool

            agent_excel_doc_ids = {doc['document_id'] for doc in agent_excel_docs}

            # Discover user-specific Excel docs (needs original_path from get_agent_knowledge_documents)
            user_excel_docs = []
            if user_id:
                all_user_docs = get_agent_knowledge_documents(agent_id, user_id=user_id)
                user_excel_docs = [
                    doc for doc in all_user_docs
                    if doc.get('filename', '').lower().endswith(('.xlsx', '.xls'))
                    and doc.get('original_path')
                    and doc['document_id'] not in agent_excel_doc_ids
                ]

            all_excel_docs = agent_excel_docs + user_excel_docs
            if all_excel_docs:
                excel_tool = ExcelTool(agent_id, all_excel_docs)
                excel_tools_list = excel_tool.get_tools()
                self.tools.extend(excel_tools_list)
                self.SYSTEM += excel_tool.get_system_prompt_addition()
                print(
                    f'Excel query tools added: {len(excel_tools_list)} tools '
                    f'for {len(all_excel_docs)} file(s) '
                    f'({len(agent_excel_docs)} agent-level, {len(user_excel_docs)} user-specific)'
                )
        except ImportError as e:
            logger.debug(f"Excel tools module not available: {e}")
        except Exception as e:
            logger.warning(f"Could not load Excel tools for agent {agent_id}: {e}")

        # Add user-specific knowledge
        # NOTE: inject_user_knowledge() captures _base_system_prompt on first call.
        # Since Excel tools are already in self.SYSTEM at this point, _base_system_prompt
        # will include Excel instructions but NOT user-specific content - exactly right.
        if user_id:
            print('Adding user-specific knowledge...')
            logger.info(f"Adding user-specific knowledge...")
            total_user_docs = self.inject_user_knowledge(user_id=user_id)
            if total_user_docs > 0:
                knowledge_tool = KnowledgeTool(agent_id, user_id=user_id)
                self.tools.append(knowledge_tool.get_user_knowledge_tool())
                print(f'User-specific knowledge tool added post knowledge injection.')
            else:
                print(f"No user-specific knowledge docs found.")

        #########################
        # EMAIL INBOX TOOLS
        #########################
        # Add email inbox tools if enabled for this agent
        try:
            email_tools = create_email_inbox_tools(agent_id)
            if email_tools:
                self.tools.extend(email_tools)
                print(f'Email inbox tools added: {len(email_tools)} tools')
                logger.info(f"Agent {self.agent_id} loaded {len(email_tools)} email inbox tools")

                # Add email capabilities to system prompt
                email_prompt_addition = get_email_tools_system_prompt_addition(agent_id)
                if email_prompt_addition:
                    self.SYSTEM += email_prompt_addition
        except ImportError as e:
            logger.debug(f"Email tools module not available: {e}")
        except Exception as e:
            logger.warning(f"Could not load email tools for agent {agent_id}: {e}")

        #########################
        # INTEGRATION TOOLS
        #########################
        # Add integration tools if the agent has integration tool names assigned
        try:
            integration_tool_names = {'list_integrations', 'get_integration_operations', 'execute_integration'}
            agent_tool_names = set(self.agent_config.get('tool_names', []))
            if agent_tool_names & integration_tool_names:
                from integration_agent_tools import (
                    create_langchain_integration_tools,
                    get_integration_tools_system_prompt_addition
                )
                integration_tools = create_langchain_integration_tools()
                if integration_tools:
                    self.tools.extend(integration_tools)
                    print(f'Integration tools added: {len(integration_tools)} tools')
                    logger.info(f"Agent {self.agent_id} loaded {len(integration_tools)} integration tools")

                    # Add integration context to system prompt
                    # Pass integration_tools_enabled=True since we've confirmed the agent has integration tools
                    integration_config = {**self.agent_config, 'integration_tools_enabled': True}
                    integration_prompt = get_integration_tools_system_prompt_addition(integration_config)
                    if integration_prompt:
                        self.SYSTEM += integration_prompt
        except ImportError as e:
            logger.debug(f"Integration tools module not available: {e}")
        except Exception as e:
            logger.warning(f"Could not load integration tools for agent {agent_id}: {e}")

        #########################
        # MCP TOOLS (experimental)
        #########################
        try:
            from builder_mcp.agent_integration.mcp_agent_tools import (
                get_mcp_tools_for_agent,
                get_mcp_system_prompt_addition
            )
            mcp_tools = get_mcp_tools_for_agent(agent_id)
            if mcp_tools:
                self.tools.extend(mcp_tools)
                print(f'MCP tools added: {len(mcp_tools)} tools')
                logger.info(f"Agent {self.agent_id} loaded {len(mcp_tools)} MCP tools")

                mcp_prompt_addition = get_mcp_system_prompt_addition(agent_id)
                if mcp_prompt_addition:
                    self.SYSTEM += mcp_prompt_addition
        except ImportError as e:
            logger.debug(f"MCP module not available: {e}")
        except Exception as e:
            logger.warning(f"Could not load MCP tools for agent {agent_id}: {e}")

        #########################
        # Bind the tools
        #########################
        self.llm_with_tools = self.llm.bind(tools=[convert_to_openai_tool(t) for t in self.tools])

        # Always register the agent (so others can call it)
        try:
            register_agent(agent_id, {
                'id': agent_id,
                'description': self.agent_config.get('agent_description', ''),
                'objective': self.agent_config.get('agent_objective', ''),
                'enabled': self.agent_config.get('agent_enabled', True),
                'tools': [t.name for t in self.tools],
                'executor': self
            })
        except ImportError:
            logger.debug("Agent communication module not available - agent won't be callable")

        MEMORY_KEY = "chat_history"
        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    self.SYSTEM,
                ),
                MessagesPlaceholder(variable_name=MEMORY_KEY),
                ("user", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        self.chat_history = []

        #########################
        # Create agent
        #########################
        self.agent = (
            {
                "input": lambda x: x["input"],
                "agent_scratchpad": lambda x: format_to_tool_messages(
                    x["intermediate_steps"]
                ),
                "chat_history": lambda x: x["chat_history"],
            }
            | self.prompt
            | self.llm_with_tools
            | ToolsAgentOutputParser()
        )

        #########################
        # Custom log
        #########################
        self.agent_logfile = cfg.LOG_DIR_AGENT
        self.handler = FileCallbackHandler(self.agent_logfile, mode='a')

        self.agent_executor = AgentExecutor(agent=self.agent, tools=self.tools, verbose=True, callbacks=[self.handler],
        max_iterations=int(cfg.MAX_GENERAL_AGENT_ITERATIONS),  # Add this - prevents runaway tool calls
        early_stopping_method="generate")

    def _create_llm(self, temperature=0.0):
        """
        Create the appropriate LLM based on BYOK/API configuration.

        Priority:
        1. BYOK enabled + user key → Direct OpenAI with user's key
        2. cfg.USE_OPENAI_API=True → Direct OpenAI with system key
        3. Default → Azure OpenAI
        """
        from api_keys_config import get_openai_config

        config = get_openai_config(use_alternate_api=True)

        # Reasoning models require temperature=1.0
        reasoning_effort = config.get('reasoning_effort')
        if reasoning_effort:
            temperature = 1.0

        if config['api_type'] == 'open_ai':
            return ChatOpenAI(
                model=config['model'],
                api_key=config['api_key'],
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )
        else:
            return AzureChatOpenAI(
                azure_deployment=config['deployment_id'],
                model=config['deployment_id'],
                api_version=config['api_version'],
                azure_endpoint=config['api_base'],
                api_key=config['api_key'],
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )

    def _set_user_request_id(self):
        try:
            # Generate or extract request ID
            request_id = str(uuid.uuid4())

            # Determine module from endpoint
            module_name = 'general_agent'

            user_id = None
            if self.user_id:
                user_id = self.user_id
            else:
                user_id = RequestTracking.get_user_id()

            # Set in Flask's g object - this is globally accessible for this request only
            if user_id:
                RequestTracking.set_tracking(request_id, module_name, user_id)
                logger.info(f"Starting request {request_id} for module {module_name} for user {user_id}")
            else:
                RequestTracking.set_tracking(request_id, module_name)
                logger.info(f"Starting request {request_id} for module {module_name}")
        except Exception as e:
            print(f"Error setting user request id: {str(e)}")
            logger.error(f"Error setting user request id: {str(e)}")


    def _get_agent_config(self, agent_id):
        try:
            # Establish the connection
            conn = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
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

            print(result)

            return result
        except Exception as e:
            print(f"Error: {e}")
            return None
        finally:
            # Close the connection
            cursor.close()
            conn.close()


    def _append_date_to_agent_log(self):
        try:
            try:
                formatted_datetime = 'Unknown'
                now = datetime.now()
                formatted_datetime = now.strftime("%Y-%m-%d_%H-%M-%S")
            except:
                now = datetime.datetime.now()
                formatted_datetime = now.strftime("%Y-%m-%d_%H-%M-%S")

            with open(self.agent_logfile, "a") as file:
                file.write("\n")
                file.write("==================================================================\n")
                file.write(f"{formatted_datetime}\n")
                print(f"Appended date/time to {self.agent_logfile}")
        except Exception as e:
            print(str(e))


    def get_tool_names(self):
        """Get list of all tool names loaded for this agent"""
        return [tool.name for tool in self.tools if hasattr(tool, 'name')]

    def has_tool(self, tool_name):
        """Check if agent has a specific tool"""
        return tool_name in self.get_tool_names()

    def get_tool_count(self):
        """Get total number of tools loaded"""
        return len(self.tools)

    def log_tool_summary(self):
        """Log a summary of all loaded tools"""
        tool_names = self.get_tool_names()
        logger.info(f"Agent {self.agent_id} has {len(tool_names)} tools: {tool_names}")

        # Group by type
        core_tools = []
        custom_tools = []
        knowledge_tools = []

        for tool in self.tools:
            if hasattr(tool, 'name'):
                if 'knowledge' in tool.name.lower():
                    knowledge_tools.append(tool.name)
                elif tool.name in self.agent_config.get('tool_names', []):
                    # Check if it's custom
                    idx = self.agent_config['tool_names'].index(tool.name)
                    if self.agent_config['custom_tool'][idx]:
                        custom_tools.append(tool.name)
                    else:
                        core_tools.append(tool.name)

        print(f"\nTool Summary for Agent {self.agent_id}:")
        print(f"  Core Tools ({len(core_tools)}): {core_tools}")
        print(f"  Custom Tools ({len(custom_tools)}): {custom_tools}")
        print(f"  Knowledge Tools ({len(knowledge_tools)}): {knowledge_tools}")
        print(f"  Total: {self.get_tool_count()} tools")


    def _convert_chat_history(self, input_chat_history):
        converted_chat_history = []
        new_entry = []
        entry_added = False
        for entry in input_chat_history:
            print(entry)
            if entry['role'] == 'user':
                human_message = HumanMessage(content=entry['content'])
                new_entry.append(human_message)
                entry_added = True
            else:
                ai_message = AIMessage(content=entry['content'])
                new_entry.append(ai_message)
                entry_added = True

        converted_chat_history.extend(new_entry)
        return converted_chat_history


    def get_chat_history(self):
        converted_chat_history = []
        new_message = None

        for message in self.chat_history:
            new_message = {"role": "user" if message.type == 'human' else 'assistant', "content": message.content}
            converted_chat_history.append(new_message)

        return converted_chat_history


    def initialize_chat_history(self, chat_hist):
        self.chat_history = self._convert_chat_history(chat_hist)


    def clear_chat_history(self):
        self.chat_history.clear()

    def _is_error_response(self, response_text):
        """Check if the response appears to be an error message"""
        if not isinstance(response_text, str):
            return False

        error_indicators = [
            "502 bad gateway", "502 server error", "502 error",
            "503 service unavailable", "500 internal server error",
            "connection error", "timeout error", "api error",
            "failed to", "error occurred", "code 429",
            "str' object has no attribute 'get'",
            "exception", "traceback", "keyerror", "attributeerror"
        ]

        response_lower = response_text.lower()
        return any(indicator in response_lower for indicator in error_indicators)

    def _generate_friendly_error_response_DEPRECATED(self, error_text, user_prompt):
        """Use azureMiniQuickPrompt to generate a user-friendly error response"""
        try:
            error_lower = str(error_text).lower()

            if 'rate limit' in error_lower or '429' in error_lower:
                return "I'm experiencing high demand right now. Please try again in a moment."
            elif 'timeout' in error_lower:
                return "That request took too long. Try asking a simpler question."
            elif 'connection' in error_lower or 'network' in error_lower:
                return "I'm having trouble connecting to the AI service. Please check your network and try again."

            system_prompt = system_prompts.FRIENDLY_ERROR_RESPONSE_SYSTEM

            user_query = system_prompts.FRIENDLY_ERROR_RESPONSE_PROMPT.format(user_prompt=user_prompt, error_text=error_text)

            friendly_response = azureMiniQuickPrompt(user_query, system_prompt)
            return friendly_response

        except Exception as e:
            # Fallback if azureMiniQuickPrompt fails
            return cfg.GENERAL_AGENT_FALLBACK_RESPONSE


    def _generate_friendly_error_response(self, error_text, user_prompt):
        """
        Generate a user-friendly error response based on error classification.

        For LLM-related errors: Returns static responses (avoids calling a failing service)
        For application errors: Uses AI to reword technical messages for users
        """
        error_lower = str(error_text).lower()

        # =========================================================================
        # LLM/API ERRORS - Handle with static messages (don't call failing service)
        # =========================================================================

        # Token/context length errors (often manifest as 502)
        if any(x in error_lower for x in [
            'context_length_exceeded',
            'maximum context length',
            'token limit',
            'too many tokens',
            'reduce the length',
            'context window'
        ]):
            return (
                "Your request was too long for me to process. This can happen when:\n\n"
                "• The conversation history has grown too large\n"
                "• You're asking me to analyze a very large document\n"
                "• The query itself is very lengthy\n\n"
                "**Try this:** Start a new conversation, or ask a more focused question with less context."
            )

        # 502 Bad Gateway (often token limits or upstream issues)
        if '502' in error_lower or 'bad gateway' in error_lower:
            return (
                "I encountered a processing error (502). This usually happens when:\n\n"
                "• The request was too complex or lengthy\n"
                "• The AI service is experiencing temporary issues\n\n"
                "**Try this:** Simplify your request or try again in a moment."
            )

        # Content filter / content policy errors
        if any(x in error_lower for x in [
            'content_filter',
            'content filter',
            'content management policy',
            'content policy',
            'responsible ai',
            'flagged',
            'blocked by',
            'harmful content',
            'safety system'
        ]):
            return (
                "Your request was flagged by the content safety filter. "
                "This can sometimes happen with legitimate requests that contain certain keywords or patterns.\n\n"
                "**Try this:** Rephrase your question using different wording."
            )

        # Rate limiting
        if any(x in error_lower for x in ['rate limit', 'ratelimit', '429', 'too many requests', 'throttl']):
            return (
                "I'm experiencing high demand right now and need a brief moment to catch up.\n\n"
                "**Try this:** Please wait 10-15 seconds and try again."
            )

        # Timeout errors
        if any(x in error_lower for x in ['timeout', 'timed out', 'deadline exceeded', 'took too long']):
            return (
                "That request took too long to process.\n\n"
                "**Try this:** Ask a simpler or more specific question, "
                "or break your request into smaller parts."
            )

        # Network/connection errors to LLM service
        if any(x in error_lower for x in [
            'openai', 'azure.com', 'api.anthropic'
        ]) and any(x in error_lower for x in [
            'connection', 'network', 'unreachable', 'dns',
            'failed to establish', 'socket', 'ssl'
        ]):
            return (
                "I'm having trouble connecting to the AI service.\n\n"
                "**Try this:** Check your network connection and try again. "
                "If the problem persists, the AI service may be temporarily unavailable."
            )

        # Server errors (500, 503, etc.) - only if clearly from LLM service
        if any(x in error_lower for x in ['openai', 'azure.com', 'anthropic']) and \
        any(x in error_lower for x in ['500', '503', '504', 'internal server error', 'service unavailable']):
            return (
                "The AI service is temporarily experiencing issues.\n\n"
                "**Try this:** Please wait a moment and try again. "
                "If this continues, the service may be undergoing maintenance."
            )

        # Authentication errors for LLM
        if any(x in error_lower for x in ['401', '403', 'unauthorized', 'forbidden', 'invalid api key']) and \
        any(x in error_lower for x in ['openai', 'azure', 'anthropic', 'api key', 'api_key']):
            return (
                "There's a configuration issue with the AI service.\n\n"
                "**Action required:** Please contact your administrator to resolve this issue."
            )

        # Quota/billing errors
        if any(x in error_lower for x in ['quota', 'billing', 'insufficient_quota', 'exceeded your current quota']):
            return (
                "The AI service usage limit has been reached.\n\n"
                "**Action required:** Please contact your administrator."
            )

        # Model overloaded
        if any(x in error_lower for x in ['overloaded', 'capacity', 'model is currently overloaded']):
            return (
                "The AI model is currently at capacity due to high demand.\n\n"
                "**Try this:** Please wait 30 seconds and try again."
            )

        # =========================================================================
        # APPLICATION/TECHNICAL ERRORS - Use AI to reword for user
        # =========================================================================

        # If we get here, it's likely an application error (database, tool failure, etc.)
        # Use AI to generate a friendly response
        try:
            system_prompt = system_prompts.FRIENDLY_ERROR_RESPONSE_SYSTEM
            user_query = system_prompts.FRIENDLY_ERROR_RESPONSE_PROMPT.format(
                user_prompt=user_prompt,
                error_text=error_text
            )

            friendly_response = azureMiniQuickPrompt(user_query, system_prompt)
            return friendly_response

        except Exception as e:
            # AI call failed - log and return static fallback
            logger.warning(f"AI error rewriting failed: {e}. Original error: {error_text[:200]}")
            return cfg.GENERAL_AGENT_FALLBACK_RESPONSE

    def handle_agent_request(self, message, context=None):
        """
        Handle a request from another agent with rich content support
        """
        try:
            # Add context to the message if provided
            full_message = message
            if context:
                full_message += f"\n\nContext: {json.dumps(context)}"

            # Process through the agent executor
            response = self.agent_executor.invoke({
                "input": full_message,
                "chat_history": []  # Fresh context for inter-agent communication
            })

            output = response.get('output', '')

            # Structure the output
            structured_output = self.smart_renderer.analyze_and_render(output, {
                "agent_id": self.agent_id,
                "inter_agent": True,
                "context": context
            })

            return {
                "status": "success",
                "output": structured_output,  # Now returns structured content
                "metadata": {
                    "agent_id": self.agent_id,
                    "execution_time": datetime.now().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Error handling agent request: {str(e)}")
            return {
                "status": "error",
                "output": {
                    "type": "rich_content",
                    "blocks": [{
                        "type": "error",
                        "content": str(e),
                        "metadata": {}
                    }]
                },
                "metadata": {
                    "agent_id": self.agent_id,
                    "execution_time": datetime.now().isoformat()
                }
            }

    def inject_user_knowledge(self, user_id=None):
        """
        Inject user-specific knowledge documents into the agent's context.

        Args:
            user_id: Optional user ID. If not provided, tries to get from RequestTracking.

        Returns:
            int: Number of user-specific documents injected
        """
        try:
            # Get user_id from RequestTracking if not provided
            if user_id is None:
                user_id = RequestTracking.get_user_id()

            if user_id is None:
                logger.info(f"Agent {self.agent_id}: No user context available for knowledge injection")
                return 0

            logger.info(f"Agent {self.agent_id}: Injecting knowledge for user {user_id}")

            # Get user-specific knowledge documents
            user_knowledge_docs = get_agent_knowledge_for_user(self.agent_id, user_id)

            if not user_knowledge_docs:
                logger.info(f"Agent {self.agent_id}: No user-specific knowledge documents found")
                return 0

            # Store original system prompt if not already stored
            if not hasattr(self, '_base_system_prompt'):
                self._base_system_prompt = self.SYSTEM

            # Group documents by batch_id first
            docs_by_batch = {}
            ungrouped_docs = []

            for doc in user_knowledge_docs:
                batch_id = doc.get('batch_id')
                if batch_id and batch_id.strip():  # Has a valid batch_id
                    if batch_id not in docs_by_batch:
                        docs_by_batch[batch_id] = []
                    docs_by_batch[batch_id].append(doc)
                else:  # No batch_id or empty
                    ungrouped_docs.append(doc)

            # Build knowledge prompt addition
            knowledge_prompt = "\n\n[User-Specific Knowledge Documents]\n"
            knowledge_prompt += "You have access to the following user-specific documents:\n"

            # First, display grouped documents (those with batch_ids)
            for batch_id in sorted(docs_by_batch.keys(), reverse=True):
                batch_docs = docs_by_batch[batch_id]

                if len(batch_docs) > 1:
                    # Multiple documents in the same batch - show as a group
                    first_doc = batch_docs[0]
                    knowledge_prompt += f"\nDocument Group ({len(batch_docs)} files uploaded together"
                    if first_doc.get('added_date'):
                        knowledge_prompt += f" on {first_doc['added_date']}"
                    knowledge_prompt += "):\n"

                    # Group by type within the batch for better organization
                    batch_by_type = {}
                    for doc in batch_docs:
                        doc_type = doc.get('document_type', 'Unknown')
                        if doc_type not in batch_by_type:
                            batch_by_type[doc_type] = []
                        batch_by_type[doc_type].append(doc)

                    for doc_type, type_docs in sorted(batch_by_type.items()):
                        if len(batch_by_type) > 1:  # Only show type if there are multiple types
                            knowledge_prompt += f"  {doc_type}:\n"
                            indent = "    • "
                        else:
                            indent = "  • "

                        for doc in type_docs:
                            desc = indent
                            if doc.get('description'):
                                desc += f"{doc['description']}"
                            else:
                                desc += f"{doc.get('filename', 'Untitled')}"
                            knowledge_prompt += desc + "\n"
                else:
                    # Single document with a batch_id (edge case, but handle it)
                    doc = batch_docs[0]
                    desc = f"\n• "
                    if doc.get('description'):
                        desc += f"{doc['description']}"
                    else:
                        desc += f"{doc.get('filename', 'Untitled')}"
                    if doc.get('document_type'):
                        desc += f" [{doc['document_type']}]"
                    if doc.get('added_date'):
                        desc += f" (Uploaded {doc['added_date']})"
                    knowledge_prompt += desc + "\n"

            # Then display ungrouped documents (those without batch_ids)
            if ungrouped_docs:
                # Group ungrouped docs by type for organization
                ungrouped_by_type = {}
                for doc in ungrouped_docs:
                    doc_type = doc.get('document_type', 'Unknown')
                    if doc_type not in ungrouped_by_type:
                        ungrouped_by_type[doc_type] = []
                    ungrouped_by_type[doc_type].append(doc)

                # Display ungrouped documents by type
                for doc_type, docs in sorted(ungrouped_by_type.items()):
                    knowledge_prompt += f"\n{doc_type} Documents ({len(docs)}):\n"
                    for doc in docs:
                        desc = f"  • "
                        if doc.get('description'):
                            desc += f"{doc['description']}"
                        else:
                            desc += f"{doc.get('filename', 'Untitled')}"
                        if doc.get('added_date'):
                            desc += f" (Uploaded {doc['added_date']})"
                        knowledge_prompt += desc + "\n"

            # Add notes about document grouping
            if docs_by_batch:
                knowledge_prompt += cfg.DOC_USER_SPECIFIC_PROMPT_NOTE

            knowledge_prompt += (
                "\nUse the get_user_specific_knowledge tool to access text content from "
                "these documents (PDFs, resumes, Word documents, text files, etc.) when "
                "relevant to the user's query. For Excel spreadsheet analysis, use the "
                "Excel-specific tools (analyze_excel_data, read_excel_data, etc.) instead."
            )

            # Update system prompt with user knowledge
            self.SYSTEM = self._base_system_prompt + knowledge_prompt

            # Store the injected user_id for reference
            self._injected_user_id = user_id

            logger.info(f"Agent {self.agent_id}: Successfully injected {len(user_knowledge_docs)} user-specific documents ({len(docs_by_batch)} batches, {len(ungrouped_docs)} ungrouped)")
            logger.info(f"NEW SYSTEM W/ USER KNOWLEDGE: {self.SYSTEM}")
            return len(user_knowledge_docs)

        except Exception as e:
            logger.error(f"Agent {self.agent_id}: Error injecting user knowledge: {str(e)}")
            return 0

    def clear_user_knowledge(self):
        """
        Clear any injected user-specific knowledge and restore base system prompt.
        """
        if hasattr(self, '_base_system_prompt'):
            self.SYSTEM = self._base_system_prompt
            logger.info(f"Agent {self.agent_id}: Cleared user-specific knowledge")

        if hasattr(self, '_injected_user_id'):
            delattr(self, '_injected_user_id')

    def run_with_user_context(self, input_prompt, user_id=None):
        """
        Run the agent with user-specific context.
        This method automatically injects and clears user knowledge.

        Args:
            input_prompt: The user's input message
            user_id: Optional user ID. If not provided, uses RequestTracking.

        Returns:
            str: The agent's response
        """
        try:
            # Inject user-specific knowledge
            self.inject_user_knowledge(user_id)

            # Run the agent
            result = self.run(input_prompt)

            return result
        finally:
            # Always clear user knowledge after execution
            self.clear_user_knowledge()

    # 5. Optional: Add a method to get plain text output if needed for backward compatibility:
    def run_text_only(self, input_prompt):
        """
        Run the agent and return plain text output (backward compatibility)
        """
        try:
            self._append_date_to_agent_log()
            self._set_user_request_id()

            print("Invoking agent...")
            result = self.agent_executor.invoke({"input": input_prompt, "chat_history": self.chat_history})

            # Extract the output from the result
            output = result.get("output", str(result))

            self.chat_history.extend([
                HumanMessage(content=input_prompt),
                AIMessage(content=output)
            ])

            return output  # Return plain text

        except Exception as e:
            print(str(e))
            logger.error(str(e))
            if self._is_error_response(str(e)):
                return self._generate_friendly_error_response(str(e), input_prompt)
            return str(e)

    def _should_process_individually(self, dfs):
        """
        Check if any dataframe exceeds the row limit.
        Returns True if any single dataframe is over the limit.
        """
        if not dfs:
            return False

        for df_name, df in dfs.items():
            if len(df) > cfg.GENERAL_CHAT_AI_PROCESSING_ROW_LIMIT:
                return True
        return False

    def _process_dataframes(self, dfs, output, context):
        """
        Helper function to process dataframes based on size limits.

        Args:
            dfs: Dictionary of dataframes
            output: The original output to process
            context: Context for the smart renderer

        Returns:
            structured_output from smart renderer
        """
        if not dfs:
            # No dataframes, process output normally
            return self.smart_renderer.analyze_and_render(output, context)

        print('DataFrame(s) found! Passing DF to smart render...')

        # Check if we need to process individually
        if self._should_process_individually(dfs):
            print(f'One or more dataframes exceed limit ({cfg.GENERAL_CHAT_AI_PROCESSING_ROW_LIMIT} rows), processing individually...')

            structured_output = None
            for df_index, (df_name, df) in enumerate(dfs.items(), 1):
                if df.empty:
                    print(f"DataFrame '{df_name}' is empty, skipping...")
                    continue

                print(f"Processing DataFrame '{df_name}': {len(df)} rows")

                if df_index == 1 or structured_output is None:
                    # First non-empty dataframe or initialize
                    structured_output = self.smart_renderer.analyze_and_render(df, context)
                else:
                    # Append subsequent dataframe blocks
                    temp_structured_output = self.smart_renderer.analyze_and_render(df, context)
                    if 'blocks' in temp_structured_output and temp_structured_output['blocks']:
                        structured_output['blocks'].append(temp_structured_output['blocks'][0])

            # Handle case where all dataframes were empty
            if structured_output is None:
                structured_output = self.smart_renderer.analyze_and_render(output, context)

        else:
            # All dataframes are within limit, process entire output with AI
            total_rows = sum(len(df) for df in dfs.values())
            print(f'All dataframes within limit (total {total_rows} rows), processing entire output with AI...')
            structured_output = self.smart_renderer.analyze_and_render(output, context)

        return structured_output

    def run(self, input_prompt, use_smart_render=False, user_id=None):
        #########################
        # Run the agent
        #########################
        try:
            if use_smart_render:
                self._append_date_to_agent_log()

                self._set_user_request_id()  # Set global request id

                if user_id:
                    self.user_id = user_id
                    RequestTracking.set_user_id(user_id)
                    # IMPORTANT: Set in thread-local storage for tools to access
                    _current_agent_context.user_id = user_id
                    _current_agent_context.request_id = RequestTracking.get_user_request_id()
                elif self.user_id:
                    user_id = self.user_id
                    RequestTracking.set_user_id(self.user_id)
                    # IMPORTANT: Set in thread-local storage for tools to access
                    _current_agent_context.user_id = self.user_id
                    _current_agent_context.request_id = RequestTracking.get_user_request_id()

                # Set email tool context if tools are loaded
                try:
                    from agent_email_tools import set_email_tool_context, _get_agent_email_config
                    config = _get_agent_email_config(self.agent_id)
                    if config and config.get('email_address'):
                        set_email_tool_context(
                            agent_id=self.agent_id,
                            email_address=config['email_address'],
                            from_name=config.get('from_name')
                        )
                except:
                    pass  # Email tools not available

                # Always cleanup any existing files before running
                df_manager.cleanup(user_id)
                rich_content_manager.cleanup(str(user_id) if user_id else "0")

                print("Invoking agent...")
                result = self.agent_executor.invoke({"input": input_prompt, "chat_history": self.chat_history})

                # Extract the output from the result
                output = result.get("output", str(result))

                print('RAW OUTPUT TYPE:', type(output))
                print('RAW OUTPUT:', output)
                print('USER ID:', user_id)
                print('REQUEST ID:', RequestTracking.get_user_request_id())

                # Check if a DataFrame was saved by a tool
                dfs = {}
                if user_id:
                    #dfs = df_manager.load_all_and_delete(user_id, request_id=RequestTracking.get_user_request_id())
                    dfs = df_manager.load_all_and_delete(user_id)

                # Check if rich content blocks (charts, etc.) were saved by tools
                rich_content_blocks = []
                if user_id:
                    rich_content_blocks = rich_content_manager.load_all_and_delete(str(user_id))

                # Analyze and structure the response using SmartContentRenderer
                context = {
                    "agent_name": self.AGENT_NAME,
                    "agent_type": "general",
                    "agent_objective": self.SYSTEM,
                    "query": input_prompt,
                    "has_tools": bool(self.tools)
                }

                # If dataframe is available and large, pass entire dataframe to be rendered directly and bypass AI
                if dfs:
                    # Dataframe processing
                    structured_output = self._process_dataframes(dfs, output, context)
                else:
                    # Standard processing of output
                    structured_output = self.smart_renderer.analyze_and_render(output, context)

                # Inject any rich content blocks from tools (charts, diagrams, etc.)
                if rich_content_blocks:
                    if 'blocks' not in structured_output:
                        structured_output['blocks'] = []
                    # Prepend rich content blocks so charts appear before text
                    structured_output['blocks'] = rich_content_blocks + structured_output['blocks']
                    structured_output['type'] = 'rich_content'

                print(86 * '#')
                print(86 * '#')
                print(86 * '#')
                print('=============== SMART CONTENT AI structured_output FROM GENERAL AGENT ===============')
                print(structured_output)
                print(86 * '#')
                print(86 * '#')
                print(86 * '#')

                # Store in chat history (keep original text for history)
                self.chat_history.extend([
                    HumanMessage(content=input_prompt),
                    AIMessage(content=output)  # Store original text in history
                ])

                # Return the structured response for rich display
                return structured_output
            else:
                return self.run_text_only(input_prompt=input_prompt)
        except Exception as e:
            print(str(e))
            logger.error(str(e))
            print(86 * '!')
            # Generate friendly response for exceptions
            friendly_response = self._generate_friendly_error_response(str(e), input_prompt)
            return friendly_response
        finally:
            # Clean up thread-local context
            if hasattr(_current_agent_context, 'user_id'):
                delattr(_current_agent_context, 'user_id')
            if hasattr(_current_agent_context, 'request_id'):
                delattr(_current_agent_context, 'request_id')

            # Clean up any leftover rich content files on error
            if user_id:
                try:
                    rich_content_manager.cleanup(str(user_id))
                except:
                    pass

            # Clean up email tool context
            try:
                from agent_email_tools import clear_email_tool_context
                clear_email_tool_context()
            except:
                pass


    # Add a new method to handle specific content types:
    def format_tool_response(self, tool_name: str, tool_output: Any) -> Dict:
        """
        Format tool outputs for rich display
        """
        if tool_name == "process_csv":
            # If the tool returns a DataFrame
            if isinstance(tool_output, pd.DataFrame):
                return self.smart_renderer.analyze_and_render(tool_output)

        elif tool_name == "call_external_api":
            # Try to parse JSON responses
            try:
                import json
                data = json.loads(tool_output)
                return self.smart_renderer.analyze_and_render(data)
            except:
                pass

        # Default handling
        return self.smart_renderer.analyze_and_render(tool_output)

    # Method to handle incoming requests from other agents
    def handle_agent_request_legacy(self, message, context=None):
        """
        Handle a request from another agent

        Parameters:
        -----------
        message : str
            The message/task from the requesting agent
        context : dict
            Additional context provided by the requesting agent

        Returns:
        --------
        dict
            Response to send back to the requesting agent
        """
        try:
            # Add context to the message if provided
            full_message = message
            if context:
                full_message += f"\n\nContext: {json.dumps(context)}"

            # Process through the agent executor
            response = self.agent_executor.invoke({
                "input": full_message,
                "chat_history": []  # Fresh context for inter-agent communication
            })

            return {
                "status": "success",
                "output": response.get('output', ''),
                "metadata": {
                    "agent_id": self.agent_id,
                    "execution_time": datetime.now().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Error handling agent request: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "agent_id": self.agent_id
            }

    # Cleanup method
    def add_knowledge(self, knowledge_item: str) -> str:
        """
        Add new knowledge to the agent's internal knowledge base

        Args:
            knowledge_item: A concise piece of information to add

        Returns:
            Confirmation message
        """
        if not self.knowledge_enabled:
            return "Knowledge management is disabled for this agent"

        # Add the knowledge item with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_knowledge = f"[{timestamp}] {knowledge_item}"

        self.agent_knowledge.append(formatted_knowledge)

        # Update the system prompt with new knowledge
        self._update_system_prompt_with_knowledge()

        # Recreate the agent with updated system prompt
        self._recreate_agent_with_updated_prompt()

        logger.info(f"Agent {self.agent_id} added knowledge: {knowledge_item}")
        return f"Successfully added knowledge: {knowledge_item}"

    def get_knowledge(self) -> str:
        """
        Get all current knowledge items

        Returns:
            Formatted string of all knowledge items
        """
        if not self.agent_knowledge:
            return "No knowledge items stored yet"

        knowledge_str = "\n".join([f"- {item}" for item in self.agent_knowledge])
        return f"Current knowledge items:\n{knowledge_str}"

    def clear_knowledge(self) -> str:
        """
        Clear all knowledge items

        Returns:
            Confirmation message
        """
        self.agent_knowledge.clear()
        self._update_system_prompt_with_knowledge()
        self._recreate_agent_with_updated_prompt()

        logger.info(f"Agent {self.agent_id} cleared all knowledge")
        return "All knowledge items have been cleared"

    def _update_system_prompt_with_knowledge(self):
        """Update the system prompt to include current knowledge using the template"""
        # Get the base objective from config
        base_objective = self.agent_config['agent_objective']

        if self.agent_knowledge and self.knowledge_enabled:
            # Format knowledge items
            knowledge_text = "\n".join([f"- {item}" for item in self.agent_knowledge])

            # Use the knowledge system prompt template
            knowledge_section = system_prompts.AGENT_KNOWLEDGE_SYSTEM_PROMPT.format(
                knowledge=knowledge_text
            )

            # Combine base objective with knowledge section
            self.SYSTEM = base_objective + "\n\n" + knowledge_section
        else:
            # No knowledge, use base objective only
            self.SYSTEM = base_objective

    def _recreate_agent_with_updated_prompt(self):
        """Recreate the agent with updated system prompt"""
        try:
            # Update the prompt template
            MEMORY_KEY = "chat_history"
            self.prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        self.SYSTEM,
                    ),
                    MessagesPlaceholder(variable_name=MEMORY_KEY),
                    ("user", "{input}"),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ]
            )

            # Recreate the agent
            self.agent = (
                {
                    "input": lambda x: x["input"],
                    "agent_scratchpad": lambda x: format_to_tool_messages(
                        x["intermediate_steps"]
                    ),
                    "chat_history": lambda x: x["chat_history"],
                }
                | self.prompt
                | self.llm_with_tools
                | ToolsAgentOutputParser()
            )

            # Recreate the agent executor
            self.agent_executor = AgentExecutor(agent=self.agent, tools=self.tools, verbose=True, callbacks=[self.handler])

        except Exception as e:
            logger.error(f"Error recreating agent with updated prompt: {str(e)}")
            print(f"Error recreating agent: {str(e)}")

    def cleanup(self):
        """Clean up agent resources and unregister from registry"""
        unregister_agent(self.agent_id)

