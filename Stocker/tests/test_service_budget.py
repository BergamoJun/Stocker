"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Budget assumptions and mock HTTP."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from research import costs, news_transport
from research.sec_report import ROOT


def test_token_budget_is_separate_from_current_zero_token_algorithm():
    policy = json.loads((ROOT / "config/service_budget_policy.json").read_text())
    result = costs.calculate(policy)
    one = next(row for row in result["workloads"] if row["id"] == "one_stock_daily")
    assert result["current_algorithm_llm_tokens"] == 0
    assert one["monthly_reports"] == 22
    assert one["monthly_input_tokens"] == 715000 and one["monthly_output_tokens"] == 165000
    assert Decimal(one["llm_estimates"]["gpt-6-luna"]["monthly_usd"]) == Decimal("0.154")
    assert one["llm_estimates"]["gpt-6-luna"]["credit_sufficiency"]["0"] is False
    assert one["llm_estimates"]["gpt-6-luna"]["credit_sufficiency"]["20"] is True
    assert one["daily_news_requests"] == 12
    frequent = next(row for row in result["workloads"] if row["id"] == "one_stock_15min_reports")
    assert frequent["daily_news_requests"] == 384
    assert frequent["gnews_free_nominal_request_capacity_sufficient"] is False
    assert frequent["gnews_essential_request_capacity_sufficient"] is True
    assert result["gdelt_daily_capacity_guaranteed"] is False


class Response:
    status = 200
    def __init__(self, body=b'{"articles": []}'):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        assert limit == 3000001
        return self.body


class Clock:
    value = datetime(2026, 11, 7, tzinfo=timezone.utc)
    def now(self):
        return self.value.isoformat()
    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


SETTINGS = {"minimum_request_spacing_seconds": 15, "default_429_cooldown_seconds": 900}
REQUEST = Request("https://api.gdeltproject.org/api/v2/doc/doc?query=synthetic")


def test_shared_request_pacing_and_429_cooldown_honor_retry_after(tmp_path):
    clock, times = Clock(), []
    class Opener:
        def open(self, request, timeout):
            times.append(clock.value)
            if len(times) == 2:
                raise HTTPError(request.full_url, 429, "Rate limit", {"Retry-After": "1800"}, None)
            return Response()
    opener = Opener()
    kwargs = dict(clock=clock.now, sleep=clock.sleep, state_dir=tmp_path, settings=SETTINGS)
    assert news_transport.request_metadata(REQUEST, opener, **kwargs) == {"articles": []}
    with pytest.raises(HTTPError):
        news_transport.request_metadata(REQUEST, opener, **kwargs)
    assert (times[1] - times[0]).total_seconds() >= 15
    with pytest.raises(news_transport.NewsRequestDeferred) as error:
        news_transport.request_metadata(REQUEST, opener, **kwargs)
    assert len(times) == 2
    assert (datetime.fromisoformat(error.value.retry_at) - clock.value).total_seconds() == 1800
    assert not (tmp_path / "request.lock").exists()


def test_busy_lock_and_plaintext_throttle_do_not_make_more_requests(tmp_path):
    clock = Clock()
    class Opener:
        calls = 0
        def open(self, *args, **kwargs):
            self.calls += 1
            return Response(b'Please limit requests to one every 5 seconds')
    opener = Opener()
    (tmp_path / "request.lock").write_text("fixture")
    kwargs = dict(clock=clock.now, sleep=clock.sleep, state_dir=tmp_path, settings=SETTINGS)
    with pytest.raises(news_transport.NewsRequestDeferred):
        news_transport.request_metadata(REQUEST, opener, **kwargs)
    assert opener.calls == 0
    (tmp_path / "request.lock").unlink()
    with pytest.raises(news_transport.NewsRequestDeferred):
        news_transport.request_metadata(REQUEST, opener, **kwargs)
    with pytest.raises(news_transport.NewsRequestDeferred):
        news_transport.request_metadata(REQUEST, opener, **kwargs)
    assert opener.calls == 1


def test_retry_date_and_invalid_endpoint_are_safe(tmp_path):
    at = "2026-11-07T00:00:00Z"
    assert news_transport.retry_time("Sat, 07 Nov 2026 01:00:00 GMT", at, 900) == "2026-11-07T01:00:00+00:00"
    assert news_transport.retry_time("invalid", at, 900) == "2026-11-07T00:15:00+00:00"
    with pytest.raises(ValueError, match="fixed GDELT"):
        news_transport.request_metadata(Request("https://private.invalid/"), None, state_dir=tmp_path, settings=SETTINGS)
