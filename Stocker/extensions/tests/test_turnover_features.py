"""ILLUSTRATIVE - analytical proxy identities, no real market inputs."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import pytest
from research.swing_models import Bar,XnysCalendar
from extensions import turnover_features as f

@pytest.fixture
def bars():
    calendar=XnysCalendar('2026-01-01','2026-05-01'); sessions=calendar.sessions('2026-01-02','2026-03-31')[:25]
    return [Bar(day,Decimal(100 if i<20 else 81+i),Decimal(101 if i<20 else 82+i),Decimal(99 if i<20 else 80+i),Decimal(100 if i<20 else 81+i),Decimal(1 if i<20 else 2),'2026-04-02T00:00:00Z') for i,day in enumerate(sessions)]

def test_analytical_golden_and_no_institution_or_news_inference(bars):
    expected=json.loads(Path(__file__).with_name('turnover_golden.json').read_text(encoding='utf-8')); value=f.calculate(bars,historical=True)
    for k,v in expected['expected'].items(): assert value[k]==v
    for k,v in expected['signals'].items(): assert value['signals'][k]==v
    assert abs(Decimal(value['signals']['original_volume'])-Decimal(2)/3)<Decimal('1e-25')
    assert value['flags']==expected['flags']
    for k,v in expected['forbidden_claims'].items(): assert value[k]==v

def test_current_day_not_in_baseline_and_excess_does_not_grow_without_bound(bars):
    last=replace(bars[-1],volume=Decimal(2000)); value=f.calculate(bars[:-1]+[last],historical=True)
    assert value['relative_notional']=='2100' and value['signals']['relative_notional']=='1'

def test_shares_and_prices_rescaled_consistently_preserve_proxy_signal(bars):
    scaled=[replace(b,open=b.open/10,high=b.high/10,low=b.low/10,close=b.close/10,volume=b.volume*10) for b in bars]
    a,b=f.calculate(bars,historical=True),f.calculate(scaled,historical=True)
    assert a==b

def test_fractional_volume_is_not_truncated(bars):
    scaled=[replace(b,volume=b.volume/Decimal(1000)) for b in bars]
    assert f.calculate(scaled,historical=True)==f.calculate(bars,historical=True)

def test_high_activity_downward_close_has_negative_signal(bars):
    changed=bars[:-1]+[replace(bars[-1],open=Decimal(95),close=Decimal(95),high=Decimal(100),low=Decimal(94),volume=Decimal(20))]
    result=f.calculate(changed,historical=True)
    assert Decimal(result['signals']['relative_notional'])<0
    assert 'HIGH_ACTIVITY_WITH_LOWER_RANGE_CLOSE' in result['flags']

def test_future_price_or_volume_cannot_change_reference_features(bars):
    future=replace(bars[-1],session='2026-04-01',close=Decimal('NaN'),volume=Decimal(-999))
    assert f.calculate(bars+[future],bars[-1].session,historical=True)==f.calculate(bars,historical=True)

def test_insufficient_gaps_duplicate_and_invalid_values(bars):
    assert f.calculate(bars[:-1],historical=True)['reason_code']=='INSUFFICIENT_HISTORY'
    calendar=XnysCalendar('2026-01-01','2026-05-01')
    gapped=bars[:-1]+[replace(bars[-1],session='2026-04-01')]
    assert f.calculate(gapped,calendar=calendar,historical=True)['reason_code']=='NONCONTIGUOUS_HISTORY'
    with pytest.raises(ValueError,match='ORDER'): f.calculate(bars+[bars[-1]],historical=True)
    for val in (Decimal('NaN'),Decimal('-1')):
        with pytest.raises(ValueError): f.calculate(bars[:-1]+[replace(bars[-1],volume=val)],historical=True)

def test_zero_baseline_is_missing_and_flat_range_is_not_filled(bars):
    result=f.calculate([replace(b,volume=Decimal(0)) for b in bars],historical=True)
    assert result['status']=='partial' and result['signals']['persistent_notional'] is None
    flat=[replace(b,open=Decimal(100),close=Decimal(100),high=Decimal(100),low=Decimal(100)) for b in bars]
    result=f.calculate(flat,historical=True)
    assert result['close_location'] is None and result['extension_atr'] is None
    assert result['signals']['relative_notional']=='0'

def test_current_availability_equal_second_is_excluded(bars):
    result=f.calculate(bars,cutoff='2026-04-02T00:00:00.999Z')
    assert result['status']=='unavailable' and result['reason_code']=='PRICE_NOT_AVAILABLE_BEFORE_CUTOFF'
    assert f.calculate(bars,cutoff='2026-04-02T00:00:02Z')['status']=='available'
    with pytest.raises(ValueError,match='CUTOFF'): f.calculate(bars)

def test_replacement_retains_two_unit_budget_and_missing_is_not_renormalized(bars):
    result=f.calculate(bars,historical=True); original=result['signals']['original_volume']
    values=f.replacement_signals('10',original,result)
    assert values['original_volume']=='10'
    assert abs(Decimal(values['relative_notional'])-(Decimal(10)-2*Decimal(original)+2))<Decimal('1e-25')
    result['signals']['persistent_notional']=None
    assert f.replacement_signals('10',original,result)['persistent_notional'] is None
    with pytest.raises(ValueError,match='MISMATCH'): f.replacement_signals('10','-1',result)

def test_event_filter_excludes_full_bar_during_or_at_publication_close(bars):
    calendar=XnysCalendar('2026-01-01','2026-05-01'); close=calendar.close(bars[-1].session)
    result=f.completed_before_event(bars,close,precision='second',calendar=calendar)
    assert bars[-1].session not in result['sessions'] and result['status']=='timing_filter_only_not_event_verification'
    assert f.completed_before_event(bars,close,precision='date',calendar=calendar)['sessions']==[]

def test_policy_cannot_enable_news_scores_training_or_higher_budget(monkeypatch):
    value=f.policy(); value['rights']['numeric_news_impact_enabled']=True
    monkeypatch.setattr(f.json,'loads',lambda raw:value)
    with pytest.raises(ValueError,match='SCOPE'): f.policy()
