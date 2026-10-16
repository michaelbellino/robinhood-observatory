# Native connection helper — 2026-10-16

The direct Railway HTTPS consent flow is still blocked before its callback. A genuine native helper is now implemented as an alternative enrollment path. **Live consent, token issuance and hosted refresh for this path are not yet verified.** Its existence is not proof that Robinhood approves every hosted integration.

## Why this path

Robinhood's [current onboarding guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) explicitly includes localhost platforms. RFC 8252 [defines native loopback authorization](https://www.rfc-editor.org/info/rfc8252/). A [first-hand NexusTrade report](https://nexustrade.io/blog/robinhood-agentic-trading-mcp-review-20260708) describes successful local consent and refresh alongside failed hosted consent. [Socratic-Trade's connection guide](https://github.com/Simple-With-Us/Socratic-Trade/blob/main/docs/robinhood-connection-guide.md) reports the same redirect restriction, but proposes manually changing or pasting a failed callback. We do not implement that method, its unverified support addresses/scopes, or its claim of indefinite connection lifetime.

Our helper runs on the computer containing the browser, registers honestly under its own name as a native public client, receives the callback on an actual loopback listener and exchanges its own fresh authorization code locally. The hosted service receives a narrowly authorized credential deposit only after the owner approves that exact helper. There is no tunnel, copied session, third-party token cache, unofficial login, or borrowed client identity. The native helper implements no brokerage tools.

## Owner action

On a desktop computer with Python 3.10 or newer, run the repository's standalone script:

```sh
python3 scripts/connect_robinhood.py
```

On Windows with the Python launcher:

```powershell
py scripts/connect_robinhood.py
```

No Python packages need installation. It opens the deployed Observatory automatically. Sign in with the configured owner account if prompted, select **Authorize this helper once**, and complete Robinhood's own login and Allow screen promptly. The browser and helper must be on the same computer; this is not an iPad-only connection method. Close the helper to cancel. Its listener closes after completion or ten minutes.

No credentials or callback URLs need to be copied into chat. After storage is confirmed, return to Observatory and select **Verify account**. Select only the intended agent account after reviewing its returned identity, eligibility, type and options level. Monitoring remains paused until that selection and a deliberate resume. If Robinhood requires a different account or refuses native consent, stop and record the actual blocker rather than opening/funding an account automatically.

For another isolated deployment, `--app https://your-own-observatory.example` selects its HTTPS origin. This is the destination that will store the newly issued credentials; never point it at another party's service. Credentials are never accepted through command-line arguments.

## Security and recovery

- The listener binds `127.0.0.1` on an operating-system-selected port. Host, Origin, local CSRF, state, PKCE, issuer, duplicate callback parameters, and expiration checks are enforced. Registration declares `application_type=native` and `token_endpoint_auth_method=none`.
- The owner-authenticated approval uses existing CSRF protection. A random ten-minute ticket and a helper-held proof authorize only one credential deposit. The ticket cannot read status, call MCP, pause/resume, or overwrite an existing connection. New enrollment invalidates an earlier pending helper ticket.
- The ticket travels to the local page in a URL fragment, which is removed from browser history before a same-origin POST. Brokerage credentials travel only in HTTPS POST bodies to the configured service; provider redirects are not followed by the HTTP client. The loopback callback code is never relayed to the hosted callback.
- The server encrypts tokens in PostgreSQL using the existing vault, records only non-sensitive audit outcomes, and serializes deposits. A repeated deposit can acknowledge prior success but cannot replace credentials. No new database migration is required.
- The helper keeps credentials only in memory and has no request logging. The worker uses the persisted original client identity during refresh. Existing source/runtime read allowlists still exclude previews and trading mutations.
- If credential deposit is interrupted, check the authenticated dashboard before retrying; do not assume failure or success. If connected, verify the account. If not connected, restart the helper for fresh consent. Revocation is still performed through Robinhood's supported controls; deleting local credentials is not a claim of broker-side revocation.
- Roll back the web service to the prior immutable release if necessary. Native credentials use the existing token schema and client binding. Keep monitoring paused during any uncertain reconnect/recovery.

## Verified vs pending

On October 16, the helper's actual unauthenticated discovery and native dynamic-registration requests succeeded. The returned client hash matches the shared default client from the failed hosted attempt. This proves response compatibility only, not consent acceptance. Synthetic tests cover the full native PKCE flow over a real local HTTP listener into the authenticated app and encrypted vault, failure boundaries, replay rejection, and subsequent refresh using persisted client binding. PostgreSQL concurrency is covered by CI. No real brokerage mutation or user credential was used in these tests.

Still required: one real owner-approved native consent, account re-verification, hosted reads and refresh/restart evidence. Production backups, external heartbeat delivery and full-session observation remain separate acceptance gates. The helper adds no paid service or recurring local process.
