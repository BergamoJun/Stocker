"""Backend identity v1. Frozen candidate-2 builders remain replayable.

Class inheritance is never evidence of a CPU run. Actual execution additionally
requires a verified owned launcher, pinned artifacts and (for CUDA) log proof.
"""
from pathlib import Path
from urllib.parse import urlparse
from copy import deepcopy
import hashlib

from research.validation import digest, stable_hash
from . import local_inference as cpu, gpu_inference as gpu

VERSION = 'local-backend-identity-v1'
KINDS = {'local_qwen_cpu', 'local_qwen_cuda', 'MOCK_NOT_ACTUAL_MODEL', 'NOT_RUN'}


def seal(value):
    snapshot = deepcopy(value)
    return {**snapshot, 'record_sha256': stable_hash(snapshot)}


def capture(backend, *, calls_before=0):
    kind = ('NOT_RUN' if backend is None else 'local_qwen_cuda' if type(backend) is gpu.Server
            else 'local_qwen_cpu' if type(backend) is cpu.Server else 'MOCK_NOT_ACTUAL_MODEL')
    attempts = max(0, getattr(backend, 'calls', 0) - calls_before)
    value = {'contract': VERSION, 'backend': kind, 'request_attempts': attempts,
             'execution_status': 'NOT_RUN', 'hardware_evidence': None,
             'model': None, 'runtime': None, 'reason': None}
    if kind in {'NOT_RUN', 'MOCK_NOT_ACTUAL_MODEL'}:
        value['execution_status'] = 'MOCK_NOT_ACTUAL_MODEL' if backend is not None else 'NOT_RUN'
        return seal(value)
    if not attempts:
        value['reason'] = 'NO_MODEL_REQUEST_ATTEMPTED'
        return seal(value)
    # A constructed Server, a spoofed subclass or a self-declared string cannot
    # become proof. These fields are produced only by the owned launcher.
    process = getattr(backend, 'process', None)
    args = getattr(process, 'args', [])
    def arg(name):
        try:
            return args[args.index(name) + 1]
        except (ValueError, IndexError):
            raise ValueError('BACKEND_LAUNCH_EVIDENCE_MISSING') from None
    endpoint = urlparse(getattr(backend, 'base', ''))
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or arg('--host') != '127.0.0.1' or not process.pid:
        raise ValueError('BACKEND_LOOPBACK_EVIDENCE_MISSING')
    folder = Path(backend.folder).resolve()
    # CPU custom roots are permitted only below private reports, never outside.
    if not folder.is_relative_to((gpu.ROOT / 'reports/private').resolve()):
        raise ValueError('BACKEND_SESSION_NOT_PRIVATE')
    if Path(arg('--api-key-file')).resolve() != (folder / 'session.key'):
        raise ValueError('BACKEND_AUTH_EVIDENCE_MISSING')
    provision, executable = (gpu.artifacts(backend.settings) if kind == 'local_qwen_cuda'
                             else cpu.verified_artifacts(backend.settings))
    if Path(args[0]).resolve() != Path(executable).resolve() or Path(arg('-m')).resolve() != Path(provision['model_path']).resolve():
        raise ValueError('BACKEND_ARTIFACT_IDENTITY_MISMATCH')
    log = folder / 'runtime.log'
    if not log.is_file():
        raise ValueError('BACKEND_LOG_MISSING')
    log_bytes = log.read_bytes()
    log_sha = hashlib.sha256(log_bytes).hexdigest()
    snapshot = folder / ('identity-' + log_sha + '.log')
    if not snapshot.exists():
        with snapshot.open('xb') as stream:
            stream.write(log_bytes)
    elif digest(snapshot) != log_sha:
        raise ValueError('BACKEND_LOG_SNAPSHOT_CHANGED')
    evidence = {'session_folder': str(folder), 'runtime_log_snapshot': snapshot.name, 'runtime_log_sha256': log_sha,
                'owned_pid': process.pid, 'loopback_authenticated_launcher': True,
                'shared_lock': 'local_data/local_inference/runtime.lock',
                'raw_responses_saved': kind == 'local_qwen_cuda',
                'model_response_observed': bool(getattr(backend, 'prompt_tokens', 0) + getattr(backend, 'completion_tokens', 0)
                                                or list((folder / 'responses').glob('*.json'))),
                'host_ram_available_at_capture_bytes': cpu.available_memory(),
                'capture_ram_is_startup_measurement': False}
    if kind == 'local_qwen_cuda':
        p = gpu.policy()
        before = backend.telemetry.get('preflight')
        if not before:
            raise ValueError('BACKEND_CUDA_PREFLIGHT_MISSING')
        gpu.preflight(p, before['system_available_bytes'], before['gpu'])
        if arg('--n-gpu-layers') != '99' or arg('--fit') != 'off':
            raise ValueError('BACKEND_CUDA_LAUNCH_MISMATCH')
        evidence.update(preflight=before, offload=gpu.offload_proof(log_bytes.decode('utf-8', errors='replace')),
                        memory=backend.telemetry, cpu_fallback=False)
    elif arg('--n-gpu-layers') != '0':
        raise ValueError('BACKEND_CPU_LAUNCH_MISMATCH')
    value.update(execution_status='ACTUAL_MODEL_EXECUTED' if evidence['model_response_observed'] else 'ATTEMPTED', hardware_evidence=evidence,
                 model={k: backend.settings[k] for k in ('model_id', 'model_revision', 'model_sha256')},
                 runtime={'executable': str(executable), 'sha256': digest(executable), 'tag': backend.settings['runtime_tag']})
    return seal(value)


