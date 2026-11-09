"""ILLUSTRATIVE - bounded independent Windows-worker state transitions."""
from pathlib import Path
import pytest
from research.local_files import atomic_json,exclusive_lock
from extensions import turnover_job as worker

AT='2026-11-10T12:00:00Z'
@pytest.fixture
def job(tmp_path,monkeypatch):
    hashes=worker.methods(); monkeypatch.setattr(worker,'ROOT',tmp_path); monkeypatch.setattr(worker,'methods',lambda:hashes)
    monkeypatch.setattr(worker.scope,'check',lambda p:{'baseline_sha256':'a'*64}); monkeypatch.setattr(worker,'historical_check',lambda *a,**k:{})
    monkeypatch.setattr(worker.coefficient_audit,'source_status',lambda p:({}, {'status':'complete_with_gaps','stage':'done'},tmp_path))
    monkeypatch.setattr(worker.followup_job,'check',lambda p:{})
    source=tmp_path/'source.json'; old=tmp_path/'old.json'; atomic_json(source,{'ILLUSTRATIVE':True}); atomic_json(old,{'ILLUSTRATIVE':True})
    path=worker.prepare(tmp_path/'baseline',source,old,clock=lambda:AT)
    return path

def test_wait_is_resumable_without_source_requests(job):
    def wait(*a,**k): return {'status':'waiting_for_prior_replay','processed_issuers':0}
    result=worker.run(job,clock=lambda:AT,audit_step=wait)
    assert result['status']=='waiting_for_prior_replay' and result['invocations']==1
    assert worker.run(job,clock=lambda:AT,audit_step=wait)['invocations']==2
    assert job.with_name('STATUS.ko.md').exists()

def test_complete_disables_only_own_task_and_does_not_rerun(job):
    calls=[]
    result=worker.run(job,clock=lambda:AT,audit_step=lambda *a,**k:{'status':'turnover_audit_complete','processed_issuers':3},disable_task=lambda name:calls.append(name) or 'disabled')
    assert result['status']=='turnover_audit_complete' and len(calls)==1 and calls[0].startswith('Stocker-Turnover-')
    assert worker.run(job,clock=lambda:AT,audit_step=lambda *a,**k:pytest.fail('finished job repeated'))==result

@pytest.mark.parametrize('mode',['stop','deadline','count'])
def test_stop_and_resource_limits_do_not_start_audit(job,mode):
    clock=lambda:AT
    if mode=='stop': job.with_name('STOP').write_text('ILLUSTRATIVE')
    elif mode=='deadline': clock=lambda:'2026-12-10T12:00:00Z'
    else: atomic_json(job.with_name('state.json'),{'invocations':1000})
    result=worker.run(job,clock=clock,audit_step=lambda *a,**k:pytest.fail('must not run'),disable_task=lambda name:'disabled')
    assert result['status']=='stopped' and job.with_name('INTERRUPTION.ko.md').exists()

def test_changed_dependency_reports_sanitized_failure(job):
    source=Path(worker.read(job)['source_job']); source.write_text('ILLUSTRATIVE changed')
    result=worker.run(job,clock=lambda:AT,disable_task=lambda name:'disabled')
    assert result['status']=='stopped_integrity' and result['reason_code']=='TURNOVER_JOB_DEPENDENCY_CHANGED'

def test_lock_blocks_duplicate_worker_without_deleting_lock(job):
    with exclusive_lock(job.with_name('worker.lock')): result=worker.run(job,clock=lambda:AT)
    assert result['status']=='already_running' and job.with_name('worker.lock').exists()

def test_outside_path_rejected_before_failure_write(tmp_path):
    path=tmp_path/'outside/job.json'
    with pytest.raises(ValueError,match='PRIVATE'): worker.run(path)
    assert not path.parent.exists()

def test_old_job_failure_blocks_without_partial_selection(job):
    result=worker.run(job,clock=lambda:AT,audit_step=lambda *a,**k:{'status':'blocked_prior_replay'},disable_task=lambda name:'disabled')
    assert result['status']=='blocked_prior_replay' and result['task_disabled']=='disabled'

def test_error_contents_are_not_copied_to_status_report(job):
    def fail(*a,**k): raise RuntimeError('ILLUSTRATIVE private vendor value or secret')
    result=worker.run(job,clock=lambda:AT,audit_step=fail,disable_task=lambda name:'disabled')
    assert result['reason_code']=='VALIDATION_OR_IO_FAILURE'
    assert 'private vendor' not in job.with_name('STATUS.ko.md').read_text(encoding='utf-8')
