"""Decimal valuation with explicit periods, attribution and price/share bases."""
from datetime import date,timedelta
from decimal import Decimal,localcontext
from pathlib import Path
from research import sec_facts,sec_fact_review,sec_filing_evidence
from research.rights import as_utc
from research.validation import digest
from extensions import financial_fill

TAGS={'revenue':['us-gaap:Revenues','us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax','us-gaap:SalesRevenueNet'],
 'parent_net_income':['us-gaap:NetIncomeLoss'],'operating_income':['us-gaap:OperatingIncomeLoss'],
 'operating_cash_flow':['us-gaap:NetCashProvidedByUsedInOperatingActivities'],
 'capital_expenditure':['us-gaap:PaymentsToAcquirePropertyPlantAndEquipment'],
 'depreciation_amortization':['us-gaap:DepreciationDepletionAndAmortization','us-gaap:DepreciationDepletionAndAmortizationPropertyPlantAndEquipment'],
 'parent_equity':['us-gaap:StockholdersEquity'],'preferred_equity':['us-gaap:PreferredStockValue']}


def trailing(annual,current,prior):
    """A + current YTD - prior YTD, only for a provably contiguous annual bridge."""
    if not all(x and x.get('value') is not None for x in [annual,current,prior]):raise ValueError('TTM_COMPONENTS_MISSING')
    if annual['unit']!='USD':raise ValueError('TTM_ADDITIVE_MONETARY_VALUES_REQUIRED')
    if any(x['unit']!=annual['unit'] or x['scope']!=annual['scope'] or x.get('amendment_pending') for x in [current,prior,annual]):raise ValueError('TTM_UNIT_SCOPE_OR_AMENDMENT_CONFLICT')
    a,c,p=[x['period'] for x in [annual,current,prior]]
    if a['basis']!='annual' or c['basis']!='cumulative_ytd' or p['basis']!='cumulative_ytd':raise ValueError('TTM_ANNUAL_YTD_BASIS_REQUIRED')
    ad,cd,pd=[{k:date.fromisoformat(x[k]) for k in ['start','end']} for x in [a,c,p]]
    if not 330<=(ad['end']-ad['start']).days<=370:raise ValueError('TTM_ANNUAL_DURATION_INVALID')
    if cd['start']!=ad['end']+timedelta(days=1) or pd['start']!=ad['start'] or not ad['start']<=pd['end']<ad['end']:
        raise ValueError('TTM_GAP_OR_OVERLAP')
    if cd['start'].replace(year=cd['start'].year-1)!=pd['start'] or cd['end'].replace(year=cd['end'].year-1)!=pd['end']:
        raise ValueError('TTM_FISCAL_CALENDAR_AMBIGUOUS')
    with localcontext() as context:
        context.prec=60;value=Decimal(annual['value'])+Decimal(current['value'])-Decimal(prior['value'])
    if not value.is_finite():raise ValueError('NONFINITE_TTM')
    return {'value':str(value),'unit':annual['unit'],'scope':annual['scope'],
        'period':{'start':(pd['end']+timedelta(days=1)).isoformat(),'end':c['end'],'basis':'TTM'},
        'input_refs':[annual['id'],current['id'],prior['id']],'formula':'annual+current_YTD-prior_YTD',
        'available_at':max((x['available_at'] for x in [annual,current,prior] if x.get('available_at')),default=None)}


