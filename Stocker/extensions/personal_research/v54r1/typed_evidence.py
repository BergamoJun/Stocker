"""Scoped SEC-only context. Source observations and accounting identities stay separate."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import re
from research.validation import stable_hash
from . import grounding

VERSION='typed-sec-context-v1'
METRICS={'revenue':'REVENUE','parent_net_income':'NET_INCOME','net_income':'NET_INCOME','operating_income':'OPERATING_INCOME',
 'operating_cash_flow':'OCF','capital_expenditure':'CAPEX','fcf':'FCF','free_cash_flow':'FCF','reported_annual_net_margin':'NET_MARGIN'}
PRODUCTS=['Microsoft 365','Azure','DRAM','NAND','HBM','Energy Server','Bloom Electrolyzer']

def normalize(e):
    """Classify only explicit text; filing dates do not invent a growth comparison period."""
    text=e.get('source_text') or '';category=e['category'];metric=METRICS.get(e['metric_name'].removeprefix('ttm_'),'UNKNOWN')
    scope='UNKNOWN';subject='UNKNOWN';financial='UNKNOWN';measurement='NARRATIVE';facts=[]
    if category in {'financial','derived','history'}:
        scope=financial='CONSOLIDATED_COMPANY';subject=e['ticker'];measurement='RATIO' if e['unit']=='ratio' else 'AMOUNT'
    elif category=='segment':scope=financial='SEGMENT';subject=e['metric_name'];metric='REVENUE';measurement='AMOUNT'
    else:
        if re.search(r'compensation to our customers|customer-generated power',text,re.I):scope=financial='CUSTOMER';subject='customer electricity export'
        else:
            products=[p for p in PRODUCTS if re.search(re.escape(p),text,re.I)]
            if products:scope='PRODUCT';subject=' / '.join(products)
        match=re.search(r'Sales of (DRAM|NAND) products increased (\d+(?:\.\d+)?)%',text,re.I)
        if match:
            scope=financial='PRODUCT';subject=match[1].upper();metric='REVENUE_GROWTH';measurement='PERCENT_CHANGE'
            facts.append({'subject':subject,'metric':'REVENUE_GROWTH','value':match[2],'unit':'percent','scope':'PRODUCT',
                'comparison_basis':'UNKNOWN','period':'UNKNOWN','quote_span':[match.start(),match.end()]})
            if 'average selling prices' in text:facts.append({'subject':subject,'metric':'ASP','value':None,'unit':'percent','scope':'PRODUCT','qualifier':'Read exact source range; not an exact point estimate.'})
            if 'bit shipments' in text:facts.append({'subject':subject,'metric':'BIT_SHIPMENTS','value':None,'unit':'percent','scope':'PRODUCT','qualifier':'Read exact source range; not a unit revenue metric.'})
    days=e.get('period_days');period=e.get('fiscal_period')
    if days is None and period and period.get('start') and period.get('end'):days=(date.fromisoformat(period['end'])-date.fromisoformat(period['start'])).days+1
    return {'evidence_id':e['evidence_id'],'ticker':e['ticker'],'issuer':e['issuer'],'evidence_type':category.upper(),
      'business_scope':scope,'financial_scope':financial,'accounting_scope':e.get('scope','UNKNOWN'),'subject':subject,'metric':metric,
      'measurement_type':measurement,'comparison_basis':(e.get('fiscal_period') or {}).get('basis','UNKNOWN'),
      'period':deepcopy(e.get('fiscal_period')),'period_days':days,'value':e['value'],'unit':e['unit'],
      'observation_type':e['evidence_kind'],'text':text or None,'source_refs':list(e['source_refs']),'source_observations':facts,
      'scope_rule':('This amount is issuer-wide; do not assign it to a product.' if financial=='CONSOLIDATED_COMPANY' else
          'This fact is about the named product; it is not company-wide growth.' if financial=='PRODUCT' else
          'Compensation accrues to customers, not automatically the issuer. Issuer effects require a conditional orders/pricing channel.' if scope=='CUSTOMER' else
          'No financial attribution is established. Scope UNKNOWN must stay unknown.')}

def build(value):
    entries=value['evidence_pack']['entries'];chosen=[];skipped=[];quotes={};resolver=grounding.Resolver(value)
    def add(e):
        if e is None or e['evidence_id'] in {x['evidence_id'] for x in chosen}:return
        if any(c['metric']==e['metric_name'] for c in value['evidence_pack']['conflicts']):skipped.append({'id':e['evidence_id'],'reason':'CONFLICT'});return
        try:
            q=resolver.resolve(e)
            if e['value'] is not None and not Decimal(str(e['value'])).is_finite():raise ValueError('NONFINITE_TYPED_VALUE')
            if e['category']=='financial' and e['evidence_kind']=='OBSERVED':
                period=e['fiscal_period']
                if not any(s['component'] and (Decimal(s['component']['value']),s['component']['unit'],s['component']['start'],s['component']['end'])==(Decimal(e['value']),e['unit'],period.get('start'),period['end']) for s in q):raise ValueError('OBSERVED_VALUE_UNIT_OR_PERIOD_MISMATCH')
        except (ValueError,KeyError,OSError) as err:skipped.append({'id':e['evidence_id'],'reason':str(err)});return
        x=normalize(e);quotes[e['evidence_id']]=q;chosen.append(x)
    for metric in ['ttm_revenue','ttm_parent_net_income','ttm_operating_income','ttm_operating_cash_flow','ttm_capital_expenditure','ttm_fcf']:
        add(next((e for e in entries if e['category']=='financial' and e['metric_name']==metric and e['value'] is not None),None))
    # Preserve available current-filing comparisons, with exact periods and53-week flags.
    for e in sorted([e for e in entries if e['category']=='history'],key=lambda e:(e['fiscal_period'] or {}).get('end',''),reverse=True)[:3]:add(e)
    for metric in ['revenue','operating_cash_flow','capital_expenditure']:
        options=[e for e in entries if e['category']=='financial' and e['metric_name']==metric and e['value'] is not None]
        for e in sorted(options,key=lambda e:(e['fiscal_period'] or {}).get('end',''),reverse=True)[:2]:add(e)
    facts=[e for e in entries if e['category'] in {'business','risk'}]
    topics={'MSFT':['cloud','software','competition','investment','supply'],'MU':['memory','customers','cycle','investment','supply'],'BE':['power','investment','supply','competition']}.get(value['ticker'],['cloud','memory','power','investment','supply'])
    for topic in topics:
        options=[e for e in facts if e['metric_name']==topic]
        if topic=='cycle':options.sort(key=lambda e:'Sales of DRAM products increased' not in e.get('source_text',''))
        add(next(iter(options),None))
        add(next((e for e in options if e['category']=='risk'),None))
    # Explicit product and customer attribution cases remain visible even if topic selection found a different paragraph.
    for pattern in [r'Sales of DRAM products increased',r'compensation to our customers']:
        add(next((e for e in facts if re.search(pattern,e.get('source_text',''),re.I)),None))
    ctx={'version':VERSION,'ticker':value['ticker'],'issuer':value['evidence_pack']['issuer'],'as_of':value['analysis_as_of'],
      'evidence':chosen,'exact_sources':quotes,'excluded':skipped,'unknowns':['Unobserved issuer/product cash attribution, normalized earnings and future guidance remain UNKNOWN.',
       'Current-filing historical comparisons are not original-vintage PIT. Missing financial series cannot establish trends.'],
      'accounting_relationships':[
       {'type':'GENERAL_ACCOUNTING_IDENTITY_NOT_NEW_ISSUER_FACT','relation':'Product revenue = units times ASP for the same product/period; product mix matters. Revenue growth is not unit-volume growth.'},
       {'type':'GENERAL_ACCOUNTING_IDENTITY_NOT_NEW_ISSUER_FACT','relation':'Revenue less COGS gives gross profit; operating costs and nonoperating/tax items separate operating income and parent net income.'},
       {'type':'GENERAL_ACCOUNTING_IDENTITY_NOT_NEW_ISSUER_FACT','relation':'FCF = OCF minus cash CAPEX under the existing definition. CAPEX is investing cash flow, not an operating cash deduction.'},
       {'type':'GENERAL_ACCOUNTING_IDENTITY_NOT_NEW_ISSUER_FACT','relation':'Capitalized PPE is not immediately expensed in full. Future depreciation may affect income without a matching current cash outflow. Working-capital movements can separate profit and cash.'}],
      'market_fields_provided':False}
    ctx['accounting_relationships'].append({'type':'GENERAL_ACCOUNTING_IDENTITY_NOT_NEW_ISSUER_FACT','relation':'R&D is not PPE depreciation. Do not invent capitalization, depreciation or a fixed revenue-proportional R&D charge; actual recognition requires source support.'})
    ctx['sha256']=stable_hash(ctx);return ctx

def model_view(ctx):
    allowed=['evidence_id','evidence_type','business_scope','financial_scope','accounting_scope','subject','metric','measurement_type',
       'comparison_basis','period','period_days','value','unit','observation_type','text','source_observations']
    return {'ticker':ctx['ticker'],'issuer':ctx['issuer'],'as_of':ctx['as_of'],'evidence':[{**{k:e[k] for k in allowed},
       'source_keys':[stable_hash([q['raw_sha256'],q['locator']])[:16] for q in ctx['exact_sources'][e['evidence_id']]]} for e in ctx['evidence']],
       'scope_rules_by_kind':{'CONSOLIDATED_COMPANY':'Issuer-wide amount, not a product KPI. Product inclusion does not quantify product attribution.',
         'PRODUCT':'Named product fact; never promote product revenue growth to total-company growth.',
         'CUSTOMER':'Customer compensation is not issuer revenue/profit; any issuer link needs a conditional orders/pricing channel.',
         'UNKNOWN':'No financial attribution established; keep UNKNOWN.'},
       'financial_relationships':ctx['accounting_relationships'],'unknowns':ctx['unknowns']}
