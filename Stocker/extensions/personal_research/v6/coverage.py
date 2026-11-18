"""P2.1 source-scoped additions; frozen P2 arithmetic remains in valuation.py."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from research import sec_facts, sec_filing_evidence
from research.rights import as_utc
from research.validation import digest
from extensions import financial_fill
from . import valuation

DEBT_POLICY = {'definition':'market_cap + interest-bearing debt - unrestricted cash + preferred + NCI',
    'finance_leases':'include; require evidence they are not already in debt',
    'operating_leases':'exclude consistently; disclose, never mix lease-adjusted EBITDA',
    'missing_components':'withhold EV; total liabilities are not debt'}


def documents(snapshot, at):
    if not snapshot:return
    if as_utc(snapshot['available_at'])>=as_utc(at):return
    for f in sec_facts.financial_filings(snapshot['filings']):
        if f['form'] not in {'10-K','10-Q'} or len(f['document_source_ids'])!=1:continue
        if f['filed_date']>as_utc(at).date().isoformat() or (f.get('accepted_at') and as_utc(f['accepted_at'])>as_utc(at)):continue
        if f.get('is_amendment') or any(a['form']==f['form']+'/A' and a['report_date']==f['report_date'] for a in snapshot['filings']):continue
        sid=f['document_source_ids'][0];s=snapshot['sources'][sid]
        prefix='https://www.sec.gov/Archives/edgar/data/'+str(int(snapshot['cik']))+'/'+f['accession'].replace('-','')+'/'
        if s['kind']!='document' or not s['source_url'].startswith(prefix):raise ValueError('SOURCE_ISSUER_ACCESSION_MISMATCH')
        if as_utc(s['available_at'])>as_utc(at):continue
        if digest(s['raw_path'])!=s['raw_sha256']:raise ValueError('SEC_SOURCE_CHANGED')
        raw=Path(s['raw_path']).read_bytes()
        yield f,sid,s,raw,sec_filing_evidence.parse(raw,s['raw_sha256'],snapshot['cik']),financial_fill.members(raw)


def reference(f,sid,s,x):
    ref=sid+':p21:'+str(x['ordinal'])
    return ref,{'url':s['source_url'],'raw_path':s['raw_path'],'raw_sha256':s['raw_sha256'],
        'available_at':s['available_at'],'published_at':f.get('accepted_at'),'filed_date':f['filed_date'],'accession':f['accession'],
        'locator':{k:x.get(k) for k in ['dom_id','tag','context_ref','row_number','row_text','ordinal']}}


def exact(rows,tags,start,end,*,instant=False,row=None):
    matches=[x for x in rows if x['tag'] in tags and x['start']==start and x['end']==end and x['is_instant']==instant
        and x['same_entity'] and not x['hidden'] and x['status']=='parsed_numeric_evidence_only' and x['unit']=='USD'
        and (row is None or row(x['row_text'].lower()))]
    return financial_fill.unique(matches)[0]


def financials(snapshot,at):
    result,refs,issues=valuation.financials(snapshot,at)
    if not snapshot:return result,refs,issues,[]
    docs=list(documents(snapshot,at));scoped=[];bridges={};ni_bridges={};da_bridges={}
    annual=next((f for f in sec_facts.financial_filings(snapshot['filings']) if f['form']=='10-K'),None)
    def field(f,sid,s,x,name,scope,basis):
        ref,meta=reference(f,sid,s,x);refs[ref]=meta
        return {'id':ref,'value':x['value'],'unit':'USD','scope':scope,'source_refs':[ref],
            'period':{'start':x['start'],'end':x['end'],'basis':basis},'evidence_kind':'OBSERVED',
            'available_at':max(s['available_at'],snapshot['available_at'])}
    for f,sid,s,raw,parsed,dims in docs:
        rows=[x for x in parsed if dims.get(x['context_ref'])==[]];end=f['report_date']
        common=exact(rows,['us-gaap:StockholdersEquity'],None,end,instant=True,
                     row=lambda t:'attributable to common stockholders' in t)
        if common:
            candidate=field(f,sid,s,common,'common_equity','parent_common','instant')
            if not result.get('common_equity') or end>result['common_equity']['period']['end']:result['common_equity']=candidate
        # Inventory actual components without certifying completeness from tag presence.
        for name,tags in {'cash':['us-gaap:CashAndCashEquivalentsAtCarryingValue'],
            'current_debt':['us-gaap:LongTermDebtCurrent'],'noncurrent_debt':['us-gaap:LongTermDebtNoncurrent'],
            'short_borrowings':['us-gaap:ShortTermBorrowings'],'preferred':['us-gaap:PreferredStockValue'],
            'NCI':['us-gaap:MinorityInterest']}.items():
            x=exact(rows,tags,None,end,instant=True)
            if x:scoped.append({'component':name,**field(f,sid,s,x,name,'component_only_not_complete','instant')})
        if not annual:continue
        if f['form']=='10-K':
            starts=sorted({x['start'] for x in rows if x['end']==end and x['start'] and 330<=(date.fromisoformat(end)-date.fromisoformat(x['start'])).days<=370})
            roles=[('annual',starts[0],end)] if len(starts)==1 else []
        elif f['report_date']>annual['report_date']:
            from datetime import timedelta
            start=(date.fromisoformat(annual['report_date'])+timedelta(days=1)).isoformat()
            roles=[('current',start,end),('prior',date.fromisoformat(start).replace(year=int(start[:4])-1).isoformat(),date.fromisoformat(end).replace(year=int(end[:4])-1).isoformat())]
        else:roles=[]
        for role,start,stop in roles:
            basis='annual' if role=='annual' else 'cumulative_ytd'
            if snapshot['cik']=='0001664703':
                x=exact(rows,['us-gaap:Revenues'],start,stop,row=lambda t:t.startswith('total revenue '))
                if x:bridges[role]=field(f,sid,s,x,'revenue','consolidated',basis)
                x=exact(rows,['be:NetIncomeLossAttributableToParentIncludingRedeemableNoncontrollingInterest','us-gaap:NetIncomeLoss'],start,stop,
                    row=lambda t:'attributable to common stockholders' in t)
                if x:ni_bridges[role]=field(f,sid,s,x,'parent_net_income','parent_attributable',basis)
            # Combined D&A-and-other is intentionally excluded. Add separate standard
            # depreciation and intangible amortization only in the same exact context period.
            dep=exact(rows,['us-gaap:Depreciation'],start,stop)
            amo=exact(rows,['us-gaap:AmortizationOfIntangibleAssets'],start,stop)
            if dep and amo and Decimal(dep['value'])>=0 and Decimal(amo['value'])>=0:
                d=field(f,sid,s,dep,'dep','consolidated',basis);a=field(f,sid,s,amo,'amo','consolidated',basis)
                da_bridges[role]={**d,'id':'p21:da:'+f['accession']+':'+role,'value':str(Decimal(d['value'])+Decimal(a['value'])),
                    'evidence_kind':'DERIVED','formula':'depreciation+intangible_amortization','input_refs':[d['id'],a['id']]}
    for name,parts in [('revenue',bridges),('parent_net_income',ni_bridges),('depreciation_amortization',da_bridges)]:
        if all(k in parts for k in ['annual','current','prior']):
            t=valuation.trailing(parts['annual'],parts['current'],parts['prior'])
            # All intermediate nodes remain traceable, including separate D&A sums.
            for part in parts.values():result[part['id']]=part
            t.update(id='p21:ttm:'+name,evidence_kind='DERIVED');result['ttm_'+name]=t
    # Frozen FCF construction shares its period dictionary with OCF. Detach these
    # display records before changing basis so every derived metric is classified.
    for x in result.values():
        if isinstance(x,dict) and x.get('period'):x['period']=dict(x['period'])
    for name,x in result.items():
        if not isinstance(x,dict) or x.get('period',{}).get('basis')!='TTM':continue
        days=(date.fromisoformat(x['period']['end'])-date.fromisoformat(x['period']['start'])).days+1
        kind='LATEST_FISCAL_YEAR' if x.get('formula','').startswith('latest_disclosed_annual') or (name=='ttm_fcf' and result.get('ttm_operating_cash_flow',{}).get('period_classification')=='LATEST_FISCAL_YEAR') else 'VERIFIED_TTM'
        x['period_classification']=kind;x['period_days']=days
        if kind=='LATEST_FISCAL_YEAR':x['period']['basis']='latest_fiscal_year'
    return result,refs,issues,scoped


def calculate(market,finance,at):
    # Reuse frozen Decimal arithmetic on a copy; expose the actual fiscal basis.
    aligned=deepcopy(finance)
    for x in aligned.values():
        if isinstance(x,dict) and x.get('period_classification')=='LATEST_FISCAL_YEAR':x['period']['basis']='TTM'
    values=valuation.calculate(market,aligned,at)
    by_id={x['id']:x for x in finance.values() if isinstance(x,dict) and x.get('id')}
    for x in values:
        fiscal=next((by_id[i] for i in x['input_refs'] if i in by_id and by_id[i].get('period_classification')),None)
        if fiscal:
            x['fiscal_period']=deepcopy(fiscal['period']);x['period_classification']=fiscal['period_classification'];x['period_days']=fiscal['period_days']
            if fiscal['period_classification']=='LATEST_FISCAL_YEAR':x['limitations'].append('최근 공시 회계연도 실적이며 정확한 달력 12개월 TTM으로 표시하지 않습니다.')
    return values


def audit(values,finance,components,issues):
    out=[]
    for x in values:
        reasons=[]
        if x['value'] is None:
            if x['metric_name']=='P/B':reasons=['ACCOUNTING_BASIS_AMBIGUOUS']
            elif x['metric_name'] in {'EV','EV/EBITDA'}:reasons=['ACCOUNTING_BASIS_AMBIGUOUS','CALCULATION_NOT_SUPPORTED']
            else:reasons=['SOURCE_NOT_ACQUIRED' if not any(k.startswith('ttm_') for k in finance) else 'SOURCE_PRESENT_NOT_EXTRACTED']
        out.append({'metric_name':x['metric_name'],'status':x['calculation_status'],'input_refs':x['input_refs'],
            'missing_inputs':x['missing_inputs'],'reason_categories':reasons,'source_issues':issues,
            'debt_policy':DEBT_POLICY if x['metric_name'] in {'EV','EV/EBITDA'} else None,
            'observed_components':[p['id'] for p in components] if x['metric_name'] in {'EV','EV/EBITDA'} else []})
    return out
