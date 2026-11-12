"""Historical evidence is not current eligibility; no silent partial proration."""
from datetime import date
from research.rights import as_utc


def check(assessment, as_of, window, *, event_effective=None):
    issues = []
    if not assessment['supported']: issues.append('SEMANTIC_SUPPORT_REQUIRED')
    at = as_utc(as_of).date().isoformat(); start,end = window['start'],window['end']
    if start >= end: issues.append('INVALID_CALCULATION_PERIOD')
    for name in ['contract_period','hedge_period']:
        p = assessment[name]
        if p is None: issues.append('SOURCE_' + name.upper() + '_MISSING'); continue
        if not p['start'] <= at < p['end']: issues.append(name.upper() + '_NOT_EFFECTIVE_AS_OF')
        if not p['start'] <= start < end <= p['end']: issues.append(name.upper() + '_DOES_NOT_COVER_CALCULATION')
    if assessment['annual_reset']:
        # No reviewer may choose the earlier annual occurrence to accelerate a shock.
        effective = max(start, event_effective or start)
        claimed = date.fromisoformat(assessment['reset_date']); lower = date.fromisoformat(effective)
        try:
            next_reset = claimed.replace(year=lower.year)
            if next_reset < lower: next_reset = next_reset.replace(year=lower.year + 1)
            if claimed != next_reset: issues.append('ANNUAL_RESET_OCCURRENCE_NOT_APPLICABLE')
        except ValueError: issues.append('ANNUAL_RESET_OCCURRENCE_UNSUPPORTED')
    return {'evidence_verified':assessment['supported'],'calculation_eligible':not issues,
            'eligibility_as_of':as_of,'calculation_window':window,
            'eligibility_reason':issues or ['SUPPORTED_CURRENT_LINEAR_CONTRACT']}
