"""Korean Markdown and immutable personal-research records."""
from decimal import Decimal
from html import escape
from pathlib import Path
import uuid
from research.local_files import atomic_json
from research.sec_refresh import read
from research.sec_report import ROOT
from research.validation import digest
from . import core, methods

LABELS = {'revenue': '매출', 'operating_income': '영업손익', 'net_income': '순손익', 'cash': '현금 및 현금성자산',
    'assets': '총자산', 'liabilities': '총부채(이자부 차입금과 다름)', 'operating_cash_flow': '영업현금흐름',
    'capital_expenditure': '현금 설비취득 지출', 'diluted_eps': '희석 EPS',
    'operating_margin_percent': '영업이익률', 'free_cash_flow': 'FCF(영업현금흐름−현금 설비취득)',
    'cash_to_total_liabilities': '현금/총부채', 'liabilities_to_assets': '총부채/총자산'}
BASES = {'quarter': '분기', 'annual': '연간', 'cumulative_ytd': '연초 누적', 'instant': '기말 잔액'}


def safe(text):
    return escape(str(text)).replace('|', '\\|').replace('\n', ' ')


def number(d):
    if d['value'] is None: return '미확인'
    n = Decimal(d['value'])
    return f'{n / Decimal(1000000):,.2f} 백만 USD' if d['unit'] == 'USD' else f'{n:,.4f} {d["unit"]}'


def valuation_lines(value):
    m=value.get('market_snapshot',{});lines=[]
    if m.get('raw_close') is not None or m.get('adjusted_close') is not None:
        lines += [f'**Market Snapshot** · {safe(m["resolved_ticker"])} / {m["security_type"]} / {m["exchange"]} / {m["currency"]}',
            f'- 거래일: {m["trading_date"]}, 정규장 종료: {m["market_session_end"]}. 일별 종가이며 실시간 시세가 아닙니다.',
            f'- 실제 종가(raw): {m.get("raw_close") or "미확보"} USD · 분할 조정 종가: {m.get("adjusted_close") or "미확보"} USD. 배당조정 가격과 구분합니다.',
            f'- 거래량: {m["volume"]} · 제공처: Massive · 신선도: {m["data_status"]}',
            f'- 취득: {m["retrieved_at"]} · 최초 이용: {m["source_available_at"]} · 증권 ID: {m["security_id"]}',
            f'- 제공처 시가총액: {m.get("provider_reported_market_cap") or "미확인"} USD (reference-date {m.get("market_cap_reference_date")}). 가중평균 EPS주식 수로 재계산하지 않았습니다.']
    else:lines += ['시장 자료 상태: '+safe(m.get('data_status','UNAVAILABLE'))+'. 가격을 임의 생성하지 않았습니다.']
    if m.get('acquisition_error'):lines += ['추가 취득 실패: '+safe(m['acquisition_error'])+'. 기존 가격은 오래된 자료로 표시하며 현재 가치평가에 사용하지 않았습니다.']
    if m.get('security_type')=='ETF':lines += ['ETF 가격 중심 보고서입니다. 기업용 PER·EV/EBITDA를 적용하지 않으며 NAV·보유자산 정보는 미연결입니다.']
    lines += ['','| Metric | Value | Basis | Status |','|---|---:|---|---|']
    for d in value.get('valuation_metrics',[]):
        n=f'{Decimal(d["value"]):,.4f}' if d['value'] is not None else 'N/M' if d['calculation_status']=='NOT_MEANINGFUL' else '미확인'
        lines.append(f'| {d["metric_name"]} | {n} | {safe(d["formula"])} | {d["calculation_status"]} |')
        if d['fiscal_period']:lines.append(f'| 재무기간 | {d["fiscal_period"]["start"] or ""} ~ {d["fiscal_period"]["end"]} | {d["fiscal_period"]["basis"]} | |')
    lines += ['','P/E는 선택된 경로의 주가/이익 또는 시가총액/지배기업 귀속 이익 배수입니다. P/S는 매출, P/B는 보통주 자기자본 대비 가격이며 FCF Yield는 현금창출 규모의 비율입니다. 성장·기간·회계 범위와 함께 읽어야 합니다. 과거·동종기업 비교가 없어 배수만으로 싸거나 비싸다고 판단하지 않습니다.',
              '총부채를 이자부 차입금으로 대체하지 않았습니다. EBITDA는 지원될 때만 영업손익+D&A의 단순 정의이며 기업 발표 조정 EBITDA와 다릅니다.']
    if value.get('missing_inputs'):lines.append('미확인 입력: '+safe(', '.join(value['missing_inputs'])))
    return lines


