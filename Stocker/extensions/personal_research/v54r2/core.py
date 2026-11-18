"""Compose available fundamentals; strict contract approval is never a core gate."""
from datetime import datetime, time, timezone, timedelta, date
from decimal import Decimal, localcontext
from copy import deepcopy
from research import sec_facts, sec_growth
from research.rights import as_utc
from research.sec_transport import now
from extensions import weighted_engine, weighted_features, weighted_critic
from . import VERSION, methods, sources


def cutoff(value=None):
    if value and len(value) == 10:
        # A date means the end of that calendar day in the user's Korean timezone.
        value = datetime.combine(date.fromisoformat(value), time.max, timezone(timedelta(hours=9))).isoformat()
    return as_utc(value or now()).isoformat().replace('+00:00', 'Z')


def metrics(facts, at):
    usable = [f for f in facts if f['role'] == 'current' and f['evidence_kind'] == 'OBSERVED']
    settings = weighted_engine.policy(); found = {}; derived = []
    data = [{**f, **f['period'], 'metric': f['name'], 'fact_id': f['id']} for f in usable]
    fields = {'operating_margin': ('operating_margin_percent', 'margin_percent', 'percent', 'operating_income/revenue*100'),
        'free_cash_flow': ('free_cash_flow', 'value', 'USD', 'operating_cash_flow-capital_expenditure'),
        'cash_coverage': ('cash_to_total_liabilities', 'cash_to_total_liabilities', 'ratio', 'cash/total_liabilities'),
        'liability_burden': ('liabilities_to_assets', 'total_liabilities_to_assets', 'ratio', 'total_liabilities/assets')}
    def put(key, value=None, raw=None, source_ids=None, reason=None):
        found[key] = weighted_features.factor(key, settings, value, raw=raw, source_ids=source_ids, reason=reason)
        if key not in fields or raw is None: return
        name, field, unit, formula = fields[key]; inputs = [f for f in usable if f['id'] in source_ids]
        derived.append({'id': 'metric:' + name, 'name': name, 'value': str(raw[field]), 'unit': unit, 'as_of': at,
            'evidence_kind': 'DERIVED', 'source_refs': sorted({r for f in inputs for r in f['source_refs']}),
            'input_ids': list(source_ids), 'formula': formula, 'period': inputs[0]['period'], 'scope': inputs[0]['scope'],
            'assumptions': [], 'limitations': ['기존 산식을 이용한 공시기간 계산이며 미래 성과 예측이 아닙니다.']})
    with localcontext() as context:
        context.prec = 60
        weighted_features.financial_factors({'as_of': at, 'financial_facts': data}, at, settings, put)
    for key in settings['features']:
        if key not in found: found[key] = weighted_features.factor(key, settings, reason='NO_PERSONAL_INPUT_ADAPTER')
    score = weighted_critic.score(found, settings)
    # Growth comparisons must come from the same filing, same scope/unit and exact year-shifted periods.
    for current in usable:
        prior = next((p for p in facts if p['role'] == 'prior_year_same_filing' and p['accession'] == current['accession'] and p['name'] == current['name'] and p['value'] is not None), None)
        if not prior or current['scope'] != prior['scope'] or current['unit'] != prior['unit']: continue
        try:
            expected = {k: date.fromisoformat(current['period'][k]).replace(year=int(current['period'][k][:4])-1).isoformat() for k in ['start', 'end']}
        except (ValueError, TypeError): continue
        if any(prior['period'][k] != expected[k] for k in expected) or prior['period']['basis'] != current['period']['basis']: continue
        change = sec_growth.change(current['value'], prior['value'])
        derived.append({'id': 'growth:' + current['id'], 'name': current['name'] + '_growth_percent', 'value': change['percent_change'],
            'unit': 'percent', 'as_of': at, 'evidence_kind': 'DERIVED' if change['percent_change'] is not None else 'UNKNOWN',
            'source_refs': sorted(set(current['source_refs'] + prior['source_refs'])), 'input_ids': [current['id'], prior['id']],
            'formula': '(current-prior)/prior*100; prior>0', 'period': current['period'], 'scope': current['scope'],
            'assumptions': [], 'limitations': [change['percent_status']], 'sign_transition': change['transition'],
            'absolute_change': change['absolute_change']})
    return derived, {'status': 'EXPERIMENTAL', 'value': None, 'released_score': None, 'factor_count': 23,
        'available_factors': [k for k, f in found.items() if f['value'] is not None], 'missing_factors': score['missing_factors'],
        'policy': 'SHADOW_UNVALIDATED_NO_REWEIGHTING', 'reason': '가격·시장·사건 요인 미연결; 전체 점수 보류', 'engine_total': score.get('experimental_total')}


