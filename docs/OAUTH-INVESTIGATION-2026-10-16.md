# OAuth consent investigation — 2026-10-16

## Observed, not inferred

- The owner completed GitHub sign-in. Production `/auth/callback` returned 303 at 17:09:12 and 17:14:11 UTC, followed by authenticated API reads.
- The hosted `/api/robinhood/connect` returned 200 at 17:09:16, 17:13:24, 17:14:19 and 17:21:48 UTC. No `/oauth/robinhood/callback` request appeared in the inspected production HTTP logs covering these attempts. Log inspection retained only paths without queries, status and timestamps.
- The owner reports that clicking Allow produces Robinhood's generic connection error.
- Official metadata advertises `https://robinhood.com/oauth`, public clients (`none`), PKCE S256, code/refresh grants, and `internal` scope. The generated authorization request includes the exact hosted redirect, these parameters, state and the Trading MCP resource. No separate read-only scope is advertised.
- Two unauthenticated registration requests with different honest application names returned HTTP 200, the same client identifier (SHA-256 prefix `b1c8cf32daad`), the generic name `Robinhood Trading`, and an echo of the requested hosted callback. This identifier also appears as a default Agent Gateway client in Robinhood's public page bundle. A registration response is **not proof that the consent service accepts its echoed redirect**.
- Public Robinhood page code maps this client to its Trading MCP onboarding flow. Its Allow handler submits consent, then navigates to the returned URI only after a successful response. A caught submit failure routes to `/oauth/error`, which displays the exact error the owner reported. The MCP consent flow preserves PKCE parameters. Its request builder does not forward the resource parameter; no causal conclusion is drawn from that observation.

## Diagnosis boundary

The failure is before the application's callback, in the browser-to-Robinhood consent step. It can still be caused by our client/redirect configuration. A shared-client redirect restriction is a hypothesis, **not a confirmed root cause**. Other broker validation or verification failures can produce the same page. Neither a broker outage nor an account eligibility failure is established.

The next discriminating evidence is the failed consent request's HTTP status and non-secret error `detail`/`error` value. This exists in the owner's browser Network response, not in Railway's logs. Do not request/export a HAR, cookies, request headers, tokens, authorization codes, full authorization URLs, or unrelated account responses. Do not impersonate a known platform's client, borrow its redirect, weaken issuer/PKCE validation, or invoke private login/consent endpoints as a workaround.

## Application corrections

- OAuth callbacks now accept error responses and missing fields without FastAPI's generic 422. Errors use fixed, specific, sanitized messages. State, owner and issuer validation remain mandatory.
- Each attempt persists a separate diagnostic UUID, stage and outcome in the audit table. The owner-control endpoint `/api/robinhood/diagnostics` reports safe stages, configured redirect, a client-ID hash prefix and whether credentials have been saved. Read-only MCP credentials and anonymous requests cannot access it.
- A flow retains its initiating client ID along with its encrypted PKCE verifier and redirect. Token refresh retains the credential's client binding. A later registration change cannot silently alter an outstanding attempt.
- Used states are consumed even on validation/exchange failures. Ambiguous token-exchange timeouts are never automatically replayed.
- No raw provider error description, authorization code, state, verifier, token, credential or account number is logged or put in diagnostics. SDK/provider logging and application access logs remain disabled.

## Verification

Local result: **110 passed, 5 PostgreSQL tests skipped** because local PostgreSQL is unavailable. The existing GitHub verification workflow runs those five tests against its isolated PostgreSQL service; inspect the workflow for this change's exact commit before deployment. The previous deployed implementation passed all 94 then-existing tests in CI. New cases cover the complete PKCE exchange and restart recovery, client binding through refresh, wrong owner/issuer, expired/reused state, consent denial, malformed callback/token, timeouts, failed exchange, redacted errors, owner-only diagnostics and paused/unbound state after authorization. Source scan and Ruff pass.

These are synthetic protocol tests. Live hosted consent, credentials, refresh and account reads remain **unverified**. Monitoring remains paused; no broker mutations, previews or trades were invoked.

## Primary public source evidence

Retrieved directly from Robinhood on 2026-10-16 without authentication, cookies or user sessions. Vendor bundles are not copied into this repository.

| Official source | SHA-256 of inspected bytes |
| --- | --- |
| [App bundle](https://cdn.robinhood.com/assets/generated_assets/webapp/App-1bfbf4606087728e.js) | `b4be28d5ce429971a231f0cccf36e371d7583ae028e44ad153d11ac79fae3f60` |
| [OAuth page](https://cdn.robinhood.com/assets/generated_assets/webapp/42899-a49eb36e83b9fe00.chunk.js) | `41965839e329b09e54f980be5db27c60550283329bf62cbbd5197b0d555cd176` |
| [MCP router](https://cdn.robinhood.com/assets/generated_assets/webapp/McpRouter-edc2037de5f79472.chunk.js) | `23a4d7e5564c5e435941a1e3479f53843416815eaf492553bd5ab67d376d9c8b` |
| [Trading consent](https://cdn.robinhood.com/assets/generated_assets/webapp/AgenticTradingOnboarding-4996f4d781fcb2b6.chunk.js) | `4ea1432564e2a5bc180b27d1b9418008e5d06d432ed6a8fe4bed2dfdaceb4ac4` |

Metadata: [authorization server](https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading), [protected resource](https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading).

## Released and verified

Commit `936a4303904dbc9df4cc43a5788cb090f8f5a2c8` passed [CI run 38941182829](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38941182829): **115 passed**, including PostgreSQL tests, plus source scan, infrastructure checks and dependency audit. Web deployment `27420e55-2f04-4c86-82c4-0a9db8471c17` and worker deployment `c4a24d1c-38d6-40cf-a421-728185da9136` succeeded with that exact commit. The deployed liveness endpoint returns 200 and anonymous status/diagnostics/read-MCP/control-MCP requests return 401. This does not prove that Robinhood consent succeeds; its error response is still needed. Rollback uses the previous immutable runtime commit `0b82049a221707987405af48c4b22b448c8994c8` for both services; database changes are not involved.
