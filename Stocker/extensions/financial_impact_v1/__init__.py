"""Versioned conditional financial arithmetic; no model or price prediction."""
from pathlib import Path
from research.validation import digest

VERSION = 'financial-impact-v1'
BASE = Path(__file__).resolve().parent
METHOD_FILES = ['__init__.py','models.py','contract.py','calculations.py','evidence.py',
                'validator.py','scenarios.py','energy.py','report.py','adapters.py','examples.py','__main__.py']


def methods():
    return {'extensions/financial_impact_v1/'+n: digest(BASE/n) for n in METHOD_FILES}
