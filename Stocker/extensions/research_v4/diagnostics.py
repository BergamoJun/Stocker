"""Fourteen-stage read-only diagnostics of frozen v3 outputs; no regrading."""
from collections import Counter
from research.validation import digest
from . import read, seal
from ..research_v3_eval import FIELDS

STAGES=['source_span_identification','atomic_candidate_generation','exact_quote_binding',
        'subject','variable','event_type','direction','negation','condition_modality',
        'observation_vs_forecast','time_horizon','schema','deterministic_semantics','decision']


def analyze(inputs,gold,receipt):
    answers={g['id']:g for g in gold};counts=Counter();details=[];raw_errors=Counter()
    for row in receipt['rows']:
        g=answers[row['id']];a=row.get('analysis');raw={};bound={};cards={}
        if a:
            for s in a['semantic_rows']:
                counts['semantic_calls']+=s['model_called'];counts['schema_completed']+=s['status']=='COMPLETED'
                counts['semantic_inference_failures']+=s['status']=='INFERENCE_FAILED'
                if s['raw']:raw.update({p['exact_quote']:p for p in s['raw']['propositions']})
            bound={p['exact_quote']:p for p in a['propositions']}
            props={p['proposition_id']:p for p in a['propositions']}
            cards={props[c['proposition_id']]['exact_quote']:c for c in a['cards']}
            counts['raw_generated']+=len(raw)
            counts['bound_propositions']+=len(bound)
            counts['logical_rejections']+=sum(p['evidence_status']!='SEMANTICALLY_SUPPORTED' for p in bound.values())
        if not g['propositions']:counts['authored_no_proposition_cases']+=1
        for e in g['propositions']:
            counts['expected']+=1;p=raw.get(e['exact_quote']);b=bound.get(e['exact_quote']);card=cards.get(e['exact_quote'])
            mismatch=[k for k in FIELDS if not p or p[k]!=e[k]]
            counts['extracted']+=p is not None;counts['quote_bound']+=b is not None
            for name,keys in {'subject':['economic_subject'],'variable':['economic_variable'],'event_type':['event_type'],
                'direction':['variable_direction'],'negation':['negation'],'condition_modality':['conditionality','condition_quote','modality'],
                'observation_vs_forecast':['statement_type'],'time_horizon':['temporal_reference','temporal_expression','horizon']}.items():
                counts[name+'_correct']+=bool(p and all(p[k]==e[k] for k in keys))
            counts['full_exact']+=not mismatch
            representation=[]
            if p:
                for k in ['condition_quote','temporal_expression']:
                    if e[k] is None and str(p[k]).lower() in {'none','null','unknown',''} and p[k] is not None:representation.append(k)
            counts['representation_variants']+=bool(representation)
            counts['possible_correct_rule_rejections']+=bool(not mismatch and b and b['evidence_status']!='SEMANTICALLY_SUPPORTED')
            counts['mixed_error_cases']+=len(set(mismatch)-set(representation))>1
            raw_errors.update(mismatch)
            details.append({'case_id':row['id'],'expected_index':counts['expected'],'mismatched_fields':mismatch,
                'representation_only_fields':representation,'raw_model_error':bool(set(mismatch)-set(representation)),
                'rule_rejected':bool(b and b['evidence_status']!='SEMANTICALLY_SUPPORTED'),
                'possible_correct_rule_rejection':bool(not mismatch and b and b['evidence_status']!='SEMANTICALLY_SUPPORTED'),
                'insufficient_context_authored':not e['answerable'],'schema_or_runtime_failure':not p,
                'final_decision':card['final_status'] if card else None})
    n=counts['expected'];rates={k:counts[k]/n if n else None for k in ['extracted','quote_bound','subject_correct','variable_correct','event_type_correct','direction_correct','negation_correct','condition_modality_correct','observation_vs_forecast_correct','time_horizon_correct','full_exact']}
    return seal({'stages':STAGES,'counts':dict(counts),'rates':rates,'field_mismatches':dict(raw_errors),'cases':details,
        'frozen_metrics_unchanged':receipt['metrics'],'single_defect_explains_failure':False,
        'representation_variants_are_not_rescored':True,'gold_kind':'developer_authored_not_independent',
        'actual_text_accuracy':'NOT_MEASURED','counterfactual_critic_diagnostic':'separate registered trial; never pool with end-to-end score'})
