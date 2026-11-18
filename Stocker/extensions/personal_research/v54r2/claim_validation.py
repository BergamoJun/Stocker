"""Bounded source/role/numeric checks, explicitly not a semantic oracle."""
import re,json
import jsonschema
from research.validation import stable_hash

VERSION='memo-claim-checks-v1'
ROLES=['THESIS','COUNTER_THESIS','MONITORING']


def schema(view,role):
    ids=[e['evidence_id'] for e in view['evidence']]
    sids=[s['scenario_id'] for s in view['scenario_context']]
    def obj(props):return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    text=lambda n:{'type':'string','minLength':1,'maxLength':n}
    return obj({'ticker':{'const':view['ticker']},'claim_id':text(20),'claim_type':{'const':role},'statement':text(220),
        'supporting_evidence_ids':{'type':'array','items':{'enum':ids},'minItems':2,'maxItems':4,'uniqueItems':True},
        'supporting_scenario_ids':{'type':'array','items':{'enum':sids} if sids else {'type':'string'},'maxItems':2 if sids else 0,'uniqueItems':True},
        'evidence_quotes':{'type':'array','items':obj({'evidence_id':{'enum':ids},'quote':text(150)}),'minItems':2,'maxItems':4},
        'assumptions':text(180),'limitations':text(180)})


def validate(raw,view,role):
    issues=[]
    try:jsonschema.Draft202012Validator(schema(view,role)).validate(raw)
    except jsonschema.ValidationError:return {'status':'REJECTED','issues':['SCHEMA_OR_EVIDENCE_ID_INVALID'],'claim':None}
    if len(json.dumps(raw,ensure_ascii=False).encode('utf-8'))>4500:return {'status':'REJECTED','issues':['OUTPUT_TOO_LONG'],'claim':None}
    refs={e['evidence_id']:e for e in view['evidence']};cited=[refs[i] for i in raw['supporting_evidence_ids']]
    quotes={}
    for q in raw['evidence_quotes']:
        if q['evidence_id'] not in raw['supporting_evidence_ids']:issues.append('UNREFERENCED_QUOTE')
        elif len(q['quote'])<6 or q['quote'] not in refs[q['evidence_id']]['anchor']:issues.append('EXACT_EVIDENCE_QUOTE_MISMATCH')
        else:quotes[q['evidence_id']]=q['quote']
    if set(quotes)!=set(raw['supporting_evidence_ids']):issues.append('QUOTED_SUPPORT_REQUIRED_FOR_EACH_EVIDENCE')
    categories={e['category'] for e in cited}
    if not categories&{'business','risk'} or not categories&{'financial','derived','segment','history'}:issues.append('BUSINESS_AND_FINANCIAL_SUPPORT_REQUIRED')
    statement=raw['statement'];combined=' '.join(raw[k] for k in ['statement','assumptions','limitations'])
    without_ids=re.sub(r'\bE\d{3}\b|\bS_(?:bull|base|bear)\b','',combined)
    if re.search(r'\d',without_ids):issues.append('MODEL_NUMERIC_LITERAL_NOT_ALLOWED_USE_CANONICAL_RENDERING')
    if re.search(r'두\s*배|세\s*배|절반|퍼센트|\bdouble\b|\btriple\b|\bpercent\b',without_ids,re.I):issues.append('MODEL_QUANTITY_PHRASE_NOT_ALLOWED')
    if re.search(r'목표주가|적정주가|기대수익률|수익률|주당가치|주당 가치|시가총액|저평가|고평가|매수|매도|P/E|P/S|\bPER\b|peer median|percentile|주가|price target|share price|return|market cap|undervalued',combined,re.I):issues.append('MARKET_OR_PRICE_CLAIM_WITHOUT_TRANSMITTED_RIGHTS')
    certainty=re.sub(r'확정(?:할\s*수(?:는)?\s*없|되지|하기\s*어렵)|보장(?:할\s*수(?:는)?\s*없|하지\s*않|되지)','',combined)
    if re.search(r'가이던스|컨센서스|시장점유율|확률|확정|보장|신규 계약|뉴스|수주 발표|market share|guidance|consensus|guarantee|probability|news|headline',certainty,re.I):issues.append('UNSUPPORTED_GUIDANCE_SHARE_NEWS_OR_CERTAINTY')
    for ticker in ['MSFT','MU','BE','XOM','SPY','ORCL','AMZN','NVDA','GOOGL','SNDK']:
        if ticker!=view['ticker'] and re.search(r'\b'+ticker+r'\b',combined):issues.append('FOREIGN_ISSUER_CLAIM')
    if re.search(r'ignore previous|system prompt|이전.*지시.*무시|<script|<\|',combined,re.I):issues.append('INSTRUCTION_OR_HTML_IN_MODEL_OUTPUT')
    terms={'cloud':['클라우드','cloud','AI'],'memory':['메모리','DRAM','NAND','HBM'],'power':['연료전지','전력','서버'],
        'energy':['에너지','원유','가스','정제'],'investment':['CAPEX','투자','설비'],'cycle':['사이클','가격','순환'],
        'supply':['공급','생산']}
    anchors=[word for e in cited if e['category'] in {'business','risk'} for word in terms.get(e['metric_name'],[])]
    if anchors and not any(w.lower() in statement.lower() for w in anchors):issues.append('COMPANY_SPECIFIC_BUSINESS_ANCHOR_REQUIRED')
    if not re.search(r'마진|이익|현금|매출|CAPEX|투자|설비|수익성|cash|margin|revenue',statement,re.I):issues.append('FINANCIAL_MECHANISM_REQUIRED')
    if role=='MONITORING' and not re.search(r'확인|점검|모니터|추적|관찰|주시|monitor|track|check',statement,re.I):issues.append('ACTIONABLE_MONITORING_REQUIRED')
    if re.search(r'성장했다|증가했다|늘었다|급증|증가한',statement):
        positive=any(e.get('segment_growth_rate') is not None and float(e['segment_growth_rate'])>0 or 'growth' in e['metric_name'] and e.get('value') is not None and float(e['value'])>0 for e in cited)
        if not positive:issues.append('OBSERVED_GROWTH_DIRECTION_NOT_SUPPORTED')
    if re.search(r'감소했다|하락했다|급감|줄었다',statement):
        negative=any(e.get('segment_growth_rate') is not None and float(e['segment_growth_rate'])<0 or 'growth' in e['metric_name'] and e.get('value') is not None and float(e['value'])<0 for e in cited)
        if not negative:issues.append('OBSERVED_DECLINE_DIRECTION_NOT_SUPPORTED')
    accepted=not issues
    return {'status':'ACCEPTED_BOUND_INTERPRETATION' if accepted else 'REJECTED','issues':sorted(set(issues)),
        'claim':raw if accepted else None,'semantic_accuracy':'NOT_MEASURED',
        'scope':'Exact source/role and absence-of-new-number checks; economic persuasiveness is not certified.',
        'response_sha256':stable_hash(raw),'version':VERSION}
