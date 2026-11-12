"""JSON-facing types. Decimal amounts cross the boundary as strings only."""
from typing import TypedDict

STATUSES = ('ARITHMETIC_VERIFIED_CONDITIONAL','INSUFFICIENT_EVIDENCE',
            'CONFLICTING_EVIDENCE','INCOMPATIBLE_UNITS','INVALID_PERIOD',
            'STALE_EVIDENCE','UNSUPPORTED_EVENT','CALCULATION_FAILED')
RELEASE_FIELDS = ('stock_return_prediction','target_price','attractiveness_score',
                  'buy_price','sell_price','recommended_allocation')
BINDINGS = {
    'interest_bearing':'interest_bearing',
    'principal':'repricing_principal',
    'repricing_fraction':'repricing_fraction',
    'rate_mapping':'contract_rate_mapping',
    'reset_date':'rate_reset_date',
    'day_count':'day_count',
    'hedged_fraction':'hedged_fraction',
    'hedge_terms':'hedge_terms',
    'principal_schedule':'principal_schedule',
    'accounting_treatment':'accounting_treatment',
}


class EconomicEvent(TypedDict):
    event_id: str
    canonical_event_id: str
    event_type: str
    economic_variable: str
    rate_reference: str | None
    rate_currency: str | None
    delta: dict | None
    effective_date: str | None
    magnitude_fact_id: str | None
    effective_fact_id: str | None
    verification_status: str
    uncertainties: list[str]


class IssuerExposure(TypedDict):
    exposure_id: str
    instrument_id: str
    cik: str
    ticker: str
    currency: str
    accounting_scope: str
    reporting_period: dict
    verification_status: str
    bindings: dict[str, str | None]


class ScenarioAssumptions(TypedDict):
    scenario_id: str
    label: str
    rate_delta_override: dict | None
    description: str
    valid_if: list[str]
    invalid_if: list[str]
    probability: None


class CalculationPolicy(TypedDict):
    version: str
    day_count: str | None
    precision: int
    rounding: str
    max_source_age_days: int
    trusted_review_hashes: list[str]


class PointInTimeContext(TypedDict):
    cik: str
    ticker: str
    as_of: str
    currency: str
    accounting_scope: str
    reporting_period: dict
    calculation_start: str
    calculation_end: str
