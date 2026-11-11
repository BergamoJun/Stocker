"""Bounded resumable CUDA batches and immutable source-level inference cache."""
from pathlib import Path
import time
import uuid
from research.local_files import exclusive_lock, LockBusy
from research.validation import digest, stable_hash
from research.sec_transport import now
from . import VERSION, LIMITS, methods, settings, read, write_once, seal, verify_seal, boundaries
from . import engine, segmentation
from .. import gpu_inference as gpu, local_inference as cpu, backend_identity


def cache_key(unit):
    return stable_hash({'unit':unit,'method':methods(),'settings':settings(),'version':VERSION})


def validate_cached(value, unit):
    verify_seal(value)
    if value['key']!=cache_key(unit) or value['unit']!=unit or value.get('methods')!=methods() or value.get('settings')!=settings(): raise ValueError('V4_CACHE_IDENTITY_CHANGED')
    engine.replay(unit,value['row'])
    return value['row']


def cached(root, unit):
    p=Path(root)/'complete'/(cache_key(unit)+'.json')
    return validate_cached(read(p),unit) if p.exists() else None


def previous(root, unit):
    paths=sorted((Path(root)/'attempts'/cache_key(unit)).glob('*.json'))
    if not paths: return None
    value=read(paths[-1]);validate_cached(value,unit)
    return value['row']


