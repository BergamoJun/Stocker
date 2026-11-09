"""ILLUSTRATIVE - test wrong model approvals separately from deterministic blocks."""
from copy import deepcopy
import pytest
from extensions import impact_quality as quality, impact_critic as critic, issuer_impact as impact


class CorrectBackend:
    calls=0
    def ask(self,messages,spec):
        import json
        self.calls+=1; v=json.loads(messages[1]['content'])
        if 'DRAFT' in v:
            bad=critic.errors(v['INPUT'],v['DRAFT'])
            return {'verdict':'unsupported' if bad else 'conditional_only','reason':'source_or_fact_error' if bad else 'exposure_unverified'},{}
        return {'route_id':v['CANDIDATES'][0]['route_id'],'quote':v['INPUT']['classification']['quote']},{}


@pytest.mark.parametrize('case',[c for c in quality.cases() if not c['safety']],ids=lambda c:c['id'])
def test_registered_valid_and_adversarial_drafts(case):
    errors=critic.errors(case['input'],case['critic_draft'])
    assert (not errors)==case['critic_should_allow']


def test_guard_cannot_be_overridden_by_model_agreement():
    case=quality.cases()[6]
    raw={'verdict':'conditional_only','reason':'exposure_unverified'}
    r=critic.finish(case['input'],case['critic_draft'],raw,{})
    assert r['status']=='withheld' and r['deterministic_errors']
    assert r['raw']['verdict']=='conditional_only'


def test_development_gate_replays_counts():
    backend=CorrectBackend(); result=quality.evaluate(backend)
    assert backend.calls==24
    assert result['counts']['raw_critic_correct']==12
    assert quality.validate(result)
    result['counts']['research_correct']=0
    from research.validation import stable_hash
    result['record_sha256']=stable_hash({k:v for k,v in result.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='DECISION_CHANGED'): quality.validate(result)


def test_raw_false_approval_fails_gate_even_when_guard_blocks():
    class WrongCritic(CorrectBackend):
        def ask(self,msg,spec):
            if 'DRAFT' in msg[1]['content']:
                self.calls+=1
                return {'verdict':'conditional_only','reason':'exposure_unverified'},{}
            return super().ask(msg,spec)
    result=quality.evaluate(WrongCritic())
    assert result['counts']['raw_false_approvals']==6
    assert not quality.validate(result)


def test_quote_elsewhere_in_paragraph_is_not_classification_citation():
    c=deepcopy(quality.cases()[0]); c['input']['source']['text']+=' An unrelated statement appears here.'
    import hashlib
    c['input']['source']['paragraph_sha256']=hashlib.sha256(c['input']['source']['text'].encode()).hexdigest()
    c['critic_draft']['quote']='An unrelated statement appears here.'
    assert critic.errors(c['input'],c['critic_draft'])==['quote_mismatch']


def test_unrun_experiment_cannot_be_relabelled_passed():
    r=quality.pending('LOCAL_MODEL_WAITING_FOR_MEMORY',available_memory_bytes=1000000000)
    assert not quality.validate(r) and r['counts'] is None
    r['decision']='conditional_use_allowed'
    from research.validation import stable_hash
    r['record_sha256']=stable_hash({k:v for k,v in r.items() if k!='record_sha256'})
    with pytest.raises(ValueError,match='PENDING_QUALITY_CHANGED'): quality.validate(r)
