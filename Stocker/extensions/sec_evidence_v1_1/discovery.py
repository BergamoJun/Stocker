"""Read-only discovery + semantic proof + independently evaluated eligibility."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from research.rights import as_utc
from research.validation import stable_hash
from extensions.sec_evidence_v1 import discovery as original, reviews as original_reviews
from extensions.sec_evidence_v1.report import semantic as original_semantic
from . import methods,seal,verify_seal,contract,semantic_validation,eligibility


def build(cik,ticker,as_of,*,source_store=None,legacy_review_root=None,review_store=None,cache_root=None,
          illustrative=False,trust=None,max_age_days=730,maximum_candidates=512,max_filings=4,
          calculation_window=None,event_effective=None):
    window=deepcopy(calculation_window or {'start':as_utc(as_of).date().isoformat(),
        'end':(as_utc(as_of).date()+timedelta(days=365)).isoformat()})
    config=dict(cik=cik,ticker=ticker,as_of=as_of,source_store=source_store,legacy_review_root=legacy_review_root,
        review_store=review_store,cache_root=cache_root,illustrative=illustrative,max_age_days=max_age_days,
        maximum_candidates=maximum_candidates,max_filings=max_filings,calculation_window=window,event_effective=event_effective)
    config={k:str(v) if isinstance(v,Path) else v for k,v in config.items()}
    # fresh=True never writes the shared parsing cache; SEC acquisition is absent.
    raw=original.build(cik,ticker,as_of,source_store=source_store,legacy_review_root=legacy_review_root,
        review_store=review_store,cache_root=cache_root,illustrative=illustrative,trust=trust,
        max_age_days=max_age_days,maximum_candidates=maximum_candidates,max_filings=max_filings,fresh=True)
    result=deepcopy(raw); result.pop('record_sha256'); current=[]; historical=[]; rows=[]
    receipts={r['record_sha256']:r for r in original_reviews.receipts(raw['review_store'],as_of)}
    for v in raw['verified_exposures']:
        # v1 already requires two identical, independent, owner-pinned reviews.
        manifest=receipts[v['review_receipt_hashes'][0]]['manifest']
        original_reviews.check_terms(manifest,raw)
        assessment=semantic_validation.check(manifest,raw)
        eligible=eligibility.check(assessment,as_of,window,event_effective=event_effective)
        rows.append({'instrument_id':v['instrument_id'],'review_receipt_hashes':v['review_receipt_hashes'],
                     'semantic':assessment,'eligibility':eligible})
        if assessment['supported']: historical.append(v)
        if eligible['calculation_eligible']: current.append(v)
    result.update(contract='sec-evidence-adapter-v1.1',methods=methods(),verified_exposures=current,
        historical_evidence=historical,semantic_assessments=rows,eligibility_window=window,
        configuration=config,legacy_discovery_sha256=stable_hash(original_semantic(raw)))
    if raw['conflicts']:result['status']='CONFLICTED'
    else:result['status']='VERIFIED' if current else ('REVIEW_REQUIRED' if raw['candidates'] else 'UNKNOWN')
    if not current:
        result['missing_evidence']=[{'field':'semantic_contract_relationships_and_current_eligibility',
            'reason':'No source-supported, independently reviewed contract covers the complete requested window at the analysis cutoff.',
            'source_links':sorted({s['source_url'] for s in raw['sources'] if s['kind']=='document'})}]+raw['missing_evidence']
    result['limitations']+=['v1.1 validates only its documented bounded paragraph grammar; unsupported language remains HUMAN_REVIEW_REQUIRED.',
        'Historical evidence verification and requested-window calculation eligibility are separate. No automatic partial proration or future activation.',
        'v1 remains frozen for replay; use extensions.sec_evidence_v1_1 for hardened execution.']
    result=seal(result);contract.validate(result);return result


def revalidate(result,trust):
    verify_seal(result);contract.validate(result)
    if result['methods']!=methods():raise ValueError('SEMANTIC_ADAPTER_METHOD_CHANGED')
    rebuilt=build(**result['configuration'],trust=trust)
    if rebuilt!=result:raise ValueError('SEMANTIC_SOURCE_TRUST_OR_RELATIONSHIP_CHANGED')
    return rebuilt
