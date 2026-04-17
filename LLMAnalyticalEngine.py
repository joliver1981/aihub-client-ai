import logging
from logging.handlers import WatchedFileHandler
import pandas as pd
# Force non-interactive matplotlib backend BEFORE any other matplotlib import.
# This prevents plt.show() from opening GUI windows on Windows (which crashes
# the server thread). Must happen before pandasai imports matplotlib.
import matplotlib
matplotlib.use('Agg')
from pandasai import Agent
from pandasai.core.response.error import ErrorResponse
from api_keys_config import create_pandasai_llm
import base64
import os
import config as cfg
from AppUtils import azureQuickPrompt, azureMiniQuickPrompt
import system_prompts as sysprompts
import json
import time
from DataUtils import get_enhanced_column_metadata_as_yaml, get_calculated_metrics_as_yaml
from CommonUtils import rotate_logs_on_startup, get_log_path


rotate_logs_on_startup(os.getenv('LLM_ANALYTICAL_ENGINE_LOG', get_log_path('llm_analytical_engine_log.txt')))

# Configure logging
logger = logging.getLogger("LLMAnalyticalEngine")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('LLM_ANALYTICAL_ENGINE_LOG', get_log_path('llm_analytical_engine_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class LLMAnalyticalEngine:
    def __init__(self, environment, provider="openai"):
        self.environment = environment
        self.provider = provider
        self.llm = create_pandasai_llm(use_alternate_api=True)
        self.pandas_agent = None
        self.formatting_requirements = None

    def _build_description(self):
        """Convert dfs_desc list into a single description string for PandasAI v3 Agent."""
        dfs_desc = getattr(self.environment, 'dfs_desc', None)
        if not dfs_desc:
            return None
        if len(dfs_desc) == 1:
            return dfs_desc[0]
        return "Dataset descriptions:\n" + "\n".join(
            f"- DataFrame {i+1}: {desc}" for i, desc in enumerate(dfs_desc)
        )

    def add_message_to_hist(self, message, is_user=True):
        logger.debug(f'Adding message to history: {message}, is_user: {is_user}')
        print(f'Adding message to history: {message}, is_user: {is_user}')
        if self.pandas_agent is not None:
            self.pandas_agent.add_message(message, is_user=is_user)
        else:
            logger.info('Message not added to hist, agent is None...')
            print('Message not added to hist, agent is None...')

    def set_conversation_hist(self, q_a_conversation):
        for entry in q_a_conversation:
            role = entry['role']
            content = entry['content']
            is_user = (role == 'Q')
            logger.debug(f'Setting conversation history: role: {role}, content: {content}')
            print(f'Setting conversation history: role: {role}, content: {content}')
            self.add_message_to_hist(content, is_user)

    def _set_conversation(self, conversation):
        for entry in conversation:
            # PandasAI v3 memory format: list of {message, is_user} dicts
            if isinstance(entry, dict) and 'message' in entry:
                msg = entry['message']
                is_user = entry.get('is_user', False)
                logger.debug(f'Setting conversation: is_user: {is_user}, message: {msg}')
                self.pandas_agent.add_message(msg, is_user)
            elif isinstance(entry, dict):
                # Legacy format: {Q: value} or {A: value}
                for key, value in entry.items():
                    is_user = key == "Q"
                    logger.debug(f'Setting conversation: key: {key}, value: {value}')
                    self.pandas_agent.add_message(value, is_user)

    def explain(self):
        # PandasAI v3 removed the explain() method
        logger.debug('explain() not supported in PandasAI v3, returning empty string.')
        return ''

    def _convert_image_to_base64(self, image_path):
        # Ensure we have a string path, not a response object
        if hasattr(image_path, 'value'):
            image_path = image_path.value
        image_path = str(image_path)
        # Resolve relative paths to absolute
        if not os.path.isabs(image_path):
            image_path = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), image_path)
        logger.debug(f'Converting image to base64: {image_path}')
        print(f'Converting image to base64: {image_path}')
        try:
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode()
            logger.debug(f'Image converted to base64 successfully.')
            print(f'Image converted to base64 successfully.')
        except Exception as e:
            logger.error(f'Error converting image to base64: {e}')
            print(f'Error converting image to base64: {e}')
            encoded_string = ""
        return encoded_string

    def _pandas_agent_answer(self, input_question, is_follow_up=False):
        try:
            logger.debug('Attempting to get answer from pandas agent.')
            print('Attempting to get answer from pandas agent.')
            answer = ''
            special_message = ''
            explain = ''
            html_img = ''
            clarify = []

            # Add defensive checks before accessing dataframes
            if not hasattr(self.environment, 'df') or self.environment.df is None:
                logger.error('Error: No dataframe available in environment')
                return "No data available for analysis", "", [], "error", "Missing dataframe", input_question
                
            if not hasattr(self.environment, 'dfs') or not self.environment.dfs:
                logger.error('Error: No dataframes list available in environment')
                return "No data available for analysis", "", [], "error", "Missing dataframes", input_question

            num_rows = len(self.environment.df)
            num_columns = len(self.environment.df.columns)
            logger.debug(f'DataFrame dimensions: {num_rows} rows, {num_columns} columns')
            print(f'DataFrame dimensions: {num_rows} rows, {num_columns} columns')

            if num_rows == 1 and num_columns == 1:  # TODO is this really necessary?
                logger.info('NOTICE: Scalar value detected in dataset, skipping call to agent and returning value, proceeding normally...')
                print('NOTICE: Scalar value detected in dataset, skipping call to agent and returning value, proceeding normally...')
                temp_answer = str(self.environment.df.iloc[0, 0])
                logger.info('Scaler Answer: ' + str(temp_answer))
                print('Scaler Answer:', temp_answer)

            if True:
                if self.pandas_agent is None or (self.environment.previous_agent_id is not None and self.environment.previous_agent_id != self.environment.agent_id):
                    logger.debug('Initializing new pandas agent.')
                    print('Initializing new pandas agent.')
                    _charts_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
                    self.pandas_agent = Agent(self.environment.dfs, description=self._build_description(), config={"llm": self.llm, "enable_cache": False, "open_charts": False, "save_charts": True, "save_charts_path": _charts_dir}, memory_size=10)
                    logger.debug('Successfully initialized pandas agent.')
                    print('Successfully initialized pandas agent.')
                elif not is_follow_up:
                    logger.debug('Processing as a new question, not a follow-up.')
                    print('Processing as a new question, not a follow-up.')
                    try:
                        saved_messages = self.pandas_agent._state.memory.all()
                        logger.debug('Saved conversation history.')
                        print('Saved conversation history.')
                    except:
                        saved_messages = []
                    _charts_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
                    self.pandas_agent = Agent(self.environment.dfs, description=self._build_description(), config={"llm": self.llm, "enable_cache": False, "open_charts": False, "save_charts": True, "save_charts_path": _charts_dir}, memory_size=10)
                    try:
                        if saved_messages:
                            logger.debug('Reloading conversation history.')
                            print('Reloading conversation history.')
                            self._set_conversation(saved_messages)
                    except:
                        logger.debug('Problem reloading conversation history.')
                        print('Problem reloading conversation history.')
                elif is_follow_up:
                    # If the underlying data changed (a new SQL query was executed),
                    # reinitialise the PandasAI Agent so its DuckDB snapshot reflects
                    # the latest query results — not the stale first-query data.
                    if getattr(self, '_data_changed', False):
                        logger.info('Follow-up with new data — reinitialising pandas agent with fresh data.')
                        print('Follow-up with new data — reinitialising pandas agent with fresh data.')
                        try:
                            saved_messages = self.pandas_agent._state.memory.all()
                        except Exception:
                            saved_messages = []
                        _charts_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
                        self.pandas_agent = Agent(self.environment.dfs, description=self._build_description(), config={"llm": self.llm, "enable_cache": False, "open_charts": False, "save_charts": True, "save_charts_path": _charts_dir}, memory_size=10)
                        try:
                            if saved_messages:
                                self._set_conversation(saved_messages)
                        except Exception:
                            pass
                        self._data_changed = False
                    else:
                        logger.debug('Processing as a follow-up question (same data).')
                        print('Processing as a follow-up question (same data).')
                
                # Answer the question w/ PandasAI
                # Check if we have formatting requirements stored
                # TODO: If this is only about auto-formatting then the input question is useless and confusing and provides no output
                # TODO: We also need to handle situations where formatting is required but no info is available for whatever reason...
                if cfg.USE_FORMATTING_AWARE_ANALYTICAL_CHECK:
                    formatting_reqs = getattr(self, 'formatting_requirements', None)
                    processed_input_question = self._preprocess_pandas_input_question(input_question, formatting_requirements=formatting_reqs)
                    
                    # Clear formatting requirements after use
                    if hasattr(self, 'formatting_requirements'):
                        self.formatting_requirements = None
                else:
                    processed_input_question = self._preprocess_pandas_input_question_legacy(input_question)

                logger.debug(f'Processed input question: {processed_input_question}')
                print(f'Processed input question: {processed_input_question}')
                
                # CHART GENERATION STRATEGY
                # When USE_LLM_CHART_GENERATION is True, use LLM spec-based chart generation
                # directly (bypasses PandasAI for charts). When False, let PandasAI handle
                # charts naturally and use LLM spec as fallback on error only.
                if cfg.USE_LLM_CHART_GENERATION:
                    is_chart, chart_spec = self._classify_chart_request(input_question, is_follow_up)
                    if is_chart and chart_spec and self.environment.dfs and len(self.environment.dfs) > 0:
                        df = self.environment.dfs[-1]
                        if df is not None and len(df) > 0:
                            logger.info(f'LLM chart generation: classified as {chart_spec.get("chart_type")} chart')
                            print(f'LLM chart generation: classified as {chart_spec.get("chart_type")} chart')
                            chart_path = self._render_chart_from_spec(df, chart_spec)
                            if chart_path:
                                base64_image_string = self._convert_image_to_base64(chart_path)
                                html_img = f'<img src="data:image/png;base64,{base64_image_string}"/>'
                                logger.info('Chart generated successfully via LLM spec.')
                                print('Chart generated successfully via LLM spec.')
                                return 'See chart...', "Skipping explanation to improve latency.", ['No clarification needed'], 'chart', html_img, input_question
                            else:
                                logger.warning('LLM spec chart rendering failed, falling back to PandasAI')
                                print('LLM spec chart rendering failed, falling back to PandasAI')

                if is_follow_up:
                    answer = self.pandas_agent.follow_up(processed_input_question)
                else:
                    answer = self.pandas_agent.chat(processed_input_question)
                explain = "Skipping explanation to improve latency."
                clarify = ['No clarification needed']

                # CRITICAL: PandasAI may return ChartResponse objects whose __str__
                # calls PIL Image.show(), opening a Windows image viewer. Extract
                # the raw chart path BEFORE any str()/print/logging touches the object.
                if hasattr(answer, 'value') and hasattr(answer, 'type') and str(getattr(answer, 'type', '')) == 'chart':
                    chart_value = answer.value
                    logger.debug(f'ChartResponse detected, extracting path: {chart_value}')
                    print(f'ChartResponse detected, extracting path: {chart_value}')
                    # Resolve to absolute path if relative
                    if chart_value and not os.path.isabs(chart_value):
                        chart_value = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), chart_value)
                    answer = chart_value
                else:
                    logger.debug(f'Answer from pandas agent: {answer}')
                    print(f'Answer from pandas agent: {answer}')

            output_type = type(answer)
            logger.debug(f'Output type: {output_type}')
            print(f'Output type: {output_type}')

            # PandasAI v3 returns ErrorResponse objects on failure
            is_error_response = isinstance(answer, ErrorResponse)
            if is_error_response:
                logger.debug('Error detected from PandasAI, attempting to regenerate code.')
                print('Error detected from PandasAI, attempting to regenerate code.')
                is_error, answer = self._attempt_code_regen()
                
                # Check if a chart path was returned (string ending in .png)
                if not is_error and isinstance(answer, str) and answer.endswith('.png'):
                    # Chart was generated successfully
                    output_type = 'chart'
                    base64_image_string = self._convert_image_to_base64(answer)
                    html_img = f'<img src="data:image/png;base64,{base64_image_string}"/>'
                    special_message = html_img
                    answer = 'See chart...'
                    logger.info('Chart generated successfully via code regeneration.')
                    print('Chart generated successfully via code regeneration.')
                    # CRITICAL: Return immediately to avoid re-processing in the type checking below
                    return answer, explain, clarify, 'chart', special_message, input_question
                elif is_error:
                    # Code regen failed — try LLM spec-based chart generation as fallback
                    if self.environment.dfs and len(self.environment.dfs) > 0 and self.environment.dfs[-1] is not None and len(self.environment.dfs[-1]) > 0:
                        logger.info('Code regen failed, attempting LLM spec-based chart generation...')
                        print('Code regen failed, attempting LLM spec-based chart generation...')
                        is_chart, chart_spec = self._classify_chart_request(input_question, is_follow_up)
                        if is_chart and chart_spec:
                            chart_path = self._render_chart_from_spec(self.environment.dfs[-1], chart_spec)
                            if chart_path:
                                output_type = 'chart'
                                base64_image_string = self._convert_image_to_base64(chart_path)
                                html_img = f'<img src="data:image/png;base64,{base64_image_string}"/>'
                                special_message = html_img
                                answer = 'See chart...'
                                logger.info('Chart generated successfully via LLM spec fallback.')
                                print('Chart generated successfully via LLM spec fallback.')
                                return answer, explain, clarify, 'chart', special_message, input_question
                            else:
                                logger.warning('LLM spec chart rendering failed in error handler.')
                                print('LLM spec chart rendering failed in error handler.')

                        # Not a chart request or chart rendering failed — return dataframe
                        output_type = 'dataframe'
                        answer = self.environment.dfs[-1]
                        if is_chart:
                            special_message = 'Note: Unable to generate the requested chart. Displaying data as a table instead.'
                        logger.info('Returning dataframe after error recovery.')
                        print('Returning dataframe after error recovery.')
                    else:
                        output_type = 'error'
                        logger.info('Error detected with analytical query that could not be resolved.')
                        print('Error detected with analytical query that could not be resolved.')
                else:
                    # Code regeneration succeeded - check if result is usable
                    # If code regen returned an empty DataFrame but we have valid source data, use the source
                    if isinstance(answer, pd.DataFrame) and len(answer) == 0:
                        if self.environment.dfs and len(self.environment.dfs) > 0 and self.environment.dfs[-1] is not None and len(self.environment.dfs[-1]) > 0:
                            logger.info('Code regen returned empty DataFrame, using original source data instead.')
                            print('Code regen returned empty DataFrame, using original source data instead.')
                            answer = self.environment.dfs[-1]
                    output_type = type(answer)
            else:
                output_type = type(answer)

            ###########################################################################################################################
            # Handle dictionary of dataframes format
            if isinstance(answer, dict) and answer.get('type') == 'dataframe' and isinstance(answer.get('value'), dict):
                # This is a multi-dataframe result with named dataframes
                logger.info('Multiple named dataframes detected, converting each to HTML...')
                print('Multiple named dataframes detected, converting each to HTML...')
                
                # Get the dictionary of dataframes/dataframe dicts
                dataframe_dict = answer.get('value', {})
                combined_html = ""
                first_df = None
                
                # Convert each dataframe to HTML and combine them
                for i, (grid_name, grid_data) in enumerate(dataframe_dict.items()):
                    # Handle both DataFrame objects and dict representations
                    if isinstance(grid_data, pd.DataFrame):
                        df = grid_data
                    elif isinstance(grid_data, dict) and 'columns' in grid_data and 'data' in grid_data:
                        # Handle 'split' format dictionary from to_dict(orient='split')
                        try:
                            df = pd.DataFrame(grid_data['data'], columns=grid_data['columns'])
                        except Exception as e:
                            logger.error(f"Error converting dict to DataFrame: {e}")
                            df = pd.DataFrame({'Error': [f'Could not convert {grid_name} to DataFrame: {str(e)}']})
                    elif isinstance(grid_data, dict):
                        # Try other conversion approaches
                        try:
                            df = pd.DataFrame.from_dict(grid_data)
                        except Exception as e:
                            logger.error(f"Error converting dict to DataFrame: {e}")
                            df = pd.DataFrame({'Error': [f'Could not convert {grid_name} to DataFrame: {str(e)}']})
                    else:
                        df = pd.DataFrame({'Error': [f'Unknown data type for {grid_name}']})
                    
                    # Save the first dataframe for conversation history
                    if first_df is None:
                        first_df = df
                    
                    # Add a header with the grid name
                    combined_html += f'<div class="dataframe-section mb-4"><h5>{grid_name}</h5>'
                    # Convert dataframe to HTML
                    combined_html += df.to_html(classes='dataframe', index=False)
                    combined_html += '</div>'
                
                # If no valid dataframes were found, create a fallback
                if first_df is None:
                    first_df = pd.DataFrame({'Message': ['Multiple datasets were generated but could not be parsed']})
                
                # Return the first dataframe for conversation history entries
                # but set the special_message to our combined HTML
                answer = first_df
                special_message = combined_html
                
                # Override the answer_type to use our special handling
                answer_type = 'multi_dataframe'
                
                return answer, explain, clarify, answer_type, special_message, input_question

            # Handle list of dataframes in a dict structure
            elif isinstance(answer, dict) and answer.get('type') == 'dataframe' and isinstance(answer.get('value'), list):
                # This is a multi-dataframe result
                logger.info('Multiple dataframes in list detected, converting each to HTML...')
                print('Multiple dataframes in list detected, converting each to HTML...')
                
                dataframes = answer.get('value', [])
                combined_html = ""
                
                # Convert each dataframe to HTML and combine them
                for i, df in enumerate(dataframes):
                    if isinstance(df, pd.DataFrame):
                        # Add a header for each dataframe
                        combined_html += f'<div class="dataframe-section mb-4"><h5>Dataset {i+1}</h5>'
                        # Convert dataframe to HTML
                        combined_html += df.to_html(classes='dataframe', index=False)
                        combined_html += '</div>'
                
                # Return the primary dataframe for conversation history entries
                # but set the special_message to our combined HTML
                if dataframes and isinstance(dataframes[0], pd.DataFrame):
                    answer = dataframes[0]  # Return first dataframe for normal processing
                    special_message = combined_html  # But use our HTML in special_message
                else:
                    # Create a simple dataframe with a message as fallback
                    answer = pd.DataFrame({'Message': ['Multiple datasets were generated']})
                    special_message = combined_html
                
                # Override the answer_type to use our special handling
                answer_type = 'multi_dataframe'
                
                return answer, explain, clarify, answer_type, special_message, input_question
                
            # Handle direct list of dataframes
            elif isinstance(answer, list) and len(answer) > 0 and all(isinstance(item, pd.DataFrame) for item in answer):
                # This is also a multi-dataframe result as a direct list
                logger.info('Multiple dataframes detected as list, converting each to HTML...')
                print('Multiple dataframes detected as list, converting each to HTML...')
                
                dataframes = answer
                combined_html = ""
                
                # Convert each dataframe to HTML and combine them
                for i, df in enumerate(dataframes):
                    # Add a header for each dataframe
                    combined_html += f'<div class="dataframe-section mb-4"><h5>Dataset {i+1}</h5>'
                    # Convert dataframe to HTML
                    combined_html += df.to_html(classes='dataframe', index=False)
                    combined_html += '</div>'
                
                # Return the primary dataframe for conversation history entries
                # but set the special_message to our combined HTML
                if dataframes:
                    answer = dataframes[0]  # Return first dataframe for normal processing
                    special_message = combined_html  # But use our HTML in special_message
                else:
                    # Create a simple dataframe with a message as fallback
                    answer = pd.DataFrame({'Message': ['Multiple datasets were generated']})
                    special_message = combined_html
                
                # Override the answer_type to use our special handling
                answer_type = 'multi_dataframe'
                
                return answer, explain, clarify, answer_type, special_message, input_question
            ###########################################################################################################################

            if str(output_type).lower().__contains__("int") or str(output_type).__contains__("float") or str(output_type).__contains__("number"):
                answer_type = 'number'
                answer = str(answer)   # Int64 cannot be serialized, convert to string
            elif str(output_type).lower().__contains__("str") and not str(answer).endswith('.png'):
                answer_type = 'string'
                if str(answer).lower().__contains__('main thread is not in main loop'):
                    answer_type = 'chart'
                    base64_image_string = self._convert_image_to_base64(os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts', 'temp_chart.png'))
                    html_img = f'<img src="data:image/png;base64,{base64_image_string}"/>'
                    special_message = html_img
                    answer = 'See chart...'
            elif str(output_type).lower().__contains__("dataframe"):
                answer_type = 'dataframe'
            elif str(output_type).lower().__contains__("chart") or str(answer).endswith('.png') or str(output_type).lower().__contains__('plot'):
                answer_type = 'chart'
                base64_image_string = self._convert_image_to_base64(answer)
                html_img = f'<img src="data:image/png;base64,{base64_image_string}"/>'
                special_message = html_img
                answer = 'See chart...'
            elif str(output_type).lower() == 'error':
                answer_type = 'error'
            else:
                answer_type = 'unknown'

            logger.debug(f'Answer type: {answer_type}, Special message: {special_message}')
            print(f'Answer type: {answer_type}, Special message: {special_message}')
        except Exception as e:
            logger.error(f'Error getting pandas agent answer: {e}')
            print(f'Error getting pandas agent answer: {e}')
            special_message = str(e)
            answer_type = 'error'
        return answer, explain, clarify, answer_type, special_message, input_question

    def _attempt_code_regen(self):
        """
        Attempt to regenerate and execute code when PandasAI fails.
        Includes fixes for:
        - dfs list properly passed (not wrapped in extra list)
        - Proper markdown/language identifier cleaning
        - Matplotlib support for chart generation
        """
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend for server-side rendering
        import matplotlib.pyplot as plt
        import numpy as np
        
        try:
            IS_ERROR = False
            result_df = None
            regen_system = cfg.LLM_PANDAS_CODE_REGEN_SYSTEM.replace("\\n", "\n")
            
            # Build the base regen prompt
            base_prompt = cfg.LLM_PANDAS_CODE_REGEN_PROMPT.replace("\\n", "\n").replace(
                '{question}', self.environment.current_input_question or ''
            ).replace(
                '{query}', self.environment.current_query or ''
            ).replace(
                '{dataframe}', 'dfs[-1]'
            )
            
            # Add critical context about the data
            context_instructions = """

CRITICAL CONTEXT:
- The SQL query shown above has ALREADY been adjusted to reflect the user's request
- The dataframe (dfs[-1]) contains the RESULTS of that SQL query
- Do NOT try to re-filter or re-aggregate the data for date ranges - the SQL already did that
- Your job is to FORMAT, TRANSFORM, or VISUALIZE the data that is already in the dataframe
- The dataframe columns are: """ + str(list(self.environment.dfs[-1].columns) if self.environment.dfs and len(self.environment.dfs) > 0 and self.environment.dfs[-1] is not None else []) + """
- Work with the data AS-IS - it already reflects what the user asked for
"""
            regen_prompt = base_prompt + context_instructions
            
            # Check if this is a visualization request and add specific instructions
            is_viz_request, viz_type = self._is_visualization_request(
                self.environment.current_input_question or ''
            )
            if is_viz_request:
                chart_instructions = """

IMPORTANT CHART GENERATION INSTRUCTIONS:
- You MUST generate a chart/visualization as requested
- Use matplotlib.pyplot to create the chart
- Do NOT use plt.show() - the chart will be captured automatically
- Do NOT save the chart yourself - just create the figure
- Available: import matplotlib.pyplot as plt
- Handle NaN values by filtering them out before plotting
- Set appropriate figure size with plt.figure(figsize=(10, 6))
"""
                regen_prompt = regen_prompt + chart_instructions
            
            logger.debug(f'Regen system: {regen_system}')
            logger.debug(f'Regen prompt: {regen_prompt}')
            print(f'Regen system: {regen_system}')
            print(f'Regen prompt: {regen_prompt}')
            code_string = azureQuickPrompt(prompt=regen_prompt, system=regen_system, use_alternate_api=True, provider=self.provider)
            
            # CRITICAL FIX: Clean the generated code to remove markdown artifacts
            code_string = self._clean_generated_code(code_string)
            
            logger.debug(f'New code generated (cleaned): {code_string}')
            print(f'New code generated (cleaned): {code_string}')
            
            # CRITICAL FIX: Don't wrap dfs in another list!
            # self.environment.dfs is already a list of DataFrames
            local_scope = {
                'dfs': self.environment.dfs,  # FIX: Removed the extra list wrapper [...]
                'pd': pd,
                'plt': plt,
                'np': np,
                'matplotlib': matplotlib,
                'os': os,
            }
            
            # Create exports directory for charts if needed
            chart_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
            os.makedirs(chart_dir, exist_ok=True)

            exec(code_string, {}, local_scope)
            
            # Check for result_df in scope
            if 'result_df' in local_scope:
                result_df = local_scope['result_df']
            
            # Check if a chart was generated and save it
            # CRITICAL: Check for figures BEFORE they might be closed
            if plt.get_fignums():
                chart_path = os.path.join(chart_dir, 'temp_chart.png')
                plt.savefig(chart_path, dpi=100, bbox_inches='tight')
                plt.close('all')
                # ALWAYS return chart path when a chart is generated
                # User asked for visualization, so that's what they should get
                result_df = chart_path
                logger.debug(f'Chart saved to: {chart_path}')
                print(f'Chart saved to: {chart_path}')
            else:
                # No figure in memory - check if chart file was created by the code
                # (e.g., if code did plt.savefig() then plt.close())
                chart_path = os.path.join(chart_dir, 'temp_chart.png')
                if os.path.exists(chart_path):
                    # Check if file was modified recently (within last 10 seconds)
                    import time as time_module
                    file_mtime = os.path.getmtime(chart_path)
                    if time_module.time() - file_mtime < 10:
                        result_df = chart_path
                        logger.debug(f'Chart detected from file: {chart_path}')
                        print(f'Chart detected from file: {chart_path}')
                
            logger.debug('New code executed successfully.')
            print('New code executed successfully.')
        except Exception as e:
            IS_ERROR = True
            result_df = None
            logger.error(f'Error executing new code: {e}')
            print(f'Error executing new code: {e}')
            plt.close('all')  # Clean up any partial figures
        return IS_ERROR, result_df
    
    def _clean_generated_code(self, code_string):
        """
        Clean generated code by removing markdown artifacts and language identifiers.
        
        Handles cases like:
        - ```python\\ncode...\\n```
        - python\\nimport pandas...
        - Leading/trailing whitespace
        - plt.show() calls (unnecessary on server)
        """
        import re
        
        code = str(code_string).strip()
        
        # Remove markdown code fences with language identifier
        code = re.sub(r'^```\w*\n?', '', code)  # Opening fence like ```python
        code = re.sub(r'\n?```$', '', code)     # Closing fence
        
        # Remove standalone language identifier on first line (e.g., "python\n")
        lines = code.split('\n')
        if lines and lines[0].strip().lower() in ['python', 'python3', 'py', 'sql']:
            lines = lines[1:]
            code = '\n'.join(lines)
        
        # Clean any remaining triple backticks
        code = code.replace('```', '')
        
        # Remove plt.show() calls - they're unnecessary on server and can cause issues
        code = re.sub(r'\n?\s*plt\.show\(\)\s*', '\n', code)
        
        return code.strip()
    
    def _is_visualization_request(self, input_question):
        """
        Detect if the user is requesting a visualization change or chart generation.
        
        Returns:
            tuple: (is_viz_request: bool, viz_type: str or None)
        """
        import re
        
        if not input_question:
            return False, None
            
        question_lower = input_question.lower()
        
        # Chart type patterns
        chart_patterns = {
            'pie': r'\b(pie\s*chart|pie\s*graph|as\s+a?\s*pie)\b',
            'bar': r'\b(bar\s*chart|bar\s*graph|as\s+a?\s*bar|barchart)\b',
            'line': r'\b(line\s*chart|line\s*graph|as\s+a?\s*line|trend\s*line)\b',
            'scatter': r'\b(scatter\s*plot|scatter\s*chart|scatter\s*graph)\b',
            'histogram': r'\b(histogram|hist)\b',
            'area': r'\b(area\s*chart|area\s*graph)\b',
            'heatmap': r'\b(heat\s*map|heatmap)\b',
        }
        
        # Generic visualization request patterns
        viz_request_patterns = [
            r'\b(show|display|visualize|plot|graph|chart)\s+(this|it|the\s+data)',
            r'\b(as\s+a\s+(chart|graph|plot|visualization))\b',
            r'\b(make\s+(it|this)\s+a)\b',
            r'\b(convert\s+to\s+a?)\b.*\b(chart|graph|plot)\b',
            r'\b(can\s+you\s+(show|display|make|create))\b.*\b(chart|graph|plot)\b',
        ]
        
        # Check for specific chart type
        for viz_type, pattern in chart_patterns.items():
            if re.search(pattern, question_lower):
                return True, viz_type
        
        # Check for generic visualization request
        for pattern in viz_request_patterns:
            if re.search(pattern, question_lower):
                return True, 'auto'
        
        return False, None

    def _classify_chart_request(self, input_question, is_follow_up=False):
        """
        Use the mini LLM to classify whether a request is for a chart and determine
        the chart parameters (type, columns, title).

        Returns:
            tuple: (is_chart: bool, chart_spec: dict or None)
        """
        try:
            df = self.environment.dfs[-1] if self.environment.dfs else None
            if df is None or len(df) == 0:
                return False, None

            # Build context about the DataFrame
            col_info = df.dtypes.to_string()
            sample_data = df.head(3).to_string()
            sql_query = getattr(self.environment, 'current_query', '') or ''
            prev_question = ''
            if is_follow_up and hasattr(self.environment, 'previous_input_question') and self.environment.previous_input_question:
                prev_question = f"\nPrevious question: {self.environment.previous_input_question}"

            system_prompt = "You are a chart classification assistant. Return ONLY valid JSON, no other text."

            user_prompt = f"""Given a user question and a DataFrame, determine if the user wants a chart/visualization.

User question: {input_question}{prev_question}

DataFrame columns and types:
{col_info}

Sample data (first 3 rows):
{sample_data}

SQL query that produced this data:
{sql_query}

If the user is NOT requesting a chart/visualization, return: {{"is_chart": false}}

If the user IS requesting a chart/visualization, return:
{{
  "is_chart": true,
  "chart_type": "<bar|pie|line|scatter|histogram|area|stacked_bar>",
  "x_column": "<column name for x-axis/labels>",
  "y_column": "<column name for y-axis/values>",
  "title": "<descriptive chart title>"
}}

Rules:
- Choose x_column and y_column based on what the user is asking about, not column position
- For "sales by month", x=month name column, y=sales column
- For pie charts, x=category/label column, y=value column
- If the user says "yes" or agrees to a previous suggestion to generate a chart, treat it as a chart request
- If unsure about chart_type, default to "bar"
- title should describe the data meaningfully, not just "Bar Chart"
- Column names must exactly match the DataFrame columns listed above"""

            response = azureMiniQuickPrompt(
                prompt=user_prompt,
                system=system_prompt,
                temp=0.0,
                provider=self.provider
            )

            logger.debug(f'Chart classification response: {response}')
            print(f'Chart classification response: {response}')

            # Parse JSON response
            spec = json.loads(response.strip())

            if not spec.get('is_chart', False):
                return False, None

            # Validate that referenced columns exist in the DataFrame
            columns = df.columns.tolist()
            x_col = spec.get('x_column', '')
            y_col = spec.get('y_column', '')

            if x_col and x_col not in columns:
                logger.warning(f'LLM chart spec referenced non-existent x_column: {x_col}')
                return False, None
            if y_col and y_col not in columns:
                logger.warning(f'LLM chart spec referenced non-existent y_column: {y_col}')
                return False, None

            logger.info(f'LLM classified as chart request: type={spec.get("chart_type")}, x={x_col}, y={y_col}')
            print(f'LLM classified as chart request: type={spec.get("chart_type")}, x={x_col}, y={y_col}')
            return True, spec

        except json.JSONDecodeError as e:
            logger.warning(f'Failed to parse chart classification JSON: {e}')
            print(f'Failed to parse chart classification JSON: {e}')
            return False, None
        except Exception as e:
            logger.error(f'Error in chart classification: {e}')
            print(f'Error in chart classification: {e}')
            return False, None

    def _render_chart_from_spec(self, df, chart_spec):
        """
        Render a matplotlib chart based on a structured spec from the LLM.

        Args:
            df: DataFrame with data
            chart_spec: dict with chart_type, x_column, y_column, title

        Returns:
            str: Path to saved chart image, or None on failure
        """
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        try:
            if df is None or len(df) == 0:
                logger.warning('Cannot render chart from spec: DataFrame is empty')
                return None

            chart_type = chart_spec.get('chart_type', 'bar')
            x_col = chart_spec.get('x_column')
            y_col = chart_spec.get('y_column')
            title = chart_spec.get('title', '')

            # Validate columns exist
            if x_col and x_col not in df.columns:
                logger.warning(f'x_column "{x_col}" not found in DataFrame')
                return None
            if y_col and y_col not in df.columns:
                logger.warning(f'y_column "{y_col}" not found in DataFrame')
                return None

            # Get data, dropping NaN values from the value column
            if y_col:
                df_clean = df.dropna(subset=[y_col])
            else:
                df_clean = df.dropna()

            if len(df_clean) == 0:
                logger.warning('Cannot render chart: All data rows have NaN values')
                return None

            # Extract labels and values
            if x_col:
                labels = df_clean[x_col].tolist()
            else:
                labels = df_clean.index.tolist()

            if y_col:
                values = df_clean[y_col].tolist()
            else:
                # Fall back to first numeric column
                numeric_cols = df_clean.select_dtypes(include=['number']).columns.tolist()
                if not numeric_cols:
                    logger.warning('Cannot render chart: No numeric columns found')
                    return None
                y_col = numeric_cols[-1]
                values = df_clean[y_col].tolist()

            # Create the chart
            fig, ax = plt.subplots(figsize=(10, 6))

            if chart_type == 'pie':
                filtered_data = [(l, v) for l, v in zip(labels, values) if v and v > 0]
                if not filtered_data:
                    logger.warning('Cannot generate pie chart: No positive values')
                    plt.close('all')
                    return None
                pie_labels, pie_values = zip(*filtered_data)
                ax.pie(pie_values, labels=pie_labels, autopct='%1.1f%%', startangle=90)
                ax.axis('equal')

            elif chart_type in ('bar', 'stacked_bar'):
                ax.bar(range(len(labels)), values, color='steelblue')
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(y_col.replace('_', ' ').title() if y_col else '')

            elif chart_type == 'line':
                ax.plot(range(len(labels)), values, marker='o', linewidth=2, markersize=6)
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(y_col.replace('_', ' ').title() if y_col else '')
                ax.grid(True, alpha=0.3)

            elif chart_type == 'scatter':
                numeric_cols = df_clean.select_dtypes(include=['number']).columns.tolist()
                if len(numeric_cols) >= 2:
                    ax.scatter(df_clean[numeric_cols[0]], df_clean[numeric_cols[1]], alpha=0.6)
                    ax.set_xlabel(numeric_cols[0].replace('_', ' ').title())
                    ax.set_ylabel(numeric_cols[1].replace('_', ' ').title())
                else:
                    ax.bar(range(len(labels)), values, color='steelblue')
                    ax.set_xticks(range(len(labels)))
                    ax.set_xticklabels(labels, rotation=45, ha='right')
                    ax.set_ylabel(y_col.replace('_', ' ').title() if y_col else '')

            elif chart_type == 'histogram':
                ax.hist(values, bins='auto', color='steelblue', edgecolor='white')
                ax.set_xlabel(y_col.replace('_', ' ').title() if y_col else '')
                ax.set_ylabel('Frequency')

            elif chart_type == 'area':
                ax.fill_between(range(len(labels)), values, alpha=0.4, color='steelblue')
                ax.plot(range(len(labels)), values, linewidth=2, color='steelblue')
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(y_col.replace('_', ' ').title() if y_col else '')

            else:
                # Default to bar chart for unknown types
                ax.bar(range(len(labels)), values, color='steelblue')
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(y_col.replace('_', ' ').title() if y_col else '')

            # Set title
            if title:
                ax.set_title(title)
            elif y_col and x_col:
                ax.set_title(f"{y_col.replace('_', ' ').title()} by {x_col.replace('_', ' ').title()}")

            plt.tight_layout()

            # Save the chart
            chart_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
            os.makedirs(chart_dir, exist_ok=True)
            chart_path = os.path.join(chart_dir, 'temp_chart.png')
            plt.savefig(chart_path, dpi=100, bbox_inches='tight')
            plt.close('all')

            logger.info(f'LLM spec chart generated: {chart_path}')
            print(f'LLM spec chart generated: {chart_path}')
            return chart_path

        except Exception as e:
            logger.error(f'Error rendering chart from spec: {e}')
            print(f'Error rendering chart from spec: {e}')
            plt.close('all')
            return None

    def _generate_chart_fallback(self, df, chart_type, title=None):
        """
        Generate a chart as a fallback when PandasAI chart generation fails.
        
        Args:
            df: DataFrame with data to plot
            chart_type: Type of chart ('pie', 'bar', 'line', 'scatter', 'auto')
            title: Optional title for the chart
            
        Returns:
            str: Path to the generated chart image, or None if generation fails
        """
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        try:
            # Prepare the data
            if df is None or len(df) == 0:
                logger.warning('Cannot generate chart: DataFrame is empty')
                return None
                
            # Drop any rows with NaN in numeric columns for charting
            numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
            if not numeric_cols:
                logger.warning('Cannot generate chart: No numeric columns found')
                return None
                
            df_clean = df.dropna(subset=numeric_cols)
            
            if len(df_clean) == 0:
                logger.warning('Cannot generate chart: All data rows have NaN values')
                return None
            
            # Identify label and value columns
            non_numeric_cols = df.select_dtypes(exclude=['number']).columns.tolist()
            label_col = non_numeric_cols[0] if non_numeric_cols else None

            # Pick the best value column: prefer the last numeric column (typically the
            # aggregate/calculated metric like total_sales) over ordinal columns like
            # month_number that happen to appear first. If there's only one numeric
            # column, use it directly.
            if len(numeric_cols) > 1 and label_col:
                # Skip numeric columns that look like row-number or ordinal identifiers
                # (e.g., month_actual, quarter_number) — use the last numeric column
                # which is most likely the measure/value being reported.
                value_col = numeric_cols[-1]
            else:
                value_col = numeric_cols[0]
            
            # Get labels
            if label_col:
                labels = df_clean[label_col].tolist()
            else:
                labels = df_clean.index.tolist()
            values = df_clean[value_col].tolist()
            
            # Auto-detect best chart type if 'auto'
            if chart_type == 'auto':
                num_categories = len(labels)
                if num_categories <= 8:
                    chart_type = 'pie'
                elif num_categories <= 20:
                    chart_type = 'bar'
                else:
                    chart_type = 'line'
            
            # Create the chart
            fig, ax = plt.subplots(figsize=(10, 6))
            
            if chart_type == 'pie':
                # Filter out zero or negative values for pie chart
                filtered_data = [(l, v) for l, v in zip(labels, values) if v and v > 0]
                if not filtered_data:
                    logger.warning('Cannot generate pie chart: No positive values')
                    plt.close('all')
                    return None
                labels, values = zip(*filtered_data)
                ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=90)
                ax.axis('equal')
                
            elif chart_type == 'bar':
                ax.bar(range(len(labels)), values, color='steelblue')
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(value_col)
                
            elif chart_type == 'line':
                ax.plot(range(len(labels)), values, marker='o', linewidth=2, markersize=6)
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=45, ha='right')
                ax.set_ylabel(value_col)
                ax.grid(True, alpha=0.3)
                
            elif chart_type == 'scatter':
                if len(numeric_cols) >= 2:
                    ax.scatter(df_clean[numeric_cols[0]], df_clean[numeric_cols[1]], alpha=0.6)
                    ax.set_xlabel(numeric_cols[0])
                    ax.set_ylabel(numeric_cols[1])
                else:
                    # Fall back to bar if not enough numeric columns
                    plt.close('all')
                    return self._generate_chart_fallback(df, 'bar', title)
            
            # Generate a smart title
            generated_title = self._generate_chart_title(title, value_col, label_col, chart_type)
            ax.set_title(generated_title)
            
            plt.tight_layout()
            
            # Save the chart
            chart_dir = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'exports', 'charts')
            os.makedirs(chart_dir, exist_ok=True)
            chart_path = os.path.join(chart_dir, 'temp_chart.png')
            plt.savefig(chart_path, dpi=100, bbox_inches='tight')
            plt.close('all')
            
            logger.info(f'Fallback chart generated: {chart_path}')
            print(f'Fallback chart generated: {chart_path}')
            return chart_path
            
        except Exception as e:
            logger.error(f'Error generating fallback chart: {e}')
            print(f'Error generating fallback chart: {e}')
            plt.close('all')
            return None
    
    def _generate_chart_title(self, user_input, value_col, label_col, chart_type):
        """
        Generate a meaningful chart title.
        
        If user_input contains descriptive content, extract it.
        Otherwise, auto-generate based on the data columns.
        
        Args:
            user_input: The user's input question (may be None or a visualization request)
            value_col: The name of the value/numeric column
            label_col: The name of the label/category column (may be None)
            chart_type: The type of chart being generated
            
        Returns:
            str: A meaningful chart title
        """
        # Phrases that indicate a visualization-only request (no meaningful title content)
        viz_only_phrases = [
            'as a bar chart', 'as a pie chart', 'as a line chart', 'as a graph',
            'as a scatter', 'as a histogram', 'instead of a table', 'show as',
            'display as', 'make it a', 'change to', 'convert to', 'visualize',
            'i want this as', 'can you show this as', 'show this as'
        ]
        
        if user_input:
            user_lower = user_input.lower().strip()
            
            # Check if the input is just a visualization request
            is_viz_only = any(phrase in user_lower for phrase in viz_only_phrases)
            
            # If the input is short and just a viz request, don't use it
            if is_viz_only and len(user_lower) < 60:
                user_input = None
            else:
                # Try to extract meaningful content by removing viz-related phrases
                cleaned = user_lower
                for phrase in viz_only_phrases:
                    cleaned = cleaned.replace(phrase, '')
                cleaned = cleaned.strip()
                
                # If there's substantial content left, use it (but clean it up)
                if len(cleaned) > 10 and not cleaned.startswith(('i want', 'can you', 'please', 'show me')):
                    # Capitalize first letter of each word for title case
                    user_input = cleaned.title()
                else:
                    user_input = None
        
        # If we have a valid user input, use it
        if user_input and len(user_input) > 5:
            return user_input
        
        # Auto-generate based on columns
        # Clean up column names for display (replace underscores, title case)
        def clean_col_name(col):
            if col is None:
                return "Values"
            return col.replace('_', ' ').title()
        
        value_display = clean_col_name(value_col)
        label_display = clean_col_name(label_col) if label_col else "Category"
        
        return f'{value_display} by {label_display}'

    def _preprocess_pandas_input_question_legacy(self, input_question):
        #input_question = input_question + ' IMPORTANT: ' + 'Base all time references such as "this year", "prior month", etc. on the current date, which is: ' + str(time.strftime("%Y-%m-%d")) + ' - Additional instructions: ' + cfg.LLM_LOGICAL_QUERY_INSTRUCTION
        input_question = (
            input_question
            + ' IMPORTANT: '
            + 'Base all time references such as "this year", "prior month", etc. on the current date, which is: '
            + str(time.strftime("%Y-%m-%d"))
            + cfg.LLM_LOGICAL_QUERY_INSTRUCTION
        )
        logger.debug(f'Preprocessed input question: {input_question}')
        print(f'Preprocessed input question: {input_question}')
        return input_question
    
    def _preprocess_pandas_input_question(self, input_question, formatting_requirements=None):
        """
        Preprocess the input question before sending to PandasAI.
        
        Args:
            input_question: The user's original question
            formatting_requirements: Dict with formatting info from data dictionary
        
        Returns:
            Enhanced question with time context and formatting instructions
        """
        input_question = (
            input_question
            + '\n\n IMPORTANT: '
            + 'Base all time references such as "this year", "prior month", etc. on the current date, which is: '
            + str(time.strftime("%Y-%m-%d"))
            + '\n\n'
            + cfg.LLM_LOGICAL_QUERY_INSTRUCTION
        )
        
        # Add formatting instructions if available
        if formatting_requirements and hasattr(formatting_requirements, 'columns'):  # From older method
            columns_to_format = formatting_requirements.get('columns', [])
            formats_dict = formatting_requirements.get('formats', {})
            
            if columns_to_format:
                input_question += "\n\n=== CRITICAL FORMATTING REQUIREMENTS ==="
                input_question += "\nThe following columns MUST be formatted in your output:"
                
                for col in columns_to_format:
                    col_format = formats_dict.get(col, 'unknown')
                    
                    if col_format == 'currency':
                        input_question += f"\n- Column '{col}': Format as CURRENCY with dollar sign ($) and comma separators (e.g., $1,234.56)"
                    elif col_format == 'percentage':
                        input_question += f"\n- Column '{col}': Format as PERCENTAGE with percent sign (e.g., 45.2%)"
                    elif 'decimal' in str(col_format).lower():
                        import re
                        precision_match = re.search(r'decimal\\((\\d+)\\)', str(col_format))
                        precision = precision_match.group(1) if precision_match else '2'
                        input_question += f"\n- Column '{col}': Format as DECIMAL with {precision} decimal places and comma separators"
                    elif col_format == 'date':
                        input_question += f"\n- Column '{col}': Format as human-readable DATE (e.g., 'Jan 15, 2025')"
                    elif col_format == 'number':
                        input_question += f"\n- Column '{col}': Format as NUMBER with comma separators (e.g., 1,000,000)"
                    else:
                        input_question += f"\\n- Column '{col}': Apply appropriate formatting based on type ({col_format})"
                
                input_question += "\n\nThese formatting requirements are MANDATORY and must be applied to all relevant columns in your response."
                
                logger.info(f'Added formatting instructions for columns: {columns_to_format}')
                print(f'Added formatting instructions for columns: {columns_to_format}')
        elif formatting_requirements:                                               # From new method (already formatted for prompt)
            input_question += "\n\n=== CRITICAL FORMATTING REQUIREMENTS ===\n"
            input_question += formatting_requirements
            input_question += "\n\nThese formatting requirements are MANDATORY and must be applied to all relevant columns in your response."
            logger.info(f'Added formatting instructions: {formatting_requirements}')
            print(f'Added formatting instructions: {formatting_requirements}')

        logger.debug(f'Preprocessed input question: {input_question}')
        print(f'Preprocessed input question: {input_question}')
        return input_question
    
    def _clean_llm_result(self, result):
        return str(result).replace('```json', '').replace('```python', '').replace('```sql', '').replace('```', '###')

    def _is_analytical_query_required(self):
        try:
            IS_REQUIRED = True
            analytic_system = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_SYSTEM
            analytic_prompt = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_PROMPT.replace('{question}', self.environment.current_input_question).replace('{query}', self.environment.current_query).replace('{dataset}', self.environment.dfs[-1].head(5).to_string(index=False))
            logger.debug(f'Analytical check system: {analytic_system}')
            logger.debug(f'Analytical check prompt: {analytic_prompt}')
            print(f'Analytical check system: {analytic_system}')
            print(f'Analytical check prompt: {analytic_prompt}')
            results = azureQuickPrompt(prompt=analytic_prompt, system=analytic_system, use_alternate_api=True, provider=self.provider)
            results = results.replace("```json", '').replace("```", '')
            logger.debug(f'Analytical check results: {results}')
            print(f'Analytical check results: {results}')
            results = json.loads(results)
            final_decision = str(results["FinalDecision"]["Answer"]).lower()
            logger.debug(f'Final decision: {final_decision}')
            print(f'Final decision: {final_decision}')
            if final_decision == 'yes':
                IS_REQUIRED = False
            else:
                IS_REQUIRED = True
        except Exception as e:
            IS_REQUIRED = True
            logger.error(f'Error checking if analytical query is required: {e}')
            print(f'Error checking if analytical query is required: {e}')
        return IS_REQUIRED, results

    def _is_analytical_query_required_v2(self):
        try:
            IS_REQUIRED = True
            explanation = ''
            confidence = '100'

            # If the last input from user was a reply to a request for more information, add the recent conversation history for context
            print(f'Last Answer Requested More Info: {self.environment.last_answer_requested_more_info}')
            conversation_history = ''
            if self.environment.last_answer_requested_more_info:
                conversation_history += '1. user: ' + self.environment.previous_input_question + '\n'
                conversation_history += '2. assistant: ' + self.environment.last_answer_requested_more_info_message + '\n'

            if self.environment.is_response:
                conversation_history = ''
                conversation_history += '1. user: ' + self.environment.previous_input_question + '\n'
                conversation_history += '2. assistant: ' + self.environment.last_answer_requested_more_info_message + '\n'

            analytic_system = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_SYSTEM
            analytic_prompt = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_PROMPT_V2.replace('{conversation_history}', conversation_history).replace('{question}', self.environment.current_input_question).replace('{query}', self.environment.current_query).replace('{dataset}', self.environment.dfs[-1].head(5).to_string(index=False))
            logger.debug(f'Analytical check system (v2): {analytic_system}')
            logger.debug(f'Analytical check prompt (v2): {analytic_prompt}')
            print(f'Analytical check system (v2): {analytic_system}')
            print(f'Analytical check prompt (v2): {analytic_prompt}')
            results = azureQuickPrompt(prompt=analytic_prompt, system=analytic_system, use_alternate_api=True, provider=self.provider)
            print(f'Analytical check results before clean (v2): {results}')

            # Clean results
            results = self._clean_llm_result(results)
            print(f'Analytical check results after clean (v2): {results}')

            # Process JSON
            try:
                logger.debug(f'Analytical check results (v2): {results}')
                print(f'Analytical check results (v2): {results}')

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
                print("Dataset is Sufficient:", dataset_is_sufficient)
                print("Explanation:", explanation)
                print("Confidence:", str(confidence))

                if str(dataset_is_sufficient).lower() == 'yes':
                    IS_REQUIRED = False   # If dataset is sufficient==yes, analytical query is not required
                else:
                    IS_REQUIRED = True
            except Exception as e:
                print('_is_analytical_query_required_v2 - error processing response:', str(e))
                logger.error('_is_analytical_query_required_v2 - error processing response: ' + str(e))
        except Exception as e:
            IS_REQUIRED = True
            results = str(e)
            logger.error(f'Error checking if analytical query is required (v2): {e}')
            print(f'Error checking if analytical query is required (v2): {e}')
        return IS_REQUIRED, confidence, explanation
    
    def _get_column_formatting_info(self, table_names=None):
        """
        Extract column formatting information from the data dictionary for columns in the current dataset.
        
        Args:
            table_names: Optional list of table names. If None, will attempt to extract from environment.
        
        Returns:
            str: Formatted string describing column formatting requirements, or empty string if none
        """
        try:
            # Import here to avoid circular dependency
            from DataUtils import get_enhanced_column_metadata_as_yaml
            
            # Get the current dataset columns
            if self.environment.df is None or self.environment.df.empty:
                return "No dataset available to check formatting requirements.", None
            
            dataset_columns = list(self.environment.df.columns)
            
            # Try to get table names from the current query or environment
            if table_names is None:
                # Attempt to extract table names from query (basic heuristic)
                if self.environment.current_query:
                    query_upper = self.environment.current_query.upper()
                    # Look for FROM and JOIN clauses
                    import re
                    # Updated patterns to capture full schema.table names as single units
                    # Matches: [schema].[table], [schema].table, schema.[table], schema.table, [table], or table
                    from_pattern = r'FROM\s+((?:\[?\w+\]?\.)?(?:\[?\w+\]?))'
                    join_pattern = r'JOIN\s+((?:\[?\w+\]?\.)?(?:\[?\w+\]?))'
                    
                    from_matches = re.findall(from_pattern, query_upper)
                    join_matches = re.findall(join_pattern, query_upper)
                    
                    # Combine all matches
                    all_matches = from_matches + join_matches
                    
                    # Clean up table names (remove brackets but keep schema.table format)
                    cleaned_names = []
                    for name in all_matches:
                        # Remove square brackets but preserve the schema.table structure
                        cleaned = name.replace('[', '').replace(']', '').strip()
                        if cleaned:
                            cleaned_names.append(cleaned)
                    
                    # Remove duplicates
                    table_names = list(set(cleaned_names))
            
            if not table_names:
                # If we still don't have table names, we can't get metadata
                print("No table names available for formatting info extraction")
                logger.debug("No table names available for formatting info extraction")
                return "Column formatting information not available (unable to determine source tables).", None
            
            # Get the enhanced column metadata
            connection_id = self.environment.current_connection_id
            if connection_id is None:
                print("No connection_id available for formatting info")
                logger.debug("No connection_id available for formatting info")
                return "Column formatting information not available (no connection context).", None
            
            logger.debug(f"Getting column metadata for connection {connection_id} tables {table_names}.")
            column_metadata_yaml = get_enhanced_column_metadata_as_yaml(table_names, connection_id)

            # Also fetch calculated metrics (is_calculated=1) which are excluded from column metadata
            calculated_metrics_yaml = get_calculated_metrics_as_yaml(connection_id)
            if calculated_metrics_yaml:
                column_metadata_yaml = (column_metadata_yaml or "") + "\n" + calculated_metrics_yaml

            if not column_metadata_yaml or column_metadata_yaml.strip() == "":
                return "No specific formatting requirements found in data dictionary.", column_metadata_yaml

            # Parse the YAML to extract formatting info for columns in our dataset
            formatting_info = self._parse_formatting_from_metadata(column_metadata_yaml, dataset_columns, None)

            return formatting_info, column_metadata_yaml

        except Exception as e:
            print(f"Error getting column formatting info: {str(e)}")
            logger.error(f"Error getting column formatting info: {str(e)}")
            return "Error retrieving column formatting information.", None

    def _get_column_formatting_info_broke(self, table_names=None):
        """
        Extract column formatting information from the data dictionary for columns in the current dataset.
        
        Args:
            table_names: Optional list of table names. If None, will attempt to extract from environment.
        
        Returns:
            str: Formatted string describing column formatting requirements, or empty string if none
        """
        try:
            # Import here to avoid circular dependency
            from DataUtils import get_enhanced_column_metadata_as_yaml
            
            # Get the current dataset columns
            if self.environment.df is None or self.environment.df.empty:
                return "No dataset available to check formatting requirements.", None
            
            dataset_columns = list(self.environment.df.columns)
            
            # Try to get table names from the current query or environment
            if table_names is None:
                # Attempt to extract table names from query (basic heuristic)
                if self.environment.current_query:
                    query_upper = self.environment.current_query.upper()
                    # Look for FROM and JOIN clauses
                    import re
                    from_pattern = r'FROM\s+(\w+)'
                    join_pattern = r'JOIN\s+(\w+)'
                    from_matches = re.findall(from_pattern, query_upper)
                    join_matches = re.findall(join_pattern, query_upper)
                    table_names = list(set(from_matches + join_matches))
                    
                    # Clean up table names (remove brackets, quotes, etc.)
                    table_names = [t.strip('[]"\'') for t in table_names]
            
            if not table_names:
                # If we still don't have table names, we can't get metadata
                print("No table names available for formatting info extraction")
                logger.debug("No table names available for formatting info extraction")
                return "Column formatting information not available (unable to determine source tables).", None
            
            # Get the enhanced column metadata
            connection_id = self.environment.current_connection_id
            if connection_id is None:
                print("No connection_id available for formatting info")
                logger.debug("No connection_id available for formatting info")
                return "Column formatting information not available (no connection context).", None
            
            logger.debug(f"Getting column metadata for connection {connection_id} tables {table_names}.")
            column_metadata_yaml = get_enhanced_column_metadata_as_yaml(table_names, connection_id)
            
            if not column_metadata_yaml or column_metadata_yaml.strip() == "":
                return "No specific formatting requirements found in data dictionary.", column_metadata_yaml
            
            # Parse the YAML to extract formatting info for columns in our dataset
            formatting_info = self._parse_formatting_from_metadata(column_metadata_yaml, dataset_columns, None)
            
            return formatting_info, column_metadata_yaml
            
        except Exception as e:
            print(f"Error getting column formatting info: {str(e)}")
            logger.error(f"Error getting column formatting info: {str(e)}")
            return "Error retrieving column formatting information.", None


    def _generate_column_mapping_via_ai(self, dataset_columns, column_metadata_yaml):
        """
        Use a mini LLM call to map dataset columns to their source columns/metrics
        defined in the YAML metadata.

        Args:
            dataset_columns: List of column names in the current dataset
            column_metadata_yaml: YAML string with column metadata

        Returns:
            dict: Mapping of {dataset_column: source_column_or_metric_name}, or None on failure
        """
        try:
            import yaml

            # Extract source column names and metric names from the YAML
            source_names = []

            column_section = column_metadata_yaml
            metrics_section = None
            if "# CALCULATED METRICS" in column_metadata_yaml:
                parts = column_metadata_yaml.split("# CALCULATED METRICS")
                column_section = parts[0]
                metrics_section = "# CALCULATED METRICS" + parts[1]

            metadata = yaml.safe_load(column_section) if column_section.strip() else None
            metrics_metadata = yaml.safe_load(metrics_section) if metrics_section else None

            if metadata and 'tables' in metadata:
                for table_name, table_data in metadata.get('tables', {}).items():
                    for col in table_data.get('columns', []):
                        name = col.get('name', '')
                        if name:
                            source_names.append(name)

            if metrics_metadata and 'metrics' in metrics_metadata:
                for metric in metrics_metadata.get('metrics', []):
                    name = metric.get('name', '')
                    if name:
                        source_names.append(f"{name} (calculated metric)")

            if not source_names:
                return None

            prompt = (
                f"Map each result column to its most likely source column or metric. "
                f"Result columns: {dataset_columns}. "
                f"Source columns/metrics: {source_names}. "
                f"Return ONLY a JSON object mapping each result column to the source column or metric name "
                f"(without any suffix like '(calculated metric)'). "
                f"If a result column does not match any source, map it to null. "
                f'Example: {{"total_sales_dollars": "sales_dollars", "region": "region", "total_cogs": "cogs"}}'
            )

            system = "You are a data column mapping assistant. Return only valid JSON, no explanation."

            response = azureMiniQuickPrompt(prompt, system=system, temp=0.0, provider=self.provider)

            mapping = json.loads(response)

            # Validate it is a dict with string keys
            if not isinstance(mapping, dict):
                logger.warning(f"AI column mapping returned non-dict: {type(mapping)}")
                return None

            # Filter out null values
            mapping = {k: v for k, v in mapping.items() if v is not None}

            logger.info(f"AI-generated column mapping: {mapping}")
            print(f"AI-generated column mapping: {mapping}")

            return mapping

        except Exception as e:
            logger.warning(f"AI column mapping failed: {str(e)}")
            print(f"AI column mapping failed: {str(e)}")
            return None


    def _generate_column_mapping_via_fuzzy(self, dataset_columns, column_metadata_yaml):
        """
        Use substring/containment matching to map dataset columns to source columns/metrics.

        Matching rules (applied in order, first match wins):
        1. Exact case-insensitive match
        2. One name contains the other (e.g., total_cogs contains cogs)
        3. Both normalize to the same base after stripping common prefixes/suffixes

        Args:
            dataset_columns: List of column names in the current dataset
            column_metadata_yaml: YAML string with column metadata

        Returns:
            dict: Mapping of {dataset_column: source_column_or_metric_name}
        """
        try:
            import yaml

            # Common prefixes and suffixes to strip for normalization
            STRIP_PREFIXES = ['total_', 'sum_', 'avg_', 'min_', 'max_', 'count_', 'net_', 'gross_']
            STRIP_SUFFIXES = ['_total', '_sum', '_avg', '_count', '_amount']

            def normalize(name):
                """Strip common aggregation prefixes/suffixes to get the base column name."""
                n = name.lower().strip()
                for prefix in STRIP_PREFIXES:
                    if n.startswith(prefix):
                        n = n[len(prefix):]
                        break
                for suffix in STRIP_SUFFIXES:
                    if n.endswith(suffix):
                        n = n[:-len(suffix)]
                        break
                return n

            # Extract all source names from YAML
            source_names = []

            column_section = column_metadata_yaml
            metrics_section = None
            if "# CALCULATED METRICS" in column_metadata_yaml:
                parts = column_metadata_yaml.split("# CALCULATED METRICS")
                column_section = parts[0]
                metrics_section = "# CALCULATED METRICS" + parts[1]

            metadata = yaml.safe_load(column_section) if column_section.strip() else None
            metrics_metadata = yaml.safe_load(metrics_section) if metrics_section else None

            if metadata and 'tables' in metadata:
                for table_name, table_data in metadata.get('tables', {}).items():
                    for col in table_data.get('columns', []):
                        name = col.get('name', '')
                        if name:
                            source_names.append(name)

            if metrics_metadata and 'metrics' in metrics_metadata:
                for metric in metrics_metadata.get('metrics', []):
                    name = metric.get('name', '')
                    if name:
                        source_names.append(name)

            if not source_names:
                return {}

            mapping = {}
            for dc in dataset_columns:
                dc_lower = dc.lower()
                matched = None

                # Strategy 1: Exact match
                for src in source_names:
                    if dc_lower == src.lower():
                        matched = src
                        break

                # Strategy 2: Containment (dataset col contains source name or vice versa)
                if not matched:
                    for src in source_names:
                        src_lower = src.lower()
                        if src_lower in dc_lower or dc_lower in src_lower:
                            matched = src
                            break

                # Strategy 3: Normalized base name match
                if not matched:
                    dc_normalized = normalize(dc)
                    for src in source_names:
                        if normalize(src) == dc_normalized:
                            matched = src
                            break

                if matched:
                    mapping[dc] = matched

            logger.info(f"Fuzzy column mapping: {mapping}")
            print(f"Fuzzy column mapping: {mapping}")

            return mapping

        except Exception as e:
            logger.warning(f"Fuzzy column mapping failed: {str(e)}")
            print(f"Fuzzy column mapping failed: {str(e)}")
            return {}


    def _parse_formatting_from_metadata(self, column_metadata_yaml, dataset_columns, column_source_mapping):
        """
        Parse YAML column metadata and extract formatting info for columns in the dataset.
        Uses AI-provided column mapping (much simpler than SQL parsing!).

        Args:
            column_metadata_yaml: YAML string with column metadata (may contain both
                regular column metadata and calculated metrics sections)
            dataset_columns: List of column names in the current dataset
            column_source_mapping: Dict from AI mapping dataset columns to source columns

        Returns:
            str: Formatted string describing which columns need formatting
        """
        try:
            import yaml

            # The YAML may contain multiple documents (column metadata + calculated metrics)
            # Split on the calculated metrics header and parse each section
            column_section = column_metadata_yaml
            metrics_section = None

            if "# CALCULATED METRICS" in column_metadata_yaml:
                parts = column_metadata_yaml.split("# CALCULATED METRICS")
                column_section = parts[0]
                metrics_section = "# CALCULATED METRICS" + parts[1]

            metadata = yaml.safe_load(column_section) if column_section.strip() else None
            metrics_metadata = yaml.safe_load(metrics_section) if metrics_section else None

            if not metadata and not metrics_metadata:
                return "No formatting requirements specified."

            formatting_list = []

            # If no mapping was provided, attempt to generate one based on config strategy
            if column_source_mapping is None and dataset_columns:
                strategy = getattr(cfg, 'COLUMN_FORMAT_MATCHING_STRATEGY', 'default')
                logger.info(f"No column_source_mapping provided. Using strategy: {strategy}")
                print(f"No column_source_mapping provided. Using strategy: {strategy}")

                if strategy == 'ai':
                    # Try AI mapping first, fall back to fuzzy, then default
                    column_source_mapping = self._generate_column_mapping_via_ai(dataset_columns, column_metadata_yaml)
                    if not column_source_mapping:
                        logger.info("AI mapping failed or empty, falling back to fuzzy matching")
                        print("AI mapping failed or empty, falling back to fuzzy matching")
                        column_source_mapping = self._generate_column_mapping_via_fuzzy(dataset_columns, column_metadata_yaml)
                    if not column_source_mapping:
                        column_source_mapping = None  # Fall through to default exact matching

                elif strategy == 'fuzzy':
                    column_source_mapping = self._generate_column_mapping_via_fuzzy(dataset_columns, column_metadata_yaml)
                    if not column_source_mapping:
                        column_source_mapping = None  # Fall through to default exact matching

                # strategy == 'default' or unrecognized: leave column_source_mapping as None

            print(f"Using AI-provided column mapping: {column_source_mapping}")
            logger.debug(f"Column source mapping: {column_source_mapping}")

            # Iterate through tables and columns in data dictionary
            if metadata and 'tables' in metadata:
                for table_name, table_data in metadata.get('tables', {}).items():
                    columns = table_data.get('columns', [])

                    for col in columns:
                        source_col_name = col.get('name', '')

                        # Check if any dataset column maps to this source column
                        matched_dataset_cols = []
                        if column_source_mapping:
                            for dataset_col, source_col in column_source_mapping.items():
                                if source_col and source_col.lower() == source_col_name.lower():
                                    matched_dataset_cols.append(dataset_col)
                                elif dataset_col.lower() == source_col_name.lower():
                                    matched_dataset_cols.append(dataset_col)
                        else:
                            # No mapping provided — fall back to matching dataset columns by name
                            if dataset_columns:
                                for dc in dataset_columns:
                                    if dc.lower() == source_col_name.lower():
                                        matched_dataset_cols.append(dc)

                        if not matched_dataset_cols:
                            continue  # This source column is not in the result dataset

                        # Check for formatting requirements
                        value_format = col.get('format', None)
                        semantic_type = col.get('semantic_type', None)
                        units = col.get('units', None)

                        if value_format or semantic_type or units:
                            for dataset_col in matched_dataset_cols:
                                format_desc = f"{dataset_col}"
                                format_desc += f" (source: {source_col_name}): "
                                format_parts = []

                                if value_format:
                                    format_parts.append(f"format={value_format}")
                                if semantic_type:
                                    format_parts.append(f"type={semantic_type}")
                                if units:
                                    format_parts.append(f"units={units}")

                                format_desc += ", ".join(format_parts)
                                formatting_list.append(format_desc)

                                print(f"Found formatting: {dataset_col} -> {source_col_name} -> {value_format}")
                                logger.debug(f"Formatting: {dataset_col} -> {source_col_name} -> {value_format}")

            # Also check calculated metrics for formatting requirements
            if metrics_metadata and 'metrics' in metrics_metadata:
                for metric in metrics_metadata.get('metrics', []):
                    metric_name = metric.get('name', '')

                    # Check if any dataset column maps to this calculated metric.
                    # The AI's column_source_mapping may map a dataset column to the
                    # underlying source column (e.g. cogs -> cost) rather than the metric
                    # name itself (cogs). So we need TWO matching strategies:
                    #   1. source_col matches metric_name (direct match)
                    #   2. dataset_col matches metric_name (the dataset alias IS the metric)
                    matched_dataset_cols = []
                    if column_source_mapping:
                        for dataset_col, source_col in column_source_mapping.items():
                            if source_col and source_col.lower() == metric_name.lower():
                                matched_dataset_cols.append(dataset_col)
                            elif dataset_col.lower() == metric_name.lower():
                                matched_dataset_cols.append(dataset_col)
                    else:
                        # No mapping provided — fall back to matching dataset columns by name
                        for dc in dataset_columns:
                            if dc.lower() == metric_name.lower():
                                matched_dataset_cols.append(dc)

                    if not matched_dataset_cols:
                        continue

                    value_format = metric.get('format', None)
                    semantic_type = metric.get('type', None)
                    units = metric.get('units', None)

                    if value_format or semantic_type or units:
                        for dataset_col in matched_dataset_cols:
                            format_desc = f"{dataset_col}"
                            format_desc += f" (source: {metric_name}, calculated): "
                            format_parts = []

                            if value_format:
                                format_parts.append(f"format={value_format}")
                            if semantic_type:
                                format_parts.append(f"type={semantic_type}")
                            if units:
                                format_parts.append(f"units={units}")

                            format_desc += ", ".join(format_parts)
                            formatting_list.append(format_desc)

                            print(f"Found formatting (calculated): {dataset_col} -> {metric_name} -> {value_format}")
                            logger.debug(f"Formatting (calculated): {dataset_col} -> {metric_name} -> {value_format}")

            if formatting_list:
                result = "Columns with formatting requirements:\n"
                for item in formatting_list:
                    result += f"  - {item}\n"
                return result
            else:
                return "No specific formatting requirements found for columns in this dataset."

        except Exception as e:
            print(f"Error parsing formatting metadata: {str(e)}")
            logger.error(f"Error parsing formatting metadata: {str(e)}")
            return "Error parsing formatting information."


    def _is_analytical_query_required_v3_with_formatting(self):
        """
        Enhanced version that considers column formatting requirements from the data dictionary.
        
        Returns:
            tuple: (IS_REQUIRED, confidence, explanation, formatting_required, columns_needing_formatting)
        """
        try:
            IS_REQUIRED = True
            explanation = ''
            confidence = '100'
            formatting_required = False
            formatting_requirements = None
            columns_needing_formatting = []

            # If the last input from user was a reply to a request for more information, add the recent conversation history for context
            print(f'Last Answer Requested More Info: {self.environment.last_answer_requested_more_info}')
            conversation_history = ''
            if self.environment.last_answer_requested_more_info:
                conversation_history += '1. user: ' + self.environment.previous_input_question + '\n'
                conversation_history += '2. assistant: ' + self.environment.last_answer_requested_more_info_message + '\n'

            if self.environment.is_response:
                conversation_history = ''
                conversation_history += '1. user: ' + self.environment.previous_input_question + '\n'
                conversation_history += '2. assistant: ' + self.environment.last_answer_requested_more_info_message + '\n'

            # Get column formatting information
            column_formatting_info, column_metadata_yaml = self._get_column_formatting_info()
            print(f"Column formatting info: {column_formatting_info}")
            logger.debug(f"Column formatting info: {column_formatting_info}")
            print(f"Column YAML info: {column_metadata_yaml}")
            logger.debug(f"Column YAML info: {column_metadata_yaml}")

            # Use the new prompt that includes formatting considerations
            analytic_system = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_SYSTEM_WITH_FORMATTING
            analytic_prompt = sysprompts.SYS_PROMPT_ANALYTICAL_CHECK_PROMPT_V3_WITH_FORMATTING.replace(
                '{conversation_history}', conversation_history
            ).replace(
                '{question}', self.environment.current_input_question
            ).replace(
                '{query}', self.environment.current_query
            ).replace(
                '{dataset}', self.environment.dfs[-1].head(5).to_string(index=False)
            ).replace(
                '{column_formatting_info}', column_formatting_info
            )
            
            logger.debug(f'Analytical check system (v3 with formatting): {analytic_system}')
            logger.debug(f'Analytical check prompt (v3 with formatting): {analytic_prompt}')
            print(f'Analytical check system (v3 with formatting): {analytic_system}')
            print(f'Analytical check prompt (v3 with formatting): {analytic_prompt}')
            
            results = azureQuickPrompt(prompt=analytic_prompt, system=analytic_system, use_alternate_api=True, provider=self.provider)
            print(f'Analytical check results before clean (v3 with formatting): {results}')

            # Clean results
            results = self._clean_llm_result(results)
            print(f'Analytical check results after clean (v3 with formatting): {results}')

            # Process JSON
            try:
                logger.debug(f'Analytical check results (v3 with formatting): {results}')
                print(f'Analytical check results (v3 with formatting): {results}')

                # Default return values
                dataset_is_sufficient = 'yes'
                explanation = ''
                confidence = '0'
                formatting_required = False
                columns_needing_formatting = []

                result = json.loads(results)
                
                # Extract individual values
                dataset_is_sufficient = result.get("dataset_is_sufficient", "yes")
                explanation = result.get("explanation", "")
                confidence = result.get("confidence", 0)
                formatting_required = result.get("formatting_required", False)
                columns_needing_formatting = result.get("columns_needing_formatting", [])
                column_source_mapping = result.get("column_source_mapping", {})  # NEW!
                
                # Print the extracted values
                print("Dataset is Sufficient:", dataset_is_sufficient)
                print("Explanation:", explanation)
                print("Confidence:", str(confidence))
                print("Formatting Required:", formatting_required)
                print("Columns Needing Formatting:", columns_needing_formatting)
                print("Column Source Mapping:", column_source_mapping)  # NEW!

                # Parse with the mapping from AI
                if column_source_mapping:
                    formatting_info = self._parse_formatting_from_metadata(
                        column_metadata_yaml, 
                        None,  # Not used
                        column_source_mapping  # Use AI mapping!
                    )
                    formatting_requirements = formatting_info
                    logger.debug(f'_parse_formatting_from_metadata: {formatting_info}')
                    print(f'_parse_formatting_from_metadata: {formatting_info}')
                else:
                    # Build structured formatting requirements
                    if formatting_required and columns_needing_formatting:
                        formatting_requirements = self._build_formatting_requirements_dict(
                            columns_needing_formatting, 
                            column_formatting_info
                        )
                        logger.debug(f'_build_formatting_requirements_dict: {formatting_requirements}')
                        print(f'_build_formatting_requirements_dict: {formatting_requirements}')

                self.formatting_requirements = formatting_requirements

                if str(dataset_is_sufficient).lower() == 'yes':
                    IS_REQUIRED = False   # If dataset is sufficient==yes, analytical query is not required
                else:
                    IS_REQUIRED = True
                    
            except Exception as e:
                print('_is_analytical_query_required_v3_with_formatting - error processing response:', str(e))
                logger.error('_is_analytical_query_required_v3_with_formatting - error processing response: ' + str(e))
                # On error, fall back to conservative approach (require analytical processing)
                IS_REQUIRED = True
                confidence = '50'
                explanation = 'Error processing analytical check, defaulting to analytical processing'
                
        except Exception as e:
            IS_REQUIRED = True
            results = str(e)
            logger.error(f'Error checking if analytical query is required (v3 with formatting): {e}')
            print(f'Error checking if analytical query is required (v3 with formatting): {e}')
            confidence = '50'
            explanation = 'Error in analytical check'
            formatting_required = False
            columns_needing_formatting = []
        
        return IS_REQUIRED, confidence, explanation, formatting_required, formatting_requirements
            
        #return IS_REQUIRED, confidence, explanation, formatting_required, columns_needing_formatting
    
    def _build_formatting_requirements_dict(self, columns_needing_formatting, column_formatting_info):
        """
        Build a structured dict of formatting requirements for preprocessing.
        
        Args:
            columns_needing_formatting: List of column names that need formatting
            column_formatting_info: String with formatting details
        
        Returns:
            Dict with structured formatting information
        """
        try:
            formats_dict = {}
            
            # Parse the formatting info string to extract format types
            if column_formatting_info and "Columns with formatting requirements:" in column_formatting_info:
                lines = column_formatting_info.split('\\n')
                for line in lines:
                    if line.strip().startswith('- '):
                        # Parse line like "- revenue: format=currency, type=amount"
                        parts = line.strip()[2:].split(':')
                        if len(parts) >= 2:
                            col_name = parts[0].strip()
                            details = parts[1].strip()
                            
                            # Extract format value
                            if 'format=' in details:
                                format_start = details.find('format=') + 7
                                format_end = details.find(',', format_start)
                                if format_end == -1:
                                    format_end = len(details)
                                format_value = details[format_start:format_end].strip()
                                formats_dict[col_name] = format_value
            
            return {
                'columns': columns_needing_formatting,
                'formats': formats_dict,
                'details': column_formatting_info
            }
            
        except Exception as e:
            logger.error(f"Error building formatting requirements dict: {str(e)}")
            return {'columns': [], 'formats': {}, 'details': ''}

    def set_data(self, dfs, dfs_desc, input_question):
        # Track whether the underlying data changed so the PandasAI Agent
        # can be reinitialised even on follow-up turns (stale DuckDB fix).
        prev_count = len(self.environment.dfs) if hasattr(self.environment, 'dfs') and self.environment.dfs else 0
        self._data_changed = (len(dfs) != prev_count)
        self.environment.dfs = dfs
        self.environment.dfs_desc = dfs_desc
        self.environment.current_input_question = input_question
        self.environment.df = dfs[-1]
        logger.debug(f'Set data: dfs count: {len(dfs)}, dfs_desc count: {len(dfs_desc)}, data_changed: {self._data_changed}, input_question: {input_question}')
        print(f'Set data: dfs count: {len(dfs)}, dfs_desc count: {len(dfs_desc)}, data_changed: {self._data_changed}, input_question: {input_question}')

    def get_answer(self, input_question, is_follow_up=False):
        logger.debug(f'Getting answer for question: {input_question}, is_follow_up: {is_follow_up}')
        print(f'Getting answer for question: {input_question}, is_follow_up: {is_follow_up}')
        answer, explain, clarify, answer_type, special_message, input_question = self._pandas_agent_answer(input_question, is_follow_up=is_follow_up)
        logger.debug(f'Answer: {answer}, Explain: {explain}, Answer Type: {answer_type}, Special Message: {special_message}, Input Question: {input_question}')
        print(f'Answer: {answer}, Explain: {explain}, Answer Type: {answer_type}, Special Message: {special_message}, Input Question: {input_question}')
        return answer, explain, clarify, answer_type, special_message, input_question