def validate(value):
    if value['contract'] != VERSION: raise ValueError('PERSONAL_CONTRACT_MISMATCH')
    refs = value['source_references']; all_data = value['facts'] + value['derived_metrics'] + value['estimates']
    ids = {d['id'] for d in all_data}
    for d in all_data:
        kind = d['evidence_kind']
        if kind not in {'OBSERVED', 'DERIVED', 'ESTIMATED', 'UNKNOWN'}: raise ValueError('INVALID_EVIDENCE_KIND')
        if kind == 'UNKNOWN' and d['value'] is not None: raise ValueError('UNKNOWN_IS_NOT_ZERO')
        if kind != 'UNKNOWN' and d['value'] is None: raise ValueError('MISSING_VALUE_KIND')
        if kind in {'OBSERVED', 'DERIVED'} and not d['source_refs']: raise ValueError('SOURCE_REFERENCE_REQUIRED')
        if kind == 'ESTIMATED' and not d['assumptions']: raise ValueError('ESTIMATE_ASSUMPTIONS_REQUIRED')
        if any(r not in refs for r in d['source_refs']): raise ValueError('BROKEN_SOURCE_REFERENCE')
        if kind == 'DERIVED' and (not d.get('formula') or not d.get('input_ids') or any(i not in ids for i in d['input_ids'])):
            raise ValueError('DERIVED_INPUT_REQUIRED')
        if kind in {'OBSERVED', 'DERIVED'}:
            if isinstance(d['value'], bool) or not Decimal(str(d['value'])).is_finite(): raise ValueError('NONFINITE_DATUM')
    if value['scoring_status']['value'] is not None or value['exact_interest_impact'] is not None or value['price_target'] is not None:
        raise ValueError('PERSONAL_MVP_RELEASE_BOUNDARY')


