"""Read saved SEC snapshots and reuse exact financial selectors without reviews."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from research import sec_refresh, sec_facts, sec_fact_review, sec_filing_evidence
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest
from extensions import financial_fill

DEFAULT_STORE = ROOT / 'reports/private/sec_store/real'


def resolve(ticker, cutoff, store=DEFAULT_STORE):
    """Only authoritative receipts, never mutable latest/state pointers."""
    ticker = sec_facts.symbol(ticker); point = as_utc(cutoff); matches = []
    for folder in sorted((Path(store) / 'entities').glob('*')):
        receipts = [sec_refresh.read(p) for p in (folder / 'receipts').glob('*.json')]
        eligible = [r for r in receipts if as_utc(r['available_at']) < point]
        if not eligible: continue
        r = max(eligible, key=lambda r: (as_utc(r['available_at']), r['sequence']))
        path = folder / 'snapshots' / (r['snapshot_id'] + '.json')
        if digest(path) != r['snapshot_sha256']: raise ValueError('SEC_SNAPSHOT_INTEGRITY_CHANGED')
        snapshot = sec_refresh.read(path)
        if ticker in snapshot['identity']['symbols']: matches.append(folder.name)
    if len(matches) != 1: raise ValueError('ISSUER_NOT_STORED' if not matches else 'AMBIGUOUS_ISSUER')
    return sec_refresh.as_of(store, matches[0], cutoff)


def validate_snapshot(snapshot, ticker, cutoff, *, illustrative=False):
    sec_refresh.validate_snapshot({k: v for k, v in snapshot.items() if k not in {'available_at', 'sequence'}})
    point = as_utc(cutoff)
    if snapshot['is_illustrative'] is not illustrative or ticker not in snapshot['identity']['symbols']:
        raise ValueError('PERSONAL_SOURCE_IDENTITY_OR_NAMESPACE_MISMATCH')
    if snapshot['entity_type'] != 'operating': raise ValueError('NONOPERATING_INSTRUMENT_FUNDAMENTALS_UNSUPPORTED')
    if as_utc(snapshot['available_at']) >= point: raise ValueError('SNAPSHOT_NOT_AVAILABLE_AT_CUTOFF')
    for source in snapshot['sources'].values():
        if as_utc(source['available_at']) > as_utc(snapshot['available_at']): raise ValueError('SOURCE_AFTER_SNAPSHOT')
        if digest(source['raw_path']) != source['raw_sha256']: raise ValueError('SEC_RAW_INTEGRITY_CHANGED')


def observed(name, fact, scope, filing, source, sid, cutoff, role, refs, reason=None):
    identifier = filing['accession'] + ':' + role + ':' + name
    available = fact is not None
    ref = sid + '#' + fact['fact_id'] if available else None
    if available:
        refs[ref] = {'url': source['source_url'], 'raw_path': source['raw_path'], 'raw_sha256': source['raw_sha256'],
            'available_at': source['available_at'], 'filed_date': filing['filed_date'], 'published_at': filing.get('accepted_at'),
            'accession': filing['accession'], 'locator': {k: fact.get(k) for k in
                ['fact_id', 'ordinal', 'dom_id', 'tag', 'context_ref', 'row_number', 'row_text', 'json_path']}}
    spec = sec_fact_review.policy()['metrics'][name]
    basis = 'instant' if spec['basis'] == 'instant' else 'annual' if filing['form'].startswith('10-K') else 'cumulative_ytd' if spec['basis'] == 'cashflow' else 'quarter'
    reasons = {'missing_source_fact':'해당 공시에서 단위·기간·회계 범위가 일치하는 원문 수치를 찾지 못했습니다.',
        'conflicting_source_values':'같은 필드 후보의 원문 값이 충돌해 확정하지 않았습니다.',
        'PRIMARY_DOCUMENT_NOT_STORED':'원문 HTML이 저장되지 않았고 이용 가능한 정확한 SEC 표준 필드도 없습니다.',
        'AMENDMENT_SCOPE_UNRESOLVED':'수정 공시의 영향 범위가 미확인이라 해당 공시 값은 보류합니다.'}
    return {'id': identifier, 'name': name, 'role': role, 'value': fact['value'] if available else None,
        'unit': spec['unit'], 'as_of': cutoff, 'evidence_kind': 'OBSERVED' if available else 'UNKNOWN',
        'source_refs': [ref] if ref else [], 'assumptions': [],
        'limitations': ['자동 원문 대조이며 독립 회계감사·계약 승인이 아닙니다.'] if available else [reasons.get(reason,'원문·기간·단위·회계 범위를 확정할 수 없습니다.')],
        'period': {'start': fact['start'] if available else None, 'end': fact['end'] if available else filing['report_date'], 'basis': basis},
        'scope': scope if available else None, 'form': filing['form'], 'accession': filing['accession'],
        'available_at': source['available_at'] if source else None, 'source_verification': 'DETERMINISTIC_SOURCE_MATCH' if available else 'UNAVAILABLE'}


def extract(snapshot, cutoff):
    settings = financial_fill.policy(cutoff); issuer = snapshot['cik']
    settings = deepcopy(settings)
    settings['issuer_profiles'].setdefault(issuer, {'profile': 'standard_statement_rows'})
    specifications = sec_fact_review.policy()['metrics']; facts, refs, errors = [], {}, []
    api_sources = [(sid, s) for sid, s in snapshot['sources'].items() if s['kind']=='companyfacts' and
                   s['source_url']=='https://data.sec.gov/api/xbrl/companyfacts/CIK'+issuer+'.json']
    api = None
    if len(api_sources)==1:
        api_sid, api_source = api_sources[0]
        api = sec_facts.loads(Path(api_source['raw_path']).read_bytes())
    def api_fact(name, target):
        if api is None or (issuer=='0002115436' and name=='revenue'): return None, None
        normal = sec_facts.normalize(api, issuer, [target], api_sid)['sections']
        if not normal: return None, None
        m = normal[0]['metrics'][name]
        if m['value'] is None: return None, None
        tag = m['tag']; entries = api['facts']['us-gaap'][tag]['units'][m['unit']]
        matching = [(n,e) for n,e in enumerate(entries) if e.get('accn')==target['accession'] and
                    e.get('end')==m['end'] and e.get('start')==m['start'] and
                    e.get('filed') and e['filed']<=as_utc(cutoff).date().isoformat() and
                    not isinstance(e.get('val'), bool) and str(e.get('val'))==m['value']]
        if not matching: return None, None
        n,entry = matching[0];path = f'facts.us-gaap.{tag}.units.{m["unit"]}[{n}]'
        item = {**m, 'fact_id': path, 'tag': 'us-gaap:'+tag, 'json_path': path}
        spec = specifications[name];scopes = spec.get('tag_scopes',{}).get(item['tag'],spec['scopes'])
        return (item, scopes[0]) if len(scopes)==1 else (None,None)
    selected = sec_facts.financial_filings(snapshot['filings'])
    for filing in selected:
        # Date-only publication on the cutoff day cannot prove intraday availability.
        if not filing['report_date'] or filing['report_date'] > as_utc(cutoff).date().isoformat(): continue
        if filing['filed_date'] > as_utc(cutoff).date().isoformat(): continue
        if filing.get('accepted_at'):
            if as_utc(filing['accepted_at']) > as_utc(cutoff): continue
        elif filing['filed_date'] == as_utc(cutoff).date().isoformat(): continue
        amended = filing.get('is_amendment') or any(f['form'] == filing['form'] + '/A' and
            (not f['report_date'] or f['report_date'] == filing['report_date']) for f in snapshot['filings'])
        ids = filing['document_source_ids']; sid = ids[0] if len(ids) == 1 else None
        source = snapshot['sources'].get(sid); parsed, dims, reason = [], {}, None
        if source:
            expected = 'https://www.sec.gov/Archives/edgar/data/' + str(int(issuer)) + '/' + filing['accession'].replace('-', '') + '/'
            if source['kind'] != 'document' or not source['source_url'].startswith(expected):
                raise ValueError('PRIMARY_SOURCE_CIK_OR_ACCESSION_MISMATCH')
            raw = Path(source['raw_path']).read_bytes()
            try:
                parsed = sec_filing_evidence.parse(raw, source['raw_sha256'], issuer)
                dims = financial_fill.members(raw)
            except (ValueError, UnicodeError) as e:
                errors.append({'accession': filing['accession'], 'code': str(e).split(':')[0]}); reason = 'UNSUPPORTED_PRIMARY_DOCUMENT'
        else: reason = 'PRIMARY_DOCUMENT_NOT_STORED'
        # No downgrade from a conflicting original to a convenient API number.
        for role in ['current', 'prior_year_same_filing']:
            target = deepcopy(filing)
            if role != 'current':
                try: target['report_date'] = date.fromisoformat(filing['report_date']).replace(year=int(filing['report_date'][:4])-1).isoformat()
                except ValueError: continue
            for name, spec in specifications.items():
                if role != 'current' and name not in {'revenue', 'operating_income', 'net_income', 'diluted_eps'}: continue
                fact = definition = None; status = 'AMENDMENT_SCOPE_UNRESOLVED' if amended else reason
                if not amended and parsed:
                    # Preserve attribution: prefer consolidated ProfitLoss; never blend with parent NetIncomeLoss.
                    tags = spec['tags'] if name == 'net_income' else [None]
                    for tag in tags:
                        local = {**spec, 'tags': [tag]} if tag else spec
                        fact, status, definition = financial_fill.select(name, parsed, dims, target, local, settings, issuer)
                        # The old overlay has issuer-specific row spelling limits. The personal
                        # consumer can also use an exact unique standard consolidated tag, while
                        # retaining the old period/entity/unit/hidden/axis checks unchanged.
                        if fact is None and status=='missing_source_fact' and not (issuer=='0002115436' and name=='revenue'):
                            candidates = [f for f in parsed if sec_fact_review.eligible(f,target,local) and dims.get(f['context_ref'])==[]]
                            if local['basis']=='cashflow' and candidates:
                                first = min(f['start'] for f in candidates);candidates = [f for f in candidates if f['start']==first]
                            fact,status = financial_fill.unique(candidates)
                            if fact:
                                scopes = local.get('tag_scopes',{}).get(fact['tag'],local['scopes'])
                                if len(scopes)!=1: fact=None
                                else: definition={'scope':scopes[0]}
                        if fact: break
                scope = definition['scope'] if definition else None
                used_source,used_sid = source,sid
                if not amended and source is None:
                    fact,scope = api_fact(name,target)
                    if fact: used_source,used_sid = api_source,api_sid
                if fact and (not Decimal(fact['value']).is_finite() or fact['unit'] != spec['unit']): raise ValueError('INVALID_NUMERIC_FACT')
                datum = observed(name, fact, scope, target, used_source, used_sid, cutoff, role, refs, status)
                if fact and used_source['kind']=='companyfacts':
                    datum['source_verification']='STANDARD_API_FIELD_MATCH'
                    datum['limitations'].append('SEC companyfacts 표준 필드이며 원문 HTML 행을 확인한 값과 구분합니다.')
                facts.append(datum)
    return facts, refs, errors
