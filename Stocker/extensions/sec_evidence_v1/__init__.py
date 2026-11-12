"""SEC discovery and independently reviewed exposure bridge, never LLM input."""
from pathlib import Path
from research.validation import digest
from extensions.research_v4 import read,write_once,seal,verify_seal

VERSION='sec-evidence-adapter-v1'
BASE=Path(__file__).parent
FILES=['__init__.py','models.py','contract.py','parsing.py','catalog.py','discovery.py','reviews.py','bridge.py','acquisition.py','report.py','__main__.py']


def methods():
    from research.sec_report import ROOT
    names=['extensions/sec_evidence_v1/'+n for n in FILES]+[
        'research/sec_filing_evidence.py','research/sec_fact_review.py','research/sec_refresh.py',
        'research/sec_transport.py','research/sec_facts.py','config/sec_fact_review_policy.json','extensions/financial_fill.py']
    return {n:digest(ROOT/n) for n in names}
