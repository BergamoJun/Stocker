"""Pinned CUDA offload with separate host/VRAM gates; CPU policy is untouched."""
import csv
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
import uuid

from research.local_files import exclusive_lock
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest
from . import local_inference as cpu, research_v3_contract as contract

BASE = Path(__file__).resolve().parent
FILES = ['gpu_inference.py', 'gpu_inference_policy.json', 'gpu_eval.py', 'GPU_INFERENCE.md']


def methods():
    return {**contract.methods(), **{'extensions/' + n: digest(BASE / n) for n in FILES}}


def policy():
    p = cpu.read(BASE / 'gpu_inference_policy.json')
    if (p['id'] != 'local-cuda-offload-v1' or p['monthly_cost_usd'] != 0 or
        any(p[k] for k in ('external_inference', 'training', 'score_release', 'cpu_fallback')) or
        p['gpu_layers'] != 99 or p['minimum_system_ram_bytes'] != 2_000_000_000 or
        p['host_prompt_cache_mib'] != 0 or
        p['minimum_free_vram_bytes'] != 5_000_000_000):
        raise ValueError('GPU_POLICY_BOUNDARY_CHANGED')
    return p


def settings(configuration='A', task='research_v3'):
    p = policy()
    if task not in {'research_v3', 'critic_v2'} or task == 'critic_v2' and configuration != 'A':
        raise ValueError('GPU_TASK_CONFIGURATION_INVALID')
    result = contract.runtime_settings(configuration)
    result.update(id=p['id'] + '-' + configuration,
                  minimum_available_memory_bytes=p['minimum_system_ram_bytes'], host_prompt_cache_mib=0)
    if task == 'critic_v2':
        result['maximum_output_tokens'] = cpu.policy()['maximum_output_tokens']
    return result


def clean_environment():
    value = cpu.clean_environment()
    # NVML's Windows driver discovery requires this public system directory.
    # Keep the original CPU environment and all API/provider secrets unchanged.
    if 'ProgramFiles' in os.environ:
        value['ProgramFiles'] = os.environ['ProgramFiles']
    return value


