"""ILLUSTRATIVE NOT LIVE DATA. Synthetic reviews are NOT actual human labels."""
from copy import deepcopy
from pathlib import Path
from hashlib import sha256
import json
import pytest
from research import sec_review_demo,sec_refresh_demo,sec_refresh,sec_fact_review
from research.sec_transport import Client,Unavailable
from research.local_files import atomic_json
from research.validation import digest,stable_hash
from extensions.sec_evidence_v1 import parsing,discovery,reviews,bridge,report,acquisition,seal,read,contract
from extensions.financial_impact_v1 import contract as impact_contract
from extensions.financial_impact_v1.examples import interest_example
from extensions.financial_impact_v1.models import BINDINGS


@pytest.fixture
def case(tmp_path):
    routes,watch=sec_review_demo.fixture();watch['entities']=watch['entities'][:1]
    for url,value in list(routes.items()):
        if isinstance(value,bytes) and '/edgar/data/1001/' in url and b'ix:nonFraction' in value:
            loan=b'''<table><tr><td>Floating rate borrowings</td><td><ix:nonFraction name="us-gaap:DebtInstrumentCarryingAmount" contextRef="instant" unitRef="usd">1000000000</ix:nonFraction></td></tr>
<tr><td>Custom floating facility</td><td><ix:nonFraction name="demo:FloatingRateBorrowings" contextRef="instant" unitRef="usd">400000000</ix:nonFraction></td></tr>
<tr><td>Fixed rate debt</td><td><ix:nonFraction name="us-gaap:LongTermDebtFixedRate" contextRef="instant" unitRef="usd">5000</ix:nonFraction></td></tr></table>
<p>ILLUSTRATIVE loan TEST_LOAN is floating interest-bearing borrowing. SOFR interest rate reset is November 9, 2026. Contract effective dates cover 2026-11-08 through 2027-11-10. ACT/365F-v1. No hedge, no amortization, expensed pretax; one-for-one ILLUSTRATIVE_USD_POLICY_RATE transmission within -100bp and +100bp.</p>'''
            routes[url]=value.replace(b'</body>',loan+b'</body>')
    clock=sec_refresh_demo.Clock();opener=sec_refresh_demo.Opener(routes)
    client=Client('Stocker fixture analyst@fixture.invalid',root=tmp_path/'http',raw_root=tmp_path/'raw',illustrative=True,opener=opener,clock=clock,sleep=clock.advance)
    source=tmp_path/'source';sec_refresh.refresh(watch,client=client,store=source,clock=clock);clock.advance(2)
    c={'root':tmp_path,'clock':clock,'client':client,'opener':opener,'source':source,'legacy':tmp_path/'legacy',
        'reviews':tmp_path/'contracts','cache':tmp_path/'cache','ticker':'DEMOA','cik':'0000001001'}
    c['result']=build(c);return c


def build(c,trust=None,**options):
    return discovery.build(c['cik'],c['ticker'],c['clock'](),source_store=c['source'],legacy_review_root=c['legacy'],
        review_store=c['reviews'],cache_root=c['cache'],illustrative=True,trust=trust,**options)


