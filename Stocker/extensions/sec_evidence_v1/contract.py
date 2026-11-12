"""Closed versioned adapter/review/trust contracts. Impact schema stays frozen."""
import jsonschema
from extensions.financial_impact_v1 import contract as i
from extensions.financial_impact_v1.models import BINDINGS
from .models import STATES,METRICS

o,n,a=i.obj,i.nullable,i.array
LOCATOR=o({'kind':{'enum':['raw_character_span','inline_xbrl','json_path']},
    'start':n({'type':'integer','minimum':0}),'end':n({'type':'integer','minimum':1}),
    'ordinal':n({'type':'integer','minimum':1}),'json_path':n(a({'anyOf':[{'type':'string'},{'type':'integer','minimum':0}]}))})
SOURCE=o({'source_key':i.HASH,'source_id':i.ID,'cik':i.CIK,'ticker':i.TICKER,'accession':i.TEXT,
    'form':i.TEXT,'filed_date':i.DATE,'published_at':n(i.TIME),'available_at':i.TIME,'raw_path':i.TEXT,
    'source_url':i.TEXT,'original_sha256':i.HASH,'incarnation_id':i.HASH,'binding':{'type':'object'},
    'kind':{'enum':['document','companyfacts']},'derived_text_sha256':n(i.HASH),
    'transformation':n(i.TEXT),'is_illustrative':i.BOOL,'amendment_pending':i.BOOL})
CANDIDATE=o({'candidate_id':i.HASH,'source_key':i.HASH,'cik':i.CIK,'ticker':i.TICKER,'accession':i.TEXT,
    'metric':{'enum':METRICS},'origin':{'enum':['INLINE_XBRL','COMPANYFACTS','NOTE']},
    'taxonomy':{'enum':['STANDARD','CUSTOM','TEXT']},'tag':n(i.TEXT),'value':n(i.TEXT),'unit':n(i.TEXT),
    'currency':n(i.CURRENCY),'reporting_basis':n(i.TEXT),'accounting_scope':n(i.TEXT),
    'effective_period':n({'type':'object'}),'contractual_period':n({'type':'object'}),'instrument_id':n(i.ID),
    'interest_rate_index':n(i.TEXT),'repricing_conditions':n(i.TEXT),'hedge_relationship':n(i.TEXT),
    'locator':LOCATOR,'exact_quote':n(i.TEXT),'derived_quote':n(i.TEXT),'derived_offsets':n(o({'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1}})),
    'parser_fact':n({'type':'object'}),'available_at':i.TIME,'reviewed_at':n(i.TIME),'as_of':i.TIME,
    'status':{'enum':STATES},'source_bound':i.BOOL,'promotion_eligible':i.BOOL,'flags':a(i.TEXT)})
WITNESS=o({'candidate_id':i.HASH,'raw_start':{'type':'integer','minimum':0},'raw_end':{'type':'integer','minimum':1}})
TERMS=o({'rate_type':{'enum':['FLOATING','FIXED','UNKNOWN']},'exposure':i.EXPOSURE,'facts':a(i.FACT),
    'witnesses':o({k:WITNESS for k in BINDINGS})})
MANIFEST=o({'contract':{'const':'sec-contract-review-manifest-v1'},'is_illustrative':i.BOOL,
    'reviewer_id':n(i.ID),'reviewer_kind':{'enum':['human_original_filing_review','assistant_original_filing_review','ILLUSTRATIVE_TEST_REVIEW']},
    'reviewed_at':n(i.TIME),'decision':{'enum':['APPROVE','REJECT','UNRESOLVED']},'complete':i.BOOL,
    'binding':{'type':'object'},'terms':n(TERMS),'rationale':i.TEXT,
    'methods':{'type':'object','additionalProperties':i.HASH},'record_sha256':i.HASH})
RECEIPT=o({'contract':{'const':'sec-contract-review-receipt-v1'},'manifest':MANIFEST,
    'available_at':i.TIME,'store_order':{'type':'integer','minimum':1},'record_sha256':i.HASH})
TRUST=o({'contract':{'const':'sec-evidence-trust-v1'},'is_illustrative':i.BOOL,
    'trusted_review_hashes':a(i.HASH),'independent_reviewer_ids':a(i.ID),'trusted_event_review_hashes':a(i.HASH),
    'owner_id':n(i.ID),'available_at':n(i.TIME)})
VERIFIED=o({'instrument_id':i.ID,'exposure':i.EXPOSURE,'facts':a(i.FACT),'witnesses':{'type':'object'},
    'review_receipt_hashes':a(i.HASH),'is_illustrative':i.BOOL,'status':{'const':'VERIFIED'}})
MISSING=o({'field':i.TEXT,'reason':i.TEXT,'source_links':a(i.TEXT)})
CONFLICT=o({'code':i.TEXT,'candidate_ids':a(i.HASH),'resolution':i.TEXT})
RESULT=o({'contract':{'const':'sec-evidence-adapter-v1'},'identity':o({'cik':i.CIK,'ticker':i.TICKER}),
    'as_of':i.TIME,'is_illustrative':i.BOOL,'status':{'enum':STATES},'methods':{'type':'object','additionalProperties':i.HASH},
    'sources':a(SOURCE),'candidates':a(CANDIDATE),'reviewed_financial_facts':a({'type':'object'}),
    'verified_exposures':a(VERIFIED),'missing_evidence':a(MISSING),'conflicts':a(CONFLICT),
    'statistics':{'type':'object'},'limitations':a(i.TEXT),'source_store':i.TEXT,'review_store':i.TEXT,'record_sha256':i.HASH})


def validate(value,kind):
    schema={'source':SOURCE,'candidate':CANDIDATE,'terms':TERMS,'manifest':MANIFEST,'receipt':RECEIPT,'trust':TRUST,'result':RESULT}[kind]
    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(value)
