"""ILLUSTRATIVE - accounting fact verification never certifies market sensitivity."""
from copy import deepcopy
import pytest
from extensions import exposure_model as m

AT='2026-11-10T01:00:00Z'
def fact(metric='cash',value='100',**changes):
 f={'fact_id':'illustrative_'+metric,'cik':'0000000001','metric':metric,'value':value,'unit':'USD','basis':'instant','start':None,'end':'2026-09-30','scope':'consolidated','available_at':'2026-11-01T00:00:00Z','validation_kind':'recorded_source_review','source_refs':[{'source_id':'illustrative_sec','url':'https://www.sec.gov/Archives/illustrative','raw_sha256':'a'*64,'locator':{'fact_id':'row1'}}]}; f.update(changes); return f
def packet(facts): return {'identity':{'cik':'0000000001','symbol':'TEST','name':'ILLUSTRATIVE'},'is_illustrative':True,'as_of':'2026-11-09T00:00:00Z','financial_facts':facts}
def item(p,key): return next(x for x in m.derive(p,AT)['items'] if x['id']==key)


def test_liabilities_never_implies_borrowings_or_floating_debt():
 p=packet([fact('liabilities')]); v=m.derive(p,AT)
 assert item(p,'liabilities_context')['status']=='VERIFIED'
 assert item(p,'total_borrowings')['status']=='UNKNOWN'
 assert item(p,'floating_rate_borrowings')['status']=='UNKNOWN'
 assert v['hypotheses'][0]['status']=='HYPOTHETICAL'


def test_exact_debt_and_hedge_amounts_do_not_certify_hedge_effect():
 p=packet([fact('floating_rate_borrowings'),fact('interest_rate_hedge_notional')])
 assert item(p,'floating_rate_borrowings')['status']=='VERIFIED'
 assert item(p,'interest_rate_hedge_notional')['status']=='VERIFIED'
 assert item(p,'interest_expense_sensitivity')['status']=='UNKNOWN'


def test_xom_sales_scope_preserved_not_promoted_to_energy_seller():
 scope='consolidated_sales_excluding_equity_affiliate_and_other_income'
 p=packet([fact('revenue',basis='quarter',start='2026-07-01',scope=scope)])
 assert item(p,'sales_context')['scope']==scope
 assert item(p,'energy_seller_role')['status']=='UNKNOWN'


def test_specific_energy_activity_only_infers_limited_role():
 p=packet([fact('energy_sales_revenue',basis='quarter',start='2026-07-01')])
 assert item(p,'energy_seller_role')['status']=='INFERRED'
 assert item(p,'long_term_energy_price_protection')['status']=='UNKNOWN'


@pytest.mark.parametrize('change',[{'unit':'USD/shares'},{'scope':'equity_affiliate'},{'available_at':AT},{'end':'2020-01-01'},{'validation_kind':'unreviewed'},{'value':'NaN'},{'start':'2026-01-01'}])
def test_ineligible_evidence_stays_unknown(change):
 assert item(packet([fact(**change)]),'cash_balance')['status']=='UNKNOWN'


def test_cross_issuer_rejected():
 with pytest.raises(ValueError,match='IDENTITY'): m.derive(packet([fact(cik='0000000002')]),AT)


def test_equal_date_conflict_not_resolved_by_receipt_order():
 a=fact(); b=fact(value='99',fact_id='competing'); p=packet([a,b])
 assert item(p,'cash_balance')['status']=='UNKNOWN'
 assert item(p,'cash_balance')['reason_code']=='conflicting_values_or_scopes'


def test_segment_is_not_consolidated_cash():
 assert item(packet([fact(scope='segment')]),'cash_balance')['status']=='UNKNOWN'


def test_source_authority_and_hash_checked():
 f=fact(); f['source_refs'][0]['url']='https://unapproved.example/fact'
 assert item(packet([f]),'cash_balance')['status']=='UNKNOWN'
 f=fact(); f['source_refs'][0]['raw_sha256']='z'*64
 assert item(packet([f]),'cash_balance')['status']=='UNKNOWN'


def test_no_invented_publication_time_or_score():
 p=m.derive(packet([fact()]),AT)
 assert p['released_score'] is None and p['released_price_plan'] is None
 assert all(x['published_at'] is None for x in p['items'])
 assert p['business_exposure_complete'] is False


def test_empty_evidence_is_unknown_without_hypotheses():
 p=m.derive(packet([]),AT)
 assert all(x['status']=='UNKNOWN' for x in p['items']) and p['hypotheses']==[]


def test_zero_activity_does_not_establish_energy_role():
 p=packet([fact('energy_sales_revenue',value='0',basis='quarter',start='2026-07-01')])
 assert item(p,'energy_seller_role')['status']=='UNKNOWN'


def test_future_packet_is_rejected():
 p=packet([]); p['as_of']='2026-11-11T00:00:00Z'
 with pytest.raises(ValueError,match='FUTURE_INPUT'): m.derive(p,AT)
