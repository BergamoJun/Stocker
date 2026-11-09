"""ILLUSTRATIVE - market timing, blind labels and temporal leakage boundaries."""
from copy import deepcopy
import pytest
from extensions import event_evaluation as e,analysis_contract
from extensions.tests.test_exposure_model import fact
from research.validation import stable_hash

OBSERVED='2026-11-20T22:00:00Z'
def sample():
 identity={'cik':'0000000001','symbol':'TEST','name':'ILLUSTRATIVE - NOT LIVE DATA'}
 f=fact(); f['source_refs'][0]['locator']={'fact_id':'ix:illustrative','dom_id':'row1','tag':'us-gaap:CashAndCashEquivalentsAtCarryingValue','context_ref':'context1','row_number':1,'source_sha256':'a'*64}
 ai,_=analysis_contract.assemble(identity,'2026-11-08T00:00:00Z',[f],[],{},illustrative=True)
 return {'case_id':'case1','event_group_id':'event1','identity':identity,'source_id':'illustrative_sec','times':{'event_at':'2026-11-09T21:09:00Z','first_public_at':'2026-11-09T21:10:00Z','source_published_at':'2026-11-09T21:10:00Z','retrieved_at':'2026-11-09T21:10:10Z','first_local_available_at':'2026-11-09T21:10:20Z','analysis_cutoff':'2026-11-09T21:11:00Z'},'first_public_precision':'second','first_public_verified':True,'source_provenance_verified':True,'prediction_frozen_at':'2026-11-09T21:10:50Z','exposure_as_of':'2026-11-09T20:00:00Z','exposure_first_available_at':'2026-11-09T20:00:01Z','exposure_view':[],'ai_input':ai,'is_illustrative':True,'partition':'validation','overlap_reviewed':True,'overlapping_events':[]}


def test_after_close_does_not_use_same_session():
 w=e.window('2026-11-09T21:10:00Z')
 assert w['sessions'][0]=='2026-11-10' and w['baseline_session']=='2026-11-09'
 assert w['publication_regime']=='after_close'


def test_holiday_and_short_session():
 w=e.window('2026-11-25T22:00:00Z')
 assert w['sessions'][0]=='2026-11-27' and '2026-11-26' not in w['sessions']
 w=e.window('2026-11-27T18:15:00Z')
 assert w['publication_regime']=='after_close' and w['sessions'][0]=='2026-11-30'


def test_daylight_saving_changes_utc_open():
 assert e.window('2026-03-06T13:00:00Z')['first_open']=='2026-03-06T14:30:00Z'
 assert e.window('2026-03-06T22:00:00Z')['first_open']=='2026-03-09T13:30:00Z'


def test_intraday_activity_reference_excludes_announcement_day():
 w=e.window('2026-11-09T18:00:00Z')
 assert w['publication_regime']=='regular_session' and w['sessions'][0]=='2026-11-10'
 assert w['pre_event_last_session']=='2026-11-06'


def test_eligible_frozen_forward_case():
 assert e.eligibility(sample(),OBSERVED)['eligible']


@pytest.mark.parametrize('which',['publication','collection','exposure','fact','late_analysis','unfrozen','outcome','overlap'])
def test_invalid_timing_and_missing_reviews_withhold(which):
 c=sample(); observed=OBSERVED
 if which=='publication': c['first_public_precision']='date'
 if which=='collection': c['times']['retrieved_at']=c['times']['analysis_cutoff']
 if which=='exposure': c['exposure_as_of']='2026-11-10T00:00:00Z'
 if which=='fact': c['ai_input']['financial_facts'][0]['available_at']='2026-11-10T00:00:00Z'
 if which=='late_analysis': c['times']['analysis_cutoff']='2026-11-10T15:00:00Z'
 if which=='unfrozen': c['prediction_frozen_at']=None
 if which=='outcome': observed='2026-11-11T00:00:00Z'
 if which=='overlap': c['overlap_reviewed']=False
 assert not e.eligibility(c,observed)['eligible']


def test_locked_final_never_eligible():
 c=sample(); c['partition']='locked_final'
 with pytest.raises(ValueError,match='LOCKED_FINAL'): e.eligibility(c,OBSERVED)


def test_model_cannot_see_gold_prices_or_future_exposure():
 c=sample(); c['gold']='HIDDEN'; c['realized_price']='ILLUSTRATIVE_HIDDEN'
 assert set(e.model_view(c))=={'approved_context','exposure_context'}
 c['exposure_first_available_at']='2026-11-10T00:00:00Z'
 with pytest.raises(ValueError,match='FUTURE_EXPOSURE'): e.model_view(c)


def test_independent_gold_is_required_and_group_count_does_not_inflate():
 c=sample(); d=deepcopy(c); d['case_id']='case2'
 r=e.interpretation([c,d],[],[])
 assert r['total_cases']==2 and r['distinct_event_groups']==1
 assert r['accuracy'] is None and r['evaluated_cases']==0


def test_interpretation_score_is_not_price_validation():
 c=sample(); p={'case_id':c['case_id'],'input_sha256':stable_hash(e.model_view(c)),'frozen_at':c['prediction_frozen_at'],'direction':'unknown'}
 g={'case_id':c['case_id'],'reviewer_kind':'independent_reviewer','outcome_blinded':True,'evidence_ids':[c['source_id']],'direction':'unknown'}
 r=e.interpretation([c],[p],[g]); assert r['correct']==1 and not r['price_accuracy_claimed']
 g['reviewer_kind']='same_model'
 assert e.interpretation([c],[p],[g])['evaluated_cases']==0


def test_unbound_gold_cannot_be_scored():
 c=sample(); g={'case_id':c['case_id'],'reviewer_kind':'independent_reviewer','outcome_blinded':True,'evidence_ids':['foreign_source'],'direction':'unknown'}
 assert e.interpretation([c],[],[g])['qualified_gold_cases']==0


def test_event_group_cannot_cross_split():
 a=sample(); b=deepcopy(a); b['case_id']='case2'; b['times']['first_public_at']='2026-11-13T22:00:00Z'
 r=e.temporal_partition([a,b],'2026-11-12T00:00:00Z')
 assert set(r.values())=={'withheld_shared_event_across_split'}


def test_outcome_overlap_is_purged_at_split():
 c=sample(); assert e.temporal_partition([c],'2026-11-12T00:00:00Z')[c['case_id']]=='purged_overlap'


def test_foreign_issuer_cannot_enter_model_context():
 c=sample(); c['identity']={**c['identity'],'cik':'0000000002'}
 with pytest.raises(ValueError,match='ISSUER_MISMATCH'): e.model_view(c)


def test_future_evidence_cannot_enter_model_context():
 c=sample(); c['ai_input']['financial_facts'][0]['available_at']='2026-11-10T00:00:00Z'
 with pytest.raises(ValueError,match='FUTURE_INPUT'): e.model_view(c)


def test_unfrozen_or_changed_prediction_cannot_be_scored():
 c=sample(); p={'case_id':c['case_id'],'input_sha256':'bad_hash','frozen_at':c['prediction_frozen_at'],'direction':'unknown'}
 g={'case_id':c['case_id'],'reviewer_kind':'independent_reviewer','outcome_blinded':True,'evidence_ids':[c['source_id']],'direction':'unknown'}
 with pytest.raises(ValueError,match='NOT_FROZEN'): e.interpretation([c],[p],[g])


def test_exact_open_boundary_uses_next_complete_session():
 w=e.window('2026-11-09T14:30:00Z')
 assert w['sessions'][0]=='2026-11-10'
