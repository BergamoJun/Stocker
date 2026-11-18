"""Personal financial research; no strict approval, model calls or trades."""
from pathlib import Path
from research.validation import digest

VERSION = 'personal-research-v4'
FILES = ['__init__.py', 'sources.py', 'core.py', 'report.py', '__main__.py','market_data.py','valuation.py','coverage.py','business.py','segments.py','relative_valuation.py','historical_valuation.py','scenarios.py']


def methods():
    from extensions import financial_fill, weighted_engine
    from extensions import market_inputs
    from research import sec_fact_review
    from research.sec_report import ROOT
    names = ['research/sec_refresh.py', 'research/sec_facts.py', 'research/sec_growth.py',
             'extensions/weighted_features.py', 'extensions/weighted_critic.py']
    return {**financial_fill.methods(), **weighted_engine.methods(), **sec_fact_review.methods(), **market_inputs.methods(),
            **{n: digest(ROOT / n) for n in names},
            **{'extensions/personal_research/' + n: digest(Path(__file__).parent / n) for n in FILES}}
