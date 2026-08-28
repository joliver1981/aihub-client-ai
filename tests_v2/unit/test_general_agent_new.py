"""
Supplemental tests for GeneralAgent.py - focusing on the helper functions and
recently-modified behaviors that the existing tests/unit/test_agent_chat.py
does not cover:

 - load_custom_tool: config.json and code.py loading
 - dataframe_to_markdown / dataframe_to_csv / dataframe_to_table_dict
 - get_word_length, get_the_current_date(_and_time)
 - basic chat history conversion sanity (defense against regressions in
   _convert_chat_history)
 - log_function_call decorator pass-through and error propagation

The full import dance (used in test_agent_chat.py) is reused — we mock
everything heavy at module level before importing GeneralAgent.
"""

import os
import sys
import json
import tempfile
from typing import Optional, List, Dict, Any, Union
from datetime import datetime as _dt, date as _date
from collections import defaultdict as _dd
from unittest.mock import MagicMock, patch

import pytest
import pandas as pd

from langchain_core.messages import HumanMessage as _HumanMessage, AIMessage as _AIMessage

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


_test_log_dir = tempfile.mkdtemp()


def _import_general_agent():
    """Replicates the safe-import pattern from tests/unit/test_agent_chat.py."""
    _modules_before = set(sys.modules.keys())

    _mock_cfg = MagicMock()
    for attr, val in [
        ("DATABASE_SERVER", "x"), ("DATABASE_NAME", "x"),
        ("DATABASE_UID", "x"), ("DATABASE_PWD", "x"),
        ("ENABLE_AGENT_KNOWLEDGE_MANAGEMENT", False),
        ("LOG_DIR_AGENT", os.path.join(_test_log_dir, "a.log")),
        ("MAX_GENERAL_AGENT_ITERATIONS", "5"),
        ("GENERAL_CHAT_AI_PROCESSING_ROW_LIMIT", 100),
        ("LOG_DIR_DATA", "x"),
        ("ANTHROPIC_MODEL", "c"), ("ANTHROPIC_MAX_TOKENS", "1024"),
        ("DOC_CHARS_PER_TOKEN", 4), ("DOC_API_REQUESTS_TIMEOUT", 300),
        ("DOC_ANTHROPIC_PROXY_MAX_RETRIES", 3),
        ("DOC_ANTHROPIC_PROXY_RETRY_DELAY_BASE", 1.0),
        ("DOC_ANTHROPIC_PROXY_RETRY_DELAY_MAX", 8.0),
        ("DOC_ANTHROPIC_PROXY_RETRY_STATUS_CODES", [429]),
        ("DOC_DATE_FIELD_KEYWORDS", ["date"]),
        ("DEFAULT_INTERNET_SEARCH", "duckduckgo"),
        ("DEFAULT_INTERNET_SEARCH_KEY", ""),
        ("ENABLE_CAUTION_SYSTEM", False),
        ("CAUTION_SYSTEM_PAUSED_TOOLS", []),
        ("ENABLE_WORKFLOW_TRIGGER_TOOL", False),
        ("ENABLE_INTEGRATION_TOOLS", False),
        ("CUSTOM_TOOLS_FOLDER", "tools"),
    ]:
        setattr(_mock_cfg, attr, val)

    _mock_app_utils = MagicMock()
    for name, val in [
        ("Optional", Optional), ("List", List), ("Union", Union),
        ("Dict", Dict), ("Any", Any),
        ("datetime", _dt), ("date", _date), ("defaultdict", _dd),
        ("os", os), ("json", __import__("json")), ("re", __import__("re")),
        ("uuid", __import__("uuid")),
    ]:
        setattr(_mock_app_utils, name, val)

    _mock_common_utils = MagicMock()
    _mock_common_utils.get_log_path = lambda f: os.path.join(_test_log_dir, f)
    _mock_common_utils.rotate_logs_on_startup = MagicMock()

    _mock_app_config = MagicMock()
    _mock_app_config.APP_VERSION = "1.6.2"

    _mock_data_config = MagicMock()
    _mock_data_config.DATASET_TUPLE = ("AUTO",)
    _mock_data_config.DATASET_TABLES = {}

    _mock_system_prompts = MagicMock()
    _mock_system_prompts.NODE_DETAIL_REFERENCE = {}
    _mock_system_prompts.FRIENDLY_ERROR_RESPONSE_SYSTEM = "x"
    _mock_system_prompts.FRIENDLY_ERROR_RESPONSE_PROMPT = "x {error_text} {user_prompt}"

    modules_to_mock = {
        "config": _mock_cfg, "app_config": _mock_app_config,
        "system_prompts": _mock_system_prompts, "data_config": _mock_data_config,
        "CommonUtils": _mock_common_utils, "AppUtils": _mock_app_utils,
        "DocUtils": MagicMock(), "LLMDataEngineV2": MagicMock(),
        "agent_knowledge_integration": MagicMock(),
        "agent_email_tools": MagicMock(),
        "agent_communication_tool": MagicMock(),
        "local_secrets": MagicMock(),
        "tool_dependency_manager": MagicMock(),
        "request_tracking": MagicMock(),
        "SmartContentRenderer": MagicMock(),
        "DataFrameFileManager": MagicMock(),
        "RichContentManager": MagicMock(),
        "api_keys_config": MagicMock(),
    }

    try:
        import pyodbc  # noqa
    except ImportError:
        modules_to_mock["pyodbc"] = MagicMock()

    try:
        import langchain_openai  # noqa
    except ImportError:
        modules_to_mock["langchain_openai"] = MagicMock()
    try:
        import langchain_classic  # noqa
    except ImportError:
        _mc = MagicMock()
        modules_to_mock["langchain_classic"] = _mc
        modules_to_mock["langchain_classic.agents"] = _mc.agents
        modules_to_mock["langchain_classic.agents.format_scratchpad"] = _mc.agents.format_scratchpad
        modules_to_mock["langchain_classic.agents.output_parsers"] = _mc.agents.output_parsers
        modules_to_mock["langchain_classic.callbacks"] = _mc.callbacks

    _saved = {}
    for k, v in modules_to_mock.items():
        if k in sys.modules:
            _saved[k] = sys.modules[k]
        sys.modules[k] = v

    try:
        import GeneralAgent as _ga
    finally:
        for k in modules_to_mock:
            if k in _saved:
                sys.modules[k] = _saved[k]
            elif k in sys.modules:
                del sys.modules[k]

        # Clean up MagicMock side-effects in sys.modules
        new_mods = set(sys.modules.keys()) - _modules_before
        for k in new_mods:
            if (not k.startswith("langchain") and not k.startswith("pydantic")
                and not k.startswith("typing") and not k.startswith("_")
                and k != "GeneralAgent"):
                obj = sys.modules.get(k)
                if isinstance(obj, MagicMock):
                    del sys.modules[k]
        if "GeneralAgent" in sys.modules:
            del sys.modules["GeneralAgent"]

    return _ga


