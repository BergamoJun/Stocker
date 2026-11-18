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
    args = parser.parse_args(argv)
    try:
        if args.validate: result = report.validate(args.validate)
        else:
            value, snapshot = core.build(args.ticker, args.as_of)
            folder = report.write(value, snapshot, args.output)
            result = {'ticker': value['ticker'], 'status': value['analysis_status'], 'output': str(folder),
                      'observed_facts': sum(f['evidence_kind']=='OBSERVED' for f in value['facts']), 'network_requests': 0}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__': raise SystemExit(main())
