"""Bounded SEC primary-content retrieval; every request uses a mock transport."""
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from observatory.db import Analysis, State, now
from observatory.research import (SECBackoff, SECSource, SECSubmissions, filing_text,
                                 generation_context, run_research_cycle, sec_document_url)
from tests.test_research import AT, sec_body

URL = "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/report.htm"
LEAD = {"primary_document_url": URL}


@pytest.mark.parametrize("document", ["../x.htm", "x/filing.htm", "https://evil.test/x.htm", "x%2f.htm", "x.htm?x=1", "x.htm#frag", "a..htm", "file.pdf", None])
def test_archive_path_rejects_untrusted_filenames(document):
    with pytest.raises(ValueError):
        sec_document_url("123", "0000000123-26-000001", document)


def test_archive_identity_and_text_safety():
    assert sec_document_url("0000000123", "0000000123-26-000001", "report.htm") == URL
    text = filing_text('<html><style>secret style</style><script>secret script</script><ix:hidden>hidden facts</ix:hidden><p>Revenue &amp; risk</p><p>Second paragraph</p></html>')
    assert text == "Revenue & risk Second paragraph"
    assert len(filing_text("word " * 20000, html=False)) == 12000


async def test_primary_document_is_verified_from_metadata_and_bounded_content():
    body = sec_body()
    body["filings"]["recent"]["primaryDocument"] = ["report.htm"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        leads = await SECSubmissions().fetch(SECSource(symbol="TEST", cik="123"), "App test@example.com", AT, client)
    lead = leads[0]
    assert lead["primary_document_url"].endswith("/report.htm")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["user-agent"] == "App test@example.com"
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"},
                              text="<p>Revenue increased and management warned that costs may adversely affect results. </p>" * 40)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        lead.update(await SECSubmissions().fetch_content(lead, "App test@example.com", AT, client))
    assert len(calls) == 1 and lead["content_status"] == "verified"
    context = generation_context(SimpleNamespace(payload={}), [lead])
    assert len(context["sources"]) == 2
    source = context["sources"][1]
    assert source["url"] == lead["primary_document_url"]
    assert "Revenue increased" in source["claim"] and "truncated" in source["claim"]
    assert len(source["claim"]) <= 2000
    assert source["retrieved_at"] == lead["content_retrieved_at"]


@pytest.mark.parametrize("status", [302, 403, 500])
async def test_content_errors_never_follow_redirects(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ValueError):
            await SECSubmissions().fetch_content(LEAD, "App test@example.com", AT, client)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 503])
async def test_content_backoff_is_not_retried(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status,
            headers={"Retry-After": "1800"}))) as client:
        with pytest.raises(SECBackoff) as error:
            await SECSubmissions().fetch_content(LEAD, "App test@example.com", AT, client)
    assert error.value.seconds == 1800


@pytest.mark.parametrize("data,ctype", [(b"a" * 2_000_001, "text/plain"), (b"binary", "application/pdf"), (b"tiny", "text/html")])
async def test_content_limits_fail_closed(data, ctype):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            content=data, headers={"Content-Type": ctype}))) as client:
        with pytest.raises(ValueError):
            await SECSubmissions().fetch_content(LEAD, "App test@example.com", AT, client)


@pytest.mark.parametrize("backoff", [False, True])
async def test_cycle_content_budget_and_backoff_preserve_metadata(configured, settings, monkeypatch, backoff):
    import asyncio
    async def no_sleep(seconds):
        assert seconds == 1
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with configured.session() as session:
        state = session.get(State, 1)
        state.account_binding = "synthetic-binding"
        state.config = {"research_sources": [{"symbol": "TEST", "cik": "123"}],
                        "research_sec_user_agent": "Synthetic App test@example.com"}
    class Adapter:
        calls = 0
        async def fetch(self, *args):
            return [{"source_id": "sec:" + str(i), "symbol": "TEST", "form": "8-K",
                     "primary_document_url": URL, "accepted_at": AT.isoformat(), "content_status": "not_requested"}
                    for i in range(5)]
        async def fetch_content(self, *args):
            self.calls += 1
            if backoff:
                raise SECBackoff(1800)
            raise ValueError("content unavailable")
    adapter = Adapter()
    snapshot = SimpleNamespace(id=str(uuid4()), account_binding="synthetic-binding", generation=0,
                               observed_at=now(), payload={})
    assert await run_research_cycle(configured, settings, snapshot, adapter) == "research_only"
    assert adapter.calls == (1 if backoff else 2)
    with configured.session() as session:
        report = session.scalar(select(Analysis)).payload
        assert len(report["leads"]) == 5
        assert report["source_errors"]
        assert ("next_source_attempt_at" in report) == backoff
        assert report["outcome"] == "no_trade" and report["live_order_count"] == 0
    if backoff:
        assert await run_research_cycle(configured, settings, snapshot, adapter) == "source_backoff"


@pytest.mark.parametrize("url", ["http://127.0.0.1/private", "https://www.sec.gov.evil.test/report.htm", URL.replace("report.htm", "a..htm"), URL + "?redirect=evil"])
async def test_content_adapter_revalidates_fixed_archive_url_before_network(url):
    def fail(request):
        pytest.fail("Unsafe content URL must not reach the network")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ValueError):
            await SECSubmissions().fetch_content({"primary_document_url": url}, "App test@example.com", AT, client)


def test_context_preserves_bounded_primary_body_beyond_header():
    from observatory.research import generation_context
    lead = {**LEAD, "symbol": "TEST", "source_id": "sec:fixture", "form": "8-K", "url": "https://data.sec.gov/submissions/CIK0000000123.json", "accepted_at": AT.isoformat(), "retrieved_at": AT.isoformat(), "retrieval_status": "verified",
            "claim": "SEC metadata describes a synthetic filing.", "content_status": "verified",
            "content_url": URL, "content_retrieved_at": AT.isoformat(),
            "content_excerpt": "Header " * 300 + "Material synthetic primary disclosure. " * 400}
    context = generation_context(SimpleNamespace(payload={}), [lead])
    excerpt = context["untrusted_filing_excerpts"][0]
    assert "Material synthetic primary disclosure" in excerpt["text"]
    assert len(excerpt["text"]) <= 12000
    assert excerpt["incomplete"] is True
    assert excerpt["source_id"].endswith(":content")
