"""Owned bounded ephemeral cache; saved report evidence is never evicted."""
from contextlib import AbstractContextManager
import json
import os
from pathlib import Path
import re
import uuid
from research.local_files import atomic_json,exclusive_lock
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import stable_hash,digest

BASE=Path(__file__).resolve().parent
FORBIDDEN={'raw','body','description','summary','insights','sentiment','image_url','api_key','apikey','authorization'}

def policy():
    v=json.loads((BASE/'ondemand_policy.json').read_text(encoding='utf-8'))
    if v['monthly_cost_usd']!=0 or any(v[k] for k in ('publisher_body_fetch_enabled','external_inference_enabled','model_training_enabled','numeric_news_impact_enabled','released_scores_enabled')): raise ValueError('ON_DEMAND_SCOPE_CHANGED')
    if not 1<=v['minimum_lookback_days']<=v['default_lookback_days']<=v['maximum_lookback_days']<=30 or v['maximum_articles_per_query']!=25 or v['maximum_http_requests_per_run']!=5 or not v['stop_gdelt_after_first_failure']: raise ValueError('ON_DEMAND_QUERY_CAP_CHANGED')
    if not 1<=v['cache_ttl_seconds']<=1800 or not v['cache_ttl_seconds']<=v['cache_retention_seconds']<=172800 or not 1<=v['maximum_cache_entries']<=128 or not 1<=v['maximum_cache_bytes']<=16777216 or not 1<=v['maximum_entry_bytes']<=262144 or not 1<=v['maximum_reports']<=1000 or not 1<=v['maximum_persistent_bytes']<=268435456 or not 1<=v['maximum_report_bytes']<=2097152: raise ValueError('ON_DEMAND_STORAGE_CAP_CHANGED')
    return v

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def encode(value): return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
def metadata_only(value):
    if isinstance(value,dict):
        if any(str(k).lower() in FORBIDDEN for k in value): raise ValueError('NON_METADATA_CACHE_FIELD')
        for item in value.values(): metadata_only(item)
    elif isinstance(value,list):
        for item in value: metadata_only(item)

