import logging
import math
import re
import secrets
import uuid
import traceback
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit
from mcp.server.transport_security import TransportSecuritySettings
from .rate_limit import RequestLimiter
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.sessions import SessionMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from .broker import ReadOnlyBroker, BrokerError, Throttled
from .db import Database, State, Audit, PaperProposal, Secret, TradeIdea, Analysis, now
from .domain import Paper, StrictModel
from .oauth import RobinhoodOAuth, OAuthProblem
from .enrollment import LocalEnrollment
from .security import Vault, bearer_principal, principal, require_control
from .service import status, history, change_pause, start_observations
from .market import market_status
from .settings import Settings
from .release_health import release_evidence
from . import rehearsal, assistant, trade_review, push, manual_orders, mandate, research

STATIC = Path(__file__).parent / "static"


class CompareResearchRequest(StrictModel):
    candidates: list[research.ResearchCandidate] = Field(max_length=20)


class ResearchSourceConfiguration(StrictModel):
    request_id: uuid.UUID
    sources: list[research.SECSource] = Field(max_length=5)
    sec_user_agent: str = Field(min_length=3, max_length=200)
    research_generation_enabled: bool = False


def log_request_failure(exc):
    # Stack locations and exception type only: no values, provider text, or source lines.
    frames = traceback.extract_tb(exc.__traceback__)
    locations = [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames if "/observatory/" in f.filename]
    logging.getLogger("observatory").error(
        "request_failed type=%s locations=%s", type(exc).__name__, ",".join(locations[-5:])
    )


