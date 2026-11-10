"""ILLUSTRATIVE — fake inference is not Qwen quality or market validation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import jsonschema

from extensions import research_v3 as r, research_v3_report as report, research_v3_contract as contract
from extensions import semantic_v3, local_inference
from extensions.tests.test_exposure_v3 import packet, grounded, proposition
from extensions.tests.test_semantic_v3 import proposal
from extensions.tests.test_analysis_contract import CUTOFF
from research.validation import stable_hash, digest


class Scripted:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.messages = []
        self.calls = self.prompt_tokens = self.completion_tokens = 0

    def ask(self, messages, schema):
        self.calls += 1
        self.messages.append(deepcopy(messages))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value), {'inference': 'MOCK_NOT_ACTUAL_MODEL', 'prompt_tokens': 0, 'completion_tokens': 0}


def research(decision='PROPOSE', routes=None):
    return {'decision': decision, 'selected_route_ids': ['financing_cost'] if routes is None else routes,
            'unresolved_conditions': []}


def critic(verdict='SUPPORTED_CONDITIONAL', counters=None):
    return {'verdict': verdict, 'counter_evidence_status': 'FOUND' if counters else 'NOT_FOUND_OR_UNAVAILABLE',
            'counter_evidence': counters or [], 'cautions': ['expectations_unmeasured', 'stock_horizon_unverified']}


def model(ai, research_raw=None, critic_raw=None):
    q = ai['official_documents'][0]['paragraphs'][0]['text']
    return Scripted([{'propositions': [proposal(q)]}, research_raw or research(), critic_raw or critic()])


def test_source_grounded_supported_conditional_keeps_net_and_stock_unknown():
    ai = grounded(); backend = model(ai)
    a = r.analyze(ai, backend)
    assert a['cards'][0]['final_status'] == 'SUPPORTED_CONDITIONAL'
    assert a['cards'][0]['net_effect'] == 'UNKNOWN'
    assert a['released_score'] is None and a['released_target'] is None
    report.replay_analysis(ai, a)


def test_agreeing_models_cannot_rescue_unverified_exposure():
    ai = packet(); backend = model(ai)
    a = r.analyze(ai, backend)
    card = a['cards'][0]
    assert card['critic_raw_output']['raw_output']['verdict'] == 'SUPPORTED_CONDITIONAL'
    assert card['final_status'] == 'INSUFFICIENT_EVIDENCE'
    assert card['deterministic_validation']['evidence']['evidence_verification'] == 'incomplete'
    assert not card['critic_statistically_independent']


def test_semantic_false_approval_is_retained_but_external_rule_blocks_it():
    ai = grounded(); q = 'Interest rates did not increase.'
    ai = packet(q, ai['financial_facts'])
    backend = Scripted([{'propositions': [proposal(q, negation='negated')]}, research(), critic()])
    a = r.analyze(ai, backend)
    assert a['propositions'][0]['evidence_status'] == 'CONTRADICTED'
    assert a['cards'][0]['final_status'] == 'CONTRADICTED'
    assert a['cards'][0]['critic_raw_output']['raw_output']['verdict'] == 'SUPPORTED_CONDITIONAL'


@pytest.mark.parametrize('bad', [ValueError('LOCAL_OUTPUT_TRUNCATED'), local_inference.Deferred('LOCAL_MODEL_CALL_CAP'),
                                 {'propositions': [{'exact_quote': 'not in source'}]}])
def test_failed_or_truncated_inference_is_not_neutral_or_success(bad):
    a = r.analyze(grounded(), Scripted([bad]))
    assert a['semantic_rows'][0]['status'] == 'INFERENCE_FAILED'
    assert a['semantic_rows'][0]['reason']
    assert not a['cards'] and a['released_score'] is None


def test_rejected_raw_propositions_survive_and_do_not_enter_findings():
    raw = {'propositions': [proposal('Interest rates decreased.')]}  # absent quote
    a = r.analyze(grounded(), Scripted([raw]))
    assert a['semantic_rows'][0]['raw'] == raw
    assert a['semantic_rows'][0]['status'] == 'INFERENCE_FAILED'
    assert not a['propositions']


def test_invalid_researcher_contract_is_retained_without_crashing_finish():
    ai = grounded()
    a = r.analyze(ai, model(ai, research_raw={'target_price': 123}))
    assert a['cards'][0]['final_status'] == 'INFERENCE_FAILED'
    assert a['cards'][0]['researcher_raw_output']['raw_output'] == {'target_price': 123}


def test_counter_evidence_must_be_real_comparable_source_bound_opposition():
    ai = grounded(); p = proposition(ai)
    bad = critic('CONTRADICTED', [{'proposition_id': 'absent', 'source_id': 'invented', 'paragraph_id': 'p1', 'exact_quote': 'Fake contrary fact.'}])
    a = r.analyze(ai, model(ai, critic_raw=bad))
    card = a['cards'][0]
    assert card['final_status'] == 'UNSUPPORTED' and not card['actual_counter_evidence']
    assert card['counter_evidence_status'] == 'NOT_FOUND_OR_UNAVAILABLE'
    assert 'unbound_counter_evidence' in card['deterministic_validation']['critic_errors']


def test_general_cautions_are_not_mislabeled_actual_counter_evidence():
    a = r.analyze(grounded(), model(grounded()))
    card = a['cards'][0]
    assert card['general_cautions'] and not card['actual_counter_evidence']
    assert card['counter_evidence_status'] == 'NOT_FOUND_OR_UNAVAILABLE'


def test_existing_opposing_same_period_quote_can_be_counter_evidence():
    ai = grounded(); a = proposition(ai)
    ai2 = packet('Interest rates decreased.', ai['financial_facts']); b = proposition(ai2, variable_direction='decrease')
    b.update(source_id='second_source', proposition_id='other')
    a.update(temporal_expression='last month', source_published_date='2026-11-08')
    b.update(temporal_expression='last month', source_published_date='2026-11-08')
    ref = {k: b[k] for k in ('proposition_id', 'source_id', 'paragraph_id', 'exact_quote')}
    found, errors = r.counter_evidence(critic('CONTRADICTED', [ref]), a, [a, b])
    assert found == [ref] and not errors
    b['temporal_reference'] = 'future'
    assert r.counter_evidence(critic('CONTRADICTED', [ref]), a, [a, b])[1]


def test_publication_date_does_not_substitute_for_event_period():
    ai = packet('Interest rates decreased last year.')
    p = proposition(ai, variable_direction='decrease', temporal_expression='last year')
    assert p['temporal_reference'] == 'past' and p['temporal_expression'] == 'last year'
    assert p['source_published_date'] == '2026-11-08'
    other = deepcopy(p)
    other.update(proposition_id='opposite', source_id='different', variable_direction='increase', temporal_expression='last month')
    ref = {k: other[k] for k in ('proposition_id', 'source_id', 'paragraph_id', 'exact_quote')}
    assert not r.counter_evidence(critic('CONTRADICTED', [ref]), p, [p, other])[0]


def test_duplicate_source_proposition_across_paragraphs_is_one_event_group():
    ai = grounded()
    p = deepcopy(ai['official_documents'][0]['paragraphs'][0]); p['id'] = 'p0002'
    ai['official_documents'][0]['paragraphs'].append(p)
    q = p['text']
    active = Scripted([{'propositions': [proposal(q)]}, {'propositions': [proposal(q)]}, research(), critic()])
    a = r.analyze(ai, active)
    assert len(a['propositions']) == 2 and len(a['duplicate_events']) == 1
    assert a['coverage']['event_groups'] == 1 and len(a['cards']) == 1
    report.replay_analysis(ai, a)


def test_no_prices_metadata_gold_or_rule_verdict_leak_to_prompts():
    ai = grounded(); backend = model(ai)
    r.analyze(ai, backend)
    rendered = json.dumps(backend.messages)
    for key in ['LOCAL_INPUT', 'market_price', 'gold', 'should_allow', 'deterministic_validation', 'evidence_status', 'applicable_routes']:
        assert key not in rendered
    assert backend.messages[1][0]['content'] != backend.messages[2][0]['content']
    assert 'CLAIM' in backend.messages[2][1]['content']


@pytest.mark.parametrize('field', ['market_price', 'vendor_headline', 'price_returns'])
def test_forbidden_payload_fields_are_rejected_before_inference(field):
    ai = grounded(); ai[field] = 'ILLUSTRATIVE_FORBIDDEN'
    backend = Scripted([])
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        r.analyze(ai, backend)
    assert backend.calls == 0


def test_proposition_and_fact_copy_tampering_fails_replay():
    ai = grounded(); a = r.analyze(ai, model(ai))
    for key in ['value', 'scope']:
        changed = deepcopy(a)
        changed['exposure']['legacy_exposure']['items'][0][key] = 'tampered'
        with pytest.raises(ValueError, match='REPLAY'):
            report.replay_analysis(ai, changed)


def stub_report_environment(monkeypatch, tmp_path, ai):
    folder = tmp_path / 'input'; folder.mkdir()
    (folder / 'AI_INPUT.json').write_text(json.dumps(ai), encoding='utf-8')
    baseline = tmp_path / 'baseline.json'; baseline.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(report.scope, 'check', lambda *a, **k: {'baseline_sha256': digest(baseline)})
    monkeypatch.setattr(report.analysis_contract, 'export_ai', lambda *a, **k: deepcopy(ai))
    monkeypatch.setattr(report, 'legacy_component', lambda *a: None)
    monkeypatch.setattr(report, 'historical_check', lambda *a, **k: None)
    return folder, baseline


@pytest.mark.parametrize('mode', ['v3_shadow', 'v3_candidate'])
def test_automatic_partial_report_needs_no_manual_article_input_and_no_adoption(monkeypatch, tmp_path, mode):
    ai = grounded(); folder, baseline = stub_report_environment(monkeypatch, tmp_path, ai)
    v, snapshot = report.build(folder, baseline, mode=mode, clock=lambda: CUTOFF, inference_reason='LOCAL_MODEL_WAITING_FOR_MEMORY')
    saved = report.write(v, snapshot, tmp_path / 'out')
    assert report.validate(saved) == v
    assert v['model_execution'] == 'NOT_RUN' and not v['official_ai_adopted']
    assert not v['candidate_registration']['eligible']
    assert all(v[k] is None for k in ['released_score', 'released_target', 'released_price_plan'])
    rendered = (saved / 'REPORT.ko.md').read_text(encoding='utf-8')
    assert all(label in rendered for label in ['검증된 재무 사실', '실제 반대 증거', '후속 관찰 지표', '보류 사유'])
    with pytest.raises(FileExistsError): report.write(v, snapshot, tmp_path / 'out')


def test_record_file_and_semantic_replay_are_checked_independently(monkeypatch, tmp_path):
    ai = grounded(); source, baseline = stub_report_environment(monkeypatch, tmp_path, ai)
    v, snapshot = report.build(source, baseline, backend=model(ai), clock=lambda: CUTOFF)
    folder = report.write(v, snapshot, tmp_path / 'out')
    report.validate(folder)
    changed = deepcopy(v); changed['analysis']['cards'][0]['final_status'] = 'UNSUPPORTED'
    changed['record_sha256'] = stable_hash({k: val for k, val in changed.items() if k != 'record_sha256'})
    (folder / 'RESEARCH_V3.json').write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(ValueError, match='REPLAY'):
        report.validate(folder)


def test_existing_modes_and_model_settings_remain_unchanged():
    p = local_inference.policy()
    assert p['temperature'] == 0.7 and p['seed'] == 1729 and not p['thinking_enabled']
    assert contract.runtime_settings('B')['temperature'] == 0.1
    assert local_inference.policy() == p
    assert all(contract.runtime_settings(k)['minimum_available_memory_bytes'] == 4000000000 for k in 'ABCDE')
