"""ILLUSTRATIVE - source contracts and model/rule outcomes tested independently."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from extensions import critic_v2 as c
from research.sec_report import ROOT

CASE=ROOT/'reports/private/reliability_case/20261110/priority1'
def inputs(): return json.loads((CASE/'inputs.json').read_text(encoding='utf-8'))
def answers(): return {x['id']:x for x in json.loads((CASE/'answers.private.json').read_text(encoding='utf-8'))}


@pytest.mark.parametrize('sample',inputs(),ids=lambda x:x['id'])
def test_frozen_regression_and_adversarial_requirements(sample):
    g=answers()[sample['id']]; decision=c.deterministic(sample['context'],sample['claim'])
    if g['split']=='model_blind': assert decision==g['decision']
    else: assert (decision in {'CONDITIONAL','FACT_VERIFIED'})==g['should_allow']


def test_model_wrong_approval_is_preserved_and_blocked():
    x=next(x for x in inputs() if x['id']=='blind_05')
    result=c.finish(x['context'],x['claim'],{'decision':'CONDITIONAL'},{})
    assert result['raw']=={'decision':'CONDITIONAL'}
    assert result['deterministic_decision']=='BAD_FACT_ID'
    assert result['rule_blocked_raw_approval'] and result['final_status']=='withheld'


def test_legacy_contradictory_two_fields_are_rejected():
    x=inputs()[0]
    import jsonschema
    with pytest.raises(jsonschema.ValidationError): c.finish(x['context'],x['claim'],{'verdict':'conditional_only','reason':'source_or_fact_error'},{})


def test_all_decision_reason_relationships_are_explicit_and_noncontradictory():
    p=c.policy()['decision_relations']
    assert set(p)==set(c.spec('raw')['properties']['decision']['enum'])
    assert {k for k,v in p.items() if v['verdict']=='conditional_only'}=={'CONDITIONAL'}
    assert c.project('BAD_FACT_ID')['verdict']=='unsupported'
    assert c.project('FACT_VERIFIED')['verdict']=='verified_fact_only'


def test_prompt_cannot_see_gold_or_rule_verdict():
    x=inputs()[0]; captured=[]
    class Fake:
        def ask(self,messages,schema):
            captured.extend(messages); return {'decision':'UNKNOWN'},{}
    c.review(x['context'],x['claim'],Fake())
    payload=json.loads(captured[1]['content'])
    assert set(payload)=={'CONTEXT','CLAIM'}
    assert not any(k in captured[1]['content'] for k in ['should_allow','deterministic_decision','answers.private'])


def test_vendor_fields_rejected_before_model():
    x=deepcopy(inputs()[0]); x['context']['market_price']='ILLUSTRATIVE_FORBIDDEN'
    class Never:
        def ask(self,*args): raise AssertionError('input must be rejected')
    with pytest.raises(ValueError): c.review(x['context'],x['claim'],Never())


def test_runtime_memory_failure_is_not_model_success():
    from extensions.local_inference import Deferred
    x=inputs()[0]
    class Failed:
        def ask(self,*args): raise Deferred('LOCAL_MODEL_WAITING_FOR_MEMORY')
    with pytest.raises(Deferred): c.review(x['context'],x['claim'],Failed())
