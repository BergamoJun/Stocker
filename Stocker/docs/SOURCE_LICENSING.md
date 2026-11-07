# Data Source and Rights Gate (2026-11-06 review)

> Active scope changed by the user on 2026-11-07: PERSONAL LOCAL research, USD 0 data budget. The public-service matrix below is retained as a deferred design. Massive Stocks Basic is selected for personal EOD downloads, storage and deterministic local analytics, conditional on the user's own non-professional account and required subscriber terms. Public redistribution and external price-data LLM input remain OFF. Current evidence, retention limits and setup: [MARKET_PREPARATION.md](MARKET_PREPARATION.md). A public-use license is not a prerequisite for the authorized personal-local path.

This memo is a technical risk register, NOT a legal opinion or authorization to use any provider in a public production service.

## Provider decision matrix

| Provider | Useful for | Official link | Public MVP status | Action |
|---|---|---|---|---|
| SEC EDGAR | filing history, companyfacts/XBRL, ticker-CIK | https://www.sec.gov/search-filings/edgar-application-programming-interfaces | Proposed core; comply with access policy and verify downstream reuse | Implement adapter and provenance |
| Issuer investor relations / ETF prospectus | primary company/fund explanations | issuer-specific official pages | Manual review per publisher | Allowlist validated URLs; avoid bulk scraping |
| BLS | CPI/employment, original-source macro | https://www.bls.gov/developers/termsOfService.htm | Candidate; requires proper notices/citation | Evaluate selected series |
| BEA | GDP, PCE and other macro data | https://apps.bea.gov/API/docs/index.htm | Candidate; registration required | Review terms and unit conventions |
| US Treasury Fiscal Data | some fiscal macro series | https://fiscaldata.treasury.gov | Candidate | Review dataset provenance and terms |
| FRED API | cross-source macro data | https://fred.stlouisfed.org/docs/api/terms_of_use.html | **BLOCKED pending explicit permission/legal review** | Avoid direct API integration in AI workflow |
| Massive individual plan | historical/quote data | https://massive.com/knowledge-base/article/what-are-pro-and-non-pro-classifications-for-massives-stock-data | **NOT cleared for public distribution** | Contact commercial sales |
| Alpha Vantage | price and market data | https://www.alphavantage.co/documentation/ | **NOT cleared for public distribution** | Contact sales, validate rights |
| Yahoo/yfinance or unofficial scraped feeds | convenient prototyping | relevant provider terms | **No assumption of public-use license** | Do not use as production shortcut |

## Specific findings

### SEC

Official EDGAR exposes submissions history and XBRL company facts without an API key. Its fair access limit is currently 10 requests per second across all requesting machines for a user and requires an identified User-Agent. Plan global throttling, 429/backoff, cache, source publication timestamps, accounting periods and amended filings. Sources: https://www.sec.gov/search-filings/edgar-application-programming-interfaces and https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data

### FRED: significant AI and caching terms

FRED API terms incorporate the broader FRED legal terms. The latter include restrictions on accessing content via its API in connection with developing/training software or machine-learning/AI systems, and on API-mediated storage/caching/archiving. Data series may have third-party rights as well. Therefore DO NOT route FRED API data into an LLM or cache in this project without explicit permission and expert review. Evaluate BLS/BEA/Treasury source APIs independently instead. Sources: https://fred.stlouisfed.org/legal/ and https://fred.stlouisfed.org/docs/api/terms_of_use.html

### Price vendors

Individual/free API plans may be strictly personal even when their responses include the data needed for technical analysis. Public display/redistribution and derived analytics require terms that expressly cover this website's intended use. Confirm market, rights, attribution, rate, latency, retention, deletion, sublicensing restrictions, and whether sending derived data to an LLM is authorized. Sources: https://massive.com/knowledge-base/article/what-are-pro-and-non-pro-classifications-for-massives-stock-data and https://www.alphavantage.co/realtime_data_policy/

## Release flags

- SEC_FILINGS_ENABLED: false until connector and provenance tests are ready.
- ETF_ISSUER_DOCS_ENABLED: false until source/issuer allowlist ready.
- PUBLIC_PRICE_DISPLAY_ENABLED: **false by default**.
- PRICE_DERIVED_METRICS_ENABLED: **false by default**; includes swing targets/invalidation, upside/downside, reward/risk and price-based attractiveness.
- SWING_PRICE_PLAN_ENABLED: **false** until licensed inputs, calendar and validated deterministic calculations exist.
- ATTRACTIVENESS_SCORE_ENABLED: **false** until methodology and validation are complete.
- TARGET_TIMING_ESTIMATE_ENABLED: **false** until a separate timing model is validated.
- NEWS_SUMMARY_ENABLED: false until copyright/excerpt/LLM rules confirmed.
- MACRO_FRED_ENABLED: **false**, not merely awaiting credentials.

A report generated without price access may analyze SEC revenue/margins/balance sheet and business risks. It MUST NOT claim latest price, market cap, P/E/P/S, relative performance, technical support/resistance or current valuation without authorized pricing inputs.

## Licenses audit record

Record source_id, contract/license URL, exact allowed uses (server ingestion, LLM input, derived report, storage, public display), date checked, reviewing person, attribution requirements, permitted regions, quote delay and renewal/termination plan.

## Policy at launch

Without a documented allowance, rights remain **unknown**, not presumed free. Each public artifact must pass a per-source entitlement gate. If permission is lost, future generation stops and previously published content is reviewed for retention/public availability restrictions.

## Swing feature gate (2026-11-07 design update)

A conditional price target, invalidation level, reward/risk or price-based 1–100 score is a derived use of market data. Missing public-display/derivation/LLM rights cannot be bypassed by hiding the raw quote, requesting an LLM estimate, or describing a number as research. No provider entitlement was newly approved by this specification update. Retain the existing price/news/FRED restrictions and SEC-only fallback.

## Zero-dollar market/news review — 2026-11-07

Monthly data budget is USD 0. No paid signup, contract or entitlement was activated. The surveyed free Twelve Data/Tiingo/Alpha Vantage/Finnhub plans do not clear this public website's price-display/derived-score/LLM uses. Credentials alone cannot change that status. Evidence and technical decisions: MARKET_PREPARATION.md and config/source_entitlements.json.

GDELT Project's open-dataset terms permit reuse with attribution; enable only its metadata/link adapter. Do not confuse GDELT Project with GDELT Cloud. Publisher article bodies/images and their use in an LLM remain OFF. NewsAPI Developer and GNews Free were not selected for public latest-news use due to use restrictions/delay documented in the review. EIA is a government-data candidate pending a free key and series-specific provenance/cadence checks; protected third-party materials are excluded.

NEWS_SUMMARY_ENABLED remains false. Metadata discovery does not constitute a body-summary entitlement. Public price/attractiveness/target-time release flags remain false.
