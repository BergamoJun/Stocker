"""Official SEC calculation boundary; immutable old modules are dependencies."""
from pathlib import Path
from research.validation import digest
from extensions.research_v4 import read,write_once,seal,verify_seal

VERIFIER='sec-evidence-adapter-v1.1'
FILES=['__init__.py','contract.py','owner.py','facade.py','bridge.py','report.py','replay.py',
       'boundary.py','legacy_boundary_policy.json','__main__.py']


class Rejected(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def methods():
    from extensions.sec_evidence_v1_1 import methods as hardened
    return {**hardened(),**{'extensions/sec_evidence_current/'+n:digest(Path(__file__).parent/n) for n in FILES}}
