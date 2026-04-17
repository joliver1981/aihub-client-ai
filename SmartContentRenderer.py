"""
SmartContentRenderer.py
AI-powered content analysis and structuring for rich display formats
"""

import json
import logging
from logging.handlers import WatchedFileHandler
import re
import pandas as pd
from typing import Dict, List, Any, Optional, Union
from AppUtils import azureQuickPrompt, azureMiniQuickPrompt
import base64
import io
import os
from CommonUtils import rotate_logs_on_startup, get_log_path
import config as cfg 

import sys
import time
import traceback
import tempfile
import ast
from io import StringIO
import subprocess
from system_prompts import SYS_PROMPT_SMART_CONTENT_RENDER_SYSTEM


rotate_logs_on_startup(os.getenv('SMART_CONTENT_RENDER_LOG', get_log_path('smart_content_render_log.txt')))


# Configure logging
logger = logging.getLogger("SmartContentRender")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('SMART_CONTENT_RENDER_LOG', get_log_path('smart_content_render_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

class SmartContentRenderer:
    """
    Intelligent content analyzer and renderer that uses AI to detect and structure
    content for optimal display in the chat interface.
    """
    
    def __init__(self):
        logger.info(86 * '#')
        logger.info('Initializing SmartContentRender...')
        logger.info(86 * '#')
        
    def analyze_and_render(self, content: Union[str, pd.DataFrame, dict, list], 
                          context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Main entry point for content analysis and rendering.
        
        Args:
            content: The content to analyze and render
            context: Optional context about the conversation/query
            
        Returns:
            Structured response with content blocks and metadata
        """
        try:
            # If content is already structured (DataFrame, dict with specific format)
            if isinstance(content, pd.DataFrame):
                logger.info('Rendering dataframe...')
                return self._render_dataframe(content, context)
            elif isinstance(content, dict) and 'type' in content:
                # Already structured content
                logger.info('Rendering original content...')
                return content
            elif isinstance(content, list) and all(isinstance(item, dict) for item in content):
                # List of dictionaries - likely tabular data
                logger.info('Rendering table from list...')
                return self._render_table_from_list(content, context)
            
            # For string content, use AI to analyze and structure
            if isinstance(content, str):
                logger.info('Analyzing content w/ AI...')
                return self._analyze_string_content(content, context)
            
            # Fallback for other types
            logger.info('Rendering fallback text...')
            return self._create_text_block(str(content))
            
        except Exception as e:
            logger.error(f"Error in analyze_and_render: {str(e)}")
            # Fallback to simple text rendering
            return self._create_text_block(str(content))
    
    def _extract_excel_chart_blocks(self, content: str) -> tuple:
        """
        Extract Excel chart JSON blocks from tool output.
        Returns (cleaned_content, list_of_chart_blocks) where cleaned_content
        has the chart markers removed so the AI doesn't see raw JSON.
        """
        chart_blocks = []
        chart_pattern = re.compile(r'EXCEL_CHART_START(\{.*?\})EXCEL_CHART_END', re.DOTALL)

        matches = chart_pattern.findall(content)
        for match in matches:
            try:
                chart_data = json.loads(match)

                # Handle the tool's block format: {type: "chart", content: {...}, metadata: {...}}
                if 'content' in chart_data and 'metadata' in chart_data:
                    chart_block = {
                        "type": "chart",
                        "content": chart_data['content'],
                        "metadata": chart_data['metadata']
                    }
                else:
                    # Legacy/fallback: raw Chart.js config format
                    chart_block = {
                        "type": "chart",
                        "content": chart_data.get('data', chart_data),
                        "metadata": {
                            "chart_type": chart_data.get('type', 'bar'),
                            "title": chart_data.get('options', {}).get('plugins', {}).get('title', {}).get('text', 'Chart'),
                            "interactive": True,
                            "downloadable": True,
                            "source": "excel_tool"
                        }
                    }
                chart_blocks.append(chart_block)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse Excel chart JSON: {e}")

        # Remove chart markers from content so AI analysis gets clean text
        cleaned_content = chart_pattern.sub('', content).strip()

        return cleaned_content, chart_blocks

    def _analyze_string_content(self, content: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Use AI to analyze string content and determine optimal rendering format.
        """
        try:
            # Extract any embedded Excel chart blocks before AI analysis
            excel_chart_blocks = []
            cleaned_content = content
            if 'EXCEL_CHART_START' in content:
                cleaned_content, excel_chart_blocks = self._extract_excel_chart_blocks(content)
                if excel_chart_blocks:
                    logger.info(f'Extracted {len(excel_chart_blocks)} Excel chart block(s) from content')
                    # If content is ONLY charts (no remaining text), return just the chart blocks
                    if not cleaned_content or cleaned_content.isspace():
                        return {
                            "type": "rich_content",
                            "blocks": excel_chart_blocks
                        }
                    # Otherwise, use cleaned_content for AI analysis and prepend charts later
                    content = cleaned_content

            # First, check for obvious patterns before calling AI
            # quick_detection = self._quick_pattern_detection(content)
            # if quick_detection:
            #     return quick_detection

            # Prepare the AI analysis prompt
            # system_prompt = """You are a content analysis expert. Analyze the given text and identify its structure and content types.
            # You must respond with a valid JSON object identifying content blocks and their types.
            
            # Content types you can identify:
            # - text: Regular text or paragraphs
            # - table: Tabular data (with headers and rows)
            # - code: Programming code with language detection
            # - list: Bulleted or numbered lists
            # - metrics: Key-value pairs, statistics, or KPIs
            # - json: JSON data structures
            # - sql: SQL queries
            # - chart_data: Data suitable for visualization
            # - alert: Important notices or warnings
            # - success: Success messages or confirmations
            # - error: Error messages
            # - image: Base64 encoded images or image references
            
            # Respond ONLY with a JSON object in this exact format:
            # {
            #     "blocks": [
            #         {
            #             "type": "content_type",
            #             "content": "extracted_content",
            #             "metadata": {}
            #         }
            #     ],
            #     "suggested_visualizations": []
            # }"""

            system_prompt = SYS_PROMPT_SMART_CONTENT_RENDER_SYSTEM

            logger.info('=============== SMART CONTENT INPUT PARAMS ===============')
            logger.info('context:', context)
            logger.info('----------------------------------------------------------')
            logger.info('content:', content)
            logger.info('===========================================================')
            print('=============== SMART CONTENT INPUT PARAMS ===============')
            print('context:', context)
            print('----------------------------------------------------------')
            print('content:', content)
            print('===========================================================')
            
            analysis_prompt = f"""
            User's question for context: {context.get('query') if context else 'No specific query provided'}

            Important to remember:
            1. Identify ALL distinct content blocks
            2. Preserve the original content accurately
            3. Detect tables even if they're in text format
            4. Identify code blocks with their language
            5. Recognize metrics/KPIs that should be highlighted
            6. Suggest visualizations where appropriate
            7. Always display FULL DATA
            8. Output valid JSON only.

            Analyze this content and structure it for optimal display:
            {content} 
            """

            # Call AI for analysis
            ai_response = azureMiniQuickPrompt(
                prompt=analysis_prompt,
                system=system_prompt,
                temp=0.1  # Low temperature for consistent structured output
            )

            logger.debug(86 * '#')
            logger.debug(86 * '#')
            logger.debug(86 * '#')
            logger.debug('=============== SMART CONTENT AI PROMPT ===============')
            logger.debug(system_prompt)
            logger.debug(86 * '-')
            logger.debug(analysis_prompt)
            logger.debug(86 * '#')
            logger.debug(86 * '#')
            logger.debug(86 * '#')
            logger.debug('=============== SMART CONTENT AI REPONSE ===============')
            logger.debug(ai_response)
            logger.debug(86 * '#')
            logger.debug(86 * '#')
            logger.debug(86 * '#')
            
            # Clean and parse AI response
            ai_response = ai_response.strip()
            if ai_response.startswith('```json'):
                ai_response = ai_response[7:]
            if ai_response.endswith('```'):
                ai_response = ai_response[:-3]
            
            try:
                analysis_result = json.loads(ai_response)
                result = self._process_ai_analysis(analysis_result, content)
            except json.JSONDecodeError:
                logger.warning("AI response was not valid JSON, falling back to text rendering")
                result = self._create_text_block(content)

            # Prepend any Excel chart blocks extracted earlier
            if excel_chart_blocks and 'blocks' in result:
                result['blocks'] = excel_chart_blocks + result['blocks']
            return result

        except Exception as e:
            logger.error(f"Error in AI content analysis: {str(e)}")
            fallback = self._create_text_block(content)
            # Still include chart blocks even if AI analysis failed
            if excel_chart_blocks and 'blocks' in fallback:
                fallback['blocks'] = excel_chart_blocks + fallback['blocks']
            return fallback

    def _quick_pattern_detection_legacy(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Quick pattern detection for common content types without AI.
        """
        # Check for HTML tables
        if '<table' in content.lower() and '</table>' in content.lower():
            return {
                "type": "rich_content",
                "blocks": [{
                    "type": "html_table",
                    "content": content,
                    "metadata": {"interactive": True}
                }]
            }
        
        # Check for obvious JSON
        content_stripped = content.strip()
        if (content_stripped.startswith('{') and content_stripped.endswith('}')) or \
           (content_stripped.startswith('[') and content_stripped.endswith(']')):
            try:
                json_data = json.loads(content_stripped)
                return {
                    "type": "rich_content",
                    "blocks": [{
                        "type": "json",
                        "content": json_data,
                        "metadata": {"collapsible": True}
                    }]
                }
            except:
                pass
        
        # Check for SQL queries
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP']
        content_upper = content.upper()
        if any(keyword in content_upper for keyword in sql_keywords) and 'FROM' in content_upper:
            return {
                "type": "rich_content",
                "blocks": [{
                    "type": "code",
                    "content": content,
                    "metadata": {"language": "sql", "copyable": True}
                }]
            }
        
        # Check for code blocks
        if '```' in content:
            return self._parse_markdown_code_blocks(content)
        
        return None
    
    def _parse_markdown_code_blocks(self, content: str) -> Dict[str, Any]:
        """
        Parse markdown-style code blocks from content.
        """
        blocks = []
        parts = content.split('```')
        
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Text block
                if part.strip():
                    blocks.append({
                        "type": "text",
                        "content": part.strip(),
                        "metadata": {}
                    })
            else:
                # Code block
                lines = part.split('\n')
                language = lines[0].strip() if lines else 'plaintext'
                code_content = '\n'.join(lines[1:]) if len(lines) > 1 else part
                
                blocks.append({
                    "type": "code",
                    "content": code_content.strip(),
                    "metadata": {"language": language, "copyable": True}
                })
        
        return {"type": "rich_content", "blocks": blocks}
    
    def _process_ai_analysis_legacy(self, analysis: Dict, original_content: str) -> Dict[str, Any]:
        """
        Process the AI analysis result and enhance it with additional metadata.
        """
        blocks = analysis.get('blocks', [])
        
        # Enhanced processing for each block type
        enhanced_blocks = []
        for block in blocks:
            block_type = block.get('type', 'text')
            content = block.get('content', '')
            metadata = block.get('metadata', {})
            
            # Enhance based on type
            if block_type == 'table':
                enhanced_block = self._enhance_table_block(content, metadata)
            elif block_type == 'code':
                enhanced_block = self._enhance_code_block(content, metadata)
            elif block_type == 'metrics':
                enhanced_block = self._enhance_metrics_block(content, metadata)
            elif block_type == 'chart_data':
                enhanced_block = self._enhance_chart_data_block(content, metadata)
            else:
                enhanced_block = block
            
            enhanced_blocks.append(enhanced_block)
        
        return {
            "type": "rich_content",
            "blocks": enhanced_blocks,
            "metadata": {
                "ai_analyzed": True,
                "suggested_visualizations": analysis.get('suggested_visualizations', [])
            }
        }
    
    def _enhance_table_block(self, content: Any, metadata: Dict) -> Dict:
        """
        Enhance table block with additional features.
        """
        # Try to parse the table content if it's a string
        if isinstance(content, str):
            table_data = self._parse_text_table(content)
        else:
            table_data = content
        
        return {
            "type": "table",
            "content": table_data,
            "metadata": {
                **metadata,
                "sortable": True,
                "filterable": True,
                "exportable": True,
                "paginated": len(table_data) > 50 if isinstance(table_data, list) else False
            }
        }
    
    def _parse_text_table(self, text: str) -> List[Dict]:
        """
        Parse a text table into structured data.
        """
        lines = text.strip().split('\n')
        if len(lines) < 2:
            return [{"value": text}]
        
        # Try to detect delimiter
        delimiters = ['\t', '|', ',', '  ']
        delimiter = None
        for d in delimiters:
            if d in lines[0]:
                delimiter = d
                break
        
        if not delimiter:
            return [{"value": text}]
        
        # Parse header and rows
        headers = [h.strip() for h in lines[0].split(delimiter)]
        rows = []
        
        for line in lines[1:]:
            if line.strip() and not all(c in '-=|' for c in line):
                values = [v.strip() for v in line.split(delimiter)]
                if len(values) == len(headers):
                    rows.append(dict(zip(headers, values)))
        
        return rows if rows else [{"value": text}]
    
    def _enhance_code_block(self, content: str, metadata: Dict) -> Dict:
        """
        Enhance code block with syntax highlighting hints.
        """
        # Detect language if not provided
        if 'language' not in metadata:
            metadata['language'] = self._detect_code_language(content)

        return {
            "type": "code",
            "content": content,
            "metadata": {
                **metadata,
                "copyable": True,
                "line_numbers": content.count('\n') > 10,
                "collapsible": content.count('\n') > 50
            }
        }
    
    def _detect_code_language(self, code: str) -> str:
        """
        Simple language detection based on patterns.
        """
        patterns = {
            'python': [r'def\s+\w+\(', r'import\s+\w+', r'print\(', r'if\s+__name__'],
            'javascript': [r'function\s+\w+\(', r'const\s+\w+', r'let\s+\w+', r'console\.log'],
            'sql': [r'SELECT\s+', r'FROM\s+', r'WHERE\s+', r'INSERT\s+INTO'],
            'json': [r'^\s*\{', r'^\s*\['],
            'html': [r'<html', r'<div', r'<span', r'</\w+>'],
            'css': [r'\w+\s*:\s*\w+;', r'\.\w+\s*\{', r'#\w+\s*\{']
        }
        
        for lang, patterns_list in patterns.items():
            for pattern in patterns_list:
                if re.search(pattern, code, re.IGNORECASE | re.MULTILINE):
                    return lang
        
        return 'plaintext'
    
    def _enhance_metrics_block(self, content: Any, metadata: Dict) -> Dict:
        """
        Enhance metrics block for better visualization.
        """
        metrics = []

        # Check if content is already in the correct format
        if isinstance(content, list) and all(
            isinstance(item, dict) and 'label' in item and 'value' in item 
            for item in content
        ):
            # Content is already properly formatted
            metrics = content
            # Add trend detection to existing metrics if not present
            for metric in metrics:
                if 'trend' not in metric:
                    metric['trend'] = self._detect_trend(str(metric.get('value', '')))
        elif isinstance(content, str):
            # Parse key-value pairs from text
            lines = content.split('\n')
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    metrics.append({
                        "label": key.strip(),
                        "value": value.strip(),
                        "trend": self._detect_trend(value.strip())
                    })
        elif isinstance(content, dict):
            for key, value in content.items():
                metrics.append({
                    "label": key,
                    "value": str(value),
                    "trend": self._detect_trend(str(value))
                })

        # Determine display style based on metadata or metric count
        display_style = metadata.get('display', 'cards')
        if display_style == 'kpi_cards' or len(metrics) <= 10:
            display_style = 'cards'
        elif len(metrics) > 10:
            display_style = 'list'
        
        return {
            "type": "metrics",
            "content": metrics,
            "metadata": {
                **metadata,
                "display": display_style,  # or "list"
                "highlight_changes": True
            }
        }
    
    def _detect_trend(self, value: str) -> Optional[str]:
        """
        Detect trend indicators in values.
        """
        # Only detect explicit trend indicators, not general minus signs
        if '↑' in value or value.lower().startswith(('increased', 'grew', 'rose')):
            return 'up'
        elif '↓' in value or value.lower().startswith(('decreased', 'fell', 'dropped')):
            return 'down'
        
        # Check for explicit percentage changes with context
        import re
        # Match patterns like "+15%" or "-10%" but not dates or ranges
        if re.match(r'^[+]\d+(\.\d+)?%?$', value.strip()):
            return 'up'
        elif re.match(r'^[-]\d+(\.\d+)?%?$', value.strip()):
            return 'down'
        
        return None
    
    def _enhance_chart_data_block(self, content: Any, metadata: Dict) -> Dict:
        """
        Enhance chart data block with visualization recommendations.
        """
        # Analyze data to suggest chart type
        chart_type = metadata.get('chart_type', 'auto')
        
        if chart_type == 'auto':
            chart_type = self._suggest_chart_type(content)
        
        return {
            "type": "chart",
            "content": content,
            "metadata": {
                **metadata,
                "chart_type": chart_type,
                "interactive": True,
                "downloadable": True
            }
        }
    
    def _suggest_chart_type(self, data: Any) -> str:
        """
        Suggest appropriate chart type based on data structure.
        """
        if isinstance(data, list) and len(data) > 0:
            if all(isinstance(item, (int, float)) for item in data):
                return 'line'
            elif all(isinstance(item, dict) for item in data):
                # Check for time series
                if any('date' in str(k).lower() or 'time' in str(k).lower() 
                      for k in data[0].keys()):
                    return 'line'
                # Check for categories
                if len(data) < 10:
                    return 'bar'
                else:
                    return 'line'
        
        return 'bar'  # Default
    
    def _render_dataframe(self, df: pd.DataFrame, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Render a pandas DataFrame with smart analysis.
        """
        logger.info("Dataframe detected, rendering dataframe...")
        print("Dataframe detected, rendering dataframe...")
        # Analyze DataFrame characteristics
        analysis = {
            "rows": len(df),
            "columns": len(df.columns),
            "numeric_columns": df.select_dtypes(include=['number']).columns.tolist(),
            "categorical_columns": df.select_dtypes(include=['object']).columns.tolist()
        }
        
        # Prepare table data
        table_data = df.to_dict('records')
        
        # Check if we should suggest visualizations
        blocks = []
        
        # Add summary if large dataset
        if len(df) > 10000:
            blocks.append({
                "type": "text",
                "content": f"Dataset contains {len(df):,} rows and {len(df.columns)} columns. Showing first 10000 rows.",
                "metadata": {"style": "info"}
            })
        
        # Add the table
        blocks.append({
            "type": "table",
            "content": table_data,
            "metadata": {
                "columns": df.columns.tolist(),
                "sortable": True,
                "filterable": True,
                "exportable": True,
                "paginated": len(df) > 50,
                "total_rows": len(df)
            }
        })
        
        # Suggest visualizations if applicable
        if analysis['numeric_columns']:
            suggestions = self._suggest_dataframe_visualizations(df, analysis)
            if suggestions:
                blocks.extend(suggestions)
        
        return {
            "type": "rich_content",
            "blocks": blocks,
            "metadata": {
                "source": "dataframe",
                "analysis": analysis
            }
        }
    
    def _suggest_dataframe_visualizations(self, df: pd.DataFrame, 
                                         analysis: Dict) -> List[Dict]:
        """
        Suggest and prepare visualization blocks for DataFrame.
        """
        suggestions = []
        numeric_cols = analysis['numeric_columns']
        
        # If there are 2+ numeric columns, suggest a correlation matrix or scatter plot
        if len(numeric_cols) >= 2:
            # Prepare data for a simple bar chart of means
            means = df[numeric_cols].mean().to_dict()
            
            suggestions.append({
                "type": "chart",
                "content": {
                    "labels": list(means.keys()),
                    "datasets": [{
                        "label": "Average Values",
                        "data": list(means.values())
                    }]
                },
                "metadata": {
                    "chart_type": "bar",
                    "title": "Average Values by Column",
                    "interactive": True
                }
            })
        
        return suggestions
    
    def _render_table_from_list(self, data: List[Dict], 
                               context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Render a list of dictionaries as a table.
        """
        if not data:
            return self._create_text_block("No data to display")
        
        # Extract columns
        columns = list(data[0].keys())
        
        return {
            "type": "rich_content",
            "blocks": [{
                "type": "table",
                "content": data,
                "metadata": {
                    "columns": columns,
                    "sortable": True,
                    "filterable": len(data) > 10,
                    "exportable": True,
                    "paginated": len(data) > 50
                }
            }]
        }
    
    def _create_text_block(self, content: str) -> Dict[str, Any]:
        """
        Create a simple text block.
        """
        return {
            "type": "rich_content",
            "blocks": [{
                "type": "text",
                "content": content,
                "metadata": {}
            }]
        }
    
    def post_process_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post-process the response to ensure compatibility and add final touches.
        """
        # Ensure the response has the correct structure
        if 'type' not in response:
            response['type'] = 'rich_content'
        
        if 'blocks' not in response and 'content' in response:
            # Convert old format to new block format
            response = {
                "type": "rich_content",
                "blocks": [{
                    "type": "text",
                    "content": response['content'],
                    "metadata": response.get('metadata', {})
                }]
            }
        
        # Add timestamp
        response['timestamp'] = pd.Timestamp.now().isoformat()
        
        return response
    

    def _quick_pattern_detection(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Quick pattern detection for common content types without AI.
        Enhanced to better detect markdown tables.
        """
        # Check for markdown table FIRST (before other patterns)
        if self._is_markdown_table(content):
            return self._parse_markdown_table(content)
        
        # Check for bullet/numbered lists with URLs
        if self._is_list_with_links(content):
            return self._parse_list_content(content)
            
        # Check for HTML tables
        if '<table' in content.lower() and '</table>' in content.lower():
            return {
                "type": "rich_content",
                "blocks": [{
                    "type": "html_table",
                    "content": content,
                    "metadata": {"interactive": True}
                }]
            }
        
        # Rest of your existing quick pattern detection...
        # Check for obvious JSON
        content_stripped = content.strip()
        if (content_stripped.startswith('{') and content_stripped.endswith('}')) or \
        (content_stripped.startswith('[') and content_stripped.endswith(']')):
            try:
                json_data = json.loads(content_stripped)
                return {
                    "type": "rich_content",
                    "blocks": [{
                        "type": "json",
                        "content": json_data,
                        "metadata": {"collapsible": True}
                    }]
                }
            except:
                pass
        
        # Check for SQL queries
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP']
        content_upper = content.upper()
        if any(keyword in content_upper for keyword in sql_keywords) and 'FROM' in content_upper:
            return {
                "type": "rich_content",
                "blocks": [{
                    "type": "code",
                    "content": content,
                    "metadata": {"language": "sql", "copyable": True}
                }]
            }
        
        # Check for code blocks
        if '```' in content:
            return self._parse_markdown_code_blocks(content)
        
        return None

    def _is_markdown_table(self, content: str) -> bool:
        """
        Check if content contains a markdown table.
        """
        lines = content.strip().split('\n')
        
        # Look for table characteristics
        has_pipes = False
        has_separator = False
        
        for i, line in enumerate(lines):
            if '|' in line:
                has_pipes = True
                # Check for separator line (contains | and -)
                if re.match(r'^[\s\|:\-]+$', line):
                    has_separator = True
                    break
        
        return has_pipes and has_separator

    def _parse_markdown_table(self, content: str) -> Dict[str, Any]:
        """
        Parse markdown table from content and return structured blocks.
        """
        lines = content.strip().split('\n')
        blocks = []
        
        # Find where the table starts
        table_start_idx = -1
        table_end_idx = len(lines)
        
        for i, line in enumerate(lines):
            # Look for table header (line with pipes)
            if '|' in line and table_start_idx == -1:
                # Check if next line is separator
                if i + 1 < len(lines) and re.match(r'^[\s\|:\-]+$', lines[i + 1]):
                    table_start_idx = i
                    break
        
        if table_start_idx == -1:
            # No table found, return as text
            return {
                "type": "rich_content",
                "blocks": [{
                    "type": "text",
                    "content": content,
                    "metadata": {}
                }]
            }
        
        # Add text before table if exists
        if table_start_idx > 0:
            pre_table_text = '\n'.join(lines[:table_start_idx]).strip()
            if pre_table_text:
                blocks.append({
                    "type": "text",
                    "content": pre_table_text,
                    "metadata": {}
                })
        
        # Parse the table
        header_line = lines[table_start_idx]
        headers = [h.strip() for h in header_line.split('|') if h.strip()]
        
        # Find where table ends (first line without pipes after table start)
        for i in range(table_start_idx + 2, len(lines)):
            if '|' not in lines[i]:
                table_end_idx = i
                break
        
        # Parse table rows
        table_data = []
        for i in range(table_start_idx + 2, table_end_idx):
            line = lines[i]
            if '|' in line:
                values = [v.strip() for v in line.split('|')]
                # Filter out empty values at start/end from split
                values = [v for v in values if v]
                
                if len(values) == len(headers):
                    row = {}
                    for j, header in enumerate(headers):
                        row[header] = values[j]
                    table_data.append(row)
        
        # Add the table block
        blocks.append({
            "type": "table",
            "content": table_data,
            "metadata": {
                "columns": headers,
                "sortable": True,
                "filterable": True,
                "exportable": True,
                "paginated": len(table_data) > 50,
                "total_rows": len(table_data)
            }
        })
        
        # Add text after table if exists
        if table_end_idx < len(lines):
            post_table_text = '\n'.join(lines[table_end_idx:]).strip()
            if post_table_text:
                blocks.append({
                    "type": "text",
                    "content": post_table_text,
                    "metadata": {}
                })
        
        return {
            "type": "rich_content",
            "blocks": blocks,
            "metadata": {
                "source": "markdown_table_parser"
            }
        }

    # Update the _process_ai_analysis method to handle table detection better
    def _process_ai_analysis(self, analysis: Dict, original_content: str) -> Dict[str, Any]:
        """
        Process the AI analysis result and enhance it with additional metadata.
        Enhanced to detect tables that AI might have missed.
        """
        blocks = analysis.get('blocks', [])
        
        # Check if any text blocks contain markdown tables
        enhanced_blocks = []
        for block in blocks:
            if block.get('type') == 'text':
                # Check if this text block contains a markdown table
                if self._is_markdown_table(block.get('content', '')):
                    # Parse the markdown table
                    parsed = self._parse_markdown_table(block.get('content', ''))
                    # Add the parsed blocks instead of the original text block
                    enhanced_blocks.extend(parsed.get('blocks', [block]))
                else:
                    enhanced_blocks.append(block)
            elif block.get('type') == 'list':
                # PROCESS LIST CONTENT HERE
                content = block.get('content', [])
                metadata = block.get('metadata', {})
                
                # Process the list items to handle links properly
                processed_content = self._process_list_content(content)
                
                enhanced_blocks.append({
                    "type": "list",
                    "content": processed_content,
                    "metadata": {
                        **metadata,
                        "has_links": any(isinstance(item, dict) for item in processed_content)
                    }
                })
            elif block.get('type') == 'code':
                content = block.get('content', '')
                metadata = block.get('metadata', {})
                if metadata.get('attempt_execute') == 'true':
                    # Return the output of the code
                    try:
                        executed_block = self.execute_code_from_block(block)
                        print('Code executed successfully, processing output for display...')
                        logger.info(f"Code executed successfully, processing output for display...")
                        display_blocks = self.process_execution_for_display(executed_block)
                        enhanced_blocks.extend(display_blocks)
                    except Exception as e:
                        print(f"Failed to execute code block - {str(e)}")
                         # Return the pure code as a block if execution fails
                        enhanced_block = self._enhance_code_block(content, metadata)
                        enhanced_blocks.append(enhanced_block)
                else:
                    # Return the pure code as a block
                    enhanced_block = self._enhance_code_block(content, metadata)
                    enhanced_blocks.append(enhanced_block)
            else:
                # Process other block types normally
                block_type = block.get('type', 'text')
                content = block.get('content', '')
                metadata = block.get('metadata', {})
                
                # Enhance based on type
                if block_type == 'table':
                    enhanced_block = self._enhance_table_block(content, metadata)
                elif block_type == 'code':
                    enhanced_block = self._enhance_code_block(content, metadata)
                elif block_type == 'metrics':
                    enhanced_block = self._enhance_metrics_block(content, metadata)
                elif block_type == 'chart_data':
                    enhanced_block = self._enhance_chart_data_block(content, metadata)
                else:
                    enhanced_block = block
                
                enhanced_blocks.append(enhanced_block)
        
        return {
            "type": "rich_content",
            "blocks": enhanced_blocks,
            "metadata": {
                "ai_analyzed": True,
                "suggested_visualizations": analysis.get('suggested_visualizations', [])
            }
        }
    
    def _process_list_content(self, content: Any) -> List[Any]:
        """
        Process list content to handle various formats including links.
        Properly converts file:// URLs to /document/serve endpoints.
        """
        if not isinstance(content, list):
            content = [content]
        
        processed_items = []
        for item in content:
            if isinstance(item, str):
                # Check if it's a markdown-style link first
                import re
                md_link_pattern = r'\[([^\]]+)\]\(([^\)]+)\)'
                md_match = re.search(md_link_pattern, item)
                
                if md_match:
                    link_text = md_match.group(1)
                    link_url = md_match.group(2)
                    
                    # Convert file:// URLs to /document/serve
                    if link_url.startswith('file://'):
                        # Extract the path after file://
                        file_path = link_url[7:]  # Remove 'file://'
                        
                        # URL encode the path for the serve endpoint
                        import urllib.parse
                        encoded_path = urllib.parse.quote(file_path, safe='')
                        
                        # Create the serve URL
                        serve_url = f"/document/serve?path={encoded_path}"
                        
                        processed_items.append({
                            'text': link_text,
                            'url': serve_url,
                            'original_url': link_url  # Keep original for reference
                        })
                    else:
                        # Non-file URL, keep as is
                        processed_items.append({
                            'text': link_text,
                            'url': link_url
                        })
                        
                # Check if it's a plain URL
                elif item.startswith(('http://', 'https://', 'ftp://')):
                    # Regular URL - create a link object
                    processed_items.append({
                        'text': item,
                        'url': item
                    })
                    
                elif item.startswith('file://'):
                    # Plain file:// URL without markdown formatting
                    # Extract the path and filename
                    file_path = item[7:]  # Remove 'file://'
                    filename = file_path.split('/')[-1]
                    
                    # URL encode the path
                    import urllib.parse
                    encoded_path = urllib.parse.quote(file_path, safe='')
                    
                    # Create the serve URL
                    serve_url = f"/document/serve?path={encoded_path}"
                    
                    processed_items.append({
                        'text': filename,
                        'url': serve_url,
                        'original_url': item
                    })
                    
                elif item.startswith('\\\\'):
                    # UNC path - convert to serve URL
                    import urllib.parse
                    encoded_path = urllib.parse.quote(item, safe='')
                    serve_url = f"/document/serve?path={encoded_path}"
                    
                    # Extract filename for display
                    filename = item.split('\\')[-1] if '\\' in item else item
                    
                    processed_items.append({
                        'text': filename,
                        'url': serve_url,
                        'original_path': item
                    })
                    
                elif '/document/serve' in item:
                    # Already a document serve path
                    processed_items.append({
                        'text': item.split('/')[-1] if '/' in item else item,
                        'url': item
                    })
                else:
                    # Regular text item
                    processed_items.append(item)
                    
            elif isinstance(item, dict):
                # Already structured - check if it needs URL conversion
                if 'url' in item and item['url'].startswith('file://'):
                    # Convert file:// URL to serve endpoint
                    file_path = item['url'][7:]
                    import urllib.parse
                    encoded_path = urllib.parse.quote(file_path, safe='')
                    
                    item['url'] = f"/document/serve?path={encoded_path}"
                    item['original_url'] = item.get('original_url', item['url'])
                    
                processed_items.append(item)
            else:
                # Convert to string
                processed_items.append(str(item))
        
        return processed_items

    # Update the _enhance_list_block method or add it if it doesn't exist
    def _enhance_list_block(self, content: Any, metadata: Dict) -> Dict:
        """
        Enhance list blocks to properly handle links and other content types
        """
        processed_content = self._process_list_content(content)
        
        return {
            "type": "list",
            "content": processed_content,
            "metadata": {
                **metadata,
                "has_links": any(isinstance(item, dict) for item in processed_content)
            }
        }
    
    def _is_list_with_links(self, content: str) -> bool:
        """
        Check if content appears to be a list with links
        """
        lines = content.strip().split('\n')
        list_indicators = 0
        url_count = 0
        
        for line in lines:
            # Check for list markers
            if re.match(r'^[\*\-\+•]\s+', line) or re.match(r'^\d+[\.\)]\s+', line):
                list_indicators += 1
            # Check for URLs (including file://)
            if re.search(r'(https?|ftp|file)://[^\s]+', line):
                url_count += 1
            # Also check for markdown-style links
            if re.search(r'\[([^\]]+)\]\(([^\)]+)\)', line):
                url_count += 1
        
        # If we have multiple list items and at least one URL, treat as list with links
        return list_indicators >= 2 or url_count >= 2


    def _parse_list_content(self, content: str) -> Dict[str, Any]:
        """
        Parse content that appears to be a list, especially with links
        """
        lines = content.strip().split('\n')
        list_items = []
        pre_text = []
        post_text = []
        in_list = False
        list_ended = False
        
        for line in lines:
            # Check if this is a list item
            list_match = re.match(r'^[\*\-\+•]\s+(.+)', line) or re.match(r'^\d+[\.\)]\s+(.+)', line)
            
            if list_match:
                in_list = True
                item_text = list_match.group(1).strip()
                
                # Check for markdown-style links first [text](url)
                md_link_match = re.search(r'\[([^\]]+)\]\(([^\)]+)\)', item_text)
                if md_link_match:
                    link_text = md_link_match.group(1)
                    link_url = md_link_match.group(2)
                    list_items.append({"text": link_text, "url": link_url})
                else:
                    # Check if item contains a URL (including file://)
                    url_match = re.search(r'((https?|ftp|file)://[^\s\)]+)', item_text)
                    if url_match:
                        url = url_match.group(1)
                        # Extract text before URL as label, or use URL if no text
                        text_before = item_text[:url_match.start()].strip()
                        if text_before:
                            list_items.append({"text": text_before, "url": url})
                        else:
                            # For file:// URLs, extract filename for display
                            if url.startswith('file://'):
                                display_text = url.split('/')[-1]
                            else:
                                display_text = url
                            list_items.append({"text": display_text, "url": url})
                    else:
                        # Regular list item
                        list_items.append(item_text)
            elif not in_list:
                # Text before list
                pre_text.append(line)
            elif in_list and line.strip():
                # Non-list line after list started - list has ended
                list_ended = True
                post_text.append(line)
            elif list_ended:
                post_text.append(line)
        
        # Build blocks
        blocks = []
        
        # Add pre-text if exists
        if pre_text:
            blocks.append({
                "type": "text",
                "content": '\n'.join(pre_text).strip(),
                "metadata": {}
            })
        
        # Add the list
        if list_items:
            processed_items = self._process_list_content(list_items)
            blocks.append({
                "type": "list",
                "content": processed_items,
                "metadata": {
                    "ordered": bool(re.match(r'^\d+[\.\)]', lines[0])),
                    "has_links": any(isinstance(item, dict) for item in processed_items)
                }
            })
        
        # Add post-text if exists
        if post_text:
            blocks.append({
                "type": "text",
                "content": '\n'.join(post_text).strip(),
                "metadata": {}
            })
        
        return {
            "type": "rich_content",
            "blocks": blocks if blocks else [{
                "type": "text",
                "content": content,
                "metadata": {}
            }]
        }
    

    def execute_python_code(self, code: str, 
                      return_format: str = "string",
                      capture_output: bool = True,
                      safe_mode: bool = True,
                      timeout: int = 30) -> Dict[str, Any]:
        """
        Execute Python code and handle return values with various output formats.
        
        Args:
            code: Python code to execute as a string
            return_format: Expected format of the return value ('string', 'file_path', 'base64', 'dataframe', 'json')
            capture_output: Whether to capture stdout/stderr
            safe_mode: If True, performs basic safety checks on the code
            timeout: Maximum execution time in seconds
            
        Returns:
            Dictionary containing:
                - success: Boolean indicating if execution was successful
                - result: The return value or captured output
                - output: Captured stdout if capture_output is True
                - error: Error message if execution failed
                - execution_time: Time taken to execute in seconds
                
        Example Usage:
            >>> renderer = SmartContentRenderer()
            >>> code = '''
            ... def calculate_sum(a, b):
            ...     return a + b
            ... result = calculate_sum(5, 3)
            ... return result
            ... '''
            >>> result = renderer.execute_python_code(code, return_format="string")
            >>> print(result['result'])  # Output: "8"
        """
        start_time = time.time()
        
        # Basic safety checks if enabled
        if safe_mode:
            try:
                # Parse the code to check for obvious dangerous operations
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    # Check for potentially dangerous imports
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            dangerous_modules = ['subprocess', 'shutil', '__builtin__', '__builtins__']
                            if alias.name in dangerous_modules:
                                logger.warning(f"Potentially dangerous import detected: {alias.name}")
                    
                    # Check for __import__ calls
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id == '__import__':
                            logger.warning("Dynamic import detected via __import__")
                            
                    # Check for eval/exec calls nested in the code
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id in ['eval', 'compile']:
                            logger.warning(f"Potentially dangerous function call: {node.func.id}")
            except SyntaxError as e:
                return {
                    "success": False,
                    "result": None,
                    "output": "",
                    "error": f"Syntax error in code: {str(e)}",
                    "execution_time": time.time() - start_time
                }
        
        # Prepare execution environment
        execution_result = {
            "success": False,
            "result": None,
            "output": "",
            "error": None,
            "execution_time": 0,
            "metadata": {}
        }
        
        # Redirect stdout/stderr if capture_output is True
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stdout_capture = StringIO()
        stderr_capture = StringIO()
        
        if capture_output:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
        
        try:
            # Create a namespace for execution with common imports
            exec_namespace = {
                '__builtins__': __builtins__,
                'pd': pd,
                'json': json,
                'base64': base64,
                're': re,
                'os': os,
                'datetime': __import__('datetime'),
                'math': __import__('math'),
                'random': __import__('random'),
                'collections': __import__('collections'),
                'itertools': __import__('itertools'),
                'io': __import__('io'),
            }
            
            # Try to import numpy if available
            try:
                import numpy as np
                exec_namespace['np'] = np
            except ImportError:
                pass

            # Import matplotlib with non-interactive backend
            try:
                import matplotlib
                matplotlib.use('Agg')  # Use non-interactive backend
                import matplotlib.pyplot as plt
                exec_namespace['plt'] = plt
                exec_namespace['matplotlib'] = matplotlib
            except ImportError:
                pass
            
            # Parse the code to handle return values properly
            code_lines = code.strip().split('\n')
            
            # Check if the code has an explicit return statement
            has_explicit_return = any('return ' in line for line in code_lines)
            
            # Check if the last line could be an expression
            last_line = code_lines[-1].strip() if code_lines else ""
            is_assignment = '=' in last_line and not any(op in last_line for op in ['==', '!=', '<=', '>='])
            
            if has_explicit_return:
                # Code has explicit return, wrap in function
                # Properly indent the code
                min_indent = min((len(line) - len(line.lstrip()) 
                                for line in code_lines if line.strip()), default=0)
                
                # Remove common indentation and re-indent
                normalized_lines = []
                for line in code_lines:
                    if line.strip():
                        normalized_lines.append('    ' + line[min_indent:])
                    else:
                        normalized_lines.append('')
                
                wrapped_code = "def __exec_function():\n" + '\n'.join(normalized_lines) + "\n__exec_result = __exec_function()"
                exec(wrapped_code, exec_namespace)
                result = exec_namespace.get('__exec_result')
                
            elif not is_assignment and last_line and len(code_lines) > 0:
                # Try to evaluate the last line as an expression
                try:
                    # Test if the last line is a valid expression
                    compile(last_line, '<string>', 'eval')
                    
                    # Execute all but the last line
                    if len(code_lines) > 1:
                        exec('\n'.join(code_lines[:-1]), exec_namespace)
                    
                    # Evaluate the last line
                    result = eval(last_line, exec_namespace)
                except:
                    # Not an expression, execute everything normally
                    exec(code, exec_namespace)
                    # Try to find a result variable
                    for var_name in ['result', 'output', 'res', 'answer']:
                        if var_name in exec_namespace:
                            result = exec_namespace[var_name]
                            break
                    else:
                        result = None
            else:
                # Normal execution
                exec(code, exec_namespace)
                # Check for common result variable names
                for var_name in ['result', 'output', 'res', 'answer']:
                    if var_name in exec_namespace:
                        result = exec_namespace[var_name]
                        break
                else:
                    # If the last line was an assignment, get that variable
                    if is_assignment and '=' in last_line:
                        var_name = last_line.split('=')[0].strip()
                        if var_name in exec_namespace:
                            result = exec_namespace[var_name]
                    else:
                        result = None

            # Auto-detect matplotlib figures
            if 'plt' in exec_namespace:
                try:
                    import matplotlib.pyplot as plt
                    if plt.get_fignums():  # Check if there are any active figures
                        logger.info("Auto-capturing matplotlib figure as base64")
                        import io
                        buffer = io.BytesIO()
                        plt.savefig(buffer, format='png', bbox_inches='tight', dpi=100)
                        buffer.seek(0)
                        result = base64.b64encode(buffer.getvalue()).decode('utf-8')
                        plt.close('all')  # Clean up figures
                        
                        # Override return format for auto-detected charts
                        return_format = "base64"
                        execution_result["metadata"]["chart_detected"] = True
                        execution_result["metadata"]["auto_captured"] = True
                except Exception as e:
                    logger.warning(f"Failed to auto-capture matplotlib figure: {e}")
            
            # Process the result based on the expected format
            if return_format == "string":
                if result is not None:
                    execution_result["result"] = str(result)
                else:
                    execution_result["result"] = stdout_capture.getvalue() if capture_output else ""
                    
            elif return_format == "json":
                if isinstance(result, (dict, list)):
                    # IMPORTANT: Always stringify JSON data
                    execution_result["result"] = json.dumps(result, default=str)
                elif isinstance(result, pd.DataFrame):
                    execution_result["result"] = result.to_json(orient='records')
                else:
                    execution_result["result"] = json.dumps({"value": str(result)})
                    
            elif return_format == "dataframe":
                if isinstance(result, pd.DataFrame):
                    # Create a dict with data and metadata
                    df_data = {
                        "data": result.to_dict('records'),
                        "shape": result.shape,
                        "columns": result.columns.tolist(),
                        "dtypes": {col: str(dtype) for col, dtype in result.dtypes.items()}
                    }
                    # IMPORTANT: Stringify the entire structure
                    execution_result["result"] = json.dumps(df_data, default=str)
                    execution_result["metadata"]["original_type"] = "dataframe"
                elif isinstance(result, (list, dict)):
                    try:
                        df = pd.DataFrame(result)
                        execution_result["result"] = df.to_dict('records')
                        execution_result["metadata"]["shape"] = df.shape
                        execution_result["metadata"]["columns"] = df.columns.tolist()
                    except:
                        execution_result["result"] = str(result) if result else ""
                else:
                    execution_result["result"] = str(result) if result else ""
                    
            elif return_format == "file_path":
                # Assume the result is a file path
                if result and isinstance(result, str):
                    # Check if it's a valid file path
                    if os.path.exists(result):
                        execution_result["result"] = result
                        execution_result["metadata"]["file_size"] = os.path.getsize(result)
                        execution_result["metadata"]["file_exists"] = True
                    else:
                        execution_result["error"] = f"File not found: {result}"
                        execution_result["success"] = False
                        return execution_result
                else:
                    execution_result["error"] = "Expected file path as result"
                    execution_result["success"] = False
                    return execution_result
                    
            elif return_format == "base64":
                # Handle different types of data that might need base64 encoding
                if isinstance(result, bytes):
                    execution_result["result"] = base64.b64encode(result).decode('utf-8')
                elif isinstance(result, str):
                    # Check if it's already base64
                    try:
                        base64.b64decode(result)
                        execution_result["result"] = result
                    except:
                        # Encode string to base64
                        execution_result["result"] = base64.b64encode(result.encode()).decode('utf-8')
                elif hasattr(result, 'read'):
                    # File-like object
                    content = result.read()
                    if isinstance(content, bytes):
                        execution_result["result"] = base64.b64encode(content).decode('utf-8')
                    else:
                        execution_result["result"] = base64.b64encode(content.encode()).decode('utf-8')
                elif result is not None:
                    # Convert to string then base64
                    execution_result["result"] = base64.b64encode(str(result).encode()).decode('utf-8')
                else:
                    execution_result["result"] = ""
            else:
                # Default: return as-is
                execution_result["result"] = result
            
            execution_result["success"] = True
            
        except TimeoutError:
            execution_result["error"] = f"Code execution timed out after {timeout} seconds"
            logger.error(f"Code execution timeout: {code[:100]}...")
            
        except Exception as e:
            execution_result["error"] = f"{type(e).__name__}: {str(e)}"
            execution_result["traceback"] = traceback.format_exc()
            logger.error(f"Code execution error: {execution_result['error']}")
            logger.debug(f"Traceback: {execution_result.get('traceback', '')}")
            
        finally:
            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            
            # Capture output
            if capture_output:
                execution_result["output"] = stdout_capture.getvalue()
                stderr_output = stderr_capture.getvalue()
                if stderr_output:
                    execution_result["stderr"] = stderr_output
            
            # Calculate execution time
            execution_result["execution_time"] = time.time() - start_time
            
            # Add additional metadata
            execution_result["metadata"]["code_length"] = len(code)
            execution_result["metadata"]["lines_of_code"] = len(code_lines)

        # Set the actual format being returned for proper display
        execution_result["return_format"] = return_format
        
        return execution_result


    def execute_code_from_block(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute code from a content block with metadata mapping.
        """
        if block.get('type') != 'code':
            return block
        
        metadata = block.get('metadata', {})
        
        # Support both old and new metadata formats
        should_execute = (
            metadata.get('attempt_execute') == 'true' or 
            metadata.get('execute') == 'true'
        )
        
        if not should_execute:
            return block
        
        # Map from cleaner metadata to function parameters
        returns = metadata.get('expected_output', 'text')
        
        # Map 'returns' to 'return_format' expected by execute_python_code
        format_mapping = {
            'dataframe': 'dataframe',
            'chart': 'base64',  # Charts become base64 images
            'value': 'string',
            'text': 'string',
            'file': 'file_path',
            'json': 'json',
            'binary': 'base64',
            'plot': 'base64'
        }
        
        return_format = format_mapping.get(returns, 'string')
        
        # Execute the code
        logger.debug(f'Executing python code with expected return format {return_format}...')
        code = block.get('content', '')
        execution_result = self.execute_python_code(
            code=code,
            return_format=return_format,
            capture_output=True,
            safe_mode=True
        )
        logger.debug(86 * '$')
        logger.debug(f"execution_result: {execution_result}")
        logger.debug(86 * '$')
        
        if execution_result['success']:
            # Store the string result
            block['metadata']['executed'] = True
            block['metadata']['execution_result'] = str(execution_result['result'])  # Ensure string
            block['metadata']['execution_time'] = execution_result['execution_time']
            
            # Store type hint for deserialization
            block['metadata']['return_format'] = execution_result["return_format"]   # This could have been changed after code execution (eg base64 for plots)
            # if execution_result["return_format"] != returns:
            #     block['metadata']['result_type'] = execution_result["return_format"]
            # else:
            #     block['metadata']['result_type'] = returns
            
            block['metadata']['result_type'] = returns
            block['metadata']['display_as'] = metadata.get('display_as', 'auto')
            
            if execution_result.get('output'):
                block['metadata']['execution_output'] = execution_result['output']
        else:
            block['metadata']['execution_error'] = execution_result['error']
            
        return block
    

    def process_execution_for_display(self, block: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convert executed code block into display blocks.
        """
        metadata = block.get('metadata', {})
        
        if not metadata.get('executed'):
            return [block]
        
        result_string = metadata.get('execution_result', '')
        result_type = metadata.get('result_type', 'text')
        display_as = metadata.get('display_as', 'auto')
        show_code = metadata.get('show_code', 'false') == 'true'

        logger.debug(86 * '$')
        logger.debug(f'result_string: {result_string}')
        logger.debug(f'result_type: {result_type}')
        logger.debug(f'display_as: {display_as}')
        logger.debug(f'show_code: {show_code}')
        logger.debug(86 * '$')
        
        display_blocks = []
        
        # Add description if present
        if metadata.get('description'):
            display_blocks.append({
                "type": "execution_header",
                "content": metadata['description'],
                "metadata": {"style": "subtle"}
            })
        
        # Parse and format the result
        try:
            if result_type == 'dataframe' or display_as == 'table':
                # Parse JSON string back to data
                data = json.loads(result_string)
                if isinstance(data, dict) and 'data' in data:
                    # Full dataframe structure
                    display_blocks.append({
                        "type": "table",
                        "content": data['data'],
                        "metadata": {
                            "columns": data.get('columns', []),
                            "shape": data.get('shape'),
                            "interactive": True
                        }
                    })
                else:
                    # Simple list of records
                    display_blocks.append({
                        "type": "table",
                        "content": data if isinstance(data, list) else [data],
                        "metadata": {"interactive": True}
                    })
                    
            elif result_type == 'json':
                data = json.loads(result_string)
                display_blocks.append({
                    "type": "json",
                    "content": data,
                    "metadata": {"collapsible": True}
                })
                
            elif display_as == 'image' or result_type == 'chart' or result_type == 'base64' or result_type == 'plot':
                # Assume base64 image
                display_blocks.append({
                    "type": "image",
                    "content": f"data:image/png;base64,{result_string}",
                    "metadata": {"generated": True}
                })
                
            elif display_as == 'metric' or result_type == 'value':
                display_blocks.append({
                    "type": "metric",
                    "content": {"value": result_string},
                    "metadata": {"style": "highlight"}
                })
                
            else:
                # Default text display
                display_blocks.append({
                    "type": "text",
                    "content": result_string,
                    "metadata": {"generated": True}
                })
                
        except (json.JSONDecodeError, ValueError) as e:
            # Fallback to text if parsing fails
            display_blocks.append({
                "type": "text",
                "content": result_string,
                "metadata": {"parse_error": str(e)}
            })
        
        # Add code if requested
        if show_code:
            display_blocks.append({
                "type": "collapsible",
                "title": "View code",
                "content": [{
                    "type": "code",
                    "content": block['content'],
                    "metadata": {"language": "python"}
                }],
                "metadata": {"collapsed": True}
            })
        
        return display_blocks


    # Integration helper for processing multiple blocks
    def process_blocks_with_execution(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process a list of content blocks and execute any marked for execution.
        
        Args:
            blocks: List of content blocks
            
        Returns:
            Processed list of blocks with execution results
        """
        processed_blocks = []
        
        for block in blocks:
            result = self.execute_code_from_block(block)
            
            # Handle case where execution creates multiple blocks
            if isinstance(result, list):
                processed_blocks.extend(result)
            else:
                processed_blocks.append(result)
        
        return processed_blocks