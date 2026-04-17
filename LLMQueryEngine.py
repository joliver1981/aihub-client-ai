import config as cfg
import system_prompts as sysprompts
from AppUtils import azureQuickPrompt, execute_sql_query, execute_sql_query_v2
import logging
#from DataUtils import get_table_descriptions_as_yaml, get_all_column_descriptions_as_yaml, get_column_descriptions_as_yaml, get_table_descriptions, script_create_table_statements, get_column_descriptions, get_connection_string, get_column_descriptions_with_table_descriptions_as_yaml
from DataUtils import (
    get_table_descriptions_as_yaml, 
    get_all_column_descriptions_as_yaml, 
    get_column_descriptions_as_yaml, 
    get_table_descriptions, 
    script_create_table_statements, 
    get_column_descriptions, 
    get_connection_string, 
    get_column_descriptions_with_table_descriptions_as_yaml,
    get_enhanced_table_metadata_as_yaml,
    get_enhanced_column_metadata_as_yaml,
    get_enhanced_full_schema_with_column_details_as_yaml,
    get_enhanced_full_schema_as_yaml,
    query_app_database
)
import json
from WebSearch import WebSearch


# logging.basicConfig(filename=cfg.LLM_ENGINE_LOG, level=logging.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

class LLMQueryEngine:
    def __init__(self, environment, provider="openai"):
        self.environment = environment
        self.provider = provider
        self.agent_connection_string = None
        self.agent_connection_id = None
        self.agent_database_type = None
        self.full_schema = None

        self.event_cache = {}  # Simple cache for event information
        # Only initialize web search if allowed in config
        if cfg.ALLOW_INTERNET_SEARCH_FOR_NLQ:
            self.web_search = WebSearch()
            logging.info("Internet search for NLQ is ENABLED")
            print("Internet search for NLQ is ENABLED")
        else:
            self.web_search = None
            logging.info("Internet search for NLQ is DISABLED")
            print("Internet search for NLQ is DISABLED")

    def _set_target_database(self, agent_id):
        conn_str, connection_id, database_type = get_connection_string(agent_id=agent_id)
        self.agent_connection_string = conn_str
        self.agent_connection_id = connection_id
        self.agent_database_type = database_type
        
        # Try to get ALL tables for this connection to build full schema
        try:
            # Get list of all tables
            from DataUtils import query_app_database
            query = "SELECT table_name FROM llm_Tables WHERE connection_id = ?"
            tables_result = query_app_database(query, (self.agent_connection_id,))
            all_table_names = [t['table_name'] for t in tables_result] if tables_result else []
            
            if all_table_names:
                # Try enhanced schema with ALL tables
                self.full_schema = get_enhanced_full_schema_with_column_details_as_yaml(
                    all_table_names, 
                    self.agent_connection_id
                )
                print(f"Loaded enhanced schema for {len(all_table_names)} tables")
            else:
                # No tables found, use basic schema
                self.full_schema = get_all_column_descriptions_as_yaml(self.agent_connection_id)
                print("No tables found in llm_Tables, using basic schema")

            # TEMPORARY TEST - Remove after verifying
            print("=" * 60)
            print("SCHEMA LOADED:")
            print(self.full_schema[:500])  # First 500 chars
            print("=" * 60)
        except Exception as e:
            # If enhanced schema fails, fall back to basic
            print(f"Enhanced schema not available ({str(e)}), using basic schema")
            self.full_schema = get_all_column_descriptions_as_yaml(self.agent_connection_id)

    def _is_internet_search_allowed(self):
        """Helper function to check if internet search is allowed and available"""
        return cfg.ALLOW_INTERNET_SEARCH_FOR_NLQ and self.web_search is not None
    
    def _choose_tables(self, input_question):
        """Enhanced with additional context"""
        try:
            conversation_history = self._get_question_hist_for_prompt()
            system = sysprompts.SYS_PROMPT_TABLE_CHOOSING_SYSTEM_YAML_V2
            
            # Get enhanced table metadata (includes business rules, filters, relationships)
            enhanced_table_info = get_enhanced_table_metadata_as_yaml(self.agent_connection_id)

            prompt = sysprompts.SYS_PROMPT_TABLE_CHOOSING_PROMPT_V2.replace(
                '{conversation_history}', conversation_history
            ).replace(
                '{schema}', self.environment.current_full_schema
            ).replace(
                '{question}', input_question
            ).replace(
                '{table_descriptions}', get_table_descriptions_as_yaml(self.agent_connection_id)
            ).replace(
                '{enhanced_table_info}', enhanced_table_info
            )
            
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
            response = self._clean_llm_result(response)
            return response
        except Exception as e:
            logging.error('ERROR (_choose_tables):' + str(e))
            return '[]'
    
    def _refine_tables(self, input_question, table_list):
        try:
            print(86 * 'R')
            logging.info('Refining tables...')
            print('Refining tables...')
            table_info = self._get_column_descriptions_with_table_descriptions(table_list)
            prompt = sysprompts.SYS_PROMPT_TABLE_CHOOSING_PROMPT.replace('{question}', input_question)
            system = sysprompts.SYS_PROMPT_TABLE_REFINING_SYSTEM_YAML.replace('{table_descriptions}', table_info)
            print('SYSTEM:', system)
            print('PROMPT:', prompt)
            logging.info('SYSTEM:' + system)
            logging.info('PROMPT:' + prompt)
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
            response = self._clean_llm_result(response)
            print('RESPONSE:', response)
            logging.info('RESPONSE:' + response)
            print(86 * 'R')
        except Exception as e:
            logging.error('ERROR (_refine_tables):' + str(e))
            response = '[]'
        return response
    
    def _get_column_descriptions(self, table_list):
        column_report = get_column_descriptions_as_yaml(table_list, self.agent_connection_id)
        return column_report
    
    def _get_column_descriptions_with_table_descriptions(self, table_list):
        column_report = get_column_descriptions_with_table_descriptions_as_yaml(table_list, self.agent_connection_id)
        return column_report
    
    def _get_all_column_descriptions(self):
        column_report = get_all_column_descriptions_as_yaml(self.agent_connection_id)
        return column_report

    def _get_df_description(self, input_question, query):
        try:
            system = sysprompts.SYS_PROMPT_DESCRIPTION_FROM_SQL_SYSTEM
            prompt = sysprompts.SYS_PROMPT_DESCRIPTION_FROM_SQL_PROMPT.replace('{input_question}', input_question).replace('{query}', query)
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
        except Exception as e:
            logging.error(str(e))
            response = None
        return response
    

    def _get_question_hist_for_prompt(self):
        # Construct question input
        question_prompt = ''
        for qidx, question in enumerate(self.environment.question_history):
            question_prompt += f'Question {qidx+1}:\n' + question + '\n'
        question_prompt += '\n'
        return question_prompt
        

    def _get_query_hist_for_prompt(self):
        # Construct query input
        query_prompt = ''
        for idx, query in enumerate(self.environment.query_hist):
            query_prompt += f'Query {idx+1}:\n' + query + '\n'
        query_prompt += '\n'
        return query_prompt


    def _get_data_hist_for_prompt(self, n_rows=5):
        # Construct data input 
        df_prompt = ''
        displayIndex = 0
        dfIndex = 0
        for _, question_query in enumerate(self.environment.query_hist):
            if 'No Query Generated' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - More information was requested from user.)' + '\n\n'
                displayIndex += 1
            elif 'Failed to generate query' in question_query:
                df_prompt += f'Dataset {displayIndex+1}:\n' + '(No Dataset Generated - Failed to generate query.)' + '\n\n'
                displayIndex += 1
            else:
                try:
                    df = self.environment.dfs[dfIndex]
                    df_prompt += f'Dataset {displayIndex+1}:\n' + df.head(n_rows).to_string(index=False) + '\n\n'
                    displayIndex += 1
                    dfIndex += 1
                except Exception as e:
                    print(f'Error setting dataset query hist in _get_data_hist_for_prompt (index={dfIndex}):', str(e))
                    logging.error(f'Error setting dataset query in _get_data_hist_for_prompt hist (index={dfIndex}): ' + str(e))

        df_prompt += '\n'
        return df_prompt
    
    def _detect_meta_question(self, input_question):
        """
        Use AI reasoning to determine if this is a meta-question about previous processing
        and generate an appropriate response if it is.
        """
        try:
            # Check if combined analysis is enabled and results are available
            if cfg.USE_COMBINED_ANALYSIS:
                combined_results = self.environment.get_combined_analysis_results()
                if combined_results and "meta_question" in combined_results:
                    return combined_results["meta_question"]
        
            # First validate this is a user question, not system content
            if not input_question or not isinstance(input_question, str):
                return {
                    "is_meta_question": False,
                    "requested_info_type": None,
                    "related_entity": None,
                    "response": None,
                    "confidence": 0
                }
                
            # Check for JSON-like structure or system message indicators
            is_likely_system_content = (
                input_question.strip().startswith('{') or 
                '"response":' in input_question or
                '"response_classification":' in input_question or
                input_question.strip().startswith('SELECT ') or
                len(input_question.split()) > 150  # Very long inputs are likely not questions
            )
            
            if is_likely_system_content:
                return {
                    "is_meta_question": False,
                    "requested_info_type": None,
                    "related_entity": None,
                    "response": None,
                    "confidence": 0
                }
        
            # Create a system prompt for meta-question detection
            meta_system = sysprompts.SYS_PROMPT_DETECT_META_QUESTION_SYSTEM
            
            # Build a meta-question prompt with context from environment
            meta_prompt = f"""
            Recent conversation history:
            {self.environment.get_recent_chat_history_for_prompt(num_entries=int(cfg.DEFAULT_RECENT_CONVERSATION_WINDOW))}
            
            Current question: {input_question}
            
            Event information available in context:
            """
            
            # Add any event information we've stored
            if hasattr(self.environment, 'event_info') and self.environment.event_info:
                event_info = self.environment.event_info
                meta_prompt += f"""
                Event: {self.environment.event_description}
                Time period: {event_info.get('start_date', 'unknown')} to {event_info.get('end_date', 'unknown')}
                Description: {event_info.get('time_period_description', 'unknown')}
                SQL Condition used: {event_info.get('sql_date_condition', 'unknown')}
                Is recurring: {event_info.get('is_recurring', False)}
                Most recent year: {event_info.get('most_recent_year', 'unknown')}
                """
            else:
                meta_prompt += "No specific event information is currently stored."
                
            # Add query information
            if self.environment.current_query:
                meta_prompt += f"\n\nLast executed query: {self.environment.current_query}"
                
            print(f"Meta-question detection - system: {meta_system}")
            print(f"Meta-question detection - prompt: {meta_prompt}")
            
            # Use azureQuickPrompt to analyze the question
            results = azureQuickPrompt(prompt=meta_prompt, system=meta_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            print(f"Meta-question detection results: {results}")
            result = json.loads(results)
            
            return result
            
        except Exception as e:
            print(f"Error in meta-question detection: {e}")
            logging.error(f"Error in meta-question detection: {e}")
            return {
                "is_meta_question": False,
                "requested_info_type": None,
                "related_entity": None,
                "response": None,
                "confidence": 0
            }
        
    def _answer_meta_question(self, requested_info_type, input_question, related_entity=None):
        """
        Generate contextually relevant responses to meta-questions about data sources,
        capabilities, and confidence using available environment data.
        
        Args:
            requested_info_type: The type of meta-question (data_sources, capabilities, confidence)
            input_question: The original user question
            related_entity: Optional specific entity the question is referring to
            
        Returns:
            str: A natural language response addressing the meta-question
        """
        try:
            # Build context information based on requested info type
            context_info = ""
            system_prompt = ""
            
            if requested_info_type == "data_sources" or requested_info_type == "data_source" or str(requested_info_type).lower().__contains__("data"):
                # Get schema information without using technical terms
                data_info = ""
                
                # Get table information in a user-friendly format
                try:
                    data_info = get_all_column_descriptions_as_yaml(self.agent_connection_id)
                except Exception as e:
                    logging.error(f"Error getting data source information: {e}")
                    data_info = "No information was found regarding data sources."
                
                # Create context information about data sources
                context_info = (
                    "I have access to the following data sources:\n"
                    f"{data_info}\n"
                )
                
                system_prompt = sysprompts.SYS_PROMPT_META_QUESTION_ANSWER_DATA_SOURCE_SYSTEM
                
            elif requested_info_type == "capabilities" or str(requested_info_type).lower().__contains__("capab"):
                # Get data info from tables and columns as the primary source
                data_info = ""
                
                try:
                    data_info = get_all_column_descriptions_as_yaml(self.agent_connection_id)
                except Exception as e:
                    logging.error(f"Error getting table information: {e}")
                    data_info = "No information was found."
                
                # Look for a custom capabilities configuration
                custom_capabilities = sysprompts.SYS_PROMPT_META_QUESTION_CAPABILITIES_LIST
                
                # Add custom capabilities if configured
                capability_points = []
                for item in custom_capabilities:
                    capability_points.append(f"- {item}")
                
                # Build the context information
                context_parts = []
                
                # Always include data content
                context_parts.append(
                    "I have access to the following:\n"
                    f"{data_info}"
                )
                
                # Include custom capabilities if available
                if capability_points:
                    context_parts.append(
                        "My capabilities include:\n"
                        f"{chr(10).join(capability_points)}"
                    )
                
                # Final common part
                context_parts.append(
                    "Based on this information, I can help answer questions "
                    "and provide insights relevant to your data."
                )
                
                # Join all parts with double newlines
                context_info = "\n\n".join(context_parts)
                
                system_prompt = sysprompts.SYS_PROMPT_META_QUESTION_ANSWER_CAPABILITIES_SYSTEM
                
            elif requested_info_type == "confidence" or str(requested_info_type).lower().__contains__("conf"):
                # Information about confidence and how it's determined
                confidence_factors = [
                    "The completeness of the data available",
                    "The clarity of the question asked",
                    "How well the available data matches the question",
                    "Whether I needed to make assumptions to answer"
                ]
                
                # Get confidence level from environment if available
                confidence_level = "moderate"
                if hasattr(self.environment, 'last_answer_confidence'):
                    if self.environment.last_answer_confidence > 0.85:
                        confidence_level = "high"
                    elif self.environment.last_answer_confidence < 0.5:
                        confidence_level = "low"
                
                # Get caution level if available
                caution_setting = "standard"
                if hasattr(self.environment, 'caution_level'):
                    caution_setting = self.environment.caution_level
                
                # Create context with explicit line breaks
                factor_points = []
                for item in confidence_factors:
                    factor_points.append(f"- {item}")
                
                context_info = (
                    "When determining my confidence in a data analysis answer, I consider:\n"
                    f"{chr(10).join(factor_points)}\n\n"
                    f"For my most recent answer, my confidence was {confidence_level}.\n\n"
                    f"Current caution setting: {caution_setting}\n\n"
                    "When I'm uncertain about an answer, I'll typically let you know or ask for clarification."
                )
                
                system_prompt = sysprompts.SYS_PROMPT_META_QUESTION_ANSWER_CONFIDENCE_SYSTEM
            
            else:
                # Fallback for unrecognized meta-question types
                context_info = """
                I'm an AI assistant that helps you analyze data and answer questions.
                
                I can help you find information, identify patterns and trends, 
                and provide insights based on the available data.
                """
                
                system_prompt = """
                You are a helpful AI assistant responding to a question about your data capabilities.
                Provide a friendly, general response about how you can help the user with data analysis.
                """
            
            # Create user prompt with the question and context
            user_prompt = f"""
            The user has asked: "{input_question}"
            
            This appears to be a question about my {"available information" if requested_info_type == "data_sources" else "data analysis capabilities" if requested_info_type == "capabilities" else "confidence in my data analysis"}.
            
            The following context information is available to help answer this question:
            
            {context_info}
            
            Generate a helpful, conversational response that addresses their question using this context.
            """
            print(45 * '#', ' META QUESTION ANSWER ', 45 * '#')
            logging.debug(f'Meta-answer system: {system_prompt}')
            logging.debug(f'Meta-answer prompt: {user_prompt}')
            print(f'Meta-answer system: {system_prompt}')
            print(f'Meta-answer prompt: {user_prompt}')
            
            # Generate the response with azureQuickPrompt
            response = azureQuickPrompt(prompt=user_prompt, system=system_prompt, use_alternate_api=True, provider=self.provider)
            
            return response
            
        except Exception as e:
            logging.error(f"Error in _answer_meta_question: {e}")
            # Provide a fallback response
            if requested_info_type == "data_sources":
                return "I have access to various business data that I can use to answer your questions. What would you like to know?"
            elif requested_info_type == "capabilities":
                return "I can help you analyze data, find information, identify trends, and answer questions based on the available information. What can I help you with today?"
            elif requested_info_type == "confidence":
                return "I try to be transparent about my confidence in my data analysis. If I'm uncertain, I'll let you know or ask for clarification."
            else:
                return "I'm an AI data assistant that can help answer your questions and provide insights based on the available information."

    def _get_query_from_question(self, input_question, is_first_question=False):
        try:
            # Add time and event reference information
            context_hint = ""
            
            # Add time reference resolution hints
            if hasattr(self.environment, 'has_time_reference') and self.environment.has_time_reference:
                if hasattr(self.environment, 'is_time_ambiguous') and self.environment.is_time_ambiguous:
                    if hasattr(self.environment, 'time_default_resolution') and self.environment.time_default_resolution:
                        context_hint += f"IMPORTANT: The user's question contains an ambiguous time reference. For ambiguous time periods like holidays without a specified year, use the most recent occurrence in the dataset. Specifically, interpret '{self.environment.time_default_resolution}' as the most recent occurrence.\n\n"
            
            # Add event reference information only if internet search is allowed
            if self._is_internet_search_allowed() and hasattr(self.environment, 'has_event_reference') and self.environment.has_event_reference:
                if hasattr(self.environment, 'event_description') and self.environment.event_description:
                    # Determine if this is a recurring event
                    is_recurring = False
                    if hasattr(self.environment, 'event_info') and self.environment.event_info:
                        event_info = self.environment.event_info
                        if 'is_recurring' in event_info:
                            is_recurring = event_info.get('is_recurring', False)
                    
                    context_hint += f"IMPORTANT: The user's question references the event: {self.environment.event_description}.\n"
                    
                    if is_recurring:
                        context_hint += f"This is a RECURRING SEASONAL event. You should use data from the MOST RECENT occurrence "
                        context_hint += f"(year {event_info.get('most_recent_year', 'unknown')}).\n"
                    
                    if hasattr(self.environment, 'event_info') and self.environment.event_info:
                        event_info = self.environment.event_info
                        context_hint += f"This event occurred from {event_info.get('start_date', 'unknown')} to {event_info.get('end_date', 'unknown')}.\n"
                        context_hint += f"When filtering data for this event, use this SQL condition: {event_info.get('sql_date_condition', '')}.\n\n"
            
            table_list = self._choose_tables(input_question)

            if cfg.REFINE_TABLE_SELECTION and table_list != 'None' and table_list != '[]' and table_list is not None:
                new_table_list = self._refine_tables(input_question, table_list)
            elif table_list == 'None' or table_list == '[]' or table_list is None:
                # Try again (a small % of the time the model gets confused)
                table_list = self._choose_tables(input_question)
                if table_list != 'None' and table_list != '[]' and table_list is not None:
                    new_table_list = table_list
                    logging.info('NOTICE: Second attempt at choosing tables was successful...')
                    print('NOTICE: Second attempt at choosing tables was successful...')
                else:
                    new_table_list = None
            else:
                new_table_list = table_list

            if new_table_list is not None and new_table_list != 'None':
                table_list = new_table_list
            else:
                logging.info('FAILURE: All attempts at choosing tables were unsuccessful...')
                print('FAILURE: All attempts at choosing tables were unsuccessful...')
                raise ValueError('Table selection failed.')

            #table_info = self._get_column_descriptions(table_list)  # Prior version - does not include table descriptions

            # Use enhanced schema for selected tables
            try:
                table_info = get_enhanced_full_schema_with_column_details_as_yaml(
                    table_list, 
                    self.agent_connection_id
                )
                if not table_info:
                    # Enhanced schema returned empty, use basic
                    print("⚠ Enhanced schema empty for selected tables, using basic")
                    table_info = self._get_column_descriptions_with_table_descriptions(table_list)
            except Exception as e:
                # If enhanced schema fails, fall back to basic
                print(f"⚠ Enhanced schema failed ({str(e)}), using basic")
                table_info = self._get_column_descriptions_with_table_descriptions(table_list)

            self.environment.schema = table_info

            # Add time hint to the prompt
            prompt = sysprompts.SYS_PROMPT_SQL_FROM_QUESTION_AND_SCHEMA_PROMPT.replace(
            '{question}', input_question
            ).replace(
                '{schema}', table_info
            ).replace(
                '{context_hint}', context_hint  # This was previously '{time_hint}', which isn't defined
            )

            # Get current date information for context
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")
            
            if is_first_question:
                system = sysprompts.SYS_PROMPT_SQL_FROM_QUESTION_AND_SCHEMA_SYSTEM_YAML.replace('{database_type}', self.agent_database_type).replace('{current_date}', current_date)
            else:
                previous_questions = self._get_question_hist_for_prompt()
                previous_queries = self._get_query_hist_for_prompt()
                n_rows = 5
                previous_results = self._get_data_hist_for_prompt(n_rows=n_rows)
                
                system = sysprompts.SYS_PROMPT_SQL_FROM_QUESTION_AND_SCHEMA_SYSTEM_NOT_FIRST_YAML_V2.replace('{database_type}', self.agent_database_type).replace('{previous_question}', self.environment.get_chat_history_for_prompt_no_data()).replace('{previous_query}', previous_queries).replace('{previous_results}', previous_results).replace('{n_rows}', str(n_rows)).replace('{current_date}', current_date)
            
            print(22 * 'QUERY')
            print(86 * '#')
            print('SYSTEM:', system)
            print('PROMPT:', prompt)
            logging.info('SYSTEM:' + system)
            logging.info('PROMPT:' + prompt)
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
            response = self._clean_llm_result(response)
            print('RESPONSE:', response)
            logging.info('RESPONSE:' + response)
            print(86 * '#')
            print(86 * 'Q')
        except Exception as e:
            logging.error('ERROR (_get_query_from_question):' + str(e))
            print('ERROR (_get_query_from_question):' + str(e))
            response = None
            return response
        # Save schema to hist for successful responses
        if response is not None:
            self.environment.schemas.append(self.environment.schema)
        return response


    def _extract_sql_only(self, result, original_query):
        """Extract only the SQL query from a response that might contain explanations using Azure AI."""
        try:
            # Define a system and prompt specifically for extracting just the SQL
            extract_system = """You are a specialized SQL extractor. Given a response that contains SQL code mixed with explanations,
            your task is to extract ONLY the valid SQL query, nothing else. Do not include any explanations, markdown code blocks,
            or commentary in your response. Return ONLY the SQL query itself."""
            
            extract_prompt = f"""
            The following text contains a SQL query with explanations. Extract ONLY the SQL query part:
            
            {result}
            
            Return ONLY the SQL code with no additional text or explanations. If no valid SQL is found, return the original query:
            {original_query}
            """
            
            # Use Azure AI to extract just the SQL
            if cfg.USE_MINI_MODELS_WHEN_POSSIBLE:
                print('Extracting SQL with mini model...')
                extracted_sql = azureQuickPrompt(extract_prompt, system=extract_system, use_alternate_api=True, provider=self.provider)
            else:
                print('Extracting SQL with standard model...')
                extracted_sql = azureQuickPrompt(extract_prompt, system=extract_system, use_alternate_api=True, provider=self.provider)
            extracted_sql = self._clean_llm_result(extracted_sql)
            
            # Basic validation - if extraction failed, return original query
            if not extracted_sql or len(extracted_sql.strip()) < 5:  # Basic check if we got something substantive
                logging.warning('SQL extraction failed, returning original query')
                return original_query
                
            return extracted_sql
        except Exception as e:
            logging.error(f'ERROR (_extract_sql_only): {e}')
            # If extraction fails, return the cleaned original result as a fallback
            return self._clean_llm_result(result)
    
    def _check_query(self, input_question, schema, original_query):
        try:
            print('!!!!!!!!!! RUNNING _check_query WITH SCHEMA... !!!!!!!!!!')
            prompt = sysprompts.SYS_PROMPT_SQL_CORRECTION_PROMPT.replace('{database_type}', self.agent_database_type).replace('{question}', input_question).replace('{schema}', schema).replace('{query}', original_query)
            system = sysprompts.SYS_PROMPT_SQL_CORRECTION_SYSTEM.replace('{database_type}', self.agent_database_type)
            print(43 * 'CK')
            print(system)
            print(86 * '-')
            print(prompt)
            print(43 * 'CK')
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
            print('RAW RESPONSE:', response)
            # Clean the response to get only the SQL
            print('FULL RESPONSE:', response)
            response = self._extract_sql_only(response, original_query)
            print('EXTRACTED SQL ONLY:', response)
        except Exception as e:
            logging.error('ERROR (_check_query):' + str(e))
            response = None
        return response
    
    def _auto_correct_query_from_error(self, original_query, error_message, input_question, schema):
        """
        Attempt to automatically correct a SQL query based on the error message received.
        
        Args:
            original_query (str): The original SQL query that failed
            error_message (str): The error message received from the database
            input_question (str): The original user question
            schema (str): The database schema
            
        Returns:
            str: The corrected SQL query or None if correction failed
        """
        try:
            print(43 * 'AUTO-CORRECT')
            logging.info('Attempting to auto-correct SQL query using error message...')
            print('Original query:', original_query)
            print('Error message:', error_message)
            
            # System prompt specifically for error-based correction
            correction_system = """
            You are an expert SQL debugger. Your task is to fix SQL queries that have failed execution.
            Focus ONLY on correcting the SQL syntax and structure to make the query valid. 
            Return ONLY the corrected SQL query - no explanations, markdown, or additional text.
            """
            
            # Detailed prompt with all context needed to fix the query
            correction_prompt = f"""
            A SQL query for {self.agent_database_type} has failed with the following error:
            
            ERROR MESSAGE:
            {error_message}
            
            ORIGINAL QUERY:
            {original_query}
            
            USER'S QUESTION:
            {input_question}
            
            DATABASE SCHEMA:
            {schema}
            
            Analyze the error message and fix the SQL query. Common issues include:
            1. Syntax errors (missing parentheses, commas, or keywords)
            2. Invalid column or table references
            3. Invalid date formats or conversions
            4. Improper join conditions
            5. SQL dialect-specific issues
            
            Return ONLY the corrected SQL query without any explanations or markdown.
            """
            
            # Get the corrected query
            corrected_query = azureQuickPrompt(correction_prompt, system=correction_system, use_alternate_api=True, provider=self.provider)
            
            # Clean the result to ensure we get only SQL
            corrected_query = self._clean_llm_result(corrected_query)
            
            print('Corrected query:', corrected_query)
            logging.info('Auto-corrected SQL query generated')
            print(43 * 'AUTO-CORRECT')
            
            # Basic validation to ensure we got something reasonable
            if corrected_query and len(corrected_query.strip()) > 10:
                return corrected_query
            else:
                logging.warning('Auto-correction failed to produce a valid query')
                return None
                
        except Exception as e:
            error_msg = str(e)
            logging.error(f'ERROR in _auto_correct_query_from_error: {error_msg}')
            print(f'ERROR in _auto_correct_query_from_error: {error_msg}')
            return None
    
    def _check_query_alias(self, input_question, schema, original_query):
        try:
            prompt = sysprompts.SYS_PROMPT_SQL_CORRECTION_PROMPT.replace('{question}', input_question).replace('{schema}', schema).replace('{query}', original_query)
            system = sysprompts.SYS_PROMPT_SQL_COLUMN_ALIAS_SYSTEM
            response = azureQuickPrompt(prompt, system=system, use_alternate_api=True, provider=self.provider)
        except Exception as e:
            logging.error('ERROR (_check_query_alias):' + str(e))
            response = None
        return response

    def _load_query(self, query):
        try:
            print(86 * 'Q')
            print('Executing query...')
            # print('Executing query via execute_sql_query_v2...')
            # print('Connection String: ' + self.agent_connection_string)
            # print('Query: ' + query)
            print(86 * 'Q')
            logging.debug('Executing query via execute_sql_query_v2...')
            # logging.debug('Connection String: ' + self.agent_connection_string)
            # logging.debug('Query: ' + query)
            # TODO Gracefully return and handle database connection errors, etc. - consider evaluating error w/ mini model to determine if hard stop and default messaging is necessary
            df, error = execute_sql_query_v2(query, self.agent_connection_string)
            
            # If the query succeeded, process the results
            if df is not None:
                print('QUERY SUCCESS!')
                print(df.head())
                self.environment.df = df
                self.environment.last_query_row_count = len(df)
                self.environment.last_query_error = None
                self.environment.was_last_query_successful = True
                
                if len(df) == 0:
                    self.environment.was_last_query_emtpy = True
                    print('WARNING: Query was successful but did not return any results')
                    logging.warning('WARNING: Query was successful but did not return any results')
            else:
                # Query failed, attempt auto-correction
                print('QUERY ERROR:', error)
                logging.error('QUERY ERROR: ' + error)
                self.environment.last_query_error = error
                self.environment.was_last_query_successful = False
                
                # Attempt to auto-correct the query
                corrected_query = self._auto_correct_query_from_error(
                    query, 
                    error, 
                    self.environment.current_input_question, 
                    self.environment.current_full_schema
                )
                
                # If auto-correction succeeded, try to execute the corrected query
                if corrected_query is not None:
                    print('Attempting to execute auto-corrected query...')
                    df, second_error = execute_sql_query_v2(corrected_query, self.agent_connection_string)
                    
                    if df is not None:
                        print('AUTO-CORRECTED QUERY SUCCESS!')
                        print(df.head())
                        self.environment.df = df
                        self.environment.last_query_row_count = len(df)
                        self.environment.last_query_error = None
                        self.environment.was_last_query_successful = True
                        self.environment.current_query = corrected_query  # Update to the corrected query
                        
                        if len(df) == 0:
                            self.environment.was_last_query_emtpy = True
                            print('WARNING: Auto-corrected query was successful but did not return any results')
                            logging.warning('WARNING: Auto-corrected query was successful but did not return any results')
                    else:
                        # Auto-correction also failed
                        print('AUTO-CORRECTED QUERY ERROR:', second_error)
                        logging.error('AUTO-CORRECTED QUERY ERROR: ' + second_error)
                        self.environment.df = None
                        self.environment.last_query_error = second_error
                else:
                    # Auto-correction failed to produce a valid query
                    self.environment.df = None
            
            return self.environment.df
            
        except Exception as e:
            error_msg = str(e)
            logging.error('ERROR LOADING QUERY: ' + error_msg)
            print('ERROR LOADING QUERY: ' + error_msg)
            self.environment.df = None
            self.environment.last_query_error = error_msg
            self.environment.was_last_query_successful = False
            return self.environment.df
        
    def _apply_known_query_mods(self, query):
        query = str(query).replace('DATEADD(day, -1, getutcdate())', 'CONVERT(DATE, DATEADD(day, -1, getutcdate()))')
        query = str(query).replace('```sql', '').replace('```', '')
        return query

    def _has_missing_headers(self, df):
        for column in df.columns:
            if column == '' or 'Unnamed' in column:
                return True
        return False
    
    def _clean_llm_result(self, result):
        return str(result).replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')
    
    def _is_data_query_required(self):
        try:
            IS_REQUIRED = True
            explanation = ''
            confidence = '0'
            query_system = sysprompts.SYS_PROMPT_QUERY_CHECK_SYSTEM

            # Check if combined analysis is enabled and results are available
            if cfg.USE_COMBINED_ANALYSIS:
                combined_results = self.environment.get_combined_analysis_results()
                if combined_results and "data_query_required" in combined_results:
                    result = combined_results["data_query_required"]
                    is_required = result.get("is_required", True)
                    explanation = result.get("explanation", "")
                    confidence = result.get("confidence", 0)
                    return is_required, explanation, confidence

            # Construct question input
            question_prompt = self._get_question_hist_for_prompt() or ''
            
            # Construct query input
            query_prompt = self._get_query_hist_for_prompt() or ''

            # Construct data input 
            df_prompt = self._get_data_hist_for_prompt() or ''

            # Ensure all values are strings before replacing
            current_question = '' if self.environment.current_input_question is None else str(self.environment.current_input_question)
            ai_request = '' if self.environment.last_answer_requested_more_info_message is None else str(self.environment.last_answer_requested_more_info_message)

            # First ensure the template itself isn't None
            template = sysprompts.SYS_PROMPT_QUERY_CHECK_PROMPT
            if template is None:
                logging.error("Template SYS_PROMPT_QUERY_CHECK_PROMPT is None")
                template = "ERROR: Template is missing"

            # Use a step-by-step replacement approach to identify where the issue might be
            try:
                temp1 = template.replace('{question}', question_prompt)
                temp2 = temp1.replace('{query}', query_prompt)
                temp3 = temp2.replace('{dataset}', df_prompt)
                temp4 = temp3.replace('{current_question}', current_question)
                query_check_prompt = temp4.replace('{ai_request}', ai_request)
            except Exception as e:
                logging.error(f"Error during string replacement: {e}")
                # Fallback to a simplified prompt if replacement fails
                query_check_prompt = f"Analyze if a new query is needed. Previous questions: {question_prompt}. Current question: {current_question}"

            #query_check_prompt = sysprompts.SYS_PROMPT_QUERY_CHECK_PROMPT.replace('{question}', question_prompt).replace('{query}', query_prompt).replace('{dataset}', df_prompt).replace('{current_question}', self.environment.current_input_question).replace('{ai_request}', self.environment.last_answer_requested_more_info_message)
            
            print(86 * '&')
            logging.debug(f'Query check system (v2): {query_system}')
            logging.debug(f'Query check prompt (v2): {query_check_prompt}')
            print(f'Query check system (v2): {query_system}')
            print(f'Query check prompt (v2): {query_check_prompt}')
            results = azureQuickPrompt(prompt=query_check_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            logging.debug(f'Query check results (v2): {results}')
            print(f'Query check results (v2): {results}')


            # Clean results
            results = self._clean_llm_result(results)
            print(f'Query check results after clean (v2): {results}')

            # Process JSON
            try:
                logging.debug(f'Query check results (v2): {results}')
                print(f'Query check results (v2): {results}')

                # Default return values
                dataset_is_sufficient = 'yes'
                explanation = ''
                confidence = '0'

                result = json.loads(results)
                # Extract individual values
                dataset_is_sufficient = result["dataset_is_sufficient"]
                explanation = result.get("explanation", "")
                confidence = result["confidence"]
                
                # Print the extracted values
                print(43 * 'REQ?')
                print("Dataset is Sufficient:", dataset_is_sufficient)
                print("Explanation:", explanation)
                print("Confidence:", str(confidence))
                logging.debug("Dataset is Sufficient:" + dataset_is_sufficient)
                logging.debug("Explanation:" + explanation)
                logging.debug("Confidence:" + str(confidence))
                print(43 * 'REQ?')

                if str(dataset_is_sufficient).lower() == 'yes':
                    IS_REQUIRED = False   # If dataset is sufficient==yes, analytical query is not required
                else:
                    IS_REQUIRED = True
            except Exception as e:
                print('_is_data_query_required - error processing response:', str(e))
                logging.error('_is_data_query_required - error processing response: ' + str(e))
        except Exception as e:
            IS_REQUIRED = True
            logging.error(f'Error checking if data query is required (v2): {e}')
            print(f'Error checking if data query is required (v2): {e}')
        return IS_REQUIRED, explanation, confidence
    
    def _detect_time_references(self, input_question):
        """
        Detects temporal references in user input and determines if they are ambiguous.
        Returns a tuple of (has_time_reference, is_ambiguous, default_resolution)
        """
        try:
            # Default return values
            has_time_reference = False
            is_ambiguous = False
            default_resolution = None

            query_system = sysprompts.SYS_PROMPT_TIME_REFERENCE_DETECTION_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_TIME_REFERENCE_DETECTION_PROMPT.replace('{user_question}', input_question)
            
            logging.debug(f'_detect_time_references - system: {query_system}')
            logging.debug(f'_detect_time_references - prompt: {query_prompt}')
            print(f'_detect_time_references - system: {query_system}')
            print(f'_detect_time_references - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logging.debug('Results:' + results)
            print('Results:', results)
            result = json.loads(results)

            # Extract individual values
            has_time_reference = result["has_time_reference"]
            is_ambiguous = result["is_ambiguous"]
            default_resolution = result["default_resolution"]
            
            # Print the extracted values
            print("has_time_reference:", has_time_reference)
            print("is_ambiguous:", is_ambiguous)
            print("default_resolution:", default_resolution)

        except Exception as e:
            print(str(e))
            logging.error(f'ERROR (_detect_time_references): {e}')
            has_time_reference = False
            is_ambiguous = False
            default_resolution = None

        return has_time_reference, is_ambiguous, default_resolution
    
    def _detect_event_references(self, input_question):
        """
        Detects event references in user input and determines if external information is needed.
        Returns a tuple of (has_event_reference, needs_external_info, event_type, event_description)
        """
        try:
            # Check if internet search is allowed
            if not self._is_internet_search_allowed():
                # If not allowed, just return default values without making LLM call
                return False, False, None, None, ""
            
            # Get current date information
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")
    
            # Default return values
            has_event_reference = False
            needs_external_info = False
            event_type = None
            event_description = None

            query_system = sysprompts.SYS_PROMPT_EVENT_REFERENCE_DETECTION_SYSTEM.replace(
            '{current_date}', current_date
            )
            query_prompt = sysprompts.SYS_PROMPT_EVENT_REFERENCE_DETECTION_PROMPT.replace('{user_question}', input_question)
            
            logging.debug(f'_detect_event_references - system: {query_system}')
            logging.debug(f'_detect_event_references - prompt: {query_prompt}')
            print(f'_detect_event_references - system: {query_system}')
            print(f'_detect_event_references - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logging.debug('Results:' + results)
            print('Results:', results)
            result = json.loads(results)

            # Extract individual values
            has_event_reference = result["has_event_reference"]
            needs_external_info = result["needs_external_info"]
            event_type = result["event_type"]
            event_description = result["event_description"]
            search_query = result.get("search_query", "")

            # Validate and correct the search query if needed
            if search_query:
                search_query = self._validate_search_query(search_query, event_type, event_description)
        
            # Print the extracted values
            print("has_event_reference:", has_event_reference)
            print("needs_external_info:", needs_external_info)
            print("event_type:", event_type)
            print("event_description:", event_description)
            print("original search_query:", result.get("search_query", ""))
            print("validated search_query:", search_query)

        except Exception as e:
            print(str(e))
            logging.error(f'ERROR (_detect_event_references): {e}')
            has_event_reference = False
            needs_external_info = False
            event_type = None
            event_description = None
            search_query = ""

        return has_event_reference, needs_external_info, event_type, event_description, search_query
    
    def _classify_event_type(self, event_type, event_description):
        """
        Classifies an event to determine if it's recurring (seasonal) or one-time.
        Returns (is_recurring, standardized_event_name, standard_dates)
        """
        # Common recurring events with standard date patterns
        recurring_events = {
            "hurricane_season": {
                "patterns": ["hurricane season", "tropical storm season", "cyclone season"],
                "standard_dates": {"start_month": 6, "start_day": 1, "end_month": 11, "end_day": 30}
            },
            "black_friday": {
                "patterns": ["black friday", "thanksgiving shopping", "thanksgiving sales"],
                "standard_dates": {"relative": "fourth thursday of november plus 1 day"}
            },
            "holiday_season": {
                "patterns": ["holiday season", "christmas shopping", "holiday shopping"],
                "standard_dates": {"start_month": 11, "start_day": 1, "end_month": 12, "end_day": 31}
            },
            "tax_season": {
                "patterns": ["tax season", "tax filing"],
                "standard_dates": {"start_month": 1, "start_day": 1, "end_month": 4, "end_day": 15}
            }
        }
        
        # Normalize the event description for matching
        normalized_desc = event_description.lower() if event_description else ""
        
        # Check if this matches any known recurring event
        for event_key, event_data in recurring_events.items():
            for pattern in event_data["patterns"]:
                if pattern in normalized_desc:
                    return True, event_key, event_data["standard_dates"]
        
        # Default: not a recognized recurring event
        return False, None, None
    
    def _validate_search_query(self, search_query, event_type, event_description):
        """
        Validates and corrects search queries to ensure they focus on finding event dates.
        """
        from datetime import datetime
        current_year = datetime.now().year
        
        # Common patterns to enforce
        recurring_event_patterns = {
            "hurricane": f"hurricane season {current_year} dates",
            "black friday": f"black friday {current_year} date",
            "holiday season": f"holiday shopping season {current_year} dates",
            "christmas": f"christmas {current_year} date",
            "tax": f"tax season {current_year} dates",
            "school": f"school year {current_year} dates",
            "election": f"election day {current_year} date",
        }
        
        # Check if this is a known event type
        if event_description:
            event_desc_lower = event_description.lower()
            for key, pattern in recurring_event_patterns.items():
                if key in event_desc_lower:
                    return pattern
        
        # General validation - remove analytical terms
        analytical_terms = ["most", "best", "top", "average", "sold", "sales", "revenue", 
                            "profit", "performance", "growth", "by region", "by category", 
                            "by product", "analysis", "comparison", "trend"]
        
        # Start with the original query
        validated_query = search_query
        
        # Remove analytical terms
        for term in analytical_terms:
            validated_query = validated_query.replace(term, "")
        
        # Add year if not present and this looks like a recurring event
        if str(current_year) not in validated_query and any(marker in event_description.lower() for marker in ["season", "holiday", "annual", "yearly"]):
            validated_query = f"{validated_query.strip()} {current_year}"
        
        # Add "dates" if not present
        if "date" not in validated_query:
            validated_query = f"{validated_query.strip()} dates"
        
        # Clean up extra spaces
        validated_query = " ".join(validated_query.split())
        
        return validated_query
        
    def _process_event_search_results(self, user_question, event_type, event_description, search_results_text):
        """
        Processes search results to extract temporal information about an event.
        Returns a dictionary with start_date, end_date, and other relevant information.
        """
        try:
            # Get current date for context
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")
            
            query_system = sysprompts.SYS_PROMPT_EVENT_SEARCH_RESULT_PROCESSING_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_EVENT_SEARCH_RESULT_PROCESSING_PROMPT.replace(
                '{user_question}', user_question
            ).replace(
                '{event_type}', event_type or 'Unknown'
            ).replace(
                '{event_description}', event_description or 'Unknown'
            ).replace(
                '{search_results}', search_results_text
            ).replace(
                '{current_date}', current_date
            )
            
            logging.debug(f'_process_event_search_results - system: {query_system}')
            logging.debug(f'_process_event_search_results - prompt: {query_prompt}')
            print(f'_process_event_search_results - system: {query_system}')
            print(f'_process_event_search_results - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logging.debug('Results:' + results)
            print('Results:', results)
            
            # Parse the JSON response
            event_info = json.loads(results)
            
            # Print the extracted values
            print("start_date:", event_info.get("start_date"))
            print("end_date:", event_info.get("end_date"))
            print("time_period_description:", event_info.get("time_period_description"))
            print("sql_date_condition:", event_info.get("sql_date_condition"))
            print("key_insights:", event_info.get("key_insights"))
            
            return event_info
            
        except Exception as e:
            print(f"Error processing event search results: {e}")
            logging.error(f"Error processing event search results: {e}")
            return None
        
    def _detect_combined_references(self, input_question):
        """
        Combined detection of time references and event references in a single LLM call.
        Returns the same tuple structure as calling both functions separately to maintain compatibility.
        """
        try:
            # Get current date information
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")
            
            # Default return values for time references
            has_time_reference = False
            is_ambiguous = False
            default_resolution = None
            
            # Default return values for event references
            has_event_reference = False
            needs_external_info = False
            event_type = None
            event_description = None
            search_query = ""
            
            # Check if internet search is allowed for event detection
            event_detection_enabled = self._is_internet_search_allowed()
            
            query_system = sysprompts.SYS_PROMPT_COMBINED_REFERENCE_DETECTION_SYSTEM.replace(
                '{current_date}', current_date
            )
            query_prompt = sysprompts.SYS_PROMPT_COMBINED_REFERENCE_DETECTION_PROMPT.replace(
                '{user_question}', input_question
            )
            
            logging.debug(f'_detect_combined_references - system: {query_system}')
            logging.debug(f'_detect_combined_references - prompt: {query_prompt}')
            print(f'_detect_combined_references - system: {query_system}')
            print(f'_detect_combined_references - prompt: {query_prompt}')
            
            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logging.debug('Combined detection results:' + results)
            print('Combined detection results:', results)
            result = json.loads(results)
            
            # Extract time reference values
            has_time_reference = result.get("has_time_reference", False)
            is_ambiguous = result.get("is_ambiguous", False)
            default_resolution = result.get("default_resolution", None)
            
            # Extract event reference values only if internet search is allowed
            if event_detection_enabled:
                has_event_reference = result.get("has_event_reference", False)
                needs_external_info = result.get("needs_external_info", False)
                event_type = result.get("event_type", None)
                event_description = result.get("event_description", None)
                search_query = result.get("search_query", "")
                
                # Validate and correct the search query if needed
                if search_query:
                    search_query = self._validate_search_query(search_query, event_description)
            
            # Print the extracted values for debugging
            print("--- Combined Detection Results ---")
            print("Time Reference Detection:")
            print("  has_time_reference:", has_time_reference)
            print("  is_ambiguous:", is_ambiguous)
            print("  default_resolution:", default_resolution)
            print("Event Reference Detection:")
            print("  has_event_reference:", has_event_reference)
            print("  needs_external_info:", needs_external_info)
            print("  event_type:", event_type)
            print("  event_description:", event_description)
            print("  search_query:", search_query)
            print("---------------------------------")
            
        except Exception as e:
            print(f'ERROR (_detect_combined_references): {str(e)}')
            logging.error(f'ERROR (_detect_combined_references): {e}')
            # Return default values on error
            has_time_reference = False
            is_ambiguous = False
            default_resolution = None
            has_event_reference = False
            needs_external_info = False
            event_type = None
            event_description = None
            search_query = ""
        
        # Return two tuples to maintain compatibility with existing code
        time_tuple = (has_time_reference, is_ambiguous, default_resolution)
        event_tuple = (has_event_reference, needs_external_info, event_type, event_description, search_query)
        
        return time_tuple, event_tuple

    def _is_more_info_required(self):
        try:
            IS_REQUIRED = True
            query_system = sysprompts.SYS_PROMPT_QUERY_NEED_ADDITIONAL_INFO_CHECK_SYSTEM

            # Check if we should use combined analysis (only if internet search is enabled otherwise there is no gain, only potential loss)
            if False and cfg.USE_COMBINED_ANALYSIS and cfg.ALLOW_INTERNET_SEARCH_FOR_NLQ:
                # Use the combined detection function
                time_results, event_results = self._detect_combined_references(self.environment.current_input_question)
                has_time_reference, is_ambiguous, default_resolution = time_results
                has_event_reference, needs_external_info, event_type, event_description, search_query = event_results
            else:
                # First check for time references (existing functionality)
                has_time_reference, is_ambiguous, default_resolution = self._detect_time_references(self.environment.current_input_question)
                
                # Initialize event variables
                has_event_reference = False
                needs_external_info = False
                event_type = None
                event_description = None
                search_query = ""
                
                # Only perform event detection if internet search is allowed
                if cfg.ALLOW_INTERNET_SEARCH_FOR_NLQ:
                    # Check for event references
                    has_event_reference, needs_external_info, event_type, event_description, search_query = self._detect_event_references(self.environment.current_input_question)
                
            # Store reference information in environment
            self.environment.has_time_reference = has_time_reference
            self.environment.is_time_ambiguous = is_ambiguous
            self.environment.time_default_resolution = default_resolution
            self.environment.has_event_reference = has_event_reference
            self.environment.needs_external_info = needs_external_info
            self.environment.event_type = event_type
            self.environment.event_description = event_description
            
            # If we need external info and it's allowed, perform web search
            event_info = None
            search_results_text = ""
            
            if cfg.ALLOW_INTERNET_SEARCH_FOR_NLQ and has_event_reference and needs_external_info and self.web_search is not None:
                cache_key = f"{event_type}:{event_description}"
                
                if cache_key in self.event_cache:
                    print(f"Using cached event information for: {event_description}")
                    event_info = self.event_cache[cache_key]
                else:
                    print(f"Performing web search for event: {event_description}")
                    try:
                        # Ensure search query is properly focused
                        if not search_query or len(search_query.strip()) < 3:
                            # Fallback if search query is empty or too short
                            from datetime import datetime
                            current_year = datetime.now().year
                            search_query = f"{event_description} {current_year} dates"
                        
                        print(f"Using search query: {search_query}")
                        search_results = None
                        search_results_text = None
                        search_results = self.web_search.search_ai(search_query, num_results=3)
                        print(f"Search results: {search_results}")

                        # # Format search results for processing
                        # search_results_text = "\n\n".join(
                        #     [f"Result {i+1}:\nTitle: {r['title']}\nLink: {r['link']}\nSnippet: {r['snippet']}" 
                        #     for i, r in enumerate(search_results)]
                        # )

                        # Initialize ai_answer and result list
                        ai_answer = search_results["ai_answer"]
                        results_only = search_results["results"]

                        # Check if ai_answer is present and non-empty
                        # if search_results and isinstance(search_results[0], dict) and "ai_answer" in search_results[0]:
                        #     potential_answer = search_results[0].get("ai_answer", "").strip()
                        #     if potential_answer:
                        #         ai_answer = potential_answer
                        #         results_only = search_results[1:]

                        # Build the search results text
                        search_results_text = ""

                        # Include ai_answer at the top if available
                        if ai_answer:
                            search_results_text += f"AI Answer:\n{ai_answer}\n\n"

                        # Add the formatted search results
                        if search_results and isinstance(search_results, dict) and results_only:
                            search_results_text += "\n\n".join(
                                [f"Search Results {i+1}:\nTitle: {r['title']}\nLink: {r['link']}\nSnippet: {r['snippet']}" 
                                for i, r in enumerate(results_only)]
                            )

                        # Process search results to extract temporal information
                        print('Processing event search results:', search_results_text)
                        if search_results_text != "":
                            event_info = self._process_event_search_results(
                                self.environment.current_input_question,
                                event_type,
                                event_description,
                                search_results_text
                            )
                        else:
                            logging.debug(f'WARNING: WebSearch with AI returned no results (_is_more_info_required)')
                            print(f'WARNING: WebSearch with AI returned no results (_is_more_info_required)')
                        
                        # Cache the results
                        if event_info:
                            self.event_cache[cache_key] = event_info
                        
                    except Exception as e:
                        print(f"Error during web search: {e}")
                        logging.error(f"Error during web search: {e}")
                
                # Store event info in environment for later use
                if event_info:
                    self.environment.event_info = event_info

            # Construct question input
            question_prompt = self._get_question_hist_for_prompt()
            
            # Construct query input
            query_prompt = self._get_query_hist_for_prompt()

            # Construct data input 
            df_prompt = self._get_data_hist_for_prompt()

            # Get schema
            schema = self.environment.current_full_schema

            # Get recent conversation history
            conversation_history = self.environment.get_recent_chat_history_for_prompt(num_entries=5, include_current_question=False, number_assistant_messages=False) or ''

            # Add reference information to the prompt if available
            context_info = ""
            if has_time_reference:
                context_info += f"Time reference detected: {default_resolution if default_resolution else 'None'}\n"
            if has_event_reference:
                context_info += f"Event reference detected: {event_description} (type: {event_type})\n"
                if event_info:
                    context_info += f"Event period: {event_info.get('time_period_description', 'Unknown')}\n"
                    context_info += f"Event date range: {event_info.get('start_date', 'Unknown')} to {event_info.get('end_date', 'Unknown')}\n"

            query_check_prompt = sysprompts.SYS_PROMPT_QUERY_NEED_ADDITIONAL_INFO_CHECK_PROMPT.replace(
                '{question}', question_prompt or ''  # Deprecated (kept in case of rollback)
            ).replace(
                '{query}', query_prompt or ''
            ).replace(
                '{dataset}', df_prompt or ''
            ).replace(
                '{current_question}', self.environment.current_input_question or ''
            ).replace(
                '{schema}', schema or ''
            ).replace(
                '{ai_request}', self.environment.last_answer_requested_more_info_message or ''  # Deprecated (conv hist s/b enough - kept in case of rollback)
            ).replace(
                '{context_info}', context_info or ''
            ).replace(
                '{current_date}', self.environment.get_current_date()
            ).replace(
                '{conversation_history}', conversation_history or ''
            )
            
            print(86 * '&')
            logging.debug(f'Query check system (_is_more_info_required): {query_system}')
            logging.debug(f'Query check prompt (_is_more_info_required): {query_check_prompt}')
            print(f'Query check system (_is_more_info_required): {query_system}')
            print(f'Query check prompt (_is_more_info_required): {query_check_prompt}')
            results = azureQuickPrompt(prompt=query_check_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            logging.debug(f'Query check results (_is_more_info_required): {results}')
            print(f'Query check results (_is_more_info_required): {results}')

            try:
                # Default return values
                sufficient_information = 'yes'
                request_for_more_information = ''
                confidence = '0'

                result = json.loads(results)
                # Extract individual values
                sufficient_information = result["sufficient_information"]
                request_for_more_information = result["request_for_more_information"]
                confidence = result["confidence"]
                
                # Print the extracted values
                print("Sufficient Information:", sufficient_information)
                print("Request for More Information:", request_for_more_information)
                print("Confidence:", str(confidence))

                # If we performed a web search but the LLM still requests more info,
                # we can provide context about what we found
                if has_event_reference and needs_external_info and event_info and str(sufficient_information).lower() == 'no':
                    request_for_more_information += f"\n\nBased on information I found about {event_description}, it occurred from {event_info.get('start_date', 'unknown date')} to {event_info.get('end_date', 'unknown date')}. "
                    request_for_more_information += f"Does this time period align with what you're asking about?"

                # Handle ambiguous time references
                if is_ambiguous and str(sufficient_information).lower() == 'yes' and int(confidence) < 90:
                    sufficient_information = 'no'
                    request_for_more_information = f"I noticed your question refers to a time period ({default_resolution}), but I'm not sure which specific year you're interested in. Could you please clarify?"
                    confidence = '85'  # Set a reasonably high confidence

                if str(sufficient_information).lower() == 'no':
                    IS_REQUIRED = True
                else:
                    IS_REQUIRED = False
            except:
                print('WARNING: Failed to parse results, could not evaluate question...')
                logging.error('WARNING: Failed to parse results, could not evaluate question...')
                IS_REQUIRED = False
                request_for_more_information = ''
                confidence = '0'
        except Exception as e:
            IS_REQUIRED = False
            request_for_more_information = ''
            confidence = '0'
            logging.error(f'Error checking if additional info is required (v2): {e}')
            print(f'Error checking if additional info is required (v2): {e}')
        return IS_REQUIRED, request_for_more_information, confidence


    def _is_more_info_required_initial_question(self):
        try:
            IS_REQUIRED = False
            IS_RELEVANT = True
            query_system = sysprompts.SYS_PROMPT_QUERY_INITIAL_QUESTION_CHECK_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_QUERY_INITIAL_QUESTION_CHECK_PROMPT.replace('{user_question}', self.environment.current_input_question).replace('{schema}', self.environment.current_full_schema).replace('{table_descriptions}', get_table_descriptions_as_yaml(self.agent_connection_id))
            logging.debug(f'_is_more_info_required_initial_question - system: {query_system}')
            logging.debug(f'_is_more_info_required_initial_question - prompt: {query_prompt}')
            print(f'_is_more_info_required_initial_question - system: {query_system}')
            print(f'_is_more_info_required_initial_question - prompt: {query_prompt}')
            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            # Default return values
            needs_more_information = 'no'
            request_for_more_information = ''
            confidence = ''
            relevant = 'yes'
            relevant_response = ''
            
            logging.debug('Results:' + results)
            print('Results:', results)
            result = json.loads(results)
            # Extract individual values
            needs_more_information = result["needs_more_information"]
            try:
                request_for_more_information = result["request_for_more_information"]
            except:
                request_for_more_information = ''
            confidence = result["confidence"]
            relevant = result["relevant"]
            try:
                relevant_response = result["relevant_response"]
            except:
                relevant_response = ''
            
            # Print the extracted values
            print("Needs More Information:", needs_more_information)
            print("Request for More Information:", request_for_more_information)
            print("Confidence:", str(confidence))
            print("relevant:", relevant)
            print("relevant_response:", relevant_response)

            if str(needs_more_information).lower() == 'yes':
                IS_REQUIRED = True
            else:
                IS_REQUIRED = False

            if str(relevant).lower() == 'no':
                IS_RELEVANT = False
            else:
                IS_RELEVANT = True
        except Exception as e:
            print(str(e))
            logging.error(f'ERROR (_is_more_info_required_initial_question): {e}')
            IS_REQUIRED = False
            IS_RELEVANT = True
            request_for_more_information = ''
            confidence = ''
            relevant = 'yes'
            relevant_response = ''

        return IS_REQUIRED, request_for_more_information, confidence, IS_RELEVANT, relevant_response


    def _initialize_data(self, input_question, is_first_question=True):
        try:
            logging.debug('---------- INIT DATA ----------')
            logging.info('Getting query from question...')
            print('Getting query from question...')
            query = self._get_query_from_question(input_question, is_first_question=is_first_question)
            # If no query was produced at all, something is very wrong and you should exit, otherwise proceed...
            if query is not None:
                query = self._apply_known_query_mods(query)
                self.environment.current_query = query
                logging.info('Loading data from query...')
                print('Loading data from query...')
                df = self._load_query(query)
                if df is not None:
                    df_desc = self._get_df_description(input_question, query)
                    if df_desc is not None:
                        self.environment.dfs_desc.append(df_desc)
                    LOAD_SUCCESS = True
                else:
                    logging.info('Something went wrong checking query from question...')
                    print('Something went wrong checking query from question...')
                    full_schema = get_all_column_descriptions_as_yaml(self.agent_connection_id)
                    new_query = self._check_query(input_question, full_schema, query)
                    new_query = self._apply_known_query_mods(new_query)
                    logging.info('New Query:' + str(new_query))
                    print('New Query:' + str(new_query))
                    new_df = self._load_query(new_query)

                    # Was the second attempt successful?
                    if new_df is not None:
                        self.environment.df = new_df
                        df = new_df
                        self.environment.current_query = new_query
                        df_desc = self._get_df_description(input_question, new_query)
                        if df_desc is not None:
                            self.environment.dfs_desc.append(df_desc)
                        LOAD_SUCCESS = True
                    else:
                        LOAD_SUCCESS = False
                        logging.info('Query was unsuccessful and was not able to be fixed.')
                        print('Query was unsuccessful and was not able to be fixed.')
            else:
                LOAD_SUCCESS = False
                logging.info('Query was unsuccessful and was not able to be generated from the question.')
                print('Query was unsuccessful and was not able to be generated from the question.')
                    
            if LOAD_SUCCESS and cfg.CHECK_FOR_MISSING_HEADERS:
                if self._has_missing_headers(df):
                    logging.info('WARNING: Missing headers detected, attempting to fix...')
                    print('WARNING: Missing headers detected, attempting to fix...')
                    new_query = self._check_query_alias(input_question, self.environment.schema, query)
                    if new_query != query:
                        df = self._load_query(new_query)
                        if df is not None:
                            self.environment.df = df
                            LOAD_SUCCESS = True
                            print('Corrected missing headers')
                            logging.info('Corrected missing headers')
                        else:
                            print('Unable to correct missing headers, keeping original dataset...')
                            logging.info('Unable to correct missing headers, keeping original dataset...')

            self.environment.was_last_query_successful = LOAD_SUCCESS
            if LOAD_SUCCESS:
                self.environment.dfs.append(self.environment.df)
                self.environment.query_hist.append(query)
                self.environment.last_query_fail_message = ''
            else:
                self.environment.last_query_fail_message = 'Query generation/execution was unsuccessful and was not able to be fixed.'
                    
            return LOAD_SUCCESS
        except Exception as e:
            logging.error('ERROR (_initialize_data):' + str(e))
            print('ERROR (_initialize_data):' + str(e))
            return False
