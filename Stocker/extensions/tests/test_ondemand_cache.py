"""ILLUSTRATIVE - bounded owned metadata storage, no licensed input."""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
from copy import deepcopy
import pytest
from research.local_files import exclusive_lock,LockBusy,atomic_json
from research.validation import digest,stable_hash
from extensions import ondemand_cache as cache

class Clock:
    def __init__(self): self.at=datetime(2026,11,10,12,tzinfo=timezone.utc)
    def __call__(self): return self.at.isoformat()
    def pause(self,seconds): self.at+=timedelta(seconds=seconds)

@pytest.fixture
def store(tmp_path,monkeypatch):
    root=tmp_path/'project'; monkeypatch.setattr(cache,'ROOT',root)
    return cache.Store(illustrative=True,clock=Clock())

def test_cache_hit_keeps_bytes_first_commit_and_expiry(store):
    with store:
        first=store.put({'query':'ILLUSTRATIVE'},{'checked_at':store.clock(),'articles':[]})
        path=store.root/'cache'/(first['cache_id']+'.json'); before=digest(path)
        store.clock.pause(20); reused=store.get({'query':'ILLUSTRATIVE'})
        assert reused==first and digest(path)==before
        store.clock.pause(880)
        assert store.get({'query':'ILLUSTRATIVE'}) is None

def test_refresh_replaces_only_temporary_cache_and_preserves_saved_copy(store):
    with store:
        key={'query':'ILLUSTRATIVE'}; first=store.put(key,{'articles':[]})
        snapshot=deepcopy(first); store.clock.pause(100)
        assert store.get(key,refresh=True) is None
        second=store.put(key,{'articles':[]})
        assert second['first_available_at']!=first['first_available_at'] and snapshot==first

def test_cache_entry_count_lru_and_size_are_enforced(store):
    store.settings={**store.settings,'maximum_cache_entries':2}
    with store:
        a=store.put({'q':'a'},{'articles':[]}); b=store.put({'q':'b'},{'articles':[]})
        os.utime(store.root/'cache'/(a['cache_id']+'.json'),ns=(1,1)); os.utime(store.root/'cache'/(b['cache_id']+'.json'),ns=(2,2))
        store.get({'q':'a'}); store.put({'q':'c'},{'articles':[]})
        assert store.get({'q':'b'}) is None and len(store.cache_files())==2
        store.settings={**store.settings,'maximum_cache_bytes':1}
        assert store.prune()==2

def test_retention_removes_only_owned_cache_not_report_files(store):
    with store:
        store.put({'q':'ILLUSTRATIVE'},{'articles':[]})
        folder=store.root/'runs'/('a'*32); atomic_json(folder/'PACKET.json',{'ILLUSTRATIVE':True})
        store.clock.pause(172801); assert store.prune()==1
        assert (folder/'PACKET.json').exists()

def test_unknown_file_and_unowned_nonempty_store_are_not_deleted(store,tmp_path):
    with store:
        path=store.root/'cache/legacy.txt'; path.write_text('ILLUSTRATIVE')
        with pytest.raises(ValueError,match='UNEXPECTED'): store.prune()
        assert path.exists()
    # This is the exact owned namespace but lacks the ownership receipt.
    owner=store.root/'STORE.json'; owner.unlink()
    with pytest.raises(ValueError,match='UNOWNED'): store.__enter__()
    assert path.exists()

def test_tamper_wrong_key_future_commit_and_body_are_rejected(store):
    with store:
        first=store.put({'q':'ILLUSTRATIVE'},{'articles':[]}); path=store.root/'cache'/(first['cache_id']+'.json')
        value=json.loads(path.read_text(encoding='utf-8')); value['value']['articles']=['changed']; path.write_text(json.dumps(value),encoding='utf-8')
        with pytest.raises(ValueError,match='CHANGED'): store.get({'q':'ILLUSTRATIVE'})
        with pytest.raises(ValueError,match='NON_METADATA'): store.put({'q':'body'},{'body':'ILLUSTRATIVE forbidden'})

def test_owned_abandoned_atomic_temp_is_cleaned_under_lock(store):
    with store:
        p=store.root/'cache'/('a'*64+'.json.'+'b'*32+'.tmp'); p.write_text('ILLUSTRATIVE incomplete')
        assert store.cache_files()==[] and not p.exists()

def test_persistent_report_cap_stops_before_saving_and_never_eviction(store):
    store.settings={**store.settings,'maximum_reports':1}
    with store:
        folder=store.save({'run_id':'a'*32,'articles':[]},'ILLUSTRATIVE')
        with pytest.raises(ValueError,match='STORAGE_CAP'): store.check_report_capacity()
        assert (folder/'PACKET.json').exists()
        store.settings={**store.settings,'maximum_reports':1000,'maximum_persistent_bytes':10}
        with pytest.raises(ValueError,match='STORAGE_CAP'): store.check_report_capacity()

def test_entry_size_and_lock_and_private_path_guards(store,tmp_path):
    with pytest.raises(ValueError,match='LOCK'): store.put({}, {})
    with store:
        with pytest.raises(ValueError,match='OVERSIZED'): store.put({'q':'ILLUSTRATIVE'},{'text':'x'*300000})
        with pytest.raises(LockBusy): cache.Store(illustrative=True,clock=store.clock).__enter__()
    with pytest.raises(ValueError,match='OWNED'): cache.Store(root=tmp_path/'outside',illustrative=True)

def test_cache_directory_alias_cannot_touch_legacy_files(store,tmp_path):
    with store:
        legacy=tmp_path/'legacy'; legacy.mkdir(); protected=legacy/('a'*64+'.json'); protected.write_text('ILLUSTRATIVE legacy')
        owned=store.root/'cache'; owned.rmdir()
        try: owned.symlink_to(legacy,target_is_directory=True)
        except OSError: pytest.skip('Windows symlink privilege unavailable')
        with pytest.raises(ValueError,match='ALIAS'): store.prune()
        assert protected.exists()

def test_real_store_cannot_override_storage_caps(store):
    with pytest.raises(ValueError,match='OVERRIDE'): cache.Store(illustrative=False,settings={**cache.policy(),'maximum_reports':2000})

def test_alias_guard_also_runs_without_windows_symlink_privilege(store,monkeypatch,tmp_path):
    with store:
        old=tmp_path/'legacy'; old.mkdir(); protected=old/'old.json'; protected.write_text('ILLUSTRATIVE legacy')
        original=Path.resolve; target=store.root/'cache'
        monkeypatch.setattr(Path,'resolve',lambda p,*a,**k:old if p==target else original(p,*a,**k))
        with pytest.raises(ValueError,match='ALIAS'): store.prune()
        assert protected.exists()

def test_policy_does_not_grant_bodies_ai_training_or_large_storage(monkeypatch):
    value=cache.policy(); value['external_inference_enabled']=True
    monkeypatch.setattr(cache.json,'loads',lambda raw:value)
    with pytest.raises(ValueError,match='SCOPE'): cache.policy()

def test_new_modules_compile_without_syntax_warnings():
    import warnings
    for name in ('ondemand_cache.py','ondemand_transport.py','ondemand_news.py'):
        path=Path(cache.__file__).with_name(name)
        with warnings.catch_warnings():
            warnings.simplefilter('error',SyntaxWarning)
            compile(path.read_text(encoding='utf-8'),str(path),'exec')