def terms(c,result=None):
    r=result or c['result'];source=next(s for s in r['sources'] if s['kind']=='document' and s['form']=='10-Q')
    principal=next(p for p in r['candidates'] if p['source_key']==source['source_key'] and p['tag']=='us-gaap:DebtInstrumentCarryingAmount')
    note=next(p for p in r['candidates'] if p['source_key']==source['source_key'] and p['origin']=='NOTE' and p['metric']=='floating_rate_borrowings')
    period={'start':None,'end':'2026-09-30','basis':'INSTANT'};scope='ILLUSTRATIVE_CONSOLIDATED'
    x={'exposure_id':'TEST_EXPOSURE','instrument_id':'TEST_LOAN','cik':c['cik'],'ticker':c['ticker'],'currency':'USD',
        'accounting_scope':scope,'reporting_period':period,'verification_status':'UNVERIFIED','bindings':{k:'test_'+v for k,v in BINDINGS.items()}}
    values={'interest_bearing':(True,'boolean'),'principal':('1000000000','CURRENCY'),'repricing_fraction':('1','fraction'),
        'rate_mapping':('1','fraction'),'reset_date':('2026-11-09','date'),'day_count':('ACT/365F-v1','convention'),
        'hedged_fraction':('0','fraction'),'hedge_terms':('NONE','contract'),'principal_schedule':('CONSTANT_CONTRACTUAL','contract'),
        'accounting_treatment':('EXPENSED_PRETAX','contract')}
    fs=[];ws={};raw=Path(source['raw_path']).read_bytes().decode()
    for name,(value,unit) in values.items():
        candidate=principal if name=='principal' else note;loc=candidate['locator'];start,end=loc['start'],loc['end']
        assert end-start<=4000
        ws[name]={'candidate_id':candidate['candidate_id'],'raw_start':start,'raw_end':end}
        fs.append({'fact_id':x['bindings'][name],'source_id':source['source_key'],'original_sha256':source['original_sha256'],
            'kind':BINDINGS[name],'value':value,'unit':unit,'currency':'USD','cik':c['cik'],'ticker':c['ticker'],'instrument_id':x['instrument_id'],
            'accounting_scope':scope,'reporting_period':deepcopy(period),'valid_from':'2026-11-08','valid_to':'2027-11-10',
            'available_at':source['available_at'],'exact_quote':raw[start:end],'start_offset':start,'end_offset':end,
            'verification_status':'UNVERIFIED','review':None,'event_reference':None,'rate_currency':None,
            'rate_link':{'benchmark':'SOFR','event_variable':'policy_rate','event_reference':'ILLUSTRATIVE_USD_POLICY_RATE','rate_currency':'USD','minimum_delta_bp':'-100','maximum_delta_bp':'100'} if name=='rate_mapping' else None})
    return source['binding'],{'rate_type':'FLOATING','exposure':x,'facts':fs,'witnesses':ws}


def manifest(c,who,body=None):
    assert c['result']['is_illustrative'] is True
    binding,value=terms(c);m=reviews.template(binding,illustrative=True)
    m.update(reviewer_id=who,reviewed_at=c['clock'](),decision='APPROVE',complete=True,terms=deepcopy(body or value),rationale='ILLUSTRATIVE TEST REVIEW ONLY; not an actual person or independent market evidence.')
    return seal({k:v for k,v in m.items() if k!='record_sha256'})


def approved(c):
    rows=[]
    for who in ['TEST_PRIMARY_A','TEST_PRIMARY_B']:
        c['clock'].advance(2);rows.append(reviews.store(manifest(c,who),c['result'],c['reviews'],clock=c['clock']))
    trust=reviews.empty_trust(True);trust.update(owner_id='TEST_OWNER',available_at=c['clock'](),
        trusted_review_hashes=[r['record_sha256'] for r in rows],independent_reviewer_ids=['TEST_PRIMARY_A','TEST_PRIMARY_B'])
    c['clock'].advance(2);return build(c,trust),trust


def event_input(c,r):
    p=interest_example();p['sources']=[s for s in p['sources'] if s['issuer'] is None];p['facts']=[f for f in p['facts'] if f['kind'].startswith('event_')]
    p['context'].update(cik=c['cik'],ticker=c['ticker'],as_of=r['as_of'],reporting_period={'start':None,'end':'2026-09-30','basis':'INSTANT'},
        accounting_scope='ILLUSTRATIVE_CONSOLIDATED',calculation_start='2026-11-09',calculation_end='2027-11-09')
    p['policy']['max_source_age_days']=730;return p


def test_reuses_sec_store_and_separates_raw_and_derived_hashes(case):
    r=case['result'];assert r['identity']=={'cik':case['cik'],'ticker':case['ticker']}
    assert not r['verified_exposures'] and r['status']=='REVIEW_REQUIRED'
    source=next(s for s in r['sources'] if s['kind']=='document')
    assert source['original_sha256']!=source['derived_text_sha256'] and source['transformation']==parsing.TRANSFORM
    assert r['statistics']['network_requests']==0


def test_standard_and_custom_xbrl_have_exact_numeric_locators(case):
    candidates=case['result']['candidates']
    assert any(c['taxonomy']=='STANDARD' and c['tag']=='us-gaap:DebtInstrumentCarryingAmount' for c in candidates)
    custom=next(c for c in candidates if c['tag']=='demo:FloatingRateBorrowings')
    assert custom['taxonomy']=='CUSTOM' and custom['value']=='400000000' and custom['locator']['kind']=='inline_xbrl'
    assert custom['status']=='REVIEW_REQUIRED'


