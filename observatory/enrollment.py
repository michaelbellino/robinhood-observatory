"""Owner-approved, short-lived credential deposit from this app's native helper.

No OAuth redirect relay, imported browser sessions, or generic token API. The
helper receives its own fresh code on a real loopback listener and exchanges it
with Robinhood locally. This ticket can only deposit that enrollment once.
"""

import math
import re
import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from .db import Audit, OAuthFlow, Secret, State, aware, now
from .security import digest


class LocalEnrollment:
    def __init__(self, db, vault, settings):
        self.db, self.vault, self.settings = db, vault, settings

    def start(self, owner, port, challenge):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise HTTPException(400, "Invalid loopback port")
        if not isinstance(challenge, str) or not re.fullmatch(r"[a-f0-9]{64}", challenge):
            raise HTTPException(400, "Invalid helper challenge")
        ticket = secrets.token_urlsafe(32)
        with self.db.session() as session:
            state = session.scalar(select(State).where(State.id == 1).with_for_update())
            if session.get(Secret, "robinhood_tokens"):
                raise HTTPException(409, "Robinhood credentials are already saved. Open the dashboard and choose Verify account; no new helper is needed.")
            if not state.paused:
                raise HTTPException(409, "Pause monitoring before starting a new connection")
            # Serialize issuance and invalidate earlier helper deposits for this owner.
            for row in session.scalars(select(OAuthFlow).where(OAuthFlow.owner == owner)).all():
                if self.vault.decrypt(row.payload_cipher).get("kind") == "local_enrollment":
                    session.delete(row)
            session.add(
                OAuthFlow(
                    state_hash=digest("enrollment:" + ticket),
                    owner=owner,
                    expires_at=now() + timedelta(minutes=10),
                    payload_cipher=self.vault.encrypt(
                        {
                            "kind": "local_enrollment",
                            "challenge": challenge,
                            "redirect": f"http://127.0.0.1:{port}/callback",
                            "completed": False,
                        }
                    ),
                )
            )
            session.add(Audit(id=str(uuid.uuid4()), actor=owner, action="local_enrollment_started"))
        return {"ticket": ticket, "expires_in": 600}

    def complete(self, authorization, body):
        # Ticket is scoped only to this method. It is not a dashboard/MCP credential.
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid or expired enrollment")
        ticket = authorization.removeprefix("Bearer ")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            raise HTTPException(401, "Invalid or expired enrollment")
        with self.db.session() as session:
            # Same lock order as issuance, so replacing a pending enrollment cannot deadlock a deposit.
            state = session.scalar(select(State).where(State.id == 1).with_for_update())
            row = session.scalar(
                select(OAuthFlow)
                .where(OAuthFlow.state_hash == digest("enrollment:" + ticket))
                .with_for_update()
            )
            if not row or row.owner != self.settings.owner_subject or aware(row.expires_at) < now():
                raise HTTPException(401, "Invalid or expired enrollment")
            payload = self.vault.decrypt(row.payload_cipher)
            if payload.get("kind") != "local_enrollment" or not isinstance(body, dict):
                raise HTTPException(401, "Invalid or expired enrollment")
            proof = body.get("proof")
            if not isinstance(proof, str) or not secrets.compare_digest(digest(proof), payload["challenge"]):
                raise HTTPException(401, "Helper proof rejected")
            # A retry may acknowledge this same deposit, but can never replace tokens.
            if payload["completed"]:
                return {"connected": True, "monitoring": "paused", "account_selection_required": True}
            if body.get("redirect_uri") != payload["redirect"]:
                raise HTTPException(400, "Enrollment redirect mismatch")
            token, client_id = body.get("token"), body.get("client_id")
            if (
                not isinstance(client_id, str)
                or not 1 <= len(client_id) <= 1024
                or not isinstance(token, dict)
            ):
                raise HTTPException(400, "Invalid enrollment credentials")
            for name in ("access_token", "refresh_token"):
                if not isinstance(token.get(name), str) or not 1 <= len(token[name]) <= 16384:
                    raise HTTPException(400, "Refreshable credentials required")
            lifetime = token.get("expires_in")
            if (
                type(lifetime) not in (int, float)
                or not math.isfinite(lifetime)
                or not 60 < lifetime <= 366 * 86400
                or not isinstance(token.get("token_type"), str)
                or token["token_type"].lower() != "bearer"
            ):
                raise HTTPException(400, "Valid token lifetime required")
            if not state.paused or session.get(Secret, "robinhood_tokens"):
                raise HTTPException(409, "Enrollment no longer available")
            self.vault.put(
                session,
                "robinhood_tokens",
                {
                    "access_token": token["access_token"],
                    "refresh_token": token["refresh_token"],
                    "token_type": "Bearer",
                    "expires_at": (now() + timedelta(seconds=lifetime)).isoformat(),
                    "client_id": client_id,
                },
            )
            self.vault.put(
                session,
                "robinhood_client",
                {
                    "client_id": client_id,
                    "redirect_uris": [payload["redirect"]],
                    "token_endpoint_auth_method": "none",
                    "enrollment": "native_loopback",
                },
            )
            state.paused, state.account_verified, state.status = True, False, "select_account"
            # Credential enrollment cannot erase the immutable account binding or mix histories.
            # A different account still requires an isolated database at selection.
            state.generation += 1
            payload["completed"] = True
            row.payload_cipher = self.vault.encrypt(payload)
            session.add(
                Audit(
                    id=str(uuid.uuid4()),
                    actor=row.owner,
                    action="local_enrollment_completed",
                    details={"refresh_token_issued": True, "monitoring_resumed": False},
                )
            )
        return {"connected": True, "monitoring": "paused", "account_selection_required": True}

