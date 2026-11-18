"""Explicit candidate sets and guarded whole-equity comparisons; no industry averages."""
from copy import deepcopy
from datetime import date
from decimal import Decimal,localcontext
from research.rights import as_utc
from .scenarios import decimal

VERSION='relative-equity-valuation-v1'
MIN_PEERS=2
METRICS=['P/E','P/S','P/B','FCF_YIELD','EV/EBITDA']
CANDIDATES={'MSFT':['ORCL','AMZN','GOOGL'],'MU':['SNDK','NVDA'],'BE':['PLUG','FCEL'],'XOM':['CVX','OXY']}
FETCH_CANDIDATES={'MSFT':{'ORCL','AMZN'},'MU':{'SNDK'}}


def peer_extension(value,snapshot):
    """Narrow observed-row mappings for acquired peers; no frozen reader is changed."""
    if not snapshot or value['ticker'] not in {'ORCL','AMZN'}:return value
    from . import coverage,valuation
    from research import sec_facts
    from datetime import timedelta
    annual=next((f for f in sec_facts.financial_filings(snapshot['filings']) if f['form']=='10-K'),None)
    if not annual:return value
    parts={};refs={};nodes={}
    for f,sid,source,raw,parsed,dims in coverage.documents(snapshot,value['analysis_as_of']):
        rows=[x for x in parsed if dims.get(x['context_ref'])==[]]
        if f['form']=='10-K':
            starts=sorted({x['start'] for x in rows if x['start'] and x['end']==f['report_date'] and 330<=(date.fromisoformat(x['end'])-date.fromisoformat(x['start'])).days<=370})
            roles=[('annual',starts[0],f['report_date'])] if len(starts)==1 else []
        elif f['report_date']>annual['report_date']:
            start=(date.fromisoformat(annual['report_date'])+timedelta(days=1)).isoformat();end=f['report_date']
            roles=[('current',start,end),('prior',date.fromisoformat(start).replace(year=int(start[:4])-1).isoformat(),date.fromisoformat(end).replace(year=int(end[:4])-1).isoformat())]
        else:roles=[]
        for role,start,end in roles:
            if value['ticker']=='ORCL':
                tag='us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax'
                x=coverage.exact(rows,[tag],start,end,row=lambda text:text.startswith('total revenues '))
                alternatives=[y for y in rows if y['tag'] in valuation.TAGS['revenue'] and y['start']==start and y['end']==end and y['same_entity'] and not y['hidden'] and y['unit']=='USD' and y['status']=='parsed_numeric_evidence_only']
                if not x or any(decimal(y['value'])!=decimal(x['value']) for y in alternatives):continue
            else:
                x=coverage.exact(rows,['us-gaap:PaymentsToAcquireProductiveAssets'],start,end,row=lambda text:text.startswith('purchases of property and equipment '))
                if not x or decimal(x['value'])<0:continue
            ref,meta=coverage.reference(f,sid,source,x);refs[ref]=meta
            parts[role]={'id':ref,'value':x['value'],'unit':'USD','scope':'consolidated','source_refs':[ref],
                'available_at':max(source['available_at'],snapshot['available_at']),
                'period':{'start':start,'end':end,'basis':'annual' if role=='annual' else 'cumulative_ytd'}}
    if not all(k in parts for k in ['annual','current','prior']):return value
    name='revenue' if value['ticker']=='ORCL' else 'capital_expenditure'
    trailing=valuation.trailing(parts['annual'],parts['current'],parts['prior'])
    trailing.update(id='peer:ttm:'+name,evidence_kind='DERIVED',period_classification='VERIFIED_TTM',
        period_days=(date.fromisoformat(trailing['period']['end'])-date.fromisoformat(trailing['period']['start'])).days+1)
    finance=value['valuation_financials'];finance['ttm_'+name]=trailing
    for x in parts.values():finance[x['id']]=x
    if value['ticker']=='AMZN':
        ocf=finance.get('ttm_operating_cash_flow')
        if ocf and ocf['period']==trailing['period'] and ocf['scope']=='consolidated':
            with localcontext() as c:
                c.prec=60
                finance['ttm_fcf']={**trailing,'id':'peer:ttm:fcf','value':str(decimal(ocf['value'])-decimal(trailing['value'])),
                    'input_refs':[ocf['id'],trailing['id']],'formula':'TTM_OCF-TTM_cash_capex'}
    value['source_references'].update(refs)
    value['valuation_metrics']=coverage.calculate(value['market_snapshot'],finance,value['analysis_as_of'])
    value['peer_mapping_notes']=['ORCL: prefer statement total-revenue standard tag only when all same-scope alternatives agree numerically.' if name=='revenue' else 'AMZN: exact cash-flow statement purchases-of-property-and-equipment row; no lease or acquisition proxy.']
    return value


