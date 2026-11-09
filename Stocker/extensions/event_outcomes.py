"""Local observational outcomes; never feed bars or returns to a language model."""
from decimal import Decimal
from statistics import median
from research.rights import require_entitlement,price_uses,RightsError
from . import event_evaluation as events


def calculate(series,w):
    if not all(series.get(k) is True for k in ('corporate_actions_reviewed','trading_halts_reviewed','adjustment_units_consistent')): raise ValueError('EVENT_ACTION_HALT_OR_ADJUSTMENT_NOT_REVIEWED')
    roles={}
    for role in ('stock','market','sector'):
        rows=series[role]
        if len({r['session'] for r in rows})!=len(rows) or rows!=sorted(rows,key=lambda r:r['session']): raise ValueError('DUPLICATE_OR_UNSORTED_EVENT_BARS')
        table={r['session']:r for r in rows}; needed=[w['baseline_session']]+w['sessions']
        if any(s not in table for s in needed): raise ValueError('MISSING_MATCHED_EVENT_SESSION')
        closes=[Decimal(table[s]['close']) for s in needed]
        if any(not n.is_finite() or n<=0 for n in closes): raise ValueError('INVALID_EVENT_PRICE')
        roles[role]={'return':closes[-1]/closes[0]-1,'table':table,'rows':rows}
    stock=roles['stock']; before=[r for r in stock['rows'] if r['session']<=w['pre_event_last_session']][-20:]
    if len(before)!=20: raise ValueError('INSUFFICIENT_PRE_EVENT_ACTIVITY_REFERENCE')
    def notionals(rows):
        result=[]
        for r in rows:
            c,v=Decimal(r['close']),Decimal(r['volume'])
            if not c.is_finite() or c<=0 or not v.is_finite() or v<0: raise ValueError('INVALID_EVENT_ACTIVITY')
            result.append(c*v)
        return result
    reference=median(notionals(before))
    if reference<=0: raise ValueError('ZERO_EVENT_ACTIVITY_REFERENCE')
    activity=median(notionals([stock['table'][s] for s in w['sessions']]))/reference
    return {'issuer_return':str(stock['return']),'market_return':str(roles['market']['return']),'sector_return':str(roles['sector']['return']),'market_relative_return':str(stock['return']-roles['market']['return']),'sector_relative_return':str(stock['return']-roles['sector']['return']),'relative_notional_proxy':str(activity),'notional_definition':'close times reported shares; not actual turnover or institutional flow','causal_effect_verified':False,'trading_profit_verified':False,'current_vintage_limitations':series.get('current_vintage_limitations',True)}


def evaluate(case,observed_at,*,entitlement=None,load_series=None):
    bound=events.eligibility(case,observed_at)
    result={'case_id':case['case_id'],'event_group_id':case['event_group_id'],'status':'withheld','eligibility':bound,'outcome':None,'prices_loaded':False,'overlapping_events':case.get('overlapping_events',[]),'causal_effect_verified':False}
    if not bound['eligible']: return result
    try: require_entitlement(entitlement or {},price_uses('local_research'),observed_at,synthetic=case['is_illustrative'])
    except (RightsError,KeyError): result['reason_code']='PRICE_RIGHTS_NOT_CLEARED'; return result
    if load_series is None: result['reason_code']='NO_APPROVED_LOCAL_PRICE_READER'; return result
    series=load_series(case,bound['window']); result['prices_loaded']=True
    try: result['outcome']=calculate(series,bound['window']); result['status']='observational_outcome_only'
    except (ValueError,KeyError): result['reason_code']='MATCHED_PRICE_ACTION_OR_ACTIVITY_INPUT_INVALID'
    return result
