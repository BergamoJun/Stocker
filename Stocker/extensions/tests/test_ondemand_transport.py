"""ILLUSTRATIVE - exact date parameters and shared provider cooldowns."""
from urllib.parse import urlparse,parse_qs
from urllib.error import HTTPError
import io
import pytest
from extensions import ondemand_transport as transport
from extensions.tests.test_api_queue import fixture
from research.news_transport import NewsRequestDeferred
from research.panel_transport import SourceUnavailable

def client_case(tmp_path):
    old,opener,clock,job,baseline=fixture(tmp_path)
    client=transport.Client(old.record,'illustrative-key',baseline,root=tmp_path/'account',gdelt_root=tmp_path/'gdelt',clock=clock,sleep=clock.pause,opener=opener)
    client.queue.monotonic=lambda:clock.elapsed
    return client,clock

def test_explicit_window_uses_shared_gdelt_budget_and_never_sends_stock_key(tmp_path):
    client,clock=client_case(tmp_path); requests=[]
    class Opener:
        def open(self,request,timeout): requests.append(request); return io.BytesIO(b'{"articles":[]}')
    client.opener=Opener()
    assert client.gdelt_window('ILLUSTRATIVE','2026-11-03T12:00:00Z',clock())=={'articles':[]}
    query=parse_qs(urlparse(requests[0].full_url).query)
    assert query['startdatetime']==['20261103120000'] and query['enddatetime']==['20261110120000'] and 'timespan' not in query
    assert requests[0].get_header('Authorization') is None and 'illustrative-key' not in requests[0].full_url
    assert client.gdelt_requests==1

def test_429_shared_cooldown_prevents_next_http_without_retry(tmp_path):
    client,clock=client_case(tmp_path); calls=[]
    class Opener:
        def open(self,request,timeout): calls.append(request); raise HTTPError(request.full_url,429,'ILLUSTRATIVE',{'Retry-After':'900'},None)
    client.opener=Opener()
    with pytest.raises(HTTPError): client.gdelt_window('ILLUSTRATIVE','2026-11-03T12:00:00Z',clock())
    with pytest.raises(NewsRequestDeferred): client.gdelt_window('ILLUSTRATIVE','2026-11-03T12:00:00Z',clock())
    assert len(calls)==1 and client.gdelt_requests==1

def test_request_cap_and_future_window_fail_before_http(tmp_path):
    client,clock=client_case(tmp_path); client.gdelt_requests=4
    with pytest.raises(SourceUnavailable,match='CAP'): client.gdelt_window('ILLUSTRATIVE','2026-11-03T12:00:00Z',clock())
    with pytest.raises(ValueError,match='WINDOW'): client.gdelt_window('ILLUSTRATIVE',clock(),'2027-01-01T00:00:00Z')
