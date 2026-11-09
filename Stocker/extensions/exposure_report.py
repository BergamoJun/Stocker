"""Immutable local exposure report with full source replay; no model calls."""
import argparse,json,uuid
from pathlib import Path
import jsonschema
from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest,stable_hash
from research.news_connection_report import text,link
from . import exposure_model as model,analysis_contract,weighted_engine,scope


def render(v):
    p=v['exposure']; lines=[f"# {p['identity']['symbol']} 기업 노출 근거",'',f"자료 선택 기준: {p['as_of']} · 원래 입력: {p['original_input_as_of']}",'','VERIFIED는 해당 숫자 자체가 공시와 대조됐다는 뜻입니다. INFERRED는 확인한 특정 활동에서 제한적으로 추론한 역할, HYPOTHETICAL은 추가 가정이 필요한 경로, UNKNOWN은 근거 부족입니다. 숫자가 있다고 금리·원자재 민감도가 확인되는 것은 아닙니다.','', '| 검토 항목 | 상태 | 값·기간·귀속 범위 |','|---|---|---|']
    for r in p['items']:
        detail='추가 공식 근거 필요' if r['value'] is None else f"{r['value']} {r['unit']} / {r['basis']} / {r['effective_period']['start']}~{r['effective_period']['end']} / {r['scope']}"
        lines.append(f"| {r['label_ko']} | {r['status']} | {text(detail)} |")
    lines += ['','## 직접 근거','']
    for r in p['items']:
        if not r['source_refs']: continue
        lines += [f"- {r['label_ko']}: "+' / '.join(link(s['url'],'SEC 공시 원문') for s in r['source_refs']),f"  - 사실 ID: {', '.join(r['fact_ids'])} · 최초 이용 가능: {r['first_available_at']} · 최초 공개 시각: 미확인"]
    lines += ['','## 확인되지 않은 경로','']
    for h in p['hypotheses']: lines.append(f"- {h['id']}: HYPOTHETICAL · 추가 확인 조건: {', '.join(h['assumptions'])}")
    lines += ['','총부채를 총차입금·변동금리 차입금으로 바꾸지 않습니다. XOM의 별도 영업매출 범위는 그대로 보존하며 전체 연결 매출이나 에너지 판매 노출로 자동 치환하지 않습니다. 고정·변동 금리, 만기·헤지, 계약·원가·판매 연결은 근거가 없으면 UNKNOWN입니다.','',f"공식 숫자로 직접 확인된 경제적 노출 항목: {p['verified_business_exposures']}개. 전체 기업 노출을 파악한 것으로 표시하지 않으며 주가 효과·추천 점수는 보류합니다.",'']
    return '\n'.join(lines)


def save(input_folder,baseline,*,clock=now):
    proof=scope.check(baseline); at=clock(); ai=analysis_contract.export_ai(input_folder,clock=clock)
    ident=uuid.uuid4().hex; p=model.derive(ai,at)
    value={'run_id':ident,'generated_at':at,'origin':str(Path(input_folder).resolve()),'origin_sha256':digest(Path(input_folder)/'AI_INPUT.json'),'baseline_path':str(Path(baseline).resolve()),'baseline_sha256':proof['baseline_sha256'],'method_hashes':model.methods(),'exposure':p}
    value['record_sha256']=stable_hash(value)
    root=ROOT/'reports/private/exposure_reports'/('illustrative' if ai['is_illustrative'] else 'real')/ai['identity']['cik']
    if root.resolve()!=root: raise ValueError('EXPOSURE_STORE_ALIAS')
    folder=root/ident; folder.mkdir(parents=True,exist_ok=False)
    atomic_json(folder/'EXPOSURE.json',value); (folder/'REPORT.ko.md').write_text(render(value),encoding='utf-8')
    atomic_json(folder/'FILES.json',{n:digest(folder/n) for n in ('EXPOSURE.json','REPORT.ko.md')}); return value,folder


def validate(folder):
    folder=Path(folder).resolve(); v=model.read(folder/'EXPOSURE.json'); p=v['exposure']; expected=(ROOT/'reports/private/exposure_reports'/('illustrative' if p['is_illustrative'] else 'real')/p['identity']['cik']).resolve()
    if folder.parent!=expected or folder.name!=v['run_id'] or v['method_hashes']!=model.methods() or v['record_sha256']!=stable_hash({k:x for k,x in v.items() if k!='record_sha256'}): raise ValueError('EXPOSURE_REPORT_CHANGED')
    historical_check(v['baseline_path'],expected_sha256=v['baseline_sha256']); ai=analysis_contract.export_ai(v['origin'],clock=lambda:v['generated_at'])
    if digest(Path(v['origin'])/'AI_INPUT.json')!=v['origin_sha256'] or model.derive(ai,v['generated_at'])!=p: raise ValueError('EXPOSURE_SOURCE_REPLAY_CHANGED')
    jsonschema.validate(p,model.read(model.BASE/'exposure_contract.schema.json'))
    files=model.read(folder/'FILES.json')
    if set(files)!={'EXPOSURE.json','REPORT.ko.md'} or any(digest(folder/n)!=s for n,s in files.items()) or render(v)!=(folder/'REPORT.ko.md').read_text(encoding='utf-8'): raise ValueError('EXPOSURE_FILES_CHANGED')
    return v


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--ticker'); parser.add_argument('--baseline',type=Path); parser.add_argument('--validate',type=Path); a=parser.parse_args(argv)
    if a.validate: v=validate(a.validate); print(json.dumps({'status':'exposure_report_valid','items':len(v['exposure']['items'])})); return 0
    if not a.ticker or not a.baseline: parser.error('--ticker and --baseline required')
    v,folder=save(weighted_engine.resolve(a.ticker.upper()),a.baseline); validate(folder)
    print(json.dumps({'symbol':v['exposure']['identity']['symbol'],'coverage':v['exposure']['coverage'],'verified_business_exposures':v['exposure']['verified_business_exposures'],'folder':str(folder)})); return 0
if __name__=='__main__': raise SystemExit(main())
