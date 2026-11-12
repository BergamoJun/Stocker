"""Bounded optional acquisition through the existing SEC client, never a crawler."""
from research.sec_transport import endpoint,Deferred,Unavailable
from research.sec_facts import cik


def fetch_missing(issuer,filings,client,*,maximum_requests=2):
    if not 0<=maximum_requests<=2:raise ValueError('ADAPTER_REQUEST_CAP_INVALID')
    issuer=cik(issuer);rows=[];seen=set();calls=0;before=client.network_requests
    for filing in filings:
        url=filing.get('source_url')
        if filing.get('document_source_ids') or not url or url in seen:continue
        seen.add(url)
        if endpoint(url)!='document' or not url.startswith('https://www.sec.gov/Archives/edgar/data/'+str(int(issuer))+'/'):
            raise ValueError('FETCH_ISSUER_OR_ENDPOINT_MISMATCH')
        if calls>=maximum_requests:
            rows.append({'accession':filing['accession'],'url':url,'status':'REQUEST_CAP','reason':'No request made'});continue
        calls+=1
        try:
            value=client.get(url)
            rows.append({'accession':filing['accession'],'url':url,'status':'CACHE_HIT' if value['cache_hit'] else 'FETCHED',
                'raw_sha256':value['raw_sha256'],'raw_path':value['raw_path'],'available_at':value['available_at'],
                'reason':'Register via the existing SEC snapshot collector before contract promotion; no registry was rewritten'})
        except (Deferred,Unavailable) as error:
            rows.append({'accession':filing['accession'],'url':url,'status':'FAILED','reason':error.code})
    return {'requests':client.network_requests-before,'get_calls':calls,'rows':rows,'existing_sec_registry_modified':False}
