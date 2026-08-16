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
        def fake_extract(text, specs, dt, with_pages=False, observe_repeating=False,
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
            by_page, unlisted, _rep = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert by_page[1] == {'execution_date': '2024-01-01'}
        assert set(unlisted) == {'renewal_option'}
        assert unlisted['renewal_option']['page'] == 2

    def test_toggle_off_means_no_observation_request(self, engine):
        engine.schemas['zz_evo_lease']['allow_evolution'] = False
        seen = {}
        def fake_extract(text, specs, dt, with_pages=False, observe_repeating=False,
                         observe_unlisted=False):
            seen['observe'] = observe_unlisted
            return {}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            by_page, unlisted, _rep = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert seen['observe'] is False and unlisted == {}

    def test_out_of_range_page_snaps_into_chunk(self, engine):
        def fake_extract(text, specs, dt, with_pages=False, observe_repeating=False,
                         observe_unlisted=False):
            return {'_unlisted': {'weird_field': {
                'value': 'v', 'page': 999, 'description': 'd'}}}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            _by_page, unlisted, _rep = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert unlisted['weird_field']['page'] == 1


@pytest.mark.unit
class TestRecordSetAutoDefine:
    """Sightings ledger + evidence-gated auto-define (_handle_repeating_unit).

    The contract: one document is an anecdote, N documents are evidence; the
    DEFINING document is always one that exhibits the unit; auto off degrades
    to flag-only; a rejected definition changes nothing."""

    UNIT = {'name': 'safety_procedures', 'grain': 'one row per procedure',
            'example_columns': ['procedure', 'frequency'], 'pages': [2],
            'chunks_agreeing': 2}

    def _pages(self):
        return [{'page_number': 1, 'text': 'intro'},
                {'page_number': 2, 'text': 'PROC-1 lockout weekly and '
                                           'PROC-2 inspection monthly'}]

    def test_sightings_accumulate_and_dedupe_per_document(self, engine):
        e1 = engine._note_repeating_unit('zz_evo_lease', self.UNIT, 'd1', 'a.pdf')
        e2 = engine._note_repeating_unit('zz_evo_lease', self.UNIT, 'd1', 'a.pdf')
        e3 = engine._note_repeating_unit('zz_evo_lease', self.UNIT, 'd2', 'b.pdf')
        assert len(e1['sightings']) == 1
        assert len(e2['sightings']) == 1, 're-ingest is not new evidence'
        assert len(e3['sightings']) == 2
        assert os.path.exists(engine._repeating_flag_path('zz_evo_lease'))

    def test_first_sighting_notifies_but_does_not_define(self, engine,
                                                         monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_RECORDS_AUTODEFINE', True, raising=False)
        monkeypatch.setattr(cfg, 'DOC_RECORDS_AUTODEFINE_SIGHTINGS', 2,
                            raising=False)
        calls = []
        with patch.object(engine, '_notify_repeating_unit',
                          side_effect=lambda *a, **k: calls.append(a)), \
             patch.object(engine, '_define_record_set',
                          side_effect=AssertionError('must not define at 1')):
            engine._handle_repeating_unit('zz_evo_lease', self.UNIT,
                                          'd1', 'a.pdf', self._pages())
        assert len(calls) == 1
        assert 'records' not in engine.schemas['zz_evo_lease']

    def test_threshold_defines_from_document_in_hand(self, engine, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_RECORDS_AUTODEFINE', True, raising=False)
        monkeypatch.setattr(cfg, 'DOC_RECORDS_AUTODEFINE_SIGHTINGS', 2,
                            raising=False)
        defined = {}

        def fake_define(dt, entry, pages):
            defined['entry'] = entry
            defined['pages'] = pages
            return True

        with patch.object(engine, '_notify_repeating_unit'), \
             patch.object(engine, '_define_record_set',
                          side_effect=fake_define):
            engine._handle_repeating_unit('zz_evo_lease', self.UNIT,
                                          'd1', 'a.pdf', self._pages())
            engine._handle_repeating_unit('zz_evo_lease', self.UNIT,
                                          'd2', 'b.pdf', self._pages())
        assert len(defined['entry']['sightings']) == 2
        assert defined['pages'] == self._pages(), \
            'must define from the document IN HAND'

    def test_auto_off_is_flag_only(self, engine, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_RECORDS_AUTODEFINE', False, raising=False)
        with patch.object(engine, '_notify_repeating_unit'), \
             patch.object(engine, '_define_record_set',
                          side_effect=AssertionError('flag-only must not define')):
            for d, f in (('d1', 'a.pdf'), ('d2', 'b.pdf'), ('d3', 'c.pdf')):
                engine._handle_repeating_unit('zz_evo_lease', self.UNIT,
                                              d, f, self._pages())
        flag = engine._repeating_flag_path('zz_evo_lease')
        assert os.path.exists(flag), 'evidence still accumulates for the badge'

    def test_schema_with_records_never_handles(self, engine):
        engine.schemas['zz_evo_lease']['records'] = {
            'rent_schedule': {'columns': {'x': 'y'}}}
        with patch.object(engine, '_note_repeating_unit',
                          side_effect=AssertionError('must not even note')):
            engine._handle_repeating_unit('zz_evo_lease', self.UNIT,
                                          'd1', 'a.pdf', self._pages())

    def test_define_writes_set_clears_flag_backs_up(self, engine):
        engine._note_repeating_unit('zz_evo_lease', self.UNIT, 'd1', 'a.pdf')
        reply = json.dumps({'records': {'safety_procedures': {
            'grain': 'one row per procedure', 'expected_rows': 2,
            'columns': {'procedure': 'The procedure', 'frequency': 'How often'},
            'vocabulary': {'frequency': ['weekly', 'monthly']}}}})
        cm1, cm2 = _gate(engine, reply)
        with cm1, cm2 as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'text': reply}]}
            ok = engine._define_record_set(
                'zz_evo_lease',
                {'name': 'safety_procedures', 'grain': 'one row per procedure',
                 'example_columns': ['procedure'],
                 'sightings': [{'document_id': 'd1', 'filename': 'a.pdf'},
                               {'document_id': 'd2', 'filename': 'b.pdf'}]},
                self._pages())
        assert ok is True
        on_disk = _disk(engine)
        spec = on_disk['records']['safety_procedures']
        assert spec['evolved'] and 'procedure' in spec['columns']
        assert 'source_pages' in spec['columns']
        # sighting provenance persisted — the coverage UI badges these docs
        assert spec['first_sighted_in'] == ['d1', 'd2']
        assert on_disk['fields'] == SCHEMA['fields']
        assert not os.path.exists(engine._repeating_flag_path('zz_evo_lease'))
        assert len(os.listdir(os.path.join(engine.schema_dir, '_history'))) == 1

    def test_rejected_definition_changes_nothing(self, engine):
        before = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                      encoding='utf-8').read()
        cm1, cm2 = _gate(engine, '{"records": {}}')
        with cm1, cm2 as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'text': '{"records": {}}'}]}
            ok = engine._define_record_set(
                'zz_evo_lease', {'name': 'toc_entries', 'sightings': []},
                self._pages())
        after = open(os.path.join(engine.schema_dir, 'zz_evo_lease_auto.yml'),
                     encoding='utf-8').read()
        assert ok is False and before == after

    def test_detection_collects_repeating_unit(self, engine):
        def fake_extract(text, specs, dt, with_pages=False,
                         observe_unlisted=False, observe_repeating=False):
            assert observe_repeating is True, \
                'record-less schema must ask about repeating units'
            return {'_repeating_unit': {
                'name': 'safety_procedures', 'grain': 'one row per procedure',
                'example_columns': ['procedure'], 'pages': [2]}}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            _bp, _ul, rep = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert rep['name'] == 'safety_procedures'
        assert rep['chunks_agreeing'] >= 1

    def test_detection_off_when_schema_has_records(self, engine):
        engine.schemas['zz_evo_lease']['records'] = {
            'rent_schedule': {'columns': {'x': 'y'}}}

        def fake_extract(text, specs, dt, with_pages=False,
                         observe_unlisted=False, observe_repeating=False):
            assert observe_repeating is False
            return {}
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=fake_extract):
            _bp, _ul, rep = engine._extract_document_level_fields(
                self._pages(), 'zz_evo_lease', engine.schemas['zz_evo_lease'])
        assert rep is None


