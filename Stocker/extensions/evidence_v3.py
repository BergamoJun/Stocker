"""Deterministic fact/provenance validation, never a raw model judgment."""
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit
import re
import jsonschema

from research.rights import as_utc
from . import research_v3_contract as contract, semantic_v3


def validate(ai, at, *, assertions=None, proposition=None, payload=None, required=None):
    errors, missing = [], []
    try:
        contract.validate_ai(ai, at)
    except (ValueError, KeyError, TypeError, jsonschema.ValidationError):
        errors.append('invalid_input_binding')
    facts = {f['fact_id']: f for f in ai['financial_facts']}
    if len(facts) != len(ai['financial_facts']):
        errors.append('duplicate_fact_id')
    raw_by_source, values = {}, {}
    for f in ai['financial_facts']:
        if f['cik'] != ai['identity']['cik']:
            errors.append('issuer_cik_mismatch')
        if f['validation_kind'] not in {'recorded_source_review', 'deterministic_source_match'}:
            errors.append('unverified_fact')
        if as_utc(f['available_at']) >= as_utc(at).replace(microsecond=0):
            errors.append('future_fact_availability')
        end = date.fromisoformat(f['end'])
        if end > as_utc(at).date() or f['start'] and date.fromisoformat(f['start']) > end:
            errors.append('invalid_reporting_period')
        if (f['basis'] == 'instant') != (f['start'] is None):
            errors.append('instant_flow_period_mismatch')
        if (as_utc(at).date() - end).days > (400 if f['basis'] == 'annual' else 120):
            missing.append('stale_fact:' + f['fact_id'])
        refs = f['source_refs']
        if not refs:
            errors.append('missing_source_link')
        if len({r['source_id'] for r in refs}) != len(refs):
            errors.append('duplicate_source_reference')
        for r in refs:
            if (urlsplit(r['url']).scheme != 'https' or urlsplit(r['url']).netloc not in {'www.sec.gov', 'data.sec.gov'}
                    or not re.fullmatch('[a-f0-9]{64}', r['raw_sha256']) or not r['locator']
                    or r['locator'].get('source_sha256', r['raw_sha256']) != r['raw_sha256']):
                errors.append('source_hash_or_locator_invalid')
            if r['source_id'] in raw_by_source and raw_by_source[r['source_id']] != r['raw_sha256']:
                errors.append('conflicting_source_versions')
            raw_by_source[r['source_id']] = r['raw_sha256']
        key = (f['metric'], f['start'], f['end'], f['basis'], f['unit'], f['scope'])
        value = Decimal(f['value'])
        if not value.is_finite():
            errors.append('nonfinite_value')
        if key in values and values[key] != value:
            errors.append('conflicting_numeric_facts')
        values[key] = value
    for assertion in assertions or []:
        f = facts.get(assertion.get('fact_id'))
        if f is None:
            errors.append('unknown_fact_id')
            continue
        for key in ('cik', 'metric', 'unit', 'basis', 'scope', 'start', 'end', 'available_at'):
            if assertion.get(key) != f[key]:
                errors.append('fact_assertion_mismatch:' + key)
        try:
            if Decimal(assertion['value']) != Decimal(f['value']):
                errors.append('numeric_value_mismatch')
        except (KeyError, ArithmeticError):
            errors.append('invalid_numeric_assertion')
        if assertion.get('source_refs') != f['source_refs']:
            errors.append('assertion_source_mismatch')
    if proposition is not None:
        if payload is None:
            errors.append('missing_paragraph_source')
        else:
            try:
                raw = {k: proposition[k] for k in contract.spec('semantic_raw')['$defs']['semantic_raw']['properties']['propositions']['items']['properties']}
                rebuilt = semantic_v3.bind({'propositions': [raw]}, payload)[0]
                if rebuilt != proposition:
                    errors.append('quote_or_proposition_binding_mismatch')
            except (ValueError, KeyError):
                errors.append('quote_not_source_bound')
    for r in required or []:
        if r['status'] == 'CONFLICTED':
            errors.append('conflicting_exposure:' + r['requirement_id'])
        elif r['status'] != 'VERIFIED':
            missing.append('unverified_exposure:' + r['requirement_id'])
        if not set(r['fact_ids']) <= set(facts):
            errors.append('exposure_fact_missing')
        expected_sources = {s['source_id'] for fid in r['fact_ids'] if fid in facts for s in facts[fid]['source_refs']}
        if set(r['source_ids']) != expected_sources:
            errors.append('exposure_source_mismatch')
    return {'evidence_verification': 'failed' if errors else 'incomplete' if missing else 'passed',
            'errors': sorted(set(errors)), 'missing': sorted(set(missing)),
            'semantic_truth_certified': False, 'performed_by': 'deterministic_python'}
