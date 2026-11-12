"""Deterministic provenance checks; attestations never become model-made gold."""
from datetime import date
from hashlib import sha256
from urllib.parse import urlsplit
from research.rights import as_utc
from research.validation import stable_hash


def issue(code,status,detail):
    return {'code':code,'status':status,'detail':detail}


def fact_hash(fact):
    return stable_hash({k:v for k,v in fact.items() if k!='review'})


def check(packet):
    problems=[];sources={};facts={};ctx=packet['context'];cutoff=as_utc(ctx['as_of'])
    real=packet['mode']=='REAL'
    def add(code,status,detail):problems.append(issue(code,status,detail))
    for s in packet['sources']:
        sid=s['source_id']
        if sid in sources:add('DUPLICATE_SOURCE_ID','CONFLICTING_EVIDENCE',sid)
        sources[sid]=s
        if sha256(s['text'].encode('utf-8')).hexdigest()!=s['original_sha256']:
            add('SOURCE_HASH_CHANGED','CONFLICTING_EVIDENCE',sid)
        if s['is_illustrative']!= (not real):add('REAL_ILLUSTRATIVE_MIX','CONFLICTING_EVIDENCE',sid)
        url=urlsplit(s['url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            add('INVALID_SOURCE_URL','INSUFFICIENT_EVIDENCE',sid)
        if not s['rights']['local_calculation']:add('LOCAL_CALCULATION_RIGHTS_MISSING','INSUFFICIENT_EVIDENCE',sid)
        if s['published_at'] is None or s['available_at'] is None:
            add('SOURCE_TIME_PRECISION_MISSING','INSUFFICIENT_EVIDENCE',sid);continue
        pub,available=as_utc(s['published_at']),as_utc(s['available_at'])
        if pub>available:add('SOURCE_TIME_ORDER','CONFLICTING_EVIDENCE',sid)
        if pub>=cutoff or available>=cutoff:add('FUTURE_INFORMATION','INSUFFICIENT_EVIDENCE',sid)
        if (cutoff-pub).total_seconds()>packet['policy']['max_source_age_days']*86400:
            add('SOURCE_STALE','STALE_EVIDENCE',sid)
    by_kind={}
    for f in packet['facts']:
        fid=f['fact_id']
        if fid in facts:add('DUPLICATE_FACT_ID','CONFLICTING_EVIDENCE',fid)
        facts[fid]=f;s=sources.get(f['source_id'])
        if s is None:add('SOURCE_ID_NOT_FOUND','INSUFFICIENT_EVIDENCE',fid);continue
        if f['original_sha256']!=s['original_sha256']:add('FACT_SOURCE_HASH_MISMATCH','CONFLICTING_EVIDENCE',fid)
        start,end=f['start_offset'],f['end_offset']
        if not 0<=start<end<=len(s['text']) or s['text'][start:end]!=f['exact_quote']:
            add('EXACT_WITNESS_MISMATCH','CONFLICTING_EVIDENCE',fid)
        if f['value'] is None:add('FACT_VALUE_MISSING','INSUFFICIENT_EVIDENCE',fid)
        if f['verification_status']=='CONFLICTING':add('FACT_MARKED_CONFLICTING','CONFLICTING_EVIDENCE',fid)
        wanted='VERIFIED' if real else 'ILLUSTRATIVE'
        if f['verification_status']!=wanted:add('FACT_NOT_VERIFIED_FOR_MODE','INSUFFICIENT_EVIDENCE',fid)
        if f['available_at'] is None:add('FACT_AVAILABILITY_MISSING','INSUFFICIENT_EVIDENCE',fid)
        else:
            at=as_utc(f['available_at'])
            if at>=cutoff:add('FUTURE_FACT','INSUFFICIENT_EVIDENCE',fid)
            if s['available_at'] and at<as_utc(s['available_at']):add('FACT_BACKDATED','CONFLICTING_EVIDENCE',fid)
        event=f['kind'].startswith('event_')
        if event:
            if any(f[k] is not None for k in ['cik','ticker','instrument_id','currency','accounting_scope','reporting_period']) or s['issuer'] is not None:
                add('GLOBAL_EVENT_SCOPE_MISMATCH','CONFLICTING_EVIDENCE',fid)
        else:
            if f['cik']!=ctx['cik'] or f['ticker']!=ctx['ticker'] or s['issuer']!={'cik':ctx['cik'],'ticker':ctx['ticker']}:
                add('ISSUER_MISMATCH','CONFLICTING_EVIDENCE',fid)
            if f['accounting_scope']!=ctx['accounting_scope']:add('ACCOUNTING_SCOPE_MISMATCH','CONFLICTING_EVIDENCE',fid)
            if f['instrument_id']!=packet['exposure']['instrument_id']:add('INSTRUMENT_MISMATCH','CONFLICTING_EVIDENCE',fid)
            if f['currency']!=ctx['currency']:add('COMPONENT_CURRENCY_MISMATCH','INCOMPATIBLE_UNITS',fid)
            if f['reporting_period']!=ctx['reporting_period']:add('REPORTING_PERIOD_MISMATCH','INVALID_PERIOD',fid)
            if f['valid_from'] is None or f['valid_to'] is None:
                add('CONTRACT_WINDOW_MISSING','INSUFFICIENT_EVIDENCE',fid)
            elif not f['valid_from']<=ctx['calculation_start']<ctx['calculation_end']<=f['valid_to']:
                add('CONTRACT_WINDOW_MISMATCH','INVALID_PERIOD',fid)
        if real:
            r=f['review']
            if not r:add('EXTERNAL_REVIEW_MISSING','INSUFFICIENT_EVIDENCE',fid)
            else:
                digest=stable_hash({k:v for k,v in r.items() if k!='record_sha256'})
                if digest!=r['record_sha256'] or r['fact_sha256']!=fact_hash(f) or r['source_sha256']!=s['original_sha256']:
                    add('REVIEW_BINDING_CHANGED','CONFLICTING_EVIDENCE',fid)
                if r['record_sha256'] not in packet['policy']['trusted_review_hashes']:
                    add('REVIEW_NOT_OWNER_TRUSTED','INSUFFICIENT_EVIDENCE',fid)
                reviewed=as_utc(r['reviewed_at'])
                if reviewed>=cutoff or not f['available_at'] or reviewed>as_utc(f['available_at']) or s['available_at'] and reviewed<as_utc(s['available_at']):
                    add('REVIEW_TIME_INVALID','INSUFFICIENT_EVIDENCE',fid)
        key=(f['kind'],f['cik'],f['instrument_id'],stable_hash(f['reporting_period']))
        signature=stable_hash([f['value'],f['unit'],f['currency'],f['accounting_scope'],f['valid_from'],f['valid_to'],f['rate_link']])
        if key in by_kind and by_kind[key]!=signature:add('CONFLICTING_COMPONENT_FACTS','CONFLICTING_EVIDENCE',fid)
        by_kind[key]=signature
    return problems,facts
