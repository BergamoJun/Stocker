# Data Source and Rights Gate (2026-11-06 review)

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
- PRICE_DERIVED_METRICS_ENABLED: **false by default**.
- NEWS_SUMMARY_ENABLED: false until copyright/excerpt/LLM rules confirmed.
- MACRO_FRED_ENABLED: **false**, not merely awaiting credentials.

A report generated without price access may analyze SEC revenue/margins/balance sheet and business risks. It MUST NOT claim latest price, market cap, P/E/P/S, relative performance, technical support/resistance or current valuation without authorized pricing inputs.

## Licenses audit record

Record source_id, contract/license URL, exact allowed uses (server ingestion, LLM input, derived report, storage, public display), date checked, reviewing person, attribution requirements, permitted regions, quote delay and renewal/termination plan.

## Policy at launch

Without a documented allowance, rights remain **unknown**, not presumed free. Each public artifact must pass a per-source entitlement gate. If permission is lost, future generation stops and previously published content is reviewed for retention/public availability restrictions.
