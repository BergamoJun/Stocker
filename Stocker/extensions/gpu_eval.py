"""GPU-specific preregistration and evaluation; frozen CPU receipts stay intact."""
import argparse
import json
from pathlib import Path
import re
import time
import uuid
import jsonschema

from research.local_files import LockBusy
from research.sec_transport import now
from research.validation import stable_hash, digest
from . import gpu_inference as gpu, local_inference as cpu
from . import research_v3 as v3, research_v3_eval as evaluation, critic_v2 as critic, critic_v2_eval


def methods():
    return {**gpu.methods(), **critic.methods()}


def register(kind, inputs, answers, path, *, clock=now):
    if kind not in {'research_v3', 'critic_v2'}:
        raise ValueError('GPU_EVALUATION_KIND_INVALID')
    if len(inputs) != len(answers) or len({c['id'] for c in inputs}) != len(inputs) or {c['id'] for c in inputs} != {a['id'] for a in answers}:
        raise ValueError('GPU_CASE_SET_INVALID')
    for row in inputs:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['id']):
            raise ValueError('GPU_CASE_ID_INVALID')
        if kind == 'research_v3':
            v3.contract.validate_ai(row['ai'], row['ai']['as_of'])
        else:
            critic.validate_input(row['context'], row['claim'])
    value = {'contract': 'gpu-evaluation-registration-v1', 'registered_at': clock(), 'kind': kind,
             'input_ids': [r['id'] for r in inputs], 'inputs_sha256': stable_hash(inputs), 'answers_sha256': stable_hash(answers),
             'methods': methods(), 'runtime_settings': {c: gpu.settings(c, kind) for c in ('ABCDE' if kind == 'research_v3' else 'A')},
             'quality_thresholds': v3.contract.policy()['quality'] if kind == 'research_v3' else critic.policy()['quality'],
             'backend': 'local_qwen_cuda', 'independent_real_event_validation': False,
             'blinding': 'unchanged authored gold excluded from all prompts; GPU backend is a separate experiment'}
    value['record_sha256'] = stable_hash(value)
    evaluation.write_once(path, value)
    return value


def check(registration, inputs, answers):
    if (registration['methods'] != methods() or registration['inputs_sha256'] != stable_hash(inputs)
            or registration['answers_sha256'] != stable_hash(answers)
            or registration['input_ids'] != [c['id'] for c in inputs]
            or registration['runtime_settings'] != {c: gpu.settings(c, registration['kind']) for c in ('ABCDE' if registration['kind'] == 'research_v3' else 'A')}
            or registration['record_sha256'] != stable_hash({k: v for k, v in registration.items() if k != 'record_sha256'})):
        raise ValueError('GPU_PREREGISTRATION_CHANGED')
    expected = v3.contract.policy()['quality'] if registration['kind'] == 'research_v3' else critic.policy()['quality']
    if registration['quality_thresholds'] != expected:
        raise ValueError('GPU_THRESHOLDS_CHANGED')


def reserve(case, kind):
    if kind == 'critic_v2':
        return 1
    from .semantic_v3 import spans
    ps = v3.payloads(case['ai'])[:v3.contract.policy()['maximum_paragraphs']]
    return len(ps) + 2 * min(v3.contract.policy()['maximum_cards'], sum(len(spans(p['text'])) for p in ps))


def metrics(kind, inputs, answers, rows):
    return evaluation.summarize(inputs, answers, rows) if kind == 'research_v3' else critic_v2_eval.summarize(inputs, answers, rows)


