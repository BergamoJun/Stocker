"""Ticker-only local research. No reviewer or owner credentials needed."""
import argparse
import json
import os
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
    parser.add_argument('--ai',action='store_true',help='선택 provider를 명시적으로 실행; 기본 local, 실패 시 결정론적 메모 유지')
    parser.add_argument('--ai-provider',choices=['local','openai'],help='기본 local. OpenAI는 별도 전송·유료 승인 필요')
    parser.add_argument('--ai-model',choices=['Qwen/Qwen3-4B-GGUF','qwen35-4b','gpt-6.1-sol'],help='local 기본 기존 Qwen; openai 기본 gpt-6.1-sol')
    parser.add_argument('--ai-dry-run','--dry-run',dest='ai_dry_run',action='store_true',help='전송 범위·예산만 검사; 모델/API 호출 없음')
    parser.add_argument('--ai-record',type=Path,help='선택 모델의 검증 가능한 AI_RECORD/API_RECORD 재생; 새 모델 호출 없음')
    parser.add_argument('--ai-mode',choices=['roles','integrated','selective','research','verified'],help='기존 모델은 roles(기본)/integrated/selective, qwen35-4b는 research(기본)/verified')
    parser.add_argument('--ai-context',choices=['primary','fresh'],default='primary',help=argparse.SUPPRESS)
    parser.add_argument('--reasoning',choices=['high','xhigh'],help='OpenAI 기본 high; xhigh는 명시적 선택')
    parser.add_argument('--allow-cloud',action='store_true',help='검사된 공개 SEC packet의 OpenAI 전송 허용')
    parser.add_argument('--allow-paid',action='store_true',help='한정된 예산 내 유료 OpenAI 요청 허용')
    parser.add_argument('--max-api-usd',default='2.00',help='공유 실험 ledger 총 USD 한도(기본 2.00)')
    parser.add_argument('--api-max-requests',type=int,default=3,help='공유 ledger 전체 HTTP 시도 한도, 재시도 포함(최대3)')
    parser.add_argument('--api-max-attempts',type=int,choices=[1,2],default=1,help='기업당 시도 상한; 기본1, 429/5xx만 제한 재시도')
    parser.add_argument('--api-max-output-tokens',type=int,help='추론 포함 출력 한도; 기본8192')
    parser.add_argument('--api-budget-ledger',type=Path,help='local_data/p6_openai_budget 내부 ledger; 기본 initial-v1.json을 기업 간 공유')
    args = parser.parse_args(argv)
    try:
        if args.validate: result = report.validate(args.validate)
        else:
            provider=args.ai_provider or os.environ.get('STOCKER_AI_PROVIDER','local')
            if provider not in ['local','openai']:raise ValueError('STOCKER_AI_PROVIDER must be local or openai')
            model=args.ai_model or ('gpt-6.1-sol' if provider=='openai' else 'Qwen/Qwen3-4B-GGUF')
            mode=args.ai_mode or ('research' if provider=='openai' or model=='qwen35-4b' else 'roles')
            options={'fetch_missing':args.fetch_missing,'scenario_horizon':args.scenario_horizon,'scenario_assumptions':json.loads(args.scenario_assumptions.read_text(encoding='utf-8')) if args.scenario_assumptions else None,'ai':args.ai,'ai_dry_run':args.ai_dry_run}
            if provider=='openai':
                if model!='gpt-6.1-sol':raise ValueError('OpenAI provider requires gpt-6.1-sol; no model fallback')
                if mode not in ['research','verified']:raise ValueError('OpenAI requires research or verified mode')
                if args.offline and args.ai and not args.ai_dry_run and not args.ai_record:raise ValueError('--offline cannot combine with live OpenAI; use --dry-run')
                from . import openai_provider,professional_research
                config=openai_provider.configuration(args.reasoning,args.api_max_output_tokens)
                if args.ai_record:
                    if not args.ai or args.ai_dry_run:raise ValueError('--ai-record requires --ai and cannot combine with dry-run')
                    options.update(saved=json.loads(args.ai_record.read_text(encoding='utf-8')),record_origin='SAVED_REPLAY')
                cloud={'allow_cloud':args.allow_cloud,'allow_paid':args.allow_paid,'offline':args.offline,
                       'max_usd':args.max_api_usd,'max_requests':args.api_max_requests,'max_attempts':args.api_max_attempts,'ledger':args.api_budget_ledger}
                value,snapshot=professional_research.build(args.ticker,args.as_of,usage_mode=mode,config=config,cloud_options=cloud,**options)
            elif model=='qwen35-4b':
                if mode not in ['research','verified']:raise ValueError('qwen35-4b requires --ai-mode research or verified')
                from . import adoption
                if args.ai_record:
                    if not args.ai or args.ai_dry_run:raise ValueError('--ai-record requires --ai and cannot combine with dry-run')
                    options.update(saved=json.loads(args.ai_record.read_text(encoding='utf-8')),record_origin='SAVED_REPLAY')
                value,snapshot=adoption.build(args.ticker,args.as_of,usage_mode=mode,**options)
            else:
                if model=='gpt-6.1-sol':raise ValueError('gpt-6.1-sol requires --ai-provider openai')
                if args.ai_record:raise ValueError('--ai-record is only for qwen35-4b')
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
            if 'professional_research' in value:
                p=value['professional_research'];result.update(ai_provider=p['provider'],ai_model=p['model'],
                   ai_status=p['record']['status'],ai_requests=p['api_requests_this_run'],stored_ai_requests=p['stored_api_requests'],
                   research_state=p['research_state'],verified_state=p['verified_state'],
                   professional_report=str(folder/'RESEARCH_REPORT.ko.md'),
                   visible_research_drafts=len(p['validation']['drafts']) if p['mode']=='research' else 0,
                   model_input_summary=p['record']['estimate'] if args.ai_dry_run else None)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__': raise SystemExit(main())
