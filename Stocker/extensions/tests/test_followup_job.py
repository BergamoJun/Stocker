"""ILLUSTRATIVE — dependency state machine, no scheduler/model/network calls."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from extensions import followup_job as worker
from research.local_files import atomic_json,exclusive_lock
from research.validation import digest

AT='2026-11-10T12:00:00Z'


@pytest.fixture
def case(tmp_path,monkeypatch):
    original_methods=worker.methods(); monkeypatch.setattr(worker,'methods',lambda:original_methods); monkeypatch.setattr(worker,'ROOT',tmp_path)
    baseline=tmp_path/'baseline.json'; atomic_json(baseline,{'ILLUSTRATIVE':True})
    source=tmp_path/'reports/private/source/job.json'; atomic_json(source,{'ILLUSTRATIVE':True})
    quality=tmp_path/'reports/private/quality.json'; atomic_json(quality,{'ILLUSTRATIVE':True})
    monkeypatch.setattr(worker.scope,'check',lambda path,**kw:{'baseline_sha256':digest(path)})
    monkeypatch.setattr(worker,'historical_check',lambda *a,**k:{})
    monkeypatch.setattr(worker.panel_job,'check',lambda path:{})
    job=worker.prepare(baseline,source,quality,clock=lambda:AT)
    state={'status':'running','stage':'evaluation'}
    monkeypatch.setattr(worker.coefficient_audit,'source_status',lambda path:({},state,source.parent))
    calls=[]
    def semantic(job,state,**kw): state['semantic']={'status':'complete'}; calls.append('semantic'); return None
    monkeypatch.setattr(worker,'semantic_step',semantic)
    return {'job':job,'source_state':state,'calls':calls,'root':tmp_path,'monkeypatch':monkeypatch}


def test_waiting_job_preserves_original_panel_and_does_no_diagnostics(case):
    before=case['job'].read_bytes(); result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled',diagnostic_save=lambda data:pytest.fail('partial source must not aggregate'))
    assert result['status']=='waiting_for_panel' and result['coefficients']['status']=='waiting_for_panel'
    assert case['job'].read_bytes()==before and case['calls']==['semantic']


def test_source_completion_runs_diagnostics_and_audit_automatically_once(case):
    case['source_state'].update(status='complete',stage='done'); mp=case['monkeypatch']
    mp.setattr(worker.panel_diagnostics,'load_job',lambda path:{'ILLUSTRATIVE':True})
    destination=case['root']/'reports/private/diagnostics'; atomic_json(destination/'diagnostics.json',{'ILLUSTRATIVE':True})
    calls=[]
    def diagnostic(data): calls.append('diagnostic'); return {},destination
    def audit(*a,**k): calls.append('audit'); return {'status':'coefficient_audit_complete','completed_issuers':3}
    result=worker.run(case['job'],clock=lambda:AT,diagnostic_save=diagnostic,audit_step=audit,disable_task=lambda task:'disabled')
    assert result['status']=='complete' and calls==['diagnostic','audit']
    second=worker.run(case['job'],clock=lambda:AT,diagnostic_save=lambda data:pytest.fail('completed job must not repeat'))
    assert second==result and case['calls']==['semantic']


def test_quality_block_does_not_stop_independent_numeric_followup(case):
    def blocked(job,state,**kw): state['semantic']={'status':'blocked_quality'}; return {'reason':'ILLUSTRATIVE failed quality','resume_condition':'new verified candidate'}
    case['monkeypatch'].setattr(worker,'semantic_step',blocked)
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled')
    assert result['status']=='waiting_for_panel' and result['semantic']['status']=='blocked_quality'
    assert case['job'].with_name('INTERRUPTION.ko.md').exists()


def test_low_memory_deferral_is_retryable_without_marking_quality_passed(case):
    def wait(*a,**k): raise worker.local_inference.Deferred('ILLUSTRATIVE_MEMORY')
    case['monkeypatch'].setattr(worker,'semantic_step',wait)
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled')
    assert result['semantic']['status']=='waiting_for_local_resources' and result['status']=='waiting_for_panel'


def test_deadline_and_user_stop_are_bounded_and_do_not_execute_pipeline(case):
    case['job'].with_name('STOP').write_text('ILLUSTRATIVE')
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled')
    assert result['status']=='stopped' and case['calls']==[]


def test_changed_method_or_dependency_stops_with_report(case):
    original=worker.read(case['job'])
    Path(original['quality_path']).write_text('ILLUSTRATIVE CHANGED')
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled')
    assert result['status']=='stopped_integrity' and case['calls']==[]
    assert case['job'].with_name('INTERRUPTION.ko.md').exists()


def test_duplicate_worker_does_not_steal_or_delete_lock(case):
    lock=case['job'].with_name('worker.lock')
    with exclusive_lock(lock): result=worker.run(case['job'],clock=lambda:AT)
    assert result['status']=='already_running' and lock.exists() and case['calls']==[]


def test_outside_job_path_is_rejected_before_any_failure_file_write(tmp_path):
    path=tmp_path/'outside/job.json'
    with pytest.raises(ValueError,match='PRIVATE'): worker.run(path)
    assert not path.parent.exists()


def test_source_exhaustion_is_reported_without_selecting_partial_winners(case):
    case['source_state'].update(status='exhausted',stage='evaluation')
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled')
    assert result['status']=='complete_with_blocks' and result['coefficients']['status']=='blocked_source'


def test_incremental_audit_remains_running_until_all_frozen_issuers_finish(case):
    case['source_state'].update(status='complete_with_gaps',stage='done')
    destination=case['root']/'reports/private/diag'; atomic_json(destination/'diagnostics.json',{})
    case['monkeypatch'].setattr(worker.panel_diagnostics,'load_job',lambda path:{})
    result=worker.run(case['job'],clock=lambda:AT,disable_task=lambda task:'disabled',diagnostic_save=lambda data:({},destination),audit_step=lambda *a,**k:{'status':'running_coefficient_audit','completed_issuers':1,'expected_issuers':5})
    assert result['status']=='running' and result['coefficients']['completed_issuers']==1
