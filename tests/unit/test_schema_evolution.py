"""Schema evolution (LLMDocumentProcessor._evolve_schema + observation channel).

A schema is learned from the FIRST document of its type; evolution lets later
documents ADD fields through a merge gate. The invariants locked down here:

  * additions ONLY — existing fields are never renamed or removed (a rename
    would orphan every stored DocumentFields row);
  * the gate can only approve names it was actually offered;
  * the per-type toggle (allow_evolution) and the global kill-switch both stop
    it cold, before any LLM call;
  * a failure anywhere leaves the schema file byte-identical and never raises
    into the ingest;
  * the approving document's own values come back so it stores them.
"""
import json
import os
import sys
import types
from unittest.mock import patch

import pytest
import yaml

for _name, _attrs in (('anthropic', ('Anthropic',)),
                      ('PyPDF2', ('PdfReader', 'PdfWriter'))):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            _stub = types.ModuleType(_name)

            class _Stub:
                def __init__(self, *a, **k):
                    pass

            for _attr in _attrs:
                setattr(_stub, _attr, _Stub)
            sys.modules[_name] = _stub

import config as cfg  # noqa: E402
from LLMDocumentEngine import LLMDocumentProcessor  # noqa: E402


SCHEMA = {
    'document_type': 'zz_evo_lease',
    'fields': {
        'execution_date': {'description': 'Date signed'},
        'parties.lessor.name': {'description': 'Landlord name'},
    },
}

OBS = {
    'percentage_rent_rate': {'value': '4% of gross sales', 'page': 12,
                             'description': 'Percentage rent over breakpoint'},
    'co_tenancy_clause': {'value': 'anchor must operate', 'page': 19,
                          'description': 'Co-tenancy condition'},
}


@pytest.fixture
def engine(tmp_path):
    """Processor whose schema_dir is a temp dir holding one real YAML file."""
    with patch.object(LLMDocumentProcessor, '_load_schemas', return_value={}):
        eng = LLMDocumentProcessor(sql_connection_string=None,
                                   schema_dir=str(tmp_path))
    with open(tmp_path / 'zz_evo_lease_auto.yml', 'w', encoding='utf-8') as f:
        yaml.dump(SCHEMA, f, sort_keys=False)
    eng.schemas = {'zz_evo_lease': yaml.safe_load(
        (tmp_path / 'zz_evo_lease_auto.yml').read_text(encoding='utf-8'))}
    return eng


def _gate(engine, reply):
    """Context managers wiring the merge-gate LLM to return `reply`."""
    engine.anthropic_proxy_client = None
    return (patch.object(engine, '_anthropic_config',
                         {'use_direct_api': False}),
            patch('LLMDocumentEngine.AnthropicProxyClient'))


def _run(engine, reply, observations=OBS, doc_type='zz_evo_lease'):
    cm1, cm2 = _gate(engine, reply)
    with cm1, cm2 as mock_client:
        mock_client.return_value.messages_create.return_value = {
            'content': [{'text': reply}]}
        return engine._evolve_schema(doc_type, observations)


def _disk(engine, doc_type='zz_evo_lease'):
    return yaml.safe_load(open(
        os.path.join(engine.schema_dir, f'{doc_type}_auto.yml'),
        encoding='utf-8'))


