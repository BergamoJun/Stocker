"""Conditional equity values, never price forecasts or inputs to the 23-factor score."""
from copy import deepcopy
from decimal import Decimal,localcontext,InvalidOperation
from research.rights import as_utc

VERSION='conditional-equity-scenarios-v1'
KINDS={'OBSERVED_BASELINE','SOURCE_BACKED_GUIDANCE','HISTORICAL_HEURISTIC','USER_ASSUMPTION','ILLUSTRATIVE_ASSUMPTION'}
FIELDS={'growth','net_margin','operating_margin','fcf_margin','exit_pe','exit_ps'}


def decimal(value):
    if value is None or isinstance(value,bool):raise ValueError('FINITE_DECIMAL_REQUIRED')
    try:x=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('FINITE_DECIMAL_REQUIRED') from None
    if not x.is_finite():raise ValueError('FINITE_DECIMAL_REQUIRED')
    return x


def horizon(value):
    if type(value) is not int or value not in {1,3}:raise ValueError('SCENARIO_HORIZON_MUST_BE_1_OR_3')
    return value


def baseline(value):
    """Reuse actual financial and valuation outputs; keep each source definition."""
    m=value['market_snapshot'];finance=value['valuation_financials'];at=value['analysis_as_of']
    if m.get('security_type')=='ETF':raise ValueError('ETF_CORPORATE_SCENARIO_NOT_APPLICABLE')
    if m.get('data_status')!='AVAILABLE' or m.get('currency')!='USD':raise ValueError('CURRENT_USD_MARKET_DATA_REQUIRED')
    if m.get('market_cap_reference_date')!=m.get('trading_date') or m.get('market_cap_scope')!='PROVIDER_ALL_COMMON_EQUITY_AS_CONVERTED':raise ValueError('ALIGNED_MARKET_CAP_SCOPE_REQUIRED')
    cap=decimal(m.get('provider_reported_market_cap'));rev=finance.get('ttm_revenue')
    if cap<=0 or not rev or decimal(rev['value'])<=0:raise ValueError('POSITIVE_CAP_AND_REVENUE_REQUIRED')
    if rev.get('unit')!='USD' or rev.get('scope')!='consolidated':raise ValueError('CONSOLIDATED_USD_REVENUE_REQUIRED')
    if rev.get('available_at') and as_utc(rev['available_at'])>as_utc(at):raise ValueError('FUTURE_SCENARIO_REFERENCE')
    ratios={x['metric_name']:x for x in value['valuation_metrics']}
    result={'issuer':value['issuer']['cik'],'ticker':value['ticker'],'security_id':m['security_id'],'currency':'USD',
        'reference_revenue':rev['value'],'reference_market_cap':str(cap),'reference_period':deepcopy(rev['period']),
        'reference_period_classification':rev.get('period_classification','VERIFIED_TTM'),
        'reference_period_days':rev.get('period_days'),'price_date':m['trading_date'],'source_as_of':at,
        'reference_net_income':None,'reference_operating_income':None,'reference_fcf':None,
        'current_pe':None,'current_ps':None,'input_refs':[rev['id'],'market:market_cap'],
        'evidence_kind':'OBSERVED_BASELINE','share_adjustment_basis':m.get('share_adjustment_basis')}
    for key,name,scope in [('reference_net_income','ttm_parent_net_income','parent_attributable'),
        ('reference_operating_income','ttm_operating_income','consolidated'),('reference_fcf','ttm_fcf','consolidated')]:
        x=finance.get(name)
        if x and x.get('unit')=='USD' and x.get('scope')==scope and x['period']==rev['period'] and (not x.get('available_at') or as_utc(x['available_at'])<=as_utc(at)):
            result[key]=str(decimal(x['value']));result['input_refs'].append(x['id'])
    for name,key in [('P/E','current_pe'),('P/S','current_ps')]:
        x=ratios.get(name)
        if x and x['calculation_status']=='AVAILABLE' and decimal(x['value'])>0:
            result[key]=x['value'];result['input_refs'].append(name)
    return result


