"""Additional diagnostics, never revisions of frozen candidate-2 metrics."""
from collections import Counter

from . import research_v3_eval as evaluation, semantic_v3, research_v3

FIELDS = evaluation.FIELDS
CATEGORIES = ('straightforward_positive', 'negative_claim', 'double_negation', 'conditional',
              'hypothetical', 'forward_looking', 'historical', 'mixed_time_horizons',
              'multiple_economic_variables', 'contradictory_evidence', 'ambiguous_language', 'prompt_injection')
TAXONOMY = ('semantic_extraction_error', 'unsupported_atomic_proposition', 'quote_binding_error',
            'temporal_interpretation_error', 'negation_or_conditionality_error', 'issuer_identity_mismatch',
            'missing_exposure_evidence', 'model_hallucination', 'contradictory_verdict_reason',
            'insufficient_independent_counter_evidence', 'over_abstention', 'inference_runtime_failure')


def categories(gold, proposition):
    category = gold.get('category', '')
    tags = set()
    if 'injection' in category:
        tags.add('prompt_injection')
    if 'hypothetical' in category:
        tags.add('hypothetical')
    if 'mixed_past_future' in category:
        tags.add('mixed_time_horizons')
    if 'multiple_economic_variables' in category or 'rate_vs_stock' in category:
        tags.add('multiple_economic_variables')
    if 'opposing' in category:
        tags.add('contradictory_evidence')
    if proposition:
        if proposition['negation'] == 'double_negation': tags.add('double_negation')
        if proposition['negation'] == 'negated' or proposition['variable_direction'] == 'decrease': tags.add('negative_claim')
        if proposition['conditionality'] == 'conditional': tags.add('conditional')
        if proposition['statement_type'] == 'forward_outlook': tags.add('forward_looking')
        if proposition['statement_type'] == 'historical_observation': tags.add('historical')
        if proposition['variable_direction'] == 'unknown': tags.add('ambiguous_language')
        if proposition['negation'] == 'none' and proposition['conditionality'] == 'unconditional' and proposition['variable_direction'] == 'increase': tags.add('straightforward_positive')
    return tags