@pytest.mark.unit
class TestEvolveSchema:
    def test_approved_fields_added_with_marker_and_backup(self, engine):
        out = _run(engine, json.dumps({'add': {
            'percentage_rent_rate': 'Percentage rent over the breakpoint'}}))
        on_disk = _disk(engine)
        assert 'percentage_rent_rate' in on_disk['fields']
        assert on_disk['fields']['percentage_rent_rate']['evolved']
        # original fields untouched, rejected candidate absent
        assert on_disk['fields']['execution_date'] == {'description': 'Date signed'}
        assert 'co_tenancy_clause' not in on_disk['fields']
        # in-memory schema updated too (this process keeps extracting with it)
        assert 'percentage_rent_rate' in engine.schemas['zz_evo_lease']['fields']
        # the observing document's own value comes back for storage
        assert out == {'percentage_rent_rate':
                       {'value': '4% of gross sales', 'page': 12}}
        hist = os.listdir(os.path.join(engine.schema_dir, '_history'))
        assert len(hist) == 1 and hist[0].startswith('zz_evo_lease_auto.yml.')

    def test_gate_rejects_all_leaves_file_untouched(self, engine):
        before = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                      encoding='utf-8').read()
        out = _run(engine, '{"add": {}}')
        after = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                     encoding='utf-8').read()
        assert out == {} and before == after
        assert not os.path.isdir(os.path.join(engine.schema_dir, '_history'))

    def test_gate_cannot_approve_names_it_was_not_offered(self, engine):
        out = _run(engine, json.dumps({'add': {
            'hallucinated_field': 'made up',
            'co_tenancy_clause': 'Co-tenancy condition'}}))
        on_disk = _disk(engine)
        assert 'hallucinated_field' not in on_disk['fields']
        assert 'co_tenancy_clause' in on_disk['fields']
        assert set(out) == {'co_tenancy_clause'}

    def test_existing_fields_never_shrink(self, engine):
        _run(engine, json.dumps({'add': {
            'percentage_rent_rate': 'x', 'co_tenancy_clause': 'y'}}))
        on_disk = _disk(engine)
        for name in SCHEMA['fields']:
            assert on_disk['fields'][name] == SCHEMA['fields'][name]

    def test_per_type_toggle_off_stops_before_llm(self, engine):
        engine.schemas['zz_evo_lease']['allow_evolution'] = False
        with patch.object(engine, '_response_text',
                          side_effect=AssertionError('must not call LLM')):
            assert engine._evolve_schema('zz_evo_lease', OBS) == {}

    def test_global_kill_switch(self, engine, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SCHEMA_EVOLUTION', False, raising=False)
        with patch.object(engine, '_response_text',
                          side_effect=AssertionError('must not call LLM')):
            assert engine._evolve_schema('zz_evo_lease', OBS) == {}

    def test_known_fields_filtered_case_insensitively(self, engine):
        obs = {'Execution_Date': {'value': 'x', 'page': 1, 'description': 'd'}}
        with patch.object(engine, '_response_text',
                          side_effect=AssertionError('must not call LLM')):
            assert engine._evolve_schema('zz_evo_lease', obs) == {}

    def test_llm_failure_never_raises_file_untouched(self, engine):
        before = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                      encoding='utf-8').read()
        cm1, cm2 = _gate(engine, '')
        with cm1, cm2 as mock_client:
            mock_client.return_value.messages_create.side_effect = \
                RuntimeError('llm down')
            assert engine._evolve_schema('zz_evo_lease', OBS) == {}
        after = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                     encoding='utf-8').read()
        assert before == after

    def test_unknown_type_or_no_observations_noop(self, engine):
        assert engine._evolve_schema('no_such_type', OBS) == {}
        assert engine._evolve_schema('zz_evo_lease', {}) == {}


@pytest.mark.unit
class TestObservationChannel:
    """_extract_document_level_fields collects _unlisted safely."""

    def _pages(self):
        return [{'page_number': 1, 'text': 'Page one text'},
                {'page_number': 2, 'text': 'Page two text'}]

    def test_unlisted_collected_and_values_survive(self, engine):
        def fake_extract(text, specs, dt, with_pages=False,
                         observe_unlisted=False):
            assert observe_unlisted is True
            return {'execution_date': {'value': '2024-01-01', 'page': 1},
                    '_unlisted': {
                        'renewal_option': {'value': 'two 5-year terms',
                                           'page': 2, 'description': 'Renewals'},
                        'execution_date': {'value': 'dup of known', 'page': 1,
                                           'description': 'shadow'},
                        'bad_entry': 'not a dict'}}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            by_page, unlisted = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert by_page[1] == {'execution_date': '2024-01-01'}
        assert set(unlisted) == {'renewal_option'}
        assert unlisted['renewal_option']['page'] == 2

    def test_toggle_off_means_no_observation_request(self, engine):
        engine.schemas['zz_evo_lease']['allow_evolution'] = False
        seen = {}
        def fake_extract(text, specs, dt, with_pages=False,
                         observe_unlisted=False):
            seen['observe'] = observe_unlisted
            return {}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            by_page, unlisted = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert seen['observe'] is False and unlisted == {}

    def test_out_of_range_page_snaps_into_chunk(self, engine):
        def fake_extract(text, specs, dt, with_pages=False,
                         observe_unlisted=False):
            return {'_unlisted': {'weird_field': {
                'value': 'v', 'page': 999, 'description': 'd'}}}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            _by_page, unlisted = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert unlisted['weird_field']['page'] == 1
