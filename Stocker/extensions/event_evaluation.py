"""Separate source interpretation labels, temporal eligibility and event grouping."""
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest,stable_hash
from research.swing_models import XnysCalendar
from . import analysis_contract

BASE=Path(__file__).resolve().parent
FILES=['extensions/'+n for n in ('event_evaluation.py','event_outcomes.py','event_eval_policy.json','event_eval.schema.json','event_eval_report.py')]
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def methods(): return {p:digest(ROOT/p) for p in FILES}
def policy():
    p=read(BASE/'event_eval_policy.json')
    if p['training'] or p['score_release'] or p['network_requests'] or p['external_calls'] or p['horizon_sessions']!=5: raise ValueError('EVENT_EVALUATION_BOUNDARY_CHANGED')
    return p


def window(first_public_at):
    at=as_utc(first_public_at); start=(at.date()-timedelta(days=10)).isoformat(); end=(at.date()+timedelta(days=40)).isoformat()
    cal=XnysCalendar(start,end)
    # Requested bounds may be weekends; the constructed calendar already clips
    # to actual session dates, unlike sessions_in_range on out-of-bound dates.
    sessions=[s.date().isoformat() for s in cal.calendar.sessions]
    opens={s:as_utc(cal.calendar.session_open(s).isoformat()) for s in sessions}
    following=[s for s in sessions if opens[s]>at][:5]
    if len(following)!=5: raise ValueError('INSUFFICIENT_EVENT_CALENDAR')
    baseline=sessions[sessions.index(following[0])-1]; day=at.date().isoformat()
    regime='non_session_day'
    if day in opens:
        regime='before_open' if at<opens[day] else 'regular_session' if at<as_utc(cal.close(day)) else 'after_close'
    before=[s for s in sessions if as_utc(cal.close(s))<at]
    return {'calendar_id':cal.id,'calendar_version':cal.version,'publication_regime':regime,'baseline_session':baseline,'pre_event_last_session':before[-1],'sessions':following,'first_open':opens[following[0]].isoformat().replace('+00:00','Z'),'ends_at':cal.close(following[-1]),'measurement':'first five complete regular sessions starting strictly after publication; not isolated immediate effect'}


def eligibility(case,observed_at):
    t=case['times']; reasons=[]; cutoff=as_utc(t['analysis_cutoff']).replace(microsecond=0)
    if case.get('partition')=='locked_final': raise ValueError('LOCKED_FINAL_CASE_FORBIDDEN')
    for name in ('retrieved_at','first_local_available_at'):
        if t.get(name) is None: reasons.append('missing_'+name)
        elif as_utc(t[name])>=cutoff: reasons.append('information_not_before_analysis')
    if not t.get('first_public_at') or case['first_public_precision']!='second' or not case['first_public_verified']:
        reasons.append('first_public_time_not_verified'); w=None
    else:
        w=window(t['first_public_at'])
        if as_utc(t['first_public_at'])>cutoff: reasons.append('publication_after_analysis')
        if cutoff>=as_utc(w['first_open']): reasons.append('analysis_after_first_reaction_open')
        if as_utc(observed_at)<as_utc(w['ends_at']): reasons.append('outcome_window_not_complete')
    if not case.get('prediction_frozen_at'): reasons.append('no_frozen_prediction')
    elif as_utc(case['prediction_frozen_at'])>cutoff or w and as_utc(case['prediction_frozen_at'])>=as_utc(w['first_open']): reasons.append('prediction_time_violation')
    for f in case['ai_input']['financial_facts']:
        if as_utc(f['available_at'])>=cutoff or f['end']>cutoff.date().isoformat(): reasons.append('future_financial_or_exposure_evidence')
    if as_utc(case['exposure_as_of'])>cutoff: reasons.append('future_exposure_packet')
    if case['exposure_first_available_at'] and as_utc(case['exposure_first_available_at'])>=cutoff: reasons.append('exposure_not_available_at_analysis')
    if not case['source_provenance_verified']: reasons.append('source_provenance_unverified')
    if case.get('overlap_reviewed') is not True: reasons.append('overlapping_event_review_not_complete')
    return {'eligible':not reasons,'reasons':sorted(set(reasons)),'window':w}


