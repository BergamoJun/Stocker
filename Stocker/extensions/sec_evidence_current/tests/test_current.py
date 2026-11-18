"""ILLUSTRATIVE — NOT LIVE DATA. Mock owner/reviewers are not human evidence."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from research.validation import digest
from extensions.sec_evidence_v1 import reviews as legacy_reviews, seal
from extensions.sec_evidence_v1 import bridge as legacy_bridge
from extensions.sec_evidence_v1_1 import semantic_validation
from extensions.sec_evidence_v1_1.tests import test_semantics as fixture
from extensions.sec_evidence_current import facade,bridge,report,Rejected,VERIFIER,boundary
from extensions.sec_evidence_current.owner import OwnerContext


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def owner_for(c,trust,label='owner'):
    path=c['root']/(label+'.json');write(path,trust)
    owner=OwnerContext.load(path,digest(path),illustrative=True,registry=c['root']/(label+'-registry'),clock=c['clock'])
    c['clock'].advance(2);return owner


def request_for(c,owner,*,window=None,effective='2026-11-09'):
    r=fixture.hardened(c,owner.trust(True),window=window,effective=effective)
    event=fixture.event(c,r)
    return facade.request_for(c['cik'],c['ticker'],c['clock'](),illustrative=True,event_input=event,
        source_store=c['source'],legacy_review_root=c['legacy'],review_store=c['reviews'],cache_root=c['cache'])


def ready(tmp_path,*,hedge=False,annual=False,equal_maturity=False):
    c,body=fixture.make_case(tmp_path,hedge=hedge,annual=annual,end='2028-11-10' if annual else '2027-11-10',
        extra_notes=['Loan TEST_LOAN matures on November 9, 2026.'] if equal_maturity else [])
    trust=fixture.pair(c,body);owner=owner_for(c,trust)
    request=request_for(c,owner,window={'start':'2027-01-01','end':'2028-01-01'} if annual else None,
                        effective='2027-01-01' if annual else '2026-11-09')
    return c,body,trust,owner,request


def withheld(decision):
    assert decision['amount'] is None and decision['status'] in {'WITHHELD','BLOCKED'}
    assert decision['calculation_status']!='ARITHMETIC_VERIFIED_CONDITIONAL' and decision['reason_codes']


def semantic_attack(tmp_path,kind):
    c,body=fixture.make_case(tmp_path)
    if kind in {'maturity','swap','other_instrument'}:
        def change(raw):
            if kind=='maturity':return raw.replace(b'The applicable interest rate resets on November 9, 2026.',b'The debt matures on November 9, 2026.')
            if kind=='swap':return raw.replace(b'Loan TEST_LOAN has no interest rate hedges during 2026-11-08 through 2027-11-10.',b'Interest rate swap notional: USD 500 million. Total debt: USD 1 billion.')
            return raw.replace(b'</body>',b'<p>Loan OTHER_LOAN (USD, SOFR): The applicable interest rate resets on November 9, 2026.</p></body>')
        fixture.appended(c,change)
        source=next(s for s in c['result']['sources'] if s['kind']=='document' and s['form']=='10-Q')
        obs=semantic_validation.observations(source,c['cik']);loan=next(o for o in obs if o.kind=='loan')
        for name in body['exposure']['bindings']:
            f=fixture.field(body,name);f['available_at']=source['available_at']
            if name=='principal':
                candidate=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='INLINE_XBRL' and loan.start<=p['locator']['start']<p['locator']['end']<=loan.end)
            else:
                expected='reset' if name=='reset_date' else 'no_hedge' if name in {'hedged_fraction','hedge_terms'} else 'loan'
                if kind=='other_instrument' and name=='reset_date':o=next(o for o in obs if o.kind=='reset' and o.attributes['instrument']=='OTHER_LOAN')
                else:o=next((o for o in obs if o.kind==expected and o.attributes['instrument']=='TEST_LOAN'),loan)
                candidate=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='NOTE' and p['locator']['start']<=o.start<o.end<=p['locator']['end'])
            fixture.rebind(c,body,name,candidate)
            if kind=='other_instrument' and name=='reset_date':
                # A discovery envelope may include adjacent valid TEST_LOAN text.
                # The attack must cite only the OTHER_LOAN observation, not a
                # broad span that genuinely supports both contracts' reset date.
                f=fixture.field(body,name);raw=Path(source['raw_path']).read_bytes().decode('utf-8')
                f.update(start_offset=o.start,end_offset=o.end,exact_quote=raw[o.start:o.end])
                body['witnesses'][name].update(raw_start=o.start,raw_end=o.end)
                assert 'Loan OTHER_LOAN' in f['exact_quote'] and 'Loan TEST_LOAN' not in f['exact_quote']
    if kind=='swap':fixture.field(body,'hedged_fraction')['value']='0.5';fixture.field(body,'hedge_terms')['value']='FIXED_OFFSET'
    if kind=='same_wrong_reviews':fixture.field(body,'repricing_fraction')['value']='0.5'
    if kind=='same_hash_wrong_claim':fixture.field(body,'reset_date')['value']='2026-12-09'
    # Existing v1 mock imports intentionally lack semantics; the official facade
    # must not turn these two owner-pinned, identical wrong claims into an amount.
    trust=fixture.pair(c,body,hardened=False);owner=owner_for(c,trust);request=request_for(c,owner)
    assert c['result']['is_illustrative']
    return c,body,trust,owner,request


@pytest.mark.parametrize('number',range(1,26),ids=lambda n:'BYPASS_%02d'%n)
def test_required_anti_bypass_cases(tmp_path,monkeypatch,number):
    kinds={10:'maturity',11:'swap',12:'same_wrong_reviews',15:'same_hash_wrong_claim',16:'other_instrument'}
    if number in kinds:
        c,body,trust,owner,request=semantic_attack(tmp_path,kinds[number])
        d=facade.process(request,owner=owner,created_at=c['clock']());withheld(d)
        assert d['status']=='WITHHELD' and any(code.startswith('SEMANTIC_') for code in d['reason_codes'])
        return
    c,body,trust,owner,request=ready(tmp_path)
    if number==1:
        request['verification_version']='sec-evidence-adapter-v1'
    elif number==2:request.pop('verification_version')
    elif number==3:request['verification_version']='sec-evidence-adapter-v99'
    elif number==4:
        calls=[]
        monkeypatch.setattr(legacy_bridge,'calculate',lambda *a,**k:calls.append(True))
        monkeypatch.setattr(facade.discovery,'build',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('injected verifier outage')))
        d=facade.process(request,owner=owner,created_at=c['clock']());withheld(d)
        assert not calls and d['reason_codes']==['VALIDATOR_EXECUTION_FAILED'];return
    elif number==5:
        request={'contract':'sec-evidence-shadow-bundle-v1','adapter':fixture.old.build(c,trust),'trust':trust}
    elif number in {6,7,8,17}:
        good=facade.authorize(request,owner=owner);r=good['result'];cert=good['certificate']
        if number==6:r=fixture.old.build(c,trust)
        elif number==7:
            r=deepcopy(r);r['contract']='sec-evidence-adapter-v1';r=seal({k:v for k,v in r.items() if k!='record_sha256'})
        elif number==8:cert=None
        else:
            request['calculation_window']['end']='2028-11-09';request['event_input']['context']['calculation_end']='2028-11-09'
        with pytest.raises(Rejected):bridge.calculate(request,r,cert,owner=owner,created_at=c['clock']())
        return
    elif number==9:
        c['clock'].advance(370*86400)
        request=request_for(c,owner,window={'start':'2027-11-15','end':'2028-11-15'})
    elif number==13:request['semantic_type']='INTEREST_RATE_RESET_DATE'
    elif number==14:request['relationship_verified']=True
    elif number==18:
        fixture.appended(c,lambda raw:raw.replace(b'November 9, 2026.',b'November 10, 2026.'))
        request=request_for(c,owner)
    elif number==19:owner=None
    elif number==20:
        modified=deepcopy(trust);modified['trusted_review_hashes']=[trust['trusted_review_hashes'][0]]*2
        owner=owner_for(c,modified,'duplicate-review');request=request_for(c,owner)
    elif number==21:
        # A changed reviewer ID does not authorize the new content hash. The
        # external owner pins only the original primary, not this forged clone.
        receipts=legacy_reviews.receipts(c['reviews'],c['clock']());a=next(r for r in receipts if r['manifest']['reviewer_id']=='TEST_PRIMARY_A')
        clone=deepcopy(a['manifest']);clone['reviewer_id']='TEST_PRIMARY_FORGED';clone=seal({k:v for k,v in clone.items() if k!='record_sha256'})
        c['clock'].advance(2);legacy_reviews.store(clone,c['result'],c['reviews'],clock=c['clock'])
        modified=deepcopy(trust);modified['trusted_review_hashes']=[a['record_sha256']];modified['independent_reviewer_ids']=['TEST_PRIMARY_A','TEST_PRIMARY_FORGED']
        owner=owner_for(c,modified,'changed-id');request=request_for(c,owner)
    elif number==22:
        path=c['reviews']/'receipts'/(trust['trusted_review_hashes'][0]+'.json')
        value=json.loads(path.read_text());value['manifest']['reviewer_id']='TAMPERED'
        path.write_text(json.dumps(value),encoding='utf-8')
    elif number==23:
        other=tmp_path/'other-contract';other.mkdir()
        newc,newbody,newtrust,newowner,newrequest=ready(other,hedge=True)
        newc['clock'].advance(30);request=request_for(newc,owner);c=newc
    elif number==24:
        owner={'embedded_report_allowlist':trust,'verified':True}
    elif number==25:
        oldrequest=deepcopy(request);c['clock'].advance(100)
        modified=deepcopy(trust);modified['available_at']='2020-01-01T00:00:00Z'
        owner=owner_for(c,modified,'backdated-policy');request=oldrequest
        assert owner.trust(True)['available_at']>request['as_of']
    d=facade.process(request,owner=owner,created_at=c['clock']());withheld(d)


@pytest.mark.parametrize('control,expected',[('reset','2500000'),('hedge','1250000'),('annual','2500000'),('equal_maturity','2500000')])
def test_four_positive_controls_through_official_facade(tmp_path,control,expected):
    c,body,trust,owner,request=ready(tmp_path,hedge=control=='hedge',annual=control=='annual',equal_maturity=control=='equal_maturity')
    before=deepcopy(request);d=facade.process(request,owner=owner,created_at=c['clock']())
    assert d['status']=='CALCULATED' and d['amount']==expected
    assert d['verification_version']==VERIFIER and d['authorization']['certificate']['verification_version']==VERIFIER
    assert request==before and d['bridge']['output']['evidence_status']=='ILLUSTRATIVE'
    assert all(v is None for v in d['bridge']['output']['release'].values())


def test_zero_is_not_missing_evidence(tmp_path):
    c,body,trust,owner,request=ready(tmp_path)
    from extensions.financial_impact_v1.tests.test_impact import change
    change(request['event_input'],'event_delta','0')
    d=facade.process(request,owner=owner,created_at=c['clock']());assert d['amount']=='0' and d['status']=='CALCULATED'
    d=facade.process(request,owner=None,created_at=c['clock']());assert d['amount'] is None


def test_report_requires_external_owner_and_replays_immutably(tmp_path,monkeypatch):
    c,body,trust,owner,request=ready(tmp_path);monkeypatch.setattr(report,'ROOT',tmp_path)
    folder=report.run(request,tmp_path/'reports/private/current',owner=owner,created_at=c['clock']())
    assert report.validate(folder,owner=owner)['amount']=='2500000'
    with pytest.raises(Rejected,match='EXTERNAL_OWNER'):report.validate(folder)
    assert 'ILLUSTRATIVE' in (folder/'REPORT.ko.md').read_text(encoding='utf-8')


def test_owner_first_seen_receipt_cannot_be_resealed_and_backdated(tmp_path):
    c,body,trust,owner,request=ready(tmp_path)
    value=json.loads(owner.receipt_path.read_text());value['first_available_at']='2020-01-01T00:00:00Z'
    value=seal({k:v for k,v in value.items() if k!='record_sha256'});owner.receipt_path.write_text(json.dumps(value),encoding='utf-8')
    d=facade.process(request,owner=owner,created_at=c['clock']());withheld(d)
    assert d['reason_codes']==['OWNER_FIRST_AVAILABILITY_CHANGED']


@pytest.mark.parametrize('source',["from extensions.sec_evidence_v1 import bridge\nbridge.calculate(None,None)",
    "from extensions import sec_evidence_v1 as old\nold.bridge.calculate(None,None)",
    "import importlib\nimportlib.import_module('extensions.'+'sec_evidence_v1.bridge')",
    "from extensions.financial_impact_v1.report import analyze\nanalyze({})"])
def test_ci_detects_new_unprotected_legacy_consumers(tmp_path,source):
    p=tmp_path/'new_service.py';p.write_text(source,encoding='utf-8')
    policy={'frozen_files':{},'official_edges':{},'entrypoints':{}}
    result=boundary.audit(tmp_path,policy)
    assert result['status']=='FAIL' and any(r['code']=='NEW_DIRECT_ENGINE_CONSUMER' for r in result['violations'])


def test_ci_accepts_official_import_and_classifies_test_only(tmp_path):
    (tmp_path/'service.py').write_text('from extensions.sec_evidence_current.facade import process\n',encoding='utf-8')
    tests=tmp_path/'tests';tests.mkdir();(tests/'test_old.py').write_text('from extensions.sec_evidence_v1 import bridge\n',encoding='utf-8')
    r=boundary.audit(tmp_path,{'frozen_files':{},'official_edges':{},'entrypoints':{}})
    assert r['status']=='PASS' and r['findings'][0]['classification']=='INTERNAL_TEST_ONLY'


def test_accept_review_never_writes_legacy_user_registry(tmp_path):
    c,body,trust,owner,request=ready(tmp_path)
    with pytest.raises(Rejected,match='OWNED_CURRENT_REVIEW_STORE_REQUIRED'):
        facade.accept_review(request,fixture.old.manifest(c,'TEST_EXTRA',body),c['reviews'],owner=owner)


def test_official_review_import_is_append_only_and_not_owner_approval(tmp_path,monkeypatch):
    c,body,trust,owner,request=ready(tmp_path)
    monkeypatch.setattr(facade,'ROOT',tmp_path)
    before={p.name:digest(p) for p in (c['reviews']/'receipts').glob('*.json')};policy=digest(owner.policy_path)
    target=tmp_path/'reports/private/sec_current_reviews/new'
    receipt=facade.accept_review(request,fixture.old.manifest(c,'TEST_EXTRA',body),target,owner=owner,clock=c['clock'])
    assert (target/'receipts'/(receipt['record_sha256']+'.json')).is_file()
    assert receipt['manifest']['reviewer_kind']=='ILLUSTRATIVE_TEST_REVIEW'
    assert digest(owner.policy_path)==policy and receipt['record_sha256'] not in owner.trust(True)['trusted_review_hashes']
    assert before=={p.name:digest(p) for p in (c['reviews']/'receipts').glob('*.json')}


def test_certificate_version_cannot_be_resealed_to_legacy(tmp_path):
    c,body,trust,owner,request=ready(tmp_path);a=facade.authorize(request,owner=owner)
    cert=deepcopy(a['certificate']);cert['verification_version']='sec-evidence-adapter-v1'
    cert=seal({k:v for k,v in cert.items() if k!='record_sha256'})
    with pytest.raises(Rejected):bridge.calculate(request,a['result'],cert,owner=owner,created_at=c['clock']())
