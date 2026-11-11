"""Blind offline annotation workflow, not independent gold or price evaluation."""
from collections import Counter

from research.rights import as_utc
from research.sec_transport import now
from research.validation import stable_hash, digest
from . import research_v3_contract as c
from .research_v3_eval import write_once


def corpus(inputs):
    rows = []
    seen = set()
    for ai in inputs:
        c.validate_ai(ai, ai['as_of'])
        for doc in ai['official_documents']:
            # Same macro document across issuers stays one source-event group.
            event_key = stable_hash([doc['source'], doc['url'], doc['record_sha256']])
            for paragraph in doc['paragraphs']:
                case_id = stable_hash([event_key, paragraph['sha256'], ai['identity']['cik']])
                if case_id in seen:
                    continue
                seen.add(case_id)
                rows.append({'case_id': case_id, 'source_event_key': event_key,
                    'issuer': ai['identity'], 'source_id': doc['source_id'], 'source_url': doc['url'],
                    'source_record_sha256': doc['record_sha256'], 'paragraph_id': paragraph['id'],
                    'paragraph_sha256': paragraph['sha256'], 'exact_excerpt': paragraph['text'],
                    'published_date': doc['published_date'], 'published_at': doc['published_at'],
                    'publication_precision': doc['time_precision'], 'first_local_available_at': doc['available_at'],
                    'analysis_cutoff': ai['as_of'], 'facts': ai['financial_facts'], 'input_sha256': stable_hash(ai),
                    'rights_status': 'already-approved analysis_contract paragraph policy; no training/redistribution extension',
                    'partition': 'annotation_pending', 'adjudicated_semantics': None, 'adjudicated_exposures': None,
                    'acceptable_mechanisms': None, 'unacceptable_mechanisms': None,
                    'supporting_evidence': None, 'contradictory_evidence': None,
                    'reviewer_ids': [], 'adjudication_status': 'PENDING_TWO_INDEPENDENT_HUMANS',
                    'price_evaluation_eligible': False, 'historical_reaction_eligible': False})
    return rows


def register(rows, path):
    if not rows or len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('ANNOTATION_CASE_SET_INVALID')
    if any(r['partition'] == 'locked_final' for r in rows):
        raise ValueError('ANNOTATION_LOCKED_FINAL_FORBIDDEN')
    value = {'contract': 'independent-annotation-protocol-v1', 'registered_at': now(),
             'method_sha256': digest(c.BASE / 'independent_validation_v1.py'),
             'corpus_sha256': stable_hash(rows), 'case_order': [r['case_id'] for r in rows],
             'source_event_groups': len({r['source_event_key'] for r in rows}),
             'issuer_event_pairs': len({(r['issuer']['cik'], r['source_event_key']) for r in rows}),
             'requirements': ['two distinct independent human annotators, blinded to model and outcomes',
                 'third independent human adjudicates disagreements; no tested-model labels',
                 'exact source/quote/component IDs for every label and counterexample',
                 'freeze gold before one model run; keep gold out of model_view',
                 'group split by source-event; report event and issuer-event denominators',
                 'reviewer independence and identity require offline verification by audit owner'],
             'future_price_protocol': ['rights before any read', 'verified publication seconds separate from first local availability',
                 'frozen prediction before first reaction open', 'XNYS sessions and price adjustment definition',
                 'selection/overlap/confound/expectations review', 'no current evidence backfill or locked final reads'],
             'status': 'INDEPENDENT_EVALUATION_NOT_RUN', 'human_reviews_completed': 0,
             'price_outcomes_read': 0, 'model_generated_gold': False}
    value['record_sha256'] = stable_hash(value)
    write_once(path, value)
    return value


def model_view(row):
    if row['partition'] == 'locked_final':
        raise ValueError('ANNOTATION_LOCKED_FINAL_FORBIDDEN')
    cutoff = as_utc(row['analysis_cutoff'])
    if as_utc(row['first_local_available_at']) >= cutoff:
        raise ValueError('ANNOTATION_SOURCE_FUTURE')
    if row['published_date'] > cutoff.date().isoformat() or row['publication_precision'] == 'date' and row['published_at'] is not None:
        raise ValueError('ANNOTATION_PUBLICATION_INVALID')
    if row['published_at'] and as_utc(row['published_at']) >= cutoff:
        raise ValueError('ANNOTATION_PUBLICATION_FUTURE')
    if any(as_utc(f['available_at']) >= cutoff or f['end'] > cutoff.date().isoformat() or f['cik'] != row['issuer']['cik'] for f in row['facts']):
        raise ValueError('ANNOTATION_FACT_FUTURE_OR_CIK')
    return {k: row[k] for k in ('case_id', 'issuer', 'source_id', 'source_url', 'source_record_sha256',
        'paragraph_id', 'paragraph_sha256', 'exact_excerpt', 'published_date', 'published_at',
        'publication_precision', 'first_local_available_at', 'analysis_cutoff', 'facts')}


def check_registration(rows, registration):
    if registration['method_sha256'] != digest(c.BASE / 'independent_validation_v1.py') or registration['corpus_sha256'] != stable_hash(rows) or registration['case_order'] != [r['case_id'] for r in rows] or registration['record_sha256'] != stable_hash({k: v for k, v in registration.items() if k != 'record_sha256'}):
        raise ValueError('ANNOTATION_PREREGISTRATION_CHANGED')
    for row in rows:
        model_view(row)
    return registration['status']
