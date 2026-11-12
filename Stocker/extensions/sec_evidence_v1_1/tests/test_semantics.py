"""ILLUSTRATIVE only. Mock reviewers are not independent human evidence."""
from copy import deepcopy
from pathlib import Path
from datetime import date
from datetime import datetime
from decimal import Decimal
import json
import pytest
import jsonschema
from research import sec_refresh
from research.validation import digest,stable_hash
from extensions.sec_evidence_v1 import reviews as old_reviews,seal
from extensions.sec_evidence_v1.tests import test_adapter as old
from extensions.sec_evidence_v1_1 import discovery,bridge,reviews,semantic_validation,report,contract


def make_case(tmp_path,*,hedge=False,annual=False,start='2026-11-08',end='2027-11-10',
              reset_date='November 9, 2026',hedge_start=None,hedge_end=None,extra_notes=()):
    c=old.case.__wrapped__(tmp_path)
    principal='<ix:nonFraction name="us-gaap:DebtInstrumentCarryingAmount" contextRef="instant" unitRef="usd">1000000000</ix:nonFraction>'
    loan=('Loan TEST_LOAN in USD has floating-rate principal USD '+principal+' indexed to SOFR; '
          'repricing fraction 1; policy reference ILLUSTRATIVE_USD_POLICY_RATE maps one-for-one to SOFR '
          'with coefficient 1; supported delta -100 to 100 bp; ACT/365F-v1; constant principal; interest is expensed pretax.')
    reset='Loan TEST_LOAN (USD, SOFR): The applicable interest rate resets on '+('January 1 of each year.' if annual else reset_date+'.')
    period=f'Loan TEST_LOAN is effective from {start} through {end}.'
    hs,he=hedge_start or start,hedge_end or end
    hedging=(f'Swap HEDGE_1 hedges Loan TEST_LOAN: receive-floating SOFR and pay-fixed; USD; notional USD 500000000; '
             f'hedged principal USD 500000000 of repricing principal USD 1000000000; coverage fraction 0.5; '
             f'fixed offset coefficient 1; effective from {hs} through {he}; no basis risk, caps, floors, or optionality.'
             if hedge else f'Loan TEST_LOAN has no interest rate hedges during {hs} through {he}.')
    appended(c,lambda raw:raw.replace(b'</body>',(''.join('<p>'+t+'</p>' for t in [loan,reset,period,hedging,*extra_notes])).encode()+b'</body>'))
    binding,body=old.terms(c);source=next(s for s in c['result']['sources'] if s['binding']==binding and s['kind']=='document')
    obs={o.kind:o for o in semantic_validation.observations(source,c['cik'])}
    principal=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='INLINE_XBRL'
                   and obs['loan'].start<=p['locator']['start']<p['locator']['end']<=obs['loan'].end)
    for name in body['exposure']['bindings']:
        f=field(body,name);f['valid_from']=start;f['valid_to']=end
        if name=='principal':candidate=principal
        else:
            kind='reset' if name=='reset_date' else ('hedge' if hedge else 'no_hedge') if name in {'hedged_fraction','hedge_terms'} else 'loan'
            o=obs[kind]
            candidate=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='NOTE'
                and p['locator']['start']<=o.start<o.end<=p['locator']['end'])
        rebind(c,body,name,candidate)
        if name in {'hedged_fraction','hedge_terms'}:f['valid_from']=hs;f['valid_to']=he
    if hedge:field(body,'hedged_fraction')['value']='0.5';field(body,'hedge_terms')['value']='FIXED_OFFSET'
    field(body,'reset_date')['value']='2027-01-01' if annual else datetime.strptime(reset_date,'%B %d, %Y').date().isoformat()
    return c,body


def field(body,name):return next(f for f in body['facts'] if f['fact_id']==body['exposure']['bindings'][name])


def rebind(c,body,name,p):
    s=next(s for s in c['result']['sources'] if s['source_key']==p['source_key']);a,b=p['locator']['start'],p['locator']['end']
    field(body,name).update(source_id=s['source_key'],original_sha256=s['original_sha256'],start_offset=a,end_offset=b,
        exact_quote=Path(s['raw_path']).read_bytes().decode('utf-8')[a:b])
    body['witnesses'][name]={'candidate_id':p['candidate_id'],'raw_start':a,'raw_end':b}


