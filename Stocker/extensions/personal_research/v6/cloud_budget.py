"""Decimal usage accounting and crash-conservative, process-locked request budget."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import uuid
from research.local_files import atomic_json, exclusive_lock
from research.sec_report import ROOT
from research.sec_transport import now


def money(value):
    try:
        if isinstance(value, bool): raise ValueError('INVALID_API_BUDGET')
        result = Decimal(str(value))
        if not result.is_finite() or result < 0: raise ValueError('INVALID_API_BUDGET')
        return result
    except (InvalidOperation, TypeError):
        raise ValueError('INVALID_API_BUDGET') from None


def usage(raw):
    """Output includes reasoning tokens; never add reasoning a second time."""
    if raw is None: return None
    def count(value):
        if type(value) is not int or value < 0: raise ValueError('INVALID_API_USAGE')
        return value
    i=count(raw['input_tokens']); o=count(raw['output_tokens'])
    details=raw.get('input_tokens_details') or {}; out=raw.get('output_tokens_details') or {}
    c=count(details.get('cached_tokens',0)); w=count(details.get('cache_write_tokens',0))
    r=out.get('reasoning_tokens'); r=count(r) if r is not None else None
    if c+w > i or (r is not None and r>o) or count(raw['total_tokens']) != i+o:
        raise ValueError('INCONSISTENT_API_USAGE')
    return {'input_tokens':i,'cached_input_tokens':c,'cache_write_tokens':w,'output_tokens':o,
            'reasoning_tokens':r,'total_tokens':i+o}


def cost(measured, profile):
    if measured is None: return None
    p={k:money(v) for k,v in profile['price_per_million'].items()}
    return str(((measured['input_tokens']-measured['cached_input_tokens']-measured['cache_write_tokens'])*p['input']
                + measured['cached_input_tokens']*p['cached_input']+measured['cache_write_tokens']*p['cache_write']
                + measured['output_tokens']*p['output'])/Decimal(1000000))


def estimate(request, profile):
    # Offline approximation, not a tokenizer measurement or a billing guarantee.
    # UTF-8 bytes plus schema/serialization reserve deliberately overestimates typical BPE text.
    tokens=len(json.dumps(request,ensure_ascii=False,sort_keys=True).encode('utf-8'))+1024
    if tokens > profile['maximum_input_estimate']: raise ValueError('INPUT_BUDGET_EXCEEDED')
    output=request['max_output_tokens']; p=profile['price_per_million']
    # Reserve the more expensive cache-write input rate; no discounts assumed.
    reserved=(tokens*max(money(p['input']),money(p['cache_write']))+output*money(p['output']))/1000000
    return {'input_tokens_estimate':tokens,'method':'UTF8_BYTES_PLUS_1024_NOT_EXACT_TOKENIZER',
            'output_token_cap_including_reasoning':output,'reserved_usd':str(reserved),
            'billing_guarantee':False,'price_checked_date':profile['price_checked_date']}


class Budget:
    def __init__(self,path=None,max_usd='2.00',max_requests=3):
        self.path=Path(path or ROOT/'local_data/p6_openai_budget/initial-v1.json').resolve()
        if not self.path.is_relative_to((ROOT/'local_data/p6_openai_budget').resolve()):
            raise ValueError('PRIVATE_API_LEDGER_REQUIRED')
        self.maximum=money(max_usd)
        if type(max_requests) is not int or not 1<=max_requests<=3: raise ValueError('API_REQUEST_CAP_MUST_BE_1_TO_3')
        self.max_requests=max_requests

    def reserve(self,amount,ticker,request_hash):
        amount=money(amount)
        with exclusive_lock(self.path.with_suffix('.lock')):
            state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
                'version':'p6-api-budget-v1','max_usd':str(self.maximum),'max_requests':self.max_requests,'attempts':[]}
            if money(state['max_usd'])!=self.maximum or state['max_requests']!=self.max_requests:
                raise ValueError('EXISTING_BUDGET_LIMITS_DIFFER_NEW_LEDGER_REQUIRED')
            spent=sum((money(r['accounted_usd']) for r in state['attempts']),Decimal(0))
            if len(state['attempts'])>=self.max_requests: raise ValueError('API_REQUEST_BUDGET_EXHAUSTED')
            if spent+amount>self.maximum: raise ValueError('INSUFFICIENT_API_BUDGET')
            receipt={'id':uuid.uuid4().hex,'ticker':ticker,'request_sha256':request_hash,'started_at':now(),
                     'reserved_usd':str(amount),'accounted_usd':str(amount),'status':'RESERVED_OUTCOME_UNKNOWN'}
            state['attempts'].append(receipt);atomic_json(self.path,state,replace=self.path.exists())
            return receipt['id']

    def settle(self,identifier,actual,status):
        with exclusive_lock(self.path.with_suffix('.lock')):
            state=json.loads(self.path.read_text(encoding='utf-8'));row=next(r for r in state['attempts'] if r['id']==identifier)
            if row['status']!='RESERVED_OUTCOME_UNKNOWN': raise ValueError('BUDGET_ALREADY_SETTLED')
            # Unknown outcome, missing usage, and ambiguous network failure retain the full reserve.
            row.update(status=status,finished_at=now(),actual_usage_estimated_usd=str(money(actual)) if actual is not None else None)
            if actual is not None: row['accounted_usd']=str(money(actual))
            atomic_json(self.path,state,replace=True)
            return deepcopy(row)
