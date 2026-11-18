"""Source-bound candidate pairs and bounded interpretation checks, not a semantic oracle."""
from copy import deepcopy
from pathlib import Path
import re
import jsonschema
from research.validation import digest,stable_hash
from research.rights import as_utc
from research import sec_filing_evidence
from . import evidence_pack as ep,business,claim_validation as legacy,scenarios

VERSION='paired-sec-grounding-v1'
ROLES=legacy.ROLES
LABELS={'ttm_revenue':'매출','ttm_parent_net_income':'지배기업 순이익','ttm_fcf':'잉여현금흐름',
    'ttm_capital_expenditure':'현금 설비투자','ttm_operating_cash_flow':'영업현금흐름','ttm_operating_income':'영업이익'}
TERMS={'cloud':['클라우드','cloud','Azure'],'memory':['메모리','DRAM','NAND','HBM'],
    'power':['연료전지','전력','fuel cell'],'energy':['에너지','원유','가스','정제'],
    'software':['소프트웨어','구독','software'],'investment':['투자','설비','CAPEX'],
    'competition':['경쟁'],'supply':['공급','생산'],'cycle':['수요','가격','순환'],'customers':['고객']}
FIN_TERMS={'ttm_revenue':['매출','revenue'],'ttm_parent_net_income':['이익','마진','income'],
    'ttm_fcf':['현금','FCF','cash'],'ttm_capital_expenditure':['투자','설비','CAPEX'],
    'ttm_operating_cash_flow':['현금','cash'],'ttm_operating_income':['이익','마진','income']}


class Resolver:
    def __init__(self,value):self.value=value;self.raw={};self.hashes={};self.parsed={}
    def resolve(self,entry):
        v=self.value;issuer=v['evidence_pack']['issuer'];out=[]
        if entry['issuer']!=issuer or entry['ticker']!=v['ticker']:raise ValueError('PAIR_FOREIGN_ISSUER')
        if not entry['source_refs']:raise ValueError('PAIR_SOURCE_REQUIRED')
        for key in entry['source_refs']:
            s=v['source_references'][key]
            if not ep.sec_source(s,issuer,v['analysis_as_of']):raise ValueError('PAIR_SOURCE_ISSUER_OR_TIME')
            path=s['raw_path'];loc=s.get('locator') or {}
            if path not in self.raw:
                self.hashes[path]=digest(path)
                self.raw[path]=Path(path).read_bytes()
            if self.hashes[path]!=s['raw_sha256']:raise ValueError('PAIR_SOURCE_HASH_CHANGED')
            raw=self.raw[path]
            if entry['category'] in {'business','risk'}:
                a,b=loc.get('char_start'),loc.get('char_end');text=raw.decode('utf-8-sig')
                if type(a) is not int or type(b) is not int or not 0<=a<b<=len(text):raise ValueError('PAIR_TEXT_LOCATOR_INVALID')
                parser=business.Blocks(text[a:b]);parser.feed(text[a:b]);parser.close()
                matches=[p['text'] for p in parser.rows if p['text']==entry['source_text'] and p['char_start']==0 and p['char_end']==b-a and p['dom_id']==loc.get('dom_id')]
                if not matches:raise ValueError('PAIR_TEXT_LOCATOR_MISMATCH')
                quote=matches[0];component=None
            else:
                if path not in self.parsed:self.parsed[path]=sec_filing_evidence.parse(raw,s['raw_sha256'],issuer)
                selectors={k:x for k,x in loc.items() if k in {'dom_id','tag','context_ref','row_number','ordinal','row_text'} and x is not None}
                if 'tag' not in selectors or 'context_ref' not in selectors:raise ValueError('PAIR_NUMERIC_LOCATOR_REQUIRED')
                matches=[x for x in self.parsed[path] if all(x.get(k)==xv for k,xv in selectors.items()) and x['same_entity'] and x['status']=='parsed_numeric_evidence_only' and not x['hidden']]
                if len(matches)!=1:raise ValueError('PAIR_NUMERIC_LOCATOR_MISMATCH')
                x=matches[0];quote=x['row_text']
                component={k:x[k] for k in ['value','unit','start','end','tag','context_ref']}
            if ep.INJECTION.search(quote):raise ValueError('PAIR_SOURCE_INSTRUCTION_QUARANTINED')
            out.append({'source_id':key,'url':s['url'],'raw_sha256':s['raw_sha256'],'available_at':s['available_at'],
                'locator':deepcopy(loc),'quote':quote,'component':component})
        return out