def create_app(settings=None, database=None):
    settings = (settings or Settings()).validate()
    db = database or Database(settings.database_url)
    vault = Vault(settings.encryption_keys)
    oauth = OAuth()
    if settings.auth_provider == "github" and settings.github_client_id:
        oauth.register(
            "owner",
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            authorize_url="https://github.com/login/oauth/authorize",
            access_token_url="https://github.com/login/oauth/access_token",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "read:user", "code_challenge_method": "S256"},
        )
    elif settings.auth_provider == "oidc" and settings.oidc_issuer:
        oauth.register(
            "owner",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
            client_kwargs={"scope": "openid", "code_challenge_method": "S256"},
        )
    broker_oauth = RobinhoodOAuth(db, vault, settings)
    local_enrollment = LocalEnrollment(db, vault, settings)
    read = MCPServer(
        "Observatory reads", instructions="Stored read-only monitoring data. No brokerage tools."
    )
    control = MCPServer("Observatory controls", instructions="Monitor controls and research proposals. No order submission tools.")

    def compare_research(body):
        with db.session() as session:
            state = session.get(State, 1)
            # Evaluate each candidate separately against server-owned evidence.
            reports = [research.compare([candidate], now(), mandate.live_proposal_gate(session, state, candidate))
                       for candidate in body.candidates]
        combined = research.compare([], now())
        combined["results"] = [result for report in reports for result in report["results"]]
        combined["families_compared"] = sorted({family for report in reports for family in report["families_compared"]})
        return combined

    @control.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def screen_research_candidates(request: CompareResearchRequest) -> dict:
        """Compare fully specified candidates and no trade. Never publish or submit an order."""
        require_control()
        return compare_research(request)

    @read.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def monitor_status() -> dict:
        return status(db)

    @read.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def monitor_history(kind: str = "events", limit: int = 20) -> list[dict]:
        return history(db, kind, limit)

    @read.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def trade_review_inbox(limit: int = 20) -> dict:
        return {"ideas": trade_review.list_ideas(db, limit), "execution_route": "owner_dashboard"}

    @control.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    async def propose_trade_for_review(proposal: trade_review.NewIdea) -> dict:
        """Save exact research terms and notify owner. Never an order or an execution approval."""
        actor = require_control()
        return trade_review.create(db, actor["sub"], proposal)

    @control.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    async def set_monitoring_paused(paused: bool, request_id: str) -> dict:
        actor = require_control()
        return change_pause(db, actor["sub"], paused, request_id)

    public = urlsplit(settings.public_url)
    allowed_hosts = [public.netloc]
    if settings.mcp_internal_host:
        allowed_hosts.append(settings.mcp_internal_host)
    transport = TransportSecuritySettings(
        allowed_hosts=allowed_hosts, allowed_origins=[settings.public_url.rstrip("/")]
    )
    read_app = read.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True, transport_security=transport
    )
    control_app = control.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True, transport_security=transport
    )

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(read.session_manager.run())
            await stack.enter_async_context(control.session_manager.run())
            async def deliver_push():
                while True:
                    try:
                        await push.dispatch(db, settings, vault)
                    except Exception:
                        logging.getLogger("observatory").warning("push_dispatch_unavailable")
                    await asyncio.sleep(10)
            delivery = asyncio.create_task(deliver_push())
            try:
                yield
            finally:
                delivery.cancel()
                with suppress(asyncio.CancelledError):
                    await delivery

    app = FastAPI(
        title="Robinhood Observatory", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.db, app.state.settings = db, settings
    app.state.oauth = oauth

    @app.middleware("http")
    async def authorize(request, call_next):
        path = request.url.path
        actor = bearer_principal(request.headers.get("authorization"), settings)
        session = request.scope.get("session", {})
        if actor is None and session.get("sub") == settings.owner_subject:
            actor = {"sub": session["sub"], "scope": "control", "method": "session"}
        protected = path.startswith(("/api/", "/mcp/", "/oauth/robinhood/"))
        credential_deposit = path == "/api/robinhood/local-complete" and request.method == "POST"
        if protected and not actor and not credential_deposit:
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        if path.startswith("/mcp/control") and actor and actor["scope"] != "control":
            return JSONResponse({"detail": "Control scope required"}, status_code=403)
        if (
            protected
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and actor
            and actor["method"] == "session"
        ):
            csrf = request.headers.get("x-csrf-token", "")
            if (
                not csrf
                or not secrets.compare_digest(csrf, session.get("csrf", ""))
                or request.headers.get("origin") != settings.public_url.rstrip("/")
            ):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        token = principal.set(actor)
        try:
            response = await call_next(request)
        except Exception as exc:
            log_request_failure(exc)
            response = JSONResponse({"detail": "Request failed. Refresh status before repeating any action."}, status_code=500)
        finally:
            principal.reset(token)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # Last-added middleware executes outermost; sessions must be available to authorize().
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="observatory_session",
        https_only=settings.environment == "production",
        same_site="lax",
        max_age=28800,
    )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse({"detail": "State storage unavailable; exposure is unknown"}, status_code=503)

    @app.exception_handler(BrokerError)
    async def broker_error(request, exc):
        if isinstance(exc, Throttled):
            return JSONResponse(
                {"detail": "Brokerage requests are paced. Wait before verifying again; the saved connection is unchanged."},
                status_code=429, headers={"Retry-After": str(math.ceil(exc.retry_after))},
            )
        return JSONResponse({"detail": exc.code}, status_code=503)

    @app.exception_handler(OAuthProblem)
    async def oauth_error(request, exc):
        return JSONResponse({"detail": str(exc), "error_code": exc.code, "stage": exc.stage}, status_code=400)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(
            {"detail": "Invalid configuration or request; review setup requirements"}, status_code=400
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Never return or log provider exceptions containing authorization codes or token payloads.
        log_request_failure(exc)
        return JSONResponse({"detail": "Request failed; monitoring state remains unchanged"}, status_code=500)

    @app.get("/health/live")
    async def liveness():
        return {"process": "alive"}  # Deliberately says nothing about broker/worker progress.

    @app.get("/health/release")
    def release_health():
        return release_evidence(db)

    @app.get("/")
    async def dashboard():
        return FileResponse(STATIC / "index.html")

    @app.get("/workbench")
    async def workbench():
        return FileResponse(STATIC / "workbench.html")

    @app.get("/trades")
    async def trade_page():
        return FileResponse(STATIC / "trades.html")

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/"})

    @app.get("/local-connect")
    async def local_connect_page(request: Request):
        # Only public helper coordinates, never tokens or OAuth codes, may survive login.
        port, challenge = request.query_params.get("port", ""), request.query_params.get("challenge", "")
        if not re.fullmatch(r"[0-9]{4,5}", port) or not 1024 <= int(port) <= 65535 or not re.fullmatch(r"[a-f0-9]{64}", challenge):
            raise HTTPException(400, "Launch the local connection helper first")
        request.session["enrollment_return"] = f"/local-connect?port={int(port)}&challenge={challenge}"
        return FileResponse(STATIC / "local-connect.html")

    @app.get("/auth/login")
    async def login(request: Request):
        if not (
            (settings.auth_provider == "github" and settings.github_client_id)
            or (settings.auth_provider == "oidc" and settings.oidc_issuer)
        ):
            return JSONResponse({"detail": "Owner authentication has not been configured"}, status_code=503)
        return await oauth.owner.authorize_redirect(
            request, settings.public_url.rstrip("/") + "/auth/callback"
        )

    @app.get("/auth/callback")
    async def oidc_callback(request: Request):
        token = await oauth.owner.authorize_access_token(request)
        if settings.auth_provider == "github":
            response = await oauth.owner.get("user", token=token)
            response.raise_for_status()
            claims = {"sub": str(response.json()["id"]), "iss": "https://github.com"}
            expected_issuer = "https://github.com"
        else:
            claims = token.get("userinfo")
            expected_issuer = settings.oidc_issuer
        if not claims or claims.get("sub") != settings.owner_subject or claims.get("iss") != expected_issuer:
            request.session.clear()
            raise HTTPException(403, "This application is limited to its configured owner")
        enrollment_return = request.session.get("enrollment_return", "/")
        request.session.clear()
        request.session.update(sub=claims["sub"], csrf=secrets.token_urlsafe(32))
        return RedirectResponse(enrollment_return, status_code=303)

    @app.post("/api/logout")
    async def logout(request: Request):
        request.session.clear()
        return {"signed_out": True}

    @app.get("/api/session")
    async def session_info(request: Request):
        return {"csrf": request.session.get("csrf"), "scope": principal.get()["scope"]}

    def owner_session():
        actor = require_control()
        if actor["method"] != "session":
            raise HTTPException(403, "Use the signed-in owner dashboard")
        return actor

    @app.get("/api/research-mandate")
    async def get_research_mandate():
        owner_session()
        return mandate.get_current(db)

    @app.post("/api/research-mandate")
    async def confirm_research_mandate(body: mandate.MandateInput):
        return mandate.save(db, vault, owner_session()["sub"], body)

    @app.post("/api/research-mandate/disable")
    async def disable_research_mandate(body: mandate.DisableInput):
        return mandate.disable(db, owner_session()["sub"], body)

    @app.post("/api/research/compare")
    async def assess_research_candidates(body: CompareResearchRequest):
        require_control()
        return compare_research(body)

    @app.get("/api/research/readiness")
    async def research_readiness_status():
        from .research_readiness import readiness
        return readiness(db, settings)

    @app.get("/api/research/reports")
    async def research_reports():
        with db.session() as session:
            rows = session.scalars(select(Analysis).where(Analysis.strategy_version == research.VERSION)
                                   .order_by(Analysis.created_at.desc()).limit(10)).all()
            return {"execution": "research_only", "reports": [{
                "id": row.id, "created_at": row.created_at,
                "outcome": row.payload.get("outcome", "unknown"),
                "leads": row.payload.get("leads", []),
                "source_errors": row.payload.get("source_errors", []),
                "limitations": row.payload.get("limitations", []),
                "generation": row.payload.get("generation", {}),
                "blockers": row.payload.get("gate", {}).get("reasons", []),
                "published_proposal_count": len(row.payload.get("published_idea_ids", [])),
            } for row in rows]}

    @app.get("/api/research/sources")
    async def research_sources():
        owner_session()
        with db.session() as session:
            config = session.get(State, 1).config or {}
            return {"sources": config.get("research_sources", []),
                    "contact_configured": bool(config.get("research_sec_user_agent")),
                    "research_generation_enabled": config.get("research_generation_enabled") is True,
                    "limit": 5, "execution": "research_only"}

    @app.post("/api/research/sources")
    async def configure_research_sources(body: ResearchSourceConfiguration):
        actor = owner_session()
        config = {"research_sources": [x.model_dump(mode="json") for x in body.sources],
                  "research_sec_user_agent": body.sec_user_agent,
                  "research_generation_enabled": body.research_generation_enabled}
        research.source_settings(config)
        request_hash = rehearsal.fingerprint(config)
        with db.session() as session:
            state = session.scalar(select(State).where(State.id == 1).with_for_update())
            prior = session.get(Audit, str(body.request_id))
            details = {"config_hash": request_hash}
            if prior:
                if prior.actor != actor["sub"] or prior.action != "research_sources_configured" or prior.details != details:
                    raise HTTPException(409, "Source configuration request ID conflict")
                return {"saved": True, "repeated": True, "execution": "research_only"}
            state.config = {**(state.config or {}), **config}
            session.add(Audit(id=str(body.request_id), actor=actor["sub"], action="research_sources_configured", details=details))
        return {"saved": True, "sources": config["research_sources"], "execution": "research_only"}

    @app.get("/api/trades")
    async def get_ideas():
        return {"ideas": trade_review.list_ideas(db), "execution_route": "manual_robinhood",
                "submissions": manual_orders.get_results(db),
                "automatic_recommendations": "gated_research_requires_verified_configuration",
                "analysis": status(db)["analysis"], "automatic_submission": False}

    @app.post("/api/trades/{ident}/revise")
    async def revise_idea(ident: uuid.UUID, body: trade_review.Revision):
        return trade_review.revise(db, owner_session()["sub"], ident, body)

    @app.post("/api/trades/{ident}/reconcile")
    async def reconcile_order(ident: uuid.UUID):
        actor = owner_session()
        with db.session() as s:
            row = s.get(TradeIdea, str(ident))
            manual_id = row.broker_order_id if row and row.status == "observed_manual_order" else None
        if manual_id:
            return await trade_review.observe_manual_order(db, settings, vault, actor["sub"], ident, manual_id)
        return await manual_orders.reconcile(db, settings, vault, ident)

    @app.post("/api/trades")
    async def save_idea(body: trade_review.NewIdea):
        actor = require_control()
        return trade_review.create(db, actor["sub"], body)

    @app.post("/api/trades/owner-ticket")
    async def save_owner_ticket(body: trade_review.OwnerTicket):
        return trade_review.create_owner_ticket(db, owner_session()["sub"], body)

    @app.post("/api/trades/{ident}/preview")
    async def preview_idea(ident: uuid.UUID):
        actor = owner_session()
        return await trade_review.preview(db, settings, vault, ident, actor["sub"])

    @app.post("/api/trades/{ident}/decision")
    async def decide_idea(ident: uuid.UUID, body: trade_review.Decision):
        return trade_review.decide(db, owner_session()["sub"], ident, body)

    @app.post("/api/trades/{ident}/submit")
    async def submit_owner_order(ident: uuid.UUID, body: manual_orders.SubmitOrder):
        owner_session()
        raise HTTPException(410, "Hosted order submission is disabled. Place any order manually in Robinhood.")

    @app.post("/api/trades/{ident}/link/{order_id}")
    async def link_idea_order(ident: uuid.UUID, order_id: uuid.UUID):
        return await trade_review.observe_manual_order(db, settings, vault, owner_session()["sub"], ident, order_id)

    @app.get("/api/notifications")
    async def notification_status():
        return push.delivery_status(db)

    @app.post("/api/notifications/key")
    async def push_key():
        owner_session()
        return {"public_key": push.application_key(db, vault)}

    @app.post("/api/notifications/subscribe")
    async def push_subscribe(body: push.Subscription):
        return push.subscribe(db, vault, owner_session()["sub"], body)

    @app.post("/api/notifications/disable/{ident}")
    async def push_disable(ident: str):
        return push.disable(db, owner_session()["sub"], ident)

    @app.post("/api/notifications/test")
    async def test_notification():
        result = push.test_event(db, owner_session()["sub"])
        await push.dispatch(db, settings, vault)
        return result

    @app.get("/api/market")
    async def get_market():
        return market_status(db)

    @app.get("/api/status")
    async def get_status():
        return status(db)

    @app.post("/api/assistant")
    async def assistant_message(message: assistant.Message):
        return assistant.respond(db, message)

    @app.get("/api/rehearsals")
    async def get_rehearsals():
        return {"limits": rehearsal.limits(), "tickets": rehearsal.recent(db)}

    @app.post("/api/rehearsals")
    async def create_rehearsal(body: rehearsal.CreateRehearsal):
        actor = require_control()
        return rehearsal.create(db, actor["sub"], body)

    @app.post("/api/rehearsals/{ident}/review")
    async def review_rehearsal(ident: uuid.UUID, body: rehearsal.ReviewRehearsal):
        actor = require_control()
        if actor["method"] != "session":
            raise HTTPException(403, "Paper review requires the signed-in owner dashboard")
        return rehearsal.review(db, actor["sub"], ident, body)

    @app.get("/api/history/{kind}")
    async def get_history(kind: str, limit: int = 50):
        return history(db, kind, limit)

    @app.post("/api/pause")
    async def set_pause(request: Request):
        actor = require_control()
        body = await request.json()
        if type(body.get("paused")) is not bool:
            raise HTTPException(422, "paused must be boolean")
        return change_pause(db, actor["sub"], body["paused"], body["request_id"])

    @app.post("/api/config")
    async def set_config(request: Request):
        actor = require_control()
        body = await request.json()
        if set(body) - {"watchlist", "calendars"}:
            raise HTTPException(422, "Unsupported configuration")
        watchlist = body.get("watchlist", [])
        calendars = body.get("calendars", {})
        if (
            not isinstance(watchlist, list)
            or len(watchlist) > 20
            or any(not isinstance(x, str) or not re.fullmatch(r"[A-Z0-9.^-]{1,12}", x) for x in watchlist)
        ):
            raise HTTPException(422, "Watchlist limited to 20 valid symbols")
        if (
            not isinstance(calendars, dict)
            or len(calendars) > 200
            or any(v not in {"XNYS", "XNAS", "24/7"} for v in calendars.values())
        ):
            raise HTTPException(422, "Unverified calendar mapping")
        with db.session() as s:
            row = s.scalar(select(State).where(State.id == 1).with_for_update())
            row.config = {"watchlist": sorted(set(watchlist)), "calendars": calendars}
            from .db import MarketState
            market = s.get(MarketState, 1)
            if market:
                market.next_run = now()
            row.generation += 1
            s.add(
                Audit(
                    id=str(uuid.uuid4()),
                    actor=actor["sub"],
                    action="configuration_changed",
                    details=row.config,
                )
            )
        return {"saved": True}

    @app.post("/api/robinhood/connect")
    async def connect():
        actor = require_control()
        return {"authorization_url": await broker_oauth.begin(actor["sub"])}

    @app.post("/api/robinhood/local-enrollment")
    async def begin_local_enrollment(request: Request):
        actor = require_control()
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid enrollment request")
        return local_enrollment.start(actor["sub"], body.get("port"), body.get("challenge"))

    @app.post("/api/robinhood/local-complete")
    async def complete_local_enrollment(request: Request):
        # Independent narrow bearer authentication; owner session alone is insufficient.
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 40000:
                raise HTTPException(413, "Enrollment payload too large")
            content.extend(chunk)
        import json
        try:
            body = json.loads(content)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid enrollment payload") from None
        return local_enrollment.complete(request.headers.get("authorization"), body)

    @app.get("/api/robinhood/diagnostics")
    async def connection_diagnostics():
        actor = require_control()
        return broker_oauth.diagnostics(actor["sub"])

    @app.get("/oauth/robinhood/callback")
    async def broker_callback(request: Request):
        actor = require_control()
        params = request.query_params
        if any(len(params.getlist(key)) > 1 for key in ("state", "code", "iss", "error")):
            broker_oauth.record(actor["sub"], "callback", "invalid_callback")
            raise OAuthProblem("invalid_callback")
        result = await broker_oauth.callback(
            actor["sub"], params.get("state"), params.get("code"), params.get("iss"), params.get("error")
        )
        with db.session() as s:
            row = s.get(State, 1)
            row.paused, row.account_verified, row.status = True, False, "select_account"
            row.generation += 1
            s.add(Audit(id=str(uuid.uuid4()), actor=actor["sub"], action="broker_authorized", details=result))
        return RedirectResponse("/", status_code=303)

    @app.get("/api/robinhood/accounts")
    async def candidates(request: Request):
        require_control()
        broker = ReadOnlyBroker(await broker_oauth.access_token(), "", settings.broker_rpm)
        broker.limiter = RequestLimiter(db, settings.broker_rpm)
        try:
            await broker.initialize()
            data = await broker.call("get_accounts", {})
        finally:
            await broker.close()
        # Return opaque selection tokens, never persist other accounts or expose full numbers.
        result = []
        for a in data.get("accounts", []):
            if a and a.get("agentic_allowed") is True and a.get("state") == "active":
                selection = vault.encrypt(
                    {
                        "account": a["account_number"],
                        "owner": principal.get()["sub"],
                        "expires": now().timestamp() + 600,
                    }
                )
                result.append(
                    {
                        "selection": selection,
                        "label": (a.get("nickname") or "Account") + " ••••" + a["account_number"][-4:],
                        "type": a.get("type"),
                        "option_level": a.get("option_level"),
                    }
                )
        return result

    @app.post("/api/robinhood/account")
    async def select_account(request: Request):
        actor = require_control()
        data = vault.decrypt((await request.json())["selection"])
        if data["owner"] != actor["sub"] or data["expires"] < now().timestamp():
            raise HTTPException(403, "Expired account selection")
        with db.session() as s:
            row = s.scalar(select(State).where(State.id == 1).with_for_update())
            if row.account_cipher and vault.decrypt(row.account_cipher) != data["account"]:
                raise HTTPException(
                    409, "Account rebinding requires an isolated database; histories must not mix"
                )
            # Stable opaque identity for this immutable account; cipher re-encryption is not identity.
            row.account_binding = row.account_binding or str(uuid.uuid4())
            row.account_cipher = vault.encrypt(data["account"])
            row.account_label = "Agent account ••••" + data["account"][-4:]
            row.account_verified, row.paused, row.status = True, True, "paused"
            row.generation += 1
            s.add(
                Audit(
                    id=str(uuid.uuid4()),
                    actor=actor["sub"],
                    action="account_bound",
                    details={"last_four": data["account"][-4:]},
                )
            )
        return {"selected": True, "paused": True}

    @app.post("/api/robinhood/disconnect")
    async def disconnect():
        actor = require_control()
        with db.session() as s:
            row = s.scalar(select(State).where(State.id == 1).with_for_update())
            row.paused, row.account_verified, row.status = True, False, "disconnected"
            row.generation += 1
            secret = s.get(Secret, "robinhood_tokens")
            if secret:
                s.delete(secret)
            s.add(
                Audit(
                    id=str(uuid.uuid4()), actor=actor["sub"], action="local_credentials_removed", details={}
                )
            )
        return {
            "disconnected": True,
            "broker_revocation": "Complete revocation in Robinhood; no revocation endpoint advertised",
        }

    @app.post("/api/paper")
    async def paper(request: Request):
        actor = require_control()
        proposal = Paper.model_validate(await request.json())
        ident = str(uuid.uuid4())
        with db.session() as s:
            s.add(PaperProposal(id=ident, payload=proposal.model_dump(mode="json")))
            s.add(
                Audit(
                    id=str(uuid.uuid4()),
                    actor=actor["sub"],
                    action="paper_saved",
                    details={"paper_id": ident},
                )
            )
        return {"id": ident, "status": "paper_unfilled"}

    @app.post("/api/observation")
    async def observation(sessions: int = Query(default=1, ge=1, le=5)):
        actor = require_control()
        ids = start_observations(db, actor["sub"], sessions)
        return {"id": ids[0], "ids": ids, "status": "pending"}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/mcp/read", read_app)
    app.mount("/mcp/control", control_app)
    diagnostic_log = logging.getLogger("observatory")
    diagnostic_log.setLevel(logging.INFO)
    if not diagnostic_log.handlers:
        diagnostic_log.addHandler(logging.StreamHandler())
    for name in ["httpx", "httpcore", "authlib", "mcp", "sqlalchemy"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    return app


