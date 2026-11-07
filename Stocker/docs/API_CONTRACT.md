# REST API Contract (design only)

> Deferred public API design. Current user-authorized scope is personal-local CLI, USD 0 data budget (2026-11-07). Current execution and source-use gates: MARKET_PREPARATION.md.

All paths prefixed with /api/v1. HTTPS only. JSON request/response. No trading or brokerage endpoints.

## Endpoints

| Method | Path | Behavior | Key responses |
|---|---|---|---|
| GET | /instruments?q=NVDA | Search canonical US symbols and asset types | 200 results; 400 bad query |
| GET | /coverage/{instrument_id} | Availability of filings/ETF docs/prices and feature flags | 200 |
| POST | /reports | Retrieve cached report or create analysis job | 200 cached or 202 queued; 422, 429, 503 |
| GET | /jobs/{job_id} | Poll processing state | 200 status; 404 |
| GET | /reports/{report_id} | Fetch immutable validated report with provenance | 200; 404 |
| GET | /reports?instrument_id=... | List latest cached public reports | 200 paginated |
| GET | /healthz | Liveness only, no secrets | 200 |
| GET | /readyz | Backend readiness (internal/ops) | 200 or 503 |

## POST /reports request

    {
      "instrument_id": "us-equity-cik-0000000000",
      "analysis_type": "auto",
      "horizon": "swing",
      "focus": "향후 약 5거래일의 촉매와 반론, 목표·무효화 조건",
      "language": "ko"
    }

- ILLUSTRATIVE — NOT LIVE MARKET DATA. All IDs, timestamps and responses on this page are synthetic interface examples.
- Only instrument IDs returned by resolver are accepted. No arbitrary URLs or prompt instructions are forwarded to tools.
- analysis_type: auto/equity/fund/sector; coerced to verified instrument classification, never blindly trusted.
- horizon: swing (default, next 5 exchange sessions)/short (1–3 months)/medium (3–12 months)/long (1+ years). focus optional, <=500 characters.
- Reject position size, brokerage credentials, order instructions and files in MVP.
- Server provides as_of timestamp, market/asset classification and official data snapshot. User cannot override trusted metadata.

## POST result: cached

HTTP 200:

    {
      "kind": "report",
      "report_id": "r_example",
      "cache_hit": true,
      "url": "/api/v1/reports/r_example"
    }

## POST result: new job

HTTP 202:

    {
      "kind": "job",
      "job_id": "j_example",
      "status": "accepted",
      "poll_after_seconds": 3,
      "url": "/api/v1/jobs/j_example"
    }

## GET /jobs/{job_id}

    {
      "job_id": "j_example",
      "status": "validating",
      "report_id": null,
      "reason_code": null,
      "updated_at": "2026-11-06T00:00:00Z"
    }

Statuses: accepted, fetching, calculating, analyzing, critiquing, validating, published, failed, blocked. On published, report_id is non-null. Do not expose private stack traces or raw external credentials.

## Report document

The response payload follows schemas/research_report.schema.json (with server-generated report ID, as_of, source list, data_status and limitations). A schema-valid payload is NOT sufficient for publication: business validators must pass.

## Deterministic errors

    {
      "error": {
        "code": "PRICE_DATA_NOT_LICENSED",
        "message": "공개 표시 가능한 시세 데이터가 없어 해당 분석은 제공하지 않습니다.",
        "retryable": false,
        "request_id": "req_example"
      }
    }

- 400 INVALID_QUERY; 404 NOT_FOUND; 409 ANALYSIS_NOT_SUPPORTED; 422 VALIDATION_ERROR
- 429 RATE_LIMITED; 503 SOURCE_UNAVAILABLE or BUDGET_EXHAUSTED; 500 INTERNAL_ERROR
- When a standalone price feature is disabled, the core report may still return 200 with data_status.missing and limitations instead of an entire request failure.
- Public API has bounded request size, per-IP quota (privacy-conscious hashing/TTL), cross-user dedupe, and idempotency to prevent duplicate LLM charges.

## Swing report contract 0.2.0 (design only)

- `swing` is always present in report JSON. Non-swing horizons use `status=not_requested`. Swing requests return an available price plan or unavailable with reasons; they never silently fall back to another horizon.
- No authorized recent quote or validated price methodology returns a partial core report with `swing.status=unavailable`, `price_plan=null`, numeric score null and corresponding missing/limitations. Do not fail the entire core report solely for missing price.
- A valid price plan does not imply an available attractiveness score or target-time estimate. Each model requires registered methodology/validation metadata; missing model outputs are null.
- Coverage reports must distinguish price-plan, attractiveness and target-timing availability. These capability labels are not proof that the corresponding validators are implemented.
- Sector price plans require a verified tradable ETF proxy. No arbitrary proxy ID, calendar, price, scoring model, as_of or permission flags are accepted from the client.
- Deduplication/cache keys include price snapshot, proxy, target/scoring/timing methodology and validation IDs, calendar version, schema/profile versions and rights state.
- Saved report JSON stays immutable. Read-time UI validity derives from expires_at, current entitlement and snapshot/catalyst state; expired reports remain historical and are not returned as a currently valid cached swing plan.
- Old 0.1.0 documents require an explicit legacy renderer or unavailable-version response; do not silently claim compatibility with 0.2.0.
- Time estimates are distinct from holding_sessions/review_at/expires_at. The API provides no brokerage order or promised sale date.

## Market preparation and news metadata

Before any additional recommendation report, source entitlements and model readiness must be evaluated. Preparation artifacts are not /reports documents and cannot be used as evidence of validated scores. News metadata follows schemas/market_context.schema.json; links are attributed discovery candidates, not automatically verified bullish catalysts. Publication/event/first-seen/retrieval times are separate. Publisher text/images are never fetched by this adapter.

No new public HTTP routes are implemented. Local research.prepare_models supplies one-shot metadata collection/readiness artifacts only. A 15-minute refresh policy is not an always-running real-time service or an upstream SLA.
