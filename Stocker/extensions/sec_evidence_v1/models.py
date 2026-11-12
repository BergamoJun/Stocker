"""Adapter semantics are explicit; discovery never certifies contract terms."""
from typing import TypedDict

STATES=['DISCOVERED','SOURCE_BOUND','REVIEW_REQUIRED','VERIFIED','CONFLICTED','STALE','UNSUPPORTED','UNKNOWN']
METRICS=['interest_bearing_borrowings','floating_rate_borrowings','fixed_rate_borrowings',
         'debt_maturity','contractual_interest_rate','reference_rate_index','reset_provisions',
         'interest_rate_hedges','applicable_principal','contract_effective_dates','general_debt',
         'interest_expense_context','swap_notional_context']


class ExposureCandidate(TypedDict):
    candidate_id:str
    source_key:str
    metric:str
    value:str|None
    unit:str|None
    instrument_id:str|None
    status:str


class SourceEvidence(TypedDict):
    source_key:str
    cik:str
    ticker:str
    accession:str
    original_sha256:str
    derived_text_sha256:str|None
    incarnation_id:str
    available_at:str


class ContractTerms(TypedDict):
    rate_type:str
    exposure:dict
    facts:list[dict]
    witnesses:dict


class ReviewBinding(TypedDict):
    binding:dict
    reviewer_id:str|None
    reviewed_at:str|None
    terms:dict|None


class VerifiedExposure(TypedDict):
    instrument_id:str
    exposure:dict
    facts:list[dict]
    review_receipt_hashes:list[str]


class MissingEvidence(TypedDict):
    field:str
    reason:str
    source_links:list[str]


class ExposureConflict(TypedDict):
    code:str
    candidate_ids:list[str]
    resolution:str


class AdapterResult(TypedDict):
    contract:str
    identity:dict
    as_of:str
    candidates:list[dict]
    verified_exposures:list[dict]
    missing_evidence:list[dict]
    conflicts:list[dict]
