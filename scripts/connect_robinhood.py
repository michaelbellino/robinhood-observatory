#!/usr/bin/env python3
"""One-time native Observatory enrollment. Python 3.10+, standard library only.

Runs on the same computer as the browser. Uses fresh official OAuth, not a
browser cookie, password wrapper, CLI token cache, or another app's connection.
Credentials stay in process memory until deposited over HTTPS into the owner's
approved Observatory vault. Nothing sensitive is printed or written to disk.
"""

import argparse
import base64
import hashlib
import html
import json
import math
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_APP = "https://web-production-0eaab.up.railway.app"
RESOURCE = "https://agent.robinhood.com/mcp/trading"
RESOURCE_METADATA = "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"
AUTH_METADATA = "https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading"
AUTHORIZE = "https://robinhood.com/oauth"
REGISTER = "https://agent.robinhood.com/oauth/trading/register"
TOKEN = "https://api.robinhood.com/oauth2/token/"


class EnrollmentError(Exception):
    """Only fixed, non-sensitive messages may be passed here."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, data=None, bearer=None, form=False):
    headers = {"Accept": "application/json"}
    payload = None
    if data is not None:
        payload = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    try:
        # Default TLS verification remains enabled. Never forward credentials on redirects.
        with urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, data=payload, headers=headers), timeout=25
        ) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise EnrollmentError("Provider response exceeded the allowed size.")
            result = json.loads(raw)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise EnrollmentError("A secure provider request failed. No response details were logged.") from None
    if not isinstance(result, dict):
        raise EnrollmentError("Provider returned an incompatible response.")
    return result


def check_app(value):
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise EnrollmentError("The Observatory address must be an HTTPS origin.")
    return value.rstrip("/")


def one_param(query, name, required=True):
    values = query.get(name, [])
    if len(values) > 1 or (required and (not values or not values[0])):
        raise EnrollmentError("Incomplete or ambiguous authorization response.")
    return values[0] if values else None


class NativeEnrollment:
    def __init__(self, app, request=request_json):
        self.app, self.request = check_app(app), request
        self.proof, self.state, self.verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.challenge = hashlib.sha256(self.proof.encode()).hexdigest()
        self.csrf = secrets.token_urlsafe(32)
        self.ticket = self.client_id = self.redirect = None
        self.done, self.success, self.consumed = False, False, False
        self.deadline = time.monotonic() + 600

    def prepare(self, port):
        self.origin = f"http://127.0.0.1:{port}"
        self.redirect = self.origin + "/callback"
        resource, metadata = self.request(RESOURCE_METADATA), self.request(AUTH_METADATA)
        if (
            resource.get("resource") != RESOURCE
            or metadata.get("issuer") != RESOURCE
            or metadata.get("authorization_endpoint") != AUTHORIZE
            or metadata.get("token_endpoint") != TOKEN
            or metadata.get("registration_endpoint") != REGISTER
            or "S256" not in metadata.get("code_challenge_methods_supported", [])
            or "none" not in metadata.get("token_endpoint_auth_methods_supported", [])
        ):
            raise EnrollmentError("Robinhood's authentication metadata changed; review is required.")
        self.issuer_required = metadata.get("authorization_response_iss_parameter_supported") is True
        registration = self.request(
            REGISTER,
            data={
                "client_name": "Robinhood Observatory Native Enrollment",
                "application_type": "native",
                "redirect_uris": [self.redirect],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "internal",
            },
        )
        if (
            not isinstance(registration.get("client_id"), str)
            or not registration["client_id"]
            or registration.get("redirect_uris") != [self.redirect]
            or registration.get("token_endpoint_auth_method") != "none"
        ):
            raise EnrollmentError("Robinhood returned an incompatible native client registration.")
        self.client_id = registration["client_id"]
        return (
            self.app
            + "/local-connect?"
            + urllib.parse.urlencode(
                {
                    "port": port,
                    "challenge": self.challenge,
                }
            )
        )

    def accept_ticket(self, body):
        if self.ticket or self.done or time.monotonic() >= self.deadline:
            raise EnrollmentError("This enrollment has already started or expired.")
        if (
            not isinstance(body, dict)
            or body.get("challenge") != self.challenge
            or not isinstance(body.get("ticket"), str)
            or len(body["ticket"]) != 43
            or body.get("csrf") != self.csrf
        ):
            raise EnrollmentError("The enrollment handoff could not be verified.")
        self.ticket = body["ticket"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        )
        return (
            AUTHORIZE
            + "?"
            + urllib.parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect,
                    "scope": "internal",
                    "state": self.state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": RESOURCE,
                }
            )
        )

    def finish(self, query):
        if not self.ticket or self.consumed or time.monotonic() >= self.deadline:
            raise EnrollmentError("This authorization attempt expired or was already used.")
        state = one_param(query, "state")
        if not secrets.compare_digest(state, self.state):
            raise EnrollmentError("Authorization state rejected.")
        self.consumed = True
        issuer = one_param(query, "iss", required=self.issuer_required)
        if issuer is not None and issuer != RESOURCE:
            raise EnrollmentError("Authorization issuer rejected.")
        error, code = one_param(query, "error", False), one_param(query, "code", False)
        if error is not None or not code or len(code) > 4096:
            raise EnrollmentError("Robinhood did not approve this connection.")
        token_time = time.monotonic()
        token = self.request(
            TOKEN,
            form=True,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "redirect_uri": self.redirect,
                "code_verifier": self.verifier,
                "resource": RESOURCE,
            },
        )
        life = token.get("expires_in")
        if (
            not isinstance(token.get("access_token"), str)
            or not token["access_token"]
            or not isinstance(token.get("refresh_token"), str)
            or not token["refresh_token"]
            or not isinstance(token.get("token_type"), str)
            or token["token_type"].lower() != "bearer"
            or type(life) not in (int, float)
            or not math.isfinite(life)
            or life <= 90
        ):
            raise EnrollmentError("Robinhood did not issue usable refreshable credentials.")
        # Send only necessary fields, with a conservative transit allowance.
        credentials = {key: token[key] for key in ("access_token", "refresh_token", "token_type")}
        credentials["expires_in"] = life - (time.monotonic() - token_time) - 30
        result = self.request(
            self.app + "/api/robinhood/local-complete",
            bearer=self.ticket,
            data={
                "proof": self.proof,
                "client_id": self.client_id,
                "redirect_uri": self.redirect,
                "token": credentials,
            },
        )
        if result.get("connected") is not True or result.get("monitoring") != "paused":
            raise EnrollmentError("The hosted service did not confirm credential storage.")
        self.success, self.done = True, True
        self.ticket = self.verifier = None


def handler_for(enrollment):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # OAuth codes, states and fragments must never enter access logs.

        def allowed_host(self):
            return self.headers.get("Host") == urllib.parse.urlsplit(enrollment.origin).netloc

        def respond(self, code, body, mime="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'nonce-"
                + enrollment.csrf
                + "'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed_host():
                self.respond(400, "Invalid local host")
                return
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/enroll" and not url.query:
                # Fragment is consumed once by a same-origin POST, then removed from history.
                script = """
