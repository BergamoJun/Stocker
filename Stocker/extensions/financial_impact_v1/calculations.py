"""Linear simple-interest arithmetic with explicit boundaries and no FX inference."""
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN, InvalidOperation

DAY_COUNTS={'ACT/365F-v1':365,'ACT/360-v1':360}
MONEY_SCALES={'CURRENCY':Decimal(1),'THOUSAND_CURRENCY':Decimal(1000),'MILLION_CURRENCY':Decimal(1000000)}
RATE_SCALES={'bp':Decimal('0.0001'),'percentage_point':Decimal('0.01'),'decimal_rate':Decimal(1)}


def number(value):
    if not isinstance(value,str):raise ValueError('DECIMAL_STRING_REQUIRED')
    n=Decimal(value)
    if not n.is_finite() or len(n.as_tuple().digits)>32 or n.adjusted()>30 or n.adjusted() < -24:
        if n==0:return n
        raise ValueError('DECIMAL_OUTSIDE_SUPPORTED_RANGE')
    return n


def decimal_text(n):
    if n==0:return '0'
    text=format(n,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def delta_rate(delta):
    with localcontext() as ctx:
        ctx.prec=50;ctx.rounding=ROUND_HALF_EVEN
        return number(delta['value'])*RATE_SCALES[delta['unit']]


def interest(principal,principal_unit,fraction,hedged_fraction,rate,link,start,end,day_count):
    """Start included/end excluded. reset/effective dates are selected upstream."""
    with localcontext() as ctx:
        ctx.prec=50;ctx.rounding=ROUND_HALF_EVEN
        p=number(principal)*MONEY_SCALES[principal_unit]
        f,h,m=map(number,[fraction,hedged_fraction,link])
        change=delta_rate(rate);days=(date.fromisoformat(end)-date.fromisoformat(start)).days
        denominator=DAY_COUNTS[day_count]
        if days<0 or p<0 or not 0<=f<=1 or not 0<=h<=1 or not 0<=m<=1:
            raise ValueError('ARITHMETIC_DOMAIN_INVALID')
        # Multiply integer days before division to avoid premature rounding of 1/365.
        active=p*f*(1-h);applicable=change*m
        amount=active*applicable*Decimal(days)/Decimal(denominator)
        if not amount.is_finite():raise InvalidOperation('NONFINITE_RESULT')
        return {'principal_in_currency':decimal_text(p),'repricing_fraction':decimal_text(f),
            'hedged_fraction':decimal_text(h),'unhedged_repricing_principal':decimal_text(active),
            'event_delta_decimal_rate':decimal_text(change),'event_to_applicable_rate_factor':decimal_text(m),
            'applicable_rate_delta':decimal_text(applicable),'effective_start':start,'effective_end':end,
            'effective_days':days,'year_denominator':denominator,'day_count':day_count,
            'effective_time_fraction':decimal_text(Decimal(days)/Decimal(denominator)),
            'amount':decimal_text(amount),'precision':50,'rounding':'ROUND_HALF_EVEN',
            'formula':'principal * repricing_fraction * (1-hedged_fraction) * event_rate_delta * contract_mapping * actual_days / year_denominator'}