def test_note_quotes_and_raw_span_replay(case):
    r=case['result'];c=next(c for c in r['candidates'] if c['origin']=='NOTE');s=next(s for s in r['sources'] if s['source_key']==c['source_key'])
    raw=Path(s['raw_path']).read_bytes().decode();assert raw[c['locator']['start']:c['locator']['end']]==c['exact_quote']
    parsed=parsing.parse(raw.encode(),s['original_sha256'],case['cik'])
    assert parsed['text'][c['derived_offsets']['start']:c['derived_offsets']['end']]==c['derived_quote']


def test_wrong_cik_cannot_load_known_issuer(case):
    with pytest.raises(ValueError,match='IDENTITY'):discovery.build(case['cik'],'OTHER',case['clock'](),source_store=case['source'],illustrative=True,cache_root=case['cache'])


def test_changed_raw_source_is_integrity_failure(case):
    s=next(s for s in case['result']['sources'] if s['kind']=='document');Path(s['raw_path']).write_bytes(b'CHANGED')
    with pytest.raises(ValueError,match='INTEGRITY|CHANGED'):build(case)


def test_cache_reuse_and_cache_tampering(case):
    r=build(case);assert r['statistics']['parse_cache_hits']>=2
    target=next(case['cache'].glob('*.json'));value=read(target);value['parsed']['text']='tampered';atomic_json(target,value,replace=True)
    with pytest.raises(ValueError,match='CHANGED'):build(case)


def test_maturity_fixed_debt_expense_and_swap_are_not_principal():
    for tag in ['us-gaap:LongTermDebtCurrent','us-gaap:Liabilities','us-gaap:InterestExpense','x:SwapNotionalAmount','x:DebtMaturity']:
        c={'tag':tag,'origin':'INLINE_XBRL','metric':discovery.metric(tag),'parser_fact':{'same_entity':True,'status':'parsed_numeric_evidence_only','is_instant':True,'hidden':False,'unit':'USD'},'exact_quote':'x'}
        assert not discovery.numeric_principal_eligible(c)
    assert discovery.metric('FixedRateBorrowings')=='fixed_rate_borrowings'


