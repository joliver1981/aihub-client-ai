import time
import os
import logging
from logging.handlers import WatchedFileHandler
from LLMDataEnvironment import Environment
from LLMQueryEngine import LLMQueryEngine
from LLMAnalyticalEngine import LLMAnalyticalEngine
import config as cfg
import system_prompts as sysprompts
from AppUtils import azureQuickPrompt, set_user_request_id
import json
from DataUtils import get_table_descriptions_as_yaml
import ast
import json
from response_filter import ResponseFilter
import pandas as pd


def _is_result_empty(result) -> bool:
    """Check if a query result is effectively empty (all None, 0, $0.00, or empty string).

    Used as a safety net to detect when PandasAI returns a stale/zero result
    while the SQL engine has real data.
    """
    if result is None:
        return True
    if isinstance(result, pd.DataFrame):
        if result.empty:
            return True
        # Check if all values are effectively zero/empty
        for col in result.columns:
            for val in result[col]:
                if val is None or val == '' or val == 'None':
                    continue
                # Handle currency-formatted strings like "$0.00"
                if isinstance(val, str):
                    cleaned = val.replace('$', '').replace(',', '').strip()
                    try:
                        if float(cleaned) != 0:
                            return False
                    except (ValueError, TypeError):
                        # Non-numeric string with content — not empty
                        if cleaned:
                            return False
                elif isinstance(val, (int, float)):
                    if val != 0:
                        return False
        return True
    return False
import uuid

try:
    if cfg.SMART_RENDER_HYBRID_ENABLED:
        from SmartContentRenderer_hybrid import SmartContentRendererHybrid as SmartContentRenderer
    else:
        from SmartContentRenderer import SmartContentRenderer
except:
    from SmartContentRenderer import SmartContentRenderer

from CommonUtils import rotate_logs_on_startup, get_log_path
import pandas as pd


rotate_logs_on_startup(os.getenv('LLM_DATA_ENGINE_LOG', get_log_path('llm_data_engine_log.txt')))

