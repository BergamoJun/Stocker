"""Versioned SHADOW proposition research; no investment release or training."""
import json
from pathlib import Path
import jsonschema
from research.validation import digest, stable_hash
from research.sec_transport import now
from .. import gpu_inference, research_v3_contract as legacy

VERSION = 'research-engine-v4-candidate-1'
BASE = Path(__file__).resolve().parent
FILES = ['__init__.py', 'segmentation.py', 'engine.py', 'execution.py', 'evaluation.py',
         'labels.py', 'diagnostics.py', 'exposure.py', 'README.md', 'AGENTS.md']
VARIABLES = {'interest_rate': 'financing_policy', 'energy_price': 'commodity_price',
             'production_volume': 'energy_supply', 'consumption_volume': 'energy_demand',
             'economic_growth': 'economic_growth', 'revenue': 'company_financial', 'unknown': 'unknown'}
DIRECTIONS = ['increase', 'decrease', 'unchanged', 'non_increase', 'non_decrease', 'unknown']
ASSERTIONS = ['observation', 'expected', 'possible', 'hypothetical', 'unknown']
NEGATIONS = ['none', 'predicate', 'expectation', 'double', 'unclear']
TIMES = ['past', 'present', 'future', 'mixed', 'unknown']
DECISIONS = ['SUPPORTED', 'CONTRADICTED', 'AMBIGUOUS', 'UNSUPPORTED', 'INSUFFICIENT_CONTEXT', 'INFERENCE_FAILED']
LIMITS = {'source_calls': 160, 'authored_calls': 96, 'critic_calls': 12, 'total_calls': 268,
          'propositions_per_unit': 2, 'units_per_server': 8, 'cache_bytes': 536870912}
THRESHOLDS = {'minimum_supported_precision': .90, 'minimum_supported_recall': .80,
              'minimum_field_accuracy': .85, 'minimum_contract_acceptance': .90,
              'maximum_false_approvals': 0, 'maximum_unnecessary_abstention': .25}


def enum(values): return {'enum': sorted(values) if isinstance(values,set) else list(values)}
def string(n=600): return {'type': 'string', 'maxLength': n}
def array(item, n=4): return {'type': 'array', 'items': item, 'maxItems': n}
def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


PROPOSITION = obj({'span_id': string(80), 'exact_quote': string(1600), 'subject_quote': string(300),
    'predicate_quote': string(300), 'economic_variable': enum(VARIABLES),
    'event_type': enum(set(VARIABLES.values())), 'direction': enum(DIRECTIONS),
    'assertion': enum(ASSERTIONS), 'negation_scope': enum(NEGATIONS),
    'condition_quotes': array(string()), 'time_quotes': array(string(100)),
    'temporal_reference': enum(TIMES), 'uncertainty_reasons': array(enum([
        'subject_unresolved', 'scope_unclear', 'time_unclear', 'variable_unclear', 'nonassertive', 'other']))})
EXTRACTION = obj({'propositions': array(PROPOSITION, 2),
                  'omission_reason': enum(['none', 'no_economic_assertion', 'insufficient_context', 'output_cap'])})
CHECK = obj({'judgment': enum(['consistent', 'contradicted', 'ambiguous', 'insufficient_context']),
             'issues': array(enum(['subject', 'variable', 'event_type', 'direction', 'negation', 'condition',
                                   'assertion', 'time', 'unrelated', 'no_assertion', 'context']), 6),
             'evidence_quotes': array(string(), 4)})


def validate(value, schema):
    jsonschema.Draft202012Validator(schema).validate(value)
    return value


def methods():
    return {**gpu_inference.methods(), 'extensions/backend_identity.py': digest(BASE.parent/'backend_identity.py'),
            **{f'extensions/research_v4/{n}': digest(BASE/n) for n in FILES}}


def settings(): return gpu_inference.settings('B')
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def write_once(path, value):
    from research.local_files import atomic_json
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)


def seal(value):
    from copy import deepcopy
    value = deepcopy(value)
    return {**value, 'record_sha256': stable_hash(value)}


def verify_seal(value):
    if value['record_sha256'] != stable_hash({k: v for k, v in value.items() if k != 'record_sha256'}):
        raise ValueError('V4_RECORD_CHANGED')


def boundaries():
    return {'mode': 'SHADOW', 'official_ai_adopted': False, 'released_score': None,
            'released_target': None, 'released_price_plan': None, 'net_effect': 'UNKNOWN',
            'stock_effect': None, 'training': False, 'external_inference_calls': 0,
            'independently_validated': False, 'monthly_data_budget_usd': 0}
