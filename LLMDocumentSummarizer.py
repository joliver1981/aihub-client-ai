# Document Page Summarization Enhancement
# This module adds AI-powered page summarization functionality to your existing document processing pipeline

import logging
import json
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
import os
import config as cfg
from CommonUtils import get_db_connection, AnthropicProxyClient
from api_keys_config import get_anthropic_config
from anthropic_streaming_helper import anthropic_messages_create


class DocumentPageSummarizer:
    """
    Handles AI-powered summarization of document pages using Claude.
    Integrates with the existing LLMDocumentEngine pipeline.
    """
    
    def __init__(self, anthropic_client=None, logger=None, anthropic_config=None, anthropic_proxy_client=None):
        """
        Initialize the document page summarizer.
        
        Args:
            anthropic_client: Initialized Anthropic API client
            logger: Optional logger instance
            anthropic_config: Configuration dict from get_anthropic_config()
            anthropic_proxy_client: AnthropicProxyClient instance for proxy mode
        """
        self.anthropic_client = anthropic_client
        self.logger = logger or logging.getLogger("DocumentPageSummarizer")
        self.anthropic_proxy_client = anthropic_proxy_client
        
        # Store config, default to direct API if client provided and no config
        if anthropic_config is None:
            self._anthropic_config = {
                'use_direct_api': anthropic_client is not None,
                'source': 'legacy'
            }
        else:
            self._anthropic_config = anthropic_config
        
        self._ensure_database_schema()
    
    def _ensure_database_schema(self):
        """Ensure the database has the necessary tables for storing summaries"""
        try:
            conn = get_db_connection()
            return
            if not conn:
                self.logger.error("Could not establish database connection")
                return
                
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Create DocumentPageSummaries table if it doesn't exist
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'DocumentPageSummaries')
            BEGIN
                CREATE TABLE DocumentPageSummaries (
                    summary_id INT IDENTITY(1,1) PRIMARY KEY,
                    page_id VARCHAR(100) NOT NULL,
                    document_id VARCHAR(100) NOT NULL,
                    page_number INT NOT NULL,
                    summary_type VARCHAR(50) NOT NULL DEFAULT 'standard',
                    summary_text NVARCHAR(MAX) NOT NULL,
                    key_points NVARCHAR(MAX) NULL,
                    entities NVARCHAR(MAX) NULL,
                    confidence_score FLOAT NULL,
                    processing_time_ms INT NULL,
                    created_at DATETIME NOT NULL DEFAULT getutcdate(),
                    model_used VARCHAR(100) NULL,
                    TenantId INT NULL DEFAULT (CONVERT(NVARCHAR(50), SESSION_CONTEXT(N'TenantId'))),
                    FOREIGN KEY (page_id) REFERENCES DocumentPages(page_id),
                    FOREIGN KEY (document_id) REFERENCES Documents(document_id)
                )
            END
            """)
            
            # Add indexes for better performance
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_DocumentPageSummaries_PageId')
            BEGIN
                CREATE INDEX IX_DocumentPageSummaries_PageId ON DocumentPageSummaries(page_id)
            END
            """)
            
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_DocumentPageSummaries_DocumentId')
            BEGIN
                CREATE INDEX IX_DocumentPageSummaries_DocumentId ON DocumentPageSummaries(document_id)
            END
            """)
            
            conn.commit()
            self.logger.info("DocumentPageSummaries table schema verified/created")
            
        except Exception as e:
            self.logger.error(f"Error ensuring database schema: {str(e)}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    
    def summarize_page(
        self, 
        page_content: str, 
        document_type: str = "document",
        summary_type: str = "standard",
        custom_instructions: str = None
    ) -> Dict[str, Any]:
        """
        Generate an AI summary for a single page of content.
        
        Args:
            page_content: The full text content of the page
            document_type: Type of document for context-aware summarization
            summary_type: Type of summary ('standard', 'brief', 'detailed', 'bullet_points', 'executive')
            custom_instructions: Additional instructions for summarization
            
        Returns:
            Dictionary containing summary data
        """
        start_time = time.time()
        
        try:
            print('Building system prompt...')
            # Build the system prompt based on document type and summary type
            system_prompt = self._build_system_prompt(document_type, summary_type, custom_instructions)
            
            print('Building user prompt...')
            # Build the user prompt
            user_prompt = self._build_user_prompt(page_content, document_type, summary_type)
            
            # Call Claude API
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt
                        }
                    ]
                }
            ]
            
            if self._anthropic_config['use_direct_api']:
                print(f"Calling Anthropic model directly (source: {self._anthropic_config['source']})...")
                # Use streaming wrapper (required for newer Anthropic models)
                response = anthropic_messages_create(
                    client=self.anthropic_client,
                    model=cfg.ANTHROPIC_MODEL,
                    max_tokens=int(cfg.ANTHROPIC_MAX_TOKENS),
                    system=system_prompt,
                    messages=messages,
                    temperature=0.1  # Low temperature for consistent summaries
                )
                ai_response = response.content[0].text
            else:
                print('Calling Anthropic model via proxy...')
                if self.anthropic_proxy_client:
                    client = self.anthropic_proxy_client
                else:
                    client = AnthropicProxyClient()
                response = client.messages_create(
                    messages=messages,
                    model=cfg.ANTHROPIC_MODEL,
                    max_tokens=int(cfg.ANTHROPIC_MAX_TOKENS),
                    system=system_prompt,
                    temperature=0.1
                )
                ai_response = response['content'][0]['text']
            
            print('Parsing AI response...')
            # Parse the AI response
            summary_data = self._parse_summary_response(ai_response, summary_type)
            
            # Add metadata
            processing_time = int((time.time() - start_time) * 1000)
            summary_data.update({
                'processing_time_ms': processing_time,
                'model_used': cfg.ANTHROPIC_MODEL,
                'summary_type': summary_type,
                'created_at': datetime.now()
            })
            
            return summary_data
            
        except Exception as e:
            self.logger.error(f"Error generating page summary: {str(e)}")
            return {
                'summary_text': f"Error generating summary: {str(e)}",
                'key_points': None,
                'entities': None,
                'confidence_score': 0.0,
                'processing_time_ms': int((time.time() - start_time) * 1000),
                'model_used': cfg.ANTHROPIC_MODEL,
                'summary_type': summary_type,
                'created_at': datetime.now()
            }
    
    def _build_system_prompt(self, document_type: str, summary_type: str, custom_instructions: str = None) -> str:
        """Build the system prompt for Claude based on document type and summary preferences"""
        
        base_prompt = f"""You are an expert document analyst specializing in summarizing {document_type} documents. 
