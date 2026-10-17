"""Bounded public-feed alpha probe. No credentials, account access or order routes.

Run with `uv run python -m scripts.paper_feed_probe --output /private/report.json`.
Ticker event time describes a match; it is NOT a documented bid/ask timestamp.
Hypothetical intents therefore remain unfilled and final cost stays unknown.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import statistics
import time
import websockets
from observatory.domain import Quote

URL = "wss://ws-feed.exchange.coinbase.com"


def evaluate(message, received):
    quote = Quote(instrument_id="BTC-USD", bid=message["best_bid"], ask=message["best_ask"],
                  quoted_at=message["time"], received_at=received, source="coinbase_exchange_public_ticker")
    return {
        "event_age_seconds": (received-quote.quoted_at).total_seconds(),
        "crossed_or_invalid": quote.bid <= 0 or quote.ask <= 0 or quote.bid > quote.ask,
        "spread_bps": float((quote.ask-quote.bid)/((quote.ask+quote.bid)/Decimal(2))*10000)
                      if quote.ask > 0 and quote.bid > 0 else None,
        "paper": {"label":"PAPER — TECHNICAL TEST ONLY", "fill_status":"unfilled",
                  "estimated_total_cost":None, "book_freshness":quote.book_freshness(received),
                  "reason":"Bid/ask source timestamps, applicable fees and execution quality unknown"},
    }


async def probe(seconds):
    started = datetime.now(timezone.utc)
    rows, gaps, errors = [], [], []
    previous_sequence = None
    duplicate_or_out_of_order = 0
    sessions = 0
    for _ in range(2):
        # An intentional close/reconnect tests transport recovery, not brokerage recovery.
        try:
            async with websockets.connect(URL, open_timeout=15, close_timeout=5, max_size=65536,
                                          max_queue=32) as socket:
                await socket.send(json.dumps({"type":"subscribe", "product_ids":["BTC-USD"],
                                               "channels":["ticker"]}))
                sessions += 1
                until, prior = time.monotonic()+seconds/2, None
                while time.monotonic() < until:
                    message = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
                    if message.get("type") == "error":
                        raise ValueError("Feed rejected subscription")
                    if message.get("type") != "ticker" or message.get("product_id") != "BTC-USD":
                        continue
                    sequence = message["sequence"]
                    if previous_sequence is not None and sequence <= previous_sequence:
                        duplicate_or_out_of_order += 1
                        continue
                    previous_sequence = sequence
                    tick = time.monotonic()
                    if prior is not None:
                        gaps.append(tick-prior)
                    prior = tick
                    rows.append(evaluate(message, datetime.now(timezone.utc)))
        except Exception as error:
            errors.append(type(error).__name__)  # Never dump server text or connection objects.
    ages = sorted(x["event_age_seconds"] for x in rows)
    return {"started_at":started.isoformat(), "finished_at":datetime.now(timezone.utc).isoformat(),
            "source": "coinbase_exchange_public_ticker", "instrument":"BTC-USD", "sessions":sessions,
            "scope":"live public crypto feed and hypothetical paper guards; no brokerage/execution validation",
            "events":len(rows), "errors":errors, "duplicate_or_out_of_order_ignored":duplicate_or_out_of_order,
            "event_age_median_seconds":statistics.median(ages) if ages else None,
            "event_age_max_seconds":max(ages) if ages else None,
            "max_observed_event_gap_seconds":max(gaps) if gaps else None,
            "crossed_or_invalid_events":sum(x["crossed_or_invalid"] for x in rows),
            "paper_intents_checked":len(rows), "paper_fills":0,
            "paper_unknown_costs":sum(x["paper"]["estimated_total_cost"] is None for x in rows),
            "bid_ask_source_timestamp":"unknown; match event time is not substituted",
            "consolidated_or_robinhood_prices":False, "strategy_evaluated":False,
            "passed_transport_probe":sessions == 2 and len(rows) >= 2 and not errors,
            "real_money_actions":0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 20 <= args.seconds <= 900:
        parser.error("Duration must be 20..900 seconds")
    report = asyncio.run(probe(args.seconds))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2)+"\n")
    destination.chmod(0o600)
    print(json.dumps(report))
