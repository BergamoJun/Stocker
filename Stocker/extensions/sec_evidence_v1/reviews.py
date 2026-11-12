"""Append-only contract reviews; two owner-trusted independent humans required."""
from copy import deepcopy
from pathlib import Path
from decimal import Decimal
from datetime import date
from research import sec_fact_review,sec_filing_evidence
from research.local_files import exclusive_lock
from research.sec_transport import now
from research.rights import as_utc
from research.validation import digest,stable_hash
from extensions.financial_impact_v1 import evidence as impact_evidence
from extensions.financial_impact_v1.calculations import number,DAY_COUNTS
from extensions.financial_impact_v1.models import BINDINGS
from . import read,write_once,seal,verify_seal,methods,contract


def empty_trust(illustrative=False):
    return {'contract':'sec-evidence-trust-v1','is_illustrative':illustrative,'trusted_review_hashes':[],
        'independent_reviewer_ids':[],'trusted_event_review_hashes':[],'owner_id':None,'available_at':None}


def load_trust(path=None,expected_sha256=None,*,illustrative=False,registry=None,clock=now):
    if path is None:return empty_trust(illustrative)
    if not expected_sha256 or digest(path)!=expected_sha256:raise ValueError('OWNER_TRUST_POLICY_PIN_REQUIRED')
    value=read(path);contract.validate(value,'trust')
    if value['is_illustrative']!=illustrative:raise ValueError('TRUST_NAMESPACE_MISMATCH')
    if value['trusted_review_hashes'] and (not value['owner_id'] or not value['available_at']):raise ValueError('OWNER_TRUST_ATTESTATION_MISSING')
    if value['trusted_review_hashes'] or value['trusted_event_review_hashes']:
        from research.sec_report import ROOT
        root=Path(registry or ROOT/'reports/private/sec_contract_trust'/('illustrative' if illustrative else 'real'))
        if not illustrative and not root.resolve().is_relative_to((ROOT/'reports/private').resolve()):raise ValueError('PRIVATE_TRUST_REGISTRY_REQUIRED')
        target=root/(expected_sha256+'.json')
        if not target.exists():write_once(target,seal({'owner_policy_sha256':expected_sha256,'policy':value,'first_available_at':clock()}))
        receipt=read(target);verify_seal(receipt)
        if receipt['policy']!=value or receipt['owner_policy_sha256']!=expected_sha256:raise ValueError('TRUST_FIRST_AVAILABILITY_CHANGED')
        value={**value,'available_at':max([value['available_at'] or receipt['first_available_at'],receipt['first_available_at']],key=as_utc)}
    return value


def template(binding,*,illustrative=False):
    return seal({'contract':'sec-contract-review-manifest-v1','is_illustrative':illustrative,
        'reviewer_id':None,'reviewer_kind':'ILLUSTRATIVE_TEST_REVIEW' if illustrative else 'human_original_filing_review',
        'reviewed_at':None,'decision':'UNRESOLVED','complete':False,'binding':deepcopy(binding),'terms':None,
        'rationale':'원문·같은 계약·기간·단위·헤지와 재설정 조건을 독립 검토한 뒤 작성한다. 빈 양식은 정답이 아니다.',
        'methods':methods()})


