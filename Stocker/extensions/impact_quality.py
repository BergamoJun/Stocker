"""Registered development-only dual-prompt experiment; raw critic and guards differ."""
from decimal import Decimal
import jsonschema
from research.sec_transport import now
from research.validation import stable_hash
from . import issuer_impact as impact, impact_critic as critic


def cases(): return impact.read(impact.BASE/'impact_quality_cases.json')['cases']


def summarize(rows):
    expected={c['id']:c for c in cases()}
    if len(rows)!=len(expected) or {r['id'] for r in rows}!=set(expected): raise ValueError('IMPACT_QUALITY_CASE_SET_CHANGED')
    research_ok=critic_ok=accepted=false_approvals=safety_errors=0
    for row in rows:
        case=expected[row['id']]; v=case['input']
        if case['safety']:
            try: impact.validate_input(v); blocked=False
            except (ValueError,KeyError,jsonschema.ValidationError): blocked=True
            if row['blocked']!=blocked: raise ValueError('IMPACT_SAFETY_RESULT_CHANGED')
            safety_errors+=not blocked
            continue
        routes=impact.candidates(v)
        p=row['research']
        if p is not None:
            if critic.errors(v,p): raise ValueError('IMPACT_QUALITY_INVALID_RESEARCH_RECORD')
            accepted+=1; research_ok+=p['route_id'] in {r['route_id'] for r in routes}
        raw=row['critic_raw']
        if raw is not None:
            jsonschema.validate(raw,impact.schema('critic'))
            allow=raw['verdict']=='conditional_only'
            critic_ok+=allow==case['critic_should_allow']; false_approvals+=allow and not case['critic_should_allow']
    return {'dual_cases':sum(not c['safety'] for c in expected.values()),'safety_cases':sum(c['safety'] for c in expected.values()),'research_correct':research_ok,'raw_critic_correct':critic_ok,'research_contract_accepted':accepted,'raw_false_approvals':false_approvals,'safety_errors':safety_errors}


def passed(counts):
    q=impact.policy()['quality']; n=counts['dual_cases']
    return all(Decimal(counts[k])/n>=Decimal(q[t]) for k,t in [('research_correct','minimum_research_accuracy'),('raw_critic_correct','minimum_raw_critic_accuracy'),('research_contract_accepted','minimum_contract_acceptance')]) and counts['raw_false_approvals']<=q['maximum_false_approvals'] and counts['safety_errors']<=q['maximum_safety_errors']


def evaluate(backend,*,clock=now):
    rows=[]
    for case in cases():
        v=case['input']; row={'id':case['id']}
        if case['safety']:
            try: impact.validate_input(v); row['blocked']=False
            except (ValueError,KeyError,jsonschema.ValidationError): row['blocked']=True
        else:
            row.update(research=None,critic_raw=None,research_usage=None,critic_usage=None)
            try:
                p,u=impact.research(v,backend)
                if not critic.errors(v,p): row.update(research=p,research_usage=u)
            except (ValueError,KeyError,jsonschema.ValidationError): pass
            try:
                c=critic.review(v,case['critic_draft'],backend); row.update(critic_raw=c['raw'],critic_usage=c['usage'])
            except (ValueError,KeyError,jsonschema.ValidationError): pass
        rows.append(row)
    counts=summarize(rows)
    result={'contract':'issuer-impact-quality-v1','evaluated_at':clock(),'decision':'conditional_use_allowed' if passed(counts) else 'quality_gate_failed','counts':counts,'details':rows,'method_hashes':impact.methods(),'model_sha256':impact.policy()['model_sha256'],'scope':'self-authored development checks; no blind/general-news/price accuracy claim','external_inference_calls':0,'model_training_performed':False}
    result['record_sha256']=stable_hash(result)
    return result


def pending(reason_code, *, available_memory_bytes, clock=now):
    if reason_code not in {'LOCAL_MODEL_WAITING_FOR_MEMORY','LOCAL_MODEL_RUNTIME_BUSY','LOCAL_INFERENCE_TRANSIENT_FAILURE'}: raise ValueError('IMPACT_UNKNOWN_DEFER_REASON')
    result={'contract':'issuer-impact-quality-pending-v1','evaluated_at':clock(),'decision':'quality_not_run','counts':None,'details':[],'method_hashes':impact.methods(),'model_sha256':impact.policy()['model_sha256'],'reason_code':reason_code,'available_memory_bytes':available_memory_bytes,'minimum_available_memory_bytes':impact.runtime_settings()['minimum_available_memory_bytes'],'local_inference_calls':0,'external_inference_calls':0,'model_training_performed':False}
    result['record_sha256']=stable_hash(result)
    return result


def validate(v):
    if v['method_hashes']!=impact.methods() or v['model_sha256']!=impact.policy()['model_sha256'] or stable_hash({k:x for k,x in v.items() if k!='record_sha256'})!=v['record_sha256']: raise ValueError('IMPACT_QUALITY_CHANGED')
    if v['contract']=='issuer-impact-quality-pending-v1':
        if v['decision']!='quality_not_run' or v['counts'] is not None or v['details'] or v['local_inference_calls']!=0 or v['external_inference_calls']!=0 or v['model_training_performed'] is not False or v['minimum_available_memory_bytes']!=impact.runtime_settings()['minimum_available_memory_bytes'] or v['reason_code'] not in {'LOCAL_MODEL_WAITING_FOR_MEMORY','LOCAL_MODEL_RUNTIME_BUSY','LOCAL_INFERENCE_TRANSIENT_FAILURE'}: raise ValueError('IMPACT_PENDING_QUALITY_CHANGED')
        return False
    if v['contract']!='issuer-impact-quality-v1': raise ValueError('IMPACT_QUALITY_CONTRACT_UNKNOWN')
    counts=summarize(v['details']); ok=passed(counts)
    if counts!=v['counts'] or v['decision']!=('conditional_use_allowed' if ok else 'quality_gate_failed') or v['external_inference_calls']!=0 or v['model_training_performed'] is not False: raise ValueError('IMPACT_QUALITY_DECISION_CHANGED')
    return ok
