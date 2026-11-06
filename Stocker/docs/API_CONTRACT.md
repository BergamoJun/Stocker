# REST API Contract (design only)

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
      "horizon": "medium",
      "focus": "데이터센터 CAPEX 둔화가 성장성에 미치는 영향",
      "language": "ko"
    }

- The instrument ID above is synthetic, for interface illustration only.
- Only instrument IDs returned by resolver are accepted. No arbitrary URLs or prompt instructions are forwarded to tools.
- analysis_type: auto/equity/fund/sector; coerced to verified instrument classification, never blindly trusted.
- horizon: short/medium/long. focus optional, <=500 characters.
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