def assumption(name,value,kind,derivation,refs,rationale):
    return {'assumption_name':name,'assumption_value':str(decimal(value)),'unit':'multiple' if name.startswith('exit_') else 'ratio',
        'assumption_type':kind,'source_or_derivation':derivation,'input_refs':list(refs),'rationale':rationale,
        'sensitivity':'Conditional value changes only within the declared scenario/grid.',
        'limitations':['Future condition, not confirmed guidance, consensus or a probability.']}


def defaults(value,base,years=1):
    """Documented research shocks anchored to observations, not estimated forecasts."""
    horizon(years);r=decimal(base['reference_revenue']);refs=base['input_refs']
    ni=base['reference_net_income'];op=base['reference_operating_income'];fcf=base['reference_fcf']
    with localcontext() as c:
        c.prec=60
        nm=decimal(ni)/r if ni is not None else None;om=decimal(op)/r if op is not None else None;fm=decimal(fcf)/r if fcf is not None else None
        growth=Decimal('0');growth_refs=[];growth_derivation='No aligned historical trend: explicit zero-growth research anchor.'
        groups=value.get('business_segments',[])
        if groups and groups[0]['reconciliation_status']=='RECONCILED':
            segments=groups[0]['segments']
            if segments and all(s['prior_period_segment_revenue'] is not None for s in segments):
                prior=sum((decimal(s['prior_period_segment_revenue']) for s in segments),Decimal(0))
                if prior>0:
                    growth=sum((decimal(s['segment_revenue']) for s in segments),Decimal(0))/prior-1
                    growth_refs=[s['id'] for s in segments];growth_derivation='Same-filing segment sum growth, clipped to [-10%,20%]; future continuation remains illustrative.'
        if not growth_refs:
            trends=[x for x in value.get('derived_metrics',[]) if x['name']=='revenue_growth_percent' and x['value'] is not None]
            if trends:
                x=max(trends,key=lambda x:(x['period']['end'],x['period']['start'] or ''))
                growth=decimal(x['value'])/100;growth_refs=[x['id']];growth_derivation='Observed same-period revenue growth clipped to [-10%,20%]; not annualized guidance.'
        g=max(Decimal('-.1'),min(Decimal('.2'),growth));ticker=value['ticker']
        if ticker=='MU':
            profiles={'bull':(Decimal('.15'),Decimal(1),Decimal(1)),
                'base':(Decimal(0),Decimal('.5'),Decimal(1)),'bear':(Decimal('-.25'),Decimal('.2'),Decimal('.8'))}
            policy='MU: current margin retention / illustrative 50% normalization / 80% margin compression; no historical-average claim.'
        elif ticker=='BE':
            profiles={'bull':(min(Decimal('.4'),g+Decimal('.1')),Decimal('1.25'),Decimal('1.1')),
                'base':(g,Decimal('.75'),Decimal(1)),'bear':(Decimal('-.2'),Decimal(0),Decimal('.7'))}
            policy='BE: revenue growth and financing/cash conversion stress; bearish net margin explicitly -5%.'
        else:
            profiles={'bull':(min(Decimal('.4'),g+Decimal('.05')),Decimal('1.1'),Decimal('1.1')),
                'base':(g,Decimal(1),Decimal(1)),'bear':(max(Decimal('-.5'),g-Decimal('.15')),Decimal('.75'),Decimal('.8'))}
            policy='MSFT/general: observed growth anchor with explicit +/- research shocks; no cloud-to-total causal pass-through.'
        result=[]
        for name,(gr,margin_scale,multiple_scale) in profiles.items():
            fields={'growth':assumption('growth',gr,'ILLUSTRATIVE_ASSUMPTION',growth_derivation,growth_refs,policy)}
            for key,observed in [('net_margin',nm),('operating_margin',om),('fcf_margin',fm)]:
                if observed is None:continue
                if ticker=='BE' and name=='bear' and key=='net_margin':future=Decimal('-.05')
                elif key=='fcf_margin':future=observed+(abs(observed)*Decimal('.15') if name=='bull' else -abs(observed)*Decimal('.35') if name=='bear' else Decimal(0))
                else:future=observed*margin_scale
                future=max(Decimal(-1),min(Decimal(1),future))
                fields[key]=assumption(key,future,'ILLUSTRATIVE_ASSUMPTION','Aligned observed margin, then declared research shock; no tax/financing inference.',refs,policy)
            for key,current in [('exit_pe',base['current_pe']),('exit_ps',base['current_ps'])]:
                if current is not None:fields[key]=assumption(key,decimal(current)*multiple_scale,'ILLUSTRATIVE_ASSUMPTION','Current observed multiple x '+str(multiple_scale),[{'exit_pe':'P/E','exit_ps':'P/S'}[key]],policy)
            result.append({'scenario_name':name,'horizon_years':years,'fields':fields,'policy':policy,'assumption_type':'ILLUSTRATIVE_ASSUMPTION'})
        return result


