"""Tag/text matches are candidates only. No financial meaning is inferred."""
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from research import sec_facts
from research.rights import as_utc
from research.validation import stable_hash,digest
from extensions.financial_impact_v1.models import BINDINGS
from . import seal,methods,catalog,contract

PATTERNS=[
    ('swap_notional_context',r'(?:swap|hedg).{0,40}notional|notional.{0,40}(?:swap|hedg)'),
    ('interest_rate_hedges',r'interest.?rate.{0,30}(?:swap|hedg)|(?:swap|hedg).{0,30}interest.?rate'),
    ('floating_rate_borrowings',r'(?:floating|variable).{0,35}(?:rate|debt|borrow)|(?:debt|borrow).{0,35}(?:floating|variable)'),
    ('fixed_rate_borrowings',r'fixed.{0,20}(?:rate|debt|borrow)'),
    ('debt_maturity',r'maturit|debt.{0,25}due(?:date|after|within)'),
    ('reset_provisions',r'repric|reset|re.?set'),
    ('reference_rate_index',r'\bSOFR\b|\bLIBOR\b|\bEURIBOR\b|reference.?rate'),
    ('interest_expense_context',r'interest.?expense'),
    ('contractual_interest_rate',r'interest.?rate|stated.?percentage'),
    ('applicable_principal',r'debt.?instrument.?carrying|principal.?amount'),
    ('interest_bearing_borrowings',r'borrow|credit.?facility|interest.?bearing|notes.?payable'),
    ('general_debt',r'long.?term.?debt|total.?debt|liabilit'),
    ('contract_effective_dates',r'(?:credit|loan|debt|agreement).{0,40}effective|effective.{0,40}(?:credit|loan|agreement)'),
]
PROHIBITED_PRINCIPAL=re.compile(r'liabilit|total.?debt|^longtermdebt(?:current|noncurrent)?$|interest.?expense|maturit|notional',re.I)


def metric(text):
    return next((name for name,pattern in PATTERNS if re.search(pattern,text,re.I|re.S)),None)


def numeric_principal_eligible(candidate):
    tag=(candidate['tag'] or '').split(':')[-1]
    f=candidate['parser_fact'] or {}
    return (candidate['origin']=='INLINE_XBRL' and not PROHIBITED_PRINCIPAL.search(tag)
        and candidate['metric'] not in {'fixed_rate_borrowings','debt_maturity','interest_expense_context','swap_notional_context','general_debt'}
        and f.get('same_entity') is True and f.get('status')=='parsed_numeric_evidence_only'
        and f.get('is_instant') is True and not f.get('hidden') and f.get('unit')=='USD'
        and candidate['exact_quote'] is not None)


def make(source,kind,category,locator,as_of,max_age_days,*,method_map=None,**extra):
    cid=stable_hash([source['source_key'],kind,category,locator,method_map or methods()])
    stale=(as_utc(as_of).date()-date.fromisoformat(source['filed_date'])).days>max_age_days
    flags=['SEMANTIC_CONTRACT_REVIEW_REQUIRED']
    if source['amendment_pending']:flags.append('AMENDMENT_OR_CHANGED_CONTRACT_REVIEW_REQUIRED')
    if stale:flags.append('STALE_FILING')
    candidate={'candidate_id':cid,'source_key':source['source_key'],'cik':source['cik'],'ticker':source['ticker'],
        'accession':source['accession'],'metric':category,'origin':kind,'taxonomy':'TEXT','tag':None,'value':None,
        'unit':None,'currency':None,'reporting_basis':None,'accounting_scope':None,'effective_period':None,
        'contractual_period':None,'instrument_id':None,'interest_rate_index':None,'repricing_conditions':None,'hedge_relationship':None,
        'locator':locator,'exact_quote':None,'derived_quote':None,'derived_offsets':None,'parser_fact':None,
        'available_at':source['available_at'],'reviewed_at':None,'as_of':as_of,'status':'STALE' if stale else 'REVIEW_REQUIRED',
        'source_bound':True,'promotion_eligible':False,'flags':flags,**extra}
    candidate['promotion_eligible']=not stale and not source['amendment_pending'] and numeric_principal_eligible(candidate)
    if kind=='INLINE_XBRL' and (not candidate['parser_fact']['same_entity'] or candidate['parser_fact']['hidden'] or candidate['parser_fact']['status']!='parsed_numeric_evidence_only'):
        candidate['status']='UNSUPPORTED';candidate['flags'].append('FOREIGN_HIDDEN_OR_UNSUPPORTED_NUMERIC_FACT')
    contract.validate(candidate,'candidate');return candidate


