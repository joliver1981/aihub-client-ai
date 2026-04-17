# tool_dependency_manager.py

import yaml
import os
import logging
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
import json

@dataclass
class ToolInfo:
    """Information about a tool"""
    name: str
    display_name: str
    description: str
    category: str
    visibility: str  # 'user_selectable', 'hidden', 'dual_purpose'
    auto_include: bool
    dependencies: Dict[str, List[str]]  # {'required': [...], 'optional': [...]}

class ToolDependencyManager:
    """Manages tool dependencies and visibility"""
    
    def __init__(self, config_path: str = None):
        """
        Initialize the tool dependency manager
        
        Args:
            config_path: Path to the tool dependencies YAML file
        """
        if config_path is None:
            # Use core_tools.yaml from config
            try:
                import config as cfg
                config_path = cfg.CORE_TOOLS_FILE
            except ImportError:
                # Fallback to default
                config_path = 'core_tools.yaml'
        
        self.config_path = config_path
        self.config = self._load_config()
        self.tool_cache = {}  # Cache for tool information
        self._build_tool_cache()
        self._build_tool_lookup()  # Build lookup from tool list
    
    def _load_config(self) -> dict:
        """Load the tool dependencies configuration"""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logging.error(f"Failed to load tool dependencies config: {str(e)}")
            return self._get_default_config()
    
    def _get_default_config(self) -> dict:
        """Return a default configuration if file loading fails"""
        return {
            'categories': {},
            'visibility': {
                'user_selectable': [],
                'hidden': [],
                'dual_purpose': []
            },
            'dependencies': {},
            'dependency_groups': {},
            'conditional_dependencies': {},
            'tool_metadata': {}
        }
    
    def _build_tool_cache(self):
        """Build a cache of tool information for quick access"""
        # First, build a lookup dictionary from the tools list
        self.tool_lookup = {}
        for tool in self.config.get('tools', []):
            self.tool_lookup[tool['name']] = tool
        
        # Build cache from configuration
        for category_name, category_info in self.config.get('categories', {}).items():
            for tool_name in category_info.get('tools', []):
                visibility = self._get_tool_visibility(tool_name)
                
                # Get tool info from the tools list
                tool_data = self.tool_lookup.get(tool_name, {})
                
                # Get additional metadata
                metadata = self.config.get('tool_metadata', {}).get(tool_name, {})
                
                self.tool_cache[tool_name] = ToolInfo(
                    name=tool_name,
                    display_name=tool_data.get('display_name', tool_name.replace('_', ' ').title()),
                    description=tool_data.get('description', metadata.get('description', '')),
                    category=category_name,
                    visibility=visibility,
                    auto_include=metadata.get('auto_include', False),
                    dependencies=self.config.get('dependencies', {}).get(tool_name, {})
                )
    
    def _build_tool_lookup(self):
        """Build lookup dictionary from tools list"""
        self.tool_lookup = {}
        for tool in self.config.get('tools', []):
            self.tool_lookup[tool['name']] = tool
    
    def _get_tool_visibility(self, tool_name: str) -> str:
        """Determine the visibility of a tool"""
        visibility_config = self.config.get('visibility', {})
        
        if tool_name in visibility_config.get('hidden', []):
            return 'hidden'
        elif tool_name in visibility_config.get('dual_purpose', []):
            return 'dual_purpose'
        elif tool_name in visibility_config.get('user_selectable', []):
            return 'user_selectable'
        else:
            # Default to user_selectable if not specified
            return 'user_selectable'
    
    def get_user_selectable_tools(self) -> List[str]:
        """Get list of tools that users can select"""
        selectable = []
        visibility_config = self.config.get('visibility', {})
        
        selectable.extend(visibility_config.get('user_selectable', []))
        selectable.extend(visibility_config.get('dual_purpose', []))
        
        return sorted(list(set(selectable)))
    
    def get_mandatory_tools(self) -> List[str]:
        """Get list of mandatory tools that are added to every agent"""
        visibility_config = self.config.get('visibility', {})
        return visibility_config.get('mandatory', [])
    
    def get_tool_dependencies(self, tool_name: str, include_optional: bool = False) -> Set[str]:
        """
        Get all dependencies for a given tool
        
        Args:
            tool_name: Name of the tool
            include_optional: Whether to include optional dependencies
            
        Returns:
            Set of dependent tool names
        """
        dependencies = set()
        tool_deps = self.config.get('dependencies', {}).get(tool_name, {})
        
        # Add required dependencies
        dependencies.update(tool_deps.get('required', []))
        
        # Add optional dependencies if requested
        if include_optional:
            dependencies.update(tool_deps.get('optional', []))
        
        # Recursively get dependencies of dependencies
        all_deps = set(dependencies)
        for dep in dependencies:
            all_deps.update(self.get_tool_dependencies(dep, include_optional))
        
        return all_deps
    
    def resolve_tool_list(self, selected_tools: List[str], 
                         include_optional: bool = False,
                         agent_config: Optional[dict] = None) -> Tuple[List[str], Dict[str, List[str]]]:
        """
        Resolve a list of selected tools to include all dependencies
        
        Args:
            selected_tools: List of tools selected by the user
            include_optional: Whether to include optional dependencies
            agent_config: Agent configuration for conditional dependencies
            
        Returns:
            Tuple of (final_tool_list, dependency_map)
        """
        # Start with mandatory tools
        final_tools = set(self.get_mandatory_tools())
        
        # Add selected tools
        final_tools.update(selected_tools)
        
        dependency_map = {}
        
        # Process each tool (including mandatory ones) for dependencies
        all_tools_to_process = list(final_tools)
        for tool in all_tools_to_process:
            deps = self.get_tool_dependencies(tool, include_optional)
            if deps:
                dependency_map[tool] = list(deps)
                final_tools.update(deps)
        
        # Check for dependency groups
        final_tools.update(self._check_dependency_groups(final_tools))
        
        # Apply conditional dependencies if agent config provided
        if agent_config:
            final_tools.update(self._apply_conditional_dependencies(final_tools, agent_config))
        
        # Filter out any tools that shouldn't be included
        final_tools = self._filter_tools(final_tools)
        
        return sorted(list(final_tools)), dependency_map
    
    def _check_dependency_groups(self, current_tools: Set[str]) -> Set[str]:
        """Check if any dependency groups should be activated"""
        additional_tools = set()
        
        for group_name, group_info in self.config.get('dependency_groups', {}).items():
            group_tools = set(group_info.get('tools', []))
            
            # If any tool from the group is selected, consider adding the whole group
            if current_tools & group_tools:
                # You might want to add logic here to decide whether to add the whole group
                # For now, we'll just log it
                logging.info(f"Dependency group '{group_name}' partially activated")
        
        return additional_tools
    
    def _apply_conditional_dependencies(self, current_tools: Set[str], agent_config: dict) -> Set[str]:
        """Apply conditional dependencies based on agent configuration"""
        additional_tools = set()
        
        for condition_name, condition_info in self.config.get('conditional_dependencies', {}).items():
            # Check if condition is met in agent config
            if agent_config.get(condition_name, False):
                triggers = set(condition_info.get('triggers', []))
                
                # If any trigger tool is in current tools, add the conditional tools
                if current_tools & triggers:
                    additional_tools.update(condition_info.get('adds', []))
        
        return additional_tools
    
    def _filter_tools(self, tools: Set[str]) -> Set[str]:
        """Filter out any tools that shouldn't be included"""
        # This is where you could add additional filtering logic
        # For now, we'll just return all tools
        return tools
    
    def get_tool_info(self, tool_name: str) -> Optional[ToolInfo]:
        """Get information about a specific tool"""
        return self.tool_cache.get(tool_name)
    
    def get_tools_by_category(self, category: str) -> List[str]:
        """Get all tools in a specific category"""
        category_info = self.config.get('categories', {}).get(category, {})
        return category_info.get('tools', [])
    
    def get_all_categories(self) -> Dict[str, str]:
        """Get all categories and their descriptions"""
        categories = {}
        for cat_name, cat_info in self.config.get('categories', {}).items():
            categories[cat_name] = cat_info.get('description', '')
        return categories
    
    def validate_tool_selection(self, selected_tools: List[str]) -> Tuple[bool, List[str]]:
        """
        Validate that the selected tools are valid and can be selected by users
        
        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []
        user_selectable = set(self.get_user_selectable_tools())
        
        for tool in selected_tools:
            if tool not in self.tool_cache:
                errors.append(f"Unknown tool: {tool}")
            elif tool not in user_selectable:
                errors.append(f"Tool '{tool}' cannot be selected by users")
        
        return len(errors) == 0, errors
    
    def export_dependency_graph(self, selected_tools: List[str], format: str = 'json') -> str:
        """
        Export a dependency graph for the selected tools
        
        Args:
            selected_tools: List of selected tools
            format: Export format ('json' or 'dot' for Graphviz)
        """
        final_tools, dep_map = self.resolve_tool_list(selected_tools)
        
        if format == 'json':
            return json.dumps({
                'selected_tools': selected_tools,
                'final_tools': final_tools,
                'dependencies': dep_map
            }, indent=2)
        
        elif format == 'dot':
            # Generate Graphviz DOT format
            dot_lines = ['digraph ToolDependencies {']
            dot_lines.append('  rankdir=LR;')
            dot_lines.append('  node [shape=box];')
            
            # Add selected tools with special styling
            for tool in selected_tools:
                dot_lines.append(f'  "{tool}" [style=filled, fillcolor=lightblue];')
            
            # Add dependency edges
            for tool, deps in dep_map.items():
                for dep in deps:
                    dot_lines.append(f'  "{tool}" -> "{dep}";')
            
            dot_lines.append('}')
            return '\n'.join(dot_lines)
        
        else:
            raise ValueError(f"Unsupported format: {format}")

# Utility functions for integration with existing code

def load_tool_dependencies(config_path: Optional[str] = None) -> ToolDependencyManager:
    """Load and return the tool dependency manager"""
    return ToolDependencyManager(config_path)

def get_tools_for_agent(selected_tools: List[str], 
                       include_optional_deps: bool = False,
                       agent_config: Optional[dict] = None) -> List[str]:
    """
    Get the final list of tools for an agent including all dependencies
    
    Args:
        selected_tools: Tools selected by the user
        include_optional_deps: Whether to include optional dependencies
        agent_config: Agent configuration for conditional dependencies
        
    Returns:
        Final list of tools including dependencies
    """
    manager = load_tool_dependencies()
    final_tools, _ = manager.resolve_tool_list(selected_tools, include_optional_deps, agent_config)
    return final_tools

def get_user_selectable_tools_filtered() -> List[Dict[str, any]]:
    """
    Get list of tools that should be shown to users for selection
    
    Returns:
        List of tool dictionaries with name, display_name, description, category
    """
    manager = load_tool_dependencies()
    selectable_tools = manager.get_user_selectable_tools()
    
    tool_list = []
    for tool_name in selectable_tools:
        tool_info = manager.get_tool_info(tool_name)
        if tool_info:
            tool_list.append({
                'name': tool_name,
                'display_name': tool_info.display_name,
                'description': tool_info.description,
                'category': tool_info.category
            })
    
    return tool_list
