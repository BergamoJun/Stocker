"""Offline diagnostic denominators; frozen scores are observations, never rewritten."""
from collections import Counter
from statistics import median
import json
import re

from extensions.research_v4.evaluation import FIELDS
from extensions.research_v4 import VARIABLES
from research.validation import stable_hash


def fraction(n,d,meaning):
    return {'numerator':n,'denominator':d,'value':n/d if d else None,'meaning':meaning}


def structural_observations(unit,raw):
    text=unit['text'];quote=raw['exact_quote'];offered=unit['allowed_spans']
    matches=[s for s in offered if s['text']==quote]
    selected=next((s for s in offered if s['span_id']==raw['span_id']),None)
    return {'quote_exists_verbatim':bool(quote and quote in text),
        'quote_equals_offered_span':bool(matches),'selected_id_exists':selected is not None,
        'id_quote_pair_matches':bool(selected and selected['text']==quote),
        'unique_offered_quote_wrong_id':len(matches)==1 and (not selected or selected['text']!=quote),
        'source_subquote_not_offered':bool(quote and quote in text and not matches),
        'subject_verbatim_in_source':bool(raw['subject_quote'] and raw['subject_quote'] in text),
        'predicate_verbatim_in_source':bool(raw['predicate_quote'] and raw['predicate_quote'] in text),
        'empty_subject':not raw['subject_quote'],'empty_predicate':not raw['predicate_quote'],
        'subject_contains_underscore':'_' in raw['subject_quote'],
        'underscore_to_space_casefold_subject_occurs':bool(raw['subject_quote'] and raw['subject_quote'].replace('_',' ').casefold() in text.casefold()),
        'ontology_consistent':VARIABLES[raw['economic_variable']]==raw['event_type'],
        'semantic_equivalence':'NOT_ADJUDICATED'}


