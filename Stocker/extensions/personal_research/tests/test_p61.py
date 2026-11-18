"""ILLUSTRATIVE software checks; no AI or financial performance certification."""
from copy import deepcopy
import json
import pytest
from extensions.personal_research.tests.test_p51 import value
from extensions.personal_research import packet_export as export

def test_stable_complete_and_no_mutation(value):
    before=deepcopy(value);a=export.build(value,'2026-11-18T00:00:00Z')
    assert a==export.build(value,'2026-11-18T00:00:00Z') and value==before
    assert all(title in export.render(a) for title in export.SECTIONS)
    from extensions.personal_research import professional_packet
    assert professional_packet.validate(export.to_api_packet(a))['status']=='PASS_SEC_ONLY'
    assert 'evidence' not in a['canonical_packet'] and 'sources' not in a['canonical_packet']

def test_export_no_vendor_or_local_paths(value):
    value['market_snapshot'].update(raw_close='LICENSED_SECRET_SENTINEL',provider_reported_market_cap='LICENSED_CAP_SENTINEL')
    p=export.build(value);s=json.dumps(p)
    assert 'LICENSED_SECRET_SENTINEL' not in s and 'raw_path' not in s
    assert 'sk-' not in s and 'C:\\' not in s and p['export_policy']['api_requests']==0

def test_values_periods_units_scopes_identical_in_md(value):
    p=export.build(value);text=export.render(p)
    for e in p['evidence_registry']:
        assert e['evidence_id'] in text and str(e['period_days']) in text and e['financial_scope'] in text
        if e['value'] is not None:assert e['value'] in text and e['unit'] in text

def test_private_key_blocked(value,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','TEST_CORP_SECRET_SENTINEL')
    value['issuer']={'name':'TEST_CORP_SECRET_SENTINEL'}
    with pytest.raises(ValueError):export.build(value)

def test_saved_export_hash_and_render(value,tmp_path,monkeypatch):
    monkeypatch.setattr(export,'ROOT',tmp_path)
    folder=export.write(export.build(value))
    assert export.validate(folder)['status']=='VALID'
    (folder/'RESEARCH_PACKET.md').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError):export.validate(folder)

@pytest.mark.parametrize('bad',['--ai','--fetch-missing','--allow-paid','--ai-dry-run'])
def test_export_never_accepts_execution_flags(bad):
    from extensions.personal_research.__main__ import main
    with pytest.raises(SystemExit):main(['--ticker','MU','--export-research-packet',bad])

def test_unknown_and_stale_preserved(value):
    value['market_data_status']='STALE_DATA';p=export.build(value)
    assert p['data_freshness']['market_data_status']=='STALE_DATA'
    assert all(e['fiscal_year']=='UNKNOWN' for e in p['evidence_registry'])
    assert not p['valuation']['values_provided']
