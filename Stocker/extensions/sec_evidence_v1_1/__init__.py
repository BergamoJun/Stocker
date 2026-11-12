"""Versioned semantic hardening; frozen v1 methods and receipts remain intact."""
from pathlib import Path
from research.validation import digest
from research.sec_report import ROOT
from extensions.sec_evidence_v1 import read, write_once, seal, verify_seal

VERSION = 'sec-evidence-adapter-v1.1'
FILES = ['__init__.py', 'contract.py', 'semantic_validation.py', 'eligibility.py',
         'reviews.py', 'discovery.py', 'bridge.py', 'report.py', '__main__.py']


def methods():
    from extensions.sec_evidence_v1 import methods as original
    from extensions.financial_impact_v1 import methods as impact
    return {**original(), **impact(), **{
        'extensions/sec_evidence_v1_1/' + n: digest(Path(__file__).parent / n) for n in FILES}}
