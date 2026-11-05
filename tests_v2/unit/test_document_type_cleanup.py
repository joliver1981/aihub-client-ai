"""Document type detection cannot fail an upload (2026-11-05).

For a document that matches no known type, the classifier sometimes answers
with a sentence ("The document is a test attachment file with no real business
content, so it matches none of the known types - test_document"). Cleaned up,
that was a 120+ character "type", and storing it failed the whole upload with
"String or binary data would be truncated": every type column is 100 wide.
A reply that long cannot be a type name, so it becomes unknown_document.

clean_detected_document_type is loaded by source extraction, so the test does
not import LLMDocumentEngine's dependency surface (the anthropic package lives
in the Documents API's own env).

Force-add to git (gitignore hides test*.py).
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


@pytest.fixture(scope="module")
def mod():
    src = (REPO / "LLMDocumentEngine.py").read_text(encoding="utf-8")
    m = re.search(r"DOCUMENT_TYPE_MAX_LEN = \d+.*?(?=\nclass MultiPagePDFHandler)", src, re.DOTALL)
    assert m, "clean_detected_document_type not found in LLMDocumentEngine.py"
    ns = {"logger": _Log()}
    exec(m.group(0), ns)  # noqa: S102 - own source, test-only
    return ns


OBSERVED_REPLY = ("The document is a test attachment file with no real business content, "
                  "so it matches none of the known types - test_document")


@pytest.mark.parametrize("reply,expected", [
    ("invoice", "invoice"),
    ("Bill of Lading", "bill_of_lading"),
    ("  Purchase-Order \n", "purchase_order"),
    ("_memo", "memo"),
    ("", ""),
    (None, ""),
])
def test_type_names_are_cleaned_as_before(mod, reply, expected):
    assert mod["clean_detected_document_type"](reply) == expected


def test_a_sentence_becomes_unknown_document(mod):
    log = _Log()
    assert mod["clean_detected_document_type"](OBSERVED_REPLY, log) == "unknown_document"
    assert len(log.warnings) == 1 and "not a type name" in log.warnings[0]


def test_the_limit_is_the_width_of_the_type_columns(mod):
    limit = mod["DOCUMENT_TYPE_MAX_LEN"]
    assert limit == 100          # Documents.document_type, DocumentTypeCategories, ...
    fits = "a" * limit
    assert mod["clean_detected_document_type"](fits) == fits
    assert mod["clean_detected_document_type"](fits + "b", _Log()) == "unknown_document"


def test_detection_uses_the_cleanup():
    src = (REPO / "LLMDocumentEngine.py").read_text(encoding="utf-8")
    body = src[src.index("def _detect_document_type"):src.index("def _get_text_from_other_documents")]
    assert "clean_detected_document_type(document_type" in body
