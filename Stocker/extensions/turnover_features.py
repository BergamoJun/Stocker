"""Fixed local close-times-volume proxies; no inference about trader identity."""
from decimal import Decimal,localcontext
from datetime import date,timedelta
import json
from pathlib import Path
from statistics import median
from research.rights import as_utc
from research.swing_models import XnysCalendar,atr14
from .weighted_features import number,clip,sign,decimal_text

BASE=Path(__file__).resolve().parent

def policy():
    value=json.loads((BASE/'turnover_policy.json').read_text(encoding='utf-8'))
    if any(value[k] for k in ('monthly_cost_usd','network_requests','external_inference_calls','model_training_enabled','released_scores_enabled')) or any(value['rights'][k] for k in ('final_holdout_unlocked','fit_or_select_coefficients','infer_institution_or_insider_activity','numeric_news_impact_enabled')):
        raise ValueError('TURNOVER_SCOPE_CHANGED')
    if value['baseline_sessions']!=20 or value['persistence_sessions']!=[3,5] or value['minimum_sessions']!=25 or value['replacement_weight']!='2' or value['excess_cap']!='1' or not value['candidate_budget_unchanged'] or value['evaluation']['permitted_phases']!=['development','validation']:
        raise ValueError('TURNOVER_PROTOCOL_CHANGED')
    r=value['runtime']
    if r['interval_minutes']<15 or not 1<=r['maximum_elapsed_days']<=14 or not 1<=r['maximum_invocations']<=1000 or not 1<=r['maximum_seconds_per_invocation']<=600 or not 1<=r['maximum_issuers_per_invocation']<=100: raise ValueError('TURNOVER_RESOURCE_CAP_CHANGED')
    return value

def empty(reference,reason):
    return {'reference_session':reference,'status':'unavailable','reason_code':reason,'measure':'close_times_reported_volume_proxy',
        'relative_notional':None,'persistence_3':None,'persistence_5':None,'close_location':None,'return_5_pct':None,'extension_atr':None,
        'signals':{k:None for k in ('original_volume','relative_notional','persistent_notional')},'flags':[],
        'news_alignment':{'status':'withheld','reason':'NO_VERIFIED_EVENT_TIMESTAMP'},'institutional_activity_verified':False,'released_score':None}

def calculate(bars,reference_session=None,*,calendar=None,cutoff=None,historical=False):
    settings=policy(); reference=reference_session or (bars[-1].session if bars else None)
    if reference is None: return empty(reference,'INSUFFICIENT_HISTORY')
    selected=[b for b in bars if b.session<=reference]
    if not selected or selected[-1].session!=reference: return empty(reference,'REFERENCE_MISSING')
    if [b.session for b in selected]!=sorted({b.session for b in selected}): raise ValueError('TURNOVER_SESSION_ORDER_OR_DUPLICATE')
    if len(selected)<settings['minimum_sessions']: return empty(reference,'INSUFFICIENT_HISTORY')
    recent=selected[-25:]
    for b in selected:
        values=[number(getattr(b,k)) for k in ('open','high','low','close','volume')]
        o,h,l,c,v=values
        if min(o,h,l,c)<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h: raise ValueError('TURNOVER_INVALID_BAR')
    calendar=calendar or XnysCalendar((date.fromisoformat(selected[0].session)-timedelta(days=10)).isoformat(),(date.fromisoformat(reference)+timedelta(days=10)).isoformat())
    if [b.session for b in recent]!=calendar.sessions(recent[0].session,reference): return empty(reference,'NONCONTIGUOUS_HISTORY')
    if not historical:
        if cutoff is None: raise ValueError('CURRENT_TURNOVER_REQUIRES_CUTOFF')
        boundary=as_utc(cutoff).replace(microsecond=0)
        if any(as_utc(b.available_at)>=boundary or as_utc(calendar.close(b.session))>=boundary for b in selected): return empty(reference,'PRICE_NOT_AVAILABLE_BEFORE_CUTOFF')
    result=empty(reference,None)
    with localcontext() as context:
        context.prec=60
        values=[]; ratios=[]
        for i in range(len(selected)-5,len(selected)):
            baseline=median(number(b.close)*number(b.volume) for b in selected[i-20:i])
            ratio=number(selected[i].close)*number(selected[i].volume)/baseline if baseline>0 else None
            ratios.append(ratio)
            values.append(sign(number(selected[i].close)-number(selected[i-1].close))*clip(ratio-1,0,1) if ratio is not None else None)
        last=selected[-1]; avg_volume=sum((number(b.volume) for b in selected[-21:-1]),Decimal(0))/20
        original=sign(number(last.close)-number(selected[-2].close))*clip(number(last.volume)/avg_volume-1,0,1) if avg_volume>0 else None
        p3=sum(values[-3:],Decimal(0))/3 if all(v is not None for v in values[-3:]) else None
        p5=sum(values,Decimal(0))/5 if all(v is not None for v in values) else None
        persistent=(values[-1]+p3+p5)/3 if values[-1] is not None and p3 is not None and p5 is not None else None
        try: atr=atr14(selected)
        except ValueError: atr=None
        sma20=sum((number(b.close) for b in selected[-20:]),Decimal(0))/20
        extension=(number(last.close)-sma20)/atr if atr is not None and atr>0 else None
        location=(2*number(last.close)-number(last.high)-number(last.low))/(number(last.high)-number(last.low)) if number(last.high)>number(last.low) else None
        def text(v): return decimal_text(v) if v is not None else None
        result.update(status='available' if all(v is not None for v in (original,values[-1],persistent)) else 'partial',reason_code=None if persistent is not None and original is not None else 'ZERO_TRAILING_BASELINE',
            relative_notional=text(ratios[-1]),persistence_3=text(p3),persistence_5=text(p5),close_location=text(location),return_5_pct=text((number(last.close)/number(selected[-6].close)-1)*100),extension_atr=text(extension),
            signals={'original_volume':text(original),'relative_notional':text(values[-1]),'persistent_notional':text(persistent)})
        if ratios[-1] is not None and ratios[-1]>1: result['flags'].append('RELATIVE_ACTIVITY_EXPANDED')
        if extension is not None and extension>2: result['flags'].append('PRICE_EXTENSION_NOT_PROOF_OF_PRICING_IN')
        if location is not None and location<0 and ratios[-1] is not None and ratios[-1]>1: result['flags'].append('HIGH_ACTIVITY_WITH_LOWER_RANGE_CLOSE')
    return result

def replacement_signals(original_sum,original_factor,features):
    """All arms retain the original factor's two-unit budget; no extra bonus."""
    original_sum,original_factor=number(original_sum),number(original_factor)
    observed=features['signals']['original_volume']
    if observed is None or abs(number(observed)-original_factor)>Decimal('1e-20'): raise ValueError('ORIGINAL_VOLUME_FACTOR_MISMATCH')
    with localcontext() as context:
        context.prec=60; weight=number(policy()['replacement_weight'])
        return {name:(decimal_text(original_sum) if name=='original_volume' else decimal_text(original_sum-weight*original_factor+weight*number(v))) if v is not None else None for name,v in features['signals'].items()}

def completed_before_event(bars,published_at,*,precision,calendar):
    """Conservative timing helper only; caller must independently authenticate news."""
    if precision not in ('second','millisecond'): return {'status':'withheld','reason':'PRECISE_EVENT_TIME_REQUIRED','sessions':[]}
    at=as_utc(published_at)
    return {'status':'timing_filter_only_not_event_verification','reason':None,'sessions':[b.session for b in bars if as_utc(calendar.close(b.session))<at]}