def appended(c,transform):
    s=next(s for s in c['result']['sources'] if s['kind']=='document' and s['form']=='10-Q')
    raw=Path(s['raw_path']).read_bytes();changed=transform(raw);assert changed!=raw
    c['opener'].routes[s['source_url']]=changed;response=c['client'].get(s['source_url'],refresh=True)
    snap=sec_refresh.latest(c['source'],c['cik'])
    content={k:deepcopy(v) for k,v in snap.items() if k not in {'snapshot_id','available_at','sequence','content_hash'}}
    sid=sec_refresh.source_id(response);content['sources'][sid]=sec_refresh.source(response)
    next(f for f in content['filings'] if f['accession']==s['accession'])['document_source_ids']=[sid]
    sec_refresh.commit(c['source'],c['cik'],content,clock=c['clock']);c['clock'].advance(2);c['result']=old.build(c)


def pair(c,body,*,other=None,hardened=True):
    rows=[]
    for index,who in enumerate(['TEST_PRIMARY_A','TEST_PRIMARY_B']):
        c['clock'].advance(2);m=old.manifest(c,who,other if index and other is not None else body)
        writer=reviews if hardened else old_reviews
        rows.append(writer.store(m,c['result'],c['reviews'],clock=c['clock']))
    t=old_reviews.empty_trust(True);t.update(owner_id='TEST_OWNER',available_at=c['clock'](),
        trusted_review_hashes=[r['record_sha256'] for r in rows],independent_reviewer_ids=['TEST_PRIMARY_A','TEST_PRIMARY_B'])
    c['clock'].advance(2);return t


def hardened(c,trust,*,window=None,effective='2026-11-09'):
    return discovery.build(c['cik'],c['ticker'],c['clock'](),source_store=c['source'],legacy_review_root=c['legacy'],
        review_store=c['reviews'],cache_root=c['cache'],illustrative=True,trust=trust,
        calculation_window=window or {'start':'2026-11-09','end':'2027-11-09'},event_effective=effective)


def event(c,r):
    p=old.event_input(c,r);p['context']['calculation_start']=r['eligibility_window']['start'];p['context']['calculation_end']=r['eligibility_window']['end']
    effective=r['configuration']['event_effective']
    if effective!='2026-01-01':
        from extensions.financial_impact_v1.tests.test_impact import change
        change(p,'event_effective_date',effective)
    return p


@pytest.mark.parametrize('hedge,annual,expected',[(False,False,'2500000'),(True,False,'1250000'),(False,True,'2500000')])
def test_explicit_positive_controls(tmp_path,hedge,annual,expected):
    c,body=make_case(tmp_path,hedge=hedge,annual=annual,end='2028-11-10' if annual else '2027-11-10')
    trust=pair(c,body);r=hardened(c,trust,window={'start':'2027-01-01','end':'2028-01-01'} if annual else None,
                               effective='2027-01-01' if annual else '2026-11-09')
    contract.validate(r);assert len(r['historical_evidence'])==len(r['verified_exposures'])==1
    assert r['semantic_assessments'][0]['semantic']['status']=='SEMANTIC_RULE_VALIDATED'
    out=bridge.calculate(r,trust,event_input=event(c,r),created_at=c['clock']())['output']
    assert out['amount']==expected and out['evidence_status']=='ILLUSTRATIVE'
    assert all(v is None for v in out['release'].values()) and out['semantic_market_accuracy']=='NOT_MEASURED'


@pytest.mark.parametrize('attack',['maturity','payment','reporting','other_instrument','false_reset_value','self_attested',
    'swap_only','wrong_currency','wrong_index','wrong_hedge_instrument','wrong_hedge_fraction','notional_not_covered',
    'wrong_direction','basis_risk','unsupported_offset','hidden_reset','ambiguous_reset','reuse_loan_fraction_as_hedge','same_hedge_instrument_id'])
