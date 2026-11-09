"""ILLUSTRATIVE - synthetic local bars only; no market profitability claims."""
from copy import deepcopy
from decimal import Decimal
import pytest
from extensions import event_evaluation as e,event_outcomes as o
from extensions.tests.test_event_evaluation import sample,OBSERVED
from research.swing_models import XnysCalendar


def series(w):
 cal=XnysCalendar('2026-09-01','2026-12-30'); days=cal.sessions('2026-09-01',w['sessions'][-1]); rows={}
 for role,last in [('stock','110'),('market','102'),('sector','101')]:
  rows[role]=[{'session':d,'close':last if d in w['sessions'] else '100','volume':'20' if d in w['sessions'] else '10'} for d in days]
 return {**rows,'corporate_actions_reviewed':True,'trading_halts_reviewed':True,'adjustment_units_consistent':True,'current_vintage_limitations':True}


def test_matched_returns_and_notional_are_local_observations():
 w=e.window(sample()['times']['first_public_at']); r=o.calculate(series(w),w)
 assert Decimal(r['issuer_return'])==Decimal('.1')
 assert Decimal(r['market_relative_return'])==Decimal('.08')
 assert Decimal(r['sector_relative_return'])==Decimal('.09')
 assert Decimal(r['relative_notional_proxy'])==Decimal('2.2')
 assert not r['causal_effect_verified'] and not r['trading_profit_verified']


@pytest.mark.parametrize('which',['actions','halts','adjustment','missing','duplicate','zero','nan_volume','too_few','unsorted'])
def test_bad_outcome_inputs_fail_closed(which):
 w=e.window(sample()['times']['first_public_at']); s=series(w)
 if which=='actions': s['corporate_actions_reviewed']=False
 if which=='halts': s['trading_halts_reviewed']=False
 if which=='adjustment': s['adjustment_units_consistent']=False
 if which=='missing': s['sector'].pop()
 if which=='duplicate': s['stock'].append(s['stock'][-1])
 if which=='zero': s['stock'][-1]['close']='0'
 if which=='nan_volume': s['stock'][-1]['volume']='NaN'
 if which=='too_few': s['stock']=s['stock'][-10:]
 if which=='unsorted': s['market'].reverse()
 with pytest.raises(ValueError): o.calculate(s,w)


def test_future_or_unknown_publication_never_loads_prices():
 c=sample(); c['first_public_verified']=False
 def never(*a): raise AssertionError('price reader must not run')
 r=o.evaluate(c,OBSERVED,load_series=never)
 assert not r['prices_loaded'] and r['outcome'] is None


def test_rights_checked_before_price_reader():
 def never(*a): raise AssertionError('uncleared prices must not be parsed')
 r=o.evaluate(sample(),OBSERVED,entitlement={},load_series=never)
 assert r['reason_code']=='PRICE_RIGHTS_NOT_CLEARED' and not r['prices_loaded']


def test_intraday_post_news_volume_not_in_reference():
 w=e.window('2026-11-09T18:00:00Z'); s=series(w)
 next(r for r in s['stock'] if r['session']=='2026-11-09')['volume']='99999999'
 assert Decimal(o.calculate(s,w)['relative_notional_proxy'])==Decimal('2.2')
