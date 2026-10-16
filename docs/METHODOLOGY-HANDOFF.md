# Methodology phase handoff

Version 0.1 deliberately has no investment strategy. `ObserveOnly` (`observe-only/v1`) returns an empty proposal list. A later version can implement `Strategy.proposals(snapshot) -> list[Paper]`, but that does not create an execution permission or a brokerage mutation route.

Before developing methodology, complete independent hosted auth, PostgreSQL/recovery, external detection, authorization and full-session acceptance gates. Establish input availability and market-data terms for the instruments actually considered. Agree on objectives, allowed instruments/structures, horizons, uncertainty, exposure constraints and evaluation criteria separately with the owner; do not infer them from the test balance, account nickname or options approval.

Keep collection, arithmetic, calendar/freshness validation, budget/limits and paper accounting deterministic. Restrict the model to bounded structured commentary supported by stored snapshot/source references. News and research sources are untrusted content, not instructions. Expand minimized model context only deliberately; the current adapter sends symbols/contract attributes/warnings without account numbers, balances or raw records.

Paper proposals are stored in `paper_proposals`, never `orders`. Each leg carries the contract, side, quantity and position effect. Structure validation covers singles, verticals, calendars, iron condors and rolls; unsupported/custom forms remain unsupported for brokerage execution. Do not infer a strategy from independently held legs. Retain broker legs and complete structures separately.

The paper price helper uses executable bid/ask sides plus explicit estimated fees/slippage; stale or incomplete data makes estimated cost unknown. It never creates a fill. Buying-power impact is unknown until a documented deterministic estimator or supported read-only source exists. Preserve assignment/exercise, adjusted-deliverable and settlement uncertainty. Any later simulated fill engine needs a versioned liquidity/time/queue/spread/fee model and clearly hypothetical results. Midpoint touches alone are insufficient; paper results do not establish live performance.

Real trading would be a distinct phase requiring new explicit authorization, broker capability validation, safeguards and code review. There is no hidden flag in this application that turns it on.
