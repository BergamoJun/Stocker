"""Windowless bounded continuation: local semantics and post-panel audits."""

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from research import panel_job, panel_diagnostics
from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.project_compatibility import check as historical_check
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest, stable_hash
from . import scope, local_inference, semantic_research as semantic, coefficient_audit, weighted_engine, analysis_contract

BASE=Path(__file__).resolve().parent
FILES=["extensions/"+p for p in ("followup_job.py","followup_policy.json","local_inference.py","local_ai_policy.json","semantic_research.py","semantic_contract.schema.json","semantic_quality.py","semantic_quality_cases.json","coefficient_audit.py","install_followup_task.ps1")]


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def methods(): return {p:digest(ROOT/p) for p in FILES}


def policy():
    value=read(BASE/'followup_policy.json')
    if any(value[k] for k in ('monthly_cost_usd','network_requests','external_inference_calls','model_training_enabled','released_scores_enabled')): raise ValueError('FOLLOWUP_SCOPE_CHANGED')
    if not 5<=value['interval_minutes'] or not 1<=value['maximum_elapsed_days']<=14 or not 1<=value['maximum_invocations']<=1000 or not 1<=value['maximum_seconds_per_invocation']<=900 or not 1<=value['maximum_audit_issuers_per_invocation']<=100: raise ValueError('FOLLOWUP_RESOURCE_CAP_CHANGED')
    coefficient_audit.policy()
    return value


def prepare(baseline, source_job, quality_path, *, clock=now):
    proof=scope.check(baseline); panel_job.check(Path(source_job)); settings=policy(); at=clock()
    body={'version':'1.0.0','created_at':at,'deadline_at':(as_utc(at)+timedelta(days=settings['maximum_elapsed_days'])).isoformat(),'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],
        'source_job':str(Path(source_job).resolve()),'source_job_sha256':digest(source_job),'quality_path':str(Path(quality_path).resolve()),'quality_sha256':digest(quality_path),
        'method_hashes':methods(),'policy':settings,'external_calls':0,'released_score':None}
    ident=stable_hash(body)[:16]; body.update(job_id=ident,task_name='Stocker-ResearchFollowup-'+ident); body['job_sha256']=stable_hash(body)
    folder=ROOT/'reports/private/followup_jobs'/ident
    atomic_json(folder/'job.json',body)
    return folder/'job.json'


def check(path):
    path=Path(path).resolve()
    if path.name!='job.json' or not path.is_relative_to((ROOT/'reports/private/followup_jobs').resolve()): raise ValueError('PRIVATE_FOLLOWUP_JOB_REQUIRED')
    job=read(path)
    if not re.fullmatch('[a-f0-9]{16}',job['job_id']) or path.parent.name!=job['job_id'] or job['task_name']!='Stocker-ResearchFollowup-'+job['job_id']: raise ValueError('FOLLOWUP_IDENTITY_CHANGED')
    if stable_hash({k:v for k,v in job.items() if k!='job_sha256'})!=job['job_sha256'] or job['method_hashes']!=methods() or job['policy']!=policy(): raise ValueError('FOLLOWUP_METHOD_OR_JOB_CHANGED')
    historical_check(job['baseline_path'],expected_sha256=job['baseline_sha256'])
    if digest(job['source_job'])!=job['source_job_sha256'] or digest(job['quality_path'])!=job['quality_sha256']: raise ValueError('FOLLOWUP_DEPENDENCY_CHANGED')
    panel_job.check(Path(job['source_job']))
    return job


def disable(task):
    if os.name!='nt' or not re.fullmatch('Stocker-ResearchFollowup-[a-f0-9]{16}',task): return 'not_disabled'
    try:
        result=subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/schtasks.exe'),'/Change','/TN',task,'/DISABLE'],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        return 'disabled' if result.returncode==0 else 'disable_failed'
    except (OSError,subprocess.TimeoutExpired): return 'disable_failed'