def authored(value,gold):
    refs={g['unit_id']:g['propositions'] for g in gold}
    if len(refs)!=len(gold) or set(refs)!={r['unit']['unit_id'] for r in value['rows']}:
        raise ValueError('RECOVERY_FIXED_DENOMINATOR_CHANGED')
    counts=Counter();fields=Counter();queue=[];rows=[];explicit=Counter();joint=Counter()
    for item in value['rows']:
        unit,row=item['unit'],item['row'];expected=refs[unit['unit_id']];used=set()
        counts['expected_reference_propositions']+=len(expected)
        counts['answerable_references']+=sum(g.get('should_support',True) for g in expected)
        counts['units']+=1;counts['safety_isolated_units']+=row['status']=='SAFETY_ISOLATED'
        counts['model_extraction_calls']+=row['extract']['status'] in {'COMPLETED','INFERENCE_FAILED'}
        counts['schema_extractions']+=row['extract']['status']=='COMPLETED'
        if row['extract']['status']=='COMPLETED':
            reason=row['extract']['raw']['omission_reason']
            explicit['extraction_'+reason]+=1
            if not row['extract']['raw']['propositions'] and reason=='insufficient_context':
                counts['explicit_extractor_abstention_units']+=1
            elif not row['extract']['raw']['propositions'] and reason=='no_economic_assertion':
                counts['explicit_no_assertion_predictions']+=1
        for index,p in enumerate(row['propositions']):
            raw=p['raw'];counts['candidate_outputs']+=1
            eligible=[i for i,g in enumerate(expected) if i not in used and g['economic_variable']==raw['economic_variable']]
            target=next((i for i in eligible if all(raw[k]==expected[i][k] for k in FIELDS)),eligible[0] if eligible else None)
            ref=expected[target] if target is not None else None
            if target is not None:used.add(target);counts['variable_aligned_references']+=1
            mismatches=[k for k in FIELDS if ref is None or raw[k]!=ref[k]]
            exact=ref is not None and not mismatches
            counts['exact_reference_matches']+=exact
            for k in FIELDS:fields[k]+=bool(ref and raw[k]==ref[k])
            accepted=p['decision']=='SUPPORTED';counts['final_supported']+=accepted
            counts['structurally_bound']+=p.get('binding') is not None
            if not p.get('binding'):counts['structural_rejections']+=1
            check=p['assessment'];completed=check['status']=='COMPLETED'
            counts['assessor_calls']+=check['status'] in {'COMPLETED','INFERENCE_FAILED'}
            counts['schema_assessments']+=completed
            consistent=bool(completed and check['raw']['judgment']=='consistent')
            if completed:explicit['assessor_'+check['raw']['judgment']]+=1
            joint[('reference_exact' if exact else 'reference_mismatch')+' / '+('assessor_consistent' if consistent else 'assessor_not_consistent' if completed else 'assessor_not_observed')]+=1
            counts['exact_correct_proposals_rejected']+=bool(exact and not accepted)
            if accepted:
                good=exact and ref.get('should_support',True)
                counts['supported_exact_answerable']+=good
                counts['supported_reference_disagreements']+=not good
            if ref and ref.get('should_support',True):
                counts['answerable_aligned_supported']+=accepted
                counts['answerable_aligned_rejected']+=not accepted
            obs=structural_observations(unit,raw)
            diagnostic={'unit_id':unit['unit_id'],'candidate_index':index,'decision':p['decision'],
                'reference_index':target,'exact_field_mismatches':mismatches,'structural_observations':obs,
                'decision_reasons':p['reasons'],'assessor_judgment':check['raw']['judgment'] if completed else None}
            rows.append(diagnostic)
            if not exact or not accepted:
                category=('ANCHOR_EXPRESSION_DIFFERENCE' if ref and mismatches and set(mismatches)<={'subject_quote','predicate_quote'}
                          else 'UNMATCHED_EXTRA_CANDIDATE' if ref is None else 'TYPED_REFERENCE_DISAGREEMENT' if mismatches else 'EXACT_REFERENCE_REJECTED')
                queue.append({'queue_id':stable_hash([unit['unit_id'],index,category]),'audience':'POST_BLIND_EQUIVALENCE_PANEL_ONLY',
                    'primary_review_must_not_see':True,'status':'PENDING_HUMAN_ADJUDICATION','category':category,
                    'unit':unit,'candidate':raw,'authored_reference':ref,'decision':p['decision'],
                    'mismatch_fields':mismatches,'structural_observations':obs,
                    'reason':'exact representation disagreement is not automatically a semantic error; examine scope and source context'})
        for index,g in enumerate(expected):
            if index in used:continue
            counts['variable_unaligned_references']+=1
            counts['answerable_missing_references']+=g.get('should_support',True)
            queue.append({'queue_id':stable_hash([unit['unit_id'],index,'missing']),
                'audience':'POST_BLIND_EQUIVALENCE_PANEL_ONLY','primary_review_must_not_see':True,
                'status':'PENDING_HUMAN_ADJUDICATION','category':'REFERENCE_WITHOUT_VARIABLE_ALIGNED_CANDIDATE',
                'unit':unit,'candidate':None,'authored_reference':g,'reason':'distinguish omission from ontology/alignment error'})
    c=counts
    metrics={
        'variable_aligned_candidate_coverage':fraction(c['variable_aligned_references'],c['expected_reference_propositions'],'same-variable alignment, not semantic recall'),
        'exact_reference_raw_precision':fraction(c['exact_reference_matches'],c['candidate_outputs'],'literal authored reference agreement'),
        'exact_reference_raw_recall':fraction(c['exact_reference_matches'],c['expected_reference_propositions'],'literal authored reference agreement'),
        'structural_binding_acceptance':fraction(c['structurally_bound'],c['candidate_outputs'],'source/offset/anchor contract, not truth'),
        'actual_schema_acceptance':fraction(c['schema_extractions']+c['schema_assessments'],c['model_extraction_calls']+c['assessor_calls'],'actual attempted model calls; excludes pre-model safety isolation'),
        'exact_reference_support_precision':fraction(c['supported_exact_answerable'],c['final_supported'],'exact-reference proxy, not adjudicated semantic precision'),
        'exact_reference_support_recall':fraction(c['supported_exact_answerable'],c['answerable_references'],'answerable authored reference denominator'),
        'exact_correct_proposal_rejection':fraction(c['exact_correct_proposals_rejected'],c['exact_reference_matches'],'conditional on an exactly correct proposal having been generated'),
        'aligned_final_rejections':fraction(c['answerable_aligned_rejected'],c['answerable_references'],'not necessarily unnecessary; candidate may be wrong'),
        'missing_answerable_reference_fraction':fraction(c['answerable_missing_references'],c['answerable_references'],'unaligned reference, not explicit abstention'),
        'field_literal_reference_agreement':{k:fraction(fields[k],c['expected_reference_propositions'],'literal reference fields; semantic equivalence unadjudicated') for k in FIELDS},
        'adjudicated_semantic_precision':'NOT_MEASURED','adjudicated_semantic_recall':'NOT_MEASURED',
        'adjudicated_wrong_approvals':'NOT_MEASURED','adjudicated_unnecessary_rejections':'NOT_MEASURED'}
    return {'counts':dict(c),'metrics':metrics,'explicit_model_statements':dict(explicit),
        'conditional_assessor_joint_counts':dict(joint),'true_error_correlation':'NOT_MEASURED',
        'selection_warning':'assessor observed only after structural acceptance; same weights, no independent truth labels',
        'diagnostic_rows':rows,'adjudication_queue':queue,'frozen_scores_changed':False}