def test_missing_reviews_and_spoofed_verified_flag_cannot_calculate(case):
    trust=reviews.empty_trust(True);r=case['result'];output=bridge.calculate(r,trust,created_at=case['clock']())
    assert output['output']['amount'] is None
    forged=deepcopy(r);forged['verified_exposures']=[{'forged':True}];forged=seal({k:v for k,v in forged.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='UNTRUSTED'):bridge.calculate(forged,trust,created_at=case['clock']())


def test_complete_pair_yields_unchanged_impact_arithmetic(case):
    r,trust=approved(case);assert len(r['verified_exposures'])==1
    result=bridge.calculate(r,trust,event_input=event_input(case,r),created_at=case['clock']())
    assert result['output']['amount']=='2500000' and result['output']['calculation_basis']=='SCENARIO_ONLY'
    impact_contract.validate(result['input']);assert all(v is None for v in result['output']['release'].values())


def test_verified_exposure_without_verified_event_is_withheld(case):
    r,trust=approved(case);value=bridge.calculate(r,trust,created_at=case['clock']())
    assert value['output']['amount'] is None and 'EVENT_MAGNITUDE_MISSING' in {i['code'] for i in value['output']['issues']}


def test_one_or_same_reviewer_is_not_independent_pair(case):
    case['clock'].advance(2);m=manifest(case,'TEST_PRIMARY_A');r=reviews.store(m,case['result'],case['reviews'],clock=case['clock'])
    again=reviews.store(m,case['result'],case['reviews'],clock=case['clock']);assert r==again
    trust=reviews.empty_trust(True);trust.update(owner_id='TEST_OWNER',available_at=case['clock'](),trusted_review_hashes=[r['record_sha256']],independent_reviewer_ids=['TEST_PRIMARY_A'])
    case['clock'].advance(2);assert not build(case,trust)['verified_exposures']


@pytest.mark.parametrize('change', ['issuer','scope','period','duration','index','hedge','effective_period','unit','principal_value','self_verified','currency'])
def test_invalid_contract_terms_cannot_be_imported(case,change):
    m=manifest(case,'TEST_PRIMARY_A');t=m['terms'];p=next(f for f in t['facts'] if f['kind']=='repricing_principal')
    if change=='issuer':t['exposure']['cik']='0000001002'
    elif change=='scope':p['accounting_scope']='PARENT_ONLY'
    elif change=='period':p['reporting_period']['end']='2025-12-31'
    elif change=='duration':t['exposure']['reporting_period']={'start':'2026-01-01','end':'2026-09-30','basis':'DURATION'}
    elif change=='index':next(f for f in t['facts'] if f['kind']=='contract_rate_mapping')['rate_link']=None
    elif change=='hedge':next(f for f in t['facts'] if f['kind']=='hedge_terms')['value']='UNKNOWN'
    elif change=='effective_period':p['valid_to']=None
    elif change=='unit':p['unit']='THOUSAND_CURRENCY'
    elif change=='principal_value':p['value']='999'
    elif change=='currency':
        t['exposure']['currency']='EUR'
        for f in t['facts']:f['currency']='EUR'
    else:p['verification_status']='VERIFIED'
    m=seal({k:v for k,v in m.items() if k!='record_sha256'})
    with pytest.raises((ValueError,ArithmeticError)):reviews.store(m,case['result'],case['reviews'],clock=case['clock'])


def test_disagreement_is_conflict_not_winner_selection(case):
    a=manifest(case,'TEST_PRIMARY_A');b=manifest(case,'TEST_PRIMARY_B');next(f for f in b['terms']['facts'] if f['kind']=='repricing_fraction')['value']='0.5';b=seal({k:v for k,v in b.items() if k!='record_sha256'})
    records=[]
    for m in [a,b]:records.append(reviews.store(m,case['result'],case['reviews'],clock=case['clock']))
    trust=reviews.empty_trust(True);trust.update(owner_id='TEST_OWNER',available_at=case['clock'](),trusted_review_hashes=[r['record_sha256'] for r in records],independent_reviewer_ids=['TEST_PRIMARY_A','TEST_PRIMARY_B'])
    case['clock'].advance(2);r=build(case,trust);assert r['status']=='CONFLICTED' and not r['verified_exposures']


def test_no_allowlist_and_equal_second_receipts_do_not_promote(case):
    r,trust=approved(case);trust['trusted_review_hashes']=[];assert not build(case,trust)['verified_exposures']
    trust['trusted_review_hashes']=r['verified_exposures'][0]['review_receipt_hashes'];trust['available_at']=case['clock']()
    assert not build(case,trust)['verified_exposures']


def test_amendment_changes_incarnation_and_invalidates_reviews(case):
    r,trust=approved(case);snapshot=sec_refresh.latest(case['source'],case['cik']);content={k:v for k,v in snapshot.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    filing=next(f for f in content['filings'] if f['form']=='10-Q');amend=deepcopy(filing)
    amend.update(accession='0000009999-26-999999',form='10-Q/A',document_source_ids=[],source_url=filing['source_url'].replace(filing['accession'].replace('-',''),'000000999926999999'))
    content['filings'].append(amend)
    case['clock'].advance(2);sec_refresh.commit(case['source'],case['cik'],content,clock=case['clock']);case['clock'].advance(2)
    after=build(case,trust);assert not after['verified_exposures'] and after['statistics']['stale_contract_reviews']==2


def test_stale_and_future_or_missing_source(case):
    assert any(c['status']=='STALE' for c in build(case,max_age_days=1)['candidates'])
    empty=discovery.build(case['cik'],case['ticker'],'2020-01-01T00:00:00Z',source_store=case['source'],legacy_review_root=case['legacy'],review_store=case['reviews'],cache_root=case['cache'],illustrative=True)
    assert empty['status']=='UNKNOWN' and not empty['candidates']


def test_bounded_transport_cache_and_failure_are_recorded(case):
    snap=sec_refresh.latest(case['source'],case['cik']);filing=deepcopy(next(f for f in snap['filings'] if f['form']=='10-Q'));filing['document_source_ids']=[]
    before=len(case['opener'].calls);r=acquisition.fetch_missing(case['cik'],[filing,filing],case['client'])
    assert r['get_calls']==1 and r['requests']==0 and r['rows'][0]['status']=='CACHE_HIT' and len(case['opener'].calls)==before
    filing['source_url']=filing['source_url'].replace('.htm','-missing.htm')
    r=acquisition.fetch_missing(case['cik'],[filing],case['client']);assert r['rows'][0]['status']=='FAILED' and r['requests']==1
    assert acquisition.fetch_missing(case['cik'],[filing],case['client'],maximum_requests=0)['rows'][0]['status']=='REQUEST_CAP'


def test_owner_policy_first_seen_prevents_backdating(case):
    policy=reviews.empty_trust(True);policy.update(owner_id='TEST_OWNER',trusted_event_review_hashes=['0'*64],available_at='2020-01-01T00:00:00Z')
    path=case['root']/'trust.json';atomic_json(path,policy)
    loaded=reviews.load_trust(path,digest(path),illustrative=True,registry=case['root']/'trust-registry',clock=case['clock'])
    assert loaded['available_at']==case['clock']()
    with pytest.raises(ValueError,match='PIN'):reviews.load_trust(path,None,illustrative=True)


def test_report_fresh_source_replay_and_tampering(case):
    folder=report.run(case['cik'],case['ticker'],case['clock'](),case['root']/'output',source_store=case['source'],legacy_review_root=case['legacy'],review_store=case['reviews'],cache_root=case['cache'],illustrative=True,created_at=case['clock']())
    assert report.validate(folder)['status']=='VALID'
    (folder/'REPORT.ko.md').write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='FILES_CHANGED'):report.validate(folder)


def test_drafts_are_not_reviewed_gold(case):
    q=report.queue(case['result']);assert q['actual_human_reviews_completed']==0 and q['no_model_input']
    with pytest.raises(ValueError,match='DRAFT'):reviews.store(q['draft_manifests'][0],case['result'],case['reviews'],clock=case['clock'])


def test_cik_dedup_and_no_industry_inference():
    from extensions.sec_evidence_v1.catalog import resolve
    assert len(resolve(['BE','BE','MSFT']))==2
    assert discovery.metric('industry is a technology company') is None


def test_companyfacts_pointer_links_original_numeric_row_without_promotion(case):
    r=case['result'];c=next(c for c in r['candidates'] if c['origin']=='COMPANYFACTS')
    s=next(s for s in r['sources'] if s['source_key']==c['source_key']);value=read(s['raw_path'])
    for key in c['locator']['json_path']:value=value[key]
    assert str(value['val'])==c['value'] and value['accn']==c['accession']
    assert not c['promotion_eligible']


def test_legacy_assistant_review_is_reused_but_not_an_independent_contract_review(case):
    filing=next(s for s in case['result']['sources'] if s['form']=='10-Q' and s['kind']=='document')
    worksheet=sec_fact_review.template(case['cik'],filing['accession'],source_store=case['source'],illustrative=True,cutoff=case['clock']())
    m=sec_review_demo.illustrative_manifest(worksheet,case['clock']());path=case['root']/'legacy_manifest.json';atomic_json(path,m)
    sec_fact_review.store(path,root=case['legacy'],source_store=case['source'],illustrative=True,clock=case['clock'])
    case['clock'].advance(2);r=build(case)
    assert len(r['reviewed_financial_facts'])==9 and not r['verified_exposures']
    assert all(f['reviewer_kind']=='assistant_original_filing_review' and not f['independent_contract_exposure'] for f in r['reviewed_financial_facts'])


def test_changed_terms_and_byte_reversion_never_restore_old_contract_reviews(case):
    r,trust=approved(case);source=next(s for s in r['sources'] if s['kind']=='document' and s['form']=='10-Q')
    snap=sec_refresh.latest(case['source'],case['cik']);base={k:v for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    original_sid=source['source_id'];original_binding=source['binding'];raw=Path(source['raw_path']).read_bytes()
    case['opener'].routes[source['source_url']]=raw.replace(b'November 9, 2026',b'November 10, 2026')
    changed=case['client'].get(source['source_url'],refresh=True);new_id=sec_refresh.source_id(changed)
    modified=deepcopy(base);modified['sources'][new_id]=sec_refresh.source(changed)
    next(f for f in modified['filings'] if f['accession']==source['accession'])['document_source_ids']=[new_id]
    sec_refresh.commit(case['source'],case['cik'],modified,clock=case['clock']);case['clock'].advance(2)
    assert not build(case,trust)['verified_exposures']
    reverted=deepcopy(modified);next(f for f in reverted['filings'] if f['accession']==source['accession'])['document_source_ids']=[original_sid]
    sec_refresh.commit(case['source'],case['cik'],reverted,clock=case['clock']);case['clock'].advance(2)
    after=build(case,trust);assert not after['verified_exposures']
    current=next(s for s in after['sources'] if s['kind']=='document' and s['accession']==source['accession'])
    assert current['original_sha256']==source['original_sha256'] and current['binding']['incarnation_id']!=original_binding['incarnation_id']


def test_different_rate_index_is_withheld_by_frozen_impact_validator(case):
    body=terms(case)[1];mapping=next(f for f in body['facts'] if f['kind']=='contract_rate_mapping')
    mapping['rate_link']['event_reference']='DIFFERENT_POLICY_RATE'
    receipts=[]
    for who in ['TEST_PRIMARY_A','TEST_PRIMARY_B']:
        case['clock'].advance(2);receipts.append(reviews.store(manifest(case,who,body),case['result'],case['reviews'],clock=case['clock']))
    trust=reviews.empty_trust(True);trust.update(owner_id='TEST_OWNER',available_at=case['clock'](),trusted_review_hashes=[r['record_sha256'] for r in receipts],independent_reviewer_ids=['TEST_PRIMARY_A','TEST_PRIMARY_B'])
    case['clock'].advance(2);r=build(case,trust);v=bridge.calculate(r,trust,event_input=event_input(case,r),created_at=case['clock']())
    assert v['output']['amount'] is None and any(i['code']=='RATE_LINK_CONFLICT' for i in v['output']['issues'])


def test_same_principal_cannot_be_duplicated_under_two_instrument_names(case):
    receipts=[]
    for instrument in ['TEST_LOAN','SECOND_NAME_SAME_PRINCIPAL']:
        body=terms(case)[1];body['exposure']['instrument_id']=instrument
        for f in body['facts']:f['instrument_id']=instrument
        for who in ['TEST_PRIMARY_A','TEST_PRIMARY_B']:
            case['clock'].advance(2);receipts.append(reviews.store(manifest(case,who,body),case['result'],case['reviews'],clock=case['clock']))
    trust=reviews.empty_trust(True);trust.update(owner_id='TEST_OWNER',available_at=case['clock'](),trusted_review_hashes=[r['record_sha256'] for r in receipts],independent_reviewer_ids=['TEST_PRIMARY_A','TEST_PRIMARY_B'])
    case['clock'].advance(2);r=build(case,trust)
    assert not r['verified_exposures'] and any(c['code']=='DUPLICATE_PRINCIPAL_SOURCE_ACROSS_INSTRUMENTS' for c in r['conflicts'])


def test_fixed_rate_numeric_candidate_cannot_be_called_floating_principal(case):
    m=manifest(case,'TEST_PRIMARY_A');source=next(s for s in case['result']['sources'] if s['kind']=='document' and s['form']=='10-Q')
    c=next(c for c in case['result']['candidates'] if c['source_key']==source['source_key'] and c['tag']=='us-gaap:LongTermDebtFixedRate')
    f=next(f for f in m['terms']['facts'] if f['kind']=='repricing_principal');loc=c['locator'];raw=Path(source['raw_path']).read_bytes().decode()
    f.update(value='5000',start_offset=loc['start'],end_offset=loc['end'],exact_quote=raw[loc['start']:loc['end']])
    m['terms']['witnesses']['principal']={'candidate_id':c['candidate_id'],'raw_start':loc['start'],'raw_end':loc['end']}
    m=seal({k:v for k,v in m.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='FORBIDDEN_PRINCIPAL'):reviews.store(m,case['result'],case['reviews'],clock=case['clock'])


def test_companyfacts_discovery_survives_missing_primary_document(case):
    snap=sec_refresh.latest(case['source'],case['cik']);content={k:v for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    for f in content['filings']:
        if f['form'] in {'10-K','10-Q'}:f['document_source_ids']=[]
    sec_refresh.commit(case['source'],case['cik'],content,clock=case['clock']);case['clock'].advance(2)
    r=build(case);assert r['candidates'] and all(c['origin']=='COMPANYFACTS' for c in r['candidates'])
    assert not r['verified_exposures'] and any('PRIMARY_DOCUMENT_MISSING' in warning for warning in r['limitations'])


def test_same_cutoff_second_sources_are_withheld(case):
    at=min(s['available_at'] for s in case['result']['sources'])
    r=discovery.build(case['cik'],case['ticker'],at,source_store=case['source'],legacy_review_root=case['legacy'],review_store=case['reviews'],cache_root=case['cache'],illustrative=True)
    assert all(sec_fact_review.precedes(s['available_at'],at) for s in r['sources']) and not r['verified_exposures']


def test_new_untrusted_withdrawal_cannot_revive_older_approval(case):
    r,trust=approved(case);assert r['verified_exposures']
    case['clock'].advance(2);m=manifest(case,'TEST_PRIMARY_A');m['decision']='UNRESOLVED';m=seal({k:v for k,v in m.items() if k!='record_sha256'})
    reviews.store(m,r,case['reviews'],clock=case['clock']);case['clock'].advance(2)
    assert not build(case,trust)['verified_exposures']


def test_real_replay_does_not_take_owner_allowlist_from_report_itself(case):
    folder=report.run(case['cik'],case['ticker'],case['clock'](),case['root']/'output',source_store=case['source'],legacy_review_root=case['legacy'],review_store=case['reviews'],cache_root=case['cache'],illustrative=True,created_at=case['clock']())
    b=read(folder/'ADAPTER.json');b['adapter']['is_illustrative']=False;b['adapter']=seal({k:v for k,v in b['adapter'].items() if k!='record_sha256'})
    b['trust']['trusted_review_hashes']=['0'*64];b=seal({k:v for k,v in b.items() if k!='record_sha256'})
    atomic_json(folder/'ADAPTER.json',b,replace=True)
    atomic_json(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir() if p.name!='FILES.json'},replace=True)
    with pytest.raises(ValueError,match='EXTERNAL_PINNED'):report.validate(folder)


def test_future_filing_publication_is_not_usable_even_if_bytes_exist(case):
    snap=sec_refresh.latest(case['source'],case['cik']);content={k:v for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    filing=next(f for f in content['filings'] if f['form']=='10-Q');accession=filing['accession'];filing['accepted_at']='2026-11-09T00:00:00Z'
    sec_refresh.commit(case['source'],case['cik'],content,clock=case['clock']);case['clock'].advance(2)
    r=build(case);assert not any(s['accession']==accession for s in r['sources'])
    assert any('PUBLICATION_NOT_BEFORE_CUTOFF' in warning for warning in r['limitations'])


def test_stored_filings_precede_many_newer_uncollected_rows_under_cap(case):
    snap=sec_refresh.latest(case['source'],case['cik']);content={k:v for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    base=next(f for f in content['filings'] if f['form']=='10-Q')
    for index in range(4):
        row=deepcopy(base);accession=f'0000009999-26-99999{index}'
        row.update(accession=accession,filed_date=f'2026-11-0{index+2}',accepted_at=f'2026-11-0{index+2}T20:00:00Z',document_source_ids=[],
            source_url=base['source_url'].replace(base['accession'].replace('-',''),accession.replace('-','')))
        content['filings'].append(row)
    sec_refresh.commit(case['source'],case['cik'],content,clock=case['clock']);case['clock'].advance(2)
    r=build(case,max_filings=4)
    assert len([s for s in r['sources'] if s['kind']=='document'])==2


def test_newer_financial_filing_requires_current_contract_review(case):
    r,trust=approved(case);snap=sec_refresh.latest(case['source'],case['cik']);content={k:v for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    base=next(f for f in content['filings'] if f['form']=='10-Q');row=deepcopy(base)
    row.update(accession='0000009999-26-999998',filed_date='2026-11-05',accepted_at='2026-11-05T20:00:00Z',document_source_ids=[],source_url=base['source_url'].replace(base['accession'].replace('-',''),'000000999926999998'))
    content['filings'].append(row);sec_refresh.commit(case['source'],case['cik'],content,clock=case['clock']);case['clock'].advance(2)
    after=build(case,trust);assert not after['verified_exposures'] and after['statistics']['stale_contract_reviews']==2
