"""ILLUSTRATIVE software fixtures; never actual CUDA execution claims."""
from copy import deepcopy
import io
import json
import os
import pytest

from extensions import gpu_inference as gpu, local_inference as cpu
from research.local_files import exclusive_lock, LockBusy


def device(**changes):
    return {'index': 0, 'uuid': gpu.policy()['gpu_uuid'], 'name': 'ILLUSTRATIVE GPU', 'driver': 'fixture',
            'total_bytes': 8_000_000_000, 'used_bytes': 1_000_000_000, 'free_bytes': 7_000_000_000, **changes}


def fixture(tmp_path, monkeypatch, *, log='CUDA0 ggml_cuda\noffloaded 37/37 layers to GPU\nCUDA0 KV buffer size = 32.00 MiB\n'):
    monkeypatch.setattr(gpu, 'ROOT', tmp_path)
    monkeypatch.setattr(cpu, 'ROOT', tmp_path)
    monkeypatch.setattr(gpu, 'process_memory', lambda p: {'peak_working_set_bytes': 123, 'working_set_bytes': 100, 'private_bytes': 150})
    exe = tmp_path / 'local_data/cuda/llama-server.exe'
    exe.parent.mkdir(parents=True); exe.write_bytes(b'ILLUSTRATIVE')
    class Process:
        code = None
        terminated = False
        def poll(self): return self.code
        def terminate(self): self.terminated = True; self.code = 0
        def wait(self, timeout): return self.code
        def kill(self): self.code = -1
    process = Process(); calls = []
    def popen(args, **kw):
        calls.append((args, kw)); kw['stdout'].write(log.encode()); kw['stdout'].flush()
        return process
    class Opener:
        finish = 'stop'
        def open(self, request, timeout):
            if request.full_url.endswith('/health'):
                return io.BytesIO(b'{"status":"ok"}')
            return io.BytesIO(json.dumps({'choices': [{'finish_reason': self.finish,
                'message': {'content': '{"ready":true}'}}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 5}}).encode())
    opener = Opener()
    server = gpu.Server(root=tmp_path / 'reports/private/gpu_sessions', gpu=lambda _: device(),
        memory=lambda: 2_500_000_000, verifier=lambda s: ({'model_path': 'ILLUSTRATIVE.gguf'}, exe),
        popen=popen, opener=opener)
    return server, process, calls, opener


def test_gpu_host_and_vram_admission_are_separate_from_cpu_policy():
    original = cpu.policy()
    assert gpu.preflight(gpu.policy(), 2_500_000_000, device())['system_available_bytes'] == 2_500_000_000
    assert original['minimum_available_memory_bytes'] == 4_000_000_000
    assert cpu.policy() == original and gpu.settings()['maximum_output_tokens'] == 960


@pytest.mark.parametrize('system,changes,reason', [
    (1_999_999_999, {}, 'HOST_RAM'), (2_500_000_000, {'free_bytes': 4_999_999_999}, 'VRAM'),
    (3_000_000_000, {'uuid': 'other'}, 'IDENTITY'), (3_000_000_000, {'index': 1}, 'IDENTITY')])
def test_insufficient_or_wrong_gpu_never_passes_by_summing_memory(system, changes, reason):
    with pytest.raises(cpu.Deferred, match=reason):
        gpu.preflight(gpu.policy(), system, device(**changes))


def test_cuda_command_loopback_hidden_environment_and_success_proof(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'ILLUSTRATIVE-SECRET')
    monkeypatch.setenv('MASSIVE_API_KEY', 'ILLUSTRATIVE-SECRET')
    server, process, calls, _ = fixture(tmp_path, monkeypatch)
    with server:
        args, kw = calls[0]
        assert args[args.index('--n-gpu-layers') + 1] == '99'
        assert args[args.index('--fit') + 1] == 'off'
        assert args[args.index('--host') + 1] == '127.0.0.1'
        assert args[args.index('--verbosity') + 1] == '4'
        assert args[args.index('--cache-ram') + 1] == '0' and '--offline' in args
        assert '--no-webui' in args and '--no-ui-mcp-proxy' in args
        assert not {'OPENAI_API_KEY', 'MASSIVE_API_KEY'} & set(kw['env'])
        assert kw['env']['CUDA_VISIBLE_DEVICES'] == gpu.policy()['gpu_uuid']
        assert server.telemetry['offload']['offloaded_layers'] == 37
        key = server.key_file
    assert process.terminated and not key.exists()


@pytest.mark.parametrize('log', ['CUDA0 offloaded 0/37 layers to GPU', 'CUDA0 offloaded 20/37 layers to GPU', 'no allocation proof'])
def test_partial_or_missing_offload_proof_cleans_up_without_cpu_fallback(tmp_path, monkeypatch, log):
    server, process, _, _ = fixture(tmp_path, monkeypatch, log=log)
    with pytest.raises(cpu.Deferred, match='OFFLOAD'):
        server.__enter__()
    assert process.terminated and not server.key_file.exists()
    assert (tmp_path / 'local_data/local_inference/runtime.lock').exists()


def test_existing_inference_lock_is_respected(tmp_path, monkeypatch):
    server, _, calls, _ = fixture(tmp_path, monkeypatch)
    with exclusive_lock(tmp_path / 'local_data/local_inference/runtime.lock'):
        with pytest.raises(LockBusy): server.__enter__()
    assert not calls


def test_schema_tokens_raw_response_and_failure_receipts(tmp_path, monkeypatch):
    server, _, _, opener = fixture(tmp_path, monkeypatch)
    schema = {'type': 'object', 'properties': {'ready': {'const': True}}, 'required': ['ready'], 'additionalProperties': False}
    with server:
        value, usage = server.ask([{'role': 'user', 'content': 'ILLUSTRATIVE'}], schema)
        assert value == {'ready': True} and usage['inference'] == 'local_cuda'
        assert server.prompt_tokens == 12 and server.successful_completions == 1
        opener.finish = 'length'
        with pytest.raises(ValueError, match='TRUNCATED'):
            server.ask([{'role': 'user', 'content': 'ILLUSTRATIVE'}], schema)
        assert server.successful_completions == 1 and server.calls == 2
    assert len(list((server.folder / 'responses').glob('*.json'))) == 2


def test_request_cap_and_forbidden_roles_are_unchanged(tmp_path, monkeypatch):
    server, _, _, _ = fixture(tmp_path, monkeypatch)
    with server:
        with pytest.raises(ValueError):
            server.ask([{'role': 'tool', 'content': 'ILLUSTRATIVE'}], {'type': 'object'})
        assert server.calls == 0
        server.calls = server.settings['maximum_calls_per_server']
        with pytest.raises(cpu.Deferred, match='CALL_CAP'):
            server.ask([{'role': 'user', 'content': 'ILLUSTRATIVE'}], {'type': 'object'})


def test_gpu_environment_keeps_only_safe_system_path_addition(monkeypatch):
    monkeypatch.setenv('ProgramFiles', 'C:/ILLUSTRATIVE-System')
    monkeypatch.setenv('SOME_PROVIDER_SECRET', 'ILLUSTRATIVE-SECRET')
    env = gpu.clean_environment()
    assert env['ProgramFiles'] == 'C:/ILLUSTRATIVE-System' and 'SOME_PROVIDER_SECRET' not in env


def test_kv_cache_must_also_be_on_cuda():
    with pytest.raises(cpu.Deferred, match='KV_OFFLOAD'):
        gpu.offload_proof('CUDA0 offloaded 37/37 layers to GPU\nCPU KV buffer size = 576.00 MiB')
