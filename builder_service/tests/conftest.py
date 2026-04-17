"""
Shared Fixtures for Builder Service Tests
==========================================
Provides reusable test data and mock objects for context gatherer,
domain detection, platform knowledge, and config tests.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, AsyncMock

# ─── Path Setup ──────────────────────────────────────────────────────────
# Add builder_service to path so imports work
BUILDER_SERVICE_DIR = os.path.dirname(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(BUILDER_SERVICE_DIR)

if BUILDER_SERVICE_DIR not in sys.path:
    sys.path.insert(0, BUILDER_SERVICE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ─── Context Gatherer Fixtures ───────────────────────────────────────────

from context_gatherer import SystemContext


@pytest.fixture
def populated_system_context():
    """SystemContext pre-populated with realistic test data."""
    ctx = SystemContext(
        core_tools=[
            {"name": "send_email", "display_name": "Email Tool", "description": "Send emails"},
            {"name": "web_search", "display_name": "Web Search", "description": "Search the web"},
            {"name": "file_operations", "display_name": "File Operations", "description": "File I/O"},
        ],
        custom_tools=[
            {"name": "my_custom_tool", "display_name": "My Custom Tool"},
        ],
        agents=[
            {"id": 1, "description": "Customer Support Bot", "enabled": True},
            {"id": 2, "description": "Data Analyst", "enabled": True},
        ],
        connections=[
            {"id": 10, "name": "Production DB", "type": "sql_server"},
            {"id": 20, "name": "Analytics Warehouse", "type": "postgresql"},
        ],
        workflows=[
            {"id": 100, "name": "Invoice Processor"},
            {"id": 200, "name": "Employee Onboarding"},
        ],
        integrations=[
            {"id": 1, "name": "Slack Notifications", "template_key": "slack"},
        ],
        mcp_servers=[
            {"id": 1, "name": "Filesystem Server"},
        ],
        users=[
            {"id": 1, "username": "admin", "name": "Admin User", "role": "admin"},
            {"id": 2, "username": "dev1", "name": "Developer One", "role": "developer"},
        ],
    )
    ctx.build_lookup_maps()
    return ctx


@pytest.fixture
def empty_system_context():
    """Empty SystemContext with lookup maps built."""
    ctx = SystemContext()
    ctx.build_lookup_maps()
    return ctx


@pytest.fixture
def mock_executor():
    """Mock executor with async execute_step for ContextGatherer tests."""
    executor = MagicMock()
    executor.execute_step = AsyncMock()
    return executor


@pytest.fixture
def make_execution_result():
    """Factory for creating mock ExecutionResult objects."""
    def _make(is_success=True, data=None, error=None):
        result = MagicMock()
        result.is_success = is_success
        result.data = data if data is not None else {}
        result.error = error
        return result
    return _make


# ─── Retail / Ecommerce Context Fixtures ──────────────────────────────────

@pytest.fixture
def retail_system_context():
    """SystemContext pre-populated with retail/wholesale/ecommerce resources.

    Provides a realistic environment for testing process automation use cases:
    - 3 agents (2 enabled, 1 disabled legacy)
    - 4 database connections (Products, Sales, Orders, Vendors)
    - 3 existing workflows (Invoice, Fulfillment, Onboarding)
    - Slack integration
    - 2 knowledge bases (Product FAQ, Return Policy)
    - Core tools: email, file ops + custom retail tools
    """
    ctx = SystemContext(
        core_tools=[
            {"name": "send_email_message", "display_name": "Send Email", "description": "Send emails to recipients"},
            {"name": "file_operations", "display_name": "File Operations", "description": "Read and write files"},
        ],
        custom_tools=[
            {"name": "barcode_scanner", "display_name": "Barcode Scanner"},
            {"name": "shipping_calculator", "display_name": "Shipping Calculator"},
        ],
        agents=[
            {"id": 1, "description": "General Assistant", "enabled": True, "created_date": "2026-01-15"},
            {"id": 2, "description": "Customer Support Bot", "enabled": True, "created_date": "2026-02-01"},
            {"id": 3, "description": "Legacy Sales Agent", "enabled": False, "created_date": "2025-06-01"},
        ],
        connections=[
            {"id": 10, "name": "Products Database", "type": "sql_server"},
            {"id": 20, "name": "Sales Database", "type": "sql_server"},
            {"id": 30, "name": "Orders Database", "type": "sql_server"},
            {"id": 40, "name": "Vendors Database", "type": "postgresql"},
        ],
        workflows=[
            {"id": 100, "name": "Invoice Processor", "category": "Finance"},
            {"id": 200, "name": "Order Fulfillment", "category": "Operations"},
            {"id": 300, "name": "Customer Onboarding", "category": "Sales"},
        ],
        integrations=[
            {"id": 1, "name": "Slack Notifications", "template_key": "slack"},
        ],
        mcp_servers=[],
        knowledge_bases=[
            {"id": 1, "name": "Product FAQ", "agent_id": 1},
            {"id": 2, "name": "Return Policy", "agent_id": 2},
        ],
        users=[
            {"id": 1, "username": "admin", "name": "Store Manager", "role": "admin"},
            {"id": 2, "username": "warehouse", "name": "Warehouse Staff", "role": "user"},
        ],
    )
    ctx.build_lookup_maps()
    return ctx
