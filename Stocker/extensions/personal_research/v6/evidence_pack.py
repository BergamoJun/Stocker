"""One source-bound index; licensed market data stays in local rendering only."""
from copy import deepcopy
from decimal import Decimal
import re
from research.rights import as_utc
from research.validation import digest,stable_hash

VERSION='memo-evidence-pack-v1'
INJECTION=re.compile(r'ignore (?:all |the )?(?:previous|prior)|system prompt|developer message|<\|(?:system|assistant)|override instructions|이전.*지시.*무시|비밀.*(?:출력|전송)',re.I)


def historical_references(value,snapshot):
    """Comparative annual rows as reported in an available filing, never backdated PIT."""
    from datetime import date
    from decimal import localcontext
    from . import coverage,valuation
    history=[]
    for f,sid,source,raw,parsed,dims in coverage.documents(snapshot,value['analysis_as_of']):
        if f['form']!='10-K':continue
        rows=[x for x in parsed if dims.get(x['context_ref'])==[]]
        periods=sorted({(x['start'],x['end']) for x in rows if x['tag']=='us-gaap:NetIncomeLoss' and x['start'] and x['end']<=f['report_date'] and 330<=(date.fromisoformat(x['end'])-date.fromisoformat(x['start'])).days<=370},reverse=True)[:3]
        for start,end in periods:
            ni=coverage.exact(rows,['us-gaap:NetIncomeLoss'],start,end)
            rev=coverage.exact(rows,['us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax'],start,end)
            alternatives=[x for x in rows if x['tag'] in valuation.TAGS['revenue'] and x['start']==start and x['end']==end and x['same_entity'] and x['unit']=='USD' and not x['hidden'] and x['status']=='parsed_numeric_evidence_only']
            if not ni or not rev or Decimal(rev['value'])<=0 or any(Decimal(x['value'])!=Decimal(rev['value']) for x in alternatives):continue
            refs=[]
            for x in [ni,rev]:
                ref,meta=coverage.reference(f,sid,source,x);value['source_references'][ref]=meta;refs.append(ref)
            with localcontext() as c:
                c.prec=60;margin=Decimal(ni['value'])/Decimal(rev['value'])
            history.append({'id':'history:'+f['accession']+':'+end,'value':str(margin),'unit':'ratio','scope':'parent_net_income/consolidated_revenue',
                'period':{'start':start,'end':end,'basis':'annual'},'period_days':(date.fromisoformat(end)-date.fromisoformat(start)).days+1,
                'source_refs':refs,'formula':'reported_parent_NI/reported_consolidated_revenue',
                'limitations':['Comparatives in the currently available filing; not original-vintage PIT and not a long-run average.']})
    return history


def sec_source(source,issuer,at):
    url=source.get('url','')
    expected='https://www.sec.gov/Archives/edgar/data/'+str(int(issuer))+'/'
    valid=url.startswith(expected) or url=='https://data.sec.gov/api/xbrl/companyfacts/CIK'+str(issuer).zfill(10)+'.json'
    return valid and bool(source.get('available_at')) and as_utc(source['available_at'])<=as_utc(at)


