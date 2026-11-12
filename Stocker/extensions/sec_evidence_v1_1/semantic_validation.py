"""Bounded source grammar -> typed observations -> cross-field relationships.

This is deliberately not a general SEC language interpreter. Unrecognized or
ambiguous source constructions require human review; reviewer booleans/types
cannot supply a missing observation. All proof spans refer to original bytes'
UTF-8 codepoint mapping and are independently re-parsed on each validation.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, date
from decimal import Decimal
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
import re
from research.validation import digest
from extensions.sec_evidence_v1 import parsing

ID = r'[A-Za-z0-9_.:-]{1,160}'
NUM = r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
SIGNED = r'-?' + NUM
ISO = r'\d{4}-\d{2}-\d{2}'
LOAN = re.compile(
    rf'Loan (?P<instrument>{ID}) in (?P<currency>[A-Z]{{3}}) has floating-rate principal '
    rf'(?P<principal_currency>[A-Z]{{3}}) (?P<principal>{NUM}) indexed to (?P<index>{ID}); '
    rf'repricing fraction (?P<fraction>{NUM}); policy reference (?P<reference>{ID}) '
    rf'maps one-for-one to (?P<mapped_index>{ID}) with coefficient (?P<mapping>{NUM}); '
    rf'supported delta (?P<minimum>{SIGNED}) to (?P<maximum>{SIGNED}) bp; '
    r'(?P<day_count>ACT/365F-v1|ACT/360-v1); constant principal; interest is expensed pretax\.')
RESET = re.compile(
    rf'Loan (?P<instrument>{ID}) \((?P<currency>[A-Z]{{3}}), (?P<index>{ID})\): '
    r'The applicable interest rate resets on (?P<date>[A-Za-z]+ [0-9]{1,2}(?:, [0-9]{4}| of each year))\.')
PERIOD = re.compile(rf'Loan (?P<instrument>{ID}) is effective from (?P<start>{ISO}) through (?P<end>{ISO})\.')
NONE = re.compile(rf'Loan (?P<instrument>{ID}) has no interest rate hedges during (?P<start>{ISO}) through (?P<end>{ISO})\.')
HEDGE = re.compile(
    rf'Swap (?P<hedge>{ID}) hedges Loan (?P<instrument>{ID}): receive-floating (?P<index>{ID}) and pay-fixed; '
    rf'(?P<currency>[A-Z]{{3}}); notional (?P<notional_currency>[A-Z]{{3}}) (?P<notional>{NUM}); '
    rf'hedged principal (?P<hedged_currency>[A-Z]{{3}}) (?P<hedged_principal>{NUM}) '
    rf'of repricing principal (?P<principal_currency>[A-Z]{{3}}) (?P<principal>{NUM}); '
    rf'coverage fraction (?P<fraction>{NUM}); fixed offset coefficient (?P<offset>{NUM}); '
    rf'effective from (?P<start>{ISO}) through (?P<end>{ISO}); no basis risk, caps, floors, or optionality\.')
GRAMMARS = {'loan': LOAN, 'reset': RESET, 'period': PERIOD, 'no_hedge': NONE, 'hedge': HEDGE}
TYPES = ['REPRICING_PRINCIPAL', 'INTEREST_RATE_RESET_DATE', 'CONTRACT_EFFECTIVE_PERIOD',
         'HEDGE_RELATIONSHIP', 'HEDGE_COVERAGE_FRACTION']


@dataclass(frozen=True)
class Observation:
    kind: str
    source_id: str
    start: int
    end: int
    attributes: dict


class Paragraphs(HTMLParser):
    """Only explicit paragraph boundaries; never associate nearby random words."""
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw; self.lines = [0]; self.opened = []; self.bounds = []
        for n, char in enumerate(raw):
            if char == '\n': self.lines.append(n + 1)

    def at(self):
        row, col = self.getpos()
        return self.lines[row - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == 'p': self.opened.append(self.at())

    def handle_endtag(self, tag):
        if tag == 'p' and self.opened:
            self.bounds.append((self.opened.pop(), self.at()))


def observations(source, cik):
    path = Path(source['raw_path'])
    if digest(path) != source['original_sha256']:
        raise ValueError('SEMANTIC_SOURCE_HASH_CHANGED')
    raw = path.read_bytes()
    parsed = parsing.parse(raw, source['original_sha256'], cik)
    if parsed['derived_text_sha256'] != source['derived_text_sha256']:
        raise ValueError('SEMANTIC_DERIVED_MAPPING_CHANGED')
    paragraphs = Paragraphs(raw.decode('utf-8')); paragraphs.feed(raw.decode('utf-8')); paragraphs.close()
    result = []
    for start, end in paragraphs.bounds:
        pieces = [s for s in parsed['segments'] if start <= s['raw_start'] < s['raw_end'] <= end]
        if not pieces: continue
        text = ' '.join(s['text'] for s in pieces)
        if len(text) > 4000: continue
        for kind, grammar in GRAMMARS.items():
            match = grammar.fullmatch(text)
            if match:
                result.append(Observation(kind, source['source_key'], pieces[0]['raw_start'],
                                          pieces[-1]['raw_end'], match.groupdict()))
    return result


def check(manifest, result):
    """Structural check must run first. No input semantic annotations are trusted."""
    terms = manifest['terms']; x = terms['exposure']; instrument = x['instrument_id']
    fields = {name: next(f for f in terms['facts'] if f['fact_id'] == fid)
              for name, fid in x['bindings'].items()}
    principal = fields['principal']; source = next(s for s in result['sources'] if s['source_key'] == principal['source_id'])
    obs = [o for o in observations(source, x['cik']) if o.attributes['instrument'] == instrument]
    issues = []; relationships = []; period = None; hedge_period = None; annual = False

    def fail(code, detail): issues.append({'code': code, 'detail': detail})

    def unique(kind):
        found = [o for o in obs if o.kind == kind]
        if len(found) != 1:
            fail('SEMANTIC_' + kind.upper() + '_UNRESOLVED', 'Exactly one explicit source relationship required for ' + instrument)
            return None
        return found[0]

    def witness(name, observation):
        w = terms['witnesses'][name]; f = fields[name]
        if f['source_id'] != observation.source_id or not (w['raw_start'] <= observation.start < observation.end <= w['raw_end']):
            fail('SEMANTIC_WITNESS_RELATIONSHIP_MISMATCH', name)

    def equal(actual, expected, code, decimal=False):
        try: okay = Decimal(str(actual)) == Decimal(str(expected)) if decimal else actual == expected
        except (ValueError, ArithmeticError): okay = False
        if not okay: fail(code, str(actual) + ' != source-supported ' + str(expected))

    loan = unique('loan'); reset = unique('reset'); effective = unique('period')
    if loan:
        a = loan.attributes
        equal(a['currency'], x['currency'], 'SEMANTIC_LOAN_CURRENCY_MISMATCH')
        equal(a['principal_currency'], x['currency'], 'SEMANTIC_PRINCIPAL_CURRENCY_MISMATCH')
        equal(a['index'], a['mapped_index'], 'SEMANTIC_RATE_INDEX_MISMATCH')
        equal(principal['value'], a['principal'], 'SEMANTIC_PRINCIPAL_RELATIONSHIP_MISMATCH', True)
        if not (loan.start <= principal['start_offset'] < principal['end_offset'] <= loan.end):
            fail('SEMANTIC_PRINCIPAL_OUTSIDE_INSTRUMENT_RECORD', 'The exact native numeric span must be inside the explicit loan relationship.')
        for name, value in [('interest_bearing', True), ('repricing_fraction', a['fraction']), ('rate_mapping', a['mapping']),
                            ('day_count', a['day_count']), ('principal_schedule', 'CONSTANT_CONTRACTUAL'),
                            ('accounting_treatment', 'EXPENSED_PRETAX')]:
            witness(name, loan)
            equal(fields[name]['value'], value, 'SEMANTIC_COMPONENT_VALUE_MISMATCH', name in {'repricing_fraction','rate_mapping'})
        link = fields['rate_mapping']['rate_link']
        for key, value in [('benchmark', a['index']), ('rate_currency', a['currency']), ('event_reference', a['reference']),
                           ('minimum_delta_bp', a['minimum']), ('maximum_delta_bp', a['maximum'])]:
            equal(link[key], value, 'SEMANTIC_RATE_MAPPING_MISMATCH', key.endswith('_bp'))
        equal(a['mapping'], '1', 'SEMANTIC_NONLINEAR_MAPPING_UNSUPPORTED', True)
        if link['event_variable'] != 'policy_rate': fail('SEMANTIC_EVENT_VARIABLE_UNSUPPORTED',link['event_variable'])
        relationships.append({'semantic_type':'REPRICING_PRINCIPAL','source':asdict(loan)})
    if reset:
        witness('reset_date', reset); a = reset.attributes
        equal(a['currency'], x['currency'], 'SEMANTIC_RESET_CURRENCY_MISMATCH')
        if loan: equal(a['index'], loan.attributes['index'], 'SEMANTIC_RESET_INDEX_MISMATCH')
        value = fields['reset_date']['value']
        try:
            if a['date'].endswith(' of each year'):
                annual = True
                observed = datetime.strptime(a['date'].replace(' of each year', ', 2000'), '%B %d, %Y').date()
                claimed = date.fromisoformat(value)
                equal((claimed.month, claimed.day), (observed.month, observed.day), 'SEMANTIC_RESET_DATE_MISMATCH')
            else:
                observed = datetime.strptime(a['date'], '%B %d, %Y').date().isoformat()
                equal(value, observed, 'SEMANTIC_RESET_DATE_MISMATCH')
        except ValueError: fail('SEMANTIC_RESET_DATE_UNSUPPORTED', a['date'])
        relationships.append({'semantic_type':'INTEREST_RATE_RESET_DATE','source':asdict(reset)})
    if effective:
        a = effective.attributes
        try:
            if date.fromisoformat(a['start']) >= date.fromisoformat(a['end']): raise ValueError()
            period = {'start':a['start'],'end':a['end']}
        except ValueError: fail('SEMANTIC_CONTRACT_PERIOD_INVALID', str(a))
        relationships.append({'semantic_type':'CONTRACT_EFFECTIVE_PERIOD','source':asdict(effective)})
    h = Decimal(fields['hedged_fraction']['value'])
    hedge = unique('no_hedge' if h == 0 else 'hedge')
    if hedge:
        a = hedge.attributes
        witness('hedged_fraction', hedge); witness('hedge_terms', hedge)
        try:
            if date.fromisoformat(a['start']) >= date.fromisoformat(a['end']): raise ValueError()
            hedge_period = {'start':a['start'],'end':a['end']}
        except ValueError: fail('SEMANTIC_HEDGE_PERIOD_INVALID',str(a))
        if h == 0:
            equal(fields['hedge_terms']['value'], 'NONE', 'SEMANTIC_HEDGE_TERMS_MISMATCH')
            if any(o.kind == 'hedge' for o in obs): fail('SEMANTIC_HEDGE_CONFLICT','Explicit no-hedge and hedge statements conflict.')
        else:
            equal(fields['hedge_terms']['value'], 'FIXED_OFFSET', 'SEMANTIC_HEDGE_TERMS_MISMATCH')
            if a['hedge'] == instrument: fail('SEMANTIC_HEDGE_INSTRUMENT_AMBIGUOUS','Hedge and debt instruments must be separately identified.')
            for key in ['currency','notional_currency','hedged_currency','principal_currency']:
                equal(a[key], x['currency'], 'SEMANTIC_HEDGE_CURRENCY_MISMATCH')
            if loan: equal(a['index'],loan.attributes['index'],'SEMANTIC_HEDGE_INDEX_MISMATCH')
            equal(a['principal'], principal['value'], 'SEMANTIC_HEDGE_DEBT_RELATIONSHIP_MISMATCH', True)
            equal(a['notional'],a['hedged_principal'],'SEMANTIC_HEDGE_NOTIONAL_PRINCIPAL_MISMATCH',True)
            equal(a['fraction'],h,'SEMANTIC_HEDGE_FRACTION_MISMATCH',True)
            equal(a['offset'],'1','SEMANTIC_HEDGE_DIRECTION_OR_OFFSET_UNSUPPORTED',True)
            if Decimal(a['principal']) <= 0 or not 0 <= Decimal(a['hedged_principal']) <= Decimal(a['principal']):
                fail('SEMANTIC_HEDGE_AMOUNT_INVALID','Explicit covered principal must be within the named repricing principal.')
            elif Fraction(a['hedged_principal']) != Fraction(a['principal']) * Fraction(h):
                fail('SEMANTIC_HEDGE_FRACTION_NOT_SUPPORTED','Coverage fraction must equal explicitly linked covered principal / repricing principal.')
            if any(o.kind == 'no_hedge' for o in obs): fail('SEMANTIC_HEDGE_CONFLICT','Contradictory hedge evidence.')
        relationships.append({'semantic_type':'HEDGE_RELATIONSHIP','source':asdict(hedge)})
        relationships.append({'semantic_type':'HEDGE_COVERAGE_FRACTION','source':asdict(hedge)})
    for name, f in fields.items():
        expected = hedge_period if name in {'hedged_fraction','hedge_terms'} else period
        if expected:
            equal((f['valid_from'],f['valid_to']), (expected['start'],expected['end']), 'SEMANTIC_VALIDITY_CLAIM_MISMATCH')
    if period and reset and not period['start'] <= fields['reset_date']['value'] < period['end']:
        fail('SEMANTIC_RESET_OUTSIDE_CONTRACT','Reset occurrence is outside the source-backed contract interval.')
    return {'status':'HUMAN_REVIEW_REQUIRED' if issues else 'SEMANTIC_RULE_VALIDATED','supported':not issues,
            'issues':issues,'relationships':relationships,'contract_period':period,'hedge_period':hedge_period,
            'annual_reset':annual,'reset_date':fields['reset_date']['value']}
