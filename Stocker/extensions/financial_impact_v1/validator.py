"""RiskCritic for arithmetic inputs: deterministic, source bound, no LLM."""
from datetime import date
from decimal import localcontext, ROUND_HALF_EVEN
from research.validation import stable_hash
from research.rights import as_utc
from .models import BINDINGS
from . import calculations as calc, evidence


def scenario_fingerprint(packet):
    with localcontext() as ctx:
        ctx.prec=50;ctx.rounding=ROUND_HALF_EVEN
        delta=packet['assumptions']['rate_delta_override'] or packet['event']['delta']
        return stable_hash({'rate_delta_decimal':calc.decimal_text(calc.delta_rate(delta))}) if delta else stable_hash(None)


def ledger_record(packet):
    c,e,x=packet['context'],packet['event'],packet['exposure']
    return {'cik':c['cik'],'canonical_event_id':e['canonical_event_id'],'instrument_id':x['instrument_id'],
        'start':c['calculation_start'],'end':c['calculation_end'],'scenario_fingerprint':scenario_fingerprint(packet)}


def check(packet):
    issues,facts=evidence.check(packet);c=packet['context'];e=packet['event'];x=packet['exposure'];p=packet['policy']
    def add(code,status,detail):issues.append(evidence.issue(code,status,detail))
    if packet['origin'] is not None:
        from .adapters import verify_origin
        try:verify_origin(packet)
        except (OSError,ValueError,KeyError):add('ORIGIN_CONTEXT_CHANGED','CONFLICTING_EVIDENCE','frozen source input / ledger / context changed')
    if e['event_type']!='INTEREST_RATE_CHANGE' or e['economic_variable'] not in {'policy_rate','benchmark_rate'}:
        add('EVENT_NOT_SUPPORTED','UNSUPPORTED_EVENT',e['event_type'])
    wanted='VERIFIED' if packet['mode']=='REAL' else 'ILLUSTRATIVE'
    for name,v in [('event',e),('exposure',x)]:
        if v['verification_status']=='CONFLICTING':add('CONFLICTING_'+name.upper(),'CONFLICTING_EVIDENCE',name)
        if v['verification_status']!=wanted:add('UNVERIFIED_'+name.upper(),'INSUFFICIENT_EVIDENCE',name)
    if (x['cik'],x['ticker'])!=(c['cik'],c['ticker']):add('EXPOSURE_ISSUER_MISMATCH','CONFLICTING_EVIDENCE',x['exposure_id'])
    if c['accounting_scope'].upper().startswith(('UNKNOWN','UNVERIFIED')):add('ACCOUNTING_SCOPE_UNKNOWN','INSUFFICIENT_EVIDENCE','interest accounting scope not established')
    if e['rate_reference'] is None or e['rate_currency'] is None:add('RATE_REFERENCE_MISSING','INSUFFICIENT_EVIDENCE','specific policy/benchmark and rate currency required')
    elif e['rate_currency']!=c['currency']:add('RATE_CURRENCY_MISMATCH','INCOMPATIBLE_UNITS','no cross-currency transmission inference')
    if x['currency']!=c['currency']:add('EXPOSURE_CURRENCY_MISMATCH','INCOMPATIBLE_UNITS',x['currency'])
    if x['accounting_scope']!=c['accounting_scope']:add('EXPOSURE_SCOPE_MISMATCH','CONFLICTING_EVIDENCE',x['accounting_scope'])
    if x['reporting_period']!=c['reporting_period']:add('EXPOSURE_PERIOD_MISMATCH','INVALID_PERIOD','exposure reporting period')
    period=c['reporting_period']
    if period['end']>as_utc(c['as_of']).date().isoformat():add('FUTURE_REPORTING_PERIOD','INSUFFICIENT_EVIDENCE',period['end'])
    if period['basis']=='DURATION' and (period['start'] is None or period['start']>period['end']) or period['basis']=='INSTANT' and period['start'] is not None:
        add('INVALID_REPORTING_PERIOD','INVALID_PERIOD','basis/start/end')
    if c['calculation_start']>=c['calculation_end']:add('INVALID_CALCULATION_PERIOD','INVALID_PERIOD','start must precede exclusive end')
    if p['day_count'] is None:add('DAY_COUNT_MISSING','INSUFFICIENT_EVIDENCE','contract day-count is required')
    elif p['day_count'] not in calc.DAY_COUNTS:add('DAY_COUNT_UNSUPPORTED','INVALID_PERIOD',p['day_count'])
    bound={}
    for name,kind in BINDINGS.items():
        f=facts.get(x['bindings'][name])
        if not f:add('MISSING_'+name.upper(),'INSUFFICIENT_EVIDENCE',kind);continue
        if f['kind']!=kind:add('FACT_COMPONENT_MISMATCH','CONFLICTING_EVIDENCE',name)
        bound[name]=f
    for name,kind in [('magnitude_fact_id','event_delta'),('effective_fact_id','event_effective_date')]:
        f=facts.get(e[name])
        if not f:add('EVENT_FACT_MISSING','INSUFFICIENT_EVIDENCE',kind)
        elif f['kind']!=kind:add('EVENT_FACT_KIND_MISMATCH','CONFLICTING_EVIDENCE',kind)
    values={k:v['value'] for k,v in bound.items()}
    if 'interest_bearing' in values and values['interest_bearing'] is not True:add('INTEREST_BEARING_NOT_ESTABLISHED','INSUFFICIENT_EVIDENCE','not total liabilities')
    if 'principal' in bound:
        f=bound['principal']
        if f['unit'] not in calc.MONEY_SCALES or f['currency']!=c['currency']:
            add('PRINCIPAL_UNIT_OR_CURRENCY','INCOMPATIBLE_UNITS',f['unit'])
    expected_units={'repricing_fraction':'fraction','rate_mapping':'fraction','hedged_fraction':'fraction',
        'reset_date':'date','day_count':'convention','interest_bearing':'boolean',
        'hedge_terms':'contract','principal_schedule':'contract','accounting_treatment':'contract'}
    for name,unit in expected_units.items():
        if name in bound and bound[name]['unit']!=unit:add('COMPONENT_UNIT_MISMATCH','INCOMPATIBLE_UNITS',name)
    if 'day_count' in values and p['day_count'] is not None and values['day_count']!=p['day_count']:add('DAY_COUNT_CONTRACT_MISMATCH','INVALID_PERIOD','explicit policy differs from contract')
    if 'principal_schedule' in values and values['principal_schedule']!='CONSTANT_CONTRACTUAL':
        add('AMORTIZATION_UNSUPPORTED','INSUFFICIENT_EVIDENCE','constant contractual principal required; no interpolation')
    if 'accounting_treatment' in values and values['accounting_treatment']!='EXPENSED_PRETAX':
        add('ACCOUNTING_TREATMENT_UNSUPPORTED','INSUFFICIENT_EVIDENCE','capitalization/tax effects not modeled')
    if e['delta'] is None:add('EVENT_MAGNITUDE_MISSING','INSUFFICIENT_EVIDENCE','no default rate shock')
    if e['effective_date'] is None:add('EVENT_EFFECTIVE_DATE_MISSING','INSUFFICIENT_EVIDENCE','no inferred reset date')
    try:
        with localcontext() as ctx:
            ctx.prec=50;ctx.rounding=ROUND_HALF_EVEN
            for name in ['principal','repricing_fraction','rate_mapping','hedged_fraction']:
                if name in values:
                    n=calc.number(values[name])
                    if n<0 or name!='principal' and n>1:add('NUMERIC_DOMAIN_INVALID','CALCULATION_FAILED',name)
            if 'hedged_fraction' in values and 'hedge_terms' in values:
                h=calc.number(values['hedged_fraction']);terms=values['hedge_terms']
                if (h==0 and terms!='NONE') or (h>0 and terms!='FIXED_OFFSET'):
                    add('HEDGE_TERMS_UNSUPPORTED','INSUFFICIENT_EVIDENCE','requires explicit none or fixed-offset coverage')
            mf=facts.get(e['magnitude_fact_id']);ef=facts.get(e['effective_fact_id'])
            for f in [mf,ef]:
                if f and (f['event_reference']!=e['rate_reference'] or f['rate_currency']!=e['rate_currency']):
                    add('EVENT_REFERENCE_CONFLICT','CONFLICTING_EVIDENCE',f['fact_id'])
            if mf and e['delta']:
                if mf['unit'] not in calc.RATE_SCALES:add('EVENT_RATE_UNIT','INCOMPATIBLE_UNITS',mf['unit'])
                elif calc.delta_rate({'value':mf['value'],'unit':mf['unit']})!=calc.delta_rate(e['delta']):add('EVENT_MAGNITUDE_CONFLICT','CONFLICTING_EVIDENCE',mf['fact_id'])
            if ef and ef['value']!=e['effective_date']:add('EVENT_EFFECTIVE_DATE_CONFLICT','CONFLICTING_EVIDENCE',ef['fact_id'])
            if ef and ef['unit']!='date':add('EVENT_DATE_UNIT','INCOMPATIBLE_UNITS',ef['unit'])
            delta=packet['assumptions']['rate_delta_override'] or e['delta']
            if 'rate_mapping' in bound:
                link=bound['rate_mapping']['rate_link']
                if link is None:add('RATE_TRANSMISSION_CONTRACT_MISSING','INSUFFICIENT_EVIDENCE','policy rate is not automatically applicable coupon')
                else:
                    low,high=calc.number(link['minimum_delta_bp']),calc.number(link['maximum_delta_bp'])
                    if low>high or link['event_variable']!=e['economic_variable'] or link['event_reference']!=e['rate_reference'] or link['rate_currency']!=e['rate_currency']:
                        add('RATE_LINK_CONFLICT','CONFLICTING_EVIDENCE','mapping scope, benchmark identity or currency')
                    if delta and not low<=calc.delta_rate(delta)*10000<=high:
                        add('OUTSIDE_LINEAR_CONTRACT_RANGE','INSUFFICIENT_EVIDENCE','cap/floor/nonlinear change not supported')
            if 'reset_date' in values:
                try:date.fromisoformat(values['reset_date'])
                except (TypeError,ValueError):add('RESET_DATE_INVALID','INVALID_PERIOD','contract reset date')
            if delta:
                fingerprint=scenario_fingerprint(packet)
                for prior in packet['prior_calculations']:
                    if prior['start']>=prior['end']:add('INVALID_PRIOR_PERIOD','INVALID_PERIOD','prior calculation')
                    if (prior['cik'],prior['canonical_event_id'],prior['instrument_id'],prior['scenario_fingerprint'])==(c['cik'],e['canonical_event_id'],x['instrument_id'],fingerprint) and max(prior['start'],c['calculation_start'])<min(prior['end'],c['calculation_end']):
                        add('DUPLICATE_OR_OVERLAPPING_CALCULATION','CALCULATION_FAILED','canonical event, instrument, scenario and time overlap')
    except (ValueError,ArithmeticError,TypeError,KeyError):add('INVALID_ARITHMETIC_INPUT','CALCULATION_FAILED','invalid Decimal/date/contract value')
    return issues,bound


def primary_status(issues):
    order=['UNSUPPORTED_EVENT','CONFLICTING_EVIDENCE','INCOMPATIBLE_UNITS','INVALID_PERIOD','STALE_EVIDENCE','INSUFFICIENT_EVIDENCE','CALCULATION_FAILED']
    return min((i['status'] for i in issues),key=order.index) if issues else 'ARITHMETIC_VERIFIED_CONDITIONAL'
