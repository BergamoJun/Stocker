"""ILLUSTRATIVE - source replay binding, fixed comparisons and report output."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import pytest
from research.local_files import atomic_json
from research.validation import digest
from extensions import turnover_report as report,turnover_audit as audit,turnover_features as f
from extensions.tests.test_coefficient_audit import case
from extensions.tests.test_weighted_engine import synthetic_bundle,AT

def test_candidate_case_reuses_exact_old_factor_receipt(case):
    _,bars,calendar,ref,value=case; profile={'cik':'0000001001','symbol':'DEMO','sector_id':'fixture'}
    prior=audit.previous.compact(value,profile,ref,bars,calendar,illustrative=True)
    row=audit.paired_case(value,prior,profile,ref,bars,calendar,illustrative=True)
    assert row['status']=='ready' and row['signals']['original_volume']==prior['weighted_signed_signal']
    index=next(i for i,b in enumerate(bars) if b.session==ref['reference_session'])
    expected=(max(b.high for b in bars[index+1:index+6])/bars[index].close-1)*100
    assert abs(Decimal(row['max_upside_5_pct'])-expected)<Decimal('1e-23')
    modified=deepcopy(prior); modified['weighted_signed_signal']='999'
    with pytest.raises(ValueError,match='RECONSTRUCTION'): audit.paired_case(value,modified,profile,ref,bars,calendar,illustrative=True)

def test_unavailable_old_rows_are_not_made_available_by_new_feature(case):
    _,bars,calendar,ref,value=case; profile={'cik':'1','symbol':'DEMO','sector_id':'fixture'}
    value={**ref,'status':'unavailable','reason_code':'ILLUSTRATIVE','source_ids':['ILLUSTRATIVE'],'actual_evaluation_at':'2026-11-20T12:00:00Z','is_illustrative':True,'point_in_time_verified':False,'released_score':None,'cost_scenarios':[],'screen_decision':'unavailable'}
    prior=audit.previous.compact(value,profile,ref,bars,calendar,illustrative=True)
    assert audit.paired_case(value,prior,profile,ref,bars,calendar,illustrative=True)['status']=='unavailable'

def comparison():
    refs=[{'reference_session':f'2026-01-{i:02d}'} for i in range(1,9)]
    rows=[]
    for ref in refs:
        for i in range(3): rows.append({'cik':str(i),'reference_session':ref['reference_session'],'status':'ready','reason':None,'signals':{'original_volume':str(i),'relative_notional':str(2-i),'persistent_notional':str(i)},'max_upside_5_pct':str(i),'hold_net_return_pct':str(i)})
    settings={**f.policy()['evaluation'],'minimum_issuers_per_date':3,'minimum_reference_dates':4,'minimum_nonoverlapping_blocks':2,'primary_block_length':2,'sensitivity_block_lengths':[1,8],'bootstrap_replications':100}
    return rows,refs,settings

def test_matched_rank_comparison_and_same_dates_block_support():
    rows,refs,settings=comparison(); result=audit.summarize(rows,refs,settings)
    for endpoint in result['endpoints'].values():
        assert endpoint['mean_rank_correlations']['original_volume']==1
        assert endpoint['mean_rank_correlations']['relative_notional']==-1
        assert endpoint['comparisons']['relative_notional']['intervals'][0]['difference_interval']==[-2,-2]
        assert endpoint['comparisons']['persistent_notional']['intervals'][0]['difference_interval']==[0,0]
        assert endpoint['comparisons']['persistent_notional']['intervals'][-1]['difference_interval'] is None
    assert result['independent_sample_count'] is None and not result['winner_selected']

def test_missing_dates_constant_signal_and_duplicate_cases_fail_closed():
    rows,refs,settings=comparison(); rows[0].update(status='unavailable',reason='ILLUSTRATIVE')
    result=audit.summarize(rows,refs,settings)
    assert result['missing_slots']==1
    assert all(v['difference_interval'] is None for v in result['endpoints']['max_upside_5_pct']['comparisons']['relative_notional']['intervals'])
    with pytest.raises(ValueError,match='DUPLICATE'): audit.summarize(rows+[rows[0]],refs,settings)
    rows,refs,settings=comparison()
    for row in rows: row['signals']['persistent_notional']='0'
    assert audit.summarize(rows,refs,settings)['endpoints']['max_upside_5_pct']['available_reference_dates']==0

def test_source_completion_required_before_reading_partial_returns(tmp_path,monkeypatch):
    monkeypatch.setattr(audit.previous,'source_status',lambda p:({}, {'status':'running','stage':'summary'},tmp_path))
    monkeypatch.setattr(audit,'read',lambda p:pytest.fail('no outcome data may be read yet'))
    assert audit.step(tmp_path/'job',tmp_path/'followup',tmp_path/'out')['status']=='waiting_for_full_panel'
    assert not (tmp_path/'out').exists()

def test_prior_replay_completion_required_before_outcome_access(tmp_path,monkeypatch):
    monkeypatch.setattr(audit.previous,'source_status',lambda p:({}, {'status':'complete_with_gaps','stage':'done'},tmp_path))
    from extensions import followup_job
    monkeypatch.setattr(followup_job,'check',lambda p:{})
    monkeypatch.setattr(audit,'read',lambda p:{'status':'waiting_for_panel','coefficients':{'status':'running_coefficient_audit'}} if Path(p).name=='state.json' else pytest.fail('no partial replay outcomes'))
    assert audit.step(tmp_path/'job',tmp_path/'followup',tmp_path/'out')['status']=='waiting_for_prior_replay'

def test_report_schema_replay_and_render_tampering(tmp_path,monkeypatch):
    bundle=synthetic_bundle(); hashes=report.methods(); source=tmp_path/'input'; atomic_json(source/'bundle.json',bundle)
    monkeypatch.setattr(report,'ROOT',tmp_path)
    schema=Path(report.__file__).with_name('turnover_contract.schema.json').read_text(encoding='utf-8'); target=tmp_path/'extensions/turnover_contract.schema.json'; target.parent.mkdir(); target.write_text(schema,encoding='utf-8')
    monkeypatch.setattr(report,'methods',lambda:hashes)
    monkeypatch.setattr(report.analysis_contract,'validate',lambda p:deepcopy(bundle))
    monkeypatch.setattr(report.scope,'check',lambda p:{'baseline_sha256':'a'*64}); monkeypatch.setattr(report,'historical_check',lambda *a,**k:{})
    value,folder=report.save(source,tmp_path/'baseline',clock=lambda:AT)
    assert report.validate(folder)==value and value['released_score'] is None and value['released_price_plan'] is None
    assert value['benchmarks']['market']['status']=='available' and value['features']['news_alignment']['status']=='withheld'
    (folder/'REPORT.ko.md').write_text('ILLUSTRATIVE modified',encoding='utf-8')
    with pytest.raises(ValueError,match='REPLAY'): report.validate(folder)

def test_benchmark_gap_stays_explicit_and_schema_blocks_score():
    import jsonschema
    bundle=synthetic_bundle(); bundle['local_input']['market']['roles'].pop('sector')
    value=report.build(bundle,AT)
    assert value['benchmarks']['sector']['status']=='unavailable'
    assert value['released_score'] is None and value['features']['institutional_activity_verified'] is False

def test_checkpoint_self_hash_detects_modification(tmp_path):
    path=tmp_path/'issuer.json'; atomic_json(path,audit.signed_record({'cik':'ILLUSTRATIVE','rows':[]}))
    value=json.loads(path.read_text(encoding='utf-8')); value['rows']=[{'fabricated':True}]; path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError,match='CHECKPOINT'): audit.checked_record(path)

def test_incremental_real_code_path_reuses_replay_then_detects_raw_change(case,tmp_path,monkeypatch):
    _,bars,calendar,ref,value=case; profile={'cik':'0000001001','symbol':'DEMO','sector_id':'fixture'}
    source=tmp_path/'source'; old=tmp_path/'old'; out=tmp_path/'reports/private/new_audit'; ticker=audit.disk_name(profile['symbol'])
    price=source/'prices'/ticker/'prices.json'; atomic_json(price,{'ILLUSTRATIVE':True})
    price.with_name('raw_response.json').write_bytes(b'ILLUSTRATIVE RAW')
    path=source/'evaluation'/ticker/'cases'/(ref['reference_session']+'.json'); atomic_json(path,value)
    prior=audit.previous.compact(value,profile,ref,bars,calendar,illustrative=True)
    receipt=old/'issuers'/(profile['cik']+'.json'); atomic_json(receipt,{'cik':profile['cik'],'source_hashes':{str(price):digest(price),str(path):digest(path)},'rows':[prior]})
    context={'folder':source,'old':old,'profiles':[profile],'entitlement':{},'fingerprint':{'prior_issuer_sha256':{profile['cik']:digest(receipt)}}}
    monkeypatch.setattr(audit,'ROOT',tmp_path); monkeypatch.setattr(audit,'source_ready',lambda *a:(context,None))
    monkeypatch.setattr(audit.historical_panel,'references',lambda:[ref]); monkeypatch.setattr(audit.panel_prices,'load',lambda *a:({},bars,calendar))
    paired=audit.paired_case
    monkeypatch.setattr(audit,'paired_case',lambda *a,**k:paired(*a,**k,illustrative=True))
    assert audit.step(source/'job',old/'job',out,max_issuers=0)['status']=='running_turnover_audit'
    assert audit.step(source/'job',old/'job',out,max_issuers=1)['status']=='turnover_audit_complete'
    result=audit.checked_record(out/'AUDIT.json')
    assert result['final_cases_read']==0 and result['phases']['development']['available_slots']==1
    assert audit.step(source/'job',old/'job',out)['status']=='turnover_audit_complete'
    price.with_name('raw_response.json').write_bytes(b'ILLUSTRATIVE CHANGED')
    with pytest.raises(ValueError,match='AFTER_CHECKPOINT'): audit.step(source/'job',old/'job',out)

@pytest.mark.parametrize('error',[ValueError('ILLUSTRATIVE private price'),OSError('ILLUSTRATIVE private path or value')])
def test_cli_errors_do_not_emit_private_values(monkeypatch,capsys,error):
    def fail(argv): raise error
    monkeypatch.setattr(report,'_main',fail)
    assert report.main([])==1
    output=capsys.readouterr().out
    assert json.loads(output)['status']=='turnover_report_failed'
    assert 'private' not in output and 'Traceback' not in output
