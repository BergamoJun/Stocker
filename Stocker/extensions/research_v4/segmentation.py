"""Offset-preserving source units; linguistic relations are proposals, not regex truth."""
import hashlib
import re
from research.rights import as_utc
from research.validation import stable_hash
from . import legacy

ABBREVIATIONS = {'u.s.', 'u.k.', 'e.g.', 'i.e.', 'mr.', 'mrs.', 'dr.', 'vs.', 'no.'}


def ranges(text):
    """Sentence punctuation, preserving decimal/initialism and abbreviation text."""
    start = 0
    for index, char in enumerate(text):
        if char not in '.!?' or index+1 < len(text) and not text[index+1].isspace():
            continue
        token = text[:index+1].split()[-1].lower() if text[:index+1].split() else ''
        if char == '.' and (token in ABBREVIATIONS or re.fullmatch(r'(?:[a-z]\.){2,}', token)):
            continue
        left = start
        while left <= index and text[left].isspace(): left += 1
        if left <= index: yield left, index+1
        start = index+1
    while start < len(text) and text[start].isspace(): start += 1
    end = len(text.rstrip())
    if start < end: yield start, end


def spans(text, start, end):
    # Offer full sentence and punctuation clauses; do not claim to resolve
    # attachment, elision, conditional scope or time by punctuation alone.
    result = [(start, end)]
    cuts = [start] + [start+m.end() for m in re.finditer(r';\s*', text[start:end])] + [end]
    for left, right in zip(cuts, cuts[1:]):
        while left < right and text[left].isspace(): left += 1
        while right > left and text[right-1].isspace(): right -= 1
        if (left,right) not in result and left < right: result.append((left,right))
    return [{'span_id': f's{a}_{b}', 'start': a, 'end': b, 'text': text[a:b]} for a,b in result]


def source_units(inputs):
    unique, issuer_links = {}, []
    for ai in inputs:
        legacy.validate_ai(ai, ai['as_of'])
        for doc in ai['official_documents']:
            namespace='illustrative' if ai['is_illustrative'] else 'real'
            key = stable_hash([namespace,doc['source'], doc['url'], doc['record_sha256'], doc['text_sha256']])
            issuer_links.append({'cik': ai['identity']['cik'], 'source_key': key, 'cutoff': ai['as_of']})
            if key in unique:
                old = unique[key]
                if old['document'] != doc or old['cutoff'] != ai['as_of']:
                    raise ValueError('V4_SOURCE_VERSION_OR_TIME_CONFLICT')
                continue
            unique[key] = {'document': doc, 'cutoff': ai['as_of']}
    units = []
    for key, packet in unique.items():
        doc, cutoff = packet['document'], packet['cutoff']
        if as_utc(doc['available_at']) >= as_utc(cutoff): raise ValueError('V4_FUTURE_SOURCE')
        for paragraph in doc['paragraphs']:
            text = paragraph['text']
            if hashlib.sha256(text.encode()).hexdigest() != paragraph['sha256']:
                raise ValueError('V4_PARAGRAPH_CHANGED')
            for start,end in ranges(text):
                unit = {'source_key': key, 'source_id': doc['source_id'], 'source': doc['source'],
                    'namespace':namespace,
                    'source_url':doc['url'],
                    'source_record_sha256': doc['record_sha256'], 'paragraph_id': paragraph['id'],
                    'paragraph_sha256': paragraph['sha256'], 'text': text, 'start': start, 'end': end,
                    'allowed_spans': spans(text,start,end), 'published_date': doc['published_date'],
                    'published_at': doc['published_at'], 'time_precision': doc['time_precision'],
                    'available_at': doc['available_at'], 'cutoff': cutoff}
                unit['unit_id'] = stable_hash(unit)
                units.append(unit)
    return units, {'unique_sources': len(unique), 'issuer_source_pairs': len({(r['cik'],r['source_key']) for r in issuer_links}),
                   'paragraphs': sum(len(p['document']['paragraphs']) for p in unique.values()), 'units': len(units),
                   'issuer_links': issuer_links, 'deduplication': 'source version first; issuer exposure separately'}


def validate_unit(unit):
    if unit['source'] not in {'federal_reserve_board','us_eia'}: raise ValueError('V4_SOURCE_FORBIDDEN')
    if not re.fullmatch('[a-f0-9]{64}',unit['source_record_sha256']):raise ValueError('V4_SOURCE_HASH_INVALID')
    if not 0<=unit['start']<unit['end']<=len(unit['text']):raise ValueError('V4_OFFSETS_CHANGED')
    if hashlib.sha256(unit['text'].encode()).hexdigest() != unit['paragraph_sha256']: raise ValueError('V4_PARAGRAPH_CHANGED')
    if unit['allowed_spans'] != spans(unit['text'],unit['start'],unit['end']): raise ValueError('V4_OFFSETS_CHANGED')
    if unit['unit_id'] != stable_hash({k:v for k,v in unit.items() if k!='unit_id'}): raise ValueError('V4_UNIT_CHANGED')
    if as_utc(unit['available_at']) >= as_utc(unit['cutoff']): raise ValueError('V4_FUTURE_SOURCE')
    if unit['time_precision']=='date' and unit['published_at'] is not None: raise ValueError('V4_INVENTED_PUBLICATION_TIME')
    if unit['published_date']>as_utc(unit['cutoff']).date().isoformat() or unit['published_at'] and as_utc(unit['published_at'])>=as_utc(unit['cutoff']):raise ValueError('V4_FUTURE_PUBLICATION')
    return unit
