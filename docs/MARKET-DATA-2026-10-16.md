# Market data and latency review — October 16, 2026

## Findings and decision

Keep Robinhood as the authority for the explicitly bound account. Treat its MCP quote calls as polling. No supported quote subscription or published request quota was verified in the inspected official material. HTTP/SSE transport alone does not establish a market-data streaming product.

Measure brokerage response time separately from pacing and full-cycle time. A slow serial reconciliation does not itself prove that the brokerage quote service is slow. Keep real account/deployment measurements in the owner's private evidence record.

The application cap remains six MCP HTTP requests per minute across dashboard and worker. This is a self-imposed conservative budget, **not a verified Robinhood quota**. Do not infer permission from third-party load tests or remove pacing because a few requests succeeded. Safe numeric rate-limit headers are now observed when supplied; they do not automatically raise the cap.

## Implemented improvement

The worker keeps a single MCP/HTTP session while it owns the PostgreSQL leadership connection. Tool metadata refreshes at most hourly in steady operation; session expiry triggers one bounded read-only reinitialization according to the MCP transport specification. Leadership, pause/configuration generation, and credentials are checked immediately before network I/O, after pacing.

Quotes have a separate durable PostgreSQL checkpoint/cache. A due quote batch runs between account reads and between full reconciliations. Up to 20 instruments are batched per call; batches rotate across held equities, the bounded watchlist, and held options. The default target is 30 seconds **after a batch completes**, sharing the existing cap. Multiple batches, setup, slow account requests, backoff, and reconnects extend the interval for an individual instrument. This is not real-time streaming or a guaranteed latency target. Known closed-session mappings reduce quote sampling to at least five minutes; unknown instrument hours retain the active target.

The authenticated dashboard reads that cache every five seconds without adding brokerage requests. It displays bid/ask source timestamps separately from local receipt time. Missing or malformed results retain previous values with explicit incomplete/failure status. Quote success does not advance the account reconciliation checkpoint or external heartbeat; a moving price display cannot conceal a stalled account collector. Core monitoring remains independent of model access.

## Alternatives checked against primary sources

| Source | Verified offering | Cost/access limitation | Decision |
|---|---|---|---|
| Robinhood official MCP | Equity quote batches up to 20; options quote reads | Streaming and permitted request quota not verified | Keep connected; measure the improved polling path |
| yfinance WebSocket / AsyncWebSocket | Open-source streaming client for Yahoo Finance pricing | Project explicitly says it is unofficial, for research/education, and Yahoo data is for personal use; consolidated stock/options coverage and reliable latency not verified | Candidate supplementary research feed; not installed or made a production dependency |
| Alpaca Basic | WebSocket data; IEX equities only, indicative options | $0 tier of a commercial/freemium service; authentication required; limited coverage | Does not meet the requested genuinely free consolidated stock/options requirement |
| Alpaca Algo Trader Plus | All U.S. equity exchanges and OPRA options | Listed at $99/month | Not purchased; exceeds the current project envelope |
| Tradier Brokerage API | Consolidated real-time stocks/options; HTTP and WebSocket streaming | Requires a Tradier brokerage account; Greeks listed as hourly; account fee schedule includes $50/year inactivity under two trades/year and restricted experience after 60 days unfunded | Not a no-obligation free-data substitute; no account opened or funded |

No genuinely free, licensed, consolidated U.S. equity-and-options streaming feed was verified in this review. Open-source client software does not itself supply exchange entitlements. A future provider adapter must preserve venue/feed provenance, source times, entitlement restrictions, reconnect recovery, bounded subscriptions and explicit unknowns. No unofficial Robinhood endpoints, scraped credentials, data purchase or trading permission change is part of this release.

## Closed-market verification

After-hours connectivity, source timestamps, cached-data behavior, restart recovery and failures can be tested now. Old trade timestamps after close are not automatically a transport failure; receipt time must never be substituted for exchange time. Friday evening tests do not prove regular-session quote behavior or options liquidity.

The service schedules the next complete XNYS reference session after its first successful account reconciliation. Retain its actual start/end and outcome privately. Regular-session account continuity and price freshness are distinct acceptance measures; neither proves trade execution quality.

## Primary sources inspected

- Robinhood tools: https://robinhood.com/us/en/support/articles/trading-with-your-agent/
- Robinhood onboarding: https://robinhood.com/us/en/support/articles/agentic-trading-overview/
- MCP transport/session recovery: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Alpaca market data and pricing: https://docs.alpaca.markets/us/docs/about-market-data-api
- Tradier coverage: https://docs.tradier.com/docs/market-data
- Tradier streaming: https://docs.tradier.com/docs/streaming-data
- Tradier pricing/account fees: https://tradier.com/pricing

## Verification boundaries

Synthetic tests cover missing/stale source timestamps, pause during reads, throttling, session reuse and bounded session recovery. Runtime quote measurements belong in a private deployment record. The dashboard labels bid/ask data older than 60 seconds stale and missing source timestamps unknown; this is a display threshold, not a liquidity or execution guarantee. Recent receipt never resets exchange time.

A free open-source client is documented by the yfinance project's [WebSocket reference](https://ranaroussi.github.io/yfinance/reference/api/yfinance.WebSocket.html) and [repository](https://github.com/ranaroussi/yfinance). It is an unofficial Yahoo research client; consolidated equity/options coverage, use rights and reliable latency must be established before adopting it as a production dependency. This review did not establish those properties.
