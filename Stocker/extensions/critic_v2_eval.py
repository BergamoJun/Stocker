"""Immutable model-blind evaluation with gold kept outside inference prompts."""
from decimal import Decimal
from pathlib import Path
import jsonschema
from research.local_files import atomic_json
from research.sec_transport import now
from research.validation import digest,stable_hash
from . import critic_v2 as critic


def register(inputs,answers,path,*,clock=now):
    if len(inputs)!=len(answers) or {c['id'] for c in inputs}!={c['id'] for c in answers}: raise ValueError('CRITIC_EVAL_CASE_SET_INVALID')
    for c in inputs: critic.validate_input(c['context'],c['claim'])
    value={'registered_at':clock(),'inputs_sha256':stable_hash(inputs),'answers_sha256':stable_hash(answers),'method_hashes':critic.methods(),'thresholds':critic.policy()['quality'],'input_ids':[c['id'] for c in inputs],'blinding':'gold hidden from model; developer-authored, not independent human adjudication','independent_market_events':0}
    value['record_sha256']=stable_hash(value); atomic_json(path,value); return value


def run_case(case,backend):
    try:
        r=critic.review(case['context'],case['claim'],backend)
        return {'id':case['id'],'status':'model_completed','result':r}
    except (ValueError,KeyError,jsonschema.ValidationError):
        return {'id':case['id'],'status':'invalid_model_or_input','result':None}


def summarize(inputs,answers,rows):
    lookup={c['id']:c for c in inputs}; gold={c['id']:c for c in answers}
    if len(rows)!=len(inputs) or {r['id'] for r in rows}!=set(lookup) or set(gold)!=set(lookup): raise ValueError('CRITIC_EVAL_RESULTS_INCOMPLETE')
    n=len(rows); exact=acceptance=wrong_approvals=blocked=raw_approval_errors=0
    positive={'FACT_VERIFIED','CONDITIONAL'}
    by_split={}
    for row in rows:
        g=gold[row['id']]; split=g['split']; s=by_split.setdefault(split,{'cases':0,'correct':0,'wrong_approvals':0,'rule_blocks':0,'completed':0}); s['cases']+=1
        if row['result'] is None: continue
        c=lookup[row['id']]; r=row['result']; rebuilt=critic.finish(c['context'],c['claim'],r['raw'],r['usage'])
        if r!=rebuilt: raise ValueError('CRITIC_FINAL_RESULT_CHANGED')
        acceptance+=1; s['completed']+=1
        ok=(r['raw']['decision'] in positive)==g['should_allow'] if split=='legacy_regression' else r['raw']['decision']==g['decision']
        exact+=ok; s['correct']+=ok
        false=r['raw']['decision'] in positive and not g['should_allow']; wrong_approvals+=false; s['wrong_approvals']+=false
        blocked+=r['rule_blocked_raw_approval']; s['rule_blocks']+=r['rule_blocked_raw_approval']
        raw_approval_errors+=r['raw']['decision'] in positive and r['raw']['decision']!=r['deterministic_decision']
    q=critic.policy()['quality']
    passed=Decimal(exact)/n>=Decimal(q['minimum_raw_critic_accuracy']) and Decimal(acceptance)/n>=Decimal(q['minimum_contract_acceptance']) and wrong_approvals<=q['maximum_false_approvals'] and all(Decimal(s['correct'])/s['cases']>=Decimal(q['minimum_raw_critic_accuracy']) and s['wrong_approvals']==0 for s in by_split.values())
    return {'cases':n,'correct':exact,'model_completed':acceptance,'raw_false_approvals':wrong_approvals,'rule_blocks':blocked,'raw_approval_disagreements':raw_approval_errors,'splits':by_split,'decision':'quality_gate_passed' if passed else 'quality_gate_failed'}


def receipt(inputs,answers,rows,registration,telemetry,*,clock=now):
    if registration['inputs_sha256']!=stable_hash(inputs) or registration['answers_sha256']!=stable_hash(answers) or registration['method_hashes']!=critic.methods() or registration['thresholds']!=critic.policy()['quality']: raise ValueError('CRITIC_PREREGISTRATION_CHANGED')
    value={'contract':'critic-v2-evaluation','evaluated_at':clock(),'method_hashes':critic.methods(),'registration_sha256':stable_hash(registration),'inputs_sha256':stable_hash(inputs),'answers_sha256':stable_hash(answers),'metrics':summarize(inputs,answers,rows),'rows':rows,'telemetry':telemetry,'external_calls':0,'training':False,'scope':'model-blind synthetic compliance only, not independent real-news or price validation'}
    value['record_sha256']=stable_hash(value); return value


def validate(value,inputs,answers,registration):
    if value['method_hashes']!=critic.methods() or value['registration_sha256']!=stable_hash(registration) or value['inputs_sha256']!=stable_hash(inputs) or value['answers_sha256']!=stable_hash(answers) or registration['thresholds']!=critic.policy()['quality'] or value['metrics']!=summarize(inputs,answers,value['rows']) or value['record_sha256']!=stable_hash({k:v for k,v in value.items() if k!='record_sha256'}): raise ValueError('CRITIC_EVALUATION_CHANGED')
    return value['metrics']['decision']=='quality_gate_passed'
