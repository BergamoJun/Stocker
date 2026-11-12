"""v1 input/receipt schemas unchanged; new output has an explicit version."""
from copy import deepcopy
import jsonschema
from extensions.sec_evidence_v1 import contract as original
from extensions.financial_impact_v1 import contract as i
from .semantic_validation import TYPES

o,n,a = i.obj,i.nullable,i.array
WINDOW=o({'start':i.DATE,'end':i.DATE})
OBSERVATION=o({'kind':{'enum':['loan','reset','period','no_hedge','hedge']},'source_id':i.HASH,
    'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1},'attributes':{'type':'object','additionalProperties':i.TEXT}})
ASSESSMENT=o({'status':{'enum':['SEMANTIC_RULE_VALIDATED','HUMAN_REVIEW_REQUIRED']},'supported':i.BOOL,
    'issues':a(o({'code':i.ID,'detail':i.TEXT})),
    'relationships':a(o({'semantic_type':{'enum':TYPES},'source':OBSERVATION})),
    'contract_period':n(WINDOW),'hedge_period':n(WINDOW),'annual_reset':i.BOOL,'reset_date':i.DATE})
ELIGIBILITY=o({'evidence_verified':i.BOOL,'calculation_eligible':i.BOOL,'eligibility_as_of':i.TIME,
    'calculation_window':WINDOW,'eligibility_reason':a(i.TEXT)})
ROW=o({'instrument_id':i.ID,'review_receipt_hashes':a(i.HASH),'semantic':ASSESSMENT,'eligibility':ELIGIBILITY})
CONFIG=o({'cik':i.CIK,'ticker':i.TICKER,'as_of':i.TIME,'source_store':n(i.TEXT),'legacy_review_root':n(i.TEXT),
    'review_store':n(i.TEXT),'cache_root':n(i.TEXT),'illustrative':i.BOOL,'max_age_days':{'type':'integer'},
    'maximum_candidates':{'type':'integer'},'max_filings':{'type':'integer'},'calculation_window':WINDOW,'event_effective':n(i.DATE)})
RESULT=deepcopy(original.RESULT)
RESULT['properties']['contract']={'const':'sec-evidence-adapter-v1.1'}
RESULT['properties'].update(historical_evidence=a(original.VERIFIED),semantic_assessments=a(ROW),
    eligibility_window=WINDOW,configuration=CONFIG,legacy_discovery_sha256=i.HASH)
RESULT['required']=list(RESULT['properties'])


def validate(value):
    jsonschema.Draft202012Validator(RESULT,format_checker=jsonschema.FormatChecker()).validate(value)