def median(values):
    xs=sorted(map(decimal,values));n=len(xs)
    if not n:raise ValueError('EMPTY_PEER_MEDIAN')
    with localcontext() as c:
        c.prec=60
        return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2


def acquire(ticker,at,fetch_missing=False,current_request=False):
    """At most two MSFT and one MU candidate acquisitions through existing CLI code."""
    from . import core
    rows=[]
    for peer in CANDIDATES.get(ticker,[]):
        value,snapshot=core.build(peer,None if current_request else at,fetch_missing=fetch_missing and peer in FETCH_CANDIDATES.get(ticker,set()),include_p4=False)
        rows.append({'ticker':peer,'value':peer_extension(value,snapshot),'snapshot':snapshot})
    return rows


def classify(target,peer):
    own_topics={x['topic'] for x in peer.get('business_profile',[])}
    target_topics={x['topic'] for x in target.get('business_profile',[])}
    refs=[r for x in peer.get('business_profile',[]) if x['topic'] in {'cloud','software','memory','power','energy'} for r in x['source_refs']]
    tref=[r for x in target.get('business_profile',[]) if x['topic'] in {'cloud','software','memory','power','energy'} for r in x['source_refs']]
    coverage=peer.get('valuation_status')
    if not peer.get('issuer') or not refs:return 'EXCLUDED','Candidate hypothesis only; own-issuer business evidence is not acquired.',refs,tref
    if target['ticker']=='MSFT' and peer['ticker'] in {'ORCL','AMZN','GOOGL'} and 'cloud' in own_topics&target_topics:
        return 'PARTIALLY_COMPARABLE','Source-bound cloud overlap; whole-company portfolio, customers, capital intensity and fiscal year differ. Cloud mention alone does not approve a pure-cloud multiple.',refs,tref
    if target['ticker']=='MU' and peer['ticker']=='SNDK' and 'memory' in own_topics&target_topics:
        return 'PARTIALLY_COMPARABLE','Source-bound memory overlap; product mix, corporate history and fiscal calendar differ. NAND is not the full DRAM/NAND/HBM portfolio.',refs,tref
    return 'INDUSTRY_REFERENCE_ONLY','Business evidence exists but equivalent economic model and accounting scope are unproven.',refs,tref


def metric(value,name):
    return next((x for x in value.get('valuation_metrics',[]) if x['metric_name']==name),None)


