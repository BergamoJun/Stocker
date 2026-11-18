"""No new calculation from a legacy artifact or self-issued JSON certificate."""
from research.validation import stable_hash
from extensions.sec_evidence_v1_1 import bridge as hardened
from . import VERIFIER,Rejected,verify_seal,contract
from .owner import context


def calculate(request,result,certificate,*,owner=None,created_at=None):
    contract.validate(request)
    if not isinstance(result,dict) or result.get('contract')!=VERIFIER:raise Rejected('LEGACY_RESULT_NOT_CURRENT_APPROVAL')
    contract.validate(certificate,'certificate');verify_seal(certificate)
    from .facade import authorize
    fresh=authorize(request,owner=owner)
    if result!=fresh['result'] or certificate!=fresh['certificate']:
        raise Rejected('CURRENT_AUTHORIZATION_REPLAY_MISMATCH')
    trust=context(owner,request['mode']=='ILLUSTRATIVE').trust(request['mode']=='ILLUSTRATIVE')
    return hardened.calculate(fresh['result'],trust,event_input=request['event_input'],
        instrument_id=request['instrument_id'],created_at=created_at)