def run(inputs, answers, registration, output, *, configuration='A', factory=gpu.Server, clock=now):
    check(registration, inputs, answers)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic(); kind = registration['kind']
    rows, sessions, reason = [], [], None
    pending = list(inputs)
    while pending:
        batch, count = [], 0
        for case in pending:
            needed = reserve(case, kind)
            if needed > gpu.settings(configuration, kind)['maximum_calls_per_server']:
                raise ValueError('GPU_SINGLE_CASE_CALL_BUDGET_EXCEEDED')
            if batch and count + needed > gpu.settings(configuration, kind)['maximum_calls_per_server']:
                break
            batch.append(case); count += needed
        server = None
        try:
            server = factory(configuration=configuration, task=kind)
            with server:
                for case in batch:
                    if kind == 'research_v3':
                        try:
                            row = {'id': case['id'], 'status': 'COMPLETED', 'analysis': v3.analyze(case['ai'], server, configuration=configuration)}
                        except (ValueError, KeyError, jsonschema.ValidationError):
                            row = {'id': case['id'], 'status': 'INPUT_OR_MODEL_FAILED', 'analysis': None}
                    else:
                        row = critic_v2_eval.run_case(case, server)
                    rows.append(row)
                    evaluation.write_once(output / (case['id'] + '.json'), row)
                    print(json.dumps({'kind': kind, 'configuration': configuration, 'completed_case': case['id'],
                                      'request_attempts_in_session': server.calls}), flush=True)
            pending = pending[len(batch):]
        except (cpu.Deferred, OSError, LockBusy) as error:
            reason = str(error) if isinstance(error, cpu.Deferred) else 'GPU_RESOURCE_LOCK_OR_RUNTIME_UNAVAILABLE'
            break
        finally:
            if server is not None:
                sessions.append({'folder': str(server.folder) if hasattr(server, 'folder') else None,
                    'requests': server.calls, 'successful_completions': server.successful_completions,
                    'prompt_tokens': server.prompt_tokens, 'completion_tokens': server.completion_tokens,
                    'telemetry': server.telemetry,
                    'files': {p.relative_to(server.folder).as_posix(): digest(p) for p in server.folder.rglob('*') if p.is_file()}
                             if hasattr(server, 'folder') else {}})
    seen = {r['id'] for r in rows}
    for case in inputs:
        if case['id'] not in seen:
            row = {'id': case['id'], 'status': 'NOT_RUN', 'reason': reason,
                   **({'analysis': None} if kind == 'research_v3' else {'result': None})}
            rows.append(row); evaluation.write_once(output / (case['id'] + '.json'), row)
    result_metrics = metrics(kind, inputs, answers, rows)
    attempts = sum(s['requests'] for s in sessions)
    completions = sum(s['successful_completions'] for s in sessions)
    status = 'NOT_RUN' if attempts == 0 else result_metrics['decision']
    value = {'contract': 'local-gpu-evaluation-v1', 'evaluated_at': clock(), 'kind': kind, 'configuration': configuration,
             'registration_sha256': stable_hash(registration), 'methods': methods(), 'rows': rows,
             'metrics': result_metrics, 'execution_status': status, 'reason': reason,
             'telemetry': {'backend': 'local_qwen_cuda', 'request_attempts': attempts,
                'successful_completions': completions, 'elapsed_seconds': round(time.monotonic() - started, 6), 'sessions': sessions},
             'external_inference_calls': 0, 'training': False, 'released_score': None,
             'released_target': None, 'released_price_plan': None, 'official_ai_adopted': False}
    value['record_sha256'] = stable_hash(value)
    evaluation.write_once(output / 'RESULT.json', value)
    return value


def validate(value, inputs, answers, registration):
    check(registration, inputs, answers)
    if value['methods'] != methods() or value['registration_sha256'] != stable_hash(registration) or value['metrics'] != metrics(value['kind'], inputs, answers, value['rows']) or value['record_sha256'] != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'}):
        raise ValueError('GPU_EVALUATION_CHANGED')
    for session in value['telemetry']['sessions']:
        if session['folder'] is None:
            continue
        root = Path(session['folder']).resolve()
        if not root.is_relative_to((gpu.ROOT / 'reports/private/gpu_inference_sessions').resolve()):
            raise ValueError('GPU_SESSION_PATH_INVALID')
        if 'session.key' in session['files'] or any(digest(root / n) != sha for n, sha in session['files'].items()):
            raise ValueError('GPU_SESSION_RECORD_CHANGED')
        if session['successful_completions']:
            proof = session['telemetry'].get('offload', {})
            if not proof.get('cuda_detected') or proof.get('offloaded_layers') != proof.get('total_layers') or not proof.get('gpu_kv_buffer_bytes'):
                raise ValueError('GPU_EXECUTION_PROOF_MISSING')
    if value['kind'] == 'research_v3':
        from .research_v3_report import replay_analysis
        lookup = {c['id']: c['ai'] for c in inputs}
        for row in value['rows']:
            if row.get('analysis') is not None:
                replay_analysis(lookup[row['id']], row['analysis'])
    if any(value[k] is not None for k in ('released_score', 'released_target', 'released_price_plan')) or value['official_ai_adopted'] or value['external_inference_calls'] or value['training']:
        raise ValueError('GPU_ADOPTION_BOUNDARY_CHANGED')
    return value['execution_status']
