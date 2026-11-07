# Free Public Service — Operations and Delivery Gates

> Deferred public-service gates. Active scope changed on 2026-11-07 to personal-local research, USD 0 data budget. Public infrastructure/legal launch gates below are not prerequisites for the authorized personal CLI; use MARKET_PREPARATION.md for current setup and retention duties.

## Cost model

    monthly_cost =
        unique_new_reports * (input_tokens / 1e6 * model_input_rate +
                              output_tokens / 1e6 * model_output_rate)
        + API/vendor fees + compute + storage + logs + bandwidth

Model and vendor prices change; measure actual usage before setting production caps. At present NO paid service is selected or activated.

## Starting controls (PROPOSED, tune after load test)

- Cached reports publicly readable without model calls.
- New generation: e.g., 3 requests per pseudonymous IP bucket per 24 hours; server-wide maximum N reports/day set by operator.
- Concurrent LLM jobs: start with 1 worker, at most 2 in flight; limit queue length (e.g., 50).
- Shared request dedupe and EOD/document snapshot cache; repeated requests return cached ID.
- Hard monthly budget threshold kills new generations, keeps cached reports available.
- Prompt/input max length; fixed model max output; no public arbitrary URL fetching.
- Report worker has per-stage timeouts, exponential backoff for transient providers and limited retry counts.
- Use bounded TTL for hashed IP/quota metadata, protect all API keys, and avoid logging raw prompts containing personal information.

Numbers above are test defaults, NOT measured capacity promises.

## Telemetry

Collect per provider: rate-limit/timeout/error rate, data freshness, response-size and cost. Per report: generation duration, cache hit, model/version, prompt hash, token count, input snapshot IDs, validation warnings, failure reason and publication state. Store report source IDs and immutable output for incident review.

Critical dashboard signals: wrong-symbol incidents, source-citation mismatch, stale data shown as current, bypassed license gate, abuse spikes, daily/monthly token spend and report quality gate failures.

## Security and privacy

- No login required to READ public reports. Limit create operations.
- Do not solicit portfolios, brokerage auth, precise financial position or identity data in MVP.
- CORS origins explicit, secure cookies only if introduced, CSP and XSS-safe rendering of source text, server-side secrets, dependency updates, SSRF allowlist for retrieval.
- Privacy page covers minimal analytics/quota metadata, retention period and contact.
- User text and source material are untrusted. Guard against prompt injection and leaking internal prompt/config.

## Legal/reputation gates

- The product is general, impersonal research information; do not offer individualized trade size/entry/exit or guaranteed outcome.
- Before public rollout to Korean users: review financial advice/marketing categorization, terms of service, data provider agreements, privacy notice and handling of inaccurate reports.
- A simple "not financial advice" footer does not replace compliance review.
- Establish correction workflow: unpublished/withdrawn reports remain auditable, visible error warning and replacement link.
- Leverage/inverse ETF warnings are mandatory when relevant; disclose daily reset and compounding path risk.

## Public beta launch checklist

- [ ] Approved source entitlements with proof of allowed public report/AI use
- [ ] SEC User-Agent and global throttling checked in staging
- [ ] All published numbers and source IDs pass deterministic checks
- [ ] 10+ golden cases run and record false facts; manual citation review
- [ ] Price data missing yields graceful partial report; no fake quotes
- [ ] Rate limit / duplicate job / concurrent request load test passes
- [ ] Token budget and outage kill switches demonstrated
- [ ] Security/privacy and public content legal review completed
- [ ] Terms, contact, reporting/corrections and disclosure pages accessible
- [ ] Observability, backup and rollback tested

## Implementation order (estimated 3–5 weeks for a solo part-time MVP)

1. Foundation: schemas, DB, resolver and SEC adapter with fixture tests; begin price-source rights and swing-methodology review in parallel.
2. Finance kernel: normalized facts, point-in-time cutoff and deterministic metrics.
3. AI report pipeline: typed input, structured output, adversarial review and validators. In a separately gated swing slice add licensed EOD inputs, session calendar, conditional target/invalidation/ratios, expiry and independently validated scoring/timing.
4. Public API: job queue, dedupe, quotas, caching, errors and observability.
5. React UI: search, status, swing summary first, underlying research/scenarios, source drilldown, unavailable/expired/historical states.
6. Coverage expansion: curated ETF/sector beta, additional valuation/chart features after the licensed swing-price path has passed its independent gates.

An estimate is not a committed date. Ship vertical slices only after tests and rights review.

## Swing activation gates

- [ ] Licensed OHLCV/corporate-action inputs and point-in-time market calendar verified
- [ ] Target/invalidation methodology, arithmetic, quote basis and provenance tests implemented
- [ ] Unavailable price plan and score paths tested without blocking SEC-only research
- [ ] Five-session observation window, next-session-close EOD expiry and historical rendering verified
- [ ] Attractiveness methodology/weights fixed before evaluation; held-out validation recorded before numeric activation
- [ ] Target-time model separately validated before estimated target sessions are shown
- [ ] Same-bar target/invalidation ambiguity, fees/slippage and out-of-sample evaluation documented

These are uncompleted runtime/model gates. Contract tests alone do not satisfy them. The earlier time estimate excludes uncertain vendor contracting and model validation lead times.
