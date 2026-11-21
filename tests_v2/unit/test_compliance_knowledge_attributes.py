"""Compliance pipeline: every document it processes carries its retailer as "customer".

The customer comes from the run — the workflow knows whose folder the file came from —
so the agent's knowledge search can be limited to that customer whatever the file is
called. Extra attributes the node supplies ride along; none of them can replace the
customer. A document that cannot be tagged is still processed.

All boundaries are mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import compliance_engine as ce
import knowledge_scope as ks
from compliance_engine import ComplianceEngine


@pytest.fixture
def engine():
    return ComplianceEngine()


def _attach(engine, retailer, extra=None, stored="echo"):
    seen = {}

    def fake_set(document_id, attributes, replace=False):
        seen["document_id"], seen["attributes"] = document_id, dict(attributes)
        return dict(attributes) if stored == "echo" else stored

    with patch.object(ComplianceEngine, "get_retailer", staticmethod(lambda rid: retailer)), \
         patch.object(ks, "set_document_attributes", fake_set):
        out = engine._attach_attributes("doc-1", 7, extra)
    return out, seen


def test_retailer_is_attached_as_customer(engine):
    out, seen = _attach(engine, {"retailer_id": 7, "name": "  Acme   Retail "})
    assert seen == {"document_id": "doc-1", "attributes": {"customer": "Acme Retail"}}
    assert out == {"customer": "Acme Retail"}


def test_extra_attributes_ride_along_but_never_replace_the_customer(engine):
    out, seen = _attach(engine, {"name": "Acme Retail"},
                        {"Region": "East", "customer": "Somebody Else", "empty": ""})
    assert seen["attributes"] == {"region": "East", "customer": "Acme Retail"}


def test_nothing_to_attach_is_not_a_write(engine):
    out, seen = _attach(engine, None)
    assert out is None and seen == {}


def test_a_document_that_cannot_be_tagged_is_still_processed(engine):
    out, _seen = _attach(engine, {"name": "Acme Retail"}, stored=None)
    assert out is None                                            # reported, not raised
    with patch.object(ComplianceEngine, "get_retailer", staticmethod(lambda rid: {"name": "Acme"})), \
         patch.object(ks, "set_document_attributes", side_effect=RuntimeError("database down")):
        assert engine._attach_attributes("doc-1", 7) is None


def test_pipeline_attaches_before_the_document_reaches_the_agent(engine):
    order = []
    with patch.object(engine, "_file_hash", return_value="h"), \
         patch.object(engine, "_check_duplicate", return_value=None), \
         patch.object(engine, "_ingest_document", return_value="doc-1"), \
         patch.object(engine, "_store_document_hash"), \
         patch.object(engine, "_attach_attributes",
                      side_effect=lambda *a, **k: order.append(("attach", a))) as attach, \
         patch.object(engine, "_create_version", return_value=(11, 1)), \
         patch.object(ComplianceEngine, "get_document_set", staticmethod(lambda sid: {})), \
         patch.object(engine, "_extract_requirements", return_value=[]), \
         patch.object(engine, "_store_requirements"), \
         patch.object(engine, "_check_manual_versions", return_value={"note": "n", "older_existing": []}), \
         patch.object(engine, "_index_to_knowledge",
                      side_effect=lambda *a, **k: order.append(("index", a))):
        result = engine.process_compliance_document(
            "C:/in/scan_0042.pdf", retailer_id=7, set_id=3, agent_id=55,
            knowledge_attributes={"region": "East"})
    assert result.error is None
    assert [step for step, _ in order] == ["attach", "index"]
    attach.assert_called_once_with("doc-1", 7, {"region": "East"})


def test_duplicate_file_is_not_retagged(engine):
    with patch.object(engine, "_file_hash", return_value="h"), \
         patch.object(engine, "_check_duplicate",
                      return_value={"version_id": 1, "version_number": 1, "document_id": "doc-0"}), \
         patch.object(engine, "_duplicate_knowledge_note", return_value="held"), \
         patch.object(engine, "_attach_attributes") as attach:
        result = engine.process_compliance_document("C:/in/a.pdf", retailer_id=7, set_id=3, agent_id=55)
    assert result.is_duplicate
    attach.assert_not_called()
