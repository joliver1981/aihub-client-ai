# integration_template_loader.py
"""
Integration Template Loader
===========================

Loads integration templates from the file system.

Directory Structure:
    /integrations/
        /builtin/           # Shipped with app (read-only)
            quickbooks_online.json
            shopify.json
            ...
        /custom/            # User-created via files (optional)
            my_custom_api.json

Template files are JSON with the following required fields:
    - template_key: Unique identifier (should match filename without .json)
    - platform_name: Display name
    - auth_type: 'oauth2', 'oauth1_tba', 'api_key', 'bearer', 'basic', 'none'
    - operations: List of operation definitions

Templates from database (IntegrationTemplates table) are also loaded
and merged with file-based templates. Database templates take precedence
for user-created custom integrations.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from threading import Lock

logger = logging.getLogger(__name__)


class TemplateValidationError(Exception):
    """Raised when a template file is invalid."""
    pass


class IntegrationTemplateLoader:
    """
    Loads and caches integration templates from files and database.
    
    Thread-safe with caching for performance.
    """
    
    # Required fields in every template
    REQUIRED_FIELDS = ['template_key', 'platform_name', 'auth_type']
    
    # Valid auth types
    VALID_AUTH_TYPES = ['oauth2', 'oauth1_tba', 'api_key', 'bearer', 'basic', 'none', 'cloud_storage']
    
    # Cache settings
    CACHE_TTL_SECONDS = 300  # 5 minutes
    
    def __init__(self, integrations_dir: str = None):
        """
        Initialize the template loader.
        
        Args:
            integrations_dir: Path to the integrations folder.
                             
        Path Resolution Order:
            1. Explicit integrations_dir argument
            2. AIHUB_INTEGRATIONS_DIR env var (full path override)
            3. APP_ROOT env var + /integrations
            4. PyInstaller frozen: executable directory + /integrations
            5. Development fallback: CWD + /integrations
        """
        if integrations_dir:
            self.integrations_dir = Path(integrations_dir)
        elif os.getenv('AIHUB_INTEGRATIONS_DIR'):
            self.integrations_dir = Path(os.getenv('AIHUB_INTEGRATIONS_DIR'))
        elif os.getenv('APP_ROOT'):
            self.integrations_dir = Path(os.getenv('APP_ROOT')) / 'integrations'
        else:
            import sys
            if getattr(sys, 'frozen', False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path.cwd()
            self.integrations_dir = base_dir / 'integrations'
        
        self.builtin_dir = self.integrations_dir / 'builtin'
        self.custom_dir = self.integrations_dir / 'custom'
        
        # Thread-safe cache
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._lock = Lock()
        
        # Ensure directories exist
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create the integrations directory structure if it doesn't exist."""
        try:
            logger.info(f"Integrations directory: {self.integrations_dir.absolute()}")
            logger.info(f"Builtin templates dir: {self.builtin_dir.absolute()}")
            logger.info(f"Custom templates dir: {self.custom_dir.absolute()}")
            
            # Only create custom dir (user-writable). Builtin should already exist from install.
            self.custom_dir.mkdir(parents=True, exist_ok=True)
            
            if not self.builtin_dir.exists():
                logger.warning(
                    f"Builtin templates directory not found: {self.builtin_dir.absolute()}. "
                    f"Integration gallery will be empty until templates are installed."
                )
            else:
                builtin_count = len(list(self.builtin_dir.glob('*.json')))
                logger.info(f"Found {builtin_count} builtin template files")
        except Exception as e:
            logger.warning(f"Could not create integrations directories: {e}")
    
    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid."""
        if self._cache_time is None:
            return False
        return datetime.now() - self._cache_time < timedelta(seconds=self.CACHE_TTL_SECONDS)
    
    def _invalidate_cache(self):
        """Invalidate the cache to force reload."""
        with self._lock:
            self._cache = {}
            self._cache_time = None
    
    def reload(self):
        """Force reload all templates from disk."""
        self._invalidate_cache()
        return self.load_all_templates()
    
    def load_all_templates(self) -> Dict[str, Any]:
        """
        Load all templates from files and database.
        
        Returns:
            Dict mapping template_key to template definition
        """
        with self._lock:
            # Return cached if valid
            if self._is_cache_valid():
                return self._cache.copy()
            
            templates = {}
            errors = []
            
            # 1. Load builtin templates
            builtin_templates, builtin_errors = self._load_templates_from_directory(
                self.builtin_dir, is_builtin=True
            )
            templates.update(builtin_templates)
            errors.extend(builtin_errors)
            
            # 2. Load custom file templates
            custom_templates, custom_errors = self._load_templates_from_directory(
                self.custom_dir, is_builtin=False
            )
            templates.update(custom_templates)
            errors.extend(custom_errors)
            
            # 3. Load database templates (these can override file templates)
            db_templates = self._load_templates_from_database()
            templates.update(db_templates)
            
            # Log any errors
            for error in errors:
                logger.warning(error)
            
            # Update cache
            self._cache = templates
            self._cache_time = datetime.now()
            
            logger.info(
                f"Loaded {len(templates)} integration templates "
                f"({len(builtin_templates)} builtin, {len(custom_templates)} custom files, "
                f"{len(db_templates)} database)"
            )
            
            return templates.copy()
    
    def _load_templates_from_directory(
        self, 
        directory: Path, 
        is_builtin: bool
    ) -> tuple[Dict[str, Any], List[str]]:
        """
        Load templates from a directory.
        
        Args:
            directory: Path to scan for .json files
            is_builtin: Whether these are builtin (shipped) templates
            
        Returns:
            Tuple of (templates dict, list of error messages)
        """
        templates = {}
        errors = []
        
        if not directory.exists():
            return templates, errors
        
        for file_path in directory.glob('*.json'):
            try:
                template = self._load_template_file(file_path)
                template['is_builtin'] = is_builtin
                template['source_file'] = str(file_path)
                
                # Validate the template
                self._validate_template(template, file_path.name)
                
                # Key should match filename (without .json)
                expected_key = file_path.stem
                actual_key = template.get('template_key')
                
                if actual_key != expected_key:
                    logger.warning(
                        f"Template key mismatch in {file_path.name}: "
                        f"expected '{expected_key}', got '{actual_key}'. Using file key."
                    )
                    template['template_key'] = expected_key
                
                templates[template['template_key']] = template
                
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON in {file_path.name}: {e}")
            except TemplateValidationError as e:
                errors.append(f"Validation error in {file_path.name}: {e}")
            except Exception as e:
                errors.append(f"Error loading {file_path.name}: {e}")
        
        return templates, errors
    
    def _load_template_file(self, file_path: Path) -> Dict:
        """Load and parse a single template file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _validate_template(self, template: Dict, filename: str):
        """
        Validate a template has required fields and valid values.
        
        Raises:
            TemplateValidationError: If validation fails
        """
        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in template or not template[field]:
                raise TemplateValidationError(f"Missing required field: {field}")
        
        # Validate auth_type
        auth_type = template.get('auth_type')
        if auth_type not in self.VALID_AUTH_TYPES:
            raise TemplateValidationError(
                f"Invalid auth_type: {auth_type}. "
                f"Must be one of: {', '.join(self.VALID_AUTH_TYPES)}"
            )
        
        # Validate operations structure
        operations = template.get('operations', [])
        if not isinstance(operations, list):
            raise TemplateValidationError("'operations' must be a list")
        
        for i, op in enumerate(operations):
            if not isinstance(op, dict):
                raise TemplateValidationError(f"Operation {i} must be an object")
            if 'key' not in op:
                raise TemplateValidationError(f"Operation {i} missing 'key' field")
            if 'method' not in op:
                raise TemplateValidationError(f"Operation {i} missing 'method' field")
    
    def _load_templates_from_database(self) -> Dict[str, Any]:
        """
        Load custom templates created via UI from database.
        
        These are stored in IntegrationTemplates table with is_builtin=0.
        """
        templates = {}
        
        try:
            from CommonUtils import get_db_connection
            import os
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Get custom templates from database
            cursor.execute("""
                SELECT 
                    template_key, platform_name, platform_category,
                    logo_url, description, documentation_url,
                    auth_type, auth_config, base_url, default_headers,
                    operations, supports_webhooks, webhook_events,
                    setup_instructions, version
                FROM IntegrationTemplates
                WHERE is_builtin = 0 AND is_active = 1
            """)
            
            columns = [col[0] for col in cursor.description]
            
            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))
                
                # Parse JSON fields
                for json_field in ['auth_config', 'default_headers', 'operations', 'webhook_events']:
                    if row_dict.get(json_field):
                        try:
                            row_dict[json_field] = json.loads(row_dict[json_field])
                        except json.JSONDecodeError:
                            row_dict[json_field] = None
                
                row_dict['is_builtin'] = False
                row_dict['source'] = 'database'
                
                templates[row_dict['template_key']] = row_dict
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.warning(f"Could not load templates from database: {e}")
        
        return templates
    
    def get_template(self, template_key: str) -> Optional[Dict]:
        """
        Get a specific template by key.
        
        Args:
            template_key: The template's unique key
            
        Returns:
            Template dict or None if not found
        """
        templates = self.load_all_templates()
        return templates.get(template_key)
    
    def get_templates_by_category(self, category: str) -> List[Dict]:
        """
        Get all templates in a category.
        
        Args:
            category: Category name (e.g., 'Accounting', 'E-Commerce')
            
        Returns:
            List of templates in the category
        """
        templates = self.load_all_templates()
        return [
            t for t in templates.values()
            if t.get('platform_category', '').lower() == category.lower()
        ]
    
    def get_categories(self) -> List[Dict]:
        """
        Get all available categories with counts.
        
        Returns:
            List of category info dicts
        """
        templates = self.load_all_templates()
        
        category_counts = {}
        for template in templates.values():
            cat = template.get('platform_category', 'Other')
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # Define standard categories with icons
        CATEGORY_INFO = {
            'Accounting': {'icon': 'bi-calculator', 'order': 1},
            'E-Commerce': {'icon': 'bi-cart', 'order': 2},
            'CRM': {'icon': 'bi-people', 'order': 3},
            'Payments': {'icon': 'bi-credit-card', 'order': 4},
            'Communication': {'icon': 'bi-chat-dots', 'order': 5},
            'Productivity': {'icon': 'bi-grid', 'order': 6},
            'Cloud Storage': {'icon': 'bi-cloud', 'order': 7},
            'Custom': {'icon': 'bi-code-slash', 'order': 99},
            'Other': {'icon': 'bi-box', 'order': 100}
        }
        
        categories = []
        for name, count in category_counts.items():
            info = CATEGORY_INFO.get(name, {'icon': 'bi-box', 'order': 50})
            categories.append({
                'name': name,
                'count': count,
                'icon': info['icon'],
                'order': info['order']
            })
        
        # Sort by order
        categories.sort(key=lambda x: x['order'])
        
        return categories
    
    def save_custom_template(self, template: Dict, save_to_file: bool = False) -> str:
        """
        Save a custom template.
        
        By default saves to database. Optionally can save to file.
        
        Args:
            template: Template definition dict
            save_to_file: If True, save to custom directory as file
            
        Returns:
            The template_key of the saved template
        """
        # Validate first
        self._validate_template(template, 'new_template')
        
        template_key = template['template_key']
        
        if save_to_file:
            # Save to custom directory
            file_path = self.custom_dir / f"{template_key}.json"
            
            # Don't overwrite builtin templates
            builtin_path = self.builtin_dir / f"{template_key}.json"
            if builtin_path.exists():
                raise ValueError(f"Cannot overwrite builtin template: {template_key}")
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(template, f, indent=2)
            
            logger.info(f"Saved custom template to file: {file_path}")
        else:
            # Save to database
            self._save_template_to_database(template)
        
        # Invalidate cache
        self._invalidate_cache()
        
        return template_key
    
    def _save_template_to_database(self, template: Dict):
        """Save a template to the IntegrationTemplates table."""
        from CommonUtils import get_db_connection
        import os
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Check if exists
            cursor.execute(
                "SELECT template_id FROM IntegrationTemplates WHERE template_key = ?",
                template['template_key']
            )
            existing = cursor.fetchone()
            
            # Prepare JSON fields
            json_fields = {
                'auth_config': json.dumps(template.get('auth_config', {})),
                'default_headers': json.dumps(template.get('default_headers', {})),
                'operations': json.dumps(template.get('operations', [])),
                'webhook_events': json.dumps(template.get('webhook_events', []))
            }
            
            if existing:
                # Update
                cursor.execute("""
                    UPDATE IntegrationTemplates SET
                        platform_name = ?,
                        platform_category = ?,
                        description = ?,
                        auth_type = ?,
                        auth_config = ?,
                        base_url = ?,
                        default_headers = ?,
                        operations = ?,
                        supports_webhooks = ?,
                        webhook_events = ?,
                        setup_instructions = ?,
                        updated_at = GETUTCDATE()
                    WHERE template_key = ?
                """, (
                    template.get('platform_name'),
                    template.get('platform_category', 'Custom'),
                    template.get('description'),
                    template.get('auth_type'),
                    json_fields['auth_config'],
                    template.get('base_url'),
                    json_fields['default_headers'],
                    json_fields['operations'],
                    template.get('supports_webhooks', False),
                    json_fields['webhook_events'],
                    template.get('setup_instructions'),
                    template['template_key']
                ))
            else:
                # Insert
                cursor.execute("""
                    INSERT INTO IntegrationTemplates (
                        template_key, platform_name, platform_category,
                        description, is_builtin, auth_type, auth_config,
                        base_url, default_headers, operations,
                        supports_webhooks, webhook_events, setup_instructions
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    template['template_key'],
                    template.get('platform_name'),
                    template.get('platform_category', 'Custom'),
                    template.get('description'),
                    template.get('auth_type'),
                    json_fields['auth_config'],
                    template.get('base_url'),
                    json_fields['default_headers'],
                    json_fields['operations'],
                    template.get('supports_webhooks', False),
                    json_fields['webhook_events'],
                    template.get('setup_instructions')
                ))
            
            conn.commit()
            logger.info(f"Saved template to database: {template['template_key']}")
            
        finally:
            cursor.close()
            conn.close()
    
    def delete_custom_template(self, template_key: str) -> bool:
        """
        Delete a custom template.
        
        Cannot delete builtin templates.
        
        Args:
            template_key: Template to delete
            
        Returns:
            True if deleted, False if not found or builtin
        """
        # Check if it's a builtin template
        builtin_path = self.builtin_dir / f"{template_key}.json"
        if builtin_path.exists():
            logger.warning(f"Cannot delete builtin template: {template_key}")
            return False
        
        deleted = False
        
        # Try to delete from custom files
        custom_path = self.custom_dir / f"{template_key}.json"
        if custom_path.exists():
            custom_path.unlink()
            deleted = True
            logger.info(f"Deleted custom template file: {custom_path}")
        
        # Try to delete from database
        try:
            from CommonUtils import get_db_connection
            import os
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                DELETE FROM IntegrationTemplates 
                WHERE template_key = ? AND is_builtin = 0
            """, template_key)
            
            if cursor.rowcount > 0:
                deleted = True
                logger.info(f"Deleted template from database: {template_key}")
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.warning(f"Error deleting from database: {e}")
        
        if deleted:
            self._invalidate_cache()
        
        return deleted
    
    def get_storage_info(self) -> Dict:
        """Get information about template storage for display."""
        templates = self.load_all_templates()
        
        builtin_count = sum(1 for t in templates.values() if t.get('is_builtin'))
        custom_count = len(templates) - builtin_count
        
        return {
            'integrations_dir': str(self.integrations_dir.absolute()),
            'builtin_dir': str(self.builtin_dir.absolute()),
            'custom_dir': str(self.custom_dir.absolute()),
            'total_templates': len(templates),
            'builtin_count': builtin_count,
            'custom_count': custom_count,
            'cache_ttl_seconds': self.CACHE_TTL_SECONDS
        }


# =============================================================================
# Global Instance
# =============================================================================

_template_loader: Optional[IntegrationTemplateLoader] = None


def get_template_loader(integrations_dir: str = None) -> IntegrationTemplateLoader:
    """Get or create the global template loader instance."""
    global _template_loader
    if _template_loader is None:
        _template_loader = IntegrationTemplateLoader(integrations_dir)
    return _template_loader


def reset_template_loader():
    """Reset the global instance (useful for testing)."""
    global _template_loader
    _template_loader = None


# =============================================================================
# Convenience Functions
# =============================================================================

def load_templates() -> Dict[str, Any]:
    """Load all integration templates."""
    return get_template_loader().load_all_templates()


def get_template(template_key: str) -> Optional[Dict]:
    """Get a specific template by key."""
    return get_template_loader().get_template(template_key)


def get_categories() -> List[Dict]:
    """Get all available template categories."""
    return get_template_loader().get_categories()


def reload_templates() -> Dict[str, Any]:
    """Force reload all templates from disk."""
    return get_template_loader().reload()
