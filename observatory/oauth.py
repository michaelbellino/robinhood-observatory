"""Supported Robinhood OAuth only; no cookie/session imports or password login."""

import base64
import hashlib
import secrets
import logging
import uuid
from datetime import timedelta
from urllib.parse import urlencode
import httpx
from sqlalchemy import select
from .broker import ENDPOINT, AuthorizationExpired
from .db import OAuthFlow, Secret, Audit, aware, now
from .security import digest

RESOURCE_METADATA = "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"
AUTH_METADATA = "https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading"
AUTHORIZATION = "https://robinhood.com/oauth"
TOKEN = "https://api.robinhood.com/oauth2/token/"
REGISTRATION = "https://agent.robinhood.com/oauth/trading/register"


# These messages are local constants. Never echo OAuth descriptions, codes, state,
# token responses or provider exception text into diagnostics or logs.
PROBLEMS = {
    "metadata_unavailable": ("discovery", "Robinhood OAuth metadata could not be verified."),
    "registration_unavailable": (
        "registration",
        "Robinhood client registration did not complete. Try a new connection.",
    ),
    "registration_refused": ("registration", "Robinhood refused this application's registration."),
    "registration_invalid": ("registration", "Robinhood returned an incompatible client registration."),
    "invalid_state": ("callback", "This connection attempt expired or is invalid. Start a new connection."),
    "invalid_callback": ("callback", "Robinhood returned an incomplete or ambiguous OAuth response."),
    "issuer_mismatch": (
        "callback",
        "Robinhood's return did not include the expected issuer. Connection rejected.",
    ),
    "consent_denied": ("consent", "Robinhood returned an access-denied response. No connection was saved."),
    "provider_error": ("consent", "Robinhood returned an OAuth error before token exchange."),
    "exchange_failed": (
        "token_exchange",
        "Robinhood rejected the authorization-code exchange. Start a new connection.",
    ),
    "exchange_unavailable": (
        "token_exchange",
        "The token exchange did not complete. Start a new connection; it will not be replayed.",
    ),
    "token_invalid": (
        "token_validation",
        "Robinhood's token response did not include verified credentials and lifetime.",
    ),
}


class OAuthProblem(ValueError):
    def __init__(self, code):
        self.code = code
        self.stage, message = PROBLEMS[code]
        super().__init__(message)