def test_adversarial_semantics_with_two_identical_wrong_reviews(tmp_path,attack):
    c,body=make_case(tmp_path,hedge=attack in {'wrong_currency','wrong_index','wrong_hedge_instrument','wrong_hedge_fraction','notional_not_covered','wrong_direction','basis_risk','unsupported_offset','same_hedge_instrument_id'})
    if attack=='self_attested':
        m=old.manifest(c,'TEST_PRIMARY_A',body);m['terms']['relationship_verified']=True;m=seal({k:v for k,v in m.items() if k!='record_sha256'})
        with pytest.raises(jsonschema.ValidationError):reviews.store(m,c['result'],c['reviews'],clock=c['clock'])
        return
    if attack=='false_reset_value':field(body,'reset_date')['value']='2026-12-09'
    elif attack=='wrong_hedge_fraction':field(body,'hedged_fraction')['value']='0.25'
    elif attack=='reuse_loan_fraction_as_hedge':field(body,'hedged_fraction')['value']='1';field(body,'hedge_terms')['value']='FIXED_OFFSET'
    else:
        edits={
            'maturity':(b'The applicable interest rate resets on November 9, 2026.',b'The debt matures on November 9, 2026.'),
            'payment':(b'The applicable interest rate resets on November 9, 2026.',b'The interest payment is due on November 9, 2026.'),
            'reporting':(b'The applicable interest rate resets on November 9, 2026.',b'The reporting period ends on November 9, 2026.'),
            'other_instrument':(b'Loan TEST_LOAN (USD, SOFR):',b'Loan OTHER_LOAN (USD, SOFR):'),
            'swap_only':(b'Loan TEST_LOAN has no interest rate hedges during 2026-11-08 through 2027-11-10.',b'Interest rate swap notional: USD 500 million. Total debt: USD 1 billion.'),
            'wrong_currency':(b'pay-fixed; USD;',b'pay-fixed; EUR;'),
            'wrong_index':(b'receive-floating SOFR',b'receive-floating EURIBOR'),
            'wrong_hedge_instrument':(b'hedges Loan TEST_LOAN:',b'hedges Loan OTHER_LOAN:'),
            'notional_not_covered':(b'hedged principal USD 500000000',b'hedged principal USD 250000000'),
            'wrong_direction':(b'receive-floating SOFR and pay-fixed',b'pay-floating SOFR and receive-fixed'),
            'basis_risk':(b'no basis risk, caps, floors, or optionality',b'with basis risk, caps, floors, or optionality'),
            'unsupported_offset':(b'fixed offset coefficient 1;',b'fixed offset coefficient 0.5;'),
            'same_hedge_instrument_id':(b'Swap HEDGE_1 hedges',b'Swap TEST_LOAN hedges'),
            'hidden_reset':(b'<p>Loan TEST_LOAN (USD, SOFR): The applicable interest rate resets on November 9, 2026.</p>',b'<ix:hidden><p>Loan TEST_LOAN (USD, SOFR): The applicable interest rate resets on November 9, 2026.</p></ix:hidden>')}
        if attack=='ambiguous_reset':
            appended(c,lambda raw:raw.replace(b'</body>',b'<p>Loan TEST_LOAN (USD, SOFR): The applicable interest rate resets on December 9, 2026.</p></body>'))
        else:
            a,b=edits[attack]
            appended(c,lambda raw:raw.replace(a,b))
        # Rebind existing claims to the *new* incarnation and exact source spans.
        source=next(s for s in c['result']['sources'] if s['form']=='10-Q' and s['kind']=='document')
        obs=semantic_validation.observations(source,c['cik'])
        loan=next(o for o in obs if o.kind=='loan' and o.attributes['instrument']=='TEST_LOAN')
        for name in body['exposure']['bindings']:
            f=field(body,name);f['available_at']=source['available_at']
            if name=='principal':
                p=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='INLINE_XBRL' and loan.start<=p['locator']['start']<p['locator']['end']<=loan.end)
            else:
                kind='reset' if name=='reset_date' else ('hedge' if Decimal(field(body,'hedged_fraction')['value']) else 'no_hedge') if name in {'hedged_fraction','hedge_terms'} else 'loan'
                wanted=next((o for o in obs if o.kind==kind),loan)
                p=next(p for p in c['result']['candidates'] if p['source_key']==source['source_key'] and p['origin']=='NOTE' and p['locator']['start']<=wanted.start<wanted.end<=p['locator']['end'])
            rebind(c,body,name,p)
        if attack=='swap_only':field(body,'hedged_fraction')['value']='0.5';field(body,'hedge_terms')['value']='FIXED_OFFSET'
    # Deliberately bypass only the NEW import guard to simulate previously stored
    # wrong but owner-pinned v1 reviews. Hardened promotion must still fail closed.
    try:trust=pair(c,body,hardened=False)
    except ValueError:
        # Structural blockers are meaningful, but do not claim semantic coverage.
        pytest.fail('Attack must pass frozen structural validator before exercising semantics')
    r=hardened(c,trust);assert not r['verified_exposures'] and not r['historical_evidence']
    codes={i['code'] for row in r['semantic_assessments'] for i in row['semantic']['issues']}
    assert 'SEMANTIC_PRINCIPAL_OUTSIDE_INSTRUMENT_RECORD' not in codes
    assert codes, 'The intended semantic layer must actually reject the claim.'
    out=bridge.calculate(r,trust,event_input=event(c,r),created_at=c['clock']())['output']
    assert out['amount'] is None and out['status']!='ARITHMETIC_VERIFIED_CONDITIONAL'


def test_equal_maturity_and_reset_require_separate_explicit_reset_support(tmp_path):
    c,body=make_case(tmp_path,extra_notes=['Loan TEST_LOAN matures on November 9, 2026.'])
    trust=pair(c,body);r=hardened(c,trust)
    assert len(r['verified_exposures'])==1
    assert bridge.calculate(r,trust,event_input=event(c,r),created_at=c['clock']())['output']['amount']=='2500000'


