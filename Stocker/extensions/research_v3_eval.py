"""Preregistered evaluation; raw model errors cannot be rescued by rule blocks."""
from decimal import Decimal
import json
from pathlib import Path
import time
import re
import jsonschema

from research.sec_transport import now
from research.validation import digest, stable_hash
from . import research_v3_contract as contract, research_v3 as engine, local_inference

FIELDS = ('economic_subject', 'economic_variable', 'event_type', 'variable_direction',
          'statement_type', 'negation', 'modality', 'conditionality', 'condition_quote',
          'temporal_reference', 'temporal_expression', 'horizon')


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def register(inputs, answers, path, *, clock=now):
    ids = [c['id'] for c in inputs]
    if len(ids) != len(set(ids)) or set(ids) != {g['id'] for g in answers} or len(answers) != len(inputs):
        raise ValueError('V3_EVAL_CASE_SET_INVALID')
    for c in inputs:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', c['id']) or c.get('partition') == 'locked_final':
            raise ValueError('V3_EVAL_ID_OR_PARTITION_INVALID')
        contract.validate_ai(c['ai'], c['ai']['as_of'])
    value = {'contract': 'research-v3-preregistration', 'registered_at': clock(),
             'input_ids': ids, 'inputs_sha256': stable_hash(inputs), 'answers_sha256': stable_hash(answers),
             'method_hashes': contract.methods(), 'thresholds': contract.policy()['quality'],
             'configurations': {k: contract.runtime_settings(k) for k in 'ABCDE'},
             'blinding': 'developer-authored synthetic gold hidden from inference; not independent human adjudication',
             'independent_adjudicated_real_events': 0, 'price_evaluations': 0}
    value['record_sha256'] = stable_hash(value)
    write_once(path, value)
    return value


def check_registration(inputs, answers, registration):
    expected = {'inputs_sha256': stable_hash(inputs), 'answers_sha256': stable_hash(answers),
                'method_hashes': contract.methods(), 'thresholds': contract.policy()['quality'],
                'input_ids': [c['id'] for c in inputs],
                'configurations': {k: contract.runtime_settings(k) for k in 'ABCDE'}}
    if any(registration.get(k) != v for k, v in expected.items()) or registration['record_sha256'] != stable_hash({k: v for k, v in registration.items() if k != 'record_sha256'}):
        raise ValueError('V3_PREREGISTRATION_CHANGED')


