"""Qwen3.5 profile on the existing owned CUDA launcher, lock and tokenizer."""
import json,subprocess,time,threading
from pathlib import Path
from urllib.request import Request
import jsonschema
from research.sec_report import ROOT
from research.sec_transport import now
from research.local_files import atomic_json
from research.validation import digest,stable_hash
from extensions import gpu_inference as gpu
from . import synthesis

PROFILE=Path(__file__).with_name('qwen35_profile.json')

def profile():
    p=json.loads(PROFILE.read_text(encoding='utf-8'))
    if (p['id']!='qwen35-4b' or p['revision']!='8d43d974efc48399cb58957ad49c8b26bf5a109c' or
        p['sha256']!='217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14' or
        p['model_bytes']!=3032091584 or p['enable_thinking'] is not False or p['context_tokens']!=8192 or
        p['output_tokens']!=2048 or p['overhead_reserve']!=256 or p['minimum_host_ram_bytes']!=2_000_000_000 or
        p['minimum_free_vram_bytes']!=5_000_000_000 or p['generation_calls_per_report']!=1):raise ValueError('QWEN35_PROFILE_BOUNDARY_CHANGED')
    return p

def artifacts():
    p=profile();model=(ROOT/p['model_path']).resolve()
    if not model.is_relative_to((ROOT/'local_data/personal_p53_models').resolve()) or model.stat().st_size!=p['model_bytes'] or digest(model)!=p['sha256']:raise ValueError('QWEN35_MODEL_BYTES_CHANGED')
    _,exe=gpu.artifacts(gpu.settings('B'))
    if digest(exe)!=p['runtime_sha256']:raise ValueError('QWEN35_RUNTIME_BYTES_CHANGED')
    return p,model,exe

def server():
    p=profile();launch={}
    def verify(settings):
        _,model,exe=artifacts();return {'model_path':str(model)},exe
    def popen(args,**kwargs):
        launch['argv']=list(args);proc=subprocess.Popen(args,**kwargs);launch['pid']=proc.pid;return proc
    s=gpu.Server(configuration='B',root=ROOT/'reports/private/p54_inference_sessions',verifier=verify,popen=popen)
    s.settings={**s.settings,'model_id':p['model_id'],'context_tokens':p['context_tokens'],'maximum_output_tokens':p['output_tokens'],
      'maximum_calls_per_server':1,'maximum_response_bytes':262144}
    return s,launch

def measure(s,msg):
    prompt=synthesis.token_request(s,'/apply-template',{'messages':msg,'chat_template_kwargs':{'enable_thinking':False}})['prompt']
    tokens=synthesis.token_request(s,'/tokenize',{'content':prompt,'add_special':False,'parse_special':True})['tokens']
    return {'input_tokens':len(tokens),'prompt_sha256':stable_hash(prompt),'token_ids_sha256':stable_hash(tokens),'auxiliary_requests':2}

