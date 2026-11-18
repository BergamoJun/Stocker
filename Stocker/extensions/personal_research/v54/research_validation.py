"""Hard source/identity rules and advisory semantics have separate outcomes."""
import re
import jsonschema
from research.validation import stable_hash

ROLES=['THESIS','COUNTER_THESIS','MONITORING']

def schema(ctx):
    business=[e['evidence_id'] for e in ctx['evidence'] if e['evidence_type'] in ['BUSINESS','RISK']]
    finance=[e['evidence_id'] for e in ctx['evidence'] if e['evidence_type'] in ['FINANCIAL','DERIVED','HISTORY','SEGMENT']]
    ids=lambda es:{'type':'array','items':{'type':'string','enum':es or ['NO_ELIGIBLE_EVIDENCE']},'minItems':1,'maxItems':4,'uniqueItems':True}
    text={'type':'string','minLength':12,'maxLength':450,'pattern':'^[^0-9]*$'}
    def obj(props):return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    return obj({role:obj({'ticker':{'const':ctx['ticker']},'role':{'const':role},'business_evidence_ids':ids(business),'financial_evidence_ids':ids(finance),
        'assertion_kind':{'const':'CONDITIONAL_HYPOTHESIS'},**{k:text for k in ['claim','mechanism','condition','counter_thesis','strengthening','weakening','uncertainty']}}) for role in ROLES})

def sentences(text):return [s.strip() for s in re.split(r'[.!?。\n;]|하지만|그러나|반면|\bbut\b',text) if s.strip()]
def denied(s):return bool(re.search(r'아니다|아닙니다|않는다|않습니다|않으며|않음|않는|않은|않았|not |never |cannot|doesn.t',s,re.I))

def prose_checks(raw,evidence):
    issues=[];fields=[raw[k] for k in ['claim','mechanism','condition','counter_thesis','strengthening','weakening','uncertainty']]
    text=' '.join(fields);numeric=re.sub(r'Microsoft\s*365|마이크로소프트\s*365','PRODUCT_NAME',text,flags=re.I)
    if re.search(r'\d',numeric):issues.append('NUMERIC_OUTPUT_MUST_BE_RENDERED_BY_PYTHON')
    other={'MSFT':r'\bMSFT\b|Microsoft|마이크로소프트','MU':r'\bMU\b|Micron|마이크론','BE':r'(?-i:\bBE\b)|Bloom|블룸'}
    if any(re.search(p,text,re.I) for t,p in other.items() if t!=raw['ticker']):issues.append('CROSS_ISSUER_INTERPRETATION')
    if re.search(r'ignore (?:previous|all)|system prompt|이전.*지시.*무시',text,re.I):issues.append('OUTPUT_PROMPT_INJECTION')
    if re.search(r'목표주가|매수하|매도하|투자점수|기대수익률|시나리오.*확률|target price|scenario probability',text,re.I):issues.append('UNSUPPORTED_MARKET_OR_PROBABILITY_CLAIM')
    for s in [s for field in fields for s in sentences(field)]:
        if denied(s):continue
        if re.search(r'가이던스.*(?:발표|확정|제시했)|guidance.*(?:announced|confirmed)',s,re.I):issues.append('FABRICATED_GUIDANCE')
        if re.search(r'지속적으로.*(?:증가하고|개선되고)|이미.*(?:개선|감소|확대)|(?:OI|OCF|현금흐름|설비투자).*(?:증가하고 있|감소하고 있)',s,re.I):issues.append('UNBOUND_OBSERVED_FINANCIAL_TREND')
        if re.search(r'(?:CAPEX|설비투자|자본적 지출).*(?:즉시|당기|전액).*(?:영업비용|영업이익.*감소)|CAPEX.*immediate.*(?:expense|income)',s,re.I):issues.append('CAPEX_IMMEDIATE_EXPENSE_ERROR')
        if re.search(r'(?:OCF|operating cash flow).*(?:after|includes).*capital expenditure|영업현금흐름.*설비투자.*포함',s,re.I):issues.append('INVESTING_CASH_MISCLASSIFIED_AS_OCF')
        if re.search(r'(?:평균.*가격|ASP|가격 상승).*(?:때문에|으로 인해|에 의해|으로).*(?:판매량|출하량).*(?:증가|늘)',s,re.I):issues.append('REVENUE_UNIT_VOLUME_CONFUSION')
        if any(e['financial_scope']=='PRODUCT' for e in evidence) and re.search(r'(?:제품|DRAM|NAND).*(?:매출|성장).*(?:전사|회사 전체).*(?:동일|같다|성장률이다)|(?:DRAM|제품).*성장.*(?:전사|전체).*수치',s,re.I):issues.append('PRODUCT_FACT_PROMOTED_TO_COMPANY')
        if any(e['business_scope']=='CUSTOMER' for e in evidence):
            if re.search(r'(?:고객|전력|보상|수출).*(?:회사|기업|지배기업).*(?:매출|순이익|수익)',s) and not re.search(r'주문|구매|도입|가격 협상|수요|고객.*경제성|수익성.*고객',s):issues.append('CUSTOMER_BENEFIT_ATTRIBUTED_TO_ISSUER')
    return sorted(set(issues))

