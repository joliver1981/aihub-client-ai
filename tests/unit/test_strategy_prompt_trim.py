"""Step-3 strategy prompt: compact field catalogue + per-lane model knob.

Before 2026-09-01 the strategy prompt embedded every universe field as six-key
indent=2 JSON (102,423 chars / ~28.9K tokens on the pack-23 lease corpus) on
every search, on the main model. It now embeds {name, document_count} compact
JSON (24,440 chars / ~5.5K tokens), keeping sample_values when present and
document_types only where a field is narrower than the search scope. The
strategy call also honours DOC_SEARCH_STRATEGY_MODEL / the admin-UI
'doc_search_strategy' override, failing open to the system model.
See docs/search-token-cost-handoff.md.
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# DocUtils drags in heavy deps at import; stub what this box's test env lacks.
for _name in ('anthropic', 'PyPDF2', 'fitz', 'openai'):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

import DocUtils  # noqa: E402

Q = "what length is the term of Summit Center, Boston lease?"


def _field(name, n, types_, samples=None):
    """A universe field_metadata entry exactly as get_document_universe builds it."""
    return {"name": name, "display_name": name.replace('_', ' ').title(),
            "count": n * 3, "document_count": n, "document_types": types_,
            "sample_values": samples or []}


# =============================================================================
# The pure helper
# =============================================================================
@pytest.mark.unit
class TestStrategyFieldBlock:
    def test_single_type_scope_keeps_only_name_and_count(self):
        fields = [_field("city", 185, ["lease_agreement"]),
                  _field("term_years", 180, ["lease_agreement"])]
        fj, aj = DocUtils._strategy_field_block(fields, [], ["lease_agreement"])
        assert json.loads(fj) == [{"name": "city", "document_count": 185},
                                  {"name": "term_years", "document_count": 180}]
        assert aj == "[]"
        for packaging in ("display_name", '"count"', "sample_values", "document_types", "\n", ": "):
            assert packaging not in fj

    def test_sample_values_survive_when_present(self):
        fields = [_field("city", 185, ["lease_agreement"], samples=["Boston, MA", "Chicago, IL"])]
        fj, _ = DocUtils._strategy_field_block(fields, [], ["lease_agreement"])
        assert json.loads(fj)[0]["sample_values"] == ["Boston, MA", "Chicago, IL"]

    def test_document_types_kept_only_where_narrower_than_scope(self):
        scope = ["lease_agreement", "invoice"]
        fields = [_field("name", 500, ["lease_agreement", "invoice"]),
                  _field("rent_amount", 185, ["lease_agreement"])]
        fj, _ = DocUtils._strategy_field_block(fields, [], scope)
        parsed = json.loads(fj)
        assert "document_types" not in parsed[0], "field spanning the whole scope is packaging"
        assert parsed[1]["document_types"] == ["lease_agreement"], "narrower list is information"

    def test_no_type_filter_uses_universe_scope(self):
        # No planner type pick: scope is the whole universe, so a field held by
        # a subset of types keeps its list and one held by every type drops it.
        universe_types = ["lease_agreement", "invoice", "resume"]
        fields = [_field("name", 900, universe_types),
                  _field("rent_amount", 185, ["lease_agreement"])]
        fj, _ = DocUtils._strategy_field_block(fields, [], universe_types)
        parsed = json.loads(fj)
        assert "document_types" not in parsed[0]
        assert parsed[1]["document_types"] == ["lease_agreement"]

    def test_attributes_are_compacted_not_trimmed(self):
        attrs = [{"attribute_name": "region", "usage_count": 3,
                  "documents_with_attribute": 2, "sample_values": ["East", "West"]}]
        _, aj = DocUtils._strategy_field_block([], attrs, ["x"])
        assert json.loads(aj) == attrs
        assert "\n" not in aj and ": " not in aj

    def test_empty_inputs(self):
        assert DocUtils._strategy_field_block([], [], []) == ("[]", "[]")
        assert DocUtils._strategy_field_block(None, None, None) == ("[]", "[]")

    def test_saving_on_a_500_field_universe(self):
        fields = [_field(f"lease_field_number_{i:03d}", 185, ["lease_agreement"]) for i in range(500)]
        old = json.dumps(fields, indent=2)
        new, _ = DocUtils._strategy_field_block(fields, [], ["lease_agreement"])
        assert len(new) < 0.30 * len(old), f"{len(new)} vs {len(old)}"
        assert len(json.loads(new)) == 500, "every field name must survive"


# =============================================================================
# Through the pipeline: what Step 3 actually receives, and which model runs it
# =============================================================================
UNIVERSE = json.dumps({
    "field_metadata": [_field("city", 185, ["lease_agreement"]),
                       _field("term_years", 180, ["lease_agreement"])],
    "custom_attribute_metadata": [],
    "document_types": [{"type": "lease_agreement", "count": 185}],
    "document_counts": [],
})

STRATEGY = {"search_approach": "semantic", "reasoning": "r", "confidence": "high",
            "semantic_search": {"search_terms": ["Summit Center Boston lease term"]}}


def _fake_vector_module():
    mod = types.ModuleType("vector_engine_client")

    class VectorEngineClient:
        def search_for_ai(self, term, filters=None):
            return {"results": []}

    mod.VectorEngineClient = VectorEngineClient
    return mod


def _run(strategy_fn, model_knob='', use_ai_fields=False, field_selector=None):
    """Run the full function with every external dependency stubbed; return
    (output, list of azureQuickPrompt kwargs)."""
    calls = []

    def fake_quick(**kw):
        calls.append(kw)
        return strategy_fn(kw)

    patches = [
        patch.object(DocUtils, "get_document_types",
                     lambda allowed_document_types=None:
                     json.dumps({"document_types": ["lease_agreement"]})),
        patch.object(DocUtils, "azureMiniQuickPrompt", lambda **kw: '["lease_agreement"]'),
        patch.object(DocUtils, "get_document_universe", lambda *a, **kw: UNIVERSE),
        patch.object(DocUtils, "azureQuickPrompt", fake_quick),
        patch.object(DocUtils, "document_search", lambda **kw: json.dumps({"results": []})),
        patch.object(DocUtils, "_strategy_model_override", lambda: model_knob),
        patch.object(DocUtils.cfg, "DOC_USE_AI_SELECTED_FIELDS", use_ai_fields),
        patch.object(DocUtils.cfg, "DOC_USE_LLM_RERANK", False, create=True),
        patch.dict(sys.modules, {"vector_engine_client": _fake_vector_module()}),
    ]
    if field_selector is not None:
        patches.append(patch.object(DocUtils, "ai_select_relevant_fields", field_selector))
    for p in patches:
        p.start()
    try:
        out = DocUtils.document_search_super_enhanced_debug(
            "conn-string", user_question=Q, max_results=50, check_completeness=False)
    finally:
        for p in reversed(patches):
            p.stop()
    return out, calls


@pytest.mark.unit
class TestStrategyPromptThroughPipeline:
    def test_prompt_embeds_the_compact_catalogue(self):
        _, calls = _run(lambda kw: json.dumps(STRATEGY))
        assert len(calls) == 1
        prompt = calls[0]["prompt"]
        assert '{"name":"city","document_count":185}' in prompt
        assert '{"name":"term_years","document_count":180}' in prompt
        # key forms: the label legitimately names sample_values / document_types
        for packaging in ('"display_name":', '"sample_values":', '"count":', '"document_types":',
                          "Detailed field metadata"):
            assert packaging not in prompt
        assert "Available custom attributes" in prompt

    def test_default_call_carries_no_model_kwarg(self):
        _, calls = _run(lambda kw: json.dumps(STRATEGY), model_knob='')
        assert "model" not in calls[0], "unset knob must leave the call exactly as before"

    def test_knob_routes_the_strategy_call(self):
        out, calls = _run(lambda kw: json.dumps(STRATEGY), model_knob='gpt-5.6-luna')
        assert len(calls) == 1
        assert calls[0]["model"] == "gpt-5.6-luna"
        assert json.loads(out)["search_strategy"]["search_approach"] == "semantic"

    def test_failed_override_retries_on_the_system_model(self):
        def strategy(kw):
            if kw.get("model"):
                raise RuntimeError("404 deployment not found")
            return json.dumps(STRATEGY)

        out, calls = _run(strategy, model_knob='gpt-typo')
        assert [c.get("model") for c in calls] == ["gpt-typo", None]
        assert json.loads(out)["search_strategy"]["search_approach"] == "semantic"

    def test_step2_input_is_untouched(self):
        # The write-up's scope rule: only the Step-3 embed changes. Step 2 keeps
        # receiving the {field_name, document_count} shape it always did.
        seen = {}

        def selector(user_question, fields, attributes, max_fields=8):
            seen["fields"] = fields
            seen["attributes"] = attributes
            return {"selected_fields": ["city"], "reasoning": "r", "confidence": "high"}

        _, calls = _run(lambda kw: json.dumps(STRATEGY), use_ai_fields=True, field_selector=selector)
        assert seen["fields"] == [{"field_name": "city", "document_count": 185},
                                  {"field_name": "term_years", "document_count": 180}]
        assert seen["attributes"] == []
        assert "AI Suggested Fields: ['city']" in calls[0]["prompt"]


@pytest.mark.unit
class TestStrategyModelOverrideResolution:
    def test_live_override_wins_over_env_pin(self, monkeypatch):
        import model_overrides
        monkeypatch.setattr(DocUtils.cfg, "DOC_SEARCH_STRATEGY_MODEL", "from-env", raising=False)
        monkeypatch.setattr(model_overrides, "load_overrides",
                            lambda: {"doc_search_strategy": "gpt-ui-pick"})
        assert DocUtils._strategy_model_override() == "gpt-ui-pick"

    def test_env_pin_when_no_live_override(self, monkeypatch):
        import model_overrides
        monkeypatch.setattr(DocUtils.cfg, "DOC_SEARCH_STRATEGY_MODEL", " from-env ", raising=False)
        monkeypatch.setattr(model_overrides, "load_overrides", lambda: {})
        assert DocUtils._strategy_model_override() == "from-env"

    def test_blank_everywhere_means_system_model(self, monkeypatch):
        import model_overrides
        monkeypatch.setattr(DocUtils.cfg, "DOC_SEARCH_STRATEGY_MODEL", "", raising=False)
        monkeypatch.setattr(model_overrides, "load_overrides", lambda: {"doc_search_strategy": ""})
        assert DocUtils._strategy_model_override() == ""
