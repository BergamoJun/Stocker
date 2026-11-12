"""Reuse the frozen numeric parser; separately map derived text to raw HTML."""
from hashlib import sha256
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from research import sec_filing_evidence
from research.validation import digest,stable_hash
from . import read,write_once,seal,verify_seal
from extensions.financial_fill import members
from research.local_files import exclusive_lock

TRANSFORM='sec-visible-chunks-codepoint-map-v1'


class CacheIntegrityError(ValueError):
    pass


class TextMap(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=False)
        self.raw=text;self.lines=[0];self.chunks=[];self.spans=[];self.active=[];self.ordinal=0;self.skip=[]
        for pos,char in enumerate(text):
            if char=='\n':self.lines.append(pos+1)
    def at(self):
        line,col=self.getpos();return self.lines[line-1]+col
    def handle_starttag(self,tag,attrs):
        start=self.at()
        if tag in {'script','style','ix:hidden','ix:exclude','xbrli:context','xbrli:unit','context','unit'}:self.skip.append(tag)
        if tag=='ix:nonfraction':
            self.ordinal+=1;self.active.append((self.ordinal,start))
    def handle_endtag(self,tag):
        if tag=='ix:nonfraction' and self.active:
            ordinal,start=self.active.pop();end=self.raw.find('>',self.at())+1
            self.spans.append({'ordinal':ordinal,'start':start,'end':end})
        if tag in self.skip:
            at=len(self.skip)-1-self.skip[::-1].index(tag);del self.skip[at:]
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs);self.handle_endtag(tag)
    def piece(self,text,raw_length):
        if self.skip:return
        value=' '.join(text.split())
        if value:self.chunks.append({'text':value,'raw_start':self.at(),'raw_end':self.at()+raw_length})
    def handle_data(self,data):self.piece(data,len(data))
    def handle_entityref(self,name):
        raw='&'+name+';';size=len(raw) if self.raw.startswith(raw,self.at()) else len(raw)-1
        self.piece(unescape(raw),size)
    def handle_charref(self,name):
        raw='&#'+name+';';self.piece(unescape(raw),len(raw))


def parse(raw,raw_sha,cik):
    if sha256(raw).hexdigest()!=raw_sha:raise ValueError('SEC_SOURCE_HASH_CHANGED')
    if len(raw)>30000000 or b'<!ENTITY' in raw.upper() or raw.startswith(b'%PDF'):raise ValueError('UNSUPPORTED_OR_UNSAFE_DOCUMENT')
    text=raw.decode('utf-8')  # Preserve BOM and every original byte on round trip.
    p=TextMap(text);p.feed(text);p.close()
    if p.active:raise ValueError('UNTERMINATED_IX_SPAN')
    derived='';segments=[]
    for chunk in p.chunks:
        start=len(derived);derived+=chunk['text']+'\n'
        segments.append({**chunk,'start':start,'end':start+len(chunk['text'])})
    return {'original_sha256':raw_sha,'text':derived,'derived_text_sha256':sha256(derived.encode()).hexdigest(),
        'transformation':TRANSFORM,'segments':segments,'ix_spans':p.spans,
        'dimensions':{k:[{'kind':x[0],'axis':x[1],'member':x[2]} for x in v] for k,v in members(raw).items()},
        'facts':sec_filing_evidence.parse(raw,raw_sha,cik)}


def cached(source,cik,cache_root,statistics,*,fresh=False):
    path=Path(source['raw_path'])
    if not path.is_file() or digest(path)!=source['raw_sha256']:raise ValueError('SEC_SOURCE_HASH_CHANGED')
    import sys
    key=stable_hash([source['raw_sha256'],cik,digest(Path(__file__)),digest(Path(sec_filing_evidence.__file__)),digest(Path(sys.modules[members.__module__].__file__))])
    root=Path(cache_root);target=root/(key+'.json')
    if fresh:return parse(path.read_bytes(),source['raw_sha256'],cik)
    if target.is_file():
        try:
            record=read(target);verify_seal(record)
            if record['key']!=key or record['parsed']['original_sha256']!=source['raw_sha256'] or sha256(record['parsed']['text'].encode()).hexdigest()!=record['parsed']['derived_text_sha256']:
                raise ValueError('cache content/key mismatch')
        except (ValueError,KeyError,TypeError) as error:raise CacheIntegrityError('PARSE_CACHE_INTEGRITY_CHANGED') from error
        statistics['parse_cache_hits']+=1;return record['parsed']
    statistics['parse_cache_misses']+=1;value=parse(path.read_bytes(),source['raw_sha256'],cik)
    record=seal({'key':key,'parsed':value})
    import json
    size=len(json.dumps(record,ensure_ascii=False).encode())
    with exclusive_lock(root/'adapter-cache-write.lock'):
        used=sum(p.stat().st_size for p in root.glob('*.json'))
        if not target.exists():
            if size<=32000000 and used+size<=256000000:write_once(target,record)
            else:statistics['parse_cache_capacity_skips']+=1
    return value