@pytest.mark.parametrize('flag,value',[('semantic_type','INTEREST_RATE_RESET_DATE'),('witness_kind','RESET'),
    ('has_explicit_source_support',True),('relationship_verified',True)])
def test_reviewer_semantic_annotations_cannot_self_approve(tmp_path,flag,value):
    c,body=make_case(tmp_path);m=old.manifest(c,'TEST_PRIMARY_A',body);m['terms'][flag]=value
    m=seal({k:v for k,v in m.items() if k!='record_sha256'})
    with pytest.raises(jsonschema.ValidationError):reviews.store(m,c['result'],c['reviews'],clock=c['clock'])


def test_annual_reset_cannot_use_prior_occurrence_to_accelerate_rate_change(tmp_path):
    c,body=make_case(tmp_path,annual=True,end='2028-11-10');trust=pair(c,body)
    r=hardened(c,trust,window={'start':'2027-02-01','end':'2028-02-01'},effective='2027-02-01')
    assert len(r['historical_evidence'])==1 and not r['verified_exposures']
    assert 'ANNUAL_RESET_OCCURRENCE_NOT_APPLICABLE' in r['semantic_assessments'][0]['eligibility']['eligibility_reason']


@pytest.mark.parametrize('kind',['historical_valid','expired','future','partial','hedge_disjoint'])
def test_historical_evidence_and_current_eligibility(tmp_path,kind):
    c,body=make_case(tmp_path,hedge=kind=='hedge_disjoint',
        start='2026-11-10' if kind=='future' else '2026-11-08',
        reset_date='November 11, 2026' if kind=='future' else 'November 9, 2026',
        hedge_start='2027-11-11' if kind=='hedge_disjoint' else None,
        hedge_end='2028-11-11' if kind=='hedge_disjoint' else None)
    trust=pair(c,body)
    if kind=='expired':c['clock'].advance(370*86400)
    window={'start':'2026-11-07','end':'2027-11-09'} if kind=='future' else {'start':'2026-11-09','end':'2027-11-11'} if kind=='partial' else None
    if kind=='expired':window={'start':'2027-11-14','end':'2028-11-14'}
    r=hardened(c,trust,window=window)
    assert len(r['historical_evidence'])==1
    eligible=kind=='historical_valid'
    assert bool(r['verified_exposures'])==eligible
    assert r['semantic_assessments'][0]['eligibility']['calculation_eligible']==eligible
    out=bridge.calculate(r,trust,event_input=event(c,r),created_at=c['clock']())['output']
    assert (out['amount'] is not None)==eligible


def test_review_disagreement_and_owner_trust_still_block(tmp_path):
    c,body=make_case(tmp_path);other=deepcopy(body);field(other,'repricing_fraction')['value']='0.5'
    trust=pair(c,body,other=other,hardened=False)
    assert not hardened(c,trust)['verified_exposures']
    trust['trusted_review_hashes']=[];assert not hardened(c,trust)['verified_exposures']


def test_old_incarnation_cannot_be_reused(tmp_path):
    c,body=make_case(tmp_path);trust=pair(c,body)
    appended(c,lambda raw:raw.replace(b'November 9, 2026.',b'November 10, 2026.'))
    r=hardened(c,trust);assert not r['verified_exposures'] and r['statistics']['stale_contract_reviews']==2


def test_resealed_output_and_changed_window_cannot_self_attest(tmp_path):
    c,body=make_case(tmp_path);trust=pair(c,body);r=hardened(c,trust)
    forged=deepcopy(r);forged['semantic_assessments'][0]['eligibility']['calculation_eligible']=False;forged=seal({k:v for k,v in forged.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='CHANGED'):bridge.calculate(forged,trust,event_input=event(c,r),created_at=c['clock']())
    packet=event(c,r);packet['context']['calculation_end']='2028-11-09'
    with pytest.raises(ValueError,match='WINDOW'):bridge.calculate(r,trust,event_input=packet,created_at=c['clock']())


def test_new_report_immutable_source_replay(tmp_path,monkeypatch):
    # Production reports remain private even for illustrative inputs.
    c,body=make_case(tmp_path);trust=pair(c,body);r=hardened(c,trust)
    monkeypatch.setattr(report,'ROOT',tmp_path)
    root=tmp_path/'reports/private/output'
    folder=report.run(c['cik'],c['ticker'],c['clock'](),root,trust=trust,event_input=event(c,r),created_at=c['clock'](),
        source_store=c['source'],legacy_review_root=c['legacy'],review_store=c['reviews'],cache_root=c['cache'],illustrative=True)
    assert report.validate(folder)['amount']=='2500000'
    assert 'ILLUSTRATIVE' in (folder/'REPORT.ko.md').read_text(encoding='utf-8')
