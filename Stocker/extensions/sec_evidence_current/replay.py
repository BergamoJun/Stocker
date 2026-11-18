"""Explicit legacy validation only; never return a current authorization."""
from . import Rejected


def validate_legacy(folder,version,*,expected_trust=None):
    if version=='v1':
        from extensions.sec_evidence_v1.report import validate
        proof=validate(folder,expected_trust=expected_trust)
    elif version=='v1.1':
        from extensions.sec_evidence_v1_1.report import validate
        proof=validate(folder,expected_trust=expected_trust)
    elif version=='impact-v1':
        from extensions.financial_impact_v1.report import validate
        proof=validate(folder)
    else:raise Rejected('EXPLICIT_LEGACY_REPLAY_VERSION_REQUIRED')
    return {'status':'LEGACY_REPLAY_PRESERVED','legacy_version':version,'current_approval':False,'proof':proof}
