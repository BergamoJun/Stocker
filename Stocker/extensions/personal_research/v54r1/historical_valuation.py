"""PIT comparison is optional; today's reconstruction never gains a past vintage."""
from datetime import date
from decimal import Decimal,localcontext
from research.rights import as_utc
from .scenarios import decimal

VERSION='historical-valuation-v1'


def evaluate(points,current,as_of):
    eligible=[];excluded=[];seen=set()
    for p in sorted(points,key=lambda p:p['trading_date']):
        reasons=[];at=p['historical_as_of'];market=p['market'];finance=p['financial']
        if as_utc(at)>as_utc(as_of):reasons.append('FUTURE_HISTORICAL_POINT')
        if p['trading_date']>as_utc(at).date().isoformat():reasons.append('FUTURE_PRICE_DATE')
        if not market.get('session_end') or as_utc(market['session_end'])>as_utc(at):reasons.append('INCOMPLETE_HISTORICAL_SESSION')
        for source in [market,finance]:
            if any(not source.get(k) or as_utc(source[k])>as_utc(at) for k in ['available_at','retrieved_at']):reasons.append('NOT_OBSERVED_AT_HISTORICAL_CUTOFF')
        if not finance.get('accepted_at') or as_utc(finance['accepted_at'])>as_utc(at):reasons.append('FINANCIAL_PUBLICATION_NOT_AVAILABLE')
        if finance.get('amendment_pending') or finance.get('revised_after_cutoff'):reasons.append('REVISED_OR_AMENDED_FINANCIALS')
        if not market.get('point_in_time_verified'):reasons.append('PIT_VINTAGE_NOT_PROVEN')
        for name in ['security_id','issuer','currency','share_basis','formula','period_classification']:
            if not p.get(name) or p[name]!=current.get(name):reasons.append('HISTORICAL_'+name.upper()+'_MISMATCH')
        if not market.get('market_cap_date')==p['trading_date'] or not market.get('shares_date')==p['trading_date']:reasons.append('CURRENT_CAP_OR_SHARES_BACKFILLED')
        if p['trading_date'] in seen:reasons.append('DUPLICATE_HISTORICAL_DATE')
        x=decimal(p['value'])
        if x<=0 and current.get('metric_name')!='FCF_YIELD':reasons.append('NONPOSITIVE_HISTORICAL_MULTIPLE')
        if reasons:excluded.append({'date':p['trading_date'],'reasons':sorted(set(reasons))});continue
        seen.add(p['trading_date']);eligible.append({'date':p['trading_date'],'value':str(x)})
    enough=len(eligible)>=3
    with localcontext() as c:
        c.prec=60
        percentile=Decimal(sum(decimal(x['value'])<=decimal(current['value']) for x in eligible))/len(eligible)*100 if enough else None
    return {'output_status':'AVAILABLE' if enough else 'UNAVAILABLE','percentile':str(percentile) if percentile is not None else None,
        'percentile_definition':'empirical fraction of eligible distinct dates <= current value','minimum_observations':3,
        'eligible_points':eligible,'excluded_points':excluded,'method_version':VERSION,
        'classification':'POINT_IN_TIME' if enough else 'RECONSTRUCTED_HISTORICAL_REFERENCE' if points else 'INSUFFICIENT_PIT_HISTORY',
        'limitations':['No percentile from revised data, current share counts or current-vintage reconstructed bars.']}


def inspect(value):
    """Audit actual cached bar span; do not silently manufacture historical ratios."""
    m=value['market_snapshot'];packet=m.get('source_packet');dates=[]
    if packet:
        import json
        from pathlib import Path
        from datetime import datetime,timezone
        from zoneinfo import ZoneInfo
        for name in ['raw.json','adjusted.json']:
            if name not in packet['evidence']:continue
            raw=json.loads((Path(packet['folder'])/packet.get('file_names',{}).get(name,name)).read_bytes())
            dates.extend(datetime.fromtimestamp(row['t']/1000,timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat() for row in raw.get('results',[]))
    return {'output_status':'UNAVAILABLE','classification':'RECONSTRUCTED_HISTORICAL_REFERENCE' if dates else 'INSUFFICIENT_PIT_HISTORY',
        'available_price_start':min(dates) if dates else None,'available_price_end':max(dates) if dates else None,
        'cached_price_sessions':len(set(dates)),'PIT_valuation_points':0,'percentile':None,'method_version':VERSION,
        'missing_inputs':['Historical market-cap/share/security vintage and contemporaneously available, unrevised financial denominator'],
        'source_as_of':value['analysis_as_of'],'input_refs':['market_snapshot.source_packet'],
        'assumption_type':'NO_HISTORICAL_VALUATION_IMPUTATION',
        'limitations':['Stored historical bars were acquired later. No5-year series or historical percentile was manufactured.']}