def critic(cases,gold,result,reference_contexts):
    expected={g['id']:g for g in gold};by_id={r['id']:r for r in result['rows']}
    if len(by_id)!=len(cases) or set(by_id)!=set(expected):raise ValueError('RECOVERY_CRITIC_CASE_SET_CHANGED')
    nominal_positive=nominal_negative=tp=tn=0;details=[]
    pairs={}
    for case in cases:
        row=by_id[case['id']];allowed=expected[case['id']]['allowed_verdicts']
        positive='SUPPORTED_CONDITIONAL' in allowed
        supported=bool(row['raw'] and row['raw']['verdict']=='SUPPORTED_CONDITIONAL')
        nominal_positive+=positive;nominal_negative+=not positive
        tp+=positive and supported;tn+=not positive and row['raw'] is not None and not supported
        ctx=json.loads(case['messages'][1]['content']);signature=stable_hash([ctx['ISSUER'],ctx['EVIDENCE'],ctx['FACTS']])
        pairs.setdefault(signature,{})[case['arm']]=row['raw']['verdict'] if row['raw'] else None
        extra=reference_contexts.get(case['id'],{})
        details.append({'id':case['id'],'arm':case['arm'],'frozen_expected_verdicts':allowed,
            'observed_verdict':row['raw']['verdict'] if row['raw'] else None,
            'nominal_reference_semantically_valid_is_not_full_claim_validity':True,
            'evidence_proposition_id_matches_claim':any(e.get('proposition_id')==ctx['PROPOSITION']['proposition_id'] for e in ctx['EVIDENCE']),
            'evidence_has_normalized_semantic_fields':all('economic_variable' in e for e in ctx['EVIDENCE']),**extra})
    return {'frozen_arm_metrics':result['metrics'],'verdict_counts':dict(Counter(d['observed_verdict'] for d in details)),
        'nominal_binary_label_counts':{'positive':nominal_positive,'negative':nominal_negative},
        'nominal_sensitivity':fraction(tp,nominal_positive,'interprets inherited labels only; claim confounding remains'),
        'nominal_specificity':fraction(tn,nominal_negative,'constant rejection can achieve this'),
        'nominal_balanced_accuracy':(tp/nominal_positive+tn/nominal_negative)/2 if nominal_positive and nominal_negative else None,
        'pair_verdict_invariance':fraction(sum(p.get('reference')==p.get('corrupted') for p in pairs.values()),len(pairs),'direction intervention plus shared exposure confounds'),
        'all_reject_gate_pass':False,'causal_over_rejection_rate':'NOT_IDENTIFIED',
        'upstream_error_attribution':'NOT_IDENTIFIED','details':details,
        'diagnostic_limit':'reference arm has three positive and three insufficient-evidence labels; changed claims/evidence prevent interpreting 0/6 as pure semantic critic accuracy'}


def runtime_stats(receipts):
    timing=[];sessions=[];prompt=completion=elapsed=0
    for value in receipts:
        sessions.extend(value['sessions']);elapsed+=value['elapsed_seconds']
        for s in value['sessions']:
            prompt+=s['prompt_tokens'];completion+=s['completion_tokens']
            if not s['folder']:continue
            from pathlib import Path
            for p in (Path(s['folder'])/'responses').glob('*.json'):
                r=json.loads(p.read_text(encoding='utf-8'))['response'];t=r.get('timings',{})
                if t:timing.append(t)
    rates=[float(t['predicted_per_second']) for t in timing if t.get('predicted_per_second')]
    return {'prompt_tokens':prompt,'completion_tokens':completion,'elapsed_seconds_with_startup':elapsed,
        'end_to_end_output_tokens_per_second':completion/elapsed if elapsed else None,
        'response_timing_records':len(timing),'median_generation_tokens_per_second':median(rates) if rates else None,
        'generation_rate_range': [min(rates),max(rates)] if rates else None,
        'hardware_exclusive_usage':False,'new_model_size_projection':'NOT_ESTIMATED_WITHOUT_CHECKPOINT_AND_KV_ARCHITECTURE',
        'paid_api_cost_usd':0,'electricity_cost_usd':'NOT_MEASURED','additional_recovery_inference_calls':0}
