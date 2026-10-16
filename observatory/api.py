import logging
import math
import re
import secrets
import uuid
import traceback
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit
from mcp.server.transport_security import TransportSecuritySettings
from .rate_limit import RequestLimiter
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from .broker import ReadOnlyBroker, BrokerError, Throttled
from .db import Database, State, Audit, PaperProposal, Secret, now
from .domain import Paper
from .oauth import RobinhoodOAuth, OAuthProblem
from .enrollment import LocalEnrollment
from .security import Vault, bearer_principal, principal, require_control
from .service import status, history, change_pause, start_observation
from .settings import Settings

STATIC = Path(__file__).parent / "static"


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
    control = MCPServer("Observatory controls", instructions="Pause/resume monitoring only. Never trading.")

    @read.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def monitor_status() -> dict:
        return status(db)

    @read.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def monitor_history(kind: str = "events", limit: int = 20) -> list[dict]:
        return history(db, kind, limit)

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
            yield

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
            response = JSONResponse({"detail": "Request failed; state remains unchanged"}, status_code=500)
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

    @app.get("/")
    async def dashboard():
        return FileResponse(STATIC / "index.html")

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

    @app.get("/api/status")
    async def get_status():
        return status(db)

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
    async def observation():
        actor = require_control()
        return {"id": start_observation(db, actor["sub"]), "status": "pending"}

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