Your task is to create accurate, concise, and useful summaries that capture the essential information.

Document Type Context: {document_type}
Summary Type: {summary_type}

General Guidelines:
- Focus on the most important information and key insights
- Maintain factual accuracy and avoid speculation
- Use clear, professional language
- Structure your response as requested
- Extract key entities (people, organizations, dates, amounts, etc.)
- Assess your confidence in the summary quality
"""
        
        # Add summary-type specific instructions
        if summary_type == "brief":
            base_prompt += "\n- Provide a very concise summary (2-3 sentences maximum)"
        elif summary_type == "detailed":
            base_prompt += "\n- Provide a comprehensive summary covering all important aspects"
        elif summary_type == "bullet_points":
            base_prompt += "\n- Structure the summary as clear bullet points"
        elif summary_type == "executive":
            base_prompt += "\n- Focus on key decisions, actions, and business-critical information"
        
        # Add document-type specific guidance
        if document_type.lower() in ["invoice", "bill", "receipt"]:
            base_prompt += "\n- Pay special attention to amounts, dates, vendor/customer information, and line items"
        elif document_type.lower() in ["contract", "agreement"]:
            base_prompt += "\n- Focus on parties involved, key terms, obligations, and important dates"
        elif document_type.lower() in ["report", "analysis"]:
            base_prompt += "\n- Highlight key findings, conclusions, and recommendations"
        elif document_type.lower() in ["email", "correspondence"]:
            base_prompt += "\n- Identify sender/recipients, main topics, and any action items"
        
        # Add custom instructions if provided
        if custom_instructions:
            base_prompt += f"\n\nAdditional Instructions: {custom_instructions}"
        
        return base_prompt
    
    def _build_user_prompt(self, page_content: str, document_type: str, summary_type: str) -> str:
        """Build the user prompt for the summarization request"""
        
        prompt = f"""Please analyze and summarize the following {document_type} page content.

Return your response in JSON format with the following structure:
{{
    "summary_text": "The main summary of the content",
    "key_points": ["List", "of", "key", "points"],
    "entities": {{
        "people": ["person names"],
        "organizations": ["company/org names"],
        "dates": ["important dates"],
        "amounts": ["monetary amounts or quantities"],
        "locations": ["places mentioned"],
        "other": ["other important entities"]
    }},
    "confidence_score": 0.95
}}

