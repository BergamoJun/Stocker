"""Separately preregistered v4 metrics, never a rescore of candidate-2/3."""
from research.validation import stable_hash
from research.sec_transport import now
from . import methods, settings, THRESHOLDS, LIMITS, VERSION, seal, verify_seal, write_once
from . import execution

FIELDS=['subject_quote','predicate_quote','economic_variable','event_type','direction','assertion','negation_scope','temporal_reference','condition_quotes','time_quotes']


def register(inputs,gold,path,*,purpose):
    from .segmentation import source_units
    units,coverage=source_units(inputs)
    value=seal({'contract':VERSION+'-registration','registered_at':now(),'purpose':purpose,
        'methods':methods(),'settings':settings(),'thresholds':THRESHOLDS,'limits':LIMITS,
        'inputs_sha256':stable_hash(inputs),'gold_sha256':stable_hash(gold),
        'unit_ids':[u['unit_id'] for u in units],'coverage':coverage,
        'alignment':'source unit and economic variable; exact typed fields; no post-hoc normalization',
        'gold_kind':'developer_authored_not_independent' if gold is not None else 'NONE_NOT_READY',
        'independent_evaluation_status':'INDEPENDENT_EVALUATION_NOT_RUN','trial_policy':'one planned evaluation; failed stages not silently retried'})
    write_once(path,value);return value


def check_registration(inputs,gold,reg):
    verify_seal(reg)
    if any(reg[k]!=v for k,v in {'methods':methods(),'settings':settings(),'thresholds':THRESHOLDS,'limits':LIMITS,
        'inputs_sha256':stable_hash(inputs),'gold_sha256':stable_hash(gold)}.items()):raise ValueError('V4_PREREGISTRATION_CHANGED')


def metrics(value,gold):
    all_props=[p for row in value['rows'] for p in row['row']['propositions']]
    stats={'candidate_propositions':len(all_props),'decisions':{d:sum(p['decision']==d for p in all_props) for d in ['SUPPORTED','CONTRADICTED','AMBIGUOUS','UNSUPPORTED','INSUFFICIENT_CONTEXT','INFERENCE_FAILED']},
        'processed_units':value['coverage']['completed_units'],'expected_units':len(value['rows']),
        'request_attempts':value['request_attempts'],'field_accuracy':None,'precision':None,'recall':None,
        'false_approvals':None,'unnecessary_abstention':None,
        'quote_anchor_binding_fraction':sum(p.get('binding') is not None for p in all_props)/len(all_props) if all_props else None,
        'descriptive_supported_unit_fraction':sum(any(p['decision']=='SUPPORTED' for p in x['row']['propositions']) for x in value['rows'])/len(value['rows']) if value['rows'] else None,
        'independent_evaluation':'INDEPENDENT_EVALUATION_NOT_RUN','quality_status':'NOT_MEASURED'}
    if gold is None:return stats
    expected=sum(len(g['propositions']) for g in gold);answerable=sum(p.get('should_support',True) for g in gold for p in g['propositions']);by_id={g['unit_id']:g for g in gold}
    if set(by_id)!={x['unit']['unit_id'] for x in value['rows']}:raise ValueError('V4_GOLD_DENOMINATOR_CHANGED')
    raw_correct=supported_correct=false=matched=0;fields={k:0 for k in FIELDS};contracts=tasks=0
    for item in value['rows']:
        row=item['row'];reference=by_id[item['unit']['unit_id']]['propositions'];used=set()
        tasks+=1+len(reference)
        contracts+=row['extract']['status']=='COMPLETED'
        for p in row['propositions']:
            contracts+=p['assessment']['status']=='COMPLETED'
            raw=p['raw'];eligible=[i for i,e in enumerate(reference) if i not in used and e['economic_variable']==raw['economic_variable']]
            target=next((i for i in eligible if all(raw[k]==reference[i][k] for k in FIELDS)),eligible[0] if eligible else None)
            correct=False
            if target is not None:
                used.add(target);matched+=1;e=reference[target]
                for k in FIELDS:fields[k]+=raw[k]==e[k]
                correct=all(raw[k]==e[k] for k in FIELDS)
                raw_correct+=correct
            if p['decision']=='SUPPORTED':
                allowed=target is not None and reference[target].get('should_support',True)
                supported_correct+=bool(correct and allowed);false+=not(correct and allowed)
    supported=stats['decisions']['SUPPORTED'];candidates=len(all_props)
    stats.update(expected_propositions=expected,raw_exact=raw_correct,raw_extraction_precision=raw_correct/candidates if candidates else None,
        raw_extraction_recall=matched/expected if expected else None,field_accuracy={k:n/expected if expected else None for k,n in fields.items()},
        entire_proposition_accuracy=raw_correct/expected if expected else None,
        supported_correct=supported_correct,precision=supported_correct/supported if supported else None,
        answerable_propositions=answerable,recall=supported_correct/answerable if answerable else None,false_approvals=false,
        unnecessary_abstention=(answerable-supported_correct)/answerable if answerable else None,
        contract_acceptance=min(contracts/tasks,1) if tasks else None)
    passed=bool(expected and supported) and stats['precision']>=THRESHOLDS['minimum_supported_precision'] and stats['recall']>=THRESHOLDS['minimum_supported_recall'] and false==0 and stats['unnecessary_abstention']<=THRESHOLDS['maximum_unnecessary_abstention'] and stats['contract_acceptance']>=THRESHOLDS['minimum_contract_acceptance'] and all(v>=THRESHOLDS['minimum_field_accuracy'] for v in stats['field_accuracy'].values())
    stats['quality_status']='QUALITY_GATE_PASSED_AUTHORED_ONLY' if passed and value['execution_status']=='ACTUAL_MODEL_EXECUTED' else 'FAILED' if value['request_attempts'] else 'NOT_RUN'
    return stats
