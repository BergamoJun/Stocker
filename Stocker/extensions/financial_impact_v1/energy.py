"""Energy procurement/sales interface only; no verified SEC contract corpus yet."""
import jsonschema
from research.rights import as_utc
from . import contract as c
from .calculations import number

SCHEMA=c.obj({'contract':{'const':'financial-impact-energy-interface-v1'},'context':c.CONTEXT,
    'path':{'enum':['ENERGY_PROCUREMENT_COST','ENERGY_SALES_REVENUE']},'event_id':c.ID,'energy_type':c.TEXT,
    'quantity':c.nullable(c.NUMBER),'quantity_unit':c.TEXT,'price_delta':c.nullable(c.NUMBER),'price_unit':c.TEXT,
    'contract_price_index':c.nullable(c.TEXT),'hedge_terms':c.nullable(c.TEXT),'pass_through_fraction':c.nullable(c.NUMBER),
    'source_id':c.nullable(c.ID),'fact_ids':c.array(c.ID),'original_sha256':c.nullable(c.HASH),
    'published_at':c.nullable(c.TIME),'available_at':c.nullable(c.TIME),'verification_status':c.VERIFICATION})


def inspect(value):
    result={'status':'UNSUPPORTED_EVENT','implementation_status':'DEFERRED','amount':None,'evidence_check_status':'NOT_RUN_INTERFACE_ONLY',
        'reason':'ENERGY_ARITHMETIC_DEFERRED_NO_VERIFIED_CONTRACT_CORPUS','missing':[]}
    try:jsonschema.Draft202012Validator(SCHEMA,format_checker=jsonschema.FormatChecker()).validate(value)
    except jsonschema.ValidationError:return {**result,'status':'CALCULATION_FAILED','reason':'ENERGY_CONTRACT_INVALID'}
    ccy=value['context']['currency'];units={'electricity':'MWh','natural_gas':'MMBtu','crude_oil':'barrel'}
    unit=units.get(value['energy_type'])
    if unit is None or value['quantity_unit']!=unit or value['price_unit']!=ccy+'/'+unit:
        return {**result,'status':'INCOMPATIBLE_UNITS','reason':'NO_IMPLICIT_ENERGY_OR_CURRENCY_CONVERSION'}
    if value['context']['calculation_start']>=value['context']['calculation_end']:
        return {**result,'status':'INVALID_PERIOD','reason':'INVALID_ENERGY_PERIOD'}
    try:
        if value['quantity'] is not None and number(value['quantity'])<0 or value['pass_through_fraction'] is not None and not 0<=number(value['pass_through_fraction'])<=1:
            return {**result,'status':'CALCULATION_FAILED','reason':'ENERGY_NUMERIC_DOMAIN_INVALID'}
        if value['price_delta'] is not None:number(value['price_delta'])
    except (ValueError,ArithmeticError):return {**result,'status':'CALCULATION_FAILED','reason':'ENERGY_NUMERIC_DOMAIN_INVALID'}
    missing=[k for k in ['quantity','price_delta','contract_price_index','hedge_terms','pass_through_fraction','source_id','original_sha256','published_at','available_at'] if value[k] is None]
    if not value['fact_ids']:missing.append('fact_ids')
    if value['verification_status'] not in {'VERIFIED','ILLUSTRATIVE'}:missing.append('verified_contract')
    if value['available_at'] and as_utc(value['available_at'])>=as_utc(value['context']['as_of']):missing.append('point_in_time_eligibility')
    if value['published_at'] and value['available_at'] and as_utc(value['published_at'])>as_utc(value['available_at']):missing.append('publication_order')
    if missing:return {**result,'status':'INSUFFICIENT_EVIDENCE','missing':missing}
    return result
