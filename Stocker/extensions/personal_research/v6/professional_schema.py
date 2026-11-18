"""Bound references and values; natural-language economic meaning still needs review."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
import json
import re
import unicodedata
import jsonschema
from .professional_packet import public_text_check

CLAIM_TYPES=['OBSERVED_FACT','PYTHON_CALCULATED','COMPANY_DISCLOSED_EXPECTATION','ANALYST_INFERENCE','CONDITIONAL_HYPOTHESIS','UNKNOWN']
SECTIONS=['EXECUTIVE_SUMMARY','COMPANY_OVERVIEW','INVESTMENT_THESIS','BUSINESS_ECONOMICS','FINANCIAL_PERFORMANCE',
          'CASH_FLOW_QUALITY','CAPITAL_ALLOCATION','INDUSTRY_CYCLICALITY','VALUATION_SCENARIO','BULL_CASE','BEAR_CASE',
          'COUNTER_THESIS','KEY_RISKS','FINAL_ASSESSMENT']
SCOPES=['CONSOLIDATED_COMPANY','SEGMENT','BUSINESS_UNIT','PRODUCT','CUSTOMER','INDUSTRY','UNKNOWN']


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def schema(packet):
    text={'type':'string'};ids=[e['evidence_id'] for e in packet['evidence']]
    identifier={'type':'string','enum':ids} if ids else text
    refs={'type':'array','items':identifier}
    numeric=obj({'evidence_id':identifier,'value':text,'unit':text})
    claim=obj({'claim_id':text,'section':{'type':'string','enum':SECTIONS},'claim_type':{'type':'string','enum':CLAIM_TYPES},
       'statement':text,'economic_mechanism':text,'condition':text,'invalidation':text,'uncertainty':text,
       'scope':{'type':'string','enum':SCOPES},'evidence_ids':refs,'numeric_references':{'type':'array','items':numeric}})
    monitor=obj({'indicator':text,'source_or_method':text,'strengthening':text,'weakening':text,'economic_reason':text,'evidence_ids':refs})
    return obj({'ticker':{'type':'string','enum':[packet['company']['ticker']]},
       'issuer_cik':{'type':'string','enum':[packet['company']['cik']]},
       'analysis_as_of':{'type':'string','enum':[packet['analysis_as_of']]},'model':{'type':'string','enum':['gpt-6.1-sol']},
       'claims':{'type':'array','items':claim},'monitoring_framework':{'type':'array','items':monitor},
       'uncertainties':{'type':'array','items':text},'data_limitations':{'type':'array','items':text},
       'investment_judgment':{'type':'string','enum':['CONDITIONAL','MIXED','INSUFFICIENT_EVIDENCE']}})


def prose_issues(text,packet):
    public_text_check(text)
    normalized=unicodedata.normalize('NFKC',text)
    # Python fills {{EvidenceID}} placeholders; no LLM arithmetic, dates or invented quantities.
    stripped=re.sub(r'\{\{E\d+\}\}|\bE\d+\b','',normalized)
    for product in ['Microsoft 365','Microsoft365','Office 365','Office365']:stripped=stripped.replace(product,'PRODUCT')
    issues=[]
    if any(c.isnumeric() for c in stripped):issues.append('UNBOUND_NUMERIC_LITERAL_USE_EVIDENCE_PLACEHOLDER')
    references=re.findall(r'\{\{(E\d+)\}\}',normalized)
    known={e['evidence_id'] for e in packet['evidence']}
    if any(i not in known for i in references):issues.append('INVALID_EVIDENCE_PLACEHOLDER')
    if re.search(r'(?i)(?:목표[주]?가|매수\s*신호|확정\s*수익|수익\s*보장|target price|guaranteed return|scenario probability)',normalized):
        issues.append('UNSUPPORTED_TARGET_OR_GUARANTEE')
    return issues


def assess(response,packet):
    result={'schema_status':'PASS','rows':[],'monitoring':[],'verified_claims':[],
            'independent_semantic_accuracy':'NOT_MEASURED','economic_usefulness':'NOT_MEASURED'}
    try:jsonschema.validate(response,schema(packet))
    except jsonschema.ValidationError:
        return {**result,'schema_status':'FAIL','status':'JSON_SCHEMA_FAILURE','drafts':[],'unresolved':[]}
    entries={e['evidence_id']:e for e in packet['evidence']};seen=set();drafts=[];unresolved=[]
    def scan(text):
        try:return prose_issues(text,packet)
        except ValueError:return ['OUTPUT_PRIVACY_OR_INSTRUCTION_VIOLATION']
    for c in response['claims']:
        issues=[];refs=[entries[i] for i in c['evidence_ids'] if i in entries]
        if not re.fullmatch(r'C\d{3}',c['claim_id']) or c['claim_id'] in seen:issues.append('CLAIM_ID_INVALID_OR_DUPLICATE')
        seen.add(c['claim_id'])
        if c['claim_type']!='UNKNOWN' and not refs:issues.append('EVIDENCE_REQUIRED')
        for k in ['statement','economic_mechanism','condition','invalidation','uncertainty']:issues+=scan(c[k])
        if not c['statement'].strip():issues.append('EMPTY_CLAIM')
        for n in c['numeric_references']:
            e=entries.get(n['evidence_id'])
            try:valid=e is not None and e['evidence_id'] in c['evidence_ids'] and e['value'] is not None and Decimal(n['value']).is_finite() and Decimal(n['value'])==Decimal(e['value']) and n['unit']==e['unit']
            except (InvalidOperation,TypeError):valid=False
            if not valid:issues.append('NUMERIC_REFERENCE_MISMATCH')
        for identifier in re.findall(r'\{\{(E\d+)\}\}',json.dumps(c,ensure_ascii=False)):
            if identifier not in c['evidence_ids']:issues.append('PLACEHOLDER_NOT_IN_SELECTED_EVIDENCE')
        if c['claim_type'] in {'OBSERVED_FACT','PYTHON_CALCULATED'}:
            # The model selects an exact fact; Python, not free prose, describes financial values.
            if not refs or c['statement'] not in ['{{'+e['evidence_id']+'}}' for e in refs]:issues.append('OBSERVED_FACT_REQUIRES_CANONICAL_EVIDENCE_SELECTION')
            for e in refs:
                if c['scope'] not in {e['financial_scope'],e['business_scope']}:issues.append('SOURCE_SCOPE_MISMATCH')
                if c['claim_type']=='PYTHON_CALCULATED' and e['observation_type']!='DERIVED':issues.append('NOT_A_PYTHON_CALCULATION')
                if c['claim_type']=='OBSERVED_FACT' and e['observation_type'] not in {'OBSERVED','SOURCE_FACT'}:issues.append('NOT_AN_OBSERVED_FACT')
        if c['claim_type']=='COMPANY_DISCLOSED_EXPECTATION' and not any(e['observation_type']=='COMPANY_DISCLOSED_EXPECTATION' for e in refs):
            issues.append('NO_COMPANY_GUIDANCE_EVIDENCE')
        if c['section']=='VALUATION_SCENARIO' and packet['valuation']['values_provided'] is False:
            # A qualitative missing-data discussion is allowed; never certify an unseen valuation.
            if c['claim_type'] in {'OBSERVED_FACT','PYTHON_CALCULATED'}:issues.append('VALUATION_VALUES_NOT_PROVIDED')
        issues=sorted(set(issues));row={'claim_id':c['claim_id'],'hard_status':'HARD_REJECT' if issues else 'HARD_PASS',
            'issues':issues,'semantic_status':'NOT_EVALUATED' if issues else 'SEMANTIC_REVIEW_NEEDED'}
        result['rows'].append(row)
        if issues:unresolved.append(c['claim_id'])
        else:drafts.append(deepcopy(c))
    for m in response['monitoring_framework']:
        problems=[issue for k in ['indicator','source_or_method','strengthening','weakening','economic_reason'] for issue in scan(m[k])]
        if not m['evidence_ids']:problems.append('EVIDENCE_REQUIRED')
        if any(identifier not in m['evidence_ids'] for identifier in re.findall(r'\{\{(E\d+)\}\}',json.dumps(m,ensure_ascii=False))):
            problems.append('PLACEHOLDER_NOT_IN_SELECTED_EVIDENCE')
        if any(not m[k].strip() for k in ['indicator','source_or_method','strengthening','weakening','economic_reason']):problems.append('INCOMPLETE_MONITORING')
        result['monitoring'].append({'value':deepcopy(m),'hard_status':'HARD_REJECT' if problems else 'HARD_PASS','issues':sorted(set(problems))})
    global_issues=[issue for text in response['uncertainties']+response['data_limitations'] for issue in scan(text)]
    result.update(drafts=drafts,unresolved=unresolved,global_issues=sorted(set(global_issues)),
                  status='AI_RESEARCH_DRAFT' if drafts and not global_issues else 'DETERMINISTIC_FALLBACK')
    if global_issues:result['drafts']=[]
    return result
