"""Ticker-only offline analysis; explicit independent review import is separate."""
import argparse,json
from pathlib import Path
from research.sec_transport import now
from research.sec_report import ROOT
from . import catalog,report,reviews,read


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tickers',nargs='+');p.add_argument('--as-of');p.add_argument('--validate',type=Path)
    p.add_argument('--out',type=Path,default=ROOT/'reports/private/sec_evidence_v1')
    p.add_argument('--trust-policy',type=Path);p.add_argument('--trust-sha256')
    p.add_argument('--event-input',type=Path);p.add_argument('--instrument-id')
    p.add_argument('--source-store',type=Path);p.add_argument('--legacy-review-root',type=Path);p.add_argument('--review-store',type=Path)
    p.add_argument('--max-filings',type=int,default=4);p.add_argument('--max-age-days',type=int,default=730)
    p.add_argument('--import-review',type=Path);p.add_argument('--adapter-report',type=Path)
    args=p.parse_args(argv)
    expected_trust=reviews.load_trust(args.trust_policy,args.trust_sha256) if args.trust_policy else None
    if args.validate:print(json.dumps(report.validate(args.validate,expected_trust=expected_trust)));return 0
    if not args.out.resolve().is_relative_to((ROOT/'reports/private').resolve()):p.error('--out must remain private')
    if args.import_review:
        if not args.adapter_report:p.error('--adapter-report required for review import')
        report.validate(args.adapter_report,expected_trust=expected_trust);r=read(args.adapter_report/'ADAPTER.json')['adapter']
        value=reviews.store(read(args.import_review),r,args.review_store or Path(r['review_store']))
        print(json.dumps({'status':'REVIEW_IMPORTED_NOT_AUTOMATICALLY_TRUSTED','receipt_hash':value['record_sha256']}));return 0
    if not args.tickers:p.error('--tickers required')
    trust=expected_trust or reviews.empty_trust()
    at=args.as_of or now()
    for identity in catalog.resolve(args.tickers):
        folder=report.run(**identity,as_of=at,output_root=args.out,trust=trust,
            event_input=read(args.event_input) if args.event_input else None,instrument_id=args.instrument_id,
            source_store=args.source_store,legacy_review_root=args.legacy_review_root,review_store=args.review_store,
            max_filings=args.max_filings,max_age_days=args.max_age_days)
        r=read(folder/'ADAPTER.json')
        print(json.dumps({'status':'SHADOW_REPORT_SAVED','folder':str(folder),'candidates':len(r['adapter']['candidates']),
            'verified_contracts':len(r['adapter']['verified_exposures']),'impact_status':r['bridge']['output']['status']}))
    return 0


if __name__=='__main__':raise SystemExit(main())
