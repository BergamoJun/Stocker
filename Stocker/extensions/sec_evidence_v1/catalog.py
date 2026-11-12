"""Reuse the existing SEC state/incarnation/review readers, offline by default."""
from pathlib import Path
from research import sec_fact_review,sec_facts
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import stable_hash
from . import read,parsing


def resolve(tickers):
    profiles=read(ROOT/'config/issuer_profiles.json')['profiles'];result={}
    for value in tickers:
        ticker=sec_facts.symbol(value);rows=[p for p in profiles if p['symbol']==ticker]
        if len(rows)!=1:raise ValueError('UNREGISTERED_OR_AMBIGUOUS_ISSUER')
        cik=sec_facts.cik(rows[0]['cik']);result.setdefault(cik,ticker)
    return [{'cik':cik,'ticker':ticker} for cik,ticker in result.items()]


def load(cik,ticker,as_of,*,source_store=None,legacy_review_root=None,cache_root=None,illustrative=False,max_filings=4,fresh=False):
    if not 1<=max_filings<=8:raise ValueError('FILING_BUDGET_INVALID')
    cik=sec_facts.cik(cik)
    state=sec_fact_review.view(cik,as_of,source_store=source_store,root=legacy_review_root,illustrative=illustrative)
    snapshot=state['snapshot'];sources=[];parsed={};legacy=[];warnings=[]
    stats={'parse_cache_hits':0,'parse_cache_misses':0,'parse_cache_capacity_skips':0,'documents':0,
        'filings_available':0,'filings_selected':0,'selected_document_bytes':0,'network_requests':0}
    cache=Path(cache_root or ROOT/'reports/private/sec_evidence_v1_cache'/('illustrative' if illustrative else 'real'))
    if not illustrative and not cache.resolve().is_relative_to((ROOT/'reports/private').resolve()):raise ValueError('PRIVATE_CACHE_REQUIRED')
    if snapshot is None:return {'state':state,'sources':[],'parsed':{},'reviewed':[],'warnings':['NO_SNAPSHOT_BEFORE_CUTOFF'],'statistics':stats}
    if snapshot['cik']!=sec_facts.cik(cik) or ticker not in snapshot['identity']['symbols']:raise ValueError('SEC_IDENTITY_MISMATCH')
    filings=[f for f in snapshot['filings'] if f['form'] in {'10-K','10-Q','10-K/A','10-Q/A'}]
    stats['filings_available']=len(filings)
    selected=[]
    def priority(f):
        completed=any(r['status']=='reviewed_source_fact' for r in state['reviews'].get(f['accession'],{}).values())
        return (completed,f['accession'] in state['bindings'],f['filed_date'],f['accession'])
    for f in sorted(filings,key=priority,reverse=True)[:max_filings]:
        b=state['bindings'].get(f['accession'])
        if b is None:
            warnings.append('PRIMARY_DOCUMENT_MISSING:'+f['accession'])
            b={k:f[k] for k in ['accession','form','report_date','filed_date','accepted_at','primary_document']}
            b.update(cik=snapshot['cik'],source_id=None,source_url=f.get('source_url'),raw_sha256=None,
                amendments=[v['accession'] for v in filings if '/A' in v['form'] and v['report_date']==f['report_date']],
                incarnation_id=stable_hash([snapshot['snapshot_id'],f['accession'],'metadata_only']),incarnation_available_at=snapshot['available_at'])
        selected.append(b)
    stats['filings_selected']=len(selected)
    if len(selected)<len(filings):warnings.append('FILING_CAP_PARTIAL_COVERAGE')
    for b in selected:
        filing=next(f for f in snapshot['filings'] if f['accession']==b['accession'])
        if b['accepted_at'] and not sec_fact_review.precedes(b['accepted_at'],as_of):
            warnings.append('FILING_PUBLICATION_NOT_BEFORE_CUTOFF:'+b['accession']);continue
        for sid,src in snapshot['sources'].items():
            if not (sid==b['source_id'] or src['kind']=='companyfacts'):continue
            if src['kind']=='document' and not src['source_url'].startswith('https://www.sec.gov/Archives/edgar/data/'+str(int(cik))+'/'+b['accession'].replace('-','')+'/'):raise ValueError('SEC_FILING_IDENTITY_URL_MISMATCH')
            if src['kind']=='companyfacts' and src['source_url']!='https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json':raise ValueError('COMPANYFACTS_IDENTITY_URL_MISMATCH')
            available=max([src['available_at'],b['incarnation_available_at']],key=as_utc)
            if not sec_fact_review.precedes(available,as_of):warnings.append('SOURCE_NOT_STRICTLY_BEFORE_CUTOFF');continue
            incarnation=b['incarnation_id'] if src['kind']=='document' else stable_hash([b['incarnation_id'],sid,snapshot['sequence']])
            key=stable_hash([sid,b['accession'],incarnation])
            source={'source_key':key,'source_id':sid,'cik':sec_facts.cik(cik),'ticker':ticker,'accession':b['accession'],
                'form':b['form'],'filed_date':b['filed_date'],'published_at':b['accepted_at'],
                'available_at':available,'raw_path':src['raw_path'],'source_url':src['source_url'],
                'original_sha256':src['raw_sha256'],'incarnation_id':incarnation,'binding':b,
                'kind':src['kind'],'derived_text_sha256':None,'transformation':None,'is_illustrative':illustrative,
                'amendment_pending':bool(b['amendments'] or '/A' in b['form'])}
            if src['kind']=='document':
                try:
                    item=parsing.cached(src,cik,cache,stats,fresh=fresh)
                    source.update(derived_text_sha256=item['derived_text_sha256'],transformation=item['transformation'])
                    parsed[key]=item;stats['documents']+=1;stats['selected_document_bytes']+=Path(src['raw_path']).stat().st_size
                except parsing.CacheIntegrityError:raise
                except (ValueError,UnicodeError) as error:
                    warnings.append('DOCUMENT_UNSUPPORTED:'+b['accession']+':'+type(error).__name__);continue
            sources.append(source)
        for metric,row in state['reviews'].get(b['accession'],{}).items():
            if row['status']=='reviewed_source_fact':
                chosen=row['chosen'];legacy.append({'accession':b['accession'],'metric':metric,'status':row['status'],
                    'fact':chosen['fact'],'scope':chosen['finding']['scope'],'receipt_id':chosen['receipt_id'],
                    'available_at':chosen['available_at'],'reviewer_kind':chosen['reviewer_kind'],
                    'independent_contract_exposure':False})
    return {'state':state,'sources':sources,'parsed':parsed,'reviewed':legacy,'warnings':warnings,'statistics':stats}