def build(value):
    issuer=(value.get('issuer') or {}).get('cik');at=value['analysis_as_of'];sources=value['source_references']
    entries=[];conflicts=[];missing=[];seen={};hashes={};finance=value.get('valuation_financials',{})
    nodes={x['id']:x for x in finance.values() if isinstance(x,dict) and x.get('id')}
    def leaves(item,visited=None):
        visited=set(visited or ());refs=[]
        for key in item.get('source_refs',[])+item.get('input_refs',[]):
            if key in sources:refs.append(key)
            elif key in nodes and key not in visited:refs+=leaves(nodes[key],visited|{key})
        return list(dict.fromkeys(refs))
    def add(category,metric,statement,amount,unit,period,kind,refs,original,extra=None,local=False):
        if amount is not None and (isinstance(amount,bool) or not Decimal(str(amount)).is_finite()):raise ValueError('EVIDENCE_NONFINITE_VALUE')
        if not local and amount is not None and not refs:raise ValueError('EVIDENCE_NUMERIC_SOURCE_REQUIRED')
        if amount is not None and category in {'financial','segment'} and unit!=('USD/shares' if metric=='diluted_eps' else 'USD'):raise ValueError('EVIDENCE_CURRENCY_UNSUPPORTED')
        if period and period.get('start') and period['start']>period['end']:raise ValueError('EVIDENCE_PERIOD_REVERSED')
        if any(ref not in sources for ref in refs):raise ValueError('EVIDENCE_SOURCE_ID_MISSING')
        for ref in refs:
            src=sources[ref]
            if as_utc(src['available_at'])>as_utc(at):raise ValueError('EVIDENCE_FUTURE_SOURCE')
            if src['raw_path'] not in hashes:
                if digest(src['raw_path'])!=src['raw_sha256']:raise ValueError('EVIDENCE_SOURCE_HASH_CHANGED')
                hashes[src['raw_path']]=src['raw_sha256']
        if category in {'financial','derived','segment','business','risk','history'} and refs and issuer and any(not sec_source(sources[r],issuer,at) for r in refs):
            raise ValueError('EVIDENCE_ISSUER_OR_SOURCE_SCOPE_MISMATCH')
        signature=stable_hash([category,metric,period,unit,(extra or {}).get('scope'),(extra or {}).get('role')])
        if amount is not None and signature in seen:
            if Decimal(seen[signature]['value'])!=Decimal(str(amount)):conflicts.append({'metric':metric,'entries':[seen[signature]['evidence_id'],original],'kind':'SOURCE_VALUE_CONFLICT'})
            else:return
        eid='E'+str(len(entries)+1).zfill(3)
        entry={'evidence_id':eid,'issuer':issuer,'ticker':value['ticker'],'category':category,'metric_name':metric,
            'statement':statement,'value':str(amount) if amount is not None else None,'unit':unit,'fiscal_period':deepcopy(period),
            'evidence_kind':kind,'source_refs':list(refs),'original_id':original,'source_as_of':at,
            'model_eligible':not local and bool(refs) and bool(issuer) and all(sec_source(sources[r],issuer,at) for r in refs),
            'limitations':['Source binding is not independent economic/semantic certification.'],**(extra or {})}
        entries.append(entry)
        if amount is not None:seen[signature]=entry
    for f in value['facts']:
        if f['value'] is None:missing.append({'metric':f['name'],'period':f['period'],'reason':f['limitations']});continue
        add('financial',f['name'],f['name'],f['value'],f['unit'],f['period'],f['evidence_kind'],f['source_refs'],f['id'],{'scope':f['scope'],'role':f['role']})
    for f in value['derived_metrics']:
        if f['value'] is not None:add('derived',f['name'],f['name'],f['value'],f['unit'],f['period'],'DERIVED',f['source_refs'],f['id'],{'scope':f['scope']})
    for name,f in finance.items():
        if isinstance(f,dict) and (name.startswith('ttm_') or name=='common_equity'):
            add('financial',name,name,f['value'],f['unit'],f['period'],f.get('evidence_kind','DERIVED'),leaves(f),f['id'],
                {'scope':f['scope'],'period_classification':f.get('period_classification'),'period_days':f.get('period_days')})
    for group in value.get('business_segments',[]):
        for f in group['segments']:
            add('segment',f['segment_name'],f['segment_name'],f['segment_revenue'],'USD',f['segment_reporting_period'],'OBSERVED',f['source_refs'],f['id'],
                {'segment_growth_rate':f['segment_growth_rate'],'operating_margin':f['segment_profit_margin'],'reconciliation':group['reconciliation_status']})
    risk_ids=set(value.get('company_risks',[]))
    for f in value.get('business_profile',[]):
        add('risk' if f['id'] in risk_ids else 'business',f['topic'],f['statement'],None,None,None,'SOURCE_FACT',f['source_refs'],f['id'],
            {'source_text':f['source_text'],'section':f['section'],'scope':'company_statement_not_independent_fact'})
    for f in value.get('memo_historical_evidence',[]):
        add('history','reported_annual_net_margin','reported_annual_net_margin',f['value'],f['unit'],f['period'],'DERIVED',f['source_refs'],f['id'],
            {'scope':f['scope'],'period_days':f['period_days'],'reference_kind':'CURRENT_FILING_COMPARATIVE_NOT_PIT'})
    for f in value.get('valuation_metrics',[]):
        add('valuation',f['metric_name'],f['formula'],f['value'],f['unit'],f['fiscal_period'],'DERIVED',[],f['metric_name'],local=True)
    pack={'version':VERSION,'ticker':value['ticker'],'issuer':issuer,'analysis_as_of':at,'entries':entries,'missing':missing,'conflicts':conflicts,
        'availability':'NOT_APPLICABLE' if value['market_snapshot'].get('security_type')=='ETF' else 'PARTIAL' if entries else 'INSUFFICIENT',
        'source_reference_location':'RESULT.source_references','local_only':{'market_snapshot':'RESULT.market_snapshot','relative_valuation':'RESULT.relative_valuation',
            'scenario_assumptions':'RESULT.scenario_assumptions','scenario_results':'RESULT.scenario_results','data_freshness':'RESULT.market_data_status',
            'known_limitations':value.get('analysis_limitations',[])},
        'data_freshness':{'price_date':value.get('price_date'),'market_status':value['market_data_status'],'source_as_of':at},
        'model_policy':'SEC public target-issuer evidence only; all market, vendor-derived, peer and full private report data excluded.'}
    pack['sha256']=stable_hash(pack)
    return pack