def build(value,context='primary'):
    if context not in {'primary','fresh'}:raise ValueError('PAIR_CONTEXT_INVALID')
    pack=value['evidence_pack'];entries=pack['entries'];resolver=Resolver(value);quotes={};rejected=[]
    facts=[e for e in entries if e['category'] in {'business','risk'}]
    major=next((topic for topic in ['memory','power','energy','cloud','software'] if any(e['metric_name']==topic and e.get('section')=='BUSINESS' for e in facts)),None)
    def select(topics,categories=('business','risk'),sections=None):
        return next((e for t in topics for e in facts if e['metric_name']==t and e['category'] in categories and (sections is None or e.get('section') in sections)),None)
    if context=='primary':
        definitions=[('THESIS',select([major],('business',),['BUSINESS','MD&A']),'ttm_revenue'),
            ('COUNTER_THESIS',select(['investment',major,'competition'],('risk',)),'ttm_fcf'),
            ('MONITORING',select(['investment',major],('business',)),'ttm_capital_expenditure')]
    else:
        definitions=[('THESIS',select([major,'customers'],('business',),['MD&A']) or select(['customers',major],('business',)),'ttm_operating_cash_flow'),
            ('COUNTER_THESIS',select(['supply','competition',major],('risk',)),'ttm_parent_net_income'),
            ('MONITORING',select(['investment','cycle',major],('business',),['MD&A']) or select(['cycle',major],('business',)),'ttm_operating_income')]
    pairs=[]
    if value['market_snapshot'].get('security_type')!='ETF':
        for role,b,metric in definitions:
            f=next((e for e in entries if e['category']=='financial' and e['metric_name']==metric and e['value'] is not None),None)
            if b is None or f is None:rejected.append({'role':role,'reason':'BUSINESS_OR_FINANCIAL_COVERAGE_MISSING'});continue
            if any(c['metric']==f['metric_name'] for c in pack['conflicts']):rejected.append({'role':role,'reason':'CONFLICTING_FINANCIAL_EVIDENCE'});continue
            try:
                for e in [b,f]:
                    if e['evidence_id'] not in quotes:quotes[e['evidence_id']]=resolver.resolve(e)
            except (ValueError,KeyError,OSError) as error:rejected.append({'role':role,'reason':str(error)});continue
            pairs.append({'pair_id':'PAIR_'+stable_hash([context,role,b['evidence_id'],f['evidence_id']])[:10],
                'ticker':value['ticker'],'issuer':pack['issuer'],'role':role,'business_evidence_id':b['evidence_id'],
                'financial_evidence_id':f['evidence_id'],'related_business_driver':b['metric_name'],'related_financial_metric':metric,
                'relationship_status':'CANDIDATE_RELATIONSHIP','scope':'Company evidence; no calibrated causal coefficient or product-level attribution.'})
    for a,row in zip(value.get('scenario_assumptions',[]),value.get('scenario_results',[])):
        if scenarios.calculate(value['scenario_reference'],a)!=row:raise ValueError('PAIR_P4_CALCULATION_CHANGED')
    result={'version':VERSION,'ticker':value['ticker'],'issuer':pack['issuer'],'analysis_as_of':value['analysis_as_of'],
        'context':context,'major_topic':major,'major_label':business.TOPICS[major][0] if major else None,
        'pairs':pairs,'quotes':quotes,'rejected':rejected,'pack_sha256':stable_hash(pack),
        'p4_sha256':stable_hash([value.get('scenario_assumptions',[]),value.get('scenario_results',[])]),
        'uncertainty':bool(value.get('scenario_review',{}).get('economic_tensions')) or any(h.get('period_days')==371 for h in value.get('memo_historical_evidence',[])),
        'status':'READY' if len(pairs)==3 else 'LIMITED' if pairs else 'NO_ELIGIBLE_PAIR'}
    result['sha256']=stable_hash(result);return result


def model_view(value,ctx,roles):
    es={e['evidence_id']:e for e in value['evidence_pack']['entries']};pairs=[p for p in ctx['pairs'] if p['role'] in roles]
    ids=list(dict.fromkeys(i for p in pairs for i in [p['business_evidence_id'],p['financial_evidence_id']]))
    evidence=[]
    for i in ids:
        e=es[i];q=ctx['quotes'][i]
        evidence.append({'id':i,'role':'BUSINESS_EVIDENCE' if e['category']=='business' else 'RISK_EVIDENCE' if e['category']=='risk' else 'FINANCIAL_EVIDENCE',
            'topic':e['metric_name'],'label':LABELS.get(e['metric_name'],e['statement']),'value':e['value'],'unit':e['unit'],
            'period':e['fiscal_period'],'period_days':e.get('period_days'),'scope':e.get('scope','consolidated'),
            'kind':e['evidence_kind'],'text':e.get('source_text'),
            'source_keys':[stable_hash([s['raw_sha256'],s['locator']])[:16] for s in q],
            'limits':'Company statement; not independent proof. Financial amount is company scope, not a product KPI.'})
    return {'ticker':ctx['ticker'],'issuer':ctx['issuer'],'as_of':ctx['analysis_as_of'],'business':ctx['major_label'],
        'pairs':[{k:p[k] for k in ['role','business_evidence_id','financial_evidence_id','relationship_status']} for p in pairs],
        'evidence':evidence,'limits':['No price, multiples, peer, target, news or guidance supplied. No long-run margin average confirmed.',
            'Read each fiscal period separately, including53-week years. Conditions are hypotheses, not observed forecasts.']}


