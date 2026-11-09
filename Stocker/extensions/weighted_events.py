"""Source-bound local event extraction; interpretation is never source approval."""

from collections import defaultdict
from decimal import Decimal, localcontext
import re
from research.rights import as_utc
from research.validation import stable_hash
from . import analysis_contract
from .weighted_features import number, clip, factor


def freshness(first_public_at, generated_at, settings):
    hours = Decimal(str((as_utc(generated_at) - as_utc(first_public_at)).total_seconds())) / 3600
    if hours < 0 or hours > settings["news"]["maximum_event_age_hours"]: raise ValueError("FUTURE_OR_EXPIRED_EVENT")
    with localcontext() as context:
        context.prec = 50
        return 1 + (Decimal(2).ln() * (-hours / number(settings["news"]["half_life_hours"]))).exp()


def event_value(event, cutoff, generated_at, settings):
    if event["status"] != "automatic_supported_event" or event["direction"] not in {"positive", "negative"} or event["horizon"] != "within_five_sessions":
        raise ValueError("DIRECTION_OR_FIVE_SESSION_IMPACT_UNRESOLVED")
    if event["time_precision"] not in {"minute", "instant"} or not event.get("published_at") or not event.get("first_public_at") or not event["evidence"]:
        raise ValueError("EVENT_TIMESTAMP_OR_EVIDENCE_UNVERIFIED")
    bound = as_utc(cutoff).replace(microsecond=0)
    if not as_utc(event["first_public_at"]) <= as_utc(event["published_at"]) <= as_utc(event["available_at"]) < bound:
        raise ValueError("EVENT_NOT_AVAILABLE_BEFORE_CUTOFF")
    values = [number(event[k]) for k in ("impact_strength", "issuer_relevance", "evidence_quality")]
    if any(not 0 < v <= 1 for v in values): raise ValueError("EVENT_SCALE_INVALID")
    multiplier = freshness(event["first_public_at"], generated_at, settings)
    signed = Decimal(1 if event["direction"] == "positive" else -1)
    return signed * values[0] * values[1] * values[2] * multiplier, multiplier


def coverage_reason(metadata, cutoff, generated_at):
    if metadata.get("status") != "bounded_verified": return "TOPIC_COVERAGE_UNVERIFIED"
    try:
        if not metadata.get("source_ids") or not as_utc(metadata["window_end"]) <= as_utc(metadata["checked_at"]) <= as_utc(metadata["available_at"]) < as_utc(cutoff).replace(microsecond=0): return "TOPIC_PROVENANCE_OR_TIME_INVALID"
        if not 0 <= (as_utc(generated_at) - as_utc(metadata["checked_at"])).total_seconds() <= 1800: return "TOPIC_COLLECTION_STALE"
        if as_utc(metadata["window_end"]) > as_utc(cutoff) or not 0 <= (as_utc(cutoff) - as_utc(metadata["window_end"])).total_seconds() <= 300 or (as_utc(metadata["window_end"]) - as_utc(metadata["window_start"])).total_seconds() < 72 * 3600: return "TOPIC_WINDOW_INCOMPLETE"
    except (KeyError, TypeError, ValueError): return "TOPIC_PROVENANCE_OR_TIME_INVALID"
    return None


def aggregate(events, coverage, cutoff, generated_at, settings):
    topics = {spec["topic"]: key for key, spec in settings["features"].items() if spec["group"] == "latest_events"}
    clusters = defaultdict(set)
    for event in events: clusters[event["cluster_id"]].add(event["topic"])
    result = {}
    for topic, key in topics.items():
        observed = [event for event in events if event["topic"] == topic]
        metadata = coverage.get(topic, {})
        reason = coverage_reason(metadata, cutoff, generated_at)
        if reason is None and any(len(clusters[event["cluster_id"]]) != 1 for event in observed): reason = "CROSS_TOPIC_DUPLICATION"
        total, sources, details = Decimal(0), set(metadata.get("source_ids", [])), []
        if reason is None:
            grouped = defaultdict(list)
            for event in observed: grouped[event["cluster_id"]].append(event)
            try:
                for cluster, versions in sorted(grouped.items()):
                    signatures = {(e["status"], e["direction"], e["horizon"], e.get("impact_strength"), e.get("issuer_relevance"), e.get("evidence_quality")) for e in versions}
                    if len(signatures) != 1: raise ValueError("CONFLICTING_EVENT_INTERPRETATIONS")
                    first = min(versions, key=lambda e: as_utc(e.get("first_public_at") or e["available_at"]))
                    value, multiplier = event_value(first, cutoff, generated_at, settings)
                    # Validate all duplicates too; a later malformed copy cannot revive approval.
                    for event in versions: event_value(event, cutoff, generated_at, settings)
                    total += value
                    sources.update(ref["source_id"] for event in versions for ref in event["evidence"])
                    details.append({"cluster_id": cluster, "signed_impact": str(value), "freshness_multiplier": str(multiplier), "deduplicated_sources": len(versions)})
            except (ValueError, KeyError, TypeError) as error:
                reason = str(error) if isinstance(error, ValueError) else "EVENT_EVIDENCE_INCOMPLETE"
        result[key] = factor(key, settings, clip(total) if reason is None else None,
            raw={"topic": topic, "coverage": metadata.get("status", "unknown"), "events": details, "bounded_scope_only": True},
            source_ids=sorted(sources), reason=reason)
    return result