def diagnose(inputs, gold, rows):
    # Reuse the frozen denominator validation and publish its result unchanged.
    frozen = evaluation.summarize(inputs, gold, rows)
    labels = {g['id']: g for g in gold}
    counts = {name: Counter(expected_propositions=0, raw_exact=0, cases=0) for name in CATEGORIES}
    errors = {name: [] for name in TAXONOMY}
    field_errors = Counter()
    details = []
    for row in rows:
        g = labels[row['id']]; analysis = row.get('analysis')
        props = [p for s in analysis['semantic_rows'] for p in (s['raw'] or {}).get('propositions', [])] if analysis else []
        by_quote = {p['exact_quote']: p for p in props}
        if analysis:
            for s in analysis['semantic_rows']:
                if s['status'] == 'INFERENCE_FAILED': errors['inference_runtime_failure'].append(row['id'])
                if s['reason'] and 'SEMANTIC_OUTPUT' in s['reason']: errors['quote_binding_error'].append(row['id'])
            for card in analysis['cards']:
                if any(r['status'] != 'VERIFIED' for route in card['routes']['hypothetical_routes'] for r in route['requirements']):
                    errors['missing_exposure_evidence'].append(row['id'])
                if card['counter_evidence_status'] != 'FOUND': errors['insufficient_independent_counter_evidence'].append(row['id'])
                if card['deterministic_validation']['selection_errors']: errors['model_hallucination'].append(row['id'])
                if card['deterministic_validation']['critic_errors']: errors['contradictory_verdict_reason'].append(row['id'])
                for key in ['researcher_raw_output', 'critic_raw_output']:
                    if card[key]['status'] == 'INFERENCE_FAILED': errors['inference_runtime_failure'].append(row['id'])
            for p in analysis['propositions']:
                if p['evidence_status'] != 'SEMANTICALLY_SUPPORTED': errors['unsupported_atomic_proposition'].append(row['id'])
        tags_seen = categories(g, None)
        for expected in g['propositions']:
            raw = by_quote.get(expected['exact_quote'])
            mismatches = [k for k in FIELDS if raw is None or raw.get(k) != expected[k]]
            for k in mismatches: field_errors[k] += 1
            if mismatches: errors['semantic_extraction_error'].append(row['id'])
            if set(mismatches) & {'statement_type', 'temporal_reference', 'temporal_expression', 'horizon'}:
                errors['temporal_interpretation_error'].append(row['id'])
            if set(mismatches) & {'negation', 'conditionality', 'condition_quote', 'modality'}:
                errors['negation_or_conditionality_error'].append(row['id'])
            if raw is None: errors['quote_binding_error'].append(row['id'])
            tags = categories(g, expected); tags_seen |= tags
            for tag in tags:
                counts[tag]['expected_propositions'] += 1
                counts[tag]['raw_exact'] += not mismatches
            details.append({'case_id': row['id'], 'category': g.get('category'), 'mismatched_fields': mismatches,
                            'quote_present': raw is not None})
        if analysis and g['propositions']:
            by_id = {p['proposition_id']: p for p in analysis['propositions']}
            cards = {by_id[card['proposition_id']]['exact_quote']: card for card in analysis['cards']}
            for expected in g['propositions']:
                card = cards.get(expected['exact_quote'])
                raw = card['researcher_raw_output']['raw_output'] if card else None
                if expected['answerable'] and (not raw or raw['decision'] in {'ABSTAIN', 'UNKNOWN'}):
                    errors['over_abstention'].append(row['id'])
        for tag in tags_seen: counts[tag]['cases'] += 1
    category_metrics = {}
    for tag, count in counts.items():
        n = count['expected_propositions']
        category_metrics[tag] = {**dict(count), 'raw_accuracy': count['raw_exact'] / n if n else None,
            'status': 'MEASURED_AUTHORED_ONLY' if n else 'SOFTWARE_SAFETY_ONLY' if count['cases'] else 'NOT_MEASURED'}
    return {'contract': 'candidate-2-diagnostics-v1', 'frozen_metrics': frozen, 'categories': category_metrics,
            'field_errors': dict(field_errors), 'taxonomy': {k: {'affected_case_ids': sorted(set(v)),
                'observations': len(v), 'status': 'OBSERVED' if v else 'NOT_OBSERVED_IN_AUTHORED_TRIAL'} for k, v in errors.items()},
            'case_details': details, 'taxonomy_counts_overlap': True,
            'critic_accuracy_interpretation': 'end-to-end agreement against original gold after model extraction; not isolated critic accuracy',
            'counter_evidence_interpretation': 'not found in supplied evidence; no claim that none exists in the world',
            'independent_accuracy': None, 'stock_accuracy': None}


def grammar_coverage(ai):
    """Coverage of the actual bounded grammar; precision needs external labels."""
    paragraphs = research_v3.payloads(ai)
    total = eligible = repeated = unsafe = 0
    for paragraph in paragraphs:
        spans = semantic_v3.spans(paragraph['text'])
        quotes = Counter(s['exact_quote'] for s in spans)
        for span in spans:
            total += 1
            repeated += quotes[span['exact_quote']] > 1
            unsafe += bool(semantic_v3.INJECTION.search(paragraph['text']))
            meaning = semantic_v3.interpret(span)
            eligible += bool(meaning and meaning['variable_direction'] not in {'unknown', 'mixed'} and quotes[span['exact_quote']] == 1 and not semantic_v3.INJECTION.search(paragraph['text']))
    return {'paragraphs': len(paragraphs), 'candidate_spans': total, 'bounded_grammar_eligible_spans': eligible,
            'bounded_grammar_coverage': eligible / total if total else None,
            'duplicate_ambiguous_spans': repeated, 'injection_spans': unsafe,
            'report_paragraph_cap': 8, 'unprocessed_paragraphs_by_cap': max(0, len(paragraphs) - 8),
            'semantic_precision': None, 'unnecessary_rejection_rate': None, 'precision_status': 'NOT_MEASURED_NO_INDEPENDENT_LABELS',
            'warning': 'grammar eligibility is neither extraction accuracy nor useful economic-claim recall'}