@pytest.mark.unit
class TestLearnTimeMinRowsFloor:
    """DOC_RECORDS_MIN_LEARN_ROWS: a tiny set judged from ONE document must not
    become the type's permanent record set (day-1 lockout); it is withheld and
    seeded as a sighting so the evidence path can define the real unit."""

    def _consolidate(self, engine, records_json, monkeypatch, floor=5):
        monkeypatch.setattr(cfg, 'DOC_RECORDS_MIN_LEARN_ROWS', floor,
                            raising=False)
        reply = json.dumps({'fields': {'program_name': 'The program name'},
                            'records': records_json})
        cm1, cm2 = _gate(engine, reply)
        with cm1, cm2 as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'text': reply}]}
            return engine._consolidate_schema_fields('zz_floor_type',
                                                     ['program_name'])

    SMALL = {'severity_classes': {'grain': 'one row per severity class',
                                  'expected_rows': 3,
                                  'columns': {'severity': 'The class',
                                              'timeframe': 'Response time'}}}
    BIG = {'requirements': {'grain': 'one row per requirement',
                            'expected_rows': 112,
                            'columns': {'requirement': 'The requirement',
                                        'deadline': 'When'}}}

    def test_small_set_withheld_and_reported(self, engine, monkeypatch):
        out = self._consolidate(engine, self.SMALL, monkeypatch)
        assert out['records'] == {}
        fl = out['floored_records']
        assert fl['name'] == 'severity_classes'
        assert 'severity' in fl['example_columns']
        assert 'source_pages' not in fl['example_columns'], \
            'provenance columns are not observation evidence'

    def test_big_set_passes_the_floor(self, engine, monkeypatch):
        out = self._consolidate(engine, self.BIG, monkeypatch)
        assert 'requirements' in out['records']
        assert out['floored_records'] is None

    def test_floor_zero_disables(self, engine, monkeypatch):
        out = self._consolidate(engine, self.SMALL, monkeypatch, floor=0)
        assert 'severity_classes' in out['records']

    def test_missing_estimate_goes_the_evidence_route(self, engine, monkeypatch):
        no_est = {'things': {'grain': 'one row per thing',
                             'columns': {'thing': 'A thing'}}}
        out = self._consolidate(engine, no_est, monkeypatch)
        assert out['records'] == {}
        assert out['floored_records']['name'] == 'things'

    def test_save_ai_schema_seeds_sighting_when_floored(self, engine,
                                                        monkeypatch):
        consolidated = {'fields': {'program_name': 'The program name'},
                        'records': {},
                        'floored_records': {'name': 'severity_classes',
                                            'grain': 'one row per class',
                                            'example_columns': ['severity']}}
        with patch.object(engine, '_consolidate_schema_fields',
                          return_value=consolidated):
            engine._save_ai_schema('zz_floor_type', {'program_name': 'X'},
                                   doc_id='docA', filename='overview.pdf')
        on_disk = yaml.safe_load(open(
            os.path.join(engine.schema_dir, 'zz_floor_type_auto.yml'),
            encoding='utf-8'))
        assert 'records' not in on_disk
        flag = json.load(open(engine._repeating_flag_path('zz_floor_type'),
                              encoding='utf-8'))
        entry = flag['units']['severity_classes']
        assert [s['document_id'] for s in entry['sightings']] == ['docA']

    def test_no_seed_without_doc_identity(self, engine):
        consolidated = {'fields': {'f': 'd'}, 'records': {},
                        'floored_records': {'name': 'x', 'grain': '',
                                            'example_columns': []}}
        with patch.object(engine, '_consolidate_schema_fields',
                          return_value=consolidated):
            engine._save_ai_schema('zz_floor_type', {'f': 'v'})
        assert not os.path.exists(engine._repeating_flag_path('zz_floor_type'))
