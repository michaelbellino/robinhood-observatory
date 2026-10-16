# Setup and independent authentication

## Owner access before exposure

The application refuses production startup without PostgreSQL, an HTTPS `PUBLIC_URL`, an explicit `OWNER_SUBJECT`, a session secret and encryption keys. It does not enroll the first visitor. No account data is embedded in the public HTML shell. All `/api/`, `/mcp/` and broker OAuth callback routes require owner authentication. Cookies are HttpOnly, SameSite=Lax and Secure in production; state-changing session requests require CSRF and matching Origin.

Use a dedicated GitHub OAuth App (not a token from ChatGPT's GitHub connector):

| Field | Value |
|---|---|
| Application name | Robinhood Observatory |
| Homepage URL | The approved HTTPS dashboard origin |
| Authorization callback URL | That origin + `/auth/callback` |
| Scope | `read:user` |
| `AUTH_PROVIDER` | `github` |
| `OWNER_SUBJECT` | Owner's verified immutable GitHub numeric ID; do not use the login name |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | Dedicated OAuth app credentials in Railway service variables |

Register at GitHub Settings → Developer settings → OAuth Apps. Authlib handles state and PKCE; the app fetches `/user` after each exchange and rejects a different immutable ID. Provider login and consent still require the owner. OIDC is an alternative (`AUTH_PROVIDER=oidc`, issuer, client ID/secret, immutable `sub`); do not mix subject IDs across providers.

Generate a random 48-byte session secret and independent Fernet key using a trusted local process. Store `ENCRYPTION_KEYS` and `SESSION_SECRET` in the new project's service variables only; keep a recovery copy in the owner's password manager. Do not reuse credentials from another application. The database contains encrypted broker tokens and account binding, but financial snapshots themselves rely on database/volume access protection. Use encrypted exports; do not publish dumps.

Local development: `uv run python -m scripts.init_local` creates mode-0600 `.env` from `.env.example`, refuses overwrite, prints no secrets. Set owner/auth configuration privately. Docker Compose injects its generated PostgreSQL password and keeps database/worker off public ports. Production must use `APP_ENV=production`; the development HTTP-cookie exception is only for localhost.

## Robinhood first gate

Verified public metadata is retained in `evidence/broker-public-metadata-2026-10-16.json`. Official endpoint: `https://agent.robinhood.com/mcp/trading`. Metadata advertises authorization code with PKCE S256, refresh_token, public client authentication (`none`), resource indicator and issuer response. It advertises only `internal` scope, **not a distinct read-only grant**. The runtime enforces a strict read allowlist; broker-issued credentials may carry broader permissions than the application uses.

The initial registration probe accepted localhost. The deployed app subsequently registered its exact Railway HTTPS callback successfully. This verifies hosted registration, not consent or account access. Robinhood's current official onboarding article lists supported platforms and localhost, and directs other platforms to support. A custom Railway-hosted client therefore needs broker confirmation or a successful, supported registration/consent path. No account-opening requirement for reusing the owner's existing account has been verified.

When the deployed owner login is working:

1. Use the registered, exact Railway HTTPS callback. If actual consent or unattended refresh is refused, retain the sanitized error and ask Robinhood support about that concrete blocker. Do not disguise Railway as localhost or another client.
2. Sign in, choose **Connect Robinhood**, and complete Robinhood's own consent. The application discovers pinned official metadata, registers its own client, generates PKCE/state and stores credentials encrypted in PostgreSQL. No chat connector tokens or browser cookies are imported.
3. The callback is exactly `PUBLIC_URL + /oauth/robinhood/callback`. A missing/wrong issuer, expired state, different owner, missing lifetime or malformed response fails closed.
4. Choose **Verify account**, then explicitly select the eligible account by masked number/type. Nicknames have no authorization meaning. The app binds one account; changing accounts requires a different database to prevent mixed histories.
5. Resume monitoring. Each reconciliation rechecks account state and caller-relative `agentic_allowed`. First establish the quiet baseline, then allow research/notifications.
6. Test expiry/refresh/restart with this independent credential, record token lifetime without the token itself, and confirm account association remains unchanged. Refresh state is committed under a row lock, retaining a rotated refresh token. Do not force broker revocation merely to test a financial session without reviewing the effect.

Revocation is not advertised in discovered metadata. **Disconnect** deletes the locally stored token, pauses monitoring and audits the action; it does not claim broker-side revocation. Revoke the dedicated application in Robinhood's connected-app/agent settings or through support, then verify subsequent reads are rejected.

If support requires a different brokerage account, stop and present the exact requirement. Do not open or fund an account, change approval levels, or authorize trading in this phase.

## Support inquiry ready for the owner

> I am deploying an independently authenticated, read-only portfolio monitor on Railway using your official Trading MCP at https://agent.robinhood.com/mcp/trading. Does Robinhood support an unattended persistent client with an HTTPS OAuth callback on Railway? Can this client bind my existing MCP account without opening another account? Please confirm supported registration/redirect rules, permissible scopes, polling quotas, access-token lifetime, refresh-token rotation/expiry, and app revocation. The application allowlists reads only and will not preview or submit orders.

Contact through the official onboarding article's **Contact Robinhood Support** link. Do not include tokens or a full account number in an unsecured inquiry.
