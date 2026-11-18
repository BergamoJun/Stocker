"""Official new SEC request entrypoint; a legacy version is never an engine choice."""
import argparse,json
from pathlib import Path
from research.sec_transport import now
from research.sec_report import ROOT
from research.validation import digest
from extensions.sec_evidence_v1.catalog import resolve
from . import VERIFIER,read,facade,report,replay
from .owner import OwnerContext


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--tickers',nargs='+');group.add_argument('--input',type=Path)
    group.add_argument('--validate',type=Path);group.add_argument('--legacy-replay',type=Path)
    p.add_argument('--legacy-version',choices=['v1','v1.1','impact-v1']);p.add_argument('--verification-version')
    p.add_argument('--as-of');p.add_argument('--out',type=Path,default=ROOT/'reports/private/sec_current_reports')
    p.add_argument('--source-store',type=Path);p.add_argument('--legacy-review-root',type=Path);p.add_argument('--review-store',type=Path);p.add_argument('--cache-root',type=Path)
    p.add_argument('--owner-policy',type=Path);p.add_argument('--owner-sha256');p.add_argument('--owner-registry',type=Path)
    p.add_argument('--event-input',type=Path);p.add_argument('--instrument-id');p.add_argument('--accept-review',type=Path);p.add_argument('--review-out',type=Path)
    a=p.parse_args(argv)
    if a.legacy_replay:
        if not a.legacy_version:p.error('--legacy-version required for explicit read-only replay')
        expected=None
        if a.owner_policy:
            if not a.owner_sha256 or digest(a.owner_policy)!=a.owner_sha256:p.error('historical owner policy byte pin required')
            expected=read(a.owner_policy)
        print(json.dumps(replay.validate_legacy(a.legacy_replay,a.legacy_version,expected_trust=expected)));return 0
    if a.validate:
        b=read(a.validate/'CURRENT.json');illustrative=isinstance(b['request'],dict) and b['request'].get('mode')=='ILLUSTRATIVE'
        owner=OwnerContext.load(a.owner_policy,a.owner_sha256,illustrative=illustrative,registry=a.owner_registry) if a.owner_policy else None
        print(json.dumps(report.validate(a.validate,owner=owner)));return 0
    if a.input:
        requests=[read(a.input)]
        if a.verification_version is not None:requests[0]['verification_version']=a.verification_version
    else:
        at=a.as_of or now();event=read(a.event_input) if a.event_input else None
        requests=[facade.request_for(**identity,as_of=at,event_input=event,instrument_id=a.instrument_id,
            source_store=a.source_store,legacy_review_root=a.legacy_review_root,review_store=a.review_store,cache_root=a.cache_root) for identity in resolve(a.tickers)]
        if a.verification_version is not None:
            for request in requests:request['verification_version']=a.verification_version
    exit_code=0
    for request in requests:
        illustrative=isinstance(request,dict) and request.get('mode')=='ILLUSTRATIVE'
        owner=OwnerContext.load(a.owner_policy,a.owner_sha256,illustrative=illustrative,registry=a.owner_registry) if a.owner_policy else None
        if a.accept_review:
            if not a.review_out:p.error('--review-out required; legacy user review registry is read-only')
            receipt=facade.accept_review(request,read(a.accept_review),a.review_out,owner=owner)
            print(json.dumps({'status':'REVIEW_IMPORTED_NOT_CURRENT_APPROVAL','verification_version':VERIFIER,'receipt_hash':receipt['record_sha256']}));continue
        folder=report.run(request,a.out,owner=owner)
        d=read(folder/'CURRENT.json')['decision']
        print(json.dumps({'status':d['status'],'verification_version':VERIFIER,'amount':d['amount'],
            'calculation_status':d['calculation_status'],'reason_codes':d['reason_codes'],'folder':str(folder)}))
        if d['status']=='BLOCKED':exit_code=2
    return exit_code


if __name__=='__main__':raise SystemExit(main())