const fields=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'','/enroll');
fetch('/enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
ticket:fields.get('ticket'),challenge:fields.get('challenge'),csrf:CSRF_VALUE})})
.then(async r=>{const d=await r.json();if(!r.ok)throw new Error();location.replace(d.authorization_url);})
.catch(()=>{document.getElementById('message').textContent='Connection handoff failed. Restart the helper.';});
""".replace("CSRF_VALUE", json.dumps(enrollment.csrf))
                self.respond(
                    200,
                    '<!doctype html><meta name="referrer" content="no-referrer">'
                    '<title>Observatory connection</title><p id="message">Opening Robinhood…</p>'
                    f'<script nonce="{enrollment.csrf}">{script}</script>',
                )
                return
            if url.path != "/callback" or len(self.path) > 8192:
                self.respond(404, "Not found")
                return
            try:
                query = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=12)
                enrollment.finish(query)
                self.respond(
                    200,
                    "<!doctype html><title>Observatory connected</title>"
                    "<h1>Connection saved</h1><p>Monitoring remains paused. "
                    'Return to Observatory to verify your selected account.</p><a href="'
                    + html.escape(enrollment.app, quote=True)
                    + '">Open Observatory</a>',
                )
            except (EnrollmentError, ValueError):
                self.respond(
                    400, "Connection could not be completed. Close this page and restart the helper."
                )
                if enrollment.consumed:
                    enrollment.done = True

        def do_POST(self):
            if (
                not self.allowed_host()
                or self.path != "/enroll"
                or self.headers.get("Origin") != enrollment.origin
                or self.headers.get("Content-Type") != "application/json"
            ):
                self.respond(403, "{}", "application/json")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 4096:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                url = enrollment.accept_ticket(body)
                self.respond(200, json.dumps({"authorization_url": url}), "application/json")
            except (EnrollmentError, ValueError):
                self.respond(400, "{}", "application/json")

    return Handler


class LoopbackServer(HTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default=DEFAULT_APP, help="Your own Observatory HTTPS origin")
    args = parser.parse_args()
    try:
        enrollment = NativeEnrollment(args.app)
        with LoopbackServer(("127.0.0.1", 0), handler_for(enrollment)) as server:
            server.timeout = 1
            url = enrollment.prepare(server.server_port)
            print(
                "Opening your Observatory service. Authorize this helper, then approve Robinhood in the browser."
            )
            print("The helper closes after one connection or 10 minutes. No trading is enabled.")
            if not webbrowser.open(url):
                raise EnrollmentError(
                    "Could not open a browser. Run this on a desktop with a default browser."
                )
            while not enrollment.done and time.monotonic() < enrollment.deadline:
                server.handle_request()
            if not enrollment.success:
                raise EnrollmentError("Connection was not confirmed. No live monitoring was started.")
        print(
            "Connection saved securely. Open Observatory and verify your account before resuming monitoring."
        )
        return 0
    except (EnrollmentError, OSError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, EnrollmentError) else "Local enrollment stopped safely.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
