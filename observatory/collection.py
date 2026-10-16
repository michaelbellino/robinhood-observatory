from decimal import Decimal
from .broker import MalformedData
from .domain import quote_from_broker, contract_from_broker
from .db import now

TERMINAL = {
    "filled",
    "cancelled",
    "canceled",
    "rejected",
    "failed",
    "voided",
    "partially_filled_rest_cancelled",
    "locate_failed",
}


def validate_rows(rows, required):
    for row in rows:
        if not all(row.get(k) is not None for k in required):
            raise MalformedData()
        if "quantity" in required:
            try:
                if not Decimal(str(row["quantity"])).is_finite():
                    raise MalformedData()
            except Exception:
                raise MalformedData() from None
    return rows


async def collect(broker, config):
    account = broker.account
    accounts = await broker.call("get_accounts", {})
    matches = [
        a for a in accounts.get("accounts", []) if isinstance(a, dict) and a.get("account_number") == account
    ]
    if len(matches) != 1 or matches[0].get("state") != "active" or matches[0].get("deactivated"):
        raise MalformedData()
    selected = matches[0]
    # Explicitly bound account only. Caller-relative agent access must be independently reverified.
    if selected.get("agentic_allowed") is not True:
        raise PermissionError("Selected account not accessible to this independently authenticated client")
    portfolio = await broker.call("get_portfolio", {"account_number": account})
    if "total_value" not in portfolio or not isinstance(portfolio.get("buying_power"), dict):
        raise MalformedData()
    try:
        for value in [portfolio["total_value"], portfolio["buying_power"]["buying_power"]]:
            if not Decimal(str(value)).is_finite():
                raise ValueError()
    except (KeyError, ValueError, ArithmeticError):
        raise MalformedData() from None
    result = {
        "portfolio": portfolio,
        "account": {
            k: selected.get(k)
            for k in ("type", "option_level", "user_option_level", "state", "brokerage_account_type")
        },
        "observed_at": now().isoformat(),
        "portfolio_as_of": None,
        "source": "robinhood_mcp_live",
        "warnings": [],
        "scope": "selected_account_only",
    }
    for asset in ["equity", "option"]:
        kwargs = {"account_number": account}
        if asset == "option":
            kwargs["nonzero"] = True
        rows = await broker.pages(f"get_{asset}_positions", "positions", kwargs)
        result[f"{asset}_positions"] = validate_rows(
            rows, ["quantity", "symbol" if asset == "equity" else "option_id"]
        )
        orders = await broker.pages(f"get_{asset}_orders", "orders", {"account_number": account})
        result[f"{asset}_orders"] = validate_rows(orders, ["id", "state"])
    result["advanced_orders"] = None
    if "get_advanced_orders" in broker.schemas:
        result["advanced_orders"] = await broker.pages(
            "get_advanced_orders", "orders", {"account_number": account}
        )
    else:
        result["warnings"].append("Advanced/grouped orders unavailable: total open-order coverage incomplete")
    result["crypto_positions"] = result["crypto_orders"] = None
    # Numeric crypto identifier comes only from the explicitly matched brokerage account.
    if selected.get("rhs_account_number") and selected.get("rhc_account_number"):
        broker.rhs_account = selected["rhs_account_number"]
        for name, key in [
            ("get_crypto_positions", "crypto_positions"),
            ("get_crypto_orders", "crypto_orders"),
        ]:
            if name in broker.schemas:
                result[key] = await broker.pages(name, "results", {"rhs_account_number": broker.rhs_account})
    if result["crypto_positions"] is None:
        result["warnings"].append("Crypto positions/orders coverage is unknown")
    for field in ["futures_value", "event_contracts_value", "fixed_income_value", "mutual_funds_value"]:
        if portfolio.get(field) is not None and Decimal(str(portfolio[field])) != 0:
            result["warnings"].append(f"{field}: contract/order details unsupported")
    symbols = sorted({x["symbol"] for x in result["equity_positions"]} | set(config.get("watchlist", [])))
    if len(config.get("watchlist", [])) > 20 or len(symbols) > 200:
        raise MalformedData()
    result["quotes"] = []
    for start in range(0, 0 if getattr(broker, "independent_quotes", False) else len(symbols), 20):
        batch = symbols[start : start + 20]
        data = await broker.call("get_equity_quotes", {"symbols": batch})
        rows = data.get("results")
        if not isinstance(rows, list):
            raise MalformedData()
        quotes = [quote_from_broker(row, now()) for row in rows]
        if {q.instrument_id for q in quotes} != set(batch):
            result["warnings"].append("Some requested equity quotes are unavailable")
        result["quotes"].extend(q.model_dump(mode="json") for q in quotes)
    ids = sorted({x["option_id"] for x in result["option_positions"]})
    result["contracts"] = []
    result["option_quotes"] = []
    chains = {}
    chain_ids = sorted({x["chain_id"] for x in result["option_positions"] if x.get("chain_id")})
    for start in range(0, len(chain_ids), 20):
        data = await broker.call("get_option_chains", {"ids": ",".join(chain_ids[start : start + 20])})
        if not isinstance(data.get("chains"), list):
            raise MalformedData()
        chains.update({c["id"]: c for c in data["chains"]})
    for start in range(0, len(ids), 20):
        batch = ids[start : start + 20]
        rows = await broker.pages("get_option_instruments", "instruments", {"ids": ",".join(batch)})
        if {x["id"] for x in rows} != set(batch):
            result["warnings"].append("Some held option specifications are unknown")
        for item in rows:
            contract = contract_from_broker(item, now()).model_dump(mode="json")
            chain = chains.get(item.get("chain_id"), {})
            contract["settle_on_open"] = chain.get("settle_on_open")
            result["contracts"].append(contract)
        if not getattr(broker, "independent_quotes", False):
            data = await broker.call("get_option_quotes", {"instrument_ids": batch})
            if not isinstance(data.get("results"), list):
                raise MalformedData()
            quotes = [quote_from_broker(row, now(), options=True) for row in data["results"]]
            result["option_quotes"].extend(q.model_dump(mode="json") for q in quotes)
            if {q.instrument_id for q in quotes} != set(batch):
                result["warnings"].append("Some held option quotes are unknown")
    result["quote_collection"] = "independent_cache" if getattr(broker, "independent_quotes", False) else "snapshot"
    result["alerts"] = None
    if "account_number" in broker.schemas.get("get_alert_log", {}).get("properties", {}):
        result["alerts"] = await broker.call("get_alert_log", {"account_number": account})
    else:
        result["warnings"].append(
            "Broker alert events are user-wide unless account-scoped schema is advertised"
        )
    result["option_chain_characteristics"] = [
        {
            k: c.get(k)
            for k in [
                "id",
                "symbol",
                "settle_on_open",
                "extended_hours_state",
                "late_close_state",
                "cash_component",
                "trade_value_multiplier",
            ]
        }
        for c in chains.values()
    ]
    result["open_orders"] = [
        x for key in ["equity_orders", "option_orders"] for x in result[key] if x["state"] not in TERMINAL
    ]
    result["open_orders_complete"] = False  # Unknown crypto/grouped orders must never become zero.
    result["option_structures"] = [
        {"source_order_id": x["id"], "legs": x.get("legs"), "structure": "unknown", "source": "broker_order"}
        for x in result["option_orders"]
    ]
    return result
