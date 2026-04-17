"""
SmartContentRenderer - Hybrid Approach
Combines deterministic DataFrame rendering with optional AI-powered contextual insights.

Key optimization: The LLM never outputs table data - only insights/context.
This dramatically reduces token output and response time.
"""

import json
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional, Union
from AppUtils import azureMiniQuickPrompt  # Use mini model for speed
from system_prompts import SYS_PROMPT_SMART_CONTENT_RENDER_SYSTEM
import config as cfg

logger = logging.getLogger("SmartContentRender")


# =============================================================================
# NEW PROMPT: Context-only analysis (no table regeneration)
# =============================================================================

SYS_PROMPT_DATAFRAME_CONTEXT_ONLY = """You are a data analyst assistant. Given a data summary, provide brief contextual insights.

RULES:
1. DO NOT output any table data, rows, or recreate the dataset
2. ONLY provide brief textual insights (2-3 sentences max)
3. Focus on: key observations, potential anomalies, suggested next questions
4. Keep response under 100 words

Respond with JSON:
{
    "summary": "Brief 1-sentence description of what the data shows",
    "insights": ["insight 1", "insight 2"],
    "suggested_questions": ["question 1"]
}
"""

SYS_PROMPT_MIXED_CONTENT_ANALYSIS = """Analyze this content and structure it for display.

CRITICAL RULES:
1. If you detect tabular data, return type "table_reference" with just the table description - DO NOT output the actual table rows
2. For text content, extract and structure normally
3. For code, identify language and structure
4. Keep your response minimal - the actual data rendering happens separately

Content types:
- text: Regular paragraphs
- table_reference: Reference to tabular data (DO NOT include actual rows)
- code: Programming code
- metrics: Key statistics (just the values, not tables)
- sql: SQL queries
- list: Bullet points

Respond with JSON:
{
    "blocks": [
        {"type": "text", "content": "...", "metadata": {}},
        {"type": "table_reference", "content": {"description": "...", "row_hint": 10}, "metadata": {}},
        {"type": "code", "content": "...", "metadata": {"language": "python"}}
    ],
    "has_embedded_table": true/false
}
"""