def report(folder,state):
    lines=['# 자동 후속 작업 상태','',f"확인: {state['checked_at']} · 상태: {state['status']}",'',
        '로컬 원문 판독, 과거 작업 완료 후 결과 해석과 가격 9개 계수 관련성 검사를 정해진 범위에서 이어갑니다. 원래 수집 작업·마지막 시험·매매 주문은 변경하지 않습니다.','',
        f"- 원문 판독: {state.get('semantic',{}).get('status','not_started')}",f"- 과거 결과 해석: {state.get('diagnostics',{}).get('status','waiting_for_panel')}",f"- 계수 관련성 검사: {state.get('coefficients',{}).get('status','waiting_for_panel')}"]
    for item in state.get('interruptions',[]): lines.append('- 대기/중단: '+item['reason']+' · 재개 조건: '+item['resume_condition'])
    lines+=['','데이터 부족과 품질 미달을 통과로 바꾸지 않습니다. 로컬 AI 검사는 경제 문구 판독 범위이며 가격 인과효과·수익성 검증이 아닙니다.','']
    (folder/'STATUS.ko.md').write_text('\n'.join(lines),encoding='utf-8')
    if state.get('interruptions'): (folder/'INTERRUPTION.ko.md').write_text('\n'.join(lines),encoding='utf-8')


def semantic_step(job,state,*,backend_factory=local_inference.Server,clock=now):
    try: semantic.quality_gate(job['quality_path'])
    except (ValueError,OSError):
        state['semantic']={'status':'blocked_quality'}
        return {'reason':'로컬 AI 의미 판독 품질이 사전 기준에 미달해 실데이터 적용을 보류했습니다.','resume_condition':'새로 검증된 모델/방법의 별도 작업 등록. 기존 실패 기록과 기준은 유지.'}
    results=state.setdefault('semantic',{}).setdefault('issuers',{}); backend=None
    class Lazy:
        @property
        def calls(self): return backend.calls if backend is not None else 0
        def ask(self,messages,schema):
            nonlocal backend
            if backend is None: backend=backend_factory().__enter__()
            return backend.ask(messages,schema)
    try:
        for ticker in job['policy']['semantic_issuers']:
            folder=weighted_engine.resolve(ticker); input_sha=digest(folder/'AI_INPUT.json'); prior=results.get(ticker,{})
            if prior.get('input_sha256')==input_sha and prior.get('pending_paragraphs')==0:
                semantic.validate(Path(prior['folder'])); continue
            ai=analysis_contract.export_ai(folder,clock=clock)
            analyzed=semantic.analyze(ai,Lazy(),clock=clock)
            if analyzed['new_classification_attempts']: time.sleep(1.05)
            value,destination=semantic.save(folder,job['baseline_path'],analyzed,quality_path=job['quality_path'],clock=clock,pinned_scope=True)
            semantic.validate(destination)
            results[ticker]={'input_sha256':input_sha,'folder':str(destination),**value['coverage']}
        complete=all(v.get('pending_paragraphs')==0 for v in results.values()) and len(results)==len(job['policy']['semantic_issuers'])
        state['semantic']['status']=('complete_with_rejections' if any(v.get('rejected_paragraphs',0) for v in results.values()) else 'complete') if complete else 'running_local_semantics'
    finally:
        if backend is not None: backend.__exit__(None,None,None)
    return None