def check_terms(manifest,result):
    """Validate exact source binding; prose economics still requires real review."""
    from . import discovery
    terms=manifest['terms'];contract.validate(terms,'terms');x=terms['exposure']
    if terms['rate_type']!='FLOATING':raise ValueError('FLOATING_CONTRACT_NOT_ESTABLISHED')
    if x['verification_status']!='UNVERIFIED':raise ValueError('REVIEW_INPUT_CANNOT_SELF_PROMOTE')
    if (x['cik'],x['ticker'])!=(result['identity']['cik'],result['identity']['ticker']):raise ValueError('CONTRACT_ISSUER_MISMATCH')
    if x['instrument_id'].upper().startswith(('UNKNOWN','MISSING')):raise ValueError('INSTRUMENT_ID_MISSING')
    if len(terms['facts'])!=len(BINDINGS) or len({f['fact_id'] for f in terms['facts']})!=len(BINDINGS):raise ValueError('COMPONENT_FACT_SET_INVALID')
    sources={s['source_key']:s for s in result['sources']};candidates={c['candidate_id']:c for c in result['candidates']}
    values={};raw_cache={};native_cache={}
    for name,kind in BINDINGS.items():
        f=next((f for f in terms['facts'] if f['fact_id']==x['bindings'][name]),None)
        w=terms['witnesses'][name];candidate=candidates.get(w['candidate_id'])
        if f is None or f['kind']!=kind or candidate is None:raise ValueError('COMPONENT_WITNESS_MISSING')
        if f['verification_status']!='UNVERIFIED' or f['review'] is not None:raise ValueError('COMPONENT_CANNOT_SELF_VERIFY')
        source=sources.get(candidate['source_key'])
        if source is None or source['kind']!='document' or source['binding']!=manifest['binding']:raise ValueError('CONTRACT_SOURCE_BINDING_MISMATCH')
        b=manifest['binding']
        if (source['source_id'],source['source_url'],source['original_sha256'],source['incarnation_id'])!=(b['source_id'],b['source_url'],b['raw_sha256'],b['incarnation_id']):raise ValueError('SOURCE_DESCRIPTOR_CHANGED')
        if source['source_key']!=stable_hash([b['source_id'],b['accession'],b['incarnation_id']]):raise ValueError('SOURCE_KEY_CHANGED')
        if source['amendment_pending'] or candidate['status'] in {'STALE','UNSUPPORTED'}:raise ValueError('STALE_AMENDED_OR_UNSUPPORTED_COMPONENT')
        expected_cid=stable_hash([source['source_key'],candidate['origin'],candidate['metric'],candidate['locator'],methods()])
        if candidate['candidate_id']!=expected_cid:raise ValueError('CANDIDATE_ID_CHANGED')
        if f['source_id']!=source['source_key'] or f['original_sha256']!=source['original_sha256']:raise ValueError('COMPONENT_SOURCE_HASH_MISMATCH')
        if (f['cik'],f['ticker'],f['instrument_id'],f['accounting_scope'],f['reporting_period'])!=(x['cik'],x['ticker'],x['instrument_id'],x['accounting_scope'],x['reporting_period']):raise ValueError('CONTRACT_SCOPE_OR_PERIOD_MISMATCH')
        if f['currency']!=x['currency']:raise ValueError('CONTRACT_CURRENCY_MISMATCH')
        if f['valid_from'] is None or f['valid_to'] is None or f['valid_from']>=f['valid_to']:raise ValueError('CONTRACT_EFFECTIVE_PERIOD_MISSING')
        if f['available_at']!=source['available_at']:raise ValueError('REVIEW_FACT_AVAILABILITY_CHANGED')
        key=source['source_key']
        if key not in raw_cache:
            path=Path(source['raw_path'])
            if digest(path)!=source['original_sha256']:raise ValueError('REVIEW_RAW_SOURCE_CHANGED')
            raw_cache[key]=path.read_bytes().decode('utf-8')
        text=raw_cache[key];start,end=w['raw_start'],w['raw_end'];loc=candidate['locator']
        if not (loc['start'] is not None and loc['end'] is not None and loc['start']<=start<end<=loc['end']<=len(text)):
            raise ValueError('COMPONENT_OUTSIDE_CANDIDATE_WITNESS')
        if (f['start_offset'],f['end_offset'],f['exact_quote'])!=(start,end,text[start:end]):raise ValueError('COMPONENT_EXACT_QUOTE_CHANGED')
        if name=='principal':
            if not discovery.numeric_principal_eligible(candidate):raise ValueError('FORBIDDEN_PRINCIPAL_INFERENCE')
            if key not in native_cache:native_cache[key]=sec_filing_evidence.document({'kind':'document','source_url':source['source_url'],'raw_path':source['raw_path'],'raw_sha256':source['original_sha256']},x['cik'])
            native=next((v for v in native_cache[key] if v['ordinal']==loc['ordinal']),None)
            if native is None or any(candidate['parser_fact'].get(k)!=v for k,v in native.items()):raise ValueError('NUMERIC_SOURCE_FACT_CHANGED')
            if f['unit']!='CURRENCY' or native['unit']!=x['currency'] or Decimal(f['value'])!=Decimal(native['value']):raise ValueError('PRINCIPAL_VALUE_OR_UNIT_MISMATCH')
            if x['reporting_period']!={'start':None,'end':native['end'],'basis':'INSTANT'}:raise ValueError('PRINCIPAL_INSTANT_PERIOD_MISMATCH')
            if native['end']!=b['report_date'] or native['end']>as_utc(result['as_of']).date().isoformat():raise ValueError('PRINCIPAL_FILING_PERIOD_MISMATCH')
        values[name]=f['value']
        units={'principal':'CURRENCY','interest_bearing':'boolean','repricing_fraction':'fraction','rate_mapping':'fraction',
            'hedged_fraction':'fraction','reset_date':'date','day_count':'convention','hedge_terms':'contract','principal_schedule':'contract','accounting_treatment':'contract'}
        if f['unit']!=units[name]:raise ValueError('COMPONENT_UNIT_INVALID')
    for name in ['principal','repricing_fraction','rate_mapping','hedged_fraction']:
        n=number(values[name])
        if n<0 or name!='principal' and n>1:raise ValueError('INVALID_CONTRACT_DECIMAL')
    if values['interest_bearing'] is not True or values['day_count'] not in DAY_COUNTS:raise ValueError('INTEREST_OR_DAY_COUNT_NOT_ESTABLISHED')
    if values['principal_schedule']!='CONSTANT_CONTRACTUAL' or values['accounting_treatment']!='EXPENSED_PRETAX':raise ValueError('UNSUPPORTED_CONTRACT_ACCOUNTING_OR_SCHEDULE')
    h=number(values['hedged_fraction'])
    if values['hedge_terms']!=('NONE' if h==0 else 'FIXED_OFFSET'):raise ValueError('HEDGE_RELATIONSHIP_UNVERIFIED')
    mapping=next(f for f in terms['facts'] if f['kind']=='contract_rate_mapping')
    if mapping['rate_link'] is None:raise ValueError('CONTRACT_RATE_INDEX_MISSING')
    if number(mapping['rate_link']['minimum_delta_bp'])>number(mapping['rate_link']['maximum_delta_bp']):raise ValueError('CONTRACT_RATE_RANGE_INVALID')
    date.fromisoformat(values['reset_date'])
    return terms