class SmartContentRendererHybrid:
    """
    Hybrid renderer that combines:
    1. Deterministic DataFrame/table rendering (instant)
    2. Optional AI contextual insights (fast, minimal tokens)
    """
    
    def __init__(self, enable_ai_insights: bool = True):
        """
        Args:
            enable_ai_insights: If True, adds AI-generated context to data responses.
                               If False, only does deterministic rendering (fastest).
        """
        self.enable_ai_insights = enable_ai_insights
        logger.info(f'SmartContentRendererHybrid initialized (AI insights: {enable_ai_insights})')
    
    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================
    
    def analyze_and_render(self, content: Union[str, pd.DataFrame, dict, list], 
                          context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Main entry point - routes to appropriate rendering strategy.
        """
        try:
            # -----------------------------------------------------------------
            # FAST PATH: DataFrame (deterministic + optional AI insights)
            # -----------------------------------------------------------------
            if isinstance(content, pd.DataFrame):
                return self._render_dataframe_hybrid(content, context)
            
            # -----------------------------------------------------------------
            # FAST PATH: Already structured content
            # -----------------------------------------------------------------
            if isinstance(content, dict) and 'type' in content:
                return content
            
            # -----------------------------------------------------------------
            # FAST PATH: List of dicts (tabular data)
            # -----------------------------------------------------------------
            if isinstance(content, list) and all(isinstance(item, dict) for item in content):
                return self._render_list_hybrid(content, context)
            
            # -----------------------------------------------------------------
            # STRING CONTENT: Check for simple vs complex
            # -----------------------------------------------------------------
            if isinstance(content, str):
                # Quick check for simple text (no LLM needed)
                if self._is_simple_text(content):
                    return self._create_text_block(content)
                
                # Check if string contains embedded DataFrame/table
                if self._contains_table_pattern(content):
                    return self._render_mixed_content(content, context)
                
                # Complex string content - use AI analysis
                return self._analyze_string_content_fast(content, context)
            
            # Fallback
            return self._create_text_block(str(content))
            
        except Exception as e:
            logger.error(f"Error in analyze_and_render: {str(e)}")
            return self._create_text_block(str(content))
    
    # =========================================================================
    # HYBRID DATAFRAME RENDERING
    # =========================================================================
    
    def _render_dataframe_hybrid(self, df: pd.DataFrame, 
                                  context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Render DataFrame with:
        1. Deterministic table block (instant)
        2. Optional AI-generated insights (fast, context-only)
        """
        blocks = []
        
        # -----------------------------------------------------------------
        # STEP 1: Deterministic analysis (instant)
        # -----------------------------------------------------------------
        analysis = self._analyze_dataframe_deterministic(df)
        
        # -----------------------------------------------------------------
        # STEP 2: Optional AI insights (fast - only if enabled)
        # -----------------------------------------------------------------
        if self.enable_ai_insights and len(df) > 0:
            insights = self._get_ai_insights_for_dataframe(df, analysis, context)
            if insights:
                # Add summary as first block
                if insights.get('summary'):
                    blocks.append({
                        "type": "text",
                        "content": insights['summary'],
                        "metadata": {"style": "summary", "ai_generated": True}
                    })
        
        # -----------------------------------------------------------------
        # STEP 3: Deterministic table rendering (instant)
        # -----------------------------------------------------------------
        table_block = self._create_table_block_deterministic(df, analysis)
        blocks.append(table_block)
        
        # -----------------------------------------------------------------
        # STEP 4: Add insights after table (if any)
        # -----------------------------------------------------------------
        if self.enable_ai_insights and 'insights' in locals() and insights:
            if insights.get('insights'):
                # Use 'list' type for array of insights
                blocks.append({
                    "type": "list",
                    "content": insights['insights'],
                    "metadata": {"ai_generated": True, "style": "insights"}
                })
            
            if insights.get('suggested_questions'):
                # Use 'text' type for suggested follow-up questions
                questions_text = "You might also ask: " + ", ".join(insights['suggested_questions'])
                blocks.append({
                    "type": "text",
                    "content": questions_text,
                    "metadata": {"style": "subtle", "ai_generated": True}
                })
        
        return {
            "type": "rich_content",
            "blocks": blocks,
            "metadata": {
                "source": "dataframe",
                "analysis": analysis,
                "rendering_mode": "hybrid"
            }
        }
    
    def _analyze_dataframe_deterministic(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Fast, deterministic DataFrame analysis (no LLM).
        Note: All values are converted to native Python types for JSON serialization.
        """
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        categorical_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
        datetime_cols = df.select_dtypes(include=['datetime64']).columns.tolist()
        
        # Check for nulls - convert numpy.bool_ to Python bool
        has_nulls = bool(df.isnull().any().any())
        
        # Get null counts - convert numpy.int64 to Python int
        null_counts = {}
        if has_nulls:
            null_counts = {k: int(v) for k, v in df.isnull().sum().to_dict().items() if v > 0}
        
        analysis = {
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "column_names": df.columns.tolist(),
            "numeric_columns": numeric_cols,
            "categorical_columns": categorical_cols,
            "datetime_columns": datetime_cols,
            "has_nulls": has_nulls,
            "null_counts": null_counts,
        }
        
        # Quick stats for numeric columns - ensure all values are native Python types
        if numeric_cols:
            analysis["numeric_summary"] = {}
            for col in numeric_cols[:5]:  # Limit to first 5 for performance
                col_min = df[col].min()
                col_max = df[col].max()
                col_mean = df[col].mean()
                analysis["numeric_summary"][col] = {
                    "min": float(col_min) if pd.notna(col_min) else None,
                    "max": float(col_max) if pd.notna(col_max) else None,
                    "mean": float(col_mean) if pd.notna(col_mean) else None,
                }
        
        return analysis
    
    def _sanitize_for_json(self, obj: Any) -> Any:
        """
        Recursively convert numpy types to native Python types for JSON serialization.
        """
        if isinstance(obj, dict):
            return {k: self._sanitize_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._sanitize_for_json(item) for item in obj]
        elif isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj) if not np.isnan(obj) else None
        elif isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif pd.isna(obj):
            return None
        else:
            return obj
    
    def _create_table_block_deterministic(self, df: pd.DataFrame, 
                                          analysis: Dict) -> Dict[str, Any]:
        """
        Create table block without LLM - pure data transformation.
        All values are sanitized for JSON serialization.
        """
        # Limit rows for large datasets
        max_rows = getattr(cfg, 'MAX_TABLE_DISPLAY_ROWS', 1000)
        truncated = bool(len(df) > max_rows)
        
        if truncated:
            display_df = df.head(max_rows)
        else:
            display_df = df
        
        # Convert to records and sanitize numpy types
        table_content = self._sanitize_for_json(display_df.to_dict('records'))
        
        return {
            "type": "table",
            "content": table_content,
            "metadata": {
                "columns": df.columns.tolist(),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "total_rows": int(len(df)),
                "displayed_rows": int(len(display_df)),
                "truncated": truncated,
                "sortable": True,
                "filterable": True,
                "exportable": True,
                "paginated": bool(len(display_df) > 50)
            }
        }
    
    def _get_ai_insights_for_dataframe(self, df: pd.DataFrame, 
                                        analysis: Dict,
                                        context: Optional[Dict] = None) -> Optional[Dict]:
        """
        Get AI-generated insights WITHOUT regenerating table data.
        Uses mini model and minimal prompt for speed.
        """
        try:
            # Build a compact summary for the LLM (not the full data!)
            summary_prompt = self._build_data_summary_prompt(df, analysis, context)
            
            # Use mini model for speed
            response = azureMiniQuickPrompt(
                prompt=summary_prompt,
                system=SYS_PROMPT_DATAFRAME_CONTEXT_ONLY,
                temp=0.3
            )
            
            # Parse response
            response = response.strip()
            if response.startswith('```'):
                response = response.split('```')[1]
                if response.startswith('json'):
                    response = response[4:]
            
            return json.loads(response)
            
        except Exception as e:
            logger.warning(f"AI insights failed, continuing without: {e}")
            return None
    
    def _build_data_summary_prompt(self, df: pd.DataFrame, 
                                    analysis: Dict,
                                    context: Optional[Dict] = None) -> str:
        """
        Build a compact prompt that describes the data WITHOUT including all rows.
        """
        query = context.get('query', 'No specific query') if context else 'No specific query'
        
        # Sample a few rows for context (not the whole dataset)
        sample_size = min(3, len(df))
        sample_str = df.head(sample_size).to_string(index=False) if sample_size > 0 else "Empty dataset"
        
        prompt = f"""User's question: {query}

Data summary:
- Rows: {analysis['rows']:,}
- Columns: {analysis['columns']} ({', '.join(analysis['column_names'][:10])}{' ...' if len(analysis['column_names']) > 10 else ''})
- Numeric columns: {', '.join(analysis['numeric_columns'][:5]) or 'None'}
- Has null values: {analysis['has_nulls']}

Sample (first {sample_size} rows):
{sample_str}

Provide brief insights about this data. Do NOT recreate the table."""

        return prompt
    
    # =========================================================================
    # MIXED CONTENT HANDLING (Text + Embedded Tables)
    # =========================================================================
    
    def _render_mixed_content(self, content: str, 
                              context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Handle content that has both text and embedded table data.
        Strategy: Extract table deterministically, only send text to LLM.
        """
        # Try to extract any embedded table
        extracted_table, remaining_text = self._extract_embedded_table(content)
        
        blocks = []
        
        # If we extracted a table, render it deterministically
        if extracted_table is not None:
            if isinstance(extracted_table, pd.DataFrame):
                table_block = self._create_table_block_deterministic(
                    extracted_table, 
                    self._analyze_dataframe_deterministic(extracted_table)
                )
                blocks.append(table_block)
        
        # For remaining text, use fast AI analysis (or simple text block)
        if remaining_text.strip():
            if self._is_simple_text(remaining_text):
                blocks.insert(0, {
                    "type": "text",
                    "content": remaining_text.strip(),
                    "metadata": {}
                })
            else:
                # Analyze remaining text with AI
                text_analysis = self._analyze_string_content_fast(remaining_text, context)
                if text_analysis.get('blocks'):
                    # Insert text blocks before table
                    blocks = text_analysis['blocks'] + blocks
        
        return {
            "type": "rich_content",
            "blocks": blocks,
            "metadata": {"mixed_content": True}
        }
    
    def _extract_embedded_table(self, content: str) -> tuple:
        """
        Extract embedded table data from string content.
        Returns: (DataFrame or None, remaining_text)
        """
        import re
        
        # Pattern 1: Markdown table
        md_table_pattern = r'(\|[^\n]+\|\n\|[-:| ]+\|\n(?:\|[^\n]+\|\n?)+)'
        md_match = re.search(md_table_pattern, content)
        
        if md_match:
            table_str = md_match.group(1)
            remaining = content.replace(table_str, '').strip()
            df = self._parse_markdown_table(table_str)
            return df, remaining
        
        # Pattern 2: Whitespace-aligned table (simple heuristic)
        lines = content.split('\n')
        table_lines = []
        text_lines = []
        in_table = False
        
        for line in lines:
            # Detect table-like lines (multiple whitespace-separated columns)
            if '  ' in line and len(line.split()) >= 3:
                in_table = True
                table_lines.append(line)
            elif in_table and line.strip() == '':
                # End of table
                in_table = False
            elif in_table:
                table_lines.append(line)
            else:
                text_lines.append(line)
        
        if len(table_lines) >= 2:
            df = self._parse_whitespace_table(table_lines)
            remaining = '\n'.join(text_lines)
            return df, remaining
        
        return None, content
    
    def _parse_markdown_table(self, table_str: str) -> Optional[pd.DataFrame]:
        """Parse markdown table into DataFrame."""
        try:
            lines = [l.strip() for l in table_str.strip().split('\n') if l.strip()]
            if len(lines) < 2:
                return None
            
            # Parse headers
            headers = [h.strip() for h in lines[0].split('|') if h.strip()]
            
            # Skip separator line, parse data
            data_rows = []
            for line in lines[2:]:
                row = [cell.strip() for cell in line.split('|') if cell.strip()]
                if len(row) == len(headers):
                    data_rows.append(row)
            
            return pd.DataFrame(data_rows, columns=headers)
        except:
            return None
    
    def _parse_whitespace_table(self, lines: List[str]) -> Optional[pd.DataFrame]:
        """Parse whitespace-aligned table into DataFrame."""
        try:
            # Assume first line is headers
            headers = lines[0].split()
            data_rows = [line.split() for line in lines[1:] if line.strip()]
            
            # Filter rows that match header count
            data_rows = [row for row in data_rows if len(row) == len(headers)]
            
            if data_rows:
                return pd.DataFrame(data_rows, columns=headers)
            return None
        except:
            return None
    
    # =========================================================================
    # FAST STRING ANALYSIS
    # =========================================================================
    
    def _analyze_string_content_fast(self, content: str, 
                                      context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Analyze string content with AI, but using optimized prompt.
        """
        try:
            query = context.get('query', '') if context else ''
            
            # Truncate very long content
            max_content_len = 4000
            truncated_content = content[:max_content_len] if len(content) > max_content_len else content
            
            prompt = f"""User's question: {query}

Analyze and structure this content (do NOT recreate any tables, just reference them):
{truncated_content}"""

            response = azureMiniQuickPrompt(
                prompt=prompt,
                system=SYS_PROMPT_MIXED_CONTENT_ANALYSIS,
                temp=0.1
            )
            
            # Parse response
            response = response.strip()
            if response.startswith('```'):
                response = response.split('```')[1]
                if response.startswith('json'):
                    response = response[4:]
            
            result = json.loads(response)
            return self._process_ai_analysis(result, content)
            
        except Exception as e:
            logger.warning(f"Fast string analysis failed: {e}")
            return self._create_text_block(content)
    
    def _process_ai_analysis(self, analysis: Dict, original_content: str) -> Dict[str, Any]:
        """
        Process AI analysis, handling table_reference types specially.
        """
        blocks = analysis.get('blocks', [])
        processed_blocks = []
        
        for block in blocks:
            block_type = block.get('type', 'text')
            
            # Handle table_reference - the AI didn't output table data
            if block_type == 'table_reference':
                # If there's embedded table data, we would have already extracted it
                # Just add a note that table is rendered separately
                processed_blocks.append({
                    "type": "text",
                    "content": block.get('content', {}).get('description', 'Data table'),
                    "metadata": {"style": "table_header"}
                })
            else:
                processed_blocks.append(block)
        
        return {
            "type": "rich_content",
            "blocks": processed_blocks,
            "metadata": {"ai_analyzed": True}
        }
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _is_simple_text(self, content: str) -> bool:
        """Quick check if content is simple text that doesn't need AI."""
        if len(content) < 300:
            complex_patterns = ['|', '```', 'SELECT ', 'INSERT ', '{', '1. ', '- ', '* ', '\t']
            return not any(p in content for p in complex_patterns)
        return False
    
    def _contains_table_pattern(self, content: str) -> bool:
        """Check if content likely contains table data."""
        # Markdown table
        if '|' in content and content.count('|') >= 4:
            return True
        # Whitespace-aligned table
        lines = content.split('\n')
        aligned_lines = sum(1 for l in lines if '  ' in l and len(l.split()) >= 3)
        if aligned_lines >= 3:
            return True
        return False
    
    def _create_text_block(self, content: str) -> Dict[str, Any]:
        """Create a simple text block."""
        return {
            "type": "rich_content",
            "blocks": [{
                "type": "text",
                "content": content,
                "metadata": {}
            }]
        }
    
    def _render_list_hybrid(self, data: List[Dict], 
                            context: Optional[Dict] = None) -> Dict[str, Any]:
        """Render list of dicts as table (deterministic)."""
        if not data:
            return self._create_text_block("No data available")
        
        df = pd.DataFrame(data)
        return self._render_dataframe_hybrid(df, context)


# =============================================================================
# CONFIGURATION OPTIONS
# =============================================================================

class SmartRenderConfig:
    """Configuration for smart rendering behavior."""
    
    # Feature flags
    ENABLE_AI_INSIGHTS = cfg.SMART_RENDER_HYBRID_ENABLE_AI_INSIGHTS                 # Add AI-generated insights to data
    ENABLE_VIZ_SUGGESTIONS = cfg.SMART_RENDER_HYBRID_ENABLE_VIZ_SUGGESTIONS         # Suggest visualizations
    
    # Performance tuning
    AI_INSIGHTS_MAX_ROWS = cfg.SMART_RENDER_HYBRID_AI_INSIGHTS_MAX_ROWS             # Skip AI insights for very large datasets
    MAX_TABLE_DISPLAY_ROWS = cfg.SMART_RENDER_HYBRID_MAX_TABLE_DISPLAY_ROWS         # Truncate displayed rows
    MAX_CONTENT_FOR_AI = cfg.SMART_RENDER_HYBRID_MAX_CONTENT_FOR_AI                 # Max chars to send to AI
    
    # Model selection
    USE_MINI_MODEL = cfg.SMART_RENDER_HYBRID_USE_MINI_MODEL                         # Use faster mini model for insights


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_smart_renderer(mode: str = "hybrid") -> SmartContentRendererHybrid:
    """
    Factory function to create renderer with appropriate settings.
    
    Args:
        mode: 
            "fast" - No AI insights, pure deterministic (fastest)
            "hybrid" - Deterministic tables + AI insights (balanced)
            "full" - Full AI analysis (slowest, most context)
    """
    if mode == "fast":
        return SmartContentRendererHybrid(enable_ai_insights=False)
    elif mode == "hybrid":
        return SmartContentRendererHybrid(enable_ai_insights=True)
    else:
        return SmartContentRendererHybrid(enable_ai_insights=True)