def model_view(case):
    ai=case['ai_input']; analysis_contract.schema(ai,'ai_input')
    cutoff=as_utc(case['times']['analysis_cutoff']).replace(microsecond=0)
    if ai['identity']!=case['identity']: raise ValueError('EVENT_ISSUER_MISMATCH')
    if any(as_utc(f['available_at'])>=cutoff or f['end']>cutoff.date().isoformat() for f in ai['financial_facts']) or any(as_utc(d['available_at'])>=cutoff or d['published_date']>cutoff.date().isoformat() for d in ai['official_documents']): raise ValueError('EVENT_MODEL_FUTURE_INPUT')
    if as_utc(case['exposure_as_of'])>cutoff or case['exposure_first_available_at'] and as_utc(case['exposure_first_available_at'])>=cutoff: raise ValueError('EVENT_MODEL_FUTURE_EXPOSURE')
    # No gold, realized return, market/sector bars or vendor news is returned.
    return {'approved_context':ai,'exposure_context':case['exposure_view']}


def interpretation(cases,predictions,gold):
    by_id={c['case_id']:c for c in cases}
    if len(by_id)!=len(cases): raise ValueError('DUPLICATE_EVENT_CASE')
    if any(p['case_id'] not in by_id for p in predictions) or len({p['case_id'] for p in predictions})!=len(predictions): raise ValueError('UNKNOWN_OR_DUPLICATE_PREDICTION')
    if any(g['case_id'] not in by_id for g in gold) or len({g['case_id'] for g in gold})!=len(gold): raise ValueError('UNKNOWN_OR_DUPLICATE_GOLD')
    labels={}
    for g in gold:
        c=by_id[g['case_id']]; allowed={c['source_id']}|{f['fact_id'] for f in c['ai_input']['financial_facts']}|{d['source_id'] for d in c['ai_input']['official_documents']}
        if g.get('reviewer_kind')=='independent_reviewer' and g.get('outcome_blinded') is True and g.get('evidence_ids') and set(g['evidence_ids'])<=allowed: labels[g['case_id']]=g
    correct=evaluated=0
    for p in predictions:
        g=labels.get(p['case_id']); c=by_id[p['case_id']]
        if not g: continue
        if p['direction'] not in {'favourable','adverse','mixed','unknown'} or g['direction'] not in {'favourable','adverse','mixed','unknown'}: raise ValueError('INVALID_INTERPRETATION_DIRECTION')
        if p['input_sha256']!=stable_hash(model_view(c)) or as_utc(p['frozen_at'])>as_utc(c['times']['analysis_cutoff']): raise ValueError('PREDICTION_NOT_FROZEN_TO_INPUT')
        evaluated+=1; correct+=p['direction']==g['direction']
    return {'total_cases':len(cases),'distinct_event_groups':len({c['event_group_id'] for c in cases}),'qualified_gold_cases':len(labels),'model_predictions':len(predictions),'evaluated_cases':evaluated,'correct':correct if evaluated else None,'accuracy':str(Decimal(correct)/evaluated) if evaluated else None,'status':'observed_interpretation_only' if evaluated else 'not_run','price_accuracy_claimed':False,'independent_human_review_authentication':'declared reviewer metadata; no cryptographic identity proof'}


def temporal_partition(cases,boundary):
    boundary=as_utc(boundary); groups={}
    for c in cases:
        if c.get('partition')=='locked_final': raise ValueError('LOCKED_FINAL_CASE_FORBIDDEN')
        groups.setdefault(c['event_group_id'],[]).append(c)
    result={}
    for group,rows in groups.items():
        proposed=set()
        for c in rows:
            t=c['times'].get('first_public_at')
            proposed.add('unassigned' if not t or c['first_public_precision']!='second' else 'development' if as_utc(t)<boundary else 'validation')
        label=next(iter(proposed)) if len(proposed)==1 else 'withheld_shared_event_across_split'
        for c in rows:
            w=window(c['times']['first_public_at']) if c['times'].get('first_public_at') and c['first_public_precision']=='second' else None
            result[c['case_id']]='purged_overlap' if label=='development' and w and as_utc(w['ends_at'])>=boundary else label
    return result
