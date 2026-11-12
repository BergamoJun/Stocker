"""Offline hardened ticker/report path; no acquisition or automatic trust import."""
import argparse,json
from pathlib import Path
from research.sec_transport import now
from research.sec_report import ROOT
from extensions.sec_evidence_v1.catalog import resolve
from . import report,reviews,read


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True);group.add_argument('--tickers',nargs='+');group.add_argument('--validate',type=Path);group.add_argument('--import-review',type=Path)
    p.add_argument('--as-of');p.add_argument('--out',type=Path,default=ROOT/'reports/private/sec_semantic_reports')
    p.add_argument('--source-store',type=Path);p.add_argument('--legacy-review-root',type=Path);p.add_argument('--review-store',type=Path);p.add_argument('--cache-root',type=Path)
    p.add_argument('--trust-policy',type=Path);p.add_argument('--trust-sha256');p.add_argument('--event-input',type=Path);p.add_argument('--instrument-id');p.add_argument('--adapter-report',type=Path)
    p.add_argument('--max-filings',type=int,default=4);p.add_argument('--max-age-days',type=int,default=730)
    a=p.parse_args(argv)
    trust=reviews.load_trust(a.trust_policy,a.trust_sha256) if a.trust_policy else None
    if a.validate:print(json.dumps(report.validate(a.validate,expected_trust=trust)));return 0
    if a.import_review:
        if not a.adapter_report:p.error('--adapter-report required')
        report.validate(a.adapter_report,expected_trust=trust);r=read(a.adapter_report/'ADAPTER.json')['adapter']
        receipt=reviews.store(read(a.import_review),r,a.review_store or Path(r['review_store']))
        print(json.dumps({'status':'REVIEW_IMPORTED_NOT_AUTOMATICALLY_TRUSTED','receipt_hash':receipt['record_sha256']}));return 0
    at=a.as_of or now()
    for identity in resolve(a.tickers):
        folder=report.run(**identity,as_of=at,output_root=a.out,source_store=a.source_store,legacy_review_root=a.legacy_review_root,review_store=a.review_store,cache_root=a.cache_root,
            trust=trust,event_input=read(a.event_input) if a.event_input else None,instrument_id=a.instrument_id,max_filings=a.max_filings,max_age_days=a.max_age_days)
        print(json.dumps({'status':'SHADOW_REPORT_SAVED','folder':str(folder)}))
    return 0


if __name__=='__main__':raise SystemExit(main())