class RobinhoodOAuth:
    def __init__(self, db, vault, settings, client=None):
        self.db, self.vault, self.settings = db, vault, settings
        self.client = client

    def record(self, owner, stage, outcome, attempt_id=None):
        details = {"stage": stage, "outcome": outcome, "attempt_id": attempt_id}
        with self.db.session() as session:
            session.add(Audit(id=str(uuid.uuid4()), actor=owner, action="broker_oauth", details=details))
        # Only internally selected stage/outcome values and an independent UUID.
        logging.getLogger("observatory").info(
            "broker_oauth stage=%s outcome=%s attempt=%s", stage, outcome, attempt_id or "none"
        )

    def diagnostics(self, owner):
        with self.db.session() as session:
            rows = session.scalars(
                select(Audit)
                .where(Audit.actor == owner, Audit.action == "broker_oauth")
                .order_by(Audit.occurred_at.desc())
                .limit(20)
            ).all()
            registration = self.vault.get(session, "robinhood_client")
            token_saved = session.get(Secret, "robinhood_tokens") is not None
            return {
                "redirect_uri": self.settings.public_url.rstrip("/") + "/oauth/robinhood/callback",
                "registration_response_saved": bool(registration),
                "client_id_sha256_prefix": (digest(registration["client_id"])[:12] if registration else None),
                "registration_proves_redirect_accepted_at_consent": False,
                "credentials_saved": token_saved,
                "attempts": [{"at": aware(row.occurred_at).isoformat(), **row.details} for row in rows],
            }

    async def metadata(self, client):
        resource_response = await client.get(RESOURCE_METADATA)
        resource_response.raise_for_status()
        metadata_response = await client.get(AUTH_METADATA)
        metadata_response.raise_for_status()
        resource = resource_response.json()
        metadata = metadata_response.json()
        if not isinstance(resource, dict) or not isinstance(metadata, dict):
            raise ValueError("Invalid OAuth metadata")
        if resource.get("resource") != ENDPOINT or metadata.get("issuer") != ENDPOINT:
            raise ValueError("Unexpected OAuth issuer/resource")
        for field, expected in [
            ("authorization_endpoint", AUTHORIZATION),
            ("token_endpoint", TOKEN),
            ("registration_endpoint", REGISTRATION),
        ]:
            if metadata.get(field) != expected:
                raise ValueError("OAuth endpoint changed; review official metadata")
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise ValueError("PKCE S256 required")
        return metadata

    async def begin(self, owner):
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            attempt_id = str(uuid.uuid4())
            try:
                await self.metadata(client)
            except (httpx.HTTPError, ValueError):
                self.record(owner, "discovery", "metadata_unavailable", attempt_id)
                raise OAuthProblem("metadata_unavailable") from None
            with self.db.session() as session:
                registration = self.vault.get(session, "robinhood_client")
            redirect = self.settings.public_url.rstrip("/") + "/oauth/robinhood/callback"
            if not registration or registration.get("redirect_uris") != [redirect]:
                try:
                    response = await client.post(
                        REGISTRATION,
                        json={
                            "client_name": "Robinhood Observatory (read-only)",
                            "redirect_uris": [redirect],
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"],
                            "token_endpoint_auth_method": "none",
                            "scope": "internal",
                        },
                    )
                except httpx.HTTPError:
                    self.record(owner, "registration", "registration_unavailable", attempt_id)
                    raise OAuthProblem("registration_unavailable") from None
                if response.status_code not in (200, 201):
                    self.record(owner, "registration", "registration_refused", attempt_id)
                    raise OAuthProblem("registration_refused")
                try:
                    registration = response.json()
                except ValueError:
                    registration = None
                if (
                    not isinstance(registration, dict)
                    or not isinstance(registration.get("client_id"), str)
                    or not registration["client_id"]
                    or registration.get("redirect_uris") != [redirect]
                    or registration.get("token_endpoint_auth_method") != "none"
                ):
                    self.record(owner, "registration", "registration_invalid", attempt_id)
                    raise OAuthProblem("registration_invalid")
                with self.db.session() as session:
                    self.vault.put(session, "robinhood_client", registration)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            )
            with self.db.session() as session:
                session.add(
                    OAuthFlow(
                        state_hash=digest(state),
                        owner=owner,
                        expires_at=now() + timedelta(minutes=10),
                        payload_cipher=self.vault.encrypt(
                            {
                                "verifier": verifier,
                                "redirect": redirect,
                                "client_id": registration["client_id"],
                                "attempt_id": attempt_id,
                            }
                        ),
                    )
                )
            self.record(owner, "authorization", "redirect_prepared", attempt_id)
            return (
                AUTHORIZATION
                + "?"
                + urlencode(
                    {
                        "response_type": "code",
                        "client_id": registration["client_id"],
                        "redirect_uri": redirect,
                        "scope": "internal",
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "resource": ENDPOINT,
                    }
                )
            )

    async def callback(self, owner, state, code, issuer, error=None):
        # Consume only an owner-bound, unexpired state. No provider request occurs
        # until state, issuer and response shape have all been validated.
        if not state or len(state) > 128:
            self.record(owner, "callback", "invalid_state")
            raise OAuthProblem("invalid_state")
        with self.db.session() as session:
            flow = session.scalar(
                select(OAuthFlow).where(OAuthFlow.state_hash == digest(state)).with_for_update()
            )
            if flow is None or flow.owner != owner or aware(flow.expires_at) < now():
                payload = None
            else:
                payload = self.vault.decrypt(flow.payload_cipher)
                registration = self.vault.get(session, "robinhood_client")
                session.delete(flow)  # Commit consumption even when validation/exchange fails.
        if payload is None:
            self.record(owner, "callback", "invalid_state")
            raise OAuthProblem("invalid_state")
        attempt_id = payload.get("attempt_id")
        self.record(owner, "callback", "received", attempt_id)

        def fail(problem):
            self.record(owner, PROBLEMS[problem][0], problem, attempt_id)
            raise OAuthProblem(problem)

        if issuer != ENDPOINT:
            fail("issuer_mismatch")
        if error and code:
            fail("invalid_callback")
        if error is not None:
            fail("consent_denied" if error == "access_denied" else "provider_error")
        if not code or len(code) > 4096:
            fail("invalid_callback")
        client_id = payload.get("client_id")
        # Recover in-flight attempts created before client binding was introduced.
        if not client_id and registration and registration.get("redirect_uris") == [payload["redirect"]]:
            client_id = registration.get("client_id")
        if not client_id:
            fail("invalid_callback")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(
                    TOKEN,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": client_id,
                        "redirect_uri": payload["redirect"],
                        "code_verifier": payload["verifier"],
                        "resource": ENDPOINT,
                    },
                )
        except httpx.HTTPError:
            fail("exchange_unavailable")
        if response.status_code != 200:
            fail("exchange_failed")
        try:
            token = self.validate_token(response.json())
        except (ValueError, TypeError, AttributeError, AuthorizationExpired):
            fail("token_invalid")
        token["client_id"] = client_id
        with self.db.session() as session:
            self.vault.put(session, "robinhood_tokens", token)
        self.record(owner, "token_exchange", "credentials_saved", attempt_id)
        return {"refresh_token_issued": bool(token.get("refresh_token")), "expires_at": token["expires_at"]}

    def validate_token(self, data, previous=None):
        if not data.get("access_token") or data.get("token_type", "").lower() != "bearer":
            raise AuthorizationExpired()
        lifetime = data.get("expires_in")
        if not isinstance(lifetime, (int, float)) or lifetime <= 0:
            # Do not infer lifetime from undocumented JWT contents.
            raise ValueError("Token lifetime not supplied; broker verification required")
        data["expires_at"] = (now() + timedelta(seconds=lifetime)).isoformat()
        if previous and not data.get("refresh_token"):
            data["refresh_token"] = previous.get("refresh_token")
        return data

    async def access_token(self):
        # Row lock serializes refreshes between web and worker processes.
        with self.db.session() as session:
            row = session.scalar(select(Secret).where(Secret.name == "robinhood_tokens").with_for_update())
            if row is None:
                raise AuthorizationExpired()
            token = self.vault.decrypt(row.cipher)
            from datetime import datetime

            if datetime.fromisoformat(token["expires_at"]) > now() + timedelta(seconds=60):
                return token["access_token"]
            if not token.get("refresh_token"):
                raise AuthorizationExpired()
            registration = self.vault.get(session, "robinhood_client")
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    response = await client.post(
                        TOKEN,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"],
                            "client_id": token.get("client_id") or registration["client_id"],
                            "resource": ENDPOINT,
                        },
                    )
            except httpx.HTTPError:
                # An ambiguous rotating-token response cannot be safely replayed indefinitely.
                raise AuthorizationExpired() from None
            if response.status_code != 200:
                raise AuthorizationExpired()
            renewed = self.validate_token(response.json(), token)
            renewed["client_id"] = token.get("client_id") or registration["client_id"]
            self.vault.put(session, "robinhood_tokens", renewed)
            return renewed["access_token"]
