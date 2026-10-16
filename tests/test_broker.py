from datetime import timedelta
import httpx
import pytest
from observatory.broker import ReadOnlyBroker, AuthorizationExpired, Throttled, MalformedData, retry_delay
from observatory.db import now


async def no_sleep(seconds):
    pass


@pytest.mark.parametrize(
    "status,exception", [(401, AuthorizationExpired), (403, AuthorizationExpired), (429, Throttled)]
)
async def test_http_failures(status, exception):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, headers={"Retry-After": "1800"}))
    ) as client:
        b = ReadOnlyBroker("synthetic", "synthetic", client=client, sleep=no_sleep)
        with pytest.raises(exception) as e:
            await b.rpc("initialize")
        if status == 429:
            assert e.value.retry_after == 1800  # Do not shorten a server-supplied delay.


def test_http_date_retry_after():
    at = now().replace(microsecond=0)
    future = at + timedelta(seconds=75)
    assert retry_delay(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), at=at) == 75


def test_bounded_backoff():
    assert 900 <= retry_delay(None, 100) <= 903


async def test_null_collection_not_empty():
    b = ReadOnlyBroker("synthetic", "synthetic")

    async def fake(*args):
        return {"positions": None}

    b.call = fake
    with pytest.raises(MalformedData):
        await b.pages("get_equity_positions", "positions", {})
    await b.close()


async def test_pagination_complete_and_loop_rejected():
    b = ReadOnlyBroker("synthetic", "synthetic")

    async def fake(name, args):
        return {"positions": [{"quantity": "1"}], "next": "same"}

    b.call = fake
    with pytest.raises(MalformedData):
        await b.pages("get_equity_positions", "positions", {})
    await b.close()


async def test_sse_response():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n',
            )
        )
    ) as c:
        b = ReadOnlyBroker("synthetic", "synthetic", client=c, sleep=no_sleep)
        assert await b.rpc("initialize") == {"ok": True}


async def test_rate_limit_pacing():
    delays = []

    async def record(seconds):
        delays.append(seconds)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"id": __import__("json").loads(req.content)["id"], "result": {}}
            )
        )
    ) as c:
        b = ReadOnlyBroker("synthetic", "synthetic", rpm=6, client=c, sleep=record)
        await b.rpc("initialize")
        await b.rpc("tools/list")
        assert delays[1] > 9.5


async def test_response_size_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1)))
    ) as c:
        b = ReadOnlyBroker("synthetic", "synthetic", client=c, sleep=no_sleep)
        with pytest.raises(MalformedData):
            await b.rpc("initialize")