def overrides(assumptions,custom):
    result=deepcopy(assumptions)
    if custom is None:return result
    if not isinstance(custom,dict) or set(custom)-{'bull','base','bear'}:raise ValueError('UNKNOWN_SCENARIO_NAME')
    for row in result:
        changes=custom.get(row['scenario_name'],{})
        if not isinstance(changes,dict) or set(changes)-FIELDS:raise ValueError('UNKNOWN_SCENARIO_FIELD')
        for name,value in changes.items():row['fields'][name]=assumption(name,value,'USER_ASSUMPTION','Explicit user JSON assumption',[],'User supplied, not source-verified guidance.')
    return result


def projected_share_count(base,shares,years):
    if shares is None:return None,'ALIGNED_OUTSTANDING_SHARES_AND_EXPLICIT_DILUTION_REQUIRED'
    if shares.get('scope')!='same_equity_value_per_security' or shares.get('count_basis')!='current_outstanding':return None,'SHARE_SCOPE_OR_WEIGHTED_AVERAGE_MISMATCH'
    if shares.get('security_id')!=base['security_id'] or not shares.get('split_basis') or shares['split_basis']!=base.get('share_adjustment_basis'):return None,'SHARE_CLASS_OR_SPLIT_BASIS_MISMATCH'
    if shares.get('currency')!=base['currency'] or shares.get('date')!=base['price_date']:return None,'SHARE_DATE_OR_CURRENCY_MISMATCH'
    if not shares.get('available_at') or as_utc(shares['available_at'])>as_utc(base['source_as_of']):return None,'FUTURE_OR_UNDATED_SHARE_INPUT'
    if shares.get('annual_dilution_assumption') is None:return None,'EXPLICIT_FUTURE_DILUTION_ASSUMPTION_REQUIRED'
    count=decimal(shares['value']);dilution=decimal(shares.get('annual_dilution_assumption'))
    if count<=0 or dilution<=-1:raise ValueError('INVALID_SHARE_COUNT_OR_DILUTION')
    return count*(1+dilution)**years,None