def validate(value):
    if (value.get('contract') != VERSION or value.get('backend') not in KINDS
            or value.get('record_sha256') != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'})):
        raise ValueError('BACKEND_IDENTITY_CHANGED')
    if value['execution_status'] == 'ACTUAL_MODEL_EXECUTED':
        if value['backend'] not in {'local_qwen_cpu', 'local_qwen_cuda'} or not value['request_attempts'] or not value['hardware_evidence'] or not value['model'] or not value['runtime']:
            raise ValueError('BACKEND_ACTUAL_EXECUTION_UNPROVEN')
        if not value['hardware_evidence'].get('model_response_observed'):
            raise ValueError('BACKEND_MODEL_RESPONSE_UNPROVEN')
        evidence = value['hardware_evidence']
        if evidence.get('session_folder'):
            root = Path(evidence['session_folder']).resolve()
            path = (root / evidence['runtime_log_snapshot']).resolve()
            if not root.is_relative_to((gpu.ROOT / 'reports/private').resolve()) or path.parent != root or digest(path) != evidence['runtime_log_sha256']:
                raise ValueError('BACKEND_LOG_SNAPSHOT_CHANGED')
            if value['backend'] == 'local_qwen_cuda' and gpu.offload_proof(path.read_text(encoding='utf-8', errors='replace')) != evidence['offload']:
                raise ValueError('BACKEND_CUDA_PROOF_CHANGED')
        if value['backend'] == 'local_qwen_cuda':
            evidence = value['hardware_evidence']
            before = evidence['preflight']
            gpu.preflight(gpu.policy(), before['system_available_bytes'], before['gpu'])
            proof = evidence['offload']
            if not proof['cuda_detected'] or not proof['total_layers'] or proof['offloaded_layers'] != proof['total_layers'] or proof['gpu_kv_buffer_bytes'] <= 0:
                raise ValueError('BACKEND_CUDA_PROOF_MISSING')
    elif value['execution_status'] not in {'NOT_RUN', 'MOCK_NOT_ACTUAL_MODEL', 'ATTEMPTED'}:
        raise ValueError('BACKEND_EXECUTION_STATUS_INVALID')
    return value


def evaluation_identity(value, inputs, answers, registration):
    """Attest a completed frozen CUDA receipt, including original HTTP/log bytes.

    The old receipt is preserved. A mock factory without owned session evidence
    receives MOCK, even if its frozen receipt claims the CUDA backend.
    """
    from . import gpu_eval
    gpu_eval.validate(value, inputs, answers, registration)
    sessions = value['telemetry']['sessions']
    attempts = value['telemetry']['request_attempts']
    kind = 'NOT_RUN' if not attempts else 'local_qwen_cuda'
    if attempts and any(not s.get('folder') for s in sessions if s['requests']):
        kind = 'MOCK_NOT_ACTUAL_MODEL'
    evidence = []
    if kind == 'local_qwen_cuda':
        for session in sessions:
            if not session['requests']:
                continue
            root = Path(session['folder'])
            log = root / 'runtime.log'
            before = session['telemetry'].get('preflight')
            if not before or 'runtime.log' not in session['files']:
                raise ValueError('EVALUATION_LAUNCH_EVIDENCE_MISSING')
            gpu.preflight(gpu.policy(), before['system_available_bytes'], before['gpu'])
            proof = gpu.offload_proof(log.read_text(encoding='utf-8', errors='replace'))
            responses = [n for n in session['files'] if n.startswith('responses/')]
            if len(responses) < session['successful_completions']:
                raise ValueError('EVALUATION_RAW_RESPONSES_MISSING')
            evidence.append({'preflight': before, 'offload': proof, 'folder': str(root), 'files': session['files']})
    hardware = ({'sessions': evidence, 'preflight': evidence[0]['preflight'], 'offload': evidence[0]['offload'],
                 'model_response_observed': any(any(n.startswith('responses/') for n in s['files']) for s in evidence)} if evidence else None)
    result = seal({'contract': VERSION, 'backend': kind,
                 'execution_status': ('ACTUAL_MODEL_EXECUTED' if hardware and hardware['model_response_observed'] else 'ATTEMPTED') if kind == 'local_qwen_cuda' else kind,
                 'request_attempts': attempts, 'hardware_evidence': hardware,
                 'frozen_receipt_sha256': value['record_sha256'],
                 'registration_sha256': stable_hash(registration),
                 'model': {k: registration['runtime_settings'][value['configuration']][k] for k in ('model_id', 'model_revision', 'model_sha256')},
                 'runtime': {'tag': registration['runtime_settings'][value['configuration']]['runtime_tag']},
                 'quality_status': value['execution_status'], 'quality_is_independent_validation': False})
    validate(result)
    return result