def assess(response,ctx,actual=False):
    if ctx['sha256']!=stable_hash({k:v for k,v in ctx.items() if k!='sha256'}):raise ValueError('TYPED_CONTEXT_CHANGED')
    if response is None:return {'actual_model_execution':actual,'rows':[],'drafts':[],'verified':[],'state':'AI_FALLBACK','independent_semantic_accuracy':'NOT_MEASURED'}
    jsonschema.validate(response,schema(ctx));es={e['evidence_id']:e for e in ctx['evidence']};rows=[];drafts=[]
    for role in ROLES:
        raw=response[role];ids=raw['business_evidence_ids']+raw['financial_evidence_ids'];evidence=[es[i] for i in ids];hard=prose_checks(raw,evidence)
        soft=[]
        if len(set(raw['mechanism'].split()))<5 or raw['mechanism']==raw['claim']:soft.append('MECHANISM_MAY_BE_TAUTOLOGICAL')
        if raw['strengthening']==raw['weakening']:soft.append('MONITORING_DIRECTIONS_NOT_DISTINCT')
        if not re.search(r'경우|하면|다면|조건|전제|달려|여부',raw['condition']):soft.append('CONDITION_EXPRESSION_REVIEW')
        if not re.search(r'현금|이익|매출|수익|cash|profit|revenue',raw['mechanism'],re.I):soft.append('FINANCIAL_MECHANISM_EXPRESSION_REVIEW')
        status='REJECTED_HARD_VALIDATION' if hard else 'AI_RESEARCH_DRAFT'
        row={'role':role,'hard_status':'HARD_REJECT' if hard else 'HARD_PASS','hard_issues':hard,
          'semantic_status':'SEMANTIC_REVIEW_NEEDED','semantic_flags':soft,'status':status,
          'grounding_status':'GROUNDING_CHECKED' if not hard else 'HARD_REJECT','interpretation':raw,'verified':False,
          'scope':'Bound source context and explicit conditional hypothesis; economic truth is not automatically certified.'}
        rows.append(row)
        if not hard and actual:drafts.append(row)
    # Lexical signals and source IDs cannot certify free-form economic semantics.
    # No independent semantic receipt is present in this contract; verified inclusion stays empty.
    return {'actual_model_execution':actual,'rows':rows,'drafts':drafts,'verified':[],'state':'AI_RESEARCH_DRAFT' if drafts else 'AI_FALLBACK',
       'verified_report_state':'DETERMINISTIC_FALLBACK','independent_semantic_accuracy':'NOT_MEASURED','verified_financial_reasoning':'NOT_ESTABLISHED'}