def calculate(base,scenario,shares=None):
    years=horizon(scenario['horizon_years']);fields=scenario['fields']
    if set(fields)-FIELDS:raise ValueError('UNKNOWN_SCENARIO_FIELD')
    if base['currency']!='USD':raise ValueError('SCENARIO_CURRENCY_MISMATCH')
    for key,field in fields.items():
        if field['assumption_type'] not in KINDS:raise ValueError('UNKNOWN_ASSUMPTION_TYPE')
        x=decimal(field['assumption_value'])
        if key=='growth' and not -1<=x<=2:raise ValueError('NON_ECONOMIC_GROWTH_ASSUMPTION')
        if key.endswith('margin') and not -1<=x<=1:raise ValueError('INVALID_MARGIN_ASSUMPTION')
        if key.startswith('exit_') and x<=0:raise ValueError('POSITIVE_EXIT_MULTIPLE_REQUIRED')
    with localcontext() as c:
        c.prec=60
        revenue=decimal(base['reference_revenue']);cap=decimal(base['reference_market_cap'])
        if revenue<=0 or cap<=0:raise ValueError('POSITIVE_REFERENCE_REVENUE_AND_CAP_REQUIRED')
        growth=decimal(fields['growth']['assumption_value']);projected=revenue*(1+growth)**years
        get=lambda k:decimal(fields[k]['assumption_value']) if k in fields else None
        nm,om,fm=get('net_margin'),get('operating_margin'),get('fcf_margin')
        ni=projected*nm if nm is not None else None
        op=projected*om if om is not None else None
        fcf=projected*fm if fm is not None else None
        count,share_error=projected_share_count(base,shares,years);values=[]
        for method,multiple_key,amount,formula in [('EARNINGS','exit_pe',ni,'projected_net_income * exit_PE'),('SALES','exit_ps',projected,'projected_revenue * exit_PS')]:
            multiple=get(multiple_key);reason=None
            if amount is None or multiple is None:equity=None;reason='MISSING_ALIGNED_EARNINGS_OR_EXPLICIT_MULTIPLE'
            elif method=='EARNINGS' and amount<=0:equity=None;reason='NONPOSITIVE_PROJECTED_EARNINGS_PE_NOT_MEANINGFUL'
            else:equity=amount*multiple
            values.append({'method':method,'equity_value':str(equity) if equity is not None else None,'enterprise_value':None,
                'change_vs_reference_market_cap_percent':str((equity/cap-1)*100) if equity is not None else None,
                'per_share_value':str(equity/count) if equity is not None and count is not None else None,
                'projected_share_count':str(count) if count is not None else None,'share_status':share_error or 'EXPLICIT_CONDITIONAL_SHARE_BASIS',
                'currency':'USD','formula':formula,'output_status':'AVAILABLE' if equity is not None else 'UNAVAILABLE','reason':reason,
                'assumption_type':'CONDITIONAL_ON_DECLARED_ASSUMPTIONS','method_version':VERSION,
                'input_refs':base['input_refs']+[scenario['scenario_name']+':'+k for k in fields],
                'source_as_of':base['source_as_of'],'limitations':['Equity value change is not total shareholder return; dividends, financing and costs are excluded.']})
        return {'scenario_name':scenario['scenario_name'],'horizon_years':years,'reference_period':base['reference_period'],
            'reference_period_classification':base['reference_period_classification'],'reference_period_days':base.get('reference_period_days'),
            'projected_revenue':str(projected),'projected_net_income':str(ni) if ni is not None else None,
            'projected_operating_income':str(op) if op is not None else None,'projected_fcf':str(fcf) if fcf is not None else None,
            'projection_formula':'reference_revenue * (1 + annual_growth)^horizon; projected_monetary_flow = projected_revenue * assumed_margin',
            'projection_period_policy':'Nominal annual model steps; reported fiscal-year scale retained, no hidden 53-week-to-52-week normalization.',
            'valuations':values,'method_version':VERSION,'source_as_of':base['source_as_of'],
            'assumption_type':'CONDITIONAL_ON_DECLARED_ASSUMPTIONS','input_refs':base['input_refs']+[scenario['scenario_name']+':'+k for k in fields]}