def run(inputs, output, cache, *, max_calls=160, retry_failed=False, factory=gpu.Server):
    output=Path(output)
    if not output.resolve().is_relative_to((gpu.ROOT/'reports/private').resolve()):raise ValueError('V4_PRIVATE_OUTPUT_REQUIRED')
    output.mkdir(parents=True,exist_ok=False)
    cache=Path(cache).resolve()
    if not cache.is_relative_to((gpu.ROOT/'reports/private').resolve()): raise ValueError('V4_PRIVATE_CACHE_REQUIRED')
    cache.mkdir(parents=True,exist_ok=True)
    if sum(p.stat().st_size for p in cache.rglob('*.json'))>LIMITS['cache_bytes']: raise ValueError('V4_CACHE_CAPACITY_STOP')
    units,coverage=segmentation.source_units(inputs)
    rows={};pending=[];hits=0
    for u in units:
        saved=cached(cache,u)
        if saved is not None: rows[u['unit_id']]=saved;hits+=1
        elif previous(cache,u) is not None and not retry_failed:
            rows[u['unit_id']]=previous(cache,u)
        else: pending.append(u)
    start=time.monotonic();sessions=[];attempts=0;reason=None
    while pending:
        if attempts+3>max_calls: reason='V4_TOTAL_REQUEST_CAP';break
        batch=pending[:min(LIMITS['units_per_server'],(max_calls-attempts)//3)]
        server=factory(configuration='B');identity=None
        try:
            with server:
                for unit in batch:
                    key=cache_key(unit)
                    with exclusive_lock(cache/'locks'/(key+'.lock')):
                        hit=cached(cache,unit)
                        if hit is not None: rows[unit['unit_id']]=hit;hits+=1;continue
                        before=server.calls
                        prior=previous(cache,unit) if retry_failed else None
                        row=engine.process(unit,server,prior=prior)
                        row['model_calls']=server.calls-before
                        rows[unit['unit_id']]=row
                        value=seal({'contract':VERSION+'-source-cache','key':key,'unit':unit,'row':row,
                            'created_at':now(),'methods':methods(),'settings':settings(),
                            'session_folder':str(server.folder) if hasattr(server,'folder') else None})
                        folder=cache/'attempts'/key;number=len(list(folder.glob('*.json')))+1
                        write_once(folder/(str(number).zfill(5)+'.json'),value)
                        if row['status'] in {'COMPLETED','SAFETY_ISOLATED'}:
                            write_once(cache/'complete'/(key+'.json'),value)
                identity=backend_identity.capture(server)
            pending=pending[len(batch):]
        except (cpu.Deferred,LockBusy,OSError) as error:
            reason=str(error) if isinstance(error,cpu.Deferred) else 'V4_RESOURCE_OR_OWNERSHIP_UNAVAILABLE'
            break
        finally:
            attempts+=getattr(server,'calls',0)
            folder=getattr(server,'folder',None)
            sessions.append({'identity':identity,'requests':getattr(server,'calls',0),
                'successful_completions':getattr(server,'successful_completions',0),
                'prompt_tokens':getattr(server,'prompt_tokens',0),'completion_tokens':getattr(server,'completion_tokens',0),
                'folder':str(folder) if folder else None,'telemetry':getattr(server,'telemetry',{}),
                'files':{p.relative_to(folder).as_posix():digest(p) for p in folder.rglob('*') if p.is_file()} if folder else {}})
    for unit in units:
        if unit['unit_id'] not in rows:
            rows[unit['unit_id']]={'unit_id':unit['unit_id'],'status':'NOT_RUN','reason':reason,
                'extract':{'status':'NOT_RUN','raw':None,'reason':reason},'checks':[],'propositions':[],'model_calls':0}
    results=[{'unit':u,'row':rows[u['unit_id']]} for u in units]
    groups={}
    for item in results:
        for p in item['row']['propositions']:
            if p.get('binding') is None:continue
            raw=p['raw'];key=stable_hash([item['unit']['source_key'],item['unit']['paragraph_sha256'],
                {k:raw[k] for k in ['exact_quote','economic_variable','direction','assertion','condition_quotes','time_quotes']}])
            groups.setdefault(key,[]).append({'unit_id':item['unit']['unit_id'],'proposition_id':p['proposition_id']})
    status='ACTUAL_MODEL_EXECUTED' if attempts and any(s['identity'] and s['identity']['execution_status']=='ACTUAL_MODEL_EXECUTED' for s in sessions) else 'MOCK_NOT_ACTUAL_MODEL' if attempts else 'CACHE_REPLAY' if hits else 'NOT_RUN'
    value=seal({'contract':VERSION+'-execution','generated_at':now(),'methods':methods(),'settings':settings(),
        'inputs_sha256':stable_hash(inputs),'cache_root':str(cache),'coverage':{**coverage,'cache_hits':hits,
            'completed_units':sum(r['row']['status']=='COMPLETED' for r in results),
            'failed_units':sum(r['row']['status']=='PARTIAL_OR_FAILED' for r in results),
            'not_run_units':sum(r['row']['status']=='NOT_RUN' for r in results)},'rows':results,
        'execution_status':status,'reason':reason,'request_attempts':attempts,'retry_failed':retry_failed,
        'elapsed_seconds':time.monotonic()-start,'sessions':sessions,**boundaries()})
    value.pop('record_sha256')
    value['proposition_groups']=[{'group_id':key,'references':refs,'economic_event_identity_independently_verified':False} for key,refs in groups.items()]
    value=seal(value)
    write_once(output/'RESULT.json',value)
    return value


def replay(value,inputs):
    verify_seal(value)
    if value['methods']!=methods() or value['settings']!=settings() or value['inputs_sha256']!=stable_hash(inputs): raise ValueError('V4_EXECUTION_METHOD_OR_INPUT_CHANGED')
    units,_=segmentation.source_units(inputs)
    if units!=[r['unit'] for r in value['rows']]: raise ValueError('V4_DENOMINATOR_CHANGED')
    if value['coverage']['completed_units']!=sum(r['row']['status']=='COMPLETED' for r in value['rows']):raise ValueError('V4_COVERAGE_CHANGED')
    for item in value['rows']:
        if item['row']['status']!='NOT_RUN':engine.replay(item['unit'],item['row'])
    if value['request_attempts']!=sum(s['requests'] for s in value['sessions']): raise ValueError('V4_ATTEMPTS_CHANGED')
    for s in value['sessions']:
        if s['identity']: backend_identity.validate(s['identity'])
        if s['folder']:
            root=Path(s['folder']).resolve()
            if not root.is_relative_to((gpu.ROOT/'reports/private/gpu_inference_sessions').resolve()): raise ValueError('V4_SESSION_PATH_INVALID')
            if 'session.key' in s['files'] or any(digest(root/n)!=sha for n,sha in s['files'].items()):raise ValueError('V4_SESSION_BYTES_CHANGED')
    if any(value[k]!=v for k,v in boundaries().items()):raise ValueError('V4_SHADOW_BOUNDARY_CHANGED')
    return value['execution_status']
