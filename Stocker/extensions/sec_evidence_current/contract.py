"""Untrusted request and replayable verification binding; never version fallback."""
import jsonschema
from extensions.financial_impact_v1 import contract as i
from . import VERIFIER,Rejected
o,n,a=i.obj,i.nullable,i.array
OPTIONS=o({'source_store':n(i.TEXT),'legacy_review_root':n(i.TEXT),'review_store':n(i.TEXT),'cache_root':n(i.TEXT),
    'max_age_days':{'type':'integer','minimum':1,'maximum':3650},'max_filings':{'type':'integer','minimum':1,'maximum':8},
    'maximum_candidates':{'type':'integer','minimum':1,'maximum':2048}})
WINDOW=o({'start':i.DATE,'end':i.DATE})
REQUEST=o({'contract':{'const':'sec-evidence-current-request-v1'},'verification_version':{'const':VERIFIER},
    'mode':{'enum':['REAL','ILLUSTRATIVE']},'cik':i.CIK,'ticker':i.TICKER,'as_of':i.TIME,
    'calculation_window':WINDOW,'event_input':n(i.INPUT),'instrument_id':n(i.ID),'source_options':OPTIONS})
CERTIFICATE=o({'contract':{'const':'sec-current-verification-v1'},'verification_version':{'const':VERIFIER},
    'facade_methods':{'type':'object','additionalProperties':i.HASH},'request_sha256':i.HASH,
    'result_sha256':i.HASH,'owner_binding_sha256':i.HASH,'trust_sha256':i.HASH,'event_sha256':i.HASH,
    'source_binding_sha256':i.HASH,'as_of':i.TIME,'calculation_window':WINDOW,'record_sha256':i.HASH})


def validate(value,kind='request'):
    if kind=='request':
        if not isinstance(value,dict):raise Rejected('REQUEST_CONTRACT_INVALID')
        version=value.get('verification_version')
        if version is None:raise Rejected('VERIFICATION_VERSION_REQUIRED')
        if version in {'v1','sec-evidence-adapter-v1'}:raise Rejected('LEGACY_VERSION_FORBIDDEN')
        if version!=VERIFIER:raise Rejected('UNSUPPORTED_VERIFICATION_VERSION')
    try:
        jsonschema.Draft202012Validator(REQUEST if kind=='request' else CERTIFICATE,
            format_checker=jsonschema.FormatChecker()).validate(value)
    except jsonschema.ValidationError as exc:
        raise Rejected('REQUEST_CONTRACT_INVALID' if kind=='request' else 'CURRENT_CERTIFICATE_REQUIRED') from exc
