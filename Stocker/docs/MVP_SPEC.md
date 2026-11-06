# MVP Development Specification v0.2

## Product contract

A free, publicly accessible Korean-language research website for US-listed equities and ETFs. It synthesizes **verifiable and legally usable source information** into conditional bull/base/bear scenarios. Not an automated trading platform or individualized investment adviser.

## MVP users and flow

1. Visitor searches symbol/company (or a curated sector).
2. Resolver displays canonical name, exchange, CIK/issuer where possible, and equity/fund classification.
3. Visitor selects horizon: short (1–3 months), medium (3–12 months), long (1+ years); optional hypothesis (max 500 characters).
4. UI returns cached latest report immediately or enqueues one bounded analysis job.
5. UI displays status, data-as-of, summary, validated metrics, bull/base/bear, invalidation conditions, risk flags, sources and limitations.
6. Visitor can inspect source links, report creation time, snapshot age and what analyses were disabled.

## Supported instruments and deliberate limits

| Cohort | MVP release | Permitted analysis | Restrictions |
|---|---|---|---|
| US domestic operating companies with suitable SEC XBRL | P0 core | Revenue, income, cash flow, balance sheet, growth, business/risks | Do not promise coverage of every ticker; handle missing or incomparable XBRL |
| US-listed ETFs/ETPs | P1 restricted beta | Product strategy, underlying exposure, issuer-disclosed fees/structure and risks | Issuer-sourced curated allowlist; all instrument properties must be checked |
| Leveraged/inverse/single-stock funds | P1 restricted beta | Reset interval, underlying exposure, compounding risks | Do not extrapolate underlying multi-day return by simple leverage multiple |
| Sector queries | P1 restricted beta | Curated sector definition, industry mechanism and representative issuers | Label constituents as illustrative, not complete index membership |
| Foreign issuers/ADRs and unsupported funds | Later | Report unavailable or partial, transparent reason | Do not misapply domestic US-GAAP extraction |

The service launches with **fundamental/source-based reports even if the price-data feature is OFF**. Price-derived metrics, valuation ratios, relative returns and chart signals are gated by permission to display and transform price data publicly.

## User-visible report sections

1. Identity / type and coverage badges.
2. Generated-at and observed-as-of timestamps; EOD/delayed/missing label.
3. Executive summary with evidence-confidence level (NOT forecast probability).
4. Evidence-backed bullish and bearish theses; value drivers and catalysts.
5. Verified metrics with source, unit, formula and reporting period.
6. Bull/base/bear scenarios: assumptions → mechanism → observable signals → invalidation.
7. Risks, largest unresolved unknowns, explicit data limitations.
8. Source list with direct links, publication/retrieval timestamps and disclosures.

## Minimum quality for external beta

- No false current-price assertion and no metric without a verifiable provenance chain.
- No fabricated source URLs or dates; no unlicensed price data exposure.
- All reports follow versioned schema and pass deterministic unit/period/date checks.
- Bull/base/bear are all present and include useful signals/invalidation.
- Unsupported symbols, ETF type uncertainty, and data outages lead to honest partial result or refusal.
- Public access has rate limits, generation quotas, cache, queue timeout, and monthly spending kill switch.
- Terms/privacy/disclaimer, service owner contact and incident reporting are available before launch.
- Legal review covers public financial content in the actual target jurisdiction; disclaimer alone is insufficient.

## Non-goals

No brokerage integration, personalized allocation or sell order, portfolio upload, per-user suitability evaluation, real-time market tape, automatic news scraping with unclear rights, model-generated unsourced price targets, live backtesting from non-point-in-time data.

## Acceptance scenarios

- A known supported US company yields correct entity mapping, source-linked SEC metrics, three scenarios, and transparent data date.
- An ambiguous symbol results in disambiguation.
- An ETF ticker never flows through operating-company ratio analysis.
- With no authorized market-data provider, quote/valuation/chart elements are hidden and the report explains why.
- A report is regenerated only when its snapshot, prompt/model version, or relevant user parameters differ; identical concurrent requests reuse one job.
- A third-party filing containing "ignore instructions" does not change model behavior.
- Cached published report can still be viewed when a model or source API is down.
- Public input cannot exceed limits, extract secrets or initiate unauthorized network destinations.
