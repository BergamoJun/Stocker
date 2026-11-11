"""Offline SEC numeric acquisition/review queue. Never auto-verifies exposure.

Tag matches are discovery candidates. Companyfacts balances cannot establish
repricing, energy contracts or an unhedged economic sensitivity. No new facts
are inserted into AI_INPUT or the frozen exposure ledger by this module.
"""
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlsplit
import re

from research.rights import as_utc
from research.validation import digest, stable_hash
from . import exposure_v3, research_v3_contract as c


def acquire_filing(source, *, cik, cutoff, rights, patterns):
    """Reparse existing SEC filing bytes through the established local parser.

    Source metadata/hash/CIK and actual locator are checked by document().
    A pattern selects review work, never a truth label or an AI input.
    """
    from research import sec_filing_evidence
    if rights.get('local_numeric_review') is not True or rights.get('redistribution') is not False:
        raise ValueError('EXPOSURE_REVIEW_RIGHTS_REQUIRED')
    if as_utc(source['available_at']) >= as_utc(cutoff):
        raise ValueError('EXPOSURE_REVIEW_AVAILABILITY_INVALID')
    rows = []
    for fact in sec_filing_evidence.document(source, cik):
        families = sorted(name for name, pattern in patterns.items() if re.search(pattern, fact.get('tag') or '', re.I))
        if not families or not fact['same_entity'] or fact['hidden'] or fact['status'] != 'parsed_numeric_evidence_only':
            continue
        row = {'contract': 'sec-exposure-review-candidate-v1', 'cik': cik, 'candidate_families': families,
               'source_url': source['source_url'], 'raw_sha256': source['raw_sha256'],
               'locator': {k: fact[k] for k in ('fact_id', 'dom_id', 'tag', 'context_ref', 'row_number', 'source_sha256')},
               'value': fact['value'], 'unit': fact['unit'], 'period': {'start': fact['start'], 'end': fact['end']},
               'dimensions': fact['dimensions'], 'row_text': fact['row_text'],
               'publication_at': None, 'publication_precision': 'NOT_VERIFIED',
               'first_local_available_at': source['available_at'], 'scope': 'NOT_REVIEWED', 'rights': rights,
               'review_status': 'PENDING_HUMAN_COMPONENT_REVIEW', 'model_input_eligible': False,
               'limitation': 'tag/row numeric candidates do not establish contractual exposure; prose excluded from model'}
        row['record_sha256'] = stable_hash(row)
        rows.append(row)
    return rows


def acquire(path, *, expected_sha256, cik, source_url, retrieved_at, first_available_at,
            cutoff, rights, tags):
    path = Path(path)
    url = urlsplit(source_url)
    if rights.get('local_numeric_review') is not True or rights.get('redistribution') is not False:
        raise ValueError('EXPOSURE_REVIEW_RIGHTS_REQUIRED')
    if url.scheme != 'https' or url.netloc != 'data.sec.gov' or url.path != '/api/xbrl/companyfacts/CIK' + cik + '.json' or url.query:
        raise ValueError('EXPOSURE_REVIEW_SOURCE_INVALID')
    if digest(path) != expected_sha256:
        raise ValueError('EXPOSURE_REVIEW_RAW_CHANGED')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if str(raw['cik']).zfill(10) != cik:
        raise ValueError('EXPOSURE_REVIEW_CIK_MISMATCH')
    if not as_utc(retrieved_at) <= as_utc(first_available_at) < as_utc(cutoff):
        raise ValueError('EXPOSURE_REVIEW_AVAILABILITY_INVALID')
    requirements = {r for route in c.policy()['routes'].values() for r in route['requirements']}
    if not set(tags.values()) <= requirements:
        raise ValueError('EXPOSURE_REVIEW_REQUIREMENT_INVALID')
    rows = []
    for tag, requirement in sorted(tags.items()):
        for unit, values in raw.get('facts', {}).get('us-gaap', {}).get(tag, {}).get('units', {}).items():
            for index, value in enumerate(values):
                if value.get('end', '9999') > as_utc(cutoff).date().isoformat() or value.get('filed', '9999') > as_utc(cutoff).date().isoformat():
                    continue
                amount = Decimal(str(value['val']))
                if not amount.is_finite():
                    continue
                row = {'contract': 'sec-exposure-review-candidate-v1', 'cik': cik, 'requirement_id': requirement,
                       'source_url': source_url, 'raw_path': str(path.resolve()), 'raw_sha256': expected_sha256,
                       'locator': {'json_path': ['facts', 'us-gaap', tag, 'units', unit, index]},
                       'value': str(amount), 'unit': unit, 'period': {'start': value.get('start'), 'end': value.get('end')},
                       'filing_accession': value.get('accn'), 'filed_date': value.get('filed'),
                       'publication_at': None, 'publication_precision': 'date', 'retrieved_at': retrieved_at,
                       'first_local_available_at': first_available_at, 'scope': 'NOT_REVIEWED', 'rights': rights,
                       'review_status': 'PENDING_HUMAN_COMPONENT_REVIEW', 'model_input_eligible': False,
                       'limitation': 'tag and numeric amount alone do not establish the requested contractual component'}
                row['record_sha256'] = stable_hash(row)
                rows.append(row)
    return rows


def review(candidate, *, reviewer_id, decision, reviewed_at, scope, rationale):
    if candidate['record_sha256'] != stable_hash({k: v for k, v in candidate.items() if k != 'record_sha256'}):
        raise ValueError('EXPOSURE_REVIEW_CANDIDATE_CHANGED')
    if not reviewer_id or decision not in {'NUMERIC_CONTEXT_ONLY', 'REJECT', 'NEEDS_FILING_CONTRACT_EVIDENCE'} or not rationale or not scope:
        raise ValueError('EXPOSURE_REVIEW_DECISION_INVALID')
    if as_utc(reviewed_at) < as_utc(candidate['first_local_available_at']):
        raise ValueError('EXPOSURE_REVIEW_BACKDATED')
    return {'contract': 'sec-exposure-review-decision-v1', 'candidate_sha256': candidate['record_sha256'],
            'reviewer_id': reviewer_id, 'reviewer_authentication': 'declared offline human; not cryptographically authenticated',
            'decision': decision, 'reviewed_at': reviewed_at, 'scope': scope, 'rationale': rationale,
            'model_input_eligible': False, 'verified_business_exposure': False}


def pipeline_inventory(ai):
    ledger = exposure_v3.derive(ai, ai['as_of'])
    metrics = sorted({f['metric'] for f in ai['financial_facts']})
    links = c.policy()['legacy_property_links']
    return {'contract': 'exposure-upstream-inventory-v1', 'issuer': ai['identity'], 'input_metrics': metrics,
            'requirements': [{'requirement_id': r['requirement_id'], 'status': r['status'],
                'direct_metric': exposure_v3.DIRECT.get(r['requirement_id']),
                'legacy_properties': links.get(r['requirement_id'], []), 'bound_fact_ids': r['fact_ids'],
                'currently_populated': bool(r['fact_ids'])} for r in ledger['requirements']],
            'adapter_feeds_frozen_ledger': False, 'new_contractual_evidence_acquired': False,
            'zero_values': 'frozen candidate-2 requires positive amounts; verified zero is not proof of positive route exposure'}
