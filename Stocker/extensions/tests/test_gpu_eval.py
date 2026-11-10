"""ILLUSTRATIVE: GPU execution never weakens model-quality or adoption gates."""
from copy import deepcopy
import pytest

from extensions import gpu_eval as evaluation, gpu_inference as gpu, local_inference
from extensions.tests.test_research_v3_eval import dataset
from extensions.tests.test_research_v3 import model
from extensions import research_v3, research_v3_eval


def test_gpu_registration_binds_unchanged_gold_backend_and_method(tmp_path):
    inputs, answers = dataset()
    r = evaluation.register('research_v3', inputs, answers, tmp_path / 'registration.json')
    evaluation.check(r, inputs, answers)
    assert r['backend'] == 'local_qwen_cuda'
    with pytest.raises(FileExistsError):
        evaluation.register('research_v3', inputs, answers, tmp_path / 'registration.json')
    changed = deepcopy(answers); changed[0]['propositions'][0]['should_allow'] = False
    with pytest.raises(ValueError, match='PREREGISTRATION'):
        evaluation.check(r, inputs, changed)


def test_raw_false_approval_remains_failure_with_gpu_backend():
    inputs, answers = dataset()
    rows = [{'id': c['id'], 'analysis': research_v3.analyze(c['ai'], model(c['ai']))} for c in inputs]
    m = evaluation.metrics('research_v3', inputs, answers, rows)
    assert m['raw_false_approvals'] == 1 and m['rule_blocks'] == 1 and m['decision'] == 'quality_gate_failed'


def test_v3_batches_respect_24_calls_without_changing_quality():
    inputs, _ = dataset()
    assert evaluation.reserve(inputs[0], 'research_v3') == 3
    assert evaluation.reserve(inputs[0], 'critic_v2') == 1
    assert gpu.settings()['maximum_calls_per_server'] == 24


def test_legacy_critic_keeps_original_320_token_limit_and_sampling():
    s = gpu.settings('A', 'critic_v2'); original = local_inference.policy()
    assert s['maximum_output_tokens'] == original['maximum_output_tokens'] == 320
    assert (s['temperature'], s['seed'], s['thinking_enabled']) == (0.7, 1729, False)
    with pytest.raises(ValueError): gpu.settings('B', 'critic_v2')


def test_no_gpu_start_is_explicit_not_run_and_no_success(tmp_path):
    inputs, answers = dataset()
    r = evaluation.register('research_v3', inputs, answers, tmp_path / 'registration.json')
    class Unavailable:
        calls = successful_completions = prompt_tokens = completion_tokens = 0
        telemetry = {'inference': 'ILLUSTRATIVE_MOCK_NOT_REAL_GPU'}
        def __enter__(self): raise local_inference.Deferred('GPU_VRAM_INSUFFICIENT')
        def __exit__(self, *args): return False
    value = evaluation.run(inputs, answers, r, tmp_path / 'run', factory=lambda **kw: Unavailable())
    assert value['execution_status'] == 'NOT_RUN'
    assert value['telemetry']['request_attempts'] == 0
    assert not value['official_ai_adopted']
    assert evaluation.validate(value, inputs, answers, r) == 'NOT_RUN'


def test_numeric_price_and_score_boundaries_stay_outside_cuda_prompts():
    inputs, _ = dataset(); active = model(inputs[0]['ai'])
    research_v3.analyze(inputs[0]['ai'], active)
    assert not any(x in str(active.messages) for x in ['market_price', 'should_allow', 'deterministic_validation'])