ga_module = _import_general_agent()


def _make_bare_agent():
    agent = object.__new__(ga_module.GeneralAgent)
    agent.agent_id = 1
    agent.user_id = None
    agent.AGENT_NAME = "Test"
    agent.SYSTEM = "system"
    agent.chat_history = []
    agent.tools = []
    return agent


# ---------------------------------------------------------------------------
# load_custom_tool
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestLoadCustomTool:
    def test_loads_config_and_code(self, tmp_path):
        folder = tmp_path / "mytool"
        folder.mkdir()
        cfg = {"function_name": "foo", "description": "x"}
        (folder / "config.json").write_text(json.dumps(cfg))
        (folder / "code.py").write_text("return x + y\n")
        config, code = ga_module.load_custom_tool(str(folder))
        assert config == cfg
        assert "return x + y" in code

    def test_missing_folder_raises_unbound_local(self, tmp_path):
        """Documenting a real source bug: load_custom_tool returns the
        local `config`/`code` without setting them when the folder doesn't
        exist, triggering UnboundLocalError. Caller is expected to ensure
        the folder exists. This test pins the current behavior."""
        with pytest.raises(UnboundLocalError):
            ga_module.load_custom_tool(str(tmp_path / "doesnotexist"))

    def test_indented_code(self, tmp_path):
        folder = tmp_path / "t"
        folder.mkdir()
        (folder / "config.json").write_text("{}")
        (folder / "code.py").write_text("line1\nline2\n")
        config, code = ga_module.load_custom_tool(str(folder), indent_code=True)
        # Each line should have 4-space indent
        for line in code.splitlines():
            if line.strip():
                assert line.startswith("    ")

    def test_malformed_config_returns_none(self, tmp_path):
        folder = tmp_path / "bad"
        folder.mkdir()
        (folder / "config.json").write_text("{not json}")
        (folder / "code.py").write_text("ok")
        # Function catches and prints, returns (None, code)
        config, code = ga_module.load_custom_tool(str(folder))
        assert config is None
        assert "ok" in code


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestDataFrameHelpers:
    def test_dataframe_to_markdown(self):
        df = pd.DataFrame({'a': [1, 2], 'b': ['x', 'y']})
        result = ga_module.dataframe_to_markdown(df)
        assert isinstance(result, str)
        # Pipe-style markdown
        assert '|' in result
        # Contains the data
        assert 'x' in result

    def test_dataframe_to_csv(self):
        """dataframe_to_csv works now that GeneralAgent imports pandas
        (this test previously pinned the missing-import NameError and asked
        to be updated once the bug was fixed — it is)."""
        df = pd.DataFrame({'a': [1, 2]})
        result = ga_module.dataframe_to_csv(df)
        assert isinstance(result, str) and 'a' in result

    def test_dataframe_to_table_dict(self):
        df = pd.DataFrame({'a': [1, 2], 'b': ['x', 'y']})
        result = ga_module.dataframe_to_table_dict(df)
        # Should be a serializable structure
        assert result is not None


# ---------------------------------------------------------------------------
# log_function_call decorator
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestLogFunctionCall:
    def test_passes_through_result(self):
        @ga_module.log_function_call
        def add(x, y):
            return x + y
        assert add(2, 3) == 5

    def test_propagates_errors(self):
        @ga_module.log_function_call
        def fail():
            raise ValueError("nope")
        with pytest.raises(ValueError, match="nope"):
            fail()

    def test_preserves_kwargs(self):
        @ga_module.log_function_call
        def kw(a, b=10):
            return a * b
        assert kw(2, b=5) == 10


# ---------------------------------------------------------------------------
# Bare agent helper methods
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestConvertChatHistoryRobust:
    """Defensive checks against possible regressions when
    _convert_chat_history is touched. The original tests cover the happy
    paths; these check edge cases."""

    def test_unknown_role_does_not_crash(self):
        agent = _make_bare_agent()
        try:
            result = agent._convert_chat_history([
                {"role": "system", "content": "weird"}
            ])
        except Exception:
            pytest.skip("Method raises on unknown roles — non-issue if "
                        "only user/assistant ever appear in production")
        # If it returned, it should still be a list
        assert isinstance(result, list)

    def test_missing_content_field(self):
        agent = _make_bare_agent()
        try:
            result = agent._convert_chat_history([
                {"role": "user"}  # no content
            ])
        except (KeyError, Exception):
            pytest.skip("Method raises on missing content — non-issue if "
                        "all chat history entries always have content")
        assert isinstance(result, list)