def summarize(inputs, answers, rows):
    by_input, gold = {c['id']: c for c in inputs}, {g['id']: g for g in answers}
    if len(rows) != len(inputs) or {r['id'] for r in rows} != set(by_input) or len(gold) != len(inputs) or set(gold) != set(by_input):
        raise ValueError('V3_EVALUATION_INCOMPLETE_OR_DUPLICATED')
    splits = {}
    for row in rows:
        g = gold[row['id']]
        s = splits.setdefault(g['split'], {'cases': 0, 'expected_propositions': 0, 'exact_raw_semantics': 0,
            'raw_contracts': 0, 'expected_contracts': 0, 'raw_critic_correct': 0, 'raw_false_approvals': 0,
            'rule_blocks': 0, 'raw_research_correct': 0, 'unnecessary_abstentions': 0, 'answerable': 0,
            'quote_bound': 0, 'raw_direction_correct': 0, 'raw_temporal_correct': 0,
            'raw_negation_condition_correct': 0, 'exposure_status_correct': 0, 'exposure_status_expected': 0})
        s.setdefault('pre_model_safety_isolations', 0)
        s['cases'] += 1
        expected = g['propositions']
        s['expected_propositions'] += len(expected)
        # Fixed task denominators survive omissions and output failures.
        s['expected_contracts'] += (len(engine.payloads(by_input[row['id']]['ai'])) + 2 * len(expected)) if expected else 0
        analysis = row.get('analysis')
        raw_props, cards, props = {}, {}, {}
        if analysis is not None:
            for semantic in analysis['semantic_rows']:
                raw = semantic['raw']
                if raw is not None:
                    try:
                        contract.validate(raw, 'semantic_raw')
                        s['raw_contracts'] += 1
                    except jsonschema.ValidationError:
                        continue
                    for p in raw['propositions']:
                        raw_props[p['exact_quote']] = p
            props = {p['proposition_id']: p for p in analysis['propositions']}
            for card in analysis['cards']:
                p = props[card['proposition_id']]
                cards[p['exact_quote']] = card
                for key in ('researcher_raw_output', 'critic_raw_output'):
                    s['raw_contracts'] += card[key]['status'] == 'COMPLETED'
                c = card['critic_raw_output']
                if c['status'] == 'COMPLETED' and c['raw_output']['verdict'] == 'SUPPORTED_CONDITIONAL':
                    allowed = next((e['should_allow'] for e in expected if e['exact_quote'] == p['exact_quote']), False)
                    s['raw_false_approvals'] += not allowed
                    s['rule_blocks'] += card['final_status'] != 'SUPPORTED_CONDITIONAL'
            reqs = {r['requirement_id']: r['status'] for r in analysis['exposure']['requirements']}
            s['exposure_status_correct'] += sum(reqs.get(k) == v for k, v in g.get('exposure_status', {}).items())
        s['exposure_status_expected'] += len(g.get('exposure_status', {}))
        # A Python injection isolation is a software safety result, never a raw
        # model success or a rescued model answer.
        if not expected:
            s['pre_model_safety_isolations'] += bool(analysis is not None and not raw_props and any(x['reason'] == 'UNTRUSTED_INSTRUCTION' and not x['model_called'] for x in analysis['semantic_rows']))
        for e in expected:
            p, card = raw_props.get(e['exact_quote']), cards.get(e['exact_quote'])
            s['answerable'] += e['answerable']
            if p:
                s['exact_raw_semantics'] += all(p[k] == e[k] for k in FIELDS)
                s['raw_direction_correct'] += p['variable_direction'] == e['variable_direction']
                s['raw_temporal_correct'] += all(p[k] == e[k] for k in ('statement_type', 'temporal_reference', 'horizon'))
                s['raw_negation_condition_correct'] += all(p[k] == e[k] for k in ('negation', 'conditionality', 'condition_quote', 'modality'))
                s['quote_bound'] += any(x['exact_quote'] == p['exact_quote'] and x['source_id'] in {d['source_id'] for d in by_input[row['id']]['ai']['official_documents']} for x in props.values())
            if card and card['critic_raw_output']['status'] == 'COMPLETED':
                s['raw_critic_correct'] += card['critic_raw_output']['raw_output']['verdict'] in e['critic_verdicts']
            research = card['researcher_raw_output'] if card else None
            if research and research['status'] == 'COMPLETED':
                r = research['raw_output']
                correct = (r['decision'] == 'PROPOSE' and bool(r['selected_route_ids']) and set(r['selected_route_ids']) <= set(e['research_routes'])) if e['research_routes'] else r['decision'] in {'ABSTAIN', 'UNKNOWN'} and not r['selected_route_ids']
                s['raw_research_correct'] += correct
                s['unnecessary_abstentions'] += e['answerable'] and r['decision'] in {'ABSTAIN', 'UNKNOWN'}
            elif e['answerable']:
                s['unnecessary_abstentions'] += 1
    q = contract.policy()['quality']
    for s in splits.values():
        n = s['expected_propositions']
        for out, numerator, denominator in [('raw_semantic_accuracy', 'exact_raw_semantics', n),
                ('raw_critic_accuracy', 'raw_critic_correct', n), ('raw_research_accuracy', 'raw_research_correct', n),
                ('contract_acceptance', 'raw_contracts', s['expected_contracts']),
                ('unnecessary_abstention_rate', 'unnecessary_abstentions', s['answerable'])]:
            s[out] = str(Decimal(s[numerator]) / denominator) if denominator else None
        s['passed'] = bool(n) and (Decimal(s['raw_semantic_accuracy']) >= Decimal(q['minimum_semantic_accuracy'])
            and Decimal(s['raw_critic_accuracy']) >= Decimal(q['minimum_raw_critic_accuracy'])
            and Decimal(s['raw_research_accuracy']) >= Decimal(q['minimum_semantic_accuracy'])
            and Decimal(s['contract_acceptance']) >= Decimal(q['minimum_contract_acceptance'])
            and s['raw_false_approvals'] == 0
            and (s['unnecessary_abstention_rate'] is None or Decimal(s['unnecessary_abstention_rate']) <= Decimal(q['maximum_unnecessary_abstention'])))
    any_run = any(r.get('analysis') is not None for r in rows)
    return {'splits': splits, 'raw_false_approvals': sum(s['raw_false_approvals'] for s in splits.values()),
            'rule_blocks': sum(s['rule_blocks'] for s in splits.values()),
            'decision': 'NOT_RUN' if not any_run else 'quality_gate_passed' if all(s['passed'] for s in splits.values()) else 'quality_gate_failed',
            'independent_actual_event_accuracy': None, 'stock_prediction_accuracy': None}