def schema(ctx,roles):
    def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
    text=lambda n:{'type':'string','minLength':12,'maxLength':n}
    props={}
    for role in roles:
        p=next(p for p in ctx['pairs'] if p['role']==role)
        fields={'business_evidence_id':{'const':p['business_evidence_id']},'financial_evidence_id':{'const':p['financial_evidence_id']},
            'claim':text(50),'mechanism':text(65),'condition':text(55),'limitation':text(65)}
        if role=='MONITORING':fields.update(strengthening=text(40),weakening=text(40))
        props[role]=obj(fields)
    return obj(props)


def validate(raw,value,ctx,role):
    if ctx['sha256']!=stable_hash({k:v for k,v in ctx.items() if k!='sha256'}):raise ValueError('PAIR_CONTEXT_CHANGED')
    try:jsonschema.Draft202012Validator(schema(ctx,[role])['properties'][role]).validate(raw)
    except (jsonschema.ValidationError,StopIteration):return {'status':'REJECTED','issues':['SCHEMA_OR_TYPED_EVIDENCE_INVALID'],'claim':None}
    issues=[];es={e['evidence_id']:e for e in value['evidence_pack']['entries']}
    if ctx['pack_sha256']!=stable_hash(value['evidence_pack']) or ctx['p4_sha256']!=stable_hash([value.get('scenario_assumptions',[]),value.get('scenario_results',[])]):raise ValueError('PAIR_INPUT_CHANGED')
    b,f=[es[raw[k]] for k in ['business_evidence_id','financial_evidence_id']]
    resolver=Resolver(value)
    try:
        quotes={e['evidence_id']:resolver.resolve(e) for e in [b,f]}
        if any(quotes[i]!=ctx['quotes'][i] for i in quotes):raise ValueError('PAIR_RESOLVED_QUOTES_CHANGED')
    except (ValueError,OSError,KeyError) as error:return {'status':'REJECTED','issues':[str(error)],'claim':None}
    statement=' '.join(raw[k] for k in ['claim','mechanism','strengthening','weakening'] if k in raw)
    body=' '.join(raw[k] for k in ['claim','mechanism','condition','limitation','strengthening','weakening'] if k in raw)
    if not re.search(r'[가-힣]',raw['claim']) or not re.search(r'[가-힣]',raw['mechanism']):issues.append('KOREAN_INTERPRETATION_REQUIRED')
    if not any(t.lower() in statement.lower() for t in TERMS.get(ctx['major_topic'],[])):issues.append('ISSUER_BUSINESS_ANCHOR_REQUIRED')
    if not any(t.lower() in raw['mechanism'].lower() for t in FIN_TERMS.get(f['metric_name'],[])):issues.append('SELECTED_FINANCIAL_MECHANISM_REQUIRED')
    if not re.search(r'이어|연결|전이|영향|회수|압박|부담|전환|기여|수익성|현금화',raw['mechanism']):issues.append('ECONOMIC_TRANSMISSION_REQUIRED')
    if not re.search(r'경우|하면|다면|조건|수 있|여부|달려|확인|점검',raw['condition']+' '+raw['mechanism']):issues.append('CONDITIONAL_MECHANISM_REQUIRED')
    if raw['claim'].strip()==raw['mechanism'].strip():issues.append('MECHANISM_REPEATS_CLAIM')
    if ctx['major_topic']=='memory' and re.search(r'클라우드|cloud|Azure',body,re.I) and not re.search(r'cloud|Azure',b.get('source_text',''),re.I):issues.append('UNSUPPORTED_CLOUD_BUSINESS')
    if f.get('scope')=='consolidated' and re.search(r'Azure\s*(?:매출|성장)|HBM\s*(?:매출액|마진)',body,re.I):issues.append('PRODUCT_KPI_NOT_CONSOLIDATED_FINANCIAL')
    if role=='MONITORING' and raw['strengthening']==raw['weakening']:issues.append('MONITORING_DIRECTIONS_IDENTICAL')
    adapted=[];qs=[]
    for e in [b,f]:
        q=quotes[e['evidence_id']][0]['quote'];adapted.append({**e,'anchor':q})
        qs.append({'evidence_id':e['evidence_id'],'quote':q[:150]})
    claim={'ticker':ctx['ticker'],'claim_id':role,'claim_type':role,'statement':statement,
        'supporting_evidence_ids':[b['evidence_id'],f['evidence_id']],'supporting_scenario_ids':[],
        'evidence_quotes':qs,'assumptions':raw['condition'],'limitations':raw['limitation']}
    old=legacy.validate(claim,{'ticker':ctx['ticker'],'evidence':adapted,'scenario_context':[]},role)
    issues+=old['issues']
    return {'status':'ACCEPTED_BOUND_INTERPRETATION' if not issues else 'REJECTED','issues':sorted(set(issues)),
        'claim':{**claim,'exact_sources':quotes,'origin':'ACTUAL_AI_PENDING_IDENTITY','grounding_scope':'AUTOMATIC_BOUND_CHECKS_ONLY'} if not issues else None,
        'semantic_accuracy':'NOT_MEASURED','economic_usefulness':'NOT_MEASURED','old_numeric_and_claim_policy':'P5_CHECKS_REUSED_UNCHANGED'}