def analyze(ai, generated_at, settings):
    """Autonomously produce grounded financial facts and conditional business channels."""
    mechanism = settings["issuer_mechanisms"].get(ai["identity"]["cik"])
    if not mechanism or mechanism["symbol"] != ai["identity"]["symbol"]: raise ValueError("ISSUER_BUSINESS_PROFILE_UNAVAILABLE")
    output = analysis_contract.output_template(ai)
    output.update(status="partial", model={"provider": "local_deterministic_rules", "model_id": "no_ai_model_rules_v1", "prompt_version": settings["id"], "run_id": stable_hash([ai["packet_id"], generated_at, settings["id"]])})
    findings, events, seen = [], [], set()
    for fact in ai["financial_facts"]:
        finding = {"id": "fact_" + fact["fact_id"], "kind": "fact", "statement": f"{fact['metric']}: {fact['value']} {fact['unit']} · {fact['basis']} · {fact['start'] or 'instant'}~{fact['end']} · {fact['scope']}",
            "direction": "unknown", "horizon": "longer_term", "evidence_quality": "medium", "evidence": [{"kind": "financial_fact", "source_id": fact["fact_id"], "paragraph_id": None, "quote": None}],
            "numeric_support": [{"fact_id": fact["fact_id"], "value": fact["value"], "unit": fact["unit"]}]}
        findings.append(finding)
    for doc in ai["official_documents"]:
        for paragraph in doc["paragraphs"]:
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph["text"]):
                sentence = sentence.strip()
                if not sentence: continue
                rate = re.match(r"(?:The )?(?:Federal Open Market )?Committee decided to (lower|raise|maintain) the target range for the federal funds rate\b", sentence, re.I) if doc["source"] == "federal_reserve_board" else None
                energy = doc["source"] == "us_eia" and bool(re.search(r"\b(?:electricity|natural gas|crude oil|energy production|power generation)\b", sentence, re.I))
                if not rate and not energy: continue
                topic = "macro" if rate else "commodity"
                rule = "fed_policy_" + rate[1].lower() if rate else "energy_transmission_candidate"
                cluster = stable_hash([doc["url"], rule, doc["published_date"]])
                event_id = stable_hash([doc["source_id"], paragraph["id"], sentence])
                if event_id in seen: continue
                seen.add(event_id)
                citation = {"kind": "official_paragraph", "source_id": doc["source_id"], "paragraph_id": paragraph["id"], "quote": sentence}
                channel = mechanism["financing" if rate else "energy"]
                statement = channel + "를 점검할 공식 문구입니다. 기업 실적에 미치는 방향과 단기 가격 효과는 혼재하거나 미확정입니다."
                counter = "이미 알려진 내용일 수 있고 시장 기대와 다를 수 있습니다. 사업 영향이 장기에 나타날 수 있어 단기 가격 변동으로 단정하지 않습니다."
                events.append({"event_id": event_id, "cluster_id": cluster, "topic": topic,
                    "status": "analysis_candidate", "rule_id": rule, "direction": "mixed", "horizon": "unknown", "source_url": doc["url"], "evidence": [citation],
                    "published_at": doc["published_at"], "first_public_at": doc["published_at"], "time_precision": doc["time_precision"], "available_at": doc["available_at"],
                    "impact_strength": None, "issuer_relevance": None, "evidence_quality": None, "conditional_business_channel": statement, "counterargument": counter,
                    "scoring_reason": "PRECISE_TIME_EXPECTATION_AND_FIVE_SESSION_IMPACT_NOT_ESTABLISHED", "human_reviewed": False, "analysis_priority": "official_policy" if rate else "business_context"})
                findings += [{"id": "interpret_" + events[-1]["event_id"], "kind": "interpretation", "statement": statement, "direction": "mixed", "horizon": "unknown", "evidence_quality": "low", "evidence": [citation], "numeric_support": []},
                    {"id": "counter_" + events[-1]["event_id"], "kind": "counterargument", "statement": counter, "direction": "mixed", "horizon": "unknown", "evidence_quality": "low", "evidence": [citation], "numeric_support": []}]
    if not events and ai["financial_facts"]:
        fact = ai["financial_facts"][0]
        findings.append({"id": "counter_financial", "kind": "counterargument", "statement": "공시 실적의 양호함이 다음 관찰기간의 가격 상승을 보장하지 않습니다. 기대 반영과 새 사건의 상반된 해석을 추가로 확인해야 합니다.",
            "direction": "mixed", "horizon": "unknown", "evidence_quality": "low", "evidence": [{"kind": "financial_fact", "source_id": fact["fact_id"], "paragraph_id": None, "quote": None}], "numeric_support": []})
    output["findings"] = findings
    output["limitations"] = ["Local deterministic extraction and conditional business channels; no external AI model or causal price-impact validation", "Company/sector/political original-body coverage is incomplete; no generic sentiment scoring"]
    analysis_contract.validate_output(output, ai)
    coverage = {spec["topic"]: {"status": "unavailable", "reason": "NO_COMPLETE_BOUNDED_TOPIC_INTERPRETATION"} for spec in settings["features"].values() if spec["group"] == "latest_events"}
    return {"analysis_output": output, "events": events, "topic_coverage": coverage, "factors": aggregate(events, coverage, ai["as_of"], generated_at, settings),
        "automatic_rule_analysis_performed": True, "external_ai_analysis_performed": False, "human_review_required_for_normal_use": False}