def render(v):
    who = v['issuer']; title = who['name'] if who else v['ticker']; refs = {k: 'S' + str(n) for n, k in enumerate(v['source_references'], 1)}
    cite = lambda d: ', '.join(refs[k] for k in d['source_refs']) or '—'
    current = [f for f in v['facts'] if f['role'] == 'current']
    valid = [f for f in current if f['value'] is not None]
    lines = [f'# {safe(title)} ({v["ticker"]}) 개인용 리서치', '', f'분석 기준: {v["analysis_as_of"]} · 전체 상태: {v["analysis_status"]}', '',
        '## 1. Executive Summary', '']
    if v['is_illustrative']: lines.insert(2,'ILLUSTRATIVE — NOT LIVE MARKET DATA')
    if not valid:
        lines += [('시장 증권은 식별했으나 핵심 SEC 재무자료는 미확보입니다. 가격 중심의 부분 보고서를 제공합니다.' if v.get('market_snapshot',{}).get('resolved_ticker') else '기준시각 전에 이용 가능한 핵심 재무자료 또는 종목 식별을 확보하지 못했습니다. 수치와 투자 논거를 생성하지 않았습니다.'),
                  '자료 상태: ' + safe(v['error'] or '원문 재무자료 미확보')]
    else:
        lines += [f'등록 기업: {safe(who["name"])} · CIK {who["cik"]} · 공시 산업 분류: {safe(who["industry"])}.',
                  '재무 현황은 자동 원문 대조로 분석했습니다. 가격·뉴스·정밀 계약 영향의 부족은 아래 해당 부분만 제한합니다.']
        latest = max(f['period']['end'] for f in valid)
        for name in ['revenue', 'operating_income', 'net_income']:
            f = next((f for f in valid if f['name'] == name and f['period']['end'] == latest), None)
            if f: lines.append(f'- {BASES[f["period"]["basis"]]} {LABELS[name]}: {number(f)} ({f["period"]["end"]}, {f["scope"]}; {cite(f)}).')
    lines += ['', '## 2. Financial Snapshot', '', '금액은 백만 USD, EPS는 USD/shares입니다. 관측값은 공시 사실이며 미래 전망이 아닙니다.', '',
              '| 공시 | 항목 | 관측값 | 회계기간 | 귀속 범위 | 근거 |', '|---|---|---:|---|---|---|']
    for f in current:
        p = f['period']; period = (p['start'] + ' ~ ' if p['start'] else '') + p['end'] + ' / ' + BASES[p['basis']]
        lines.append(f'| {f["form"]} | {LABELS[f["name"]]} | {number(f)} | {period} | {f["scope"] or "미확인"} | {cite(f)} |')
    lines += ['', '## 3. Growth & Profitability', '', '| 지표 | 결과 | 기간 | 근거 |', '|---|---:|---|---|']
    for d in v['derived_metrics']:
        if 'growth' not in d['name'] and d['name'] != 'operating_margin_percent': continue
        label = LABELS.get(d['name'], LABELS.get(d['name'].replace('_growth_percent', ''), d['name']) + ' 전년 동기 성장률')
        lines.append(f'| {label} | {number(d)} | {d["period"]["start"] or ""} ~ {d["period"]["end"]} | {cite(d)} |')
        if d['value'] is None and d.get('sign_transition'):
            lines.append(f'| 성장률 보류 사유 | 전년 값이0 이하: {d["sign_transition"]} | 절대 변화 {d["absolute_change"]} | {cite(d)} |')
    lines += ['', '## 4. Financial Health', '', '| 지표 | 결과 | 기준일/기간 | 근거 |', '|---|---:|---|---|']
    for d in v['derived_metrics']:
        if 'growth' in d['name'] or d['name'] == 'operating_margin_percent': continue
        lines.append(f'| {LABELS.get(d["name"],d["name"])} | {number(d)} | {d["period"]["start"] or ""} ~ {d["period"]["end"]} | {cite(d)} |')
    lines += ['','ROE·ROIC·유동비율·이자보상배율·순차입금은 필요한 분모/차입금 범위가 연결되지 않아 보류합니다. 총부채를 차입금으로 대체하지 않습니다.',
              '', '## 5. Valuation', '', *valuation_lines(v),
              '', '## 6. Events & Industry Context', '', v['optional_engine_statuses']['news']['reason'] + '.',
              'ResearchEngine는 기존 SHADOW 품질 상태를 유지합니다. 이번 실행에서 모델을 호출하거나 실제 사건의 손익·주가 영향을 확정하지 않았습니다.',
              '', '## 7. Company-Specific Risks', '']
    risks = []
    for name in ['operating_income', 'net_income']:
        observations = [f for f in valid if f['name'] == name]
        if observations:
            f = max(observations, key=lambda f: f['period']['end'])
            risks.append(f'{f["period"]["end"]} {LABELS[name]} {number(f)}: ' + ('손실의 지속 여부를 확인해야 합니다.' if Decimal(f['value']) < 0 else '현재 양의 손익이 미래에도 유지되는지는 별도 확인이 필요합니다.') + f' ({cite(f)})')
    capex = [f for f in valid if f['name'] == 'capital_expenditure']
    if capex:
        f = max(capex, key=lambda f: f['period']['end']); risks.append(f'현금 설비취득 지출 {number(f)}의 향후 회수와 현금흐름을 확인해야 합니다. ({cite(f)})')
    if any(f.get('scope') == 'consolidated_sales_excluding_equity_affiliate_and_other_income' for f in valid):
        risks.append('매출은 지분법 이익·기타이익을 제외한 범위입니다. 총수익과 혼합한 이익률을 계산하지 않았습니다.')
    lines += ['- ' + x for x in risks] or ['- 종목별 재무 위험을 판단할 확인 자료가 부족합니다.']
    lines += ['- 계약별 금리 노출과 헤지 비율은 미확인입니다. 시설 한도·총부채·swap notional을 실제 변동금리 원금으로 해석하지 않습니다.',
              '', '## 8. Bull / Base / Bear', '']
    lines += [f'- {d["name"].upper()} [가정]: {d["value"]}. 정량 목표·확률 없음.' for d in v['estimates']] or ['자료 부족으로 시나리오를 작성하지 않았습니다.']
    growth = [d for d in v['derived_metrics'] if d['name'] == 'revenue_growth_percent' and d['value'] is not None]
    thesis = ('가격 대비 일부 재무 배수는 계산했지만 비교 평가·산업 맥락이 부족해 전체 투자 매력도 판단은 유보합니다.' if v.get('valuation_status')=='PARTIAL' else '정렬된 밸류에이션 입력과 산업 맥락이 부족해 전체 투자 매력도 판단은 유보합니다.')
    if growth:
        d = max(growth, key=lambda d: d['period']['end'])
        thesis = f'{d["period"]["end"]} 매출 성장률 {number(d)}은 ' + ('성장 논거' if Decimal(d['value']) > 0 else '수요·매출 추이의 점검 요인') + f'입니다. ({cite(d)}) ' + thesis
    lines += ['', '## 9. Investment Attractiveness', '', thesis,
              f'기존23요인 중 이번 자료로 지원되는 요인은 {len(v["scoring_status"]["available_factors"])}개입니다. 전체 점수는 미산출이며 가중치를 재배분하지 않았습니다. 기존 모델은 EXPERIMENTAL/SHADOW입니다.',
              '', '## 10. Sources & Uncertainty', '',
              'OBSERVED=원문 관측, DERIVED=출처 입력의 결정론 계산, ESTIMATED=명시적 가정, UNKNOWN=미확인입니다. 미확인은0이 아닙니다.']
    for ref, key in refs.items():
        s = v['source_references'][ref]
        if s.get('kind')=='market':
            lines.append(f'- {key}: [Massive EOD/reference]({s["url"]}), 거래일 {s["trading_date"]}, 취득 {s["retrieved_at"]}, 최초 이용 {s["available_at"]}; SHA-256 `{s["raw_sha256"]}`.');continue
        loc = s['locator']
        lines.append(f'- {key}: [{s["accession"]}]({s["url"]}), 제출 {s["filed_date"]}, 최초 저장 {s["available_at"]}; {safe(loc.get("tag"))}, context={safe(loc.get("context_ref"))}, row={loc.get("row_number")}, SHA-256 `{s["raw_sha256"]}`.')
    lines += ['- ' + safe(x) for x in v['limitations']]
    for f in current:
        if f['value'] is None: lines.append(f'- {f["form"]} {f["period"]["end"]} {LABELS[f["name"]]}: '+safe(f['limitations'][0]))
    for error in v['errors']: lines.append('- 처리 제한: ' + safe(error['code']))
    return '\n'.join(lines) + '\n'