def sensitivity(base,scenario,method,axis_values=None):
    first='net_margin' if method=='EARNINGS' else 'growth';second='exit_pe' if method=='EARNINGS' else 'exit_ps'
    if first not in scenario['fields'] or second not in scenario['fields']:
        return {'method':method,'output_status':'UNAVAILABLE','reason':'MISSING_SENSITIVITY_ASSUMPTION','grid':[]}
    a=decimal(scenario['fields'][first]['assumption_value']);b=decimal(scenario['fields'][second]['assumption_value'])
    with localcontext() as c:
        c.prec=60
        rows,columns=axis_values if axis_values is not None else ([a*Decimal('.8'),a,a*Decimal('1.2')] if first=='net_margin' else [max(Decimal(-1),a-Decimal('.05')),a,min(Decimal(2),a+Decimal('.05'))],[b*Decimal('.8'),b,b*Decimal('1.2')])
        if len(rows) not in {3,5} or len(columns) not in {3,5}:raise ValueError('SENSITIVITY_GRID_MUST_BE_3_OR_5')
        grid=[]
        for r in rows:
            line=[]
            for col in columns:
                changed=deepcopy(scenario);changed['fields'][first]['assumption_value']=str(decimal(r));changed['fields'][second]['assumption_value']=str(decimal(col))
                out=calculate(base,changed);line.append(next(x['equity_value'] for x in out['valuations'] if x['method']==method))
            grid.append(line)
        complete=all(x is not None for row in grid for x in row)
        row_span=max((abs(decimal(grid[-1][i])-decimal(grid[0][i])) for i in range(len(columns))),default=Decimal(0)) if complete else None
        col_span=max((abs(decimal(row[-1])-decimal(row[0])) for row in grid),default=Decimal(0)) if complete else None
        return {'method':method,'row_variable':first,'row_values':list(map(str,rows)),'column_variable':second,'column_values':list(map(str,columns)),
            'grid':grid,'currency':'USD','output_status':'AVAILABLE' if complete else 'PARTIAL','method_version':VERSION,
            'largest_declared_range_effect':first if complete and row_span>col_span else second if complete and col_span>row_span else 'TIED' if complete else None,
            'range_effects':{first:str(row_span) if row_span is not None else None,second:str(col_span) if col_span is not None else None},
            'input_refs':base['input_refs']+[scenario['scenario_name']+':'+k for k in scenario['fields']],
            'source_as_of':base['source_as_of'],'assumption_type':'CONDITIONAL_ON_DECLARED_GRID',
            'formula':'Recompute the same base scenario with only the named row/column assumption replaced.',
            'limitations':['Sensitivity is conditional on the selected grid, not volatility, probability or a causal estimate.']}


def enrich(value,years=1,custom=None):
    value.update(scenario_assumptions=[],scenario_results=[],sensitivity_results=[],valuation_drivers=[],monitoring_indicators=[],
        scenario_reference=None,scenario_status='UNAVAILABLE',scenario_limitations=['Conditional valuation, not a validated future price, fair-value certification or buy/sell signal.'])
    try:base=baseline(value)
    except (ValueError,KeyError) as error:value['scenario_limitations'].append(str(error));return
    assumptions=overrides(defaults(value,base,years),custom)
    values=[calculate(base,a) for a in assumptions]
    value.update(scenario_reference=base,scenario_assumptions=assumptions,scenario_results=values,
        sensitivity_results=[sensitivity(base,assumptions[1],method) for method in ['EARNINGS','SALES']],scenario_status='PARTIAL')
    facts={f['id']:f for f in value.get('business_profile',[])}
    for identifier in value.get('growth_drivers',[])[:3]:
        f=facts[identifier]
        value['valuation_drivers'].append({'business_driver':f['statement'],'source_refs':f['source_refs'],
            'related_financial_metric':'revenue_growth / margin / FCF margin','scenario_assumption':'Declared illustrative growth and margin shocks',
            'valuation_effect':'Projected sales or net income multiplied by explicit exit multiple',
            'counter_thesis':'CAPEX, working capital, cyclical demand and multiple compression may offset growth.',
            'uncertainty':'The source describes business context, not a measured future pass-through coefficient.'})
    value['monitoring_indicators']=[{'indicator':name,'input_refs':base['input_refs'],'purpose':purpose} for name,purpose in [
        ('revenue_and_segment_growth','Check same-period growth and organizational changes.'),('parent_net_margin','Test margin retention/normalization assumptions.'),
        ('OCF_minus_cash_CAPEX','Monitor cash conversion and investment burden.'),('exit_multiple','Compare market rerating with the declared sensitivity range.')]]
