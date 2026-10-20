"""Only explicit broker reads are permitted. Model output never reaches this adapter."""

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
from jsonschema import Draft202012Validator

ENDPOINT = "https://agent.robinhood.com/mcp/trading"
ALLOWED = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_option_positions",
        "get_option_orders",
        "get_option_quotes",
        "get_option_instruments",
        "get_option_chains",
        "get_alert_log",
        "get_alerts",
        "get_crypto_positions",
        "get_crypto_orders",
        "get_crypto_quotes",
        "get_advanced_orders",
    }
)
ACCOUNT_READS = frozenset(
    {
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_option_positions",
        "get_option_orders",
        "get_crypto_positions",
        "get_crypto_orders",
        "get_advanced_orders",
    }
)


class BrokerError(Exception):
    code = "broker_error"


class AuthorizationExpired(BrokerError):
    code = "authorization_expired"


class MalformedData(BrokerError):
    code = "malformed_data"


class SessionExpired(BrokerError):
    code = "session_expired"


class Throttled(BrokerError):
    code = "throttled"

    def __init__(self, retry_after):
        super().__init__("Broker requested backoff")
        self.retry_after = retry_after


def retry_delay(value, attempt=0, at=None):
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(
                    0.0, (parsedate_to_datetime(value) - (at or datetime.now(timezone.utc))).total_seconds()
                )
            except (ValueError, TypeError):
                pass
    return min(900, 5 * 2 ** min(attempt, 8)) + random.uniform(0, 3)


class ReadOnlyBroker:
    allowed = ALLOWED
    account_reads = ACCOUNT_READS
    client_name = "robinhood-observatory-read-only"
    def __init__(self, token, account, rpm=6, client=None, sleep=asyncio.sleep):
        self.token, self.account, self.rpm = token, account, rpm
        self.rhs_account = None
        self.limiter = None
        self.client = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.session_id = None
        self.sequence = 0
        self.schemas = {}
        self.next_request = 0.0
        self.sleep = sleep
        self.metrics = {"requests": 0, "throttles": 0, "elapsed_ms": 0}
        self.preflight = None
        self.rate_headers = {}

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def rpc(self, method, params=None, notification=False):
        async with asyncio.timeout(65):
            return await self._rpc(method, params, notification)

    async def _rpc(self, method, params=None, notification=False):
        if method not in {"initialize", "notifications/initialized", "tools/list", "tools/call"}:
            raise PermissionError("MCP method denied")
        if method == "tools/call":
            self.validate_call(params["name"], params.get("arguments", {}))
        if self.limiter is not None:
            await self.limiter.acquire()
        await self.sleep(max(0, self.next_request - time.monotonic()))
        # Recheck leadership, pause and credentials after waiting, immediately before I/O.
        if self.preflight is not None:
            await self.preflight()
        self.next_request = time.monotonic() + 60 / self.rpm
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = self.sequence
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        started = time.monotonic()
        self.metrics["requests"] += 1
        try:
            async with self.client.stream("POST", ENDPOINT, json=body, headers=headers) as response:
                for key in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset",
                            "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
                    value = response.headers.get(key, "")
                    if value.isascii() and value.isdigit() and len(value) <= 16:
                        self.rate_headers[key] = int(value)
                if response.status_code in (401, 403):
                    raise AuthorizationExpired()
                if response.status_code == 429:
                    self.metrics["throttles"] += 1
                    delay = retry_delay(response.headers.get("Retry-After"))
                    if self.limiter is not None:
                        self.limiter.defer(delay)
                    raise Throttled(delay)
                if response.status_code == 404 and self.session_id:
                    self.session_id = None
                    self.schemas = {}
                    raise SessionExpired()
                if response.status_code >= 400:
                    raise BrokerError()  # Do not log URLs, response bodies, tokens, or account IDs.
                self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                if notification:
                    return {}
                size = 0
                if "text/event-stream" in response.headers.get("content-type", ""):
                    fragments = []
                    async for line in response.aiter_lines():
                        size += len(line)
                        if size > 4 * 1024 * 1024:
                            raise MalformedData()
                        if line.startswith("data:"):
                            fragments.append(line[5:].lstrip())
                        elif line == "" and fragments:
                            obj = json.loads("\n".join(fragments))
                            fragments = []
                            if obj.get("id") == body["id"]:
                                return self.result(obj)
                    raise MalformedData()
                chunks = []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise MalformedData()
                    chunks.append(chunk)
                obj = json.loads(b"".join(chunks))
                if obj.get("id") != body["id"]:
                    raise MalformedData()
                return self.result(obj)
        except (ValueError, KeyError, httpx.HTTPError):
            raise MalformedData() from None
        finally:
            self.metrics["elapsed_ms"] += round((time.monotonic() - started) * 1000)

    def result(self, obj):
        if "error" in obj or "result" not in obj:
            raise BrokerError()
        return obj["result"]

    async def initialize(self):
        await self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "0.1.0"},
            },
        )
        await self.rpc("notifications/initialized", notification=True)
        cursor = None
        seen = set()
        for _ in range(20):
            result = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools", []):
                if tool["name"] in self.allowed:
                    self.schemas[tool["name"]] = tool["inputSchema"]
            cursor = result.get("nextCursor")
            if not cursor:
                return
            if cursor in seen:
                break
            seen.add(cursor)
        raise MalformedData()

    def validate_call(self, name, args):
        if name not in self.allowed or name not in self.schemas:
            raise PermissionError("Broker tool is absent or not allowlisted")
        if name in {"get_crypto_positions", "get_crypto_orders"}:
            if not self.rhs_account or args.get("rhs_account_number") != self.rhs_account:
                raise PermissionError("Cross-account crypto read denied")
        elif name in self.account_reads and args.get("account_number") != self.account:
            raise PermissionError("Cross-account read denied")
        if name in {"get_alerts", "get_alert_log"}:
            # These may be user-wide. No silent aggregation of other accounts.
            schema = self.schemas[name]
            if (
                "account_number" not in schema.get("properties", {})
                or args.get("account_number") != self.account
            ):
                raise PermissionError("Alert tool lacks verified account scoping")
        Draft202012Validator(self.schemas[name]).validate(args)

    async def call(self, name, args):
        self.validate_call(name, args)
        result = await self.rpc("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise BrokerError()
        data = result.get("structuredContent")
        if data is None:
            texts = [x["text"] for x in result.get("content", []) if x.get("type") == "text"]
            if len(texts) != 1:
                raise MalformedData()
            try:
                data = json.loads(texts[0])
            except ValueError:
                raise MalformedData() from None
        data = data.get("data", data)
        if not isinstance(data, dict):
            raise MalformedData()
        return data

    async def pages(self, name, key, args):
        rows, seen = [], set()
        params = dict(args)
        for _ in range(50):
            data = await self.call(name, params)
            page = data.get(key)
            if not isinstance(page, list) or any(not isinstance(x, dict) for x in page):
                raise MalformedData()
            rows.extend(page)
            cursor = data.get("next")
            if not cursor:
                return rows
            if cursor in seen:
                raise MalformedData()
            seen.add(cursor)
            params["cursor"] = cursor
        raise MalformedData()


class PreviewBroker(ReadOnlyBroker):
    """Explicit previews only. Never order placement, replacement, or cancellation."""
    allowed = ALLOWED | {"review_equity_order", "review_option_order"}
    account_reads = ACCOUNT_READS | {"review_equity_order", "review_option_order"}