def _fundamentals(ticker, as_of=None, *, snapshot=None, source_store=sources.DEFAULT_STORE, illustrative=False):
    ticker = sec_facts.symbol(ticker); at = cutoff(as_of); error = None
    try:
        snapshot = deepcopy(snapshot) if snapshot is not None else sources.resolve(ticker, at, source_store)
        sources.validate_snapshot(snapshot, ticker, at, illustrative=illustrative)
        facts, refs, errors = sources.extract(snapshot, at)
    except (ValueError, OSError, KeyError) as e:
        facts, refs, errors = [], {}, [{'code': type(e).__name__ + ':' + str(e).split(':')[0]}]
        error = errors[0]['code']; snapshot = None
    try:
        derived, score = metrics(facts, at)
    except (ValueError,KeyError,ArithmeticError) as e:
        derived=[]
        score={'status':'UNAVAILABLE','value':None,'released_score':None,'factor_count':23,'available_factors':[],
               'missing_factors':list(weighted_engine.read(weighted_engine.BASE/'weighted_policy.json')['features']),
               'policy':'OPTIONAL_ENGINE_FAILED_CLOSED','reason':type(e).__name__}
        errors.append({'code':'OPTIONAL_FINANCIAL_ENGINE:'+type(e).__name__})
    available = [f for f in facts if f['role'] == 'current' and f['value'] is not None]
    status = 'PARTIAL' if available else 'UNAVAILABLE'
    optional = {'strict_impact': {'status': 'UNAVAILABLE', 'reason': '계약·경제 사건의 정확한 영향은 미확인; 기본 재무 분석과 분리'},
        'research_v4': {'status': 'EXPERIMENTAL', 'reason': '기존 quality_gate_failed/SHADOW 유지; 이번 실행에서 추론하지 않음'},
        'valuation': {'status': 'UNAVAILABLE', 'reason': '기존 시장자료가 있을 수 있으나 현재가·권리·기준일을 연결하는 어댑터는 이번 MVP 범위 밖'},
        'news': {'status': 'UNAVAILABLE', 'reason': '기존 저장 뉴스의 종목 귀속·최신성 연결을 이번 MVP에 통합하지 않음; 기사 내용 추정 없음'},
        'contract_extraction_p15': {'status': 'UNAVAILABLE', 'reason': '계약 취득은 개인용 보고서의 선행 조건이 아님'}}
    estimates = []
    if available:
        references = sorted({r for f in available for r in f['source_refs']})
        for key, premise in [('bull', '향후 매출이 성장하고 영업현금흐름이 개선되며 투자 지출의 회수가 이루어지는 경우'),
                             ('base', '현재 확인할 수 있는 최근 재무 추세가 유지되는 경우'),
                             ('bear', '수요·가격 또는 비용 환경 악화로 수익성과 현금흐름이 하락하는 경우')]:
            estimates.append({'id': 'scenario:' + key, 'name': key, 'value': premise, 'unit': None, 'as_of': at,
                'evidence_kind': 'ESTIMATED', 'source_refs': references, 'assumptions': [premise],
                'limitations': ['정성적 조건 분기이며 발생확률·적정주가·금액 예측이 아닙니다.']})
    value = {'contract': VERSION, 'is_illustrative': illustrative, 'ticker': ticker, 'issuer': {'cik': snapshot['cik'], 'name': snapshot['name'],
        'industry': snapshot['sic_description'], 'snapshot_id': snapshot['snapshot_id']} if snapshot else None,
        'analysis_as_of': at, 'facts': facts, 'derived_metrics': derived, 'estimates': estimates,
        'unavailable_metrics': [f['id'] for f in facts + derived if f['value'] is None] +
            [n for n in ['operating_margin_percent','free_cash_flow','cash_to_total_liabilities','liabilities_to_assets'] if not any(d['name']==n for d in derived)] +
            ['valuation', 'ROE', 'ROIC', 'current_ratio', 'interest_coverage', 'net_debt', 'fcf_growth'],
        'source_references': refs, 'analysis_status': status, 'core_financial_status': 'AVAILABLE' if available else 'UNAVAILABLE',
        'scoring_status': score, 'optional_engine_statuses': optional, 'errors': errors, 'error': error,
        'exact_interest_impact': None, 'contract_verified': False, 'price_target': None,
        'limitations': ['수집된 공시 범위의 자동 분석입니다. 누락 자료를0으로 채우지 않습니다.',
            '총부채는 이자부 차입금이나 변동금리 원금이 아닙니다. 회계기간·귀속 범위를 지표마다 구분합니다.',
            '단일 원문의 자동 대조는 공식 계약 승인이나 미래 성과 검증과 다릅니다.'],
        'provenance': {'method_hashes': methods(), 'network_requests': 0, 'model_calls': 0, 'review_receipts_created': 0}}
    validate(value)
    return value, snapshot