class Store(AbstractContextManager):
    def __init__(self,*,root=None,illustrative=False,settings=None,clock=now):
        self.illustrative=illustrative; self.settings=settings or policy(); self.clock=clock
        if not illustrative and self.settings!=policy(): raise ValueError('REAL_NEWS_STORAGE_POLICY_OVERRIDE_FORBIDDEN')
        expected=(ROOT/'reports/private').resolve()/'on_demand_news'/('illustrative' if illustrative else 'real')
        if expected.resolve()!=expected: raise ValueError('ON_DEMAND_STORE_ALIAS_FORBIDDEN')
        self.root=Path(root or expected).resolve()
        if self.root!=expected: raise ValueError('OWNED_ON_DEMAND_STORE_REQUIRED')
        self.lock=None; self.locked=False
    def __enter__(self):
        self.root.mkdir(parents=True,exist_ok=True); self.lock=exclusive_lock(self.root/'worker.lock'); self.lock.__enter__(); self.locked=True
        try:
            owner={'id':'stocker-owned-on-demand-news-v1','is_illustrative':self.illustrative}
            p=self.root/'STORE.json'
            if p.exists() and read(p)!=owner: raise ValueError('ON_DEMAND_STORE_OWNER_CHANGED')
            if not p.exists():
                if any(x.name!='worker.lock' for x in self.root.iterdir()): raise ValueError('UNOWNED_NONEMPTY_NEWS_STORE')
                atomic_json(p,owner)
            (self.root/'cache').mkdir(exist_ok=True); (self.root/'runs').mkdir(exist_ok=True)
            return self
        except BaseException: self.__exit__(None,None,None); raise
    def __exit__(self,*args):
        if self.locked: self.lock.__exit__(*args); self.locked=False
        return False
    def require_lock(self):
        if not self.locked: raise ValueError('ON_DEMAND_STORE_LOCK_REQUIRED')
        if any((self.root/n).resolve()!=self.root/n for n in ('cache','runs')): raise ValueError('ON_DEMAND_SUBDIRECTORY_ALIAS_FORBIDDEN')
    def cache_files(self):
        self.require_lock(); values=[]
        for p in (self.root/'cache').iterdir():
            if re.fullmatch(r'[a-f0-9]{64}\.json\.[a-f0-9]{32}\.tmp',p.name) and p.is_file() and not p.is_symlink() and p.resolve().is_relative_to((self.root/'cache').resolve()):
                p.unlink(); continue
            if not re.fullmatch(r'[a-f0-9]{64}\.json',p.name) or not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to((self.root/'cache').resolve()): raise ValueError('UNEXPECTED_NEWS_CACHE_FILE')
            values.append(p)
        return values
    def entry(self,p):
        if p.is_symlink() or not p.resolve().is_relative_to((self.root/'cache').resolve()): raise ValueError('NEWS_CACHE_FILE_ALIAS_FORBIDDEN')
        if p.stat().st_size>self.settings['maximum_entry_bytes']: raise ValueError('NEWS_CACHE_ENTRY_OVERSIZED')
        v=read(p)
        if v.get('cache_id')!=p.stem or stable_hash(v['key'])!=p.stem or v.get('is_illustrative') is not self.illustrative or stable_hash({k:x for k,x in v.items() if k!='record_sha256'})!=v.get('record_sha256'): raise ValueError('NEWS_CACHE_ENTRY_CHANGED')
        metadata_only(v)
        if as_utc(v['first_available_at'])>as_utc(self.clock()): raise ValueError('NEWS_CACHE_FUTURE_COMMIT')
        return v
    def prune(self,reserve=0,new_entries=0):
        files=self.cache_files(); removed=0
        for p in list(files):
            v=self.entry(p)
            if (as_utc(self.clock())-as_utc(v['first_available_at'])).total_seconds()>self.settings['cache_retention_seconds']:
                p.unlink(); files.remove(p); removed+=1
        files.sort(key=lambda p:(p.stat().st_mtime_ns,p.name))
        while files and (len(files)+new_entries>self.settings['maximum_cache_entries'] or sum(p.stat().st_size for p in files)+reserve>self.settings['maximum_cache_bytes']):
            files.pop(0).unlink(); removed+=1
        if reserve>self.settings['maximum_cache_bytes']: raise ValueError('NEWS_CACHE_RESERVATION_EXCEEDS_CAP')
        return removed
    def get(self,key,*,refresh=False):
        self.require_lock(); p=self.root/'cache'/(stable_hash(key)+'.json')
        if not p.exists() or refresh: return None
        v=self.entry(p)
        if v['key']!=key: raise ValueError('NEWS_CACHE_KEY_CHANGED')
        if (as_utc(self.clock())-as_utc(v['first_available_at'])).total_seconds()>=self.settings['cache_ttl_seconds']: return None
        os.utime(p,None); return v
    def put(self,key,value):
        self.require_lock(); metadata_only(value); ident=stable_hash(key)
        v={'cache_id':ident,'key':key,'is_illustrative':self.illustrative,'first_available_at':self.clock(),'value':value}; v['record_sha256']=stable_hash(v)
        size=len(encode(v))
        if size>self.settings['maximum_entry_bytes']: raise ValueError('NEWS_CACHE_ENTRY_OVERSIZED')
        p=self.root/'cache'/(ident+'.json')
        # Replacing an expired/explicitly refreshed temporary entry never rewrites report copies.
        if p.exists(): self.entry(p); p.unlink()
        self.prune(size,1); atomic_json(p,v); return v
    def capacity(self):
        self.require_lock(); runs=self.root/'runs'; files=[]; folders=[]
        for p in runs.iterdir():
            if not p.is_dir() or not re.fullmatch('[a-f0-9]{32}',p.name) or p.is_symlink() or not p.resolve().is_relative_to(runs.resolve()): raise ValueError('UNEXPECTED_NEWS_RUN_FOLDER')
            folders.append(p)
            for f in p.iterdir():
                if not f.is_file() or f.is_symlink() or not f.resolve().is_relative_to(p.resolve()): raise ValueError('UNEXPECTED_NEWS_RUN_FILE')
                files.append(f)
        return len(folders),sum(f.stat().st_size for f in files)
    def check_report_capacity(self):
        count,size=self.capacity()
        if count>=self.settings['maximum_reports'] or size+self.settings['maximum_report_bytes']>self.settings['maximum_persistent_bytes']: raise ValueError('NEWS_REPORT_STORAGE_CAP_REACHED')
    def save(self,value,markdown):
        self.check_report_capacity(); metadata_only(value)
        size=len(encode(value))+len(markdown.encode('utf-8'))+1024
        if size>self.settings['maximum_report_bytes']: raise ValueError('NEWS_REPORT_OVERSIZED')
        folder=self.root/'runs'/value['run_id']
        if not re.fullmatch('[a-f0-9]{32}',value['run_id']): raise ValueError('INVALID_NEWS_RUN_ID')
        folder.mkdir(exist_ok=False); atomic_json(folder/'PACKET.json',value)
        (folder/'REPORT.ko.md').write_text(markdown,encoding='utf-8')
        atomic_json(folder/'FILES.json',{'PACKET.json':digest(folder/'PACKET.json'),'REPORT.ko.md':digest(folder/'REPORT.ko.md')})
        return folder
