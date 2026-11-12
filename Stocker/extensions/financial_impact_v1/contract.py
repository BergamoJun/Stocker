"""Closed, versioned JSON contracts; no missing numeric value is defaulted."""
import jsonschema
from .models import BINDINGS, STATUSES, RELEASE_FIELDS


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def nullable(value):
    return {'anyOf':[value,{'type':'null'}]}


def array(value):
    return {'type':'array','items':value}


TEXT={'type':'string','minLength':1,'maxLength':4000}
ID={'type':'string','pattern':r'^[A-Za-z0-9_.:-]{1,160}$'}
HASH={'type':'string','pattern':'^[0-9a-f]{64}$'}
NUMBER={'type':'string','pattern':r'^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$','maxLength':70}
DATE={'type':'string','format':'date'}
TIME={'type':'string','format':'date-time'}
BOOL={'type':'boolean'}
CIK={'type':'string','pattern':'^[0-9]{10}$'}
TICKER={'type':'string','pattern':r'^[A-Z][A-Z0-9.\-]{0,15}$'}
CURRENCY={'type':'string','pattern':'^[A-Z]{3}$'}
PERIOD=obj({'start':nullable(DATE),'end':DATE,'basis':{'enum':['DURATION','INSTANT']}})
DELTA=obj({'value':NUMBER,'unit':{'enum':['bp','percentage_point','decimal_rate']}})
VERIFICATION={'enum':['VERIFIED','UNVERIFIED','ILLUSTRATIVE','CONFLICTING']}
SOURCE=obj({'source_id':ID,'url':TEXT,'original_sha256':HASH,'text':{'type':'string','maxLength':2000000},
    'published_at':nullable(TIME),'available_at':nullable(TIME),'is_illustrative':BOOL,
    'issuer':nullable(obj({'cik':CIK,'ticker':TICKER})),
    'rights':obj({'local_calculation':BOOL,'redistribution':{'const':False}})})
REVIEW=obj({'reviewer_id':ID,'reviewed_at':TIME,'source_sha256':HASH,'fact_sha256':HASH,'record_sha256':HASH})
FACT=obj({'fact_id':ID,'source_id':ID,'original_sha256':HASH,'kind':{'enum':list(BINDINGS.values())+['event_delta','event_effective_date']},
    'value':{'anyOf':[TEXT,BOOL,{'type':'null'}]},'unit':TEXT,'currency':nullable(CURRENCY),
    'cik':nullable(CIK),'ticker':nullable(TICKER),'instrument_id':nullable(ID),'accounting_scope':nullable(TEXT),'reporting_period':nullable(PERIOD),
    'valid_from':nullable(DATE),'valid_to':nullable(DATE),'available_at':nullable(TIME),
    'exact_quote':TEXT,'start_offset':{'type':'integer','minimum':0},'end_offset':{'type':'integer','minimum':1},
    'verification_status':VERIFICATION,'review':nullable(REVIEW),'event_reference':nullable(TEXT),'rate_currency':nullable(CURRENCY),
    'rate_link':nullable(obj({'benchmark':TEXT,'event_variable':TEXT,'event_reference':TEXT,'rate_currency':CURRENCY,'minimum_delta_bp':NUMBER,'maximum_delta_bp':NUMBER}))})
CONTEXT=obj({'cik':CIK,'ticker':TICKER,'as_of':TIME,'currency':CURRENCY,'accounting_scope':TEXT,
    'reporting_period':PERIOD,'calculation_start':DATE,'calculation_end':DATE})
EVENT=obj({'event_id':ID,'canonical_event_id':ID,'event_type':TEXT,'economic_variable':TEXT,'rate_reference':nullable(TEXT),'rate_currency':nullable(CURRENCY),
    'delta':nullable(DELTA),'effective_date':nullable(DATE),'magnitude_fact_id':nullable(ID),'effective_fact_id':nullable(ID),
    'verification_status':VERIFICATION,'uncertainties':array(TEXT)})
EXPOSURE=obj({'exposure_id':ID,'instrument_id':ID,'cik':CIK,'ticker':TICKER,'currency':CURRENCY,'accounting_scope':TEXT,
    'reporting_period':PERIOD,'verification_status':VERIFICATION,'bindings':obj({k:nullable(ID) for k in BINDINGS})})
ASSUMPTIONS=obj({'scenario_id':ID,'label':{'enum':['Base','Bull','Bear','Custom']},'rate_delta_override':nullable(DELTA),
    'description':TEXT,'valid_if':array(TEXT),'invalid_if':array(TEXT),'probability':{'type':'null'}})
POLICY=obj({'version':{'const':'financial-impact-policy-v1'},'day_count':nullable(TEXT),'precision':{'const':50},
    'rounding':{'const':'ROUND_HALF_EVEN'},'max_source_age_days':{'type':'integer','minimum':0,'maximum':3650},
    'trusted_review_hashes':array(HASH)})
PRIOR=obj({'cik':CIK,'canonical_event_id':ID,'instrument_id':ID,'start':DATE,'end':DATE,'scenario_fingerprint':HASH})
ORIGIN=obj({'source_input_path':TEXT,'source_input_sha256':HASH,'exposure_ledger_path':TEXT,'exposure_ledger_sha256':HASH})
INPUT=obj({'contract':{'const':'financial-impact-input-v1'},'mode':{'enum':['ILLUSTRATIVE','REAL']},
    'context':CONTEXT,'event':EVENT,'exposure':EXPOSURE,'assumptions':ASSUMPTIONS,'policy':POLICY,
    'sources':array(SOURCE),'facts':array(FACT),'prior_calculations':array(PRIOR),
    'context_facts':array({'type':'object'}),'origin':nullable(ORIGIN)})
ISSUE=obj({'code':ID,'status':{'enum':list(STATUSES[1:])},'detail':TEXT})
OUTPUT=obj({'contract':{'const':'financial-impact-output-v1'},'created_at':TIME,'input_sha256':HASH,
    'methods':{'type':'object','additionalProperties':HASH},'mode':{'const':'SHADOW'},
    'status':{'enum':list(STATUSES)},'evidence_status':{'enum':['ILLUSTRATIVE','EVIDENCE_VERIFIED','INSUFFICIENT_EVIDENCE']},
    'calculation_basis':{'enum':['SCENARIO_ONLY','CONDITIONAL_EVIDENCE_INPUTS']},
    'issuer':nullable(obj({'cik':CIK,'ticker':TICKER})),'event_id':nullable(ID),'scenario_id':nullable(ID),
    'amount':nullable({**NUMBER,'maxLength':150}),'currency':nullable(CURRENCY),'metric':{'const':'interest_expense_delta_pre_tax'},
    'calculation':nullable({'type':'object'}),'issues':array(ISSUE),'conditions':array(TEXT),'invalidations':array(TEXT),
    'countereffects':array(TEXT),'semantic_market_accuracy':{'const':'NOT_MEASURED'},
    'release':obj({k:{'type':'null'} for k in RELEASE_FIELDS}),'record_sha256':HASH})


def validate(value,kind='input'):
    schema={'input':INPUT,'output':OUTPUT}[kind]
    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(value)
