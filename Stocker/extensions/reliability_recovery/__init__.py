"""Read-only reliability audit, not a new inference candidate."""
from pathlib import Path
from research.validation import digest
from extensions.research_v4 import read, write_once, seal, verify_seal

VERSION = 'reliability-recovery-audit-v1'
BASE = Path(__file__).resolve().parent


def methods():
    return {f'extensions/reliability_recovery/{n}': digest(BASE/n)
            for n in ['__init__.py','audit.py','labels.py','README.md']}
