"""SEC-only external packet. Local/vendor records never cross this allowlist."""
from copy import deepcopy
from decimal import Decimal
import json
import os
import re
from urllib.parse import urlsplit
from research.rights import as_utc
from research.validation import stable_hash
from . import typed_evidence, scenarios

VERSION='professional-sec-packet-v1'
PRIVATE=re.compile(r'(?i)(?:(?<![A-Za-z0-9])[A-Z]:[\\/]|/Users/|/home/|reports[\\/]private|local_data[\\/]|(?:sk|sess)-[a-zA-Z0-9_-]{12,}|Bearer\s+\S+|[\w.+-]+@[\w.-]+\.[a-z]{2,}|api[_-]?key\s*[=:])')
INJECTION=re.compile(r'(?i)(?:ignore (?:all |previous |the )*instructions|system prompt|developer message|<\|(?:system|im_start)|override (?:the )?instructions)')
LOCATORS={'char_start','char_end','dom_id','tag','context_ref','row_number','row_text','ordinal','json_path'}


def public_text_check(value):
    text=json.dumps(value,ensure_ascii=False)
    secret=os.environ.get('OPENAI_API_KEY')
    if secret and secret in text:raise ValueError('CLOUD_PRIVATE_OR_SECRET_CONTENT_BLOCKED')
    if PRIVATE.search(text): raise ValueError('CLOUD_PRIVATE_OR_SECRET_CONTENT_BLOCKED')
    if INJECTION.search(text): raise ValueError('CLOUD_SOURCE_INSTRUCTION_QUARANTINED')


def validate(packet):
    if packet.get('version')!=VERSION: raise ValueError('CLOUD_PACKET_VERSION')
    if packet.get('sha256')!=stable_hash({k:v for k,v in packet.items() if k!='sha256'}): raise ValueError('CLOUD_PACKET_HASH_CHANGED')
    public_text_check(packet)
    ids={e['evidence_id'] for e in packet['evidence']}
    if len(ids)!=len(packet['evidence']): raise ValueError('CLOUD_DUPLICATE_EVIDENCE')
    for key,s in packet['sources'].items():
        u=urlsplit(s['url'])
        if u.scheme!='https' or u.hostname not in {'sec.gov','www.sec.gov'} or u.query or u.fragment:
            raise ValueError('CLOUD_NON_SEC_SOURCE')
        if '/Archives/edgar/data/'+str(int(packet['company']['cik']))+'/' not in u.path:
            raise ValueError('CLOUD_SOURCE_CIK_MISMATCH')
        if as_utc(s['available_at'])>as_utc(packet['analysis_as_of']): raise ValueError('CLOUD_FUTURE_SOURCE')
        if s['filing_date']!='UNKNOWN' and s['filing_date']>packet['analysis_as_of'][:10]: raise ValueError('CLOUD_FUTURE_FILING')
        if not re.fullmatch('[a-f0-9]{64}',s['source_hash']): raise ValueError('CLOUD_SOURCE_HASH_INVALID')
        if not s['source_locator'] or set(s['source_locator'])-LOCATORS: raise ValueError('CLOUD_SOURCE_LOCATOR_INVALID')
    for e in packet['evidence']:
        if not e['source_keys'] or any(k not in packet['sources'] for k in e['source_keys']): raise ValueError('CLOUD_UNRESOLVED_SOURCE')
        if e.get('text_reference') and e['text_reference'] not in ids: raise ValueError('CLOUD_TEXT_ALIAS_UNRESOLVED')
    return {'status':'PASS_SEC_ONLY','evidence_count':len(ids),'source_count':len(packet['sources']),
            'vendor_data_transmitted':False,'local_paths_transmitted':False,'external_research_enabled':False}


def build(value):
    ctx=typed_evidence.build(value);view=typed_evidence.model_view(ctx)
    issuer=value.get('issuer') or {};registry={}
    for evidence_id,rows in ctx['exact_sources'].items():
        for q in rows:
            ref=value['source_references'][q['source_id']];key=stable_hash([q['raw_sha256'],q['locator']])[:16]
            registry[key]={'source_type':'PUBLIC_SEC_FILING','url':q['url'],'source_hash':q['raw_sha256'],
                'source_locator':{k:deepcopy(v) for k,v in q['locator'].items() if k in LOCATORS},
                'filing_date':ref.get('filed_date','UNKNOWN'),'available_at':q['available_at']}
    economic_scenarios=[]
    # Exit multiples, peer prices and market-derived values are never exported.
    # Even a growth assumption may derive from licensed inputs: require SEC-only input_refs.
    sec_ids={f.get('id') for f in value.get('facts',[])+value.get('derived_metrics',[]) if f.get('source_refs') and
             all(value['source_references'].get(r,{}).get('kind')!='market' for r in f['source_refs'])}
    sec_ids.update(x['id'] for x in value.get('valuation_financials',{}).values() if isinstance(x,dict) and x.get('id') and
                   x.get('source_refs') and all(value['source_references'].get(r,{}).get('kind')!='market' for r in x['source_refs']))
    for row in value.get('scenario_assumptions',[]):
        fields={}
        for k,f in row['fields'].items():
            if k not in {'growth','net_margin','operating_margin','fcf_margin'}: continue
            if f['assumption_type']!='USER_ASSUMPTION' and (not f['input_refs'] or any(r not in sec_ids for r in f['input_refs'])): continue
            d=scenarios.decimal(f['assumption_value'])
            fields[k]={'value':str(d),'unit':'ratio','kind':f['assumption_type'],
                       'limitation':'Hypothetical input, not company guidance or a probability.'}
        if fields:economic_scenarios.append({'name':row['scenario_name'],'horizon_years':row['horizon_years'],'fields':fields})
    packet={'version':VERSION,'company':{'ticker':value['ticker'],'cik':ctx['issuer'],
                 'name':issuer.get('name','UNKNOWN'),'industry':issuer.get('industry') or 'UNKNOWN'},
        'analysis_as_of':value['analysis_as_of'],'is_illustrative':bool(value.get('is_illustrative')),
        'freshness':{'market_data_status':value.get('market_data_status','UNKNOWN'),
                     'market_data_provided':False,'financial_data_status':value.get('core_financial_status','UNKNOWN')},
        'evidence':view['evidence'],'sources':registry,'scope_rules':view['scope_rules_by_kind'],
        'accounting_relationships':view['financial_relationships'],'economic_scenario_assumptions':economic_scenarios,
        'valuation':{'status':'WITHHELD_EXTERNAL_MARKET_DATA_RIGHTS','values_provided':False,
                     'reason':'Local valuation and P4 results remain available in the private deterministic memo; licensed prices, multiples, peer values and price-derived scenarios are not sent.'},
        'missing_information':view['unknowns']+['No current prices, market multiples, peer valuation, news or actual probabilities supplied.',
             'Scenario assumptions are absent where current P4 engine withholds calculation or external transmission provenance is insufficient.'],
        'analysis_questions':['Explain company-specific business-to-profit/cash transmission and its failure conditions.',
             'Challenge product/company/customer attribution, cyclical earnings normalization and CAPEX recovery.',
             'Identify observable follow-up filing indicators, strengthening/weakening directions and incremental insight.',
             'Assess supplied scenario assumptions only; withheld valuation cannot support cheap/expensive or price targets.']}
    packet['sha256']=stable_hash(packet);validate(packet);return packet
