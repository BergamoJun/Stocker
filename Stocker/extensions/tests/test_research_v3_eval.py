"""ILLUSTRATIVE — quality metrics retain omissions, raw errors and blinding."""
from copy import deepcopy
import pytest

from extensions import research_v3_eval as e, research_v3 as engine
from extensions.tests.test_exposure_v3 import grounded, packet
from extensions.tests.test_semantic_v3 import proposal
from extensions.tests.test_research_v3 import model, critic


def dataset():
    inputs = [{'id': 'good', 'ai': grounded()}, {'id': 'bad', 'ai': packet()}]
    answers = []
    for case in inputs:
        p = proposal('Interest rates increased.')
        answers.append({'id': case['id'], 'split': 'golden_authored', 'propositions': [{**p,
            'should_allow': case['id'] == 'good', 'critic_verdicts': ['SUPPORTED_CONDITIONAL'] if case['id'] == 'good' else ['INSUFFICIENT_EVIDENCE'],
            'research_routes': ['financing_cost', 'cash_yield'], 'answerable': True}]})
    return inputs, answers


def test_rule_blocks_cannot_fix_raw_critic_false_approval():
    inputs, answers = dataset()
    rows = [{'id': c['id'], 'analysis': engine.analyze(c['ai'], model(c['ai']))} for c in inputs]
    summary = e.summarize(inputs, answers, rows)
    assert summary['raw_false_approvals'] == 1 and summary['rule_blocks'] == 1
    assert summary['decision'] == 'quality_gate_failed'


def test_all_unknown_or_omitted_model_answers_cannot_pass():
    inputs, answers = dataset()
    rows = [{'id': c['id'], 'analysis': engine.analyze(c['ai'])} for c in inputs]
    summary = e.summarize(inputs, answers, rows)
    assert summary['decision'] == 'quality_gate_failed'
    s = summary['splits']['golden_authored']
    assert s['unnecessary_abstention_rate'] == '1'
    assert s['contract_acceptance'] == '0'


def test_not_run_is_distinct_from_zero_errors_and_quality_pass():
    inputs, answers = dataset()
    rows = [{'id': c['id'], 'analysis': None, 'status': 'NOT_RUN'} for c in inputs]
    summary = e.summarize(inputs, answers, rows)
    assert summary['decision'] == 'NOT_RUN' and summary['raw_false_approvals'] == 0


def test_duplicate_and_missing_cases_cannot_change_denominators():
    inputs, answers = dataset()
    with pytest.raises(ValueError, match='INCOMPLETE'):
        e.summarize(inputs, answers, [{'id': 'good', 'analysis': None}] * 2)


def test_preregistration_is_immutable_and_binds_code_inputs_gold_and_config(tmp_path):
    inputs, answers = dataset(); path = tmp_path / 'registration.json'
    registration = e.register(inputs, answers, path)
    e.check_registration(inputs, answers, registration)
    with pytest.raises(FileExistsError): e.register(inputs, answers, path)
    changed = deepcopy(answers); changed[0]['propositions'][0]['should_allow'] = False
    with pytest.raises(ValueError, match='PREREGISTRATION'):
        e.check_registration(inputs, changed, registration)
    changed_inputs = deepcopy(inputs); changed_inputs[0]['ai']['identity']['symbol'] = 'CHANGED'
    with pytest.raises(ValueError, match='PREREGISTRATION'):
        e.check_registration(changed_inputs, answers, registration)


def test_raw_accuracy_and_guard_block_are_separate_dimensions():
    inputs, answers = dataset()
    rows = [{'id': c['id'], 'analysis': engine.analyze(c['ai'], model(c['ai'], critic_raw=critic('SUPPORTED_CONDITIONAL' if c['id'] == 'good' else 'INSUFFICIENT_EVIDENCE')))} for c in inputs]
    metrics = e.summarize(inputs, answers, rows)
    assert metrics['decision'] == 'quality_gate_passed'
    assert metrics['splits']['golden_authored']['raw_semantic_accuracy'] == '1'
    assert metrics['raw_false_approvals'] == 0


def test_actual_resource_deferral_is_preregistered_not_mock_success(tmp_path, monkeypatch):
    inputs, answers = dataset()
    registration = e.register(inputs, answers, tmp_path / 'registration.json')
    monkeypatch.setattr(e.local_inference.Server, '__enter__', lambda self: (_ for _ in ()).throw(e.local_inference.Deferred('LOCAL_MODEL_WAITING_FOR_MEMORY')))
    result = e.run(inputs, answers, registration, tmp_path / 'run')
    assert result['metrics']['decision'] == 'NOT_RUN'
    assert result['telemetry']['local_model_calls'] == 0
    assert not e.validate(result, inputs, answers, registration)


def test_gold_is_never_passed_to_any_model_stage():
    inputs, answers = dataset(); active = model(inputs[0]['ai'])
    engine.analyze(inputs[0]['ai'], active)
    assert not any(key in str(active.messages) for key in ['should_allow', 'critic_verdicts', 'golden_authored', 'answers_sha256'])
