"""ILLUSTRATIVE - immutable output and exact re-derivation."""
import pytest
from extensions import exposure_model as model,exposure_report as report
from extensions.tests.test_exposure_model import packet,fact,AT
from research.local_files import atomic_json

def setup(tmp_path,monkeypatch):
 ai=packet([fact('liabilities')]); origin=tmp_path/'reports/private/inputs'; atomic_json(origin/'AI_INPUT.json',ai)
 monkeypatch.setattr(report,'ROOT',tmp_path)
 monkeypatch.setattr(report.scope,'check',lambda *a,**k:{'baseline_sha256':'a'*64})
 monkeypatch.setattr(report,'historical_check',lambda *a,**k:{})
 monkeypatch.setattr(report.analysis_contract,'export_ai',lambda *a,**k:ai)
 return origin,ai

def test_write_and_replay(tmp_path,monkeypatch):
 origin,ai=setup(tmp_path,monkeypatch); v,folder=report.save(origin,'baseline',clock=lambda:AT)
 assert report.validate(folder)==v
 assert '금리·원자재 민감도' in (folder/'REPORT.ko.md').read_text(encoding='utf-8')
 (folder/'REPORT.ko.md').write_text('tampered',encoding='utf-8')
 with pytest.raises(ValueError,match='FILES_CHANGED'): report.validate(folder)

def test_context_change_invalidates_report(tmp_path,monkeypatch):
 origin,ai=setup(tmp_path,monkeypatch); _,folder=report.save(origin,'baseline',clock=lambda:AT)
 ai['financial_facts'][0]['value']='7'
 with pytest.raises(ValueError,match='SOURCE_REPLAY_CHANGED'): report.validate(folder)
