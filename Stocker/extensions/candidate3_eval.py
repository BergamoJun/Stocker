"""One-shot candidate-3 authored holdout; never independent validation."""
import time
from pathlib import Path

from research.sec_transport import now
from research.validation import stable_hash, digest
from research.local_files import LockBusy
from . import candidate3, gpu_eval, gpu_inference as gpu, local_inference as cpu
from . import research_v3_eval as old, research_v3_contract as c


def methods():
    return {**candidate3.methods(), **gpu_eval.methods(),
            'extensions/candidate3_eval.py': digest(c.BASE / 'candidate3_eval.py')}


def register(inputs, answers, path):
    if not inputs or len(inputs) != len(answers) or len({r['id'] for r in inputs}) != len(inputs) or {r['id'] for r in inputs} != {r['id'] for r in answers}:
        raise ValueError('C3_CASE_SET_INVALID')
    for row in inputs:
        if row.get('partition') == 'locked_final':
            raise ValueError('C3_LOCKED_FINAL_FORBIDDEN')
        c.validate_ai(row['ai'], row['ai']['as_of'])
    value = {'contract': 'candidate-3-registration-v1', 'candidate': candidate3.VERSION,
             'registered_at': now(), 'inputs_sha256': stable_hash(inputs), 'gold_sha256': stable_hash(answers),
             'case_order': [r['id'] for r in inputs], 'methods': methods(), 'thresholds': c.policy()['quality'],
             'runtime_settings': gpu.settings('B'), 'independent_gold': False,
             'protocol': 'one frozen run; developer authored holdout; no output-informed retuning; raw scorer unchanged'}
    value['record_sha256'] = stable_hash(value)
    old.write_once(path, value)
    return value


def check(inputs, gold, registration):
    expected = {'candidate': candidate3.VERSION, 'inputs_sha256': stable_hash(inputs), 'gold_sha256': stable_hash(gold),
                'case_order': [r['id'] for r in inputs], 'methods': methods(),
                'thresholds': c.policy()['quality'], 'runtime_settings': gpu.settings('B')}
    if any(registration.get(k) != v for k, v in expected.items()) or registration['record_sha256'] != stable_hash({k: v for k, v in registration.items() if k != 'record_sha256'}):
        raise ValueError('C3_PREREGISTRATION_CHANGED')


def run(inputs, gold, registration, output):
    check(inputs, gold, registration)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    rows, sessions, reason = [], [], None
    start = time.monotonic()
    pending = list(inputs)
    while pending:
        batch, budget = [], 0
        for row in pending:
            needed = gpu_eval.reserve(row, 'research_v3')
            if needed > gpu.settings('B')['maximum_calls_per_server']:
                raise ValueError('C3_CASE_BUDGET_EXCEEDED')
            if budget + needed > gpu.settings('B')['maximum_calls_per_server']:
                break
            batch.append(row); budget += needed
        server = gpu.Server(configuration='B')
        try:
            with server:
                for case in batch:
                    analysis = candidate3.analyze(case['ai'], server)
                    row = {'id': case['id'], 'status': 'COMPLETED', 'analysis': analysis}
                    rows.append(row); old.write_once(output / (case['id'] + '.json'), row)
            pending = pending[len(batch):]
        except (cpu.Deferred, LockBusy, OSError) as e:
            reason = str(e) if isinstance(e, cpu.Deferred) else 'C3_RESOURCE_UNAVAILABLE'
            break
        finally:
            folder = getattr(server, 'folder', None)
            sessions.append({'folder': str(folder) if folder else None, 'requests': server.calls,
                             'successful_completions': server.successful_completions,
                             'prompt_tokens': server.prompt_tokens, 'completion_tokens': server.completion_tokens,
                             'telemetry': server.telemetry,
                             'files': {p.relative_to(folder).as_posix(): digest(p) for p in folder.rglob('*') if p.is_file()} if folder else {}})
    seen = {r['id'] for r in rows}
    for case in inputs:
        if case['id'] not in seen:
            row = {'id': case['id'], 'analysis': None, 'status': 'NOT_RUN', 'reason': reason}
            rows.append(row); old.write_once(output / (case['id'] + '.json'), row)
    metrics = old.summarize(inputs, gold, rows)
    attempts = sum(s['requests'] for s in sessions)
    value = {'contract': 'candidate-3-evaluation-v1', 'candidate': candidate3.VERSION,
             'evaluated_at': now(), 'registration_sha256': stable_hash(registration), 'methods': methods(),
             'rows': rows, 'metrics': metrics, 'execution_status': metrics['decision'] if attempts else 'NOT_RUN',
             'backend': 'local_qwen_cuda' if attempts else 'NOT_RUN', 'reason': reason,
             'telemetry': {'sessions': sessions, 'request_attempts': attempts,
                           'successful_completions': sum(s['successful_completions'] for s in sessions),
                           'elapsed_seconds': time.monotonic() - start},
             'independently_validated': False, 'official_ai_adopted': False,
             'released_score': None, 'released_target': None, 'released_price_plan': None}
    value['record_sha256'] = stable_hash(value)
    old.write_once(output / 'RESULT.json', value)
    return value


def validate(value, inputs, gold, registration):
    check(inputs, gold, registration)
    if (value['methods'] != methods() or value['registration_sha256'] != stable_hash(registration)
            or value['metrics'] != old.summarize(inputs, gold, value['rows'])
            or value['record_sha256'] != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'})):
        raise ValueError('C3_RECEIPT_CHANGED')
    from .research_v3_report import replay_analysis
    by_id = {r['id']: r for r in inputs}
    for row in value['rows']:
        if row['analysis'] is not None:
            replay_analysis(by_id[row['id']]['ai'], row['analysis'])
    for session in value['telemetry']['sessions']:
        if session['folder']:
            folder = Path(session['folder']).resolve()
            if not folder.is_relative_to((gpu.ROOT / 'reports/private/gpu_inference_sessions').resolve()) or 'session.key' in session['files'] or any(digest(folder / n) != sha for n, sha in session['files'].items()):
                raise ValueError('C3_SESSION_CHANGED')
            if session['requests']:
                before = session['telemetry']['preflight']
                gpu.preflight(gpu.policy(), before['system_available_bytes'], before['gpu'])
                gpu.offload_proof((folder / 'runtime.log').read_text(encoding='utf-8', errors='replace'))
    if value['backend'] != ('local_qwen_cuda' if value['telemetry']['request_attempts'] else 'NOT_RUN') or value['official_ai_adopted'] or value['independently_validated'] or any(value[k] is not None for k in ('released_score', 'released_target', 'released_price_plan')):
        raise ValueError('C3_ADOPTION_BOUNDARY_CHANGED')
    return value['execution_status']