def write(value, snapshot, output=None):
    core.validate(value)
    root = Path(output or ROOT / 'reports/private/personal_research').resolve()
    if not root.is_relative_to((ROOT / 'reports/private').resolve()): raise ValueError('PRIVATE_PERSONAL_OUTPUT_REQUIRED')
    folder = root / uuid.uuid4().hex; folder.mkdir(parents=True, exist_ok=False)
    atomic_json(folder / 'RESULT.json', value); atomic_json(folder / 'INPUT.json', snapshot)
    with (folder / 'REPORT.ko.md').open('x', encoding='utf-8') as f: f.write(render(value))
    atomic_json(folder / 'FILES.json', {n: digest(folder/n) for n in ['RESULT.json', 'INPUT.json', 'REPORT.ko.md']})
    return folder


def validate(folder):
    folder = Path(folder); value = read(folder/'RESULT.json'); snapshot = read(folder/'INPUT.json')
    if value['contract']=='personal-research-v1':
        from .v1 import report as original
        return original.validate(folder)
    if any(digest(folder/n) != h for n,h in read(folder/'FILES.json').items()): raise ValueError('PERSONAL_REPORT_BYTES_CHANGED')
    if value['provenance']['method_hashes'] != methods(): raise ValueError('PERSONAL_METHOD_CHANGED')
    core.validate(value)
    rebuilt, _ = core.build(value['ticker'], value['analysis_as_of'], snapshot=snapshot, illustrative=value['is_illustrative'],
                           market_snapshot=value['market_snapshot'],sec_acquisition=value.get('sec_acquisition'))
    if rebuilt != value: raise ValueError('PERSONAL_REPLAY_CHANGED')
    if render(value) != (folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('PERSONAL_RENDER_CHANGED')
    return {'status': 'VALID', 'ticker': value['ticker'], 'analysis_status': value['analysis_status'], 'network_requests': 0}
