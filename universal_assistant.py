"""
Universal Assistant Framework - Enhanced Version
Provides context-aware assistance across all application pages.

Key Features:
- Automatic page context awareness (from frontend DOM extraction)
- Works on ANY page without requiring documentation
- Optional page-specific documentation enhances responses
- In-memory session management (no database required)

The assistant receives comprehensive page context including:
- Page URL, title, and detected page name
- Visible sections, forms, buttons, and tables
- Selected items and active states
- Custom page context if defined
"""

import os
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class Message:
    """Single message in conversation"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            'role': self.role,
            'content': self.content,
            'timestamp': self.timestamp.isoformat()
        }


# =============================================================================
# In-Memory Session Manager
# =============================================================================

class SessionManager:
    """
    Manages conversation sessions in memory.
    Automatically cleans up old sessions to prevent memory bloat.
    """
    
    def __init__(self, 
                 max_sessions: int = 500, 
                 session_ttl_hours: int = 24, 
                 max_messages_per_session: int = 50):
        self.sessions: Dict[str, deque] = {}
        self.session_metadata: Dict[str, Dict] = {}
        self.max_sessions = max_sessions
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self.max_messages = max_messages_per_session
        
    def get_or_create_session(self, session_id: str) -> deque:
        """Get existing session or create new one"""
        self._cleanup_if_needed()
        
        if session_id not in self.sessions:
            self.sessions[session_id] = deque(maxlen=self.max_messages)
            self.session_metadata[session_id] = {
                'created_at': datetime.now(),
                'last_accessed': datetime.now()
            }
        else:
            self.session_metadata[session_id]['last_accessed'] = datetime.now()
            
        return self.sessions[session_id]
    
    def add_message(self, session_id: str, role: str, content: str):
        """Add a message to session"""
        session = self.get_or_create_session(session_id)
        session.append(Message(role=role, content=content))
        
    def get_history(self, session_id: str, max_messages: int = 10) -> List[Dict]:
        """Get recent conversation history"""
        if session_id not in self.sessions:
            return []
        
        messages = list(self.sessions[session_id])[-max_messages:]
        return [msg.to_dict() for msg in messages]
    
    def get_context_summary(self, session_id: str, max_turns: int = 5) -> str:
        """Get conversation summary for context injection"""
        if session_id not in self.sessions:
            return ""
        
        messages = list(self.sessions[session_id])[-max_turns * 2:]
        
        if not messages:
            return ""
        
        summary_parts = []
        for msg in messages:
            prefix = "User" if msg.role == 'user' else "Assistant"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            summary_parts.append(f"{prefix}: {content}")
        
        return "\n".join(summary_parts)
    
    def clear_session(self, session_id: str):
        """Clear a specific session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
        if session_id in self.session_metadata:
            del self.session_metadata[session_id]
    
    def _cleanup_if_needed(self):
        """Clean up old sessions if over limit"""
        now = datetime.now()
        
        expired = [
            sid for sid, meta in self.session_metadata.items()
            if now - meta['last_accessed'] > self.session_ttl
        ]
        
        for sid in expired:
            self.clear_session(sid)
        
        if len(self.sessions) > self.max_sessions:
            sorted_sessions = sorted(
                self.session_metadata.items(),
                key=lambda x: x[1]['last_accessed']
            )
            to_remove = max(1, len(sorted_sessions) // 10)
            for sid, _ in sorted_sessions[:to_remove]:
                self.clear_session(sid)


# =============================================================================
# Documentation Manager
# =============================================================================

class DocumentationManager:
    """
    Loads and caches documentation from markdown files.
    Documentation is OPTIONAL - the assistant works without it.
    """
    
    def __init__(self, docs_dir: str = "assistant_docs"):
        self.docs_dir = Path(docs_dir)
        self.cache: Dict[str, str] = {}
        self.cache_timestamps: Dict[str, datetime] = {}
        self.cache_ttl = timedelta(minutes=30)
        
        self._ensure_directory_structure()
        
    def _ensure_directory_structure(self):
        """Create basic directory structure"""
        if not self.docs_dir.exists():
            self.docs_dir.mkdir(parents=True, exist_ok=True)
            
        core_dir = self.docs_dir / 'core'
        core_dir.mkdir(exist_ok=True)
        
        pages_dir = self.docs_dir / 'pages'
        pages_dir.mkdir(exist_ok=True)
        
    def get_documentation(self, page: str, user_role: str = "user") -> Optional[str]:
        """
        Get documentation for a specific page.
        Returns None if no documentation exists (which is fine).
        """
        cache_key = f"{page}:{user_role}"
        
        if cache_key in self.cache:
            if datetime.now() - self.cache_timestamps.get(cache_key, datetime.min) < self.cache_ttl:
                return self.cache[cache_key]
        
        docs = []
        
        # Core docs (if they exist)
        core_doc = self._load_doc(self.docs_dir / 'core' / 'general.md')
        if core_doc:
            docs.append(core_doc)
        
        # Page-specific docs (folder structure)
        page_dir = self.docs_dir / 'pages' / page
        if page_dir.exists():
            for md_file in sorted(page_dir.glob('*.md')):
                doc_content = self._load_doc(md_file)
                if doc_content:
                    docs.append(doc_content)
        
        # Single-file page doc (alternative)
        single_file = self.docs_dir / 'pages' / f'{page}.md'
        if single_file.exists():
            doc_content = self._load_doc(single_file)
            if doc_content:
                docs.append(doc_content)
        
        if not docs:
            return None
        
        combined = "\n\n---\n\n".join(docs)
        self.cache[cache_key] = combined
        self.cache_timestamps[cache_key] = datetime.now()
        
        return combined
    
    def _load_doc(self, path: Path) -> str:
        """Load a single documentation file"""
        try:
            if path.exists():
                content = path.read_text(encoding='utf-8')
                if content.strip() and '<!-- placeholder -->' not in content.lower():
                    return content
        except Exception as e:
            logger.error(f"Error loading doc {path}: {e}")
        return ""
    
    def reload_cache(self):
        """Force reload all documentation"""
        self.cache.clear()
        self.cache_timestamps.clear()


# =============================================================================
# Enhanced Prompt Builder - Context Aware
# =============================================================================

class PromptBuilder:
    """
    Builds system and user prompts with comprehensive context.
    Works with auto-extracted page context even without documentation.
    """
    
    # Base system prompt - establishes AI identity and behavior
    BASE_SYSTEM_PROMPT = """You are an AI Assistant integrated into AI Hub, an enterprise platform for building custom AI agents, workflows, and data tools.

## YOUR CAPABILITIES
You are directly connected to the application and can see:
- What page the user is currently viewing
- The sections, forms, buttons, and data visible on the page
- Any items the user has selected or is working with
- Real-time page state and configuration

## IMPORTANT: DATA PRIORITY
When answering questions about page content (like selected tools, configurations, or settings):
1. ALWAYS prioritize "Page-Specific Data (AUTHORITATIVE)" if present - this is extracted directly from the page's internal state and is the most accurate
2. Only fall back to general page context if page-specific data is not available
3. Do NOT guess or infer tool selections - only report what is explicitly shown in the page-specific data

## YOUR ROLE
- Help users understand and use the current page effectively
- Provide specific, actionable guidance based on what you can see
- Answer questions about visible elements, buttons, and forms
- Suggest next steps based on the user's current context
- Troubleshoot issues by analyzing the current state

## SCOPE & BOUNDARIES
You are a **page assistant** — your job is to help with the page the user is currently viewing in AI Hub.

**You should NOT:**
- Build entire agents, workflows, or solutions on behalf of the user
- Answer general knowledge questions unrelated to AI Hub (e.g., trivia, coding help, personal advice)
- Act as a general-purpose chatbot or replacement for ChatGPT/Claude

**If a user asks something outside your scope**, politely let them know:
- For general questions: "I'm here to help you navigate and use this page in AI Hub. For general questions, you might want to use a dedicated AI assistant like ChatGPT or Claude."
- For requests to build things: "I can guide you through the steps on this page, but I'm not able to build the solution for you. Let me walk you through what you need to do here."

Keep it friendly — don't be preachy or repeat the boundary message if the user has already acknowledged it.

## RESPONSE GUIDELINES
- Be conversational but informative
- Reference specific elements you can see when relevant
- If you see potential issues (empty required fields, missing configurations), mention them
- Provide step-by-step guidance when explaining how to do something
- Use bullet points for lists of steps or options
- If you're unsure about something specific, say so and suggest what might help

## ABOUT AI HUB
AI Hub allows users to:
- Create custom AI agents with specific tools and knowledge
- Build automated workflows using visual node-based design
- Connect to databases and external services
- Process and analyze documents
- Schedule automated jobs
- Manage environments and configurations
"""

    def build(self, 
              question: str,
              page_data: Dict[str, Any],
              documentation: Optional[str] = None,
              conversation_history: str = "") -> Tuple[str, str]:
        """
        Build system prompt and user prompt with full context.
        
        Args:
            question: User's question
            page_data: Full context from frontend (auto + custom)
            documentation: Optional page-specific documentation
            conversation_history: Recent conversation for context
            
        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        # Extract auto context and custom context
        auto_context = page_data.get('auto', {})
        custom_context = page_data.get('custom', {})
        
        # Build the page context section
        page_context_section = self._build_page_context_section(auto_context, custom_context)
        
        # Build system prompt
        system_prompt_parts = [self.BASE_SYSTEM_PROMPT]
        
        # Add current page context
        system_prompt_parts.append(f"\n## CURRENT PAGE CONTEXT\n{page_context_section}")
        
        # Add documentation if available
        if documentation:
            system_prompt_parts.append(f"\n## PAGE DOCUMENTATION\n{documentation}")
        else:
            system_prompt_parts.append("""
## NOTE
No specific documentation is available for this page, but you can still help by:
- Describing what you see on the page
- Explaining the purpose of visible elements based on their labels
- Providing general guidance about similar features in applications
- Suggesting what the user might want to do based on the page context
""")
        
        system_prompt = "\n".join(system_prompt_parts)
        
        # Build user prompt
        user_prompt_parts = []
        
        if conversation_history:
            user_prompt_parts.append(f"## Recent Conversation\n{conversation_history}\n")
        
        user_prompt_parts.append(f"## User Question\n{question}")
        
        user_prompt = "\n".join(user_prompt_parts)
        
        return system_prompt, user_prompt
    
    def _build_page_context_section(self, auto_context: Dict, custom_context: Dict) -> str:
        """Build a detailed page context section from extracted data"""
        parts = []
        
        # Page identification
        page_name = auto_context.get('pageName', 'Unknown Page')
        url = auto_context.get('url', '')
        
        parts.append(f"**Current Page:** {page_name}")
        if url:
            parts.append(f"**URL Path:** {url}")
        
        # Active modal
        modal_info = auto_context.get('modalOpen', {})
        if modal_info.get('isOpen'):
            parts.append(f"**Active Dialog:** {modal_info.get('title', 'Open dialog')}")
        
        # Active tab
        active_tab = auto_context.get('activeTab')
        if active_tab:
            parts.append(f"**Active Tab:** {active_tab}")
        
        # Sections visible
        sections = auto_context.get('sections', [])
        if sections:
            parts.append(f"**Visible Sections:** {', '.join(sections[:10])}")
        
        # Form fields
        forms = auto_context.get('forms', [])
        if forms:
            field_summary = []
            for f in forms[:15]:
                label = f.get('label', 'Unknown')
                field_type = f.get('type', 'text')
                has_value = f.get('hasValue', False)
                selected = f.get('selectedOption', '')
                
                if selected:
                    field_summary.append(f"{label} ({field_type}, selected: {selected})")
                elif has_value:
                    field_summary.append(f"{label} ({field_type}, has value)")
                else:
                    field_summary.append(f"{label} ({field_type})")
            
            parts.append(f"**Form Fields:** {'; '.join(field_summary)}")
        
        # Buttons/Actions
        buttons = auto_context.get('buttons', [])
        if buttons:
            btn_names = [b.get('text', '') for b in buttons[:12]]
            parts.append(f"**Available Actions:** {', '.join(btn_names)}")
        
        # Tables
        tables = auto_context.get('tables', [])
        if tables:
            for i, table in enumerate(tables[:3]):
                columns = table.get('columns', [])
                row_count = table.get('rowCount', 0)
                if columns:
                    parts.append(f"**Data Table {i+1}:** {row_count} rows with columns: {', '.join(columns[:8])}")
        
        # Selected items
        selected = auto_context.get('selectedItems', [])
        if selected:
            parts.append(f"**Selected/Active Items:** {', '.join(selected[:5])}")
        
        # Custom context from page-specific getPageData()
        if custom_context:
            custom_summary = self._summarize_custom_context(custom_context)
            if custom_summary:
                parts.append(f"\n**Page-Specific Data (AUTHORITATIVE - use this for accurate information):**\n{custom_summary}")
        
        return "\n".join(parts)
    
    def _summarize_custom_context(self, context: Dict, max_depth: int = 2) -> str:
        """Summarize custom page context into readable format"""
        if not context:
            return ""
        
        lines = []
        
        for key, value in context.items():
            if key.startswith('_'):
                continue
                
            # Format key nicely
            display_key = key.replace('_', ' ').replace('-', ' ').title()
            
            if isinstance(value, bool):
                lines.append(f"  - {display_key}: {'Yes' if value else 'No'}")
            elif isinstance(value, str):
                if len(value) > 100:
                    lines.append(f"  - {display_key}: {value[:100]}...")
                elif value:
                    lines.append(f"  - {display_key}: {value}")
            elif isinstance(value, (int, float)):
                lines.append(f"  - {display_key}: {value}")
            elif isinstance(value, list):
                if len(value) > 0:
                    # Try to extract meaningful names from list items
                    if all(isinstance(x, str) for x in value[:5]):
                        # Simple string list
                        items_str = ', '.join(str(x) for x in value[:10])
                        if len(value) > 10:
                            items_str += f" (and {len(value) - 10} more)"
                        lines.append(f"  - {display_key}: {items_str}")
                    elif all(isinstance(x, dict) for x in value[:5]):
                        # List of objects - check if these are tools with descriptions
                        has_descriptions = any(x.get('description') for x in value[:3])
                        
                        if has_descriptions:
                            # Format as detailed tool list
                            lines.append(f"  - {display_key}:")
                            for item in value[:15]:
                                name = item.get('name') or item.get('displayName') or item.get('title') or item.get('toolId', '')
                                desc = item.get('description', '')
                                category = item.get('category', '')
                                if name:
                                    if desc:
                                        lines.append(f"      • {name}: {desc}")
                                    else:
                                        lines.append(f"      • {name}")
                            if len(value) > 15:
                                lines.append(f"      (and {len(value) - 15} more)")
                        else:
                            # Just extract names
                            names = []
                            for item in value[:15]:
                                name = (item.get('name') or item.get('displayName') or 
                                       item.get('title') or item.get('toolId') or 
                                       item.get('tool_name') or item.get('label') or '')
                                if name:
                                    names.append(str(name))
                            if names:
                                items_str = ', '.join(names)
                                if len(value) > 15:
                                    items_str += f" (and {len(value) - 15} more)"
                                lines.append(f"  - {display_key}: {items_str}")
                            else:
                                lines.append(f"  - {display_key}: {len(value)} items")
                    else:
                        lines.append(f"  - {display_key}: {len(value)} items")
            elif isinstance(value, dict) and max_depth > 0:
                # Recursively summarize nested dicts for important keys
                if key in ('agent', 'tools', 'customTools', 'validation'):
                    nested_lines = []
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, (str, int, float, bool)) and nested_value:
                            nested_display = nested_key.replace('_', ' ').title()
                            nested_lines.append(f"    - {nested_display}: {nested_value}")
                        elif isinstance(nested_value, list) and len(nested_value) > 0:
                            # Check if these are tools with descriptions
                            if all(isinstance(x, dict) for x in nested_value[:3]):
                                has_descriptions = any(x.get('description') for x in nested_value[:3])
                                
                                if has_descriptions:
                                    nested_display = nested_key.replace('_', ' ').title()
                                    nested_lines.append(f"    - {nested_display}:")
                                    for item in nested_value[:15]:
                                        name = item.get('name') or item.get('displayName') or item.get('title') or item.get('toolId', '')
                                        desc = item.get('description', '')
                                        if name:
                                            if desc:
                                                nested_lines.append(f"        • {name}: {desc}")
                                            else:
                                                nested_lines.append(f"        • {name}")
                                    if len(nested_value) > 15:
                                        nested_lines.append(f"        (and {len(nested_value) - 15} more)")
                                else:
                                    names = [item.get('name') or item.get('displayName') or item.get('title') or item.get('toolId', '') for item in nested_value[:10]]
                                    names = [n for n in names if n]
                                    if names:
                                        nested_display = nested_key.replace('_', ' ').title()
                                        nested_lines.append(f"    - {nested_display}: {', '.join(names)}")
                    if nested_lines:
                        lines.append(f"  - {display_key}:")
                        lines.extend(nested_lines)
                    else:
                        lines.append(f"  - {display_key}: {len(value)} properties")
                else:
                    lines.append(f"  - {display_key}: {len(value)} properties")
        
        return "\n".join(lines)


# =============================================================================
# Main Universal Assistant Class
# =============================================================================

class UniversalAssistant:
    """
    Main assistant orchestrator.
    Works on any page with automatic context extraction.
    """
    
    def __init__(self, 
                 docs_dir: str = "assistant_docs",
                 ai_api_function = None):
        """
        Initialize the Universal Assistant.
        
        Args:
            docs_dir: Path to documentation directory (optional enhancement)
            ai_api_function: Function to call AI API
        """
        self.docs_manager = DocumentationManager(docs_dir)
        self.session_manager = SessionManager()
        self.prompt_builder = PromptBuilder()
        
        if ai_api_function:
            self.ai_api = ai_api_function
        else:
            try:
                from AppUtils import azureMiniQuickPrompt
                self.ai_api = azureMiniQuickPrompt
            except ImportError:
                logger.warning("Could not import azureMiniQuickPrompt")
                self.ai_api = None
    
    def process_query(self,
                      question: str,
                      page: str,
                      session_id: str,
                      page_data: Optional[Dict] = None,
                      user_role: str = "user",
                      include_history: bool = True) -> Dict[str, Any]:
        """
        Process an assistant query.
        
        Args:
            question: User's question
            page: Page identifier (from URL or custom)
            session_id: Session identifier
            page_data: Full context from frontend including:
                - auto: Auto-extracted page context
                - custom: Custom page-specific context
            user_role: User role for documentation filtering
            include_history: Include conversation history
            
        Returns:
            Dict with 'status', 'response', 'session_id'
        """
        try:
            if not self.ai_api:
                return {
                    'status': 'error',
                    'error': 'AI API not configured',
                    'session_id': session_id
                }
            
            # Get optional documentation (enhances but not required)
            documentation = self.docs_manager.get_documentation(page, user_role)
            
            # Get conversation history
            conversation_history = ""
            if include_history:
                conversation_history = self.session_manager.get_context_summary(session_id)
            
            # Build prompts with full context
            system_prompt, user_prompt = self.prompt_builder.build(
                question=question,
                page_data=page_data or {},
                documentation=documentation,
                conversation_history=conversation_history
            )
            
            # Call AI API
            result = self.ai_api(user_prompt, system_prompt)
            
            # Handle response
            if isinstance(result, dict):
                if result.get('error'):
                    return {
                        'status': 'error',
                        'error': result['error'],
                        'session_id': session_id
                    }
                response_text = result.get('response', result.get('content', str(result)))
            else:
                response_text = str(result)
            
            # Store in session
            self.session_manager.add_message(session_id, 'user', question)
            self.session_manager.add_message(session_id, 'assistant', response_text)
            
            return {
                'status': 'success',
                'response': response_text,
                'session_id': session_id
            }
            
        except Exception as e:
            logger.error(f"Universal assistant error: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'session_id': session_id
            }
    
    def handle_request(self, request) -> 'flask.Response':
        """Handle a Flask request directly."""
        from flask import jsonify
        
        try:
            data = request.get_json()
            
            question = data.get('question', '').strip()
            page = data.get('page', 'general')
            session_id = data.get('session_id', f"session-{datetime.now().timestamp()}")
            page_data = data.get('page_data', {})
            user_role = data.get('user_role', 'user')
            include_history = data.get('include_history', True)
            
            if not question:
                return jsonify({
                    'status': 'error',
                    'error': 'Question is required'
                }), 400
            
            result = self.process_query(
                question=question,
                page=page,
                session_id=session_id,
                page_data=page_data,
                user_role=user_role,
                include_history=include_history
            )
            
            status_code = 200 if result['status'] == 'success' else 500
            return jsonify(result), status_code
            
        except Exception as e:
            logger.error(f"Request handling error: {e}", exc_info=True)
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500
    
    def get_history(self, session_id: str) -> List[Dict]:
        """Get conversation history for a session"""
        return self.session_manager.get_history(session_id)
    
    def clear_history(self, session_id: str):
        """Clear conversation history for a session"""
        self.session_manager.clear_session(session_id)
    
    def reload_documentation(self):
        """Force reload all documentation"""
        self.docs_manager.reload_cache()


# =============================================================================
# Flask Blueprint
# =============================================================================

def create_assistant_blueprint(assistant: UniversalAssistant, url_prefix: str = '/api/assistant'):
    """Create a Flask Blueprint for the assistant routes."""
    from flask import Blueprint, request, jsonify
    
    bp = Blueprint('universal_assistant', __name__, url_prefix=url_prefix)
    
    @bp.route('/query', methods=['POST'])
    def query():
        """Main query endpoint"""
        return assistant.handle_request(request)
    
    @bp.route('/history', methods=['GET'])
    def get_history():
        """Get conversation history"""
        session_id = request.args.get('session_id')
        if not session_id:
            return jsonify({'status': 'error', 'error': 'Session ID required'}), 400
        
        history = assistant.get_history(session_id)
        return jsonify({
            'status': 'success',
            'history': history,
            'session_id': session_id
        })
    
    @bp.route('/history', methods=['DELETE'])
    def clear_history():
        """Clear conversation history"""
        session_id = request.args.get('session_id')
        if not session_id:
            return jsonify({'status': 'error', 'error': 'Session ID required'}), 400
        
        assistant.clear_history(session_id)
        return jsonify({
            'status': 'success',
            'message': 'History cleared',
            'session_id': session_id
        })
    
    @bp.route('/reload-docs', methods=['POST'])
    def reload_docs():
        """Reload documentation cache"""
        assistant.reload_documentation()
        return jsonify({
            'status': 'success',
            'message': 'Documentation reloaded'
        })
    
    return bp


# =============================================================================
# Quick Setup Function
# =============================================================================

def setup_assistant_routes(app, docs_dir: str = "assistant_docs", ai_api_function = None):
    """
    Quick setup function to add assistant routes to an existing Flask app.
    
    Usage:
        from universal_assistant import setup_assistant_routes
        setup_assistant_routes(app, docs_dir='assistant_docs')
    """
    assistant = UniversalAssistant(docs_dir=docs_dir, ai_api_function=ai_api_function)
    blueprint = create_assistant_blueprint(assistant)
    app.register_blueprint(blueprint)
    
    return assistant