def build(ticker,as_of=None,*,snapshot=None,source_store=sources.DEFAULT_STORE,illustrative=False,fetch_missing=False,market_snapshot=None,sec_acquisition=None,scenario_horizon=1,scenario_assumptions=None,peer_inputs=None,include_p4=True,ai=False,ai_dry_run=False,ai_record=None,ai_mode='roles',ai_context='primary'):
    from . import market_data,valuation,coverage,business,relative_valuation,historical_valuation,scenarios
    ticker=sec_facts.symbol(ticker);at=cutoff(as_of);selected=snapshot;sec_status=sec_acquisition
    if selected is None:
        for store in [source_store,market_data.SEC_STORE]:
            try:selected=sources.resolve(ticker,at,store);break
            except (ValueError,OSError,KeyError):continue
    if include_p4 and peer_inputs is None:
        peer_inputs=relative_valuation.acquire(ticker,at,fetch_missing,as_of is None)
        if as_of is None:at=cutoff()
    expected=selected['cik'] if selected and selected.get('entity_type')=='operating' else None
    market=market_data.get(ticker,at,expected_cik=expected,fetch_missing=fetch_missing,illustrative=illustrative,
                           saved=market_snapshot,current_request=as_of is None)
    if selected is None and fetch_missing and not illustrative and market.get('security_type')!='ETF' and ticker not in market_inputs_funds():
        try:sec_status=market_data.fetch_sec(ticker)
        except (ValueError,OSError,KeyError) as error:sec_status={'status':'SEC_UNAVAILABLE','reason':type(error).__name__,'network_requests':0}
        try:selected=sources.resolve(ticker,cutoff() if as_of is None else at,market_data.SEC_STORE)
        except (ValueError,OSError,KeyError):pass
    if as_of is None and fetch_missing:
        at=cutoff();market=market_data.get(ticker,at,expected_cik=selected['cik'] if selected else None,saved=market,illustrative=illustrative)
    value,selected=_fundamentals(ticker,at,snapshot=selected,source_store=source_store,illustrative=illustrative)
    # SEC may have become available only after the initial market lookup.
    # Recheck identity for explicit historical/date cutoffs as well as current requests.
    if selected and market.get('resolved_ticker') and market.get('security_type')=='EQUITY' and market.get('cik')!=selected['cik']:
        market={**market_data.unavailable(ticker,'SYMBOL_CIK_MISMATCH'),'network_requests':market.get('network_requests',0)}
    try:finance,refs,issues,components=coverage.financials(selected,at)
    except (ValueError,OSError,KeyError,ArithmeticError) as error:finance,refs,components,issues={},{},[],['VALUATION_SOURCE_UNAVAILABLE:'+type(error).__name__]
    if market.get('security_type')=='ETF':finance={};refs={};issues=['ETF_COMPANY_VALUATION_NOT_APPLICABLE']
    value['source_references'].update(refs)
    for n,s in enumerate(market.get('source_reference') or []):
        value['source_references']['market:'+str(n)]={**s,'kind':'market','available_at':market.get('source_available_at'),'trading_date':market.get('trading_date')}
        ref=value['source_references']['market:'+str(n)]
        if '/v3/reference/tickers/' in s['url'] and market.get('provider_reported_market_cap') is not None:
            value['source_references']['market:market_cap']={**ref,'locator':{'json_path':'results.market_cap'}}
        if 'adjusted=false' in s['url'] and market.get('raw_close') is not None:
            value['source_references']['market:raw_close']={**ref,'locator':market['price_locator']}
    valuations=coverage.calculate(market,finance,at)
    supported=sum(v['calculation_status']=='AVAILABLE' for v in valuations)
    value.update(market_snapshot=market,valuation_financials=finance,valuation_metrics=valuations,
        market_data_status=market['data_status'],valuation_status='PARTIAL' if supported else 'UNAVAILABLE',
        price_date=market.get('trading_date'),source_provider=market.get('source_provider'),
        missing_inputs=sorted({m for v in valuations for m in v['missing_inputs']}),valuation_limitations=issues,
        sec_acquisition=sec_status)
    if market.get('raw_close') is not None or market.get('adjusted_close') is not None:
        value['analysis_status']='PARTIAL'
        if value['issuer'] is None:value['issuer']={'cik':market.get('cik'),'name':market.get('name'),'industry':None,'snapshot_id':None,'identity_source':'market_provider'}
    value['optional_engine_statuses']['valuation']={'status':value['valuation_status'],'reason':'계산 가능한 항목만 산출; TTM·귀속·주식 기준·미확인 입력은 항목별 보류'}
    value['provenance']['network_requests']=market.get('network_requests',0)+(sec_status or {}).get('network_requests',0)
    value['valuation_coverage']=coverage.audit(valuations,finance,components,issues)
    value['debt_components']=components
    try:business.enrich(value,selected,at)
    except (ValueError,OSError,KeyError,ArithmeticError) as error:
        business.enrich(value,None,at);value['analysis_limitations'].append('BUSINESS_SOURCE_UNAVAILABLE:'+type(error).__name__)
    business.validate(value)
    if include_p4:
        relative_valuation.enrich(value,peer_inputs or [])
        value['historical_valuation']=historical_valuation.inspect(value)
        scenarios.enrich(value,scenario_horizon,scenario_assumptions)
        value['scenario_horizon']=scenario_horizon
        value['scenario_override_inputs']=scenario_assumptions
        value['provenance']['network_requests']+=sum(p['value']['provenance']['network_requests'] for p in peer_inputs or [])
        from . import memo
        memo.enrich(value,selected,ai=ai,dry_run=ai_dry_run,saved=ai_record,ai_mode=ai_mode,ai_context=ai_context)
    validate(value)
    return value,selected


def market_inputs_funds():
    from extensions import market_inputs
    return market_inputs.policy()['funds']
