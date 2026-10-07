"""config.anthropic_response_text — the reply text whatever the block order.

Claude 5.x models (claude-sonnet-5 = the platform primary, haiku-5-5, opus-5/5-5)
think by default and return a ``thinking`` block BEFORE the text. Every call
site used to read ``content[0]['text']`` / ``content[0].text`` and died with
KeyError/AttributeError on realistic prompts (live-reproduced 2026-10-07:
claudeQuickPrompt raised KeyError 'text' on sonnet-5 and haiku-5-5).

Force-add to git (gitignore hides test*.py).
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

text_of = config.anthropic_response_text


def test_proxy_dict_thinking_first():
    resp = {"content": [{"type": "thinking", "thinking": "", "signature": "x"},
                        {"type": "text", "text": '{"total": 1.5}'}]}
    assert text_of(resp) == '{"total": 1.5}'


def test_sdk_objects_thinking_first():
    resp = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="", signature="x"),
                                    SimpleNamespace(type="text", text="OK")])
    assert text_of(resp) == "OK"


def test_citation_split_text_blocks_are_joined():
    resp = {"content": [{"type": "text", "text": "The total is "},
                        {"type": "text", "text": "$12.50", "citations": [{"cited_text": "12.50"}]},
                        {"type": "text", "text": "."}]}
    assert text_of(resp) == "The total is $12.50."


def test_plain_single_text_block_unchanged():
    assert text_of({"content": [{"type": "text", "text": "hi"}]}) == "hi"


def test_proxy_error_dict_raises_clearly():
    with pytest.raises(ValueError, match="LLM call failed"):
        text_of({"error": "Proxy returned status code 500", "details": "upstream"})


def test_empty_content_raises():
    with pytest.raises(ValueError, match="no content"):
        text_of({"content": []})


def test_thinking_only_raises():
    # e.g. max_tokens spent while thinking: there is no answer to return
    with pytest.raises(ValueError, match="no text block"):
        text_of({"content": [{"type": "thinking", "thinking": "x"}]})


def test_streamed_response_shape():
    # anthropic_streaming_helper.StreamedResponse exposes ContentBlock(type='text')
    block = SimpleNamespace(type="text", text="streamed")
    assert text_of(SimpleNamespace(content=[block])) == "streamed"


def test_mock_blocks_with_text_strings_still_work():
    # Unit tests across the repo mock responses with MagicMock blocks whose
    # .type is itself a MagicMock — keep accepting a real .text string.
    block = MagicMock()
    block.text = "mocked"
    resp = MagicMock()
    resp.content = [block]
    assert text_of(resp) == "mocked"
