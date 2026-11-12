"""Offline CLI; stdout contains status/count/path only, never private facts."""
import argparse
import json
from pathlib import Path
from research.sec_report import ROOT
from extensions.research_v4 import read
from . import report,adapters
from .examples import interest_example


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--demo',action='store_true');group.add_argument('--ticker',choices=['BE','MSFT','XOM'])
    group.add_argument('--input',type=Path);group.add_argument('--validate',type=Path)
    parser.add_argument('--out',type=Path,default=ROOT/'reports/private/financial_impact_v1')
    parser.add_argument('--legacy-report',type=Path)
    args=parser.parse_args(argv)
    if args.validate:
        print(json.dumps(report.validate(args.validate)));return 0
    packet=interest_example() if args.demo else adapters.issuer_probe(args.ticker) if args.ticker else read(args.input)
    if not args.out.resolve().is_relative_to((ROOT/'reports/private').resolve()):
        parser.error('--out must remain in reports/private')
    folder=report.write(packet,args.out,legacy=args.legacy_report)
    print(json.dumps({'status':'SHADOW_REPORT_SAVED','folder':str(folder),**report.validate(folder)}))
    return 0


if __name__=='__main__':raise SystemExit(main())