def model_view(pack,sources,review):
    """Explicit whitelist, not a redacted private report or a caller-set rights flag."""
    safe=[];blocked=[]
    financial=[e for e in pack['entries'] if e['category']=='financial' and e['metric_name'].startswith('ttm_')]
    if not financial:financial=[e for e in pack['entries'] if e['category']=='financial' and e.get('role')=='current']
    priority=['ttm_revenue','ttm_parent_net_income','ttm_operating_cash_flow','ttm_capital_expenditure','ttm_fcf']
    selected=sorted(financial,key=lambda e:priority.index(e['metric_name']) if e['metric_name'] in priority else 99)[:5]
    selected+=sorted([e for e in pack['entries'] if e['category']=='history'],key=lambda e:e['fiscal_period']['end'],reverse=True)[:2]
    segments=sorted([e for e in pack['entries'] if e['category']=='segment'],key=lambda e:(e['fiscal_period']['end'],Decimal(e['value'])),reverse=True)
    selected+=segments[:1]
    for category in ['business','risk']:
        choices=[e for e in pack['entries'] if e['category']==category and e['metric_name'] in {'cloud','memory','power','energy','investment','cycle','supply'}]
        selected+=choices[:2]
    for e in selected:
        if any(c['metric']==e['metric_name'] for c in pack['conflicts']):
            blocked.append({'evidence_id':e['evidence_id'],'reason':'CONFLICTING_METRIC_QUARANTINED'});continue
        if e['category'] not in {'financial','derived','segment','business','risk','history'} or not e['source_refs'] or not pack['issuer'] or any(not sec_source(sources[r],pack['issuer'],pack['analysis_as_of']) for r in e['source_refs']):
            blocked.append({'evidence_id':e['evidence_id'],'reason':'TRANSFER_SCOPE_NOT_CONFIRMED'});continue
        if INJECTION.search(e.get('source_text','')):blocked.append({'evidence_id':e['evidence_id'],'reason':'SOURCE_INSTRUCTION_QUARANTINED'});continue
        item={k:e[k] for k in ['evidence_id','category','metric_name','statement','value','unit','fiscal_period','evidence_kind']}
        item['anchor']=e['metric_name'] if e['value'] is not None else e.get('source_text','')[:300]
        if e['value'] is None:item['source_text']=e.get('source_text','')[:300]
        if e.get('period_days'):item['period_days']=e['period_days']
        item['source_urls']=list(dict.fromkeys(sources[r]['url'] for r in e['source_refs']))[:1]
        if e['category']=='segment':item['segment_growth_rate']=e.get('segment_growth_rate')
        safe.append(item)
    view={'ticker':pack['ticker'],'issuer':pack['issuer'],'evidence':safe,
        'scenario_context':[{'scenario_id':x['scenario_id'],'coherence':x['coherence'],'guidance':'UNKNOWN',
            'description':'Research growth/margin assumptions require future confirmation; no market multiples or equity values were supplied.'} for x in review['scenarios']],
        'limitations':['Market/price/valuation/peer/scenario-equity values are not supplied. Do not judge price levels.',
            'Company statements are not independent competitive proof. No guidance or long-run margin average is confirmed.'],
        'period_context':'53-week latest fiscal year, not an exact12-month TTM.' if any(e.get('period_days')==371 for e in selected) else 'Read each evidence period separately; do not combine durations.'}
    while len(str(view).encode('utf-8'))>7800 and len(view['evidence'])>4:view['evidence'].pop()
    return view,{'policy':'SEC_ONLY_NO_VENDOR_INPUT','included_evidence_ids':[e['evidence_id'] for e in view['evidence']],
        'excluded_categories':['market','valuation','relative','peer','scenario_equity','exit_multiple','news_metadata'],
        'quarantined':blocked,'bytes':len(str(view).encode('utf-8')),'external_calls':0,'contains_private_paths':False}