Content to summarize:
{page_content}
"""
        return prompt
    
    def _parse_summary_response(self, ai_response: str, summary_type: str) -> Dict[str, Any]:
        """Parse the AI response and extract structured summary data"""
        
        try:
            # Try to extract JSON from the response
            import re
            json_match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', ai_response)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Look for JSON without code blocks
                json_match = re.search(r'\{[\s\S]*\}', ai_response)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    # Fallback: treat entire response as summary text
                    return {
                        'summary_text': ai_response.strip(),
                        'key_points': None,
                        'entities': None,
                        'confidence_score': 0.8
                    }
            
            # Parse JSON
            summary_data = json.loads(json_str)
            
            # Ensure required fields exist
            result = {
                'summary_text': summary_data.get('summary_text', ''),
                'key_points': json.dumps(summary_data.get('key_points')) if summary_data.get('key_points') else None,
                'entities': json.dumps(summary_data.get('entities')) if summary_data.get('entities') else None,
                'confidence_score': summary_data.get('confidence_score', 0.8)
            }
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error parsing summary response: {str(e)}")
            # Fallback: use the raw response as summary text
            return {
                'summary_text': ai_response.strip(),
                'key_points': None,
                'entities': None,
                'confidence_score': 0.5
            }
    
    def save_page_summary(
        self, 
        page_id: str, 
        document_id: str, 
        page_number: int, 
        summary_data: Dict[str, Any]
    ) -> bool:
        """
        Save the page summary to the database.
        
        Args:
            page_id: ID of the page
            document_id: ID of the document
            page_number: Page number
            summary_data: Summary data dictionary
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = get_db_connection()
            if not conn:
                self.logger.error("Could not establish database connection")
                return False
                
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Insert the summary
            cursor.execute("""
                INSERT INTO DocumentPageSummaries 
                (page_id, document_id, page_number, summary_type, summary_text, 
                 key_points, entities, confidence_score, processing_time_ms, model_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                page_id,
                document_id,
                page_number,
                summary_data.get('summary_type', 'standard'),
                summary_data.get('summary_text', ''),
                summary_data.get('key_points'),
                summary_data.get('entities'),
                summary_data.get('confidence_score', 0.0),
                summary_data.get('processing_time_ms', 0),
                summary_data.get('model_used', cfg.ANTHROPIC_MODEL)
            ))
            
            conn.commit()
            self.logger.info(f"Saved summary for page {page_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving page summary: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()
    
    def get_page_summary(self, page_id: str, summary_type: str = None) -> Optional[Dict[str, Any]]:
        """
        Retrieve a page summary from the database.
        
        Args:
            page_id: ID of the page
            summary_type: Optional filter by summary type
            
        Returns:
            Summary data dictionary or None if not found
        """
        try:
            conn = get_db_connection()
            if not conn:
                return None
                
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Build query
            query = """
                SELECT summary_id, page_id, document_id, page_number, summary_type,
                       summary_text, key_points, entities, confidence_score,
                       processing_time_ms, created_at, model_used
                FROM DocumentPageSummaries 
                WHERE page_id = ?
            """
            params = [page_id]
            
            if summary_type:
                query += " AND summary_type = ?"
                params.append(summary_type)
            
            query += " ORDER BY created_at DESC"
            
            cursor.execute(query, params)
            row = cursor.fetchone()
            
            if row:
                return {
                    'summary_id': row[0],
                    'page_id': row[1],
                    'document_id': row[2],
                    'page_number': row[3],
                    'summary_type': row[4],
                    'summary_text': row[5],
                    'key_points': json.loads(row[6]) if row[6] else None,
                    'entities': json.loads(row[7]) if row[7] else None,
                    'confidence_score': row[8],
                    'processing_time_ms': row[9],
                    'created_at': row[10],
                    'model_used': row[11]
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error retrieving page summary: {str(e)}")
            return None
        finally:
            if conn:
                conn.close()
    
    def get_document_summaries(self, document_id: str, summary_type: str = None) -> List[Dict[str, Any]]:
        """
        Retrieve all page summaries for a document.
        
        Args:
            document_id: ID of the document
            summary_type: Optional filter by summary type
            
        Returns:
            List of summary data dictionaries
        """
        try:
            conn = get_db_connection()
            if not conn:
                return []
                
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Build query
            query = """
                SELECT summary_id, page_id, document_id, page_number, summary_type,
                       summary_text, key_points, entities, confidence_score,
                       processing_time_ms, created_at, model_used
                FROM DocumentPageSummaries 
                WHERE document_id = ?
            """
            params = [document_id]
            
            if summary_type:
                query += " AND summary_type = ?"
                params.append(summary_type)
            
            query += " ORDER BY page_number, created_at DESC"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            summaries = []
            for row in rows:
                summaries.append({
                    'summary_id': row[0],
                    'page_id': row[1],
                    'document_id': row[2],
                    'page_number': row[3],
                    'summary_type': row[4],
                    'summary_text': row[5],
                    'key_points': json.loads(row[6]) if row[6] else None,
                    'entities': json.loads(row[7]) if row[7] else None,
                    'confidence_score': row[8],
                    'processing_time_ms': row[9],
                    'created_at': row[10],
                    'model_used': row[11]
                })
            
            return summaries
            
        except Exception as e:
            self.logger.error(f"Error retrieving document summaries: {str(e)}")
            return []
        finally:
            if conn:
                conn.close()
