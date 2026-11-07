"""Explicit source entitlements; credentials or a free tier are not a license."""

from datetime import datetime, timezone
from urllib.parse import urlparse


class RightsError(ValueError):
    pass


def as_utc(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp is missing or not a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def require_entitlement(record, uses, as_of, *, synthetic=False):
    """Validate an operator-reviewed record, not the legal truth of its claims."""
    cutoff = as_utc(as_of)
    if type(record.get("is_illustrative")) is not bool or record["is_illustrative"] != synthetic:
        raise RightsError("Synthetic and real source entitlements cannot be interchanged")
    if record.get("monthly_cost_usd") != 0 or isinstance(record.get("monthly_cost_usd"), bool):
        raise RightsError("Monthly data budget is USD 0; paid or unknown-cost entitlement rejected")
    for key in ("id", "provider", "reviewer", "reviewed_at", "review_due_at", "evidence_url", "plan"):
        if not record.get(key):
            raise RightsError(f"Missing entitlement metadata: {key}")
    evidence = urlparse(record["evidence_url"])
    if evidence.scheme != "https" or not evidence.hostname:
        raise RightsError("Entitlement requires a direct HTTPS evidence/contract reference")
    if not as_utc(record["reviewed_at"]) <= cutoff < as_utc(record["review_due_at"]):
        raise RightsError("Entitlement is future-dated or due for review")
    if record.get("account_eligibility_required"):
        if record.get("account_eligibility_verified") is not True:
            raise RightsError("Personal/non-professional account eligibility has not been confirmed")
        confirmed = record.get("account_confirmed_at")
        if not confirmed or as_utc(confirmed) > cutoff:
            raise RightsError("Account confirmation is missing or after the requested cutoff")
    missing = [use for use in uses if record.get("uses", {}).get(use) != "allowed"]
    if missing:
        raise RightsError("Source use not cleared: " + ", ".join(missing))


def price_uses(scope):
    if scope not in {"local_research", "public_report"}:
        raise ValueError("Unknown data-use scope")
    uses = ["ingest", "store", "derived_analytics"]
    if scope == "public_report":
        uses += ["public_display", "llm_input"]
    return uses