def run(inputs, answers, registration, output, *, configuration='A', backend=None, clock=now):
    check_registration(inputs, answers, registration)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    telemetry = {'available_memory_before_bytes': local_inference.available_memory(),
                 'minimum_available_memory_bytes': contract.runtime_settings(configuration)['minimum_available_memory_bytes'],
                 'inference': 'MOCK_NOT_ACTUAL_MODEL' if backend is not None else 'local_qwen_cpu',
                 'local_model_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
                 'peak_model_resident_bytes': None, 'peak_model_memory_status': 'NOT_MEASURED',
                 'external_calls': 0, 'cost_usd': 0}
    rows, reason = [], None

    def execute(active, cases):
        initial = {k: getattr(active, k, 0) for k in ('calls', 'prompt_tokens', 'completion_tokens')}
        for c in cases:
            try:
                analysis = engine.analyze(c['ai'], active, configuration=configuration)
                row = {'id': c['id'], 'analysis': analysis, 'status': 'COMPLETED'}
            except (ValueError, KeyError, jsonschema.ValidationError):
                row = {'id': c['id'], 'analysis': None, 'status': 'INPUT_OR_MODEL_FAILED'}
            rows.append(row)
            write_once(output / (c['id'] + '.json'), row)
        telemetry['local_model_calls'] += getattr(active, 'calls', 0) - initial['calls']
        telemetry['prompt_tokens'] += getattr(active, 'prompt_tokens', 0) - initial['prompt_tokens']
        telemetry['completion_tokens'] += getattr(active, 'completion_tokens', 0) - initial['completion_tokens']

    if backend is not None:
        execute(backend, inputs)
    else:
        try:
            # Restart only our own ephemeral runtime between bounded batches;
            # never raise the existing 24-call server limit or retry a failed case.
            pending = list(inputs)
            while pending:
                settings = contract.runtime_settings(configuration)
                batch, budget = [], 0
                for case in pending:
                    ps = engine.payloads(case['ai'])[:contract.policy()['maximum_paragraphs']]
                    from .semantic_v3 import spans
                    reserve = len(ps) + 2 * min(contract.policy()['maximum_cards'], sum(len(spans(p['text'])) for p in ps))
                    if batch and budget + reserve > settings['maximum_calls_per_server']:
                        break
                    batch.append(case)
                    budget += reserve
                with local_inference.Server(settings=settings) as active:
                    execute(active, batch)
                pending = pending[len(batch):]
        except (local_inference.Deferred, OSError) as error:
            reason = str(error) if isinstance(error, local_inference.Deferred) else 'LOCAL_RUNTIME_UNAVAILABLE'
    present = {r['id'] for r in rows}
    for c in inputs:
        if c['id'] not in present:
            row = {'id': c['id'], 'analysis': None, 'status': 'NOT_RUN', 'reason': reason}
            rows.append(row)
            write_once(output / (c['id'] + '.json'), row)
    telemetry['elapsed_seconds'] = round(time.monotonic() - start, 6)
    telemetry['available_memory_after_bytes'] = local_inference.available_memory()
    receipt = {'contract': 'research-v3-evaluation', 'evaluated_at': clock(), 'configuration': configuration,
               'registration_sha256': stable_hash(registration), 'method_hashes': contract.methods(),
               'metrics': summarize(inputs, answers, rows), 'rows': rows, 'telemetry': telemetry, 'reason': reason,
               'scope': 'authored synthetic semantic/exposure compliance, not independent real-event/price performance'}
    receipt['record_sha256'] = stable_hash(receipt)
    write_once(output / 'RESULT.json', receipt)
    return receipt


def validate(receipt, inputs, answers, registration):
    check_registration(inputs, answers, registration)
    from .research_v3_report import replay_analysis
    lookup = {c['id']: c['ai'] for c in inputs}
    for row in receipt['rows']:
        if row.get('analysis') is not None:
            replay_analysis(lookup[row['id']], row['analysis'])
    if receipt['method_hashes'] != contract.methods() or receipt['registration_sha256'] != stable_hash(registration) or receipt['metrics'] != summarize(inputs, answers, receipt['rows']) or receipt['record_sha256'] != stable_hash({k: v for k, v in receipt.items() if k != 'record_sha256'}):
        raise ValueError('V3_EVALUATION_RECEIPT_CHANGED')
    # Mock success cannot authorize an actual model, even with perfect metrics.
    return receipt['telemetry']['inference'] == 'local_qwen_cpu' and receipt['telemetry']['local_model_calls'] > 0 and receipt['metrics']['decision'] == 'quality_gate_passed'