def compatible(target,peer,name):
    a,b=metric(target,name),metric(peer,name);reasons=[]
    tm,pm=target['market_snapshot'],peer['market_snapshot']
    if not a or not b or a['calculation_status']!='AVAILABLE' or b['calculation_status']!='AVAILABLE':return ['MISSING_OR_NONMEANINGFUL_METRIC']
    if tm.get('security_type')!='EQUITY' or pm.get('security_type')!='EQUITY':reasons.append('EQUITY_ONLY')
    if tm.get('data_status')!='AVAILABLE' or pm.get('data_status')!='AVAILABLE':reasons.append('STALE_OR_MISSING_PRICE')
    if tm.get('currency')!='USD' or pm.get('currency')!='USD' or a['unit']!=b['unit']:reasons.append('CURRENCY_OR_UNIT_MISMATCH')
    if tm.get('trading_date')!=pm.get('trading_date'):reasons.append('PRICE_DATE_MISMATCH')
    if as_utc(peer['analysis_as_of'])>as_utc(target['analysis_as_of']):reasons.append('FUTURE_PEER_ANALYSIS')
    if (as_utc(target['analysis_as_of'])-as_utc(peer['analysis_as_of'])).total_seconds()>86400:reasons.append('PEER_CUTOFF_TOO_OLD')
    for v,m in [(target,tm),(peer,pm)]:
        if not m.get('security_id') or m.get('cik')!=v['issuer']['cik']:reasons.append('SECURITY_ID_OR_CIK_MISMATCH')
        if m.get('market_cap_scope')!='PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED' or m.get('market_cap_reference_date')!=m.get('trading_date'):reasons.append('MARKET_CAP_SCOPE_MISMATCH')
    if a['formula']!=b['formula']:reasons.append('VALUATION_DEFINITION_MISMATCH')
    pa,pb=a.get('fiscal_period'),b.get('fiscal_period')
    if not pa or not pb:return sorted(set(reasons+['FINANCIAL_PERIOD_MISSING']))
    if pa['basis']!=pb['basis']:reasons.append('FISCAL_CLASSIFICATION_MISMATCH')
    if pa['basis']!='instant':
        da=(date.fromisoformat(pa['end'])-date.fromisoformat(pa['start'])).days+1
        db=(date.fromisoformat(pb['end'])-date.fromisoformat(pb['start'])).days+1
        if (da>366)!=(db>366) or abs(da-db)>3:reasons.append('FISCAL_52_53_WEEK_MISMATCH')
    if abs((date.fromisoformat(pa['end'])-date.fromisoformat(pb['end'])).days)>92:reasons.append('FINANCIAL_PERIOD_END_MISMATCH')
    if name!='FCF_YIELD' and (decimal(a['value'])<=0 or decimal(b['value'])<=0):reasons.append('NONPOSITIVE_MULTIPLE')
    if name=='P/E':
        if max(decimal(a['value']),decimal(b['value']))>500:reasons.append('EXTREME_PE_NOT_A_FAIR_VALUE_SIGNAL')
        for v in [target,peer]:
            f=v.get('valuation_financials',{});ni=f.get('ttm_parent_net_income');rev=f.get('ttm_revenue')
            if not ni or not rev or ni['period']!=rev['period'] or decimal(rev['value'])<=0:reasons.append('EARNINGS_SCOPE_OR_PERIOD_UNPROVEN')
            elif abs(decimal(ni['value'])/decimal(rev['value']))<Decimal('.005'):reasons.append('NEAR_ZERO_EARNINGS_MARGIN')
    # EBITDA/EV only comes from the existing full-scope calculator, never a proxy.
    return sorted(set(reasons))