def receipts(root,as_of):
    result=[]
    for path in (Path(root)/'receipts').glob('*.json'):
        r=read(path);contract.validate(r,'receipt');verify_seal(r);verify_seal(r['manifest'])
        if path.stem!=r['record_sha256']:raise ValueError('CONTRACT_RECEIPT_ID_CHANGED')
        m=r['manifest']
        if not m['complete'] or not m['reviewer_id'] or not m['reviewed_at'] or not as_utc(m['binding']['incarnation_available_at'])<=as_utc(m['reviewed_at'])<=as_utc(r['available_at']):raise ValueError('CONTRACT_RECEIPT_TIME_OR_COMPLETENESS_INVALID')
        if sec_fact_review.precedes(r['available_at'],as_of):result.append(r)
    if len({r['store_order'] for r in result})!=len(result):raise ValueError('CONTRACT_RECEIPT_ORDER_CONFLICT')
    return sorted(result,key=lambda r:r['store_order'])


def store(manifest,result,root,*,clock=now):
    contract.validate(manifest,'manifest');verify_seal(manifest)
    if not manifest['complete'] or not manifest['reviewer_id'] or not manifest['reviewed_at']:raise ValueError('DRAFT_REVIEW_NOT_IMPORTABLE')
    if manifest['methods']!=methods() or manifest['is_illustrative']!=result['is_illustrative']:raise ValueError('REVIEW_METHOD_OR_NAMESPACE_CHANGED')
    current=sec_fact_review.state(result['identity']['cik'],clock(),source_store=result['source_store'],illustrative=result['is_illustrative'])
    if current['bindings'].get(manifest['binding']['accession'])!=manifest['binding']:raise ValueError('REVIEW_INCARNATION_CHANGED')
    at=clock()
    if not as_utc(manifest['binding']['incarnation_available_at'])<=as_utc(manifest['reviewed_at'])<=as_utc(at):raise ValueError('REVIEW_TIME_ORDER_INVALID')
    if manifest['decision']=='APPROVE':check_terms(manifest,result)
    path=Path(root)
    from research.sec_report import ROOT
    if not result['is_illustrative'] and not path.resolve().is_relative_to((ROOT/'reports/private').resolve()):raise ValueError('PRIVATE_REVIEW_STORE_REQUIRED')
    with exclusive_lock(path/'write.lock'):
        existing=receipts(path,'9999-12-31T23:59:59Z')
        same=next((r for r in existing if r['manifest']['record_sha256']==manifest['record_sha256']),None)
        if same:return same
        if existing and as_utc(at)<max(as_utc(r['available_at']) for r in existing):raise ValueError('REVIEW_CLOCK_MOVED_BACKWARDS')
        value=seal({'contract':'sec-contract-review-receipt-v1','manifest':manifest,'available_at':at,'store_order':len(existing)+1})
        write_once(path/'receipts'/(value['record_sha256']+'.json'),value);return value


