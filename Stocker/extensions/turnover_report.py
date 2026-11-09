"""Private source-bound turnover research report; no recommendation release."""
import argparse
import json
from pathlib import Path
import uuid
import jsonschema
from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest,stable_hash
from . import scope,analysis_contract,weighted_engine,weighted_features,turnover_features as features

FILES=['extensions/'+p for p in ('turnover_policy.json','turnover_features.py','turnover_report.py','turnover_contract.schema.json')]
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def methods(): return {p:digest(ROOT/p) for p in FILES}

def build(bundle,at):
    ai,local=bundle['ai_input'],bundle['local_input']; settings=weighted_engine.policy()
    stock=local['market']['roles']['stock']
    bars,calendar=weighted_features.price_history(stock,ai['as_of'],at,settings)
    result=features.calculate(bars,calendar=calendar,cutoff=ai['as_of'])
    comparison={}
    for role in ('market','sector'):
        try:
            other,_=weighted_features.price_history(local['market']['roles'][role],ai['as_of'],at,settings)
            if [b.session for b in bars[-6:]]!=[b.session for b in other[-6:]]: raise ValueError('BENCHMARK_SESSION_MISMATCH')
            comparison[role]={'status':'available','five_session_excess_pct_points':weighted_features.decimal_text(((bars[-1].close/bars[-6].close-1)-(other[-1].close/other[-6].close-1))*100),'source_ids':local['market']['roles'][role]['source_ids']}
        except (ValueError,KeyError): comparison[role]={'status':'unavailable','reason':'NO_ALIGNED_BENCHMARK'}
    return {'identity':ai['identity'],'is_illustrative':ai['is_illustrative'],'evidence_as_of':ai['as_of'],'source_ids':stock['source_ids'],'features':result,'benchmarks':comparison,
        'measure_limitations':['Close times reported shares is a proxy, not exact traded notional','Current split-adjusted vintage; not certified historical availability','No trader identity, order-flow direction, verified news effect or calibrated probability','No change to original forecast, cost screen, weights or total-score withholding'],
        'released_score':None,'released_price_plan':None,'source_requests':0,'external_inference_calls':0,'model_training_performed':False}

def render(v):
    f=v['features']; s=f['signals']
    return '\n'.join([f"# {v['identity']['symbol']} 거래대금 근사 활동 분석",'',f"생성: {v['generated_at']} · 가격 기준일: {f['reference_session']}",'',
        '거래대금은 종가×거래량으로 계산한 근사값입니다. 실제 순유입 자금·기관 매수·미공개 정보 거래를 확인한 값이 아닙니다. 큰 거래가 앞으로의 상승을 보장하지 않습니다.','',
        '| 평가 요소 | 계산값 | 의미 |','|---|---:|---|',
        f"| 평소 대비 거래대금 근사 배수 | {f['relative_notional']} | 직전 20거래일 중앙값 대비 배수. 당일은 비교 기준에서 제외 |",
        f"| 최근 3일 방향·활동 지속성 | {f['persistence_3']} | -1~1, 하락에 동반된 확대는 음수. 기관 매매 지표가 아님 |",
        f"| 최근 5일 방향·활동 지속성 | {f['persistence_5']} | 각 날짜의 직전 20일 기준을 따로 적용 |",
        f"| 종가의 일중 범위 위치 | {f['close_location']} | -1은 저가, +1은 고가. 고가=저가이면 미산출 |",'',
        f"계산 상태: {f['status']} · 부족 사유: {f['reason_code'] or '없음'}",'',
        '비교 후보는 기존 거래량 변수, 상대거래대금 근사 변수, 지속성 결합 변수입니다. 기존 변수의 계수 2를 교체하며 가점을 추가로 얹지 않습니다. 이는 순위 신호 후보이며 예측 수익률·승률·1~100점 추천도가 아닙니다.','',
        f"원래 거래량 신호: {s['original_volume']} · 상대 근사 신호: {s['relative_notional']} · 지속성 신호: {s['persistent_notional']}",'',
        '뉴스 최초 공개 시각과의 연결은 미완료입니다. 과거 상승을 특정 뉴스의 선행 매수나 선반영으로 인증하지 않습니다. 같은 거래일의 시장·업종 초과 변화는 보조 자료이며 추가 가점으로 중복 반영하지 않습니다.','',
        '5거래일 고점 기회와 비용 후 보유 수익의 순위 관련성은 별도 과거 비교에서 확인합니다. 기존 목표가·가격 모형·비용 필터·정식 점수 보류는 유지합니다.',''])

def save(input_folder,baseline,*,clock=now):
    proof=scope.check(baseline); bundle=analysis_contract.validate(input_folder); at=clock(); ident=uuid.uuid4().hex
    value={'contract':'turnover-proxy-research-v1','run_id':ident,'generated_at':at,'input_folder':str(Path(input_folder).resolve()),'input_sha256':digest(Path(input_folder)/'bundle.json'),
        'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],'method_hashes':methods(),**build(bundle,at)}
    value['record_sha256']=stable_hash(value)
    jsonschema.validate(value,read(ROOT/'extensions/turnover_contract.schema.json'))
    folder=ROOT/'reports/private/turnover_research'/('illustrative' if value['is_illustrative'] else 'real')/value['identity']['cik']/ident
    folder.mkdir(parents=True,exist_ok=False); atomic_json(folder/'TURNOVER.json',value)
    (folder/'REPORT.ko.md').write_text(render(value),encoding='utf-8')
    atomic_json(folder/'FILES.json',{p.name:digest(p) for p in folder.iterdir() if p.is_file()})
    return value,folder

def validate(folder):
    folder=Path(folder); value=read(folder/'TURNOVER.json'); jsonschema.validate(value,read(ROOT/'extensions/turnover_contract.schema.json'))
    if value['run_id']!=folder.name or value['method_hashes']!=methods() or stable_hash({k:v for k,v in value.items() if k!='record_sha256'})!=value['record_sha256']: raise ValueError('TURNOVER_REPORT_CHANGED')
    historical_check(value['baseline_path'],expected_sha256=value['baseline_sha256'])
    source=Path(value['input_folder'])
    if digest(source/'bundle.json')!=value['input_sha256']: raise ValueError('TURNOVER_SOURCE_CHANGED')
    result=build(analysis_contract.validate(source),value['generated_at'])
    if any(value[k]!=v for k,v in result.items()) or render(value)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('TURNOVER_REPORT_REPLAY_CHANGED')
    manifest=read(folder/'FILES.json')
    if set(manifest)!={'TURNOVER.json','REPORT.ko.md'} or any(digest(folder/n)!=sha for n,sha in manifest.items()): raise ValueError('TURNOVER_REPORT_FILES_CHANGED')
    return value

def _main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--ticker',choices=['BE','MSFT','XOM']); p.add_argument('--baseline',type=Path); p.add_argument('--validate',type=Path); a=p.parse_args(argv)
    if a.validate: value=validate(a.validate); print(json.dumps({'status':'turnover_report_valid','symbol':value['identity']['symbol']})); return
    if not a.ticker or not a.baseline: p.error('--ticker and --baseline required')
    value,folder=save(weighted_engine.resolve(a.ticker),a.baseline); validate(folder)
    print(json.dumps({'status':value['features']['status'],'symbol':a.ticker,'folder':str(folder),'source_requests':0}))

def main(argv=None):
    try: _main(argv); return 0
    except Exception:
        print(json.dumps({'status':'turnover_report_failed','reason_code':'SOURCE_OR_REPORT_VALIDATION_FAILED'})); return 1

if __name__=='__main__': raise SystemExit(main())