def run(messages,schema,context_sha256):
    p=profile();s,launch=server();r={'version':'qwen35-owned-cuda-v1','model':'qwen35-4b','profile_sha256':digest(PROFILE),'context_sha256':context_sha256,
      'messages_sha256':stable_hash(messages),'schema_sha256':stable_hash(schema),'started_at':now(),'execution_status':'NOT_RUN','request_attempts':0,
      'response':None,'error':None,'usage':{},'measurement':None,'provider_seconds':None,'external_calls':0,'paid_cost_usd':'0'}
    stopped=threading.Event();sampler=None
    def sample():
        while not stopped.wait(1):
            if s.process and s.process.poll() is None:s.sample()
    try:
        with s:
            sampler=threading.Thread(target=sample,daemon=True);sampler.start();r['measurement']=measure(s,messages)
            if r['measurement']['input_tokens']>p['maximum_input_tokens']:raise ValueError('EXACT_QWEN35_CONTEXT_BUDGET_EXCEEDED')
            payload={'model':p['model_id'],'messages':messages,**p['sampling'],'max_tokens':p['output_tokens'],'stream':False,
              'chat_template_kwargs':{'enable_thinking':False},'reasoning_format':'deepseek',
              'response_format':{'type':'json_schema','json_schema':{'name':'scoped_research','strict':True,'schema':schema}}}
            s.calls+=1;r['request_attempts']=1;r['request_sha256']=stable_hash(payload);start=time.monotonic()
            try:
                req=Request(s.base+'/v1/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers={'Authorization':'Bearer '+s.secret,'Content-Type':'application/json'})
                with s.opener.open(req,timeout=p['request_timeout_seconds']) as f:raw=f.read(s.settings['maximum_response_bytes']+1)
                if len(raw)>s.settings['maximum_response_bytes']:raise ValueError('QWEN35_RESPONSE_BYTE_CAP')
                data=json.loads(raw)
                for choice in data.get('choices',[]):
                    thought=choice.get('message',{}).pop('reasoning_content',None)
                    if thought:r['unexpected_reasoning_content']={'sha256':stable_hash(thought),'stored':False}
                path=s.folder/'FINAL_RESPONSE.json';atomic_json(path,data);r.update(raw_response_path=str(path),raw_response_sha256=digest(path),usage=data.get('usage',{}),timings=data.get('timings',{}))
                r['execution_status']='ACTUAL_MODEL_EXECUTED'
                if data['choices'][0].get('finish_reason')!='stop':raise ValueError('QWEN35_OUTPUT_TRUNCATED')
                response=json.loads(data['choices'][0]['message']['content']);jsonschema.validate(response,schema)
                if r['usage'].get('prompt_tokens')!=r['measurement']['input_tokens']:raise ValueError('QWEN35_TOKEN_ACCOUNTING_MISMATCH')
                if r['usage']['prompt_tokens']+p['output_tokens']+p['overhead_reserve']>p['context_tokens']:raise ValueError('QWEN35_PROVIDER_BUDGET_EXCEEDED')
                r['response']=response
            finally:r['provider_seconds']=time.monotonic()-start
    except (ValueError,OSError,RuntimeError,KeyError,IndexError,subprocess.SubprocessError,jsonschema.ValidationError) as error:
        r['error']=type(error).__name__+':'+str(error).split('\n')[0][:180]
        if s.calls and r['execution_status']=='NOT_RUN':r['execution_status']='ATTEMPTED_PROVIDER_FAILED'
    finally:
        stopped.set()
        if sampler:sampler.join(5)
        r.update(finished_at=now(),launch=launch,session_folder=str(s.folder) if s.folder else None,telemetry=s.telemetry,
          runtime_log_sha256=digest(s.folder/'runtime.log') if s.folder and (s.folder/'runtime.log').exists() else None)
    return r

def validate_record(r,messages,schema,context_sha256):
    p=profile()
    if r['version']!='qwen35-owned-cuda-v1' or r['model']!='qwen35-4b' or r['profile_sha256']!=digest(PROFILE) or r['context_sha256']!=context_sha256 or r['messages_sha256']!=stable_hash(messages) or r['schema_sha256']!=stable_hash(schema) or r['external_calls']!=0 or r['request_attempts'] not in [0,1]:raise ValueError('QWEN35_REPLAY_CONTRACT_CHANGED')
    if r['execution_status']!='ACTUAL_MODEL_EXECUTED':
        if r['response'] is not None:raise ValueError('QWEN35_UNATTESTED_RESPONSE')
        return False
    _,model,exe=artifacts();folder=Path(r['session_folder']).resolve();raw=Path(r['raw_response_path']).resolve();args=r['launch']['argv']
    if folder.parent!=(ROOT/'reports/private/p54_inference_sessions').resolve() or raw.parent!=folder or digest(raw)!=r['raw_response_sha256'] or digest(folder/'runtime.log')!=r['runtime_log_sha256']:raise ValueError('QWEN35_EXECUTION_EVIDENCE_CHANGED')
    expected={'-m':str(model),'--host':'127.0.0.1','--ctx-size':str(p['context_tokens']),'--n-gpu-layers':'99','--fit':'off','--chat-template-kwargs':'{"enable_thinking":false}'}
    if args[0]!=str(exe) or any(args[args.index(k)+1]!=v for k,v in expected.items()) or '--api-key-file' not in args or not isinstance(r['launch']['pid'],int):raise ValueError('QWEN35_LAUNCH_IDENTITY_CHANGED')
    proof=gpu.offload_proof((folder/'runtime.log').read_text(encoding='utf-8',errors='replace'))
    if proof!=r['telemetry']['offload'] or not proof['cuda_detected']:raise ValueError('QWEN35_CUDA_NOT_CONFIRMED')
    data=json.loads(raw.read_text(encoding='utf-8'))
    if data.get('usage')!=r['usage']:raise ValueError('QWEN35_USAGE_CHANGED')
    if r['response'] is not None:
        if data['choices'][0]['finish_reason']!='stop' or json.loads(data['choices'][0]['message']['content'])!=r['response']:raise ValueError('QWEN35_SAVED_FINAL_CHANGED')
        jsonschema.validate(r['response'],schema)
    return True
