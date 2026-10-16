"""Shared PostgreSQL reservation; pace all web and worker broker requests across restarts."""

import asyncio
from datetime import timedelta
from sqlalchemy import select
from .db import RequestBudget, now, aware


class RequestLimiter:
    def __init__(self, db, rpm, sleep=asyncio.sleep):
        self.db, self.rpm, self.sleep = db, rpm, sleep

    def reserve(self):
        with self.db.session() as s:
            row = s.scalar(select(RequestBudget).where(RequestBudget.id == 1).with_for_update())
            current = now()
            slot = max(current, aware(row.next_allowed) or current)
            row.next_allowed = slot + timedelta(seconds=60 / self.rpm)
            return max(0, (slot - current).total_seconds())

    async def acquire(self):
        await self.sleep(self.reserve())

    def defer(self, seconds):
        with self.db.session() as s:
            row = s.scalar(select(RequestBudget).where(RequestBudget.id == 1).with_for_update())
            row.next_allowed = max(aware(row.next_allowed) or now(), now() + timedelta(seconds=seconds))