def financials(snapshot,at):
    if not snapshot:return {},{},[]
    filings=sec_facts.financial_filings(snapshot['filings']);annual=next((f for f in filings if f['form']=='10-K'),None)
    afields={};qfields={};pfields={};balances={};refs={};issues=[]
    newer_quarter=bool(annual and any(f['form']=='10-Q' and f['report_date']>annual['report_date'] and
        f['filed_date']<=as_utc(at).date().isoformat() and (not f.get('accepted_at') or as_utc(f['accepted_at'])<=as_utc(at)) for f in filings))
    settings=financial_fill.policy(at);issuer=snapshot['cik'];settings['issuer_profiles'].setdefault(issuer,{'profile':'standard_statement_rows'})
    for f in filings:
        if f['form'] not in {'10-K','10-Q'} or len(f['document_source_ids'])!=1:continue
        if f['filed_date']>as_utc(at).date().isoformat() or (f.get('accepted_at') and as_utc(f['accepted_at'])>as_utc(at)):continue
        amended=f.get('is_amendment') or any(x['form']==f['form']+'/A' and (not x['report_date'] or x['report_date']==f['report_date']) for x in snapshot['filings'])
        if amended:issues.append('AMENDMENT_SCOPE_UNRESOLVED:'+f['accession']);continue
        sid=f['document_source_ids'][0];source=snapshot['sources'][sid]
        if as_utc(source['available_at'])>as_utc(at):continue
        if digest(source['raw_path'])!=source['raw_sha256']:raise ValueError('VALUATION_SOURCE_HASH_CHANGED')
        raw=Path(source['raw_path']).read_bytes();parsed=sec_filing_evidence.parse(raw,source['raw_sha256'],issuer);dims=financial_fill.members(raw)
        isannual=f['form']=='10-K'
        for name,tags in TAGS.items():
            instant=name in {'parent_equity','preferred_equity'}
            roles=['annual'] if isannual else ['ytd','prior_ytd']
            if instant:roles=['balance']
            for role in roles:
                target={**f};start=None
                if role=='prior_ytd':
                    target['report_date']=date.fromisoformat(f['report_date']).replace(year=int(f['report_date'][:4])-1).isoformat()
                if not isannual and not instant and annual:
                    start=(date.fromisoformat(annual['report_date'])+timedelta(days=1)).isoformat()
                    if role=='prior_ytd':start=date.fromisoformat(start).replace(year=int(start[:4])-1).isoformat()
                spec={'tags':tags,'unit':'USD','basis':'instant' if instant else 'cashflow','scopes':['consolidated']}
                matches=[x for x in parsed if sec_fact_review.eligible(x,target,spec) and dims.get(x['context_ref'])==[] and (not start or x['start']==start)]
                chosen,status=financial_fill.unique(matches);scope='parent_attributable' if name in {'parent_net_income','parent_equity','preferred_equity'} else 'consolidated'
                if name=='revenue' and issuer=='0002115436':
                    chosen,status,definition=financial_fill.select('revenue',parsed,dims,target,spec,settings,issuer)
                    if chosen:scope=definition['scope']
                if chosen is None:continue
                ref=f'{sid}:valuation:{chosen["ordinal"]}'
                refs[ref]={'url':source['source_url'],'raw_path':source['raw_path'],'raw_sha256':source['raw_sha256'],
                    'available_at':max(source['available_at'],snapshot['available_at']), 'filed_date':f['filed_date'],
                    'accession':f['accession'],'locator':{k:chosen[k] for k in ['dom_id','tag','context_ref','row_number','row_text']}}
                field={'id':ref,'value':chosen['value'],'unit':'USD','scope':scope,'source_refs':[ref],
                    'period':{'start':chosen['start'],'end':chosen['end'],'basis':'instant' if instant else 'annual' if isannual else 'cumulative_ytd'},
                    'available_at':refs[ref]['available_at'],'evidence_kind':'OBSERVED'}
                if instant:
                    if name not in balances or field['period']['end']>balances[name]['period']['end']:balances[name]=field
                else:({'annual':afields,'ytd':qfields,'prior_ytd':pfields}[role])[name]=field
    result=dict(balances)
    for name in sorted(set(afields)|set(qfields)):
        try:
            t=annual_trailing(afields[name]) if not newer_quarter and name in afields else trailing(afields.get(name),qfields.get(name),pfields.get(name))
            t.update(id='ttm:'+name,evidence_kind='DERIVED')
            result['ttm_'+name]=t
        except ValueError as e:issues.append(name+':'+str(e))
    a,b=result.get('ttm_operating_cash_flow'),result.get('ttm_capital_expenditure')
    if a and b and a['period']==b['period'] and a['scope']==b['scope'] and Decimal(b['value'])>=0:
        result['ttm_fcf']={'id':'ttm:fcf','value':str(Decimal(a['value'])-Decimal(b['value'])),'unit':'USD','period':a['period'],
            'scope':'consolidated','input_refs':a['input_refs']+b['input_refs'],'formula':'TTM_OCF-TTM_cash_capex','evidence_kind':'DERIVED',
            'available_at':max((x['available_at'] for x in [a,b] if x.get('available_at')),default=None)}
    e,p=result.get('parent_equity'),result.get('preferred_equity')
    if e and p and e['period']==p['period']:
        result['common_equity']={**e,'value':str(Decimal(e['value'])-Decimal(p['value'])),'scope':'parent_common',
            'id':'common_equity','input_refs':[e['id'],p['id']],'formula':'parent_equity-preferred_equity','evidence_kind':'DERIVED'}
    return result,refs,issues