def promote(result,root,trust):
    contract.validate(trust,'trust')
    if trust['is_illustrative']!=result['is_illustrative']:raise ValueError('TRUST_NAMESPACE_MISMATCH')
    if trust['trusted_review_hashes'] and not trust['owner_id']:raise ValueError('OWNER_TRUST_ATTESTATION_MISSING')
    stats={'contract_review_receipts':0,'untrusted_or_nonindependent_reviews':0,'stale_contract_reviews':0}
    latest={};conflicts=[];approved=[]
    current=sec_fact_review.state(result['identity']['cik'],result['as_of'],source_store=result['source_store'],illustrative=result['is_illustrative'])
    snapshot=current['snapshot'] or {'filings':[]}
    relevant=[f for f in snapshot['filings'] if f['form'] in {'10-K','10-Q','10-K/A','10-Q/A'} and
        f['filed_date']<=as_utc(result['as_of']).date().isoformat() and (not f['accepted_at'] or sec_fact_review.precedes(f['accepted_at'],result['as_of']))]
    latest_filed=max((f['filed_date'] for f in relevant),default=None)
    for r in receipts(root,result['as_of']):
        m=r['manifest'];stats['contract_review_receipts']+=1
        if m['binding']['cik']!=result['identity']['cik']:continue
        if m['is_illustrative']!=result['is_illustrative'] or m['methods']!=methods() or current['bindings'].get(m['binding']['accession'])!=m['binding']:
            stats['stale_contract_reviews']+=1;continue
        if latest_filed and m['binding']['filed_date']<latest_filed:
            stats['stale_contract_reviews']+=1;continue
        instrument=m['terms']['exposure']['instrument_id'] if m['terms'] else None
        key=(m['binding']['accession'],instrument,m['reviewer_id']);latest[key]=r
    groups={}
    for r in latest.values():
        m=r['manifest'];t=m['terms'];instrument=t['exposure']['instrument_id'] if t else 'UNRESOLVED'
        groups.setdefault((m['binding']['accession'],instrument),[]).append(r)
    for key,rows in groups.items():
        accepted=[];signatures=set();invalid=[]
        if key[1]!='UNRESOLVED' and any(k[0]==key[0] and k[1] is None for k in latest):
            invalid.append('UNRESOLVED_FILING_LEVEL_REVIEW')
        for r in rows:
            m=r['manifest'];kind='ILLUSTRATIVE_TEST_REVIEW' if result['is_illustrative'] else 'human_original_filing_review'
            if r['record_sha256'] not in trust['trusted_review_hashes'] or m['reviewer_id'] not in trust['independent_reviewer_ids'] or m['reviewer_kind']!=kind or not trust['available_at'] or not sec_fact_review.precedes(trust['available_at'],result['as_of']):
                stats['untrusted_or_nonindependent_reviews']+=1;continue
            if m['decision']!='APPROVE' or not m['complete']:invalid.append('UNRESOLVED_OR_REJECTED_REVIEW');continue
            try:check_terms(m,result)
            except (ValueError,ArithmeticError,TypeError,KeyError) as error:invalid.append(str(error));continue
            signatures.add(stable_hash(m['terms']));accepted.append(r)
        if invalid or len(signatures)>1:
            conflicts.append({'code':'CONTRACT_REVIEW_CONFLICT','candidate_ids':[],
                'resolution':'Resolve source/terms disagreements independently; no automatic winner. '+','.join(invalid)});continue
        if len({r['manifest']['reviewer_id'] for r in accepted})<2:continue
        selected=accepted[0];terms=deepcopy(selected['manifest']['terms']);available=max([trust['available_at']]+[r['available_at'] for r in accepted],key=as_utc)
        exposure=terms['exposure'];exposure['verification_status']='ILLUSTRATIVE' if result['is_illustrative'] else 'VERIFIED'
        for f in terms['facts']:
            f['verification_status']=exposure['verification_status'];f['available_at']=available
            # A derived binding certificate, explicitly NOT a human reviewer.
            # Original human receipts remain separate and owner-pinned above.
            f['review']=None if result['is_illustrative'] else seal({'reviewer_id':'SEC_ADAPTER_BINDING_NOT_HUMAN','reviewed_at':available,
                'source_sha256':f['original_sha256'],'fact_sha256':impact_evidence.fact_hash(f)})
        approved.append({'instrument_id':key[1],'exposure':exposure,'facts':terms['facts'],'witnesses':terms['witnesses'],
            'review_receipt_hashes':[r['record_sha256'] for r in accepted],'is_illustrative':result['is_illustrative'],'status':'VERIFIED'})
    by_principal={}
    for v in approved:by_principal.setdefault(v['witnesses']['principal']['candidate_id'],[]).append(v['instrument_id'])
    duplicated={cid for cid,ids in by_principal.items() if len(set(ids))>1}
    if duplicated:
        conflicts.append({'code':'DUPLICATE_PRINCIPAL_SOURCE_ACROSS_INSTRUMENTS','candidate_ids':sorted(duplicated),
            'resolution':'One numeric principal witness cannot be counted as separate instruments; independently resolve the contract partition.'})
        approved=[v for v in approved if v['witnesses']['principal']['candidate_id'] not in duplicated]
    return approved,conflicts,stats