def gpu_status(index=0):
    if type(index) is not int or index != 0:
        raise cpu.Deferred('GPU_DEVICE_NOT_REGISTERED')
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvidia-smi.exe'
    r = subprocess.run([str(executable), '--id=' + str(index),
        '--query-gpu=index,uuid,name,driver_version,memory.total,memory.used,memory.free',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=8,
        env=clean_environment(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if r.returncode != 0:
        raise cpu.Deferred('GPU_QUERY_UNAVAILABLE')
    rows = list(csv.reader(r.stdout.strip().splitlines(), skipinitialspace=True))
    if len(rows) != 1 or len(rows[0]) != 7:
        raise cpu.Deferred('GPU_QUERY_INVALID')
    i, ident, name, driver, total, used, free = [s.strip() for s in rows[0]]
    return {'index': int(i), 'uuid': ident, 'name': name, 'driver': driver,
            **{k: int(v) * 1048576 for k, v in [('total_bytes', total), ('used_bytes', used), ('free_bytes', free)]}}


def preflight(p, system_bytes, gpu):
    if system_bytes < p['minimum_system_ram_bytes']:
        raise cpu.Deferred('GPU_HOST_RAM_INSUFFICIENT')
    if gpu['uuid'] != p['gpu_uuid'] or gpu['index'] != p['gpu_index']:
        raise cpu.Deferred('GPU_IDENTITY_CHANGED')
    if gpu['free_bytes'] < p['minimum_free_vram_bytes']:
        raise cpu.Deferred('GPU_VRAM_INSUFFICIENT')
    return {'system_available_bytes': system_bytes, 'gpu': gpu,
            'minimum_system_ram_bytes': p['minimum_system_ram_bytes'],
            'minimum_free_vram_bytes': p['minimum_free_vram_bytes']}


def artifacts(configuration):
    p = policy()
    receipt, _ = cpu.verified_artifacts(configuration)
    path = (ROOT / p['provision_manifest']).resolve()
    if not path.is_relative_to((ROOT / 'reports/private').resolve()) or digest(path) != p['provision_sha256']:
        raise ValueError('GPU_PROVISION_RECEIPT_CHANGED')
    provision = cpu.read(path)
    root = Path(provision['root']).resolve()
    if root != (ROOT / 'local_data/local_inference_gpu/b12110-cuda12.4').resolve() or provision['tag'] != configuration['runtime_tag']:
        raise ValueError('GPU_RUNTIME_IDENTITY_CHANGED')
    for name, sha in provision['files'].items():
        target = (root / name).resolve()
        if not target.is_relative_to(root) or digest(target) != sha:
            raise ValueError('GPU_RUNTIME_BYTES_CHANGED')
    if 'ggml-cuda.dll' not in provision['files'] or 'llama-server.exe' not in provision['files']:
        raise ValueError('GPU_CUDA_BACKEND_MISSING')
    return receipt, root / 'llama-server.exe'


def offload_proof(log):
    matches = re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU', log)
    if not matches or not all(int(a) == int(b) and int(a) > 0 for a, b in matches):
        raise cpu.Deferred('GPU_FULL_OFFLOAD_NOT_CONFIRMED')
    kv = re.findall(r'CUDA\d+\s+KV buffer size\s*=\s*([0-9.]+)\s+MiB', log)
    if not kv or not all(float(size) > 0 for size in kv):
        raise cpu.Deferred('GPU_KV_OFFLOAD_NOT_CONFIRMED')
    return {'offloaded_layers': int(matches[-1][0]), 'total_layers': int(matches[-1][1]),
            'gpu_kv_buffer_bytes': int(sum(float(size) for size in kv) * 1048576),
            'cuda_detected': bool(re.search(r'CUDA\d|ggml_cuda', log))}


def process_memory(process):
    if os.name != 'nt' or process is None:
        return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in (
            'peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    value = Counters(); value.cb = ctypes.sizeof(value)
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    if not fn(int(process._handle), ctypes.byref(value), value.cb):
        return None
    return {'peak_working_set_bytes': value.peak_working_set, 'working_set_bytes': value.working_set, 'private_bytes': value.private}


class Server(cpu.Server):
    """Reuse request/schema/budget/cleanup code, with a separate CUDA launcher."""
    def __init__(self, *, configuration='A', task='research_v3', root=None, memory=cpu.available_memory,
                 gpu=gpu_status, verifier=artifacts, **kwargs):
        super().__init__(root=root or ROOT / 'reports/private/gpu_inference_sessions',
                         settings=settings(configuration, task), memory=memory, verifier=verifier, **kwargs)
        self.gpu = gpu
        self.successful_completions = 0
        self.telemetry = {'inference': 'local_qwen_cuda', 'peak_process_working_set_bytes': 0,
                          'peak_device_used_bytes': 0, 'device_memory_is_process_exclusive': False}

    def _request(self, route, payload, *, timeout):
        result = super()._request(route, payload, timeout=timeout)
        if route == '/v1/chat/completions':
            path = self.folder / 'responses' / (str(self.calls).zfill(4) + '.json')
            path.parent.mkdir(exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump({'received_at': now(), 'response': result}, stream, ensure_ascii=False, indent=2)
        return result

    def sample(self):
        memory = process_memory(self.process)
        if memory:
            self.telemetry['peak_process_working_set_bytes'] = max(self.telemetry['peak_process_working_set_bytes'], memory['peak_working_set_bytes'])
            self.telemetry['last_process_memory'] = memory
        try:
            device = self.gpu(policy()['gpu_index'])
            self.telemetry['peak_device_used_bytes'] = max(self.telemetry['peak_device_used_bytes'], device['used_bytes'])
            self.telemetry['last_gpu'] = device
        except (OSError, cpu.Deferred, subprocess.SubprocessError):
            self.telemetry['gpu_sampling_unavailable'] = True

    def __enter__(self):
        p = policy()
        if not as_utc(self.settings['frozen_at']) <= as_utc(now()) < as_utc(self.settings['review_due_at']):
            raise cpu.Deferred('LOCAL_POLICY_REVIEW_EXPIRED')
        self.telemetry['preflight'] = preflight(p, self.memory(), self.gpu(p['gpu_index']))
        # Share the CPU lock; never contend for other jobs' locks or stop workers.
        self.lock = exclusive_lock(ROOT / 'local_data/local_inference/runtime.lock')
        self.lock.__enter__()
        try:
            receipt, exe = self.verifier(self.settings)
            self.folder = self.root / uuid.uuid4().hex
            self.folder.mkdir(parents=True, exist_ok=False)
            self.secret = secrets.token_urlsafe(32)
            self.key_file = self.folder / 'session.key'; self.key_file.write_text(self.secret, encoding='ascii')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            self.base = 'http://127.0.0.1:' + str(port)
            args = [str(exe), '-m', receipt['model_path'], '--host', '127.0.0.1', '--port', str(port),
                '--api-key-file', str(self.key_file), '--ctx-size', str(self.settings['context_tokens']),
                '--threads', '2', '--threads-batch', '2', '--parallel', '1', '--n-gpu-layers', '99', '--fit', 'off',
                '--verbosity', '4',
                '--cache-ram', '0', '--offline',
                '--no-webui', '--no-ui-mcp-proxy', '--no-slots', '--chat-template-kwargs', '{"enable_thinking":false}',
                '--prio', '-1', '--poll', '0']
            env = clean_environment(); env['CUDA_VISIBLE_DEVICES'] = p['gpu_uuid']
            self.log = (self.folder / 'runtime.log').open('wb')
            self.process = self.popen(args, cwd=exe.parent, env=env, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=self.log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            started = self.monotonic()
            while self.monotonic() - started < self.settings['startup_timeout_seconds']:
                if self.process.poll() is not None:
                    raise cpu.Deferred('GPU_RUNTIME_START_FAILED')
                try:
                    if self._request('/health', None, timeout=2).get('status') == 'ok':
                        proof = offload_proof((self.folder / 'runtime.log').read_text(encoding='utf-8', errors='replace'))
                        if not proof['cuda_detected']:
                            raise cpu.Deferred('GPU_CUDA_NOT_CONFIRMED')
                        self.telemetry['offload'] = proof
                        self.sample()
                        return self
                except (OSError, ValueError):
                    pass
                self.pause(.25)
            raise cpu.Deferred('GPU_RUNTIME_STARTUP_TIMEOUT')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def ask(self, messages, schema):
        self.sample()
        try:
            value, usage = super().ask(messages, schema)
            self.successful_completions += 1
            return value, {**usage, 'inference': 'local_cuda'}
        finally:
            self.sample()

    def __exit__(self, *exc):
        if self.process is not None and self.process.poll() is None:
            self.sample()
        return super().__exit__(*exc)
