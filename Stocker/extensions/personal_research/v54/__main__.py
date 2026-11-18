"""Ticker-only local research. No reviewer or owner credentials needed."""
import argparse
import json
from pathlib import Path
from . import core, report


def main(argv=None):
    parser = argparse.ArgumentParser(description='저장된 SEC 자료로 개인용 한국어 종목 리서치를 생성합니다. 기본 실행은 오프라인입니다.')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--ticker'); choice.add_argument('--validate', type=Path)
    parser.add_argument('--as-of', help='시간대 포함 ISO시각 또는 한국 날짜(해당일 종료)')
    parser.add_argument('--output', type=Path, help='개인용 private 출력 폴더')
    network=parser.add_mutually_exclusive_group()
    network.add_argument('--offline',action='store_true',help='저장된 자료만 사용(기본값)')
    network.add_argument('--fetch-missing',action='store_true',help='부족하거나 오래된 EOD가격과 없는 SEC재무자료를 기존 수집기로 제한 취득')
    parser.add_argument('--scenario-horizon',type=int,choices=[1,3],default=1,help='조건부 시나리오 기간(명목 연도 단계)')
    parser.add_argument('--scenario-assumptions',type=Path,help='bull/base/bear별 명시적 사용자 가정 JSON')
    parser.add_argument('--memo',action='store_true',help='동일 계산에 연결된 한국어 Investment Memo 경로 표시(기본 실행도 저장)')
    parser.add_argument('--ai',action='store_true',help='기존 로컬 CUDA Qwen을 명시적으로 실행; 실패 시 결정론적 메모 유지')
    parser.add_argument('--ai-model',choices=['Qwen/Qwen3-4B-GGUF','qwen35-4b'],default='Qwen/Qwen3-4B-GGUF',help='기존 모델 또는 명시적 실험용 Qwen3.5; 다운로드 없음')
    parser.add_argument('--ai-dry-run',action='store_true',help='모델 전송 범위 요약만 기록; 모델 호출 없음')
    parser.add_argument('--ai-mode',choices=['roles','integrated','selective','research','verified'],help='기존 모델은 roles(기본)/integrated/selective, qwen35-4b는 research(기본)/verified')
    parser.add_argument('--ai-context',choices=['primary','fresh'],default='primary',help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.validate: result = report.validate(args.validate)
        else:
            mode=args.ai_mode or ('research' if args.ai_model=='qwen35-4b' else 'roles')
            options={'fetch_missing':args.fetch_missing,'scenario_horizon':args.scenario_horizon,'scenario_assumptions':json.loads(args.scenario_assumptions.read_text(encoding='utf-8')) if args.scenario_assumptions else None,'ai':args.ai,'ai_dry_run':args.ai_dry_run}
            if args.ai_model=='qwen35-4b':
                if mode not in ['research','verified']:raise ValueError('qwen35-4b requires --ai-mode research or verified')
                from . import adoption
                value,snapshot=adoption.build(args.ticker,args.as_of,usage_mode=mode,**options)
            else:
                if mode not in ['roles','integrated','selective']:raise ValueError('Legacy model requires roles/integrated/selective')
                value,snapshot=core.build(args.ticker,args.as_of,ai_mode=mode,ai_context=args.ai_context,**options)
            folder = report.write(value, snapshot, args.output)
            result = {'ticker': value['ticker'], 'status': value['analysis_status'], 'output': str(folder),
                      'observed_facts': sum(f['evidence_kind']=='OBSERVED' for f in value['facts']),
                      'market_data_status':value['market_data_status'],'network_requests':value['provenance']['network_requests'],
                      'memo':str(folder/'MEMO.ko.md'),'synthesis_mode':value['synthesis_mode'],
                      'ai_status':value['ai_provider_status']['status'],'ai_requests':value['ai_provider_status']['requests'],
                      'model_input_summary':value['model_input_summary'] if args.ai_dry_run else None}
            if 'research_pipeline' in value:
                p=value['research_pipeline'];result.update(ai_model=p['model'],research_state=p['research_state'],verified_state=p['verified_state'],research_memo=str(folder/'RESEARCH_MEMO.ko.md'),visible_research_drafts=len(p['assessment']['drafts']) if p['usage_mode']=='research' else 0)
                if args.ai_dry_run:result['model_input_summary']=p['input_summary']
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__': raise SystemExit(main())