def run(path,*,clock=now,monotonic=time.monotonic,backend_factory=local_inference.Server,disable_task=disable,diagnostic_save=panel_diagnostics.save,audit_step=coefficient_audit.step):
    path=Path(path).resolve(); folder=path.parent
    if path.name!='job.json' or not path.is_relative_to((ROOT/'reports/private/followup_jobs').resolve()): raise ValueError('PRIVATE_FOLLOWUP_JOB_REQUIRED')
    try: job=check(path)
    except Exception:
        state={'checked_at':clock(),'status':'stopped_integrity','interruptions':[{'reason':'작업·방법·원문 의존성 무결성 검사에 실패했습니다.','resume_condition':'원인을 확인하고 원래 기록을 보존한 새 작업 준비.'}]}
        state['task_disabled']=disable_task('Stocker-ResearchFollowup-'+folder.name)
        atomic_json(folder/'integrity_failure.json',state,replace=True); report(folder,state)
        return state
    try:
        with exclusive_lock(folder/'worker.lock'):
            state=read(folder/'state.json') if (folder/'state.json').exists() else {'invocations':0,'semantic':{},'diagnostics':{},'coefficients':{}}
            if state.get('status') in {'complete','complete_with_blocks','stopped','stopped_integrity'}: return state
            state.update(checked_at=clock(),status='running',interruptions=[]); state['invocations']+=1
            if (folder/'STOP').exists() or as_utc(clock())>=as_utc(job['deadline_at']) or state['invocations']>job['policy']['maximum_invocations']:
                state.update(status='stopped',interruptions=[{'reason':'사용자 중단 표시 또는 사전 시간/횟수 상한에 도달했습니다.','resume_condition':'기존 결과를 보존한 별도 작업 재개.'}]); state['task_disabled']=disable_task(job['task_name'])
            else:
                finish=monotonic()+job['policy']['maximum_seconds_per_invocation']
                try:
                    interruption=semantic_step(job,state,backend_factory=backend_factory,clock=clock)
                    if interruption: state['interruptions'].append(interruption)
                except (local_inference.Deferred,LockBusy):
                    state.setdefault('semantic',{})['status']='waiting_for_local_resources'
                except Exception:
                    state.setdefault('semantic',{})['status']='blocked_analysis_error'; state['interruptions'].append({'reason':'원문 판독 또는 결과 재생 검사에 실패했습니다.','resume_condition':'실패 기록의 원인 확인 후 새 검증 작업.'})
                _,source,source_folder=coefficient_audit.source_status(job['source_job'])
                if source['status'] in {'complete','complete_with_gaps'} and source['stage']=='done':
                    if state.get('diagnostics',{}).get('status')!='complete' and monotonic()<finish:
                        dataset=panel_diagnostics.load_job(Path(job['source_job'])); value,destination=diagnostic_save(dataset)
                        state['diagnostics']={'status':'complete','folder':str(destination),'result_sha256':digest(destination/'diagnostics.json')}
                    if state.get('coefficients',{}).get('status')!='coefficient_audit_complete' and monotonic()<finish:
                        state['coefficients']=audit_step(job['source_job'],folder/'coefficient_audit',max_issuers=job['policy']['maximum_audit_issuers_per_invocation'],finish_before=finish)
                elif source['status'] in {'withheld','exhausted'}:
                    state['diagnostics']={'status':'blocked_source'}; state['coefficients']={'status':'blocked_source'}
                    state['interruptions'].append({'reason':'원래 과거 평가가 미완료 상태로 중단돼 전체 표본 해석을 보류합니다.','resume_condition':'원래 수집 작업의 원인 해결 또는 별도 새 표본 작업.'})
                else:
                    state['diagnostics']={'status':'waiting_for_panel'}; state['coefficients']={'status':'waiting_for_panel'}
                semantic_done=state.get('semantic',{}).get('status') in {'complete','complete_with_rejections','blocked_quality','blocked_analysis_error'}
                numeric_done=state.get('diagnostics',{}).get('status') in {'complete','blocked_source'} and state.get('coefficients',{}).get('status') in {'coefficient_audit_complete','blocked_source'}
                if semantic_done and numeric_done:
                    state['status']='complete_with_blocks' if state['interruptions'] else 'complete'; state['task_disabled']=disable_task(job['task_name'])
                elif state.get('diagnostics',{}).get('status')=='waiting_for_panel': state['status']='waiting_for_panel'
            check(path)
            atomic_json(folder/'state.json',state,replace=True); atomic_json(folder/'events'/(uuid.uuid4().hex+'.json'),state); report(folder,state)
            return state
    except LockBusy: return {'status':'already_running','checked_at':clock()}
    except Exception:
        state={'checked_at':clock(),'status':'stopped_integrity','interruptions':[{'reason':'후속 처리의 소스·검증 또는 계산 단계에 실패했습니다.','resume_condition':'원래 결과를 보존하고 실패 원인 확인 후 별도 작업.'}]}
        atomic_json(folder/'failure.json',state,replace=True); report(folder,state); disable_task(job['task_name']); return state


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--job',required=True,type=Path); parser.add_argument('--check',action='store_true'); parser.add_argument('--status',action='store_true'); parser.add_argument('--stop',action='store_true')
    args=parser.parse_args(argv)
    if args.check: job=check(args.job); print(json.dumps({'status':'followup_job_integrity_valid','task_name':job['task_name']})); return
    if args.stop: check(args.job); args.job.with_name('STOP').write_text(now(),encoding='utf-8')
    if args.status:
        value=read(args.job.with_name('state.json')) if args.job.with_name('state.json').exists() else {'status':'not_started'}
    else: value=run(args.job)
    print(json.dumps({'status':value['status'],'semantic_status':value.get('semantic',{}).get('status'),'coefficient_status':value.get('coefficients',{}).get('status'),'folder':str(args.job.parent)}))


if __name__=='__main__': main()
