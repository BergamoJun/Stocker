# Public Service Architecture v0.3

> Deferred design. The user changed the active scope to personal-local research with a USD 0 data budget on 2026-11-07. This document does not require public infrastructure or public price rights before the personal CLI can proceed. See MARKET_PREPARATION.md.

## Design style

Small modular monolith + background worker. Three deployable units only when necessary: frontend, API, worker. A public service needs cost and concurrency controls from day one, but does NOT need multiple cooperating AI agents, Kubernetes, vector DB or real-time streaming for MVP.

## Data flow

    Browser (React) -> FastAPI -> resolver + permission gate
                           |            |
                           |            +-> allowed source adapter -> raw snapshot
                           |                                      -> normalized facts
                           +-> report cache / job deduplication
                           +-> queue -> Worker
                                         |
                                         +-> MetricEngine (deterministic)
                                         +-> Evidence packer
                                         +-> Equity/Fund/Sector analyzer
                                         +-> Red-team critic
                                         +-> Report composer (structured JSON)
                                         +-> Rule validator
                                         +-> Published report (PostgreSQL)
    Browser <- status polling / cached published report <- FastAPI

**Pipeline gate:** rights check is applied before source ingestion, before model context creation, and before public output. If a source's allowed uses are unknown, do not use its data in a public generated report.

## Recommended stack

| Area | Recommendation | Rationale |
|---|---|---|
| Web | React + TypeScript + Vite | Familiar stack, accessible report cards |
| API | FastAPI + Pydantic | Python data calculations and schema validation |
| Database | PostgreSQL | instruments, facts, reports, snapshots and job state |
| Queue | Redis + RQ/Arq or equivalent | bounded asynchronous report tasks, deduplication |
| Jobs | single Python worker to start | simple deployment, explicit concurrency cap |
| Files | local dev files; object storage for licensed and permitted raw documents | reproducible snapshots |
| Calculations | Python Decimal / Pandas or Polars as appropriate | unit-tested deterministic finance operations |
| AI | one configurable model + system/domain/critic/writer prompts | track token spend and test before multi-model expansion |
| Ops | structured logs + Sentry-compatible errors + budgets | visible outages and cost protection |

Cloud vendors and live model are deliberately not chosen yet.

## Suggested code layout (future, NOT implemented)

    apps/web/
    services/api/app/main.py
    services/api/app/routes/{instruments,reports,jobs}.py
    services/api/app/domain/{models,schemas,metrics,instruments}.py
    services/api/app/adapters/{sec,price_vendor,issuer,macro}.py
    services/api/app/research/{orchestrator,evidence,validator,renderer}.py
    services/worker/
    infra/{migrations,containers}
    docs/, prompts/, schemas/, tests/, config/

## Data model

- instruments: canonical ID, ticker, exchange, CIK if applicable, issuer, asset type, leverage metadata, active and updated.
- source_documents: source ID, publisher, URL, document type, published_at, retrieved_at, raw hash, license/status and storage pointer.
- financial_facts: source reference, XBRL concept, standardized field, value, currency/unit, period_start/end, filed_at, fiscal_period, amendment provenance.
- normalized_metrics: metric name, value, formula version, input fact IDs, evaluation cutoff and unit.
- research_jobs: job ID, idempotency key, requester quota bucket, status, retry count, failure code, timestamps.
- reports: report ID, instrument, profile, schema/prompt/model versions, source snapshot hash, as_of, published state, JSON payload and quality status.
- report_sources: report-to-document refs for stable citation support.
- usage_counters: pseudonymous/short-lived rate-limit keys and aggregate cost counts; minimize raw IP retention.

## Job lifecycle

    accepted -> fetching -> calculating -> analyzing -> critiquing -> validating -> published
                      \-> failed  \-> blocked       \-> failed

- Queue is bounded. Job timeouts and retry budgets are explicit. Do not recursively trigger analysis on failed jobs.
- Deduplicate using normalized subject + horizon + focus hash + snapshot ID + prompt/model/schema versions. For swing include price snapshot, selected proxy, target/scoring/timing methodology and validation IDs, calendar version, and entitlement state.
- A published report is immutable; new version gets a new ID. Latest report pointer may change.
- Changes in source filings or permission state invalidate the appropriate cached report; expired reports remain historical with original as_of.
- Model calls are not allowed to request arbitrary URLs; only vetted adapters retrieve external data.

## Caching / freshness

- Quote cache disabled until licensed; when activated use exchange-trading-session EOD semantics, not naive 24-hour freshness.
- SEC filing records cached with revision provenance; jobs only compare eligible source documents published by as_of.
- Public reports share cache where inputs are identical. A personalized focus question yields a separate dedup key.
- Existing reports can display under outage, with original as_of and an out-of-date banner.
- No silent refresh that changes content under an existing report ID.

## Trust boundaries

Browser input is untrusted; normalize symbols and validate lengths. SEC/issuer text is **data**, not instructions. Run separate deterministic validators (source-ID existence, publication cutoff, metric equality, PII/secret scans) after the LLM; JSON Schema alone is not proof of factual validity.

Security baseline: strict CORS allowlist, backend-only secrets, output encoding for source excerpts, SSRF protections in source fetching, request-size caps, access logs with redaction, public endpoint quotas, circuit breaker for API/model failures.

## Swing profile (planned)

MetricEngine produces a trusted `SWING_INPUT` with price-plan eligibility, deterministic price/ratio outputs, snapshot/calculation/methodology references, calendar-aware review/expiry, and separately gated scoring/timing outputs. The LLM may explain the scenario and write a one-line view; it may not invent or overwrite these values.

The existing filing/business report remains available when the swing plan is unavailable. UI renders the swing card first for horizon swing and preserves source-backed research below it. Scoring may remain unavailable even when a price plan is available. A sector plan must identify a verified tradable ETF proxy and retain the distinction from sector-level analysis.

EOD plans expire at the next regular session close by default; a new snapshot or material catalyst change prompts reevaluation sooner. Market/session rules, methodology versions and entitlement changes invalidate current-use cache eligibility. Historical immutable reports remain readable with expiry/out-of-date labels when retention/publication rights allow. Read-time validity must be derived by the server, without mutating the saved report. Expiry, quota limits and outages must not silently reuse an old plan as current.

Research-only candidate price/scoring/timing mathematics, calendar handling, technical entitlement checks and GDELT metadata discovery now exist. They are not empirically validated or connected to a released recommendation service. Normative details: SWING_REPORT_SPEC.md.
