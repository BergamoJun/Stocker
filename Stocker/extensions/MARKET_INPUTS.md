# 다종목 현재 종가·시장·업종 입력

`market_inputs` connects BE/SPY/PBW, MSFT/SPY/XLK and XOM/SPY/XLE using the exact existing reviewed issuer registry. It uses a separately versioned current-price transport with the same queue, account lock, completion spacing and cooldown. No old collector, forecast, policy or job is modified.

Reference metadata must identify the exact ticker, active US stock market listing, currency, type, issuer CIK and share class for common stock. ETFs use their actual ETF classification, reviewed listing and nonleveraged reference role. A mismatched CIK is withheld, never guessed or merged. Data uses the free plan's preceding-ET-day request window. The report still checks the latest completed exchange session and marks delayed/stale EOD as stale.

Stock normalization reuses `panel_prices`; ETF normalization preserves instrument type and decimal values without treating a fund as a corporation. Split-adjusted OHLCV/raw sources, actual retrieval and first snapshot availability are retained privately. Returns for 1/5/20 sessions require contiguous aligned exchange dates; comparisons are percentage-point differences, not future returns or a causal attribution. No dividend total return, model fitting, target or released score is added.

`python -m extensions.market_inputs --baseline <baseline> --ciks 1664703 789019 2115436 --probe` validates through the source cache without committing a derived snapshot. Omit `--probe` to commit. A committed record is consumed by the common contract's `--market <record-folder>` after a full second. Raw/normalized replay, rights, identity, source hashes and cutoff availability are checked before local use. Prices remain absent from AI_INPUT.json.
