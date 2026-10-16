"""Supported Robinhood OAuth only; no cookie/session imports or password login."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode
import httpx
from sqlalchemy import select
from .broker import ENDPOINT, AuthorizationExpired
from .db import OAuthFlow, Secret, aware, now
from .security import digest

RESOURCE_METADATA = "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"
AUTH_METADATA = "https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading"
AUTHORIZATION = "https://robinhood.com/oauth"
TOKEN = "https://api.robinhood.com/oauth2/token/"
REGISTRATION = "https://agent.robinhood.com/oauth/trading/register"


class RobinhoodOAuth:
    def __init__(self, db, vault, settings, client=None):
        self.db, self.vault, self.settings = db, vault, settings
        self.client = client

    async def metadata(self, client):
        resource = (await client.get(RESOURCE_METADATA)).json()
        metadata = (await client.get(AUTH_METADATA)).json()
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
            await self.metadata(client)
            with self.db.session() as session:
                registration = self.vault.get(session, "robinhood_client")
            redirect = self.settings.public_url.rstrip("/") + "/oauth/robinhood/callback"
            if not registration or registration.get("redirect_uris") != [redirect]:
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
                if response.status_code not in (200, 201):
                    raise ValueError("Hosted client registration refused; contact Robinhood support")
                registration = response.json()
                if not registration.get("client_id") or registration.get("redirect_uris") != [redirect]:
                    raise ValueError("Registration returned unexpected redirect")
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
                        payload_cipher=self.vault.encrypt({"verifier": verifier, "redirect": redirect}),
                    )
                )
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

    async def callback(self, owner, state, code, issuer):
        if issuer != ENDPOINT:
            raise ValueError("OAuth response issuer mismatch")
        with self.db.session() as session:
            flow = session.scalar(
                select(OAuthFlow).where(OAuthFlow.state_hash == digest(state)).with_for_update()
            )
            if flow is None or flow.owner != owner or aware(flow.expires_at) < now():
                raise ValueError("Expired or invalid OAuth transaction")
            payload = self.vault.decrypt(flow.payload_cipher)
            registration = self.vault.get(session, "robinhood_client")
            session.delete(flow)  # Single-use state, including after a failed exchange.
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(
                TOKEN,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": registration["client_id"],
                    "redirect_uri": payload["redirect"],
                    "code_verifier": payload["verifier"],
                    "resource": ENDPOINT,
                },
            )
        if response.status_code != 200:
            raise AuthorizationExpired()
        token = self.validate_token(response.json())
        with self.db.session() as session:
            self.vault.put(session, "robinhood_tokens", token)
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
                            "client_id": registration["client_id"],
                            "resource": ENDPOINT,
                        },
                    )
            except httpx.HTTPError:
                # An ambiguous rotating-token response cannot be safely replayed indefinitely.
                raise AuthorizationExpired() from None
            if response.status_code != 200:
                raise AuthorizationExpired()
            renewed = self.validate_token(response.json(), token)
            self.vault.put(session, "robinhood_tokens", renewed)
            return renewed["access_token"]
