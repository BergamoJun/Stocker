"""ILLUSTRATIVE - no model or paid API; gate must retain raw failures."""
from copy import deepcopy
import pytest
from extensions import critic_v2 as c,critic_v2_eval as e
from extensions.tests.test_critic_v2 import inputs,answers,CASE
from research.validation import stable_hash


def rows_for(items):
    return [{'id':x['id'],'status':'model_completed','result':c.finish(x['context'],x['claim'],{'decision':c.deterministic(x['context'],x['claim'])},{})} for x in items]


def test_rule_blocks_do_not_rescue_raw_quality():
    items=inputs(); gold=list(answers().values()); rows=rows_for(items)
    bad=next(i for i,x in enumerate(items) if x['id']=='blind_05')
    rows[bad]['result']=c.finish(items[bad]['context'],items[bad]['claim'],{'decision':'CONDITIONAL'},{})
    r=e.summarize(items,gold,rows)
    assert r['raw_false_approvals']==1 and r['rule_blocks']==1 and r['decision']=='quality_gate_failed'


def test_frozen_registration_and_output_replay():
    items=inputs(); gold=list(answers().values()); rows=rows_for(items); registration=c.read(CASE/'PREREGISTRATION.json')
    value=e.receipt(items,gold,rows,registration,{'inference':'MOCK - NOT ACTUAL MODEL'})
    assert e.validate(value,items,gold,registration)
    changed=deepcopy(value); changed['metrics']['correct']=0
    changed['record_sha256']=stable_hash({k:v for k,v in changed.items() if k!='record_sha256'})
    with pytest.raises(ValueError): e.validate(changed,items,gold,registration)


def test_missing_cases_are_not_silently_dropped():
    items=inputs()
    with pytest.raises(ValueError,match='INCOMPLETE'): e.summarize(items,list(answers().values()),rows_for(items)[:-1])


def test_answers_never_relabelled_after_registration():
    items=inputs(); gold=list(answers().values()); rows=rows_for(items); registration=c.read(CASE/'PREREGISTRATION.json')
    gold[0]['should_allow']=not gold[0]['should_allow']
    with pytest.raises(ValueError,match='PREREGISTRATION_CHANGED'): e.receipt(items,gold,rows,registration,{})
