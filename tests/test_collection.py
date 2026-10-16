"""Synthetic schema-shaped records; never captured financial account fixtures."""

from datetime import timedelta
import pytest
from observatory.collection import collect
from observatory.broker import MalformedData
from observatory.db import now


class SchemaBroker:
    account = "SYNTHETIC-ACCOUNT"
    schemas = {}

    async def call(self, name, args):
        if name == "get_accounts":
            return {
                "accounts": [
                    {
                        "account_number": self.account,
                        "state": "active",
                        "agentic_allowed": True,
                        "type": "limited_margin",
                        "option_level": "option_level_3",
                    },
                    {"account_number": "OTHER", "state": "active", "agentic_allowed": False},
                ]
            }
        if name == "get_portfolio":
            return {"total_value": "100", "buying_power": {"buying_power": "25"}}
        if name == "get_equity_quotes":
            return {
                "results": [
                    {
                        "quote": {
                            "symbol": "TEST",
                            "bid_price": "2.00",
                            "ask_price": "2.20",
                            "venue_bid_time": now().isoformat(),
                            "venue_ask_time": now().isoformat(),
                            "has_traded": True,
                            "venue_last_trade_time": now().isoformat(),
                            "last_trade_price": "2.10",
                        }
                    }
                ]
            }
        if name == "get_option_quotes":
            return {
                "results": [
                    {
                        "quote": {
                            "instrument_id": "OPTION-TEST",
                            "bid_price": "1.00",
                            "ask_price": "1.30",
                            "updated_at": (now() - timedelta(minutes=20)).isoformat(),
                            "delta": ".5",
                            "open_interest": None,
                        }
                    }
                ]
            }
        raise AssertionError("Unexpected read")

    async def pages(self, name, key, args):
        if name.endswith("_positions"):
            if name == "get_equity_positions":
                return [{"symbol": "TEST", "quantity": "2"}]
            return [{"option_id": "OPTION-TEST", "quantity": "1", "type": "short"}]
        if name.endswith("_orders"):
            return []
        if name == "get_option_instruments":
            return [
                {
                    "id": "OPTION-TEST",
                    "chain_symbol": "TEST",
                    "expiration_date": "2026-10-23",
                    "strike_price": "100",
                    "type": "call",
                    "trade_value_multiplier": "10",
                }
            ]
        raise AssertionError("Unexpected read")


async def test_collection_retains_option_units_timestamps_and_unknowns():
    data = await collect(SchemaBroker(), {"watchlist": []})
    assert data["scope"] == "selected_account_only"
    assert data["contracts"][0]["multiplier"] == "10"
    assert data["contracts"][0]["deliverable"] is None
    assert data["contracts"][0]["exercise_style"] is None
    assert data["option_quotes"][0]["open_interest"] is None
    assert data["option_quotes"][0]["quoted_at"]
    assert data["portfolio_as_of"] is None
    assert data["open_orders_complete"] is False
    assert "OTHER" not in str(data)


async def test_null_portfolio_cannot_become_zero():
    broker = SchemaBroker()
    original = broker.call

    async def call(name, args):
        if name == "get_portfolio":
            return {"total_value": None, "buying_power": None}
        return await original(name, args)

    broker.call = call
    with pytest.raises(MalformedData):
        await collect(broker, {"watchlist": []})


async def test_selected_account_permission_reverified():
    broker = SchemaBroker()
    original = broker.call

    async def call(name, args):
        data = await original(name, args)
        if name == "get_accounts":
            data["accounts"][0]["agentic_allowed"] = False
        return data

    broker.call = call
    with pytest.raises(PermissionError):
        await collect(broker, {"watchlist": []})
