"""Bounded standalone continuation for fixed turnover candidates."""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from research.local_files import atomic_json,exclusive_lock,LockBusy
from research.project_compatibility import check as historical_check
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest,stable_hash
from . import scope,followup_job,coefficient_audit,turnover_features,turnover_audit

FILES=['extensions/'+n for n in ('turnover_policy.json','turnover_features.py','turnover_report.py','turnover_contract.schema.json','turnover_audit.py','turnover_job.py','install_turnover_task.ps1')]
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def methods(): return {name:digest(ROOT/name) for name in FILES}

def prepare(baseline,source_job,prior_followup,*,clock=now):
    proof=scope.check(baseline); coefficient_audit.source_status(source_job); followup_job.check(prior_followup)
    policy=turnover_features.policy(); at=clock()
    value={'version':'turnover-job-v1','created_at':at,'deadline_at':(as_utc(at)+timedelta(days=policy['runtime']['maximum_elapsed_days'])).isoformat(),
        'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],'source_job':str(Path(source_job).resolve()),'source_job_sha256':digest(source_job),
        'prior_followup':str(Path(prior_followup).resolve()),'prior_followup_sha256':digest(prior_followup),'method_hashes':methods(),'policy':policy}
    ident=stable_hash(value)[:16]; value.update(job_id=ident,task_name='Stocker-Turnover-'+ident); value['job_sha256']=stable_hash(value)
    path=ROOT/'reports/private/turnover_jobs'/ident/'job.json'; atomic_json(path,value); return path

def check(path):
    path=Path(path).resolve()
    if path.name!='job.json' or not path.is_relative_to((ROOT/'reports/private/turnover_jobs').resolve()): raise ValueError('PRIVATE_TURNOVER_JOB_REQUIRED')
    v=read(path)
    if not re.fullmatch('[a-f0-9]{16}',v['job_id']) or path.parent.name!=v['job_id'] or v['task_name']!='Stocker-Turnover-'+v['job_id']: raise ValueError('TURNOVER_JOB_IDENTITY_CHANGED')
    if stable_hash({k:x for k,x in v.items() if k!='job_sha256'})!=v['job_sha256'] or v['method_hashes']!=methods() or v['policy']!=turnover_features.policy(): raise ValueError('TURNOVER_JOB_OR_METHOD_CHANGED')
    historical_check(v['baseline_path'],expected_sha256=v['baseline_sha256'])
    if digest(v['source_job'])!=v['source_job_sha256'] or digest(v['prior_followup'])!=v['prior_followup_sha256']: raise ValueError('TURNOVER_JOB_DEPENDENCY_CHANGED')
    coefficient_audit.source_status(v['source_job']); followup_job.check(v['prior_followup']); return v

def disable(name):
    if os.name!='nt' or not re.fullmatch('Stocker-Turnover-[a-f0-9]{16}',name): return 'not_disabled'
    try:
        r=subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/schtasks.exe'),'/Change','/TN',name,'/DISABLE'],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        return 'disabled' if r.returncode==0 else 'disable_failed'
    except (OSError,subprocess.TimeoutExpired): return 'disable_failed'

def report(folder,state):
    status=state['status']; descriptions={'waiting_for_full_panel':'기존 과거 평가 전체 완료를 기다립니다.','waiting_for_prior_replay':'기존 가격 9개 변수의 원본 재생 검사 완료를 기다립니다.','running_turnover_audit':'원래 거래량·상대거래대금 근사·지속성 결합 후보를 같은 계수에서 비교 중입니다.','turnover_audit_complete':'비교 완료. 후보 채택·학습·정식 점수 승인은 수행하지 않았습니다.','blocked_source':'원래 과거 평가가 중단돼 비교를 보류했습니다.','blocked_prior_replay':'기존 재생 검사가 중단돼 비교를 보류했습니다.','stopped':'요청 또는 실행 상한에 따라 중지했습니다.','stopped_integrity':'자료·코드·검사 경계에 문제가 있어 중지했습니다.'}
    text='\n'.join(['# 거래대금 후보 자동 평가 상태','',f"확인: {state['checked_at']} · 상태: {status}",'',descriptions.get(status,status),'',
        f"처리 기업: {state.get('processed_issuers',0)} / {state.get('expected_issuers','대기')}",'',
        '거래대금은 종가×거래량 근사값이며 기관 순매수·뉴스 선행 매수·승률을 뜻하지 않습니다. 새 API·외부 AI·학습·최종 시험 해제·주문은 없습니다.','',
        f"진단 코드: {state.get('reason_code','없음')}",'재개 조건: 검증 실패는 원인을 해결한 별도 작업이 필요합니다. 선행 작업 대기는 다음 예약에 자동 확인합니다.',''])
    (folder/'STATUS.ko.md').write_text(text,encoding='utf-8')
    if status.startswith(('stopped','blocked')): (folder/'INTERRUPTION.ko.md').write_text(text,encoding='utf-8')

def run(path,*,clock=now,monotonic=time.monotonic,disable_task=disable,audit_step=turnover_audit.step):
    path=Path(path).resolve()
    if path.name!='job.json' or not path.is_relative_to((ROOT/'reports/private/turnover_jobs').resolve()): raise ValueError('PRIVATE_TURNOVER_JOB_REQUIRED')
    folder=path.parent; name='Stocker-Turnover-'+folder.name
    try:
        with exclusive_lock(folder/'worker.lock'):
            job=check(path); state=read(folder/'state.json') if (folder/'state.json').exists() else {'invocations':0}
            if state.get('status') in ('turnover_audit_complete','stopped','stopped_integrity','blocked_source','blocked_prior_replay'): return state
            state.update(checked_at=clock()); state['invocations']+=1
            limits=job['policy']['runtime']
            if (folder/'STOP').exists() or as_utc(clock())>=as_utc(job['deadline_at']) or state['invocations']>limits['maximum_invocations']:
                state['status']='stopped'; state['task_disabled']=disable_task(name)
            else:
                state.update(audit_step(job['source_job'],job['prior_followup'],folder/'audit',max_issuers=limits['maximum_issuers_per_invocation'],finish_before=monotonic()+limits['maximum_seconds_per_invocation']))
                if state['status'] in ('turnover_audit_complete','blocked_source','blocked_prior_replay'): state['task_disabled']=disable_task(name)
            check(path); atomic_json(folder/'state.json',state,replace=True); atomic_json(folder/'events'/(uuid.uuid4().hex+'.json'),state); report(folder,state); return state
    except LockBusy: return {'status':'already_running','checked_at':clock()}
    except Exception as error:
        code=str(error) if re.fullmatch('[A-Z][A-Z0-9_]{2,100}',str(error)) else 'VALIDATION_OR_IO_FAILURE'
        state={'status':'stopped_integrity','checked_at':clock(),'reason_code':code,'task_disabled':disable_task(name)}
        atomic_json(folder/'failure.json',state,replace=True); report(folder,state); return state

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--job',type=Path,required=True); p.add_argument('--check',action='store_true'); p.add_argument('--status',action='store_true'); p.add_argument('--stop',action='store_true'); a=p.parse_args(argv)
    if a.check: value=check(a.job); print(json.dumps({'status':'turnover_job_valid','task_name':value['task_name']})); return
    if a.stop: check(a.job); a.job.with_name('STOP').write_text(now(),encoding='utf-8')
    state=(read(a.job.with_name('state.json')) if a.job.with_name('state.json').exists() else {'status':'not_started'}) if a.status else run(a.job)
    print(json.dumps({'status':state['status'],'processed_issuers':state.get('processed_issuers',0),'folder':str(a.job.parent)}))

if __name__=='__main__': main()
