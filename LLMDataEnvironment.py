import logging
from datetime import datetime


class Environment:
    def __init__(self):
        self.last_query_executed = None
        self.last_input_question = None
        self.chat_history = []
        self.current_query = None
        self.current_input_question = None
        self.previous_agent_id = None
        self.agent_id = None
        self.df = None
        self.dfs = []
        self.dfs_desc = []
        self.is_first_question = True
        self.is_new_query = True
        self.total_time_of_last_request = 0
        self.question_count = 0
        self.previous_input_question = None
        self.previous_query = None
        self.schema = None
        self.schemas = []
        self.query_hist = []
        self.question_history = []
        self.was_last_query_successful = False
        self.was_last_query_emtpy = False
        self.last_query_fail_message = None
        self.current_full_schema = None
        self.current_connection_id = None
        self.last_answer_requested_more_info = False   
        self.last_answer_requested_more_info_message = None
        self.last_query_row_count = None
        self.is_response = False
        self.has_time_reference = False
        self.is_time_ambiguous = False
        self.time_default_resolution = None
        self.current_formatting_requirements = None
        # Event-related properties
        self.has_event_reference = False
        self.needs_external_info = False
        self.event_type = None
        self.event_description = None
        self.event_info = None
        self.event_is_recurring = False

    def get_current_date(self):
        # Get current date information for context
        current_date = datetime.now().strftime("%Y-%m-%d")
        return current_date

    def log_environment(self):
        logging.debug(f"last_query_executed: {self.last_query_executed}")
        logging.debug(f"last_input_question: {self.last_input_question}")
        logging.debug(f"chat_history: {self.chat_history}")
        logging.debug(f"current_query: {self.current_query}")
        logging.debug(f"current_input_question: {self.current_input_question}")
        logging.debug(f"previous_agent_id: {self.previous_agent_id}")
        logging.debug(f"agent_id: {self.agent_id}")
        logging.debug(f"df: {self.df}")
        logging.debug(f"dfs: {self.dfs}")
        logging.debug(f"dfs_desc: {self.dfs_desc}")
        logging.debug(f"is_first_question: {self.is_first_question}")
        logging.debug(f"is_new_query: {self.is_new_query}")
        logging.debug(f"total_time_of_last_request: {self.total_time_of_last_request}")
        logging.debug(f"question_count: {self.question_count}")
        logging.debug(f"previous_input_question: {self.previous_input_question}")
        logging.debug(f"previous_query: {self.previous_query}")
        logging.debug(f"schema: {self.schema}")
        logging.debug(f"schemas: {self.schemas}")
        logging.debug(f"query_hist: {self.query_hist}")
        logging.debug(f"question_history: {self.question_history}")


    def get_question_hist_for_prompt(self):
        # Construct question input
        question_prompt = ''
        for qidx, question in enumerate(self.question_history):
            question_prompt += f'Question {qidx+1}:\n' + question + '\n'
        question_prompt += '\n'
        return question_prompt
        

    def get_query_hist_for_prompt(self):
        # Construct query input
        query_prompt = ''
        for idx, query in enumerate(self.query_hist):
            query_prompt += f'Query {idx+1}:\n' + query + '\n'
        query_prompt += '\n'
        return query_prompt
    

    def get_recent_query_hist_for_prompt(self, num_entries):
        # Get the last num_entries from the query history
        recent_query_hist = self.query_hist[-num_entries:]

        # Construct query input
        query_prompt = ''
        for idx, query in enumerate(recent_query_hist):
            query_prompt += f'Query {idx+1}:\n' + query + '\n'
        query_prompt += '\n'
        return query_prompt
    

    def get_data_preview_hist_for_prompt(self, n_rows=5):
        # Construct data input 
        df_prompt = ''
        displayIndex = 0
        dfIndex = 0
        for _, question_query in enumerate(self.query_hist):
            if 'No Query Generated' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - More information was requested from user.)' + '\n\n'
                displayIndex += 1
            elif 'Failed to generate query' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - Failed to generate query.)' + '\n\n'
                displayIndex += 1
            else:
                try:
                    df = self.dfs[dfIndex]
                    df_prompt += f'Dataset {displayIndex+1}:\n' + df.head(n_rows).to_string(index=False) + '\n\n'
                    displayIndex += 1
                    dfIndex += 1
                except Exception as e:
                    print(f'Missing dataset query hist in get_data_preview_hist_for_prompt (index={dfIndex}):', str(e))
                    logging.error(f'Missing dataset query in get_data_preview_hist_for_prompt hist (index={dfIndex}): ' + str(e))

        df_prompt += '\n'
        return df_prompt
    
    
    def get_recent_data_preview_hist_for_prompt(self, num_entries, n_rows=5):
        # Get the last num_entries from the chat history
        recent_query_hist = self.query_hist[-num_entries:]

        # Construct data input 
        df_prompt = ''
        displayIndex = 0
        dfIndex = 0
        for _, question_query in enumerate(recent_query_hist):
            if 'No Query Generated' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - More information was requested from user.)' + '\n\n'
                displayIndex += 1
            elif 'Failed to generate query' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - Failed to generate query.)' + '\n\n'
                displayIndex += 1
            else:
                try:
                    df = self.dfs[dfIndex]
                    df_prompt += f'Dataset {displayIndex+1}:\n' + df.head(n_rows).to_string(index=False) + '\n\n'
                    displayIndex += 1
                    dfIndex += 1
                except Exception as e:
                    print(f'Missing dataset query hist in get_data_preview_hist_for_prompt (index={dfIndex}):', str(e))
                    logging.error(f'Missing dataset query in get_data_preview_hist_for_prompt hist (index={dfIndex}): ' + str(e))

        df_prompt += '\n'
        return df_prompt
    

    def get_recent_chat_history_for_prompt_legacy(self, num_entries):
        try:
            # Get the last num_entries from the chat history
            recent_history = self.chat_history[-num_entries:]
            
            # Format each message with a role prefix
            formatted_history = []
            for idx, entry in enumerate(recent_history):
                role = "User" if entry["role"] == "user" else "Assistant"
                formatted_message = f"{idx+1}. {role}: {entry['content']}"
                formatted_history.append(formatted_message)
        except Exception as e:
            formatted_history = ''
            print('get_recent_chat_history_for_prompt error - ', str(e))
            logging.error('get_recent_chat_history_for_prompt error - ' + str(e))
        
        # Join the formatted messages into a single string with line breaks
        return "\n".join(formatted_history)
    
    
    def get_recent_chat_history_for_prompt(self, num_entries, include_current_question=True, number_assistant_messages=True):
        """
        Returns the recent conversation history as a formatted string.

        Args:
        - num_entries (int): The number of recent chat entries to include.
        - include_current_question (bool): Whether to include the current question in the history.

        Returns:
        - str: A formatted string of the recent conversation history.
        """
        try:
            # Get the appropriate number of entries from the chat history
            if include_current_question:
                # Include all recent entries
                recent_history = self.chat_history[-num_entries:]
            else:
                # Exclude the current question (which would be the last entry if the user just asked it)
                if len(self.chat_history) > 0 and self.chat_history[-1]["role"] == "user":
                    # If the last entry is from the user, exclude it
                    recent_history = self.chat_history[-(num_entries+1):-1] if len(self.chat_history) > 1 else []
                else:
                    # Otherwise just take the last num_entries excluding the current question
                    recent_history = self.chat_history[-num_entries:]

            # Format each message with a role prefix
            formatted_history = []
            if number_assistant_messages:
                for idx, entry in enumerate(recent_history, start=1):
                    role = "User" if entry["role"] == "user" else "Assistant"
                    formatted_message = f"{idx}. {role}: {entry['content']}"
                    formatted_history.append(formatted_message)
            else:
                idx = 1
                for entry in recent_history:
                    role = "User" if entry["role"] == "user" else "Assistant"
                    if role == "User":
                        formatted_message = f"{idx}. {role}: {entry['content']}"
                        idx += 1
                    else:
                        formatted_message = f"{role}: {entry['content']}"
                    formatted_history.append(formatted_message)
        except Exception as e:
            formatted_history = []
            print('get_recent_chat_history_for_prompt error - ', str(e))
            logging.error('get_recent_chat_history_for_prompt error - ' + str(e))
        
        # Join the formatted messages into a single string with line breaks
        return "\n".join(formatted_history)
    

    def get_chat_history_for_prompt(self):
        try:
            # Format each message with a role prefix
            formatted_history = []
            displayIndex = 0
            for idx, entry in enumerate(self.chat_history):
                role = "User" if entry["role"] == "user" else "Assistant"

                if role == "User":
                    displayIndex += 1
                    formatted_message = f"{displayIndex}. {role}: {entry['content']}"
                else:
                    formatted_message = f"{role}: {entry['content']}"

                formatted_history.append(formatted_message)
        except Exception as e:
            formatted_history = ''
            print('get_recent_chat_history_for_prompt error - ', str(e))
            logging.error('get_recent_chat_history_for_prompt error - ' + str(e))
        
        # Join the formatted messages into a single string with line breaks
        return "\n".join(formatted_history)
    

    def get_chat_history_for_prompt_no_data(self):
        try:
            # DF indicator text
            df_indicator = 'class="dataframe"'

            # Format each message with a role prefix
            formatted_history = []
            displayIndex = 0
            for idx, entry in enumerate(self.chat_history):
                role = "User" if entry["role"] == "user" else "Assistant"

                if role == "User":
                    displayIndex += 1

                    if df_indicator in entry['content']:
                        formatted_message = f"{displayIndex}. {role}: <See query result {displayIndex} below>"
                    else:
                        formatted_message = f"{displayIndex}. {role}: {entry['content']}"
                else:
                    if df_indicator in entry['content']:
                        formatted_message = f"{role}: <See query result {displayIndex} below>"
                    else:
                        formatted_message = f"{role}: {entry['content']}"
                formatted_history.append(formatted_message)
        except Exception as e:
            formatted_history = ''
            print('get_recent_chat_history_for_prompt error - ', str(e))
            logging.error('get_recent_chat_history_for_prompt error - ' + str(e))
        
        # Join the formatted messages into a single string with line breaks
        return "\n".join(formatted_history)
    
    def store_combined_analysis_results(self, analysis_results):
        """Store the results from combined analysis for use across different components"""
        self.combined_analysis_results = analysis_results
        
        # Store individual components for easy access
        self.meta_question_result = analysis_results.get("meta_question", {})
        self.input_classification_result = analysis_results.get("input_classification", {})
        self.data_query_required_result = analysis_results.get("data_query_required", {})
        self.more_info_required_result = analysis_results.get("more_info_required", {})
        self.analytical_required_result = analysis_results.get("analytical_required", {})
    
    def get_combined_analysis_results(self):
        """Retrieve stored combined analysis results"""
        return getattr(self, 'combined_analysis_results', None)