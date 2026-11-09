"""ILLUSTRATIVE — source-prefix replay and paired rank checks, no real returns."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from extensions import coefficient_audit as audit,weighted_engine
from extensions.tests.test_weighted_engine import synthetic_bundle
from research import historical_panel
from research.swing_models import Bar,XnysCalendar


@pytest.fixture
def case():
    bundle=synthetic_bundle(); raw=bundle['local_input']['market']['roles']['stock']['history']['bars']
    bars=[Bar(b['session'],*[Decimal(b[k]) for k in ('open','high','low','close','volume')],'2026-11-20T12:00:00Z') for b in raw]
    calendar=XnysCalendar('2026-04-01','2027-01-31'); ref={'reference_session':bars[-1].session,'phase':'development','label_sessions':calendar.review_window(bars[-1].session)['sessions']}
    for i,day in enumerate(ref['label_sessions'],1):
        price=bars[-1].close+Decimal('.1'); bars.append(Bar(day,price,price+1,price-1,price+Decimal('.2'),Decimal(1000),'2026-11-20T12:00:00Z'))
    value=historical_panel.evaluate_case(bars,calendar,ref,as_of='2026-11-20T12:00:00Z',source_ids=['ILLUSTRATIVE'],is_illustrative=True)
    return bundle,bars,calendar,ref,value


def test_nine_price_factors_match_current_formulas_without_filling_other_fourteen(case):
    bundle,bars,calendar,ref,value=case
    subset=audit.factors(value,bars[:-5]); current=weighted_engine.calculate(bundle,'2026-11-10T12:00:00Z',weighted_engine.policy())
    expected={f['id']:f for f in current['factors']}
    assert len(subset)==9 and sum(Decimal(f['weight']) for f in subset.values())==40
    for key,row in subset.items():
        assert row['status']==expected[key]['status']
        if row['value'] is not None: assert abs(Decimal(row['value'])-Decimal(expected[key]['value']))<Decimal('1e-20')


def test_future_prices_and_stored_outcomes_cannot_change_features(case):
    _,bars,calendar,ref,value=case; first=audit.factors(value,bars[:-5])
    changed=deepcopy(value); changed['cost_scenarios']=[]; changed['actual_peak_atr']=999999
    future=[replace(b,open=Decimal(9999),close=Decimal(9999),high=Decimal(10000),low=Decimal(9998)) for b in bars[-5:]]
    assert audit.factors(changed,(bars[:-5]+future)[:-5])==first


def test_source_case_replay_rejects_modified_forecast(case):
    _,bars,calendar,ref,value=case; profile={'cik':'0000001001','symbol':'DEMO','sector_id':'fixture'}
    assert audit.compact(value,profile,ref,bars,calendar,illustrative=True)['status']=='ready'
    changed=deepcopy(value); changed['forecast']['peak']['estimated_modal_bin_mass']=0.999
    with pytest.raises(ValueError,match='REPLAY_CHANGED'): audit.compact(changed,profile,ref,bars,calendar,illustrative=True)


def test_average_tied_ranks_and_constant_signal():
    assert audit.ranks([1,1,2])==[1.5,1.5,3.0]
    assert audit.spearman([1,2,3],[3,2,1])==-1
    assert audit.spearman([1,2,3],[2,4,6])==1
    assert audit.spearman([1,1,1],[1,2,3]) is None


def rows():
    refs=[{'reference_session':f'2026-01-{day:02d}'} for day in range(1,5)]
    values=[]
    for ref in refs:
        for i in range(3): values.append({'cik':str(i),'sector_id':'fixture','phase':'development','reference_session':ref['reference_session'],'status':'ready','reason':None,'weighted_signed_signal':str(i),'equal_signed_signal':str(i),'hold_net_return_pct':str(i-1)})
    s={**audit.policy(),'minimum_issuers_per_date':3,'minimum_reference_dates':4,'minimum_nonoverlapping_blocks':2,'primary_block_length':2,'sensitivity_block_lengths':[1,3],'bootstrap_replications':100}
    return values,refs,s


def test_paired_rank_comparison_and_block_support_do_not_claim_independence():
    values,refs,s=rows(); result=audit.phase_summary(values,refs,s)
    assert result['mean_weighted_spearman']==1 and result['mean_equal_spearman']==1
    assert result['block_sensitivity'][0]['paired_difference_interval']==[0,0]
    assert result['block_sensitivity'][-1]['paired_difference_interval'] is None
    assert result['independent_sample_count'] is None and result['coefficient_selection_performed'] is False


def test_missing_cases_and_dates_remain_in_denominator_not_cash():
    values,refs,s=rows(); values[0].update(status='unavailable',reason='ILLUSTRATIVE_MISSING'); result=audit.phase_summary(values,refs,s)
    assert result['expected_slots']==12 and result['available_slots']==11 and result['missing_slots']==1
    assert result['available_reference_dates']==3
    assert all(b['paired_difference_interval'] is None for b in result['block_sensitivity'])


def test_incomplete_panel_is_not_opened_for_early_performance_selection(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'source_status',lambda path:({}, {'status':'running','stage':'evaluation'},tmp_path))
    monkeypatch.setattr(audit,'read',lambda path:pytest.fail('must not read partial results'))
    result=audit.step(tmp_path/'job.json',tmp_path/'output')
    assert result['status']=='waiting_for_full_panel' and not (tmp_path/'output').exists()


def test_locked_issuers_are_excluded_before_any_result_folder_is_opened():
    cohort={'selected':[{'cik':'1','symbol_partition':'development_and_validation'},{'cik':'2','symbol_partition':'unseen_symbol_final_only'}]}
    assert [p['cik'] for p in audit.selected_profiles(cohort)]==['1']
    cohort['selected'][1]['symbol_partition']='final'
    with pytest.raises(ValueError,match='PARTITION'): audit.selected_profiles(cohort)


def test_duplicate_issuer_and_unlocked_policy_are_rejected(monkeypatch):
    with pytest.raises(ValueError,match='DUPLICATE'): audit.selected_profiles({'selected':[{'cik':'1','symbol_partition':'development_and_validation'}]*2})
    value=audit.policy(); value['final_holdout_unlocked']=True
    monkeypatch.setattr(audit,'read',lambda path:{'coefficient_audit':value})
    with pytest.raises(ValueError,match='BOUNDARY'): audit.policy()