def discover(data,as_of,*,max_age_days=730,maximum_candidates=512):
    if not 1<=max_age_days<=3650 or not 1<=maximum_candidates<=2048:raise ValueError('DISCOVERY_BUDGET_INVALID')
    result=[];seen=set();truncated=0;method_map=methods();per_source=Counter();per_group=Counter()
    source_cap=max(1,maximum_candidates//max(1,len(data['sources'])))
    def append(row):
        nonlocal truncated
        if row['candidate_id'] in seen:return
        seen.add(row['candidate_id'])
        group=(row['source_key'],row['origin'],row['metric'])
        group_cap=max(1,source_cap//(len(PATTERNS)+1)) if row['origin']=='NOTE' else source_cap
        inline_count=sum(v for k,v in per_group.items() if k[:2]==(row['source_key'],'INLINE_XBRL'))
        inline_ok=row['origin']!='INLINE_XBRL' or inline_count<max(1,source_cap//2)
        if len(result)<maximum_candidates and per_source[row['source_key']]<source_cap and per_group[group]<group_cap and inline_ok:
            result.append(row);per_source[row['source_key']]+=1;per_group[group]+=1
        else:truncated+=1
    cf_cache={}
    for source in data['sources']:
        key=source['source_key'];raw=Path(source['raw_path'])
        if digest(raw)!=source['original_sha256']:raise ValueError('SOURCE_CHANGED_DURING_DISCOVERY')
        if source['kind']=='companyfacts':
            sha=source['original_sha256']
            if sha not in cf_cache:cf_cache[sha]=sec_facts.loads(raw.read_bytes())
            content=cf_cache[sha]
            if sec_facts.cik(content['cik'])!=source['cik']:raise ValueError('COMPANYFACTS_CIK_MISMATCH')
            for taxonomy,concepts in content.get('facts',{}).items():
                for tag,definition in concepts.items():
                    category=metric(tag)
                    if not category:continue
                    for unit,rows in definition.get('units',{}).items():
                        for index,row in enumerate(rows):
                            if row.get('accn')!=source['accession'] or row.get('filed','9999')>as_utc(as_of).date().isoformat():continue
                            value=Decimal(str(row['val']))
                            if not value.is_finite():continue
                            pointer=['facts',taxonomy,tag,'units',unit,index]
                            append(make(source,'COMPANYFACTS',category,{'kind':'json_path','start':None,'end':None,'ordinal':None,'json_path':pointer},as_of,max_age_days,method_map=method_map,
                                taxonomy='STANDARD' if taxonomy in {'us-gaap','ifrs-full','dei','srt'} else 'CUSTOM',tag=taxonomy+':'+tag,
                                value=format(value,'f'),unit=unit,currency=unit if re.fullmatch('[A-Z]{3}',unit) else None,
                                reporting_basis='Duration' if row.get('start') else 'Instant',
                                effective_period={'start':row.get('start'),'end':row.get('end'),'basis':'DURATION' if row.get('start') else 'INSTANT'}))
        else:
            parsed=data['parsed'][key];raw_text=raw.read_bytes().decode('utf-8');spans={p['ordinal']:p for p in parsed['ix_spans']}
            for f in parsed['facts']:
                category=metric(f['tag'] or '')
                if not category:continue
                span=spans.get(f['ordinal']);quote=None
                if span and 0<=span['start']<span['end']<=len(raw_text) and span['end']-span['start']<=4000:quote=raw_text[span['start']:span['end']]
                details={**f,'dimension_members':parsed['dimensions'].get(f['context_ref'],[])}
                append(make(source,'INLINE_XBRL',category,{'kind':'inline_xbrl','start':span['start'] if span else None,'end':span['end'] if span else None,'ordinal':f['ordinal'],'json_path':None},as_of,max_age_days,method_map=method_map,
                    taxonomy='STANDARD' if (f['tag'] or '').split(':')[0] in {'us-gaap','ifrs-full','dei','srt'} else 'CUSTOM',
                    tag=f['tag'],value=f['value'],unit=f['unit'],currency='USD' if f['unit']=='USD' else None,
                    reporting_basis='Instant' if f['is_instant'] else 'Duration',effective_period={'start':f['start'],'end':f['end'],'basis':'INSTANT' if f['is_instant'] else 'DURATION'},
                    parser_fact=details,exact_quote=quote))
            # Notes remain review candidates regardless of nearby rates, debt or swap words.
            for category,pattern in PATTERNS:
                for match in list(re.finditer(pattern,parsed['text'],re.I|re.S))[:48]:
                    start=max(0,match.start()-140);end=min(len(parsed['text']),match.end()+220)
                    pieces=[p for p in parsed['segments'] if p['end']>start and p['start']<end]
                    if not pieces:continue
                    raw_start,raw_end=pieces[0]['raw_start'],pieces[-1]['raw_end'];quote=raw_text[raw_start:raw_end]
                    if len(quote)>4000:quote=None
                    append(make(source,'NOTE',category,{'kind':'raw_character_span','start':raw_start,'end':raw_end,'ordinal':None,'json_path':None},as_of,max_age_days,method_map=method_map,
                        exact_quote=quote,derived_quote=parsed['text'][start:end],derived_offsets={'start':start,'end':end}))
    data['statistics']['candidate_cap_omitted']=truncated
    return result


def build(cik,ticker,as_of,*,source_store=None,legacy_review_root=None,review_store=None,cache_root=None,
          illustrative=False,trust=None,max_age_days=730,maximum_candidates=512,max_filings=4,fresh=False):
    from research.sec_report import ROOT
    from . import reviews
    data=catalog.load(cik,ticker,as_of,source_store=source_store,legacy_review_root=legacy_review_root,cache_root=cache_root,
        illustrative=illustrative,max_filings=max_filings,fresh=fresh)
    candidates=discover(data,as_of,max_age_days=max_age_days,maximum_candidates=maximum_candidates)
    root=Path(review_store or ROOT/'reports/private/sec_contract_reviews'/('illustrative' if illustrative else 'real'))
    result={'contract':'sec-evidence-adapter-v1','identity':{'cik':sec_facts.cik(cik),'ticker':ticker},'as_of':as_of,
        'is_illustrative':illustrative,'status':'REVIEW_REQUIRED' if candidates else 'UNKNOWN','methods':methods(),
        'sources':data['sources'],'candidates':candidates,'reviewed_financial_facts':data['reviewed'],
        'verified_exposures':[],'missing_evidence':[],'conflicts':[],'statistics':data['statistics'],
        'limitations':data['warnings']+['Candidate words/tags are not verified contract meaning. No SEC prose is sent to an LLM.',
            'Legacy assistant/numeric reviews do not independently verify contractual exposure.'],
        'source_store':data['state']['source_store'],'review_store':str(root.resolve())}
    approved,conflicts,review_stats=reviews.promote(result,root,trust or reviews.empty_trust(illustrative))
    result['verified_exposures']=approved;result['conflicts']=conflicts;result['statistics'].update(review_stats)
    if conflicts:result['status']='CONFLICTED'
    elif approved:result['status']='VERIFIED'
    links=sorted({s['source_url'] for s in result['sources'] if s['kind']=='document'}|{
        f['source_url'] for f in (data['state']['snapshot'] or {}).get('filings',[]) if f.get('source_url') and f['form'] in {'10-K','10-Q','10-K/A','10-Q/A'}})
    if not approved:
        result['missing_evidence']=[{'field':k,'reason':'No complete, independently reviewed, current instrument binding for '+kind,'source_links':links} for k,kind in BINDINGS.items()]
    contract.validate(seal(result),'result');return seal(result)
