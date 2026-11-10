"""Versioned model-safe contracts; legacy policies are never mutated."""
import json
from pathlib import Path

import jsonschema

from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest
from . import analysis_contract, local_inference

BASE = Path(__file__).resolve().parent
NAMES = (
    'RESEARCH_V3.md', 'research_v3_contract.py', 'research_v3_policy.json',
    'research_v3.schema.json', 'semantic_v3.py', 'exposure_v3.py', 'evidence_v3.py',
    'research_v3.py', 'research_v3_report.py', 'research_v3_eval.py',
)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def methods():
    own = {'extensions/' + name: digest(BASE / name) for name in NAMES}
    # Pin dependencies actually used, without editing their historical method sets.
    from . import exposure_model
    return {**own, **analysis_contract.methods(), **exposure_model.methods(),
            'extensions/local_inference.py': digest(BASE / 'local_inference.py'),
            'extensions/local_ai_policy.json': digest(BASE / 'local_ai_policy.json')}


def policy():
    value = read(BASE / 'research_v3_policy.json')
    if (value['version'] != 'research-engine-v3-candidate-2' or value['monthly_cost_usd'] != 0
            or any(value[k] for k in ('external_inference', 'training', 'score_release', 'price_input'))
            or value['quality']['maximum_raw_false_approvals'] != 0
            or value['quality']['minimum_raw_critic_accuracy'] != '0.85'
            or value['quality']['minimum_contract_acceptance'] != '0.90'):
        raise ValueError('V3_POLICY_BOUNDARY_CHANGED')
    return value


def spec(name):
    definitions = read(BASE / 'research_v3.schema.json')['$defs']
    return {'$ref': '#/$defs/' + name, '$defs': definitions}


def validate(value, name):
    jsonschema.Draft202012Validator(spec(name), format_checker=jsonschema.FormatChecker()).validate(value)
    return value


def validate_ai(ai, cutoff):
    # assemble checks allowed sources, fact identity/period/time, source URLs and paragraph hashes.
    analysis_contract.schema(ai, 'ai_input')
    analysis_contract.assemble(ai['identity'], ai['as_of'], ai['financial_facts'],
                               ai['official_documents'], {}, packet_id=ai['packet_id'],
                               illustrative=ai['is_illustrative'], coverage=ai['coverage'])
    if as_utc(ai['as_of']) > as_utc(cutoff):
        raise ValueError('V3_FUTURE_INPUT')
    if len({d['source_id'] for d in ai['official_documents']}) != len(ai['official_documents']):
        raise ValueError('V3_DUPLICATE_SOURCE')
    for doc in ai['official_documents']:
        if len({p['id'] for p in doc['paragraphs']}) != len(doc['paragraphs']):
            raise ValueError('V3_DUPLICATE_PARAGRAPH')
        if doc['time_precision'] == 'date' and doc['published_at'] is not None:
            raise ValueError('V3_INVENTED_PUBLICATION_TIME')
        if doc['time_precision'] == 'second' and (not doc['published_at'] or
                as_utc(doc['published_at']) >= as_utc(ai['as_of']).replace(microsecond=0) or
                as_utc(doc['published_at']).date().isoformat() != doc['published_date']):
            raise ValueError('V3_PUBLICATION_TIME_INVALID')
    return ai


def runtime_settings(configuration='A'):
    p = policy()
    if configuration not in p['experiments']:
        raise ValueError('V3_UNKNOWN_CONFIGURATION')
    settings = dict(local_inference.policy())
    settings.update(id=p['version'] + '-' + configuration,
                    temperature=p['experiments'][configuration]['temperature'],
                    maximum_output_tokens=960)
    return settings