def annual_trailing(field):
    p=field['period']
    if p['basis']!='annual' or field['unit']!='USD' or not 330<=(date.fromisoformat(p['end'])-date.fromisoformat(p['start'])).days<=370:
        raise ValueError('COMPLETE_ANNUAL_PERIOD_REQUIRED')
    return {**field,'period':{**p,'basis':'TTM'},'input_refs':[field['id']],
            'formula':'latest_disclosed_annual_period_is_complete_trailing_fiscal_year'}


def calculate(market,finance,at):
    """Finance entries carry explicit period/scope; absent inputs never become zero."""
    if market.get('security_type')=='ETF':finance={}
    available=market.get('data_status')=='AVAILABLE' and market.get('raw_close') is not None and market.get('currency')=='USD' and market.get('security_type')=='EQUITY'
    if available:
        try:available=not isinstance(market['raw_close'],bool) and Decimal(str(market['raw_close'])).is_finite() and Decimal(str(market['raw_close']))>0
        except ArithmeticError:available=False
    if market.get('source_available_at') and as_utc(market['source_available_at'])>as_utc(at):available=False
    if market.get('market_session_end') and as_utc(market['market_session_end'])>as_utc(at):available=False
    price_date=market.get('trading_date');cap=None
    if available and market.get('market_cap_reference_date')==price_date and market.get('market_cap_scope')=='PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED':
        value=market.get('provider_reported_market_cap')
        if value is not None and not isinstance(value,bool):
            d=Decimal(str(value))
            if d.is_finite() and d>0:cap=d
    def get(name,scope=None,ttm=True):
        x=finance.get(name)
        if not x or x.get('value') is None or x.get('unit')!='USD' or (scope and x.get('scope')!=scope):return None
        if isinstance(x['value'],bool) or not Decimal(str(x['value'])).is_finite():return None
        if x.get('available_at') and as_utc(x['available_at'])>as_utc(at):return None
        if x.get('amendment_pending') or x['period']['end']>as_utc(at).date().isoformat():return None
        if ttm and (x['period']['basis']!='TTM' or not 330<=(date.fromisoformat(x['period']['end'])-date.fromisoformat(x['period']['start'])).days<=370):return None
        return x
    results=[]
    def emit(name,value,formula,inputs,missing=None,meaningless=False,period=None):
        if value is not None and not value.is_finite():value=None;missing=['NONFINITE_RESULT']
        results.append({'metric_name':name,'value':format(value,'f') if value is not None else None,'unit':'percent' if name=='FCF_YIELD' else 'USD' if name in {'EV','EBITDA'} else 'multiple',
            'formula':formula,'input_refs':inputs,'fiscal_period':period,'price_date':price_date,'as_of':at,
            'calculation_status':'AVAILABLE' if value is not None else 'NOT_MEANINGFUL' if meaningless else 'UNAVAILABLE',
            'missing_inputs':missing or [],'limitations':['현재 관측값의 비율이며 적정주가·동종/과거 비교 또는 매매 신호가 아닙니다.']})
    with localcontext() as ctx:
        ctx.prec=60
        ni=get('ttm_parent_net_income','parent_attributable');rev=get('ttm_revenue');fcf=get('ttm_fcf','consolidated');eq=get('common_equity','parent_common',False)
        if rev and rev['scope'] not in {'consolidated','consolidated_sales_excluding_equity_affiliate_and_other_income'}:rev=None
        if cap is not None and ni:
            emit('P/E',cap/Decimal(ni['value']) if Decimal(ni['value'])>0 else None,'provider_market_cap/TTM_parent_net_income',[ni['id'],'market:market_cap'],meaningless=Decimal(ni['value'])<=0,period=ni['period'])
        else:
            eps=finance.get('ttm_diluted_eps');valid=bool(available and eps and eps.get('unit')=='USD/shares' and eps.get('scope')=='common_diluted' and eps['period']['basis']=='TTM' and 330<=(date.fromisoformat(eps['period']['end'])-date.fromisoformat(eps['period']['start'])).days<=370 and eps['period']['end']<=as_utc(at).date().isoformat() and eps.get('share_adjustment_basis') and eps['share_adjustment_basis']==market.get('share_adjustment_basis') and eps.get('security_id')==market.get('security_id') and (not eps.get('available_at') or as_utc(eps['available_at'])<=as_utc(at)) and not eps.get('amendment_pending'))
            e=Decimal(eps['value']) if valid else None
            emit('P/E',Decimal(market['raw_close'])/e if valid and e>0 else None,'raw_price/TTM_diluted_EPS',['market:raw_close',eps['id']] if valid else [],missing=None if valid else ['ALIGNED_MARKET_CAP_AND_PARENT_TTM_OR_SHARE_ALIGNED_TTM_EPS'],meaningless=bool(valid and e<=0),period=eps['period'] if valid else None)
        emit('P/S',cap/Decimal(rev['value']) if cap is not None and rev and Decimal(rev['value'])>0 else None,'market_cap/TTM_revenue',['market:market_cap',rev['id']] if rev else [],missing=[] if cap and rev else ['MARKET_CAP_OR_TTM_REVENUE'],meaningless=bool(rev and Decimal(rev['value'])<=0),period=rev['period'] if rev else None)
        emit('P/B',cap/Decimal(eq['value']) if cap is not None and eq and Decimal(eq['value'])>0 else None,'market_cap/parent_common_equity',['market:market_cap',eq['id']] if eq else [],missing=[] if cap and eq else ['MARKET_CAP_OR_PARENT_COMMON_EQUITY'],meaningless=bool(eq and Decimal(eq['value'])<=0),period=eq['period'] if eq else None)
        emit('FCF_YIELD',Decimal(fcf['value'])/cap*100 if cap is not None and fcf else None,'TTM_FCF/market_cap*100',['market:market_cap',fcf['id']] if fcf else [],missing=[] if cap and fcf else ['MARKET_CAP_OR_TTM_FCF'],period=fcf['period'] if fcf else None)
        op=get('ttm_operating_income','consolidated');da=get('ttm_depreciation_amortization','consolidated');ebitda=None
        if op and da and op['period']==da['period']:ebitda=Decimal(op['value'])+Decimal(da['value'])
        emit('EBITDA',ebitda,'TTM_operating_income+TTM_D_and_A',[op['id'],da['id']] if op and da else [],missing=[] if ebitda is not None else ['ALIGNED_TTM_OPERATING_INCOME_AND_D_AND_A'],period=op['period'] if op else None)
        debt=get('interest_bearing_debt','interest_bearing_complete',False);cash=get('cash','consolidated',False);ev=None
        if cap is not None and debt and cash and debt['period']==cash['period'] and finance.get('ev_adjustments_complete') is True:
            ev=cap+Decimal(debt['value'])-Decimal(cash['value'])
        emit('EV',ev,'market_cap+complete_interest_bearing_debt-cash',['market:market_cap',debt['id'],cash['id']] if debt and cash else [],missing=[] if ev is not None else ['MARKET_CAP_COMPLETE_INTEREST_BEARING_DEBT_CASH_AND_SCOPE'])
        emit('EV/EBITDA',ev/ebitda if ev is not None and ebitda is not None and ebitda>0 else None,'EV/TTM_EBITDA',['EV','EBITDA'],missing=[] if ev is not None and ebitda is not None else ['EV_OR_EBITDA'],meaningless=bool(ebitda is not None and ebitda<=0),period=op['period'] if op else None)
    return results
