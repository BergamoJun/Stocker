"""Compatible v1 receipt input; independent source semantics are rechecked."""
from research.sec_transport import now
from extensions.sec_evidence_v1 import reviews as original
from extensions.sec_evidence_v1 import contract, verify_seal
from . import semantic_validation

empty_trust = original.empty_trust
load_trust = original.load_trust
template = original.template


def check_terms(manifest, result):
    contract.validate(manifest, 'manifest'); verify_seal(manifest)
    original.check_terms(manifest, result)
    assessment = semantic_validation.check(manifest, result)
    if not assessment['supported']:
        raise ValueError('SEMANTIC_SUPPORT_REQUIRED: ' + ','.join(i['code'] for i in assessment['issues']))
    return assessment


def store(manifest, result, root, *, clock=now):
    # Original store still validates current incarnation, method, namespace and time.
    if manifest.get('decision') == 'APPROVE': check_terms(manifest, result)
    return original.store(manifest, result, root, clock=clock)
