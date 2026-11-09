"""ILLUSTRATIVE - fake process/HTTP only; no model or external requests."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import pytest
from extensions import local_inference as local
from research.local_files import atomic_json,exclusive_lock,LockBusy
from research.validation import digest


def fixture(tmp_path,monkeypatch,*,failure=False):
    monkeypatch.setattr(local,'ROOT',tmp_path)
    settings=local.policy(); settings.update(frozen_at='2000-01-01T00:00:00Z',review_due_at='2999-01-01T00:00:00Z')
    exe=tmp_path/'local_data/bin/llama-server.exe'; exe.parent.mkdir(parents=True); exe.write_bytes(b'ILLUSTRATIVE')
    class Process:
        def __init__(self): self.code=None; self.terminated=False
        def poll(self): return self.code
        def terminate(self): self.terminated=True; self.code=0
        def wait(self,timeout): return self.code
        def kill(self): self.code=-1
    process=Process(); calls=[]
    def popen(args,**kw): calls.append((args,kw)); return process
    class Opener:
        def __init__(self): self.calls=[]; self.finish='stop'; self.large=False
        def open(self,request,timeout):
            self.calls.append(request)
            if failure: raise OSError('ILLUSTRATIVE')
            if request.full_url.endswith('/health'): return io.BytesIO(b'{"status":"ok"}')
            result={'choices':[{'finish_reason':self.finish,'message':{'content':'{"ready":true}'}}],'usage':{'prompt_tokens':12,'completion_tokens':5}}
            return io.BytesIO(b'x'*(settings['maximum_response_bytes']+1) if self.large else json.dumps(result).encode())
    opener=Opener()
    server=local.Server(root=tmp_path/'reports/private/sessions',settings=settings,popen=popen,opener=opener,memory=lambda:10**10,verifier=lambda s:({'model_path':'ILLUSTRATIVE.gguf'},exe))
    return server,opener,process,calls


def test_cpu_loopback_only_and_secret_environment_is_not_inherited(tmp_path,monkeypatch):
    monkeypatch.setenv('MASSIVE_API_KEY','ILLUSTRATIVE-SECRET'); monkeypatch.setenv('OPENAI_API_KEY','ILLUSTRATIVE-SECRET')
    server,opener,process,calls=fixture(tmp_path,monkeypatch)
    with server:
        args,kw=calls[0]
        assert args[args.index('--host')+1]=='127.0.0.1' and args[args.index('--threads')+1]=='2'
        assert '--no-webui' in args and '--no-ui-mcp-proxy' in args
        assert 'MASSIVE_API_KEY' not in kw['env'] and 'OPENAI_API_KEY' not in kw['env']
        key=server.key_file; assert key.exists()
    assert process.terminated and not key.exists()


def test_local_response_schema_usage_and_request_cap(tmp_path,monkeypatch):
    server,opener,_,_=fixture(tmp_path,monkeypatch); server.settings['maximum_calls_per_server']=1
    schema={'type':'object','properties':{'ready':{'const':True}},'required':['ready'],'additionalProperties':False}
    with server:
        value,usage=server.ask([{'role':'user','content':'ILLUSTRATIVE'}],schema)
        assert value=={'ready':True} and usage['external_calls']==0 and server.prompt_tokens==12
        with pytest.raises(local.Deferred,match='CALL_CAP'): server.ask([{'role':'user','content':'ILLUSTRATIVE'}],schema)
        assert all(r.full_url.startswith('http://127.0.0.1:') for r in opener.calls)


@pytest.mark.parametrize('mode',['truncated','oversized','bad_role','huge_prompt'])
def test_bad_or_out_of_scope_completion_is_rejected(tmp_path,monkeypatch,mode):
    server,opener,_,_=fixture(tmp_path,monkeypatch)
    with server:
        if mode=='truncated': opener.finish='length'
        if mode=='oversized': opener.large=True
        messages=[{'role':'tool' if mode=='bad_role' else 'user','content':'x'*25000 if mode=='huge_prompt' else 'ILLUSTRATIVE'}]
        with pytest.raises(ValueError): server.ask(messages,{'type':'object'})


def test_low_memory_defers_before_starting_or_reading_artifacts(tmp_path,monkeypatch):
    server,_,_,calls=fixture(tmp_path,monkeypatch); server.memory=lambda:0
    with pytest.raises(local.Deferred,match='MEMORY'): server.__enter__()
    assert not calls


def test_runtime_lock_is_not_deleted_or_stolen(tmp_path,monkeypatch):
    server,_,_,calls=fixture(tmp_path,monkeypatch)
    path=tmp_path/'local_data/local_inference/runtime.lock'
    with exclusive_lock(path):
        with pytest.raises(LockBusy): server.__enter__()
    assert path.exists() and not calls


def test_verification_rejects_changed_model_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(local,'ROOT',tmp_path); s=local.policy()
    root=tmp_path/'local_data/models'; root.mkdir(parents=True); model=root/'fixture.gguf'; model.write_bytes(b'ILLUSTRATIVE')
    exe=root/s['runtime_tag']/'llama-server.exe'; exe.parent.mkdir(); exe.write_bytes(b'ILLUSTRATIVE RUNTIME')
    receipt={'root':str(root),'model_path':str(model),'model_bytes':model.stat().st_size,'model_sha256':digest(model),'model_revision':s['model_revision'],'runtime_tag':s['runtime_tag'],'runtime_files':{exe.relative_to(root).as_posix():digest(exe)}}
    path=tmp_path/'reports/private/receipt.json'; atomic_json(path,receipt)
    s.update(provision_manifest=path.relative_to(tmp_path).as_posix(),provision_manifest_sha256=digest(path),model_sha256=digest(model))
    assert local.verified_artifacts(s)[1]==exe
    model.write_bytes(b'ILLUSTRATIVE CHANGED')
    with pytest.raises(ValueError,match='MODEL_BYTES'): local.verified_artifacts(s)


def test_policy_blocks_remote_host_or_paid_scope(monkeypatch):
    original=local.policy()
    for key,value in [('host','0.0.0.0'),('monthly_cost_usd',1),('model_training_enabled',True)]:
        p=deepcopy(original); p[key]=value; monkeypatch.setattr(local,'read',lambda path,p=p:p)
        with pytest.raises(ValueError): local.policy()
