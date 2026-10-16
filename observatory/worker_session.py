"""Reusable MCP session owned exclusively by one worker leadership connection."""
import time
from .broker import ReadOnlyBroker, SessionExpired


class WorkerBroker(ReadOnlyBroker):
    independent_quotes = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initialized_at = None
        self.initializing = False
        self.before_account_call = None

    async def initialize(self):
        if self.initialized_at is not None and self.schemas and time.monotonic() - self.initialized_at < 3600:
            return
        self.session_id, self.schemas = None, {}
        self.initializing = True
        try:
            await super().initialize()
            self.initialized_at = time.monotonic()
        finally:
            self.initializing = False

    async def rpc(self, method, params=None, notification=False):
        try:
            return await super().rpc(method, params, notification)
        except SessionExpired:
            self.initialized_at = None
            if self.initializing or method not in {"tools/call", "tools/list"}:
                raise
            # MCP specifies a new initialize after session 404. Read-only retry once.
            await self.initialize()
            return await super().rpc(method, params, notification)

    async def call(self, name, args):
        if self.before_account_call and name not in {"get_equity_quotes", "get_option_quotes"}:
            await self.before_account_call()
        return await super().call(name, args)

    async def close(self):
        # A reconciliation ends, but the leader keeps the transport for quote refreshes.
        pass

    async def shutdown(self):
        await super().close()