# Configure logging
logger = logging.getLogger("LLMDataEngineV2")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('LLM_DATA_ENGINE_LOG', get_log_path('llm_data_engine_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class LLMDataEngine:
    def __init__(self, provider="openai"):
        self.provider = provider
        self.environment = Environment()
        self.query_engine = LLMQueryEngine(self.environment, provider=self.provider)
        self.analytical_engine = LLMAnalyticalEngine(self.environment, provider=self.provider)
        self._consecutive_auto_responses = 0
        self._handling_error = False  # Add this flag
        self.content_renderer = SmartContentRenderer()

    def __getstate__(self):
        state = self.__dict__.copy()
        # Remove unpicklable objects
        state['query_engine'] = None
        state['analytical_engine'] = None
        self.environment.chat_history = None   # TODO Attempt to fix recursive error
        return state


    def __setstate__(self, state):
        self.__dict__.update(state)
        # Restore unpicklable objects
        self.query_engine = LLMQueryEngine(self.environment, provider=self.provider)
        self.analytical_engine = LLMAnalyticalEngine(self.environment, provider=self.provider)


    def clear_chat_hist(self):
        self.environment.chat_history = []

    
    def format_response_with_rich_content(self, answer, answer_type, context=None):
        """
        Transform traditional response into rich content format
        """
        # For backward compatibility, check if rich content is enabled
        if not cfg.ENABLE_RICH_CONTENT_RENDERING:
            return answer, answer_type
        
        # Handle different answer types
        if answer_type == 'dataframe':
            # Let SmartContentRenderer handle dataframe rendering
            rich_response = self.content_renderer.analyze_and_render(
                answer, 
                context={'query': context.get('question'), 'type': 'data_response'}
            )
        elif answer_type == 'string':
            # Analyze string content for potential structured elements
            rich_response = self.content_renderer.analyze_and_render(
                answer,
                context={'query': context.get('question'), 'type': 'text_response'}
            )
        elif answer_type == 'chart':
            # Handle chart responses
            rich_response = {
                'type': 'rich_content',
                'blocks': [{
                    'type': 'chart',
                    'content': answer,
                    'metadata': {'generated': True}
                }]
            }
        else:
            # Default fallback
            rich_response = self.content_renderer._create_text_block(str(answer))
        
        return rich_response, 'rich_content'


    def add_message_to_hist(self, message, is_user=True):
        if is_user:
            role = 'user'
        else:
            role = 'assistant'
        self.environment.chat_history.append({"role": role, "content": message})
        #self.analytical_engine.add_message_to_hist(message, is_user=is_user)

    def _fix_conversation_history(self, conversation_history_str):
        """
        A direct, focused approach to fix conversation history
        with special handling for entries containing backticks.
        
        Args:
            conversation_history_str: The conversation history string
            
        Returns:
            list: The parsed conversation history
        """
        print(f"Analyzing conversation history input ({len(conversation_history_str)} chars)")
        
        # If input is already a list, return it as is
        if isinstance(conversation_history_str, list):
            print("Input is already a list, no parsing needed")
            return conversation_history_str
        
        # Try standard parsing first
        try:
            import ast
            result = ast.literal_eval(conversation_history_str)
            print("Standard parsing successful")
            return result
        except Exception as e:
            print(f"Standard parsing failed: {str(e)[:100]}")
        
        # Direct approach - handle the specific issue
        result = []
        
        # Simple string-based identification of entries
        try:
            # First remove outer brackets and whitespace
            clean_str = conversation_history_str.strip()
            if clean_str.startswith("["):
                clean_str = clean_str[1:]
            if clean_str.endswith("]"):
                clean_str = clean_str[:-1]
            
            # Insert a special marker before every entry
            marked_str = clean_str.replace("{'role':", "###ENTRY###{'role':")
            
            # Split by the marker
            entries = marked_str.split("###ENTRY###")
            
            # Process each entry
            for i, entry_text in enumerate(entries):
                # Skip empty entries
                if not entry_text.strip():
                    continue
                    
                # Make sure entry starts properly
                entry_text = entry_text.strip()
                if not entry_text.startswith("{"):
                    entry_text = "{" + entry_text
                    
                # Check if this is an entry with backticks
                is_problematic = "`" in entry_text
                
                if is_problematic:
                    print(f"Found problematic entry {i} with backtick")
                    
                    # Extract role
                    import re
                    role_match = re.search(r"'role':\s*'([QA])'", entry_text)
                    if role_match:
                        role = role_match.group(1)
                    else:
                        # Default to 'A' if we can't determine
                        role = 'A'
                        print(f"Could not determine role for entry {i}, using 'A'")
                    
                    # Extract the content directly - we know the structure
                    content_start = entry_text.find("'content': '") + len("'content': '")
                    
                    # Extract everything after the content marker up to the last possible content end
                    content_end = entry_text.rfind("'}")
                    if content_end > content_start:
                        content = entry_text[content_start:content_end]
                    else:
                        # If we can't find the end, take a more aggressive approach
                        # Find where the next entry likely begins by looking for a comma followed by role marker
                        next_entry = entry_text.find("}, {")
                        if next_entry > content_start:
                            content = entry_text[content_start:next_entry]
                        else:
                            # Last resort - take everything after content_start
                            content = entry_text[content_start:]
                    
                    # Clean up the content - fix the prefix issue
                    if content.startswith("\', \'content\': \""):
                        content = content[len("\', \'content\': \""):]
                        print(f"Removed incorrect prefix from problematic entry")
                    
                    # Add the problematic entry
                    result.append({"role": role, "content": content})
                    print(f"Successfully extracted problematic entry content ({len(content)} chars)")
                else:
                    # For normal entries, try standard parsing
                    try:
                        # Make sure the entry text is properly formatted
                        if not entry_text.endswith("}"):
                            # Find the proper end of this entry
                            entry_end = entry_text.rfind("}")
                            if entry_end > 0:
                                entry_text = entry_text[:entry_end+1]
                        
                        # Normalize the entry text to help with parsing
                        entry_text = entry_text.replace("\n", " ").strip()
                        
                        # Try to parse with ast.literal_eval
                        import ast
                        entry = ast.literal_eval(entry_text)
                        
                        if isinstance(entry, dict) and 'role' in entry and 'content' in entry:
                            result.append(entry)
                            print(f"Successfully parsed entry {i} with role {entry['role']}")
                        else:
                            print(f"Entry {i} missing required fields: {entry.keys() if isinstance(entry, dict) else type(entry)}")
                    except Exception as e:
                        print(f"Error parsing entry {i}: {str(e)[:100]}")
                        
                        # Fallback for this entry - manual extraction
                        try:
                            import re
                            role_match = re.search(r"'role':\s*'([QA])'", entry_text)
                            content_match = re.search(r"'content':\s*'([^']*)'", entry_text)
                            
                            if role_match and content_match:
                                role = role_match.group(1)
                                content = content_match.group(1)
                                result.append({"role": role, "content": content})
                                print(f"Manually extracted entry {i} with role {role}")
                            else:
                                print(f"Could not extract role/content from entry {i}")
                        except Exception as e:
                            print(f"Manual extraction failed for entry {i}: {str(e)[:100]}")
            
            print(f"Processed {len(result)} entries successfully")
            
            # As a safety check, verify we have question/answer pairs
            q_count = sum(1 for entry in result if entry.get('role') == 'Q')
            a_count = sum(1 for entry in result if entry.get('role') == 'A')
            
            print(f"Entry counts: {q_count} questions, {a_count} answers")
            
            return result
        
        except Exception as e:
            print(f"Processing failed: {str(e)[:100]}")
            # Return empty list as fallback
            return []

    def set_conversation_history(self, conversation_history):
        # Accepts list as string in format: [{"role": "Q", "content": "<user question>"},{"role": "A", "content": "<ai answer>"}]
        self.clear_chat_hist()
        
        try:
            conversation_history = ast.literal_eval(str(conversation_history))
        except:
            logger.warning(f'Problem detected parsing conversation history (in main engine), attempting to repair...')
            conversation_history = self._fix_conversation_history(conversation_history)

        for entry in conversation_history:
            is_user = entry['role'] == 'Q'
            self.add_message_to_hist(entry['content'], is_user=is_user)


    def get_recent_chat_history(self, num_entries):
        """
        Returns the recent conversation history as a formatted string.

        Args:
        - num_entries (int): The number of recent chat entries to include.

        Returns:
        - str: A formatted string of the recent conversation history.
        """
        try:
            # Get the last num_entries from the chat history
            recent_history = self.environment.chat_history[-num_entries:]
            
            # Format each message with a role prefix
            formatted_history = []
            for idx, entry in enumerate(recent_history, start=1):
                role = "User" if entry["role"] == "user" else "Assistant"
                formatted_message = f"{idx}. {role}: {entry['content']}"
                formatted_history.append(formatted_message)
        except Exception as e:
            formatted_history = ''
            print('get_recent_chat_history error - ', str(e))
        
        # Join the formatted messages into a single string with line breaks
        return "\n".join(formatted_history)


    def get_zero_row_answer(self):
        try:
            recent_chat_hist = self.get_recent_chat_history(num_entries=3)
            zero_system = sysprompts.SYS_PROMPT_QUERY_ZERO_ROWS_SYSTEM
            zero_prompt = sysprompts.SYS_PROMPT_QUERY_ZERO_ROWS_PROMPT.replace('{database_schema}', self.environment.current_full_schema).replace('{failed_query}', self.environment.current_query).replace('{conversation_history}', recent_chat_hist).replace('{user_question}', self.environment.current_input_question)
            
            print(86 * 'Z')
            print('Zero Results System:')
            print(zero_system)
            print(86 * '-')
            print('Zero Results Prompt:')
            print(zero_prompt)
            print(86 * 'Z')

            response = azureQuickPrompt(prompt=zero_prompt, system=zero_system, use_alternate_api=True, temp=0.3, provider=self.provider)
            print('ZERO ROW REFINED RESPONSE:', response)
            logger.info(f'ZERO ROW REFINED RESPONSE: {response}')
        except Exception as e:
            response = None
            print(str(e))
            logger.error(str(e))
        return response
    

    def _clean_llm_result(self, result):
        return str(result).replace('```json', '').replace('```sql', '').replace('```', '')
    

    def _00_validate_new_topic_input(self):
        try:
            # First question or questions classified as being a new topic
            # Default return values
            relevant = 'yes'
            response = ''
            confidence = ''
            IS_RELEVANT = True

            query_system = sysprompts.DATA_INPUT_VALIDATION_SYSTEM
            query_prompt = sysprompts.DATA_INPUT_VALIDATION_PROMPT.replace('{user_question}', self.environment.current_input_question).replace('{schema}', self.environment.current_full_schema).replace('{table_descriptions}', get_table_descriptions_as_yaml(self.agent_connection_id))
            logger.debug(f'_00_validate_new_topic_input - system: {query_system}')
            logger.debug(f'_00_validate_new_topic_input - prompt: {query_prompt}')
            print(f'_00_validate_new_topic_input - system: {query_system}')
            print(f'_00_validate_new_topic_input - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logger.debug('Results:' + results)
            print('Results:', results)
            result = json.loads(results)

            # Extract individual values
            relevant = result["relevant"]
            response = result["response"]
            confidence = result["confidence"]
            
            # Print the extracted values
            print("relevant:", relevant)
            print("response:", response)
            print("Confidence:", confidence)

            if str(relevant).lower() == 'no':
                IS_RELEVANT = False
            else:
                IS_RELEVANT = True
        except Exception as e:
            print(str(e))
            logger.error(f'ERROR (_00_validate_new_topic_input): {e}')
            IS_RELEVANT = True
            confidence = ''
            relevant = 'yes'
            response = ''

        return IS_RELEVANT, response, confidence
    

    def _01_classify_input(self):
        try:
            # First question or questions classified as being a new topic
            # Default return values
            classification = ''
            explanation = ''
            confidence = ''
            final_classification = 'unknown'

            # Check if combined analysis is enabled and results are available
            if cfg.USE_COMBINED_ANALYSIS:
                combined_results = self.environment.get_combined_analysis_results()
                if combined_results and "input_classification" in combined_results:
                    result = combined_results["input_classification"]
                    classification = result.get("classification", "new")
                    explanation = result.get("explanation", "")
                    confidence = result.get("confidence", 0)
                    return classification, explanation, confidence

            #query_system = sysprompts.DATA_INPUT_CLASSIFICATION_SYSTEM
            #query_prompt = sysprompts.DATA_INPUT_CLASSIFICATION_PROMPT.replace('{conversation_history}', self.environment.get_recent_chat_history_for_prompt(num_entries=5)).replace('{user_question}', self.environment.current_input_question).replace('{schema}', self.environment.current_full_schema or '').replace('{table_descriptions}', get_table_descriptions_as_yaml(self.environment.current_connection_id) or '')
            
            # Get conversation history
            conversation_history = self.environment.get_recent_chat_history_for_prompt(num_entries=5, include_current_question=False) or ''
            
            # Safely get schema and table descriptions
            schema = self.environment.current_full_schema or ''
            table_descriptions = get_table_descriptions_as_yaml(self.environment.current_connection_id) or ''
            
            query_system = sysprompts.DATA_INPUT_CLASSIFICATION_SYSTEM
            query_prompt = sysprompts.DATA_INPUT_CLASSIFICATION_PROMPT.replace(
                '{conversation_history}', conversation_history
            ).replace(
                '{user_question}', self.environment.current_input_question
            ).replace(
                '{schema}', schema
            ).replace(
                '{table_descriptions}', table_descriptions
            )
            
            logger.debug(f'_01_classify_input - system: {query_system}')
            logger.debug(f'_01_classify_input - prompt: {query_prompt}')
            print(f'_01_classify_input - system: {query_system}')
            print(f'_01_classify_input - prompt: {query_prompt}')

            logger.debug('_01_classify_input - Executing prompt...')
            print('_01_classify_input - Executing prompt...')
            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)

            logger.debug(f'_01_classify_input - Raw Results: {results}')
            print(f'_01_classify_input - Raw Results: {results}')

            logger.debug('_01_classify_input - Cleaning LLM results...')
            print('_01_classify_input - Cleaning LLM results...')
            results = self._clean_llm_result(results)

            logger.debug('_01_classify_input - Loading JSON results...')
            print('_01_classify_input - Loading JSON results...')
            result = json.loads(results)

            # Extract individual values
            classification = result["classification"]
            explanation = result.get("explanation", "")
            confidence = result["confidence"]
            
            # Print the extracted values
            print("classification:", classification)
            print("explanation:", explanation)
            print("Confidence:", str(confidence))

            if 'new' in str(classification).lower():
                final_classification = 'new'
            elif 'follow' in str(classification).lower():
                final_classification = 'follow'
            elif 'response' in str(classification).lower():
                final_classification = 'response'
            else:
                final_classification = 'irrelevant'
        except Exception as e:
            print(str(e))
            logger.error(f'ERROR (_01_classify_input): {e}')
            final_classification = 'error'
            explanation = ''
            confidence = ''

        return final_classification, explanation, confidence
    

    def _can_lookup_query_respond(self):
        try:
            # Add a loop detection mechanism
            # Initialize counter if it doesn't exist
            if not hasattr(self, '_consecutive_auto_responses'):
                self._consecutive_auto_responses = 0

            consecutive_auto_responses = self._consecutive_auto_responses
            logger.debug(f'_can_lookup_query_respond - consecutive_auto_responses: {str(consecutive_auto_responses)}')

            if consecutive_auto_responses > 2:
                # Too many consecutive auto-responses, break the loop
                logger.warning("WARNING: Too many auto-response attempts detected...")
                logger.warning(f"Breaking potential loop after {consecutive_auto_responses} consecutive auto-responses")
                self._consecutive_auto_responses = 0
                return False, "", 0
            
            # Default return values
            can_query_respond = False
            response = ''
            query = ''
            confidence = ''

            # Get current date information for context
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d")

            query_system = sysprompts.SYS_PROMPT_LOOKUP_QUERY_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_LOOKUP_QUERY_PROMPT.replace('{query_history}', self.environment.get_recent_query_hist_for_prompt(num_entries=2)).replace('{datasets}', self.environment.get_recent_data_preview_hist_for_prompt(num_entries=2)).replace('{conversation_history}', self.environment.get_recent_chat_history_for_prompt(num_entries=2)).replace('{database_type}', self.query_engine.agent_database_type).replace('{request}', self.environment.last_answer_requested_more_info_message).replace('{user_question}', self.environment.current_input_question).replace('{schema}', self.environment.current_full_schema).replace('{table_descriptions}', get_table_descriptions_as_yaml(self.environment.current_connection_id)).replace('{current_date}', current_date)
            logger.debug(f'_can_lookup_query_respond - system: {query_system}')
            logger.debug(f'_can_lookup_query_respond - prompt: {query_prompt}')
            print(f'_can_lookup_query_respond - system: {query_system}')
            print(f'_can_lookup_query_respond - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logger.debug('Results:' + results)
            print('Results:', results)
            result = json.loads(results)

            # Extract individual values
            response = result["response"]
            query = result["query"]
            confidence = result["confidence"]
            
            # Print the extracted values
            print("response:", response)
            print("query:", query)
            print("Confidence:", confidence)

            if str(response).lower() == 'yes':
                can_query_respond = True
            else:
                can_query_respond = False

            # If we're going to auto-respond, increment the counter
            if can_query_respond:
                self._consecutive_auto_responses = consecutive_auto_responses + 1
            else:
                self._consecutive_auto_responses = 0
        except Exception as e:
            print(str(e))
            logger.error(f'ERROR (_can_lookup_query_respond): {e}')
            can_query_respond = False
            response = ''
            query = ''
            confidence = ''

        return can_query_respond, query, confidence
    

    # Step 1: Preprocess by escaping real line breaks inside string content
    def _fix_response_json(self, response_text):
        # Find where the "response" value starts and ends
        start_idx = response_text.find('"response": "') + len('"response": "')
        end_idx = response_text.find('",', start_idx)
        
        # Extract and fix the "response" value
        response_value = response_text[start_idx:end_idx]
        response_value_fixed = response_value.replace('\n', '\\n')
        
        # Rebuild the fixed JSON
        corrected_json = response_text[:start_idx] + response_value_fixed + response_text[end_idx:]
        return corrected_json
        
    def _get_lookup_query_response(self, query_results, query_desc):
        try:
            # Default return values
            response = ''
            response_classification = 'user'
            response_type = 'string'

            query_system = sysprompts.SYS_PROMPT_LOOKUP_QUERY_RESPONSE_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_LOOKUP_QUERY_RESPONSE_PROMPT.replace('{results}', query_results).replace('{user_question}', self.environment.current_input_question).replace('{request}', self.environment.last_answer_requested_more_info_message).replace('{current_date}', self.environment.get_current_date()).replace('{description}', query_desc or '')
            logger.debug(f'_get_lookup_query_response - system: {query_system}')
            logger.debug(f'_get_lookup_query_response - prompt: {query_prompt}')
            print(f'_get_lookup_query_response - system: {query_system}')
            print(f'_get_lookup_query_response - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            logger.debug('Results:' + results)
            print('Results:', results)

            # Extract the return values
            try:
                # Use a more robust JSON parsing approach
                import json
                
                # Try a simple parse first
                try:
                    result = json.loads(results)
                except json.JSONDecodeError as e:
                    logger.warning(f"Simple JSON parsing failed, attempting to correct response: {e}")

                    # Fix the response text
                    corrected_results = self._fix_response_json(results)

                    if corrected_results:
                        # Load into a Python dictionary
                        result = json.loads(corrected_results)
                        logger.warning(f"Response corrected successfully")
                    else:
                        # If regex fails, try a fallback approach
                        # Create a default response with the full text
                        result = {
                            "response": results.strip(),
                            "response_classification": "user",  # Default to user
                            "response_type": "string"
                        }
                
                # Extract individual values
                response = result.get("response", results.strip())
                response_classification = result.get("response_classification", "user")
                response_type = result.get("response_type", "string")

                print("response:", response)
                print("response_classification:", response_classification)
                print("response_type:", response_type)
                logger.debug("response:" + response)
                logger.debug("response_classification:" + response_classification)
                logger.debug("response_type:" + response_type)
            except Exception as e:
                print(f'Failed to extract JSON results (_get_lookup_query_response) - {str(e)}')
                logger.error(f'Failed to extract JSON results (_get_lookup_query_response) - {str(e)}')
                # Fallback to using the raw results as the response
                response = results.strip()
                response_classification = "user"  # Default to user classification
                response_type = "string"
        except Exception as e:
            print(str(e))
            logger.error(f'ERROR (_get_lookup_query_response): {e}')
            response = ''
            response_classification = 'user'
            response_type = 'string'

        return response, response_classification, response_type
    
    
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
            
            logger.debug(f'_detect_time_references - system: {query_system}')
            logger.debug(f'_detect_time_references - prompt: {query_prompt}')
            print(f'_detect_time_references - system: {query_system}')
            print(f'_detect_time_references - prompt: {query_prompt}')

            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logger.debug('Results:' + results)
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
            logger.error(f'ERROR (_detect_time_references): {e}')
            has_time_reference = False
            is_ambiguous = False
            default_resolution = None

        return has_time_reference, is_ambiguous, default_resolution
    

    def _is_dataframe_response(self, input_text):
        """
        Detect if the input text is a JSON response containing dataframe data
        that should be displayed to the user.
        
        Args:
            input_text (str): The input text to check
            
        Returns:
            bool: True if the input contains dataframe data to display
        """
        try:
            # Check if input looks like JSON
            if not (input_text.strip().startswith('{') and input_text.strip().endswith('}')):
                return False
                
            # Try to parse as JSON
            data = json.loads(input_text)
            
            # Check for dataframe response structure
            if ("response" in data and 
                "response_type" in data and 
                data["response_type"] == "dataframe" and
                isinstance(data["response"], dict)):
                return True
                
            return False
        except:
            return False


    def _extract_dataframe_from_json(self, input_text):
        """
        Extract dataframe data from JSON input to display to the user.
        
        Args:
            input_text (str): JSON string containing dataframe data
            
        Returns:
            pd.DataFrame: DataFrame created from the JSON data
        """
        try:
            data = json.loads(input_text)
            response_data = data["response"]
            
            # Convert the response data into a pandas DataFrame
            import pandas as pd
            df = pd.DataFrame(response_data)
            return df
        except Exception as e:
            logger.error(f"Error extracting dataframe from JSON: {e}")
            return None
        

    def _perform_combined_pipeline_analysis(self):
        """
        Perform all pipeline analysis in a single LLM call.
        This combines meta-question detection, input classification, data query requirement check,
        more info requirement check, and analytical processing requirement check.
        """
        try:
            # Prepare context information
            conversation_history = self.environment.get_recent_chat_history_for_prompt(
                num_entries=5, 
                include_current_question=False
            ) or ''
            
            schema = self.environment.current_full_schema or ''
            table_descriptions = get_table_descriptions_as_yaml(
                self.environment.current_connection_id
            ) or ''
            
            query_history = self.environment.get_recent_query_hist_for_prompt(
                num_entries=5
            ) or 'No previous queries'
            
            dataset_preview = self.environment.get_recent_data_preview_hist_for_prompt(
                num_entries=5
            ) or 'No existing datasets'
            
            # Build context info including time/event references if available
            context_info = ""
            if hasattr(self.environment, 'has_time_reference') and self.environment.has_time_reference:
                context_info += f"Time reference detected: {self.environment.time_default_resolution or 'None'}\n"
            if hasattr(self.environment, 'has_event_reference') and self.environment.has_event_reference:
                context_info += f"Event reference: {self.environment.event_description} (type: {self.environment.event_type})\n"
                if hasattr(self.environment, 'event_info') and self.environment.event_info:
                    event_info = self.environment.event_info
                    context_info += f"Event period: {event_info.get('time_period_description', 'Unknown')}\n"
                    context_info += f"Date range: {event_info.get('start_date', 'Unknown')} to {event_info.get('end_date', 'Unknown')}\n"
            
            context_info += f"Current date: {self.environment.get_current_date()}"
            
            ai_request = self.environment.last_answer_requested_more_info_message or ''
            
            # Build the combined analysis prompt
            query_system = sysprompts.SYS_PROMPT_COMBINED_PIPELINE_ANALYSIS_SYSTEM
            query_prompt = sysprompts.SYS_PROMPT_COMBINED_PIPELINE_ANALYSIS_PROMPT.replace(
                '{conversation_history}', conversation_history
            ).replace(
                '{user_question}', self.environment.current_input_question
            ).replace(
                '{schema}', schema
            ).replace(
                '{table_descriptions}', table_descriptions
            ).replace(
                '{query_history}', query_history
            ).replace(
                '{dataset_preview}', dataset_preview
            ).replace(
                '{context_info}', context_info
            ).replace(
                '{ai_request}', ai_request
            )
            
            logger.debug(f'Combined pipeline analysis - system: {query_system}')
            logger.debug(f'Combined pipeline analysis - prompt: {query_prompt}')
            print('Performing combined pipeline analysis...')
            
            # Make the LLM call
            results = azureQuickPrompt(prompt=query_prompt, system=query_system, use_alternate_api=True, provider=self.provider)
            results = self._clean_llm_result(results)
            
            logger.debug(f'Combined analysis results: {results}')
            print(f'Combined analysis results: {results}')
            
            # Parse the results
            analysis_results = json.loads(results)
            
            # Store results in environment for cross-class access
            self.environment.store_combined_analysis_results(analysis_results)
            
            return analysis_results
            
        except Exception as e:
            logger.error(f'Error in combined pipeline analysis: {e}')
            print(f'Error in combined pipeline analysis: {e}')
            
            # Return default values on error
            return {
                "meta_question": {
                    "is_meta_question": False,
                    "requested_info_type": None,
                    "related_entity": None,
                    "response": None,
                    "confidence": 0
                },
                "input_classification": {
                    "classification": "new",
                    "explanation": "Error in analysis, defaulting to new question",
                    "confidence": 0
                },
                "data_query_required": {
                    "is_required": True,
                    "explanation": "Error in analysis, assuming data query required",
                    "confidence": 0
                },
                "more_info_required": {
                    "is_required": False,
                    "request_message": "",
                    "confidence": 0
                },
                "analytical_required": {
                    "is_required": True,
                    "explanation": "Error in analysis, assuming analytical processing required",
                    "confidence": 0
                }
            }
        

    def _set_user_request_id(self):
        try:
            # Generate or extract request ID
            request_id = str(uuid.uuid4())
            
            # Determine module from endpoint
            module_name = 'nlq_agent'

            set_user_request_id(module_name=module_name, request_id=request_id)

            # Log the request
            logger.info(f"Starting request {request_id} for module {module_name}")
        except Exception as e:
            print(f"Error setting user request id: {str(e)}")
            logger.error(f"Error setting user request id: {str(e)}")
        

    def get_answer(self, agent_id, input_question, recursion_depth=0):
        start_time = time.time()
        self._set_user_request_id()

        # Default fallback response in case of errors
        fallback_answer = cfg.DATA_AGENT_FALLBACK_RESPONSE
        fallback_explain = "Internal error occurred while processing the query."
        fallback_clarify = ""
        fallback_answer_type = "string"
        fallback_special_message = ""

        # Add circuit breaker to prevent infinite loops
        max_recursion_depth = 1  # Only allow one level of fallback
        if recursion_depth > max_recursion_depth:
            logger.warning(f"Maximum recursion depth ({max_recursion_depth}) reached. Breaking potential loop.")
            return (
                self.environment.df if self.environment.df is not None else fallback_answer,
                fallback_explain, 
                fallback_clarify, 
                "dataframe" if self.environment.df is not None else "string", 
                fallback_special_message,
                input_question,
                "",  # revised_question
                ""   # return_query
            )

        # Check if input is a JSON response containing dataframe data
        if self._is_dataframe_response(input_question):
            # Extract and display the dataframe
            df = self._extract_dataframe_from_json(input_question)
            if df is not None:
                self.environment.df = df
                self.environment.dfs.append(df)
                self.environment.last_query_row_count = len(df)
                self.environment.was_last_query_successful = True

                # Reset the auto-response counter since we're showing a result
                if hasattr(self, '_consecutive_auto_responses'):
                    self._consecutive_auto_responses = 0

                logger.warning(f"Dataframe input question detected. Returning dataframe.")
                return (
                    df,  # The dataframe to display
                    "This is the data you requested.",  # explanation
                    "",  # clarify
                    "dataframe",  # answer_type
                    "",  # special_message
                    input_question,  # original input
                    "",  # revised_question
                    ""   # return_query
                )
            else:
                # Reset the auto-response counter since we're showing a result
                if hasattr(self, '_consecutive_auto_responses'):
                    self._consecutive_auto_responses = 0
                    
                logger.warning(f"Invalid input question detected. Returning fallback response per config.")
                return (
                    cfg.DATA_AGENT_FALLBACK_RESPONSE,  # The dataframe to display
                    "Invalid input question detected.",  # explanation
                    "",  # clarify
                    "string",  # answer_type
                    "",  # special_message
                    input_question,  # original input
                    "",  # revised_question
                    ""   # return_query
                )

        self.query_engine._set_target_database(agent_id)
        self.environment.previous_agent_id = self.environment.agent_id
        self.environment.agent_id = agent_id
        self.environment.question_count += 1
        self.environment.previous_input_question = self.environment.current_input_question
        self.environment.previous_query = self.environment.current_query
        self.environment.current_input_question = input_question
        self.environment.current_connection_id = self.query_engine.agent_connection_id
        self.environment.current_full_schema = self.query_engine.full_schema
        self.environment.is_response = False

        # TEMPORARY TEST - Remove after verifying
        print("=" * 60)
        print("environment.current_full_schema SET:")
        print(f"Length: {len(self.environment.current_full_schema)} characters")
        print("First 200 chars:", self.environment.current_full_schema[:200])
        print("=" * 60)

        # Default return values
        answer = None 
        explain = '' 
        clarify = '' 
        answer_type = 'none' 
        special_message = '' 
        revised_question = '' 
        return_query = ''
        skip_to_analytical_failed = False

        # Perform combined analysis if enabled (do this early in the flow)
        if cfg.USE_COMBINED_ANALYSIS:
            print("Using combined pipeline analysis...")
            self._perform_combined_pipeline_analysis()

        # Check if this is a meta-question about previous processing
        meta_question_result = self.query_engine._detect_meta_question(input_question)
        
        # If this is a meta-question with high confidence, short-circuit the normal flow
        if meta_question_result.get("is_meta_question", False) and meta_question_result.get("confidence", 0) > 70:
            requested_info_type = meta_question_result.get("requested_info_type")
            related_entity = meta_question_result.get("related_entity")
            print(45 * '#', ' META QUESTION DETECTED ', 45 * '#')
            logger.debug('Meta-question detected!')
            print('requested_info_type: ', requested_info_type)
            logger.debug(f'requested_info_type: {requested_info_type}')
            # For data sources, capabilities, and confidence meta-questions, use the enhanced function
            if requested_info_type in ["data_sources", "capabilities", "confidence", "data_source"]:
                print('Answering meta question...')
                logger.debug('Answering meta question...')
                answer = self.query_engine._answer_meta_question(
                    requested_info_type, 
                    input_question,
                    related_entity
                )
                print('Answer:', answer)
                logger.debug(f'Answer (meta): {answer}')
            else:
                # For other meta-question types, use the existing response
                answer = meta_question_result.get("response", "I don't have enough information to answer that question.")
                print('Answer:', answer)
                logger.debug(f'Answer (meta): {answer}')

            answer_type = 'string'
            special_message = ''
            revised_question = ''
            explain = ''
            clarify = ''
            
            # Record timings and add to history
            end_time = time.time()
            total_time = end_time - start_time
            self.environment.total_time_of_last_request = total_time
            
            # Update question history
            self.environment.is_first_question = False
            self.environment.question_history.append(input_question)
            self.environment.query_hist.append("No Query Generated - Meta-question about previous processing")
            
            return answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query


        # 01 Classify user input (new, follow (-up), response, irrelevant)
        input_classification, input_explanation, input_confidence = self._01_classify_input()

        jump_to_end = False
        AUTO_ANSWER_TRIGGERED_FLAG = False

        # 02 Set processing flags for pipeline
        if input_classification == 'new':
            is_data_query_required = True
        elif input_classification == 'follow':
            # Check if follow up can be answered with existing datasets
            is_data_query_required, data_query_explanation, data_query_confidence = self.query_engine._is_data_query_required()
            print('Returned from _is_data_query_required... is_data_query_required = ', is_data_query_required)
            logger.debug('Returned from _is_data_query_required... is_data_query_required = ' + str(is_data_query_required))
        elif input_classification ==  'response':
            self.environment.is_response = True
            is_data_query_required = True     # Should we assume new data is needed? Think about it more...
        elif input_classification == 'irrelevant':
            is_data_query_required = False
            answer = input_explanation
            answer_type = 'string'
            jump_to_end = True
        elif input_classification == 'error':
            is_data_query_required = False
            answer = input_explanation
            answer_type = 'string'
            jump_to_end = True
        else:
            is_data_query_required = False
            answer = 'Encountered an unexpected issue.'
            answer_type = 'string'
            jump_to_end = True
        
        if not jump_to_end:
            # 03 Query data (if applicable)
            if is_data_query_required:
                print('Checking if more info is required...')
                # Check that we have enough context to produce the query
                more_info_required, request_for_more_information, confidence = self.query_engine._is_more_info_required()

                # Check confidence score vs threshold (if confidence less than thresh, reverse decision)
                if more_info_required and int(confidence) < cfg.CONFIDENCE_THRESHOLD_REQUESTING_MORE_INFO:
                    more_info_required = False
                    self.environment.last_answer_requested_more_info = False
                    self.environment.last_answer_requested_more_info_message = ''

                if not more_info_required:
                    load_success = self.query_engine._initialize_data(input_question, is_first_question=self.environment.is_first_question)
                    self.environment.last_answer_requested_more_info = False
                    self.environment.last_answer_requested_more_info_message = ''
                else:
                    answer = request_for_more_information
                    self.environment.last_answer_requested_more_info = True
                    self.environment.last_answer_requested_more_info_message = request_for_more_information
            else:
                load_success = False
                more_info_required = False
                self.environment.last_answer_requested_more_info = False
                self.environment.last_answer_requested_more_info_message = ''
                
            # 03 Analytical layer (if applicable)
            # Did we require additional information from the user?
            if not more_info_required:   # NO
                if load_success and is_data_query_required:  # Data query was required and loaded successfully
                    self.analytical_engine.set_data(self.environment.dfs, self.environment.dfs_desc, input_question)

                    # If data was loaded, check if it is ok to bypass analytical engine
                    if cfg.USE_FORMATTING_AWARE_ANALYTICAL_CHECK:
                        # Use the enhanced formatting-aware version
                        analytical_query_required, analytical_query_required_confidence, explanation, formatting_required, formatting_requirements = self.analytical_engine._is_analytical_query_required_v3_with_formatting()
    
                        if formatting_required and formatting_requirements:
                            # Store formatting requirements in environment
                            self.environment.current_formatting_requirements = formatting_requirements
                    else:
                        # Use the original version
                        analytical_query_required, analytical_query_required_confidence, _ = self.analytical_engine._is_analytical_query_required_v2()

                    if analytical_query_required and int(analytical_query_required_confidence) < cfg.CONFIDENCE_THRESHOLD_FOR_ANALYTICAL_PROCESSING:
                        analytical_query_required = False
                        logger.warning('WARNING: Low confidence detected in decision to use analytical layer, proceeding with original dataset...')
                        print('WARNING: Low confidence detected in decision to use analytical layer, proceeding with original dataset...')

                    if analytical_query_required:
                        # Treat both 'follow' and 'response' as follow-ups for visualization inheritance
                        # 'response' = user responding to AI's request for clarification (still a follow-up context)
                        is_follow_up = (input_classification in ['follow', 'response'])
                        answer, explain, clarify, answer_type, special_message, _ = self.analytical_engine.get_answer(input_question, is_follow_up=is_follow_up)
                        revised_question = ''
                        if 'string' in answer_type and 'Unfortunately, I was not able to answer your question, because of the following error' in str(answer):
                            answer = self.environment.dfs[-1]
                            answer_type = 'dataframe'
                            logger.error('WARNING: Encountered issue during analytical processing, skipping analytical query and displaying data to user...')
                            print('WARNING: Encountered issue during analytical processing, skipping analytical query and displaying data to user...')

                        # Safety net: If the analytical engine returned an empty/zero
                        # result but the SQL engine produced non-empty data, prefer
                        # the direct SQL result.  This guards against stale PandasAI
                        # DuckDB cache returning $0.00 when real data exists.
                        if answer_type == 'dataframe' and is_data_query_required and len(self.environment.dfs) > 0:
                            fresh_df = self.environment.dfs[-1]
                            if _is_result_empty(answer) and not _is_result_empty(fresh_df):
                                logger.warning('Safety net: Analytical engine returned empty/zero but SQL result has data — using SQL result instead.')
                                print('Safety net: Analytical engine returned empty/zero but SQL result has data — using SQL result instead.')
                                answer = fresh_df
                    else:
                        logger.info('Determined dataset is sufficient, skipping analytical query and displaying data to user...')
                        print('Determined dataset is sufficient, skipping analytical query and displaying data to user...')
                        answer = self.environment.dfs[-1]
                        special_message = ''
                        revised_question = ''
                        explain = ''
                        answer_type = 'dataframe'
                        clarify = ''
                elif not is_data_query_required and len(self.environment.dfs) > 0: # Data query was NOT required and data exists for analytical engine
                    # If data was not loaded, skip to analytical engine for the answer
                    self.analytical_engine.set_data(self.environment.dfs, self.environment.dfs_desc, input_question)
                    self.environment.current_input_question = input_question

                    # Pass formatting requirements if available
                    if hasattr(self.environment, 'current_formatting_requirements') and self.environment.current_formatting_requirements:
                        self.analytical_engine.formatting_requirements = self.environment.current_formatting_requirements
                    
                    # Treat both 'follow' and 'response' as follow-ups for visualization inheritance
                    is_follow_up = (input_classification in ['follow', 'response'])
                    answer, explain, clarify, answer_type, special_message, _ = self.analytical_engine.get_answer(input_question, is_follow_up=is_follow_up)
                    revised_question = ''
                    if str(answer_type).lower() == 'error' or 'Empty DataFrame' in str(answer):  # 'Unfortunately, I was not able to answer your question' or 'error' in answer
                        print('WARNING: Error detected getting answer from analytical engine, using data engine instead.')
                        logger.warning('WARNING: Error detected getting answer from analytical engine, using data engine instead.')
                        is_data_query_required = True
                        skip_to_analytical_failed = True
                        _recursive_result = self.get_answer(agent_id=self.environment.agent_id, input_question=input_question, recursion_depth=recursion_depth + 1)
                        if isinstance(_recursive_result, dict):
                            answer = _recursive_result.get('answer', '')
                            explain = _recursive_result.get('explain', '')
                            clarify = _recursive_result.get('clarify', '')
                            answer_type = _recursive_result.get('answer_type', 'string')
                            special_message = _recursive_result.get('special_message', '')
                            revised_question = _recursive_result.get('revised_question', '')
                            return_query = _recursive_result.get('query', '')
                        else:
                            answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query = _recursive_result
                else:
                    # Failed to generate/execute data load query
                    if not load_success:
                        answer = cfg.DATA_AGENT_FALLBACK_RESPONSE
                        special_message = self.environment.last_query_fail_message or ''
                    else:
                        answer = cfg.DATA_AGENT_FALLBACK_RESPONSE
                        special_message = ''
                    explain = 'Failed to initialize data.'
                    answer_type = 'none'
                    revised_question = ''
                    clarify = ''
                    self.environment.was_last_query_successful = False
                    self.environment.current_query = None
                    self.environment.log_environment()
            else:   # YES - we required more information from the user
                answer = request_for_more_information
                special_message = ''
                revised_question = ''
                explain = ''
                answer_type = 'string'
                clarify = request_for_more_information

            end_time = time.time()
            total_time = end_time - start_time
            self.environment.total_time_of_last_request = total_time
            print(f"Total time taken for the request: {total_time:.4f} seconds")

            return_query = ""
            if self.environment.current_query is None or not self.environment.is_new_query or self.environment.last_answer_requested_more_info:
                return_query = ""
            else:
                return_query = '=== Data Query ===\n' + self.environment.current_query
                if self.analytical_engine.pandas_agent is not None:
                    if self.analytical_engine.pandas_agent.last_code_executed:
                        return_query += f'\n=== Analytical Query ===\n{self.analytical_engine.pandas_agent.last_code_executed}'
            return_query += f"\nTotal time taken for the request: {total_time:.4f} seconds"

            self.environment.is_first_question = False
            self.environment.question_history.append(input_question)
            if self.environment.last_answer_requested_more_info:
                self.environment.query_hist.append(f'No Query Generated - AI assistant requested additional information/clarity from the user:\n{self.environment.last_answer_requested_more_info_message}\n')
            elif not load_success:
                self.environment.query_hist.append(f'Failed to generate query.')

            # If more info was requested, try to answer with a lookup query
            query_response_results = ''
            query_response_description = ''
            if self.environment.last_answer_requested_more_info and cfg.ATTEMPT_TO_RESPOND_ON_USERS_BEHALF and not AUTO_ANSWER_TRIGGERED_FLAG:
                print('Checking if a lookup query can answer the question...')
                logger.error('Checking if a lookup query can answer the question...')
                can_query_respond, query, confidence = self._can_lookup_query_respond()
                if can_query_respond and query != '':
                    try:
                        query = self.query_engine._apply_known_query_mods(query)
                        df = self.query_engine._load_query(query)

                        # Add the query to history
                        if df is not None:
                            # Update environment to track this query
                            self.environment.current_query = query
                            self.environment.is_new_query = True
                            self.environment.query_hist.append(query)
                            self.environment.was_last_query_successful = True
                            self.environment.last_query_row_count = len(df)
                            self.environment.dfs.append(df)
                            return_query = '=== Data Query ===\n' + self.environment.current_query
                            return_query += f"\nTotal time taken for the request: {total_time:.4f} seconds"
                            
                            # Get a description for the dataset if needed
                            df_desc = self.query_engine._get_df_description(self.environment.current_input_question, query)
                            if df_desc is not None:
                                self.environment.dfs_desc.append(df_desc)
                                query_response_description = df_desc

                        query_response_results = df.to_string(index=False)
                    except:
                        query_response_results = ''
                        print('Failed attempting to execute query response.')
                        logger.error('Failed attempting to execute query response.')
            elif AUTO_ANSWER_TRIGGERED_FLAG:
                print('Previous response was auto answered, skipping to avoid perpetual loop...')
                logger.error('Previous response was auto answered, skipping to avoid perpetual loop...')

            # Final answer processing
            if self.environment.was_last_query_successful and self.environment.last_query_row_count == 0:  # Formulate a response w/ empty rows for user
                if cfg.REFINE_ZERO_ROW_RESPONSES:
                    new_answer = self.get_zero_row_answer()
                    if new_answer is not None and new_answer != '':
                        answer = new_answer
                        answer_type = 'string'
            elif self.environment.last_answer_requested_more_info and can_query_respond and query_response_results != '':
                # Formulate response in place of the user, using results from the lookup query
                ai_input_response, ai_input_response_classification, ai_input_response_type = self._get_lookup_query_response(query_response_results, query_response_description)

                print('Adding AI request for more info to chat history...')
                self.add_message_to_hist(self.environment.last_answer_requested_more_info_message, is_user=False)     # Log response to history (because not being passed back to user)

                # Supply as user input here...
                print('Issuing response on the users behalf...')
                logger.info('Issuing response on the users behalf...')
                AUTO_ANSWER_TRIGGERED_FLAG = True
                if ai_input_response_classification == 'user':
                    _recursive_result = self.get_answer(agent_id=self.environment.agent_id, input_question=ai_input_response, recursion_depth=recursion_depth + 1)
                    if isinstance(_recursive_result, dict):
                        answer = _recursive_result.get('answer', '')
                        explain = _recursive_result.get('explain', '')
                        clarify = _recursive_result.get('clarify', '')
                        answer_type = _recursive_result.get('answer_type', 'string')
                        special_message = _recursive_result.get('special_message', '')
                        revised_question = _recursive_result.get('revised_question', '')
                        return_query = _recursive_result.get('query', '')
                    else:
                        answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query = _recursive_result
                else:
                    if ai_input_response_type == 'dataframe' and self.environment.was_last_query_successful:
                        print('Showing dataframe to user...')
                        logger.debug('Showing dataframe to user...')
                        answer = self.environment.dfs[-1]
                        answer_type = 'dataframe'
                    else:
                        print('Showing string response to user...')
                        logger.debug('Showing string response to user...')
                        answer = ai_input_response
                        answer_type = 'string'
        else:
            # Jump to end cleanup
            self.environment.is_first_question = False
            self.environment.question_history.append(input_question)
            self.environment.query_hist.append(f'Query generation not possible due to invalid input from user.')

        # Final safety check - ensure we never return empty values
        if answer is None:
            answer = fallback_answer
        elif str(type(answer).__name__) == "DataFrame":
            # DataFrames are already handled properly, do nothing
            pass
        elif isinstance(answer, str) and answer.strip() == "":
            answer = fallback_answer

        if answer_type not in ["string", "dataframe", "chart", "file", "none"]:
            answer_type = "string"

        # Response filter
        print(15 * '*', 'RESPONSE FILTER', 15 * '*')
        # Filter the text response
        if answer_type == 'string':
            # Create filter
            self._handling_error = False  # Reset flag
            response_filter = ResponseFilter()
            original_answer = answer
            answer = response_filter.filter_response(answer, input_question)
        elif answer_type == 'error':
            answer = cfg.DATA_AGENT_FALLBACK_RESPONSE
            self._handling_error = True  # Set flag
        else:
            self._handling_error = False  # Reset flag

        # Before returning, check if rich content rendering is enabled
        if cfg.ENABLE_RICH_CONTENT_RENDERING:
            print('Formatting response with rich content...')
            if 'chartresponse' in str(type(answer)).lower():
                # ChartResponse.__str__() calls PIL Image.show() which opens Windows image viewer.
                # Extract the raw path value to prevent this.
                print(f'ChartResponse detected, extracting path value...')
                logger.info(f'ChartResponse detected, extracting path value...')
                answer = getattr(answer, "value", str(object.__repr__(answer)))
                print(f'Extracted chart path: {answer}')
                logger.info(f'Extracted chart path: {answer}')
            elif 'dataframeresponse' in str(type(answer)).lower():
                print(f'Non-standard dataframe detected, converting to standard dataframe...')
                logger.info(f'Non-standard dataframe detected, converting to standard dataframe...')
                answer = getattr(answer, "value", answer)
                print(f'Conversion to type: {str(type(answer))} successful')
                logger.info(f'Conversion to type: {str(type(answer))} successful')
            elif 'smartdataframe' in str(type(answer)).lower():
                print(f'Non-standard dataframe detected, converting to standard dataframe...')
                logger.info(f'Non-standard dataframe detected, converting to standard dataframe...')
                if not isinstance(answer, pd.DataFrame):
                    # Save the column headers from the nonstandard dataframe
                    headers = answer.columns if hasattr(answer, 'columns') else None

                    # Convert answer to a pandas DataFrame
                    answer = pd.DataFrame(answer)

                    # Manually set the headers if they were saved
                    if headers is not None:
                        answer.columns = headers
                print(f'Conversion to type: {str(type(answer))} successful')
                logger.info(f'Conversion to type: {str(type(answer))} successful')

            logger.debug(f'Returning formatted answer: {answer}')
            print(f'Returning formatted answer: {answer}')
            logger.debug(f'Returning formatted answer type: {type(answer)}')

            rich_answer, rich_type = self.format_response_with_rich_content(
                answer, 
                answer_type,
                context={'question': input_question, 'agent_id': agent_id}
            )
            
            logger.debug(f'Returned rich answer: {rich_answer}')
            print(f'Returned rich answer: {rich_answer}')
            logger.debug(f'Returned rich answer type: {type(rich_type)}')
            
            # Return both formats for compatibility
            return {
                'answer': answer,  # Original format for backward compatibility
                'answer_type': answer_type,
                'rich_content': rich_answer,  # New rich content format
                'rich_content_enabled': True,
                'explain': explain,
                'clarify': clarify,
                'special_message': special_message,
                'query': return_query
            }
        else:
            # Original return format
            return answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query


    def explain(self):
        return self.analytical_engine.explain()


    @property
    def question_count(self):
        return self.environment.question_count
    
