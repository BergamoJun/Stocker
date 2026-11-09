# 공식 원문 수집과 공유 요청 확장

The implementation lives outside frozen research/config/schema directories. New-operation checks inventory this directory; historical source validation checks its pinned files without rejecting unrelated future extension files. Existing methods, receipts and jobs remain unchanged.

Run from the Stocker repository using its virtual environment:

```powershell
.\.venv\Scripts\python.exe -B -m extensions.text_collect --baseline <baseline.json> --check --replay
.\.venv\Scripts\python.exe -B -m extensions.text_collect --baseline <baseline.json> --collect
.\.venv\Scripts\python.exe -B -m extensions.text_collect --baseline <baseline.json> --packet
```

Only Board-authored monetary press releases and EIA-authored Today in Energy narrative are eligible after matching the reviewed policy and checking robots rules. Figures, tables, images, source captions and protected narrative are excluded. Third-party articles, issuer filing narratives, FRED and market prices are not authorized by this extension. Full HTML is discarded; selected paragraph text, hashes, source dates and actual first availability are retained privately.

Public-domain status is the documented basis for inference eligibility, not a claim that the websites explicitly grant AI-specific permissions. No external model is called and no training or automatic investment-impact judgment is performed. Date-only releases retain null published_at. A 45-day discovery window may include old reference material; packet ages are explicit and do not imply fresh swing evidence or complete news coverage.

Tests use a separate illustrative namespace. Run `pytest tests context_adapters/tests extensions/tests`; program tests do not establish source truth or investment profitability.