def compare(target,peers,candidates):
    output=[]
    for name in METRICS:
        included=[];excluded=[];seen=set()
        for peer,candidate in zip(peers,candidates):
            if candidate['comparability_status'] not in {'DIRECTLY_COMPARABLE','PARTIALLY_COMPARABLE'}:
                excluded.append({'ticker':peer['ticker'],'reasons':['ECONOMIC_COMPARABILITY_NOT_ESTABLISHED']});continue
            cik=(peer.get('issuer') or {}).get('cik')
            if not cik or cik in seen or cik==target['issuer']['cik']:
                excluded.append({'ticker':peer['ticker'],'reasons':['DUPLICATE_OR_TARGET_ISSUER']});continue
            reasons=compatible(target,peer,name)
            if reasons:excluded.append({'ticker':peer['ticker'],'reasons':reasons});continue
            seen.add(cik);included.append({'ticker':peer['ticker'],'value':metric(peer,name)['value'],'input_refs':metric(peer,name)['input_refs'],
                'input_ref_scope':'peer_inputs.'+peer['ticker']+'.value',
                'source_refs':['peer:'+peer['ticker']+':'+key for key in peer.get('source_references',{})]})
        target_metric=metric(target,name);current=target_metric['value'] if target_metric and target_metric['calculation_status']=='AVAILABLE' else None
        enough=len(included)>=MIN_PEERS and current is not None
        med=median(x['value'] for x in included) if enough else None
        with localcontext() as c:
            c.prec=60
            premium=(decimal(current)/med-1)*100 if enough and name!='FCF_YIELD' and med>0 else None
            spread=decimal(current)-med if enough and name=='FCF_YIELD' else None
        output.append({'metric_name':name,'target_value':current,'peer_median':str(med) if med is not None else None,
            'peer_range':[str(min(decimal(x['value']) for x in included)),str(max(decimal(x['value']) for x in included))] if enough else None,
            'premium_discount_percent':str(premium) if premium is not None else None,
            'yield_spread_percentage_points':str(spread) if spread is not None else None,'minimum_peers':MIN_PEERS,
            'eligible_peer_count':len(included),'candidate_count':len(candidates),'included':included,'excluded':excluded,
            'output_status':'PARTIAL_COMPARISON' if enough else 'INSUFFICIENT_PEERS','method_version':VERSION,
            'formula':'target_yield - peer_median_yield (percentage points)' if name=='FCF_YIELD' else '(target_multiple / peer_median - 1) * 100',
            'input_refs':[name]+['peer:'+x['ticker']+':'+name for x in included],
            'assumption_type':'PARTIAL_OBSERVED_COMPARISON',
            'source_as_of':target['analysis_as_of'],'limitations':['Partial whole-company peer set, not an industry average or intrinsic overvaluation verdict.',
                'One-off earnings and accounting differences have not been independently audited. Fiscal ends may differ within92 days.']})
    return output


def enrich(value,packages):
    from . import core,methods
    value.update(peer_inputs=deepcopy(packages),peer_candidates=[],peer_comparability=[],relative_valuation=[])
    if value['market_snapshot'].get('security_type')=='ETF':return
    peers=[]
    for item in packages:
        peer=item['value']
        if peer['ticker']!=item['ticker'] or peer['provenance']['method_hashes']!=methods():raise ValueError('PEER_METHOD_OR_IDENTITY_CHANGED')
        rebuilt,_=core.build(peer['ticker'],peer['analysis_as_of'],snapshot=item['snapshot'],market_snapshot=peer['market_snapshot'],
            sec_acquisition=peer.get('sec_acquisition'),illustrative=peer['is_illustrative'],include_p4=False)
        rebuilt=peer_extension(rebuilt,item['snapshot'])
        if rebuilt!=peer:raise ValueError('PEER_INPUT_REPLAY_CHANGED')
        status,reason,refs,trefs=classify(value,peer)
        aliases=[]
        for ref in peer['source_references']:
            alias='peer:'+peer['ticker']+':'+ref;value['source_references'][alias]=peer['source_references'][ref];aliases.append(alias)
        candidate={'target_ticker':value['ticker'],'peer_ticker':peer['ticker'],'peer_security_id':peer['market_snapshot'].get('security_id'),
            'economic_similarity':'Source-bound product overlap only' if status=='PARTIALLY_COMPARABLE' else 'UNVERIFIED',
            'key_differences':reason,'source_refs':list(dict.fromkeys(['peer:'+peer['ticker']+':'+r for r in refs]+trefs)),'selection_as_of':value['analysis_as_of'],
            'data_coverage':peer.get('valuation_status'),'comparability_status':status}
        value['peer_candidates'].append(candidate);peers.append(peer)
    value['peer_comparability']=value['peer_candidates']
    if value.get('issuer'):value['relative_valuation']=compare(value,peers,value['peer_candidates'])
