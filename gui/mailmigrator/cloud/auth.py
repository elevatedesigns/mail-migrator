"""Signing in to Google and Microsoft.

User mode: the standard browser sign-in for desktop apps (authorization code
with PKCE, redirected to a temporary local web server). Admin mode: a Google
service account with domain-wide delegation, or a Microsoft app with
application permissions (client credentials).

Tokens are kept in memory only, never written to disk, so closing the app
signs everyone out (consistent with passwords never being stored).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

import requests

from .http import ApiError, Stopped

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
MS_LOGIN = "https://login.microsoftonline.com"

# Everything a migration may touch. Requested together so one sign-in covers
# mail, contacts, calendars and files for that account.
GOOGLE_SCOPES = [
    "openid", "email", "profile",
    "https://mail.google.com/",                         # IMAP (XOAUTH2)
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]
GOOGLE_ADMIN_SCOPES = GOOGLE_SCOPES[3:] + [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]

MS_IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All"
MS_GRAPH_SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Contacts.ReadWrite",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/Files.ReadWrite.All",
    "https://graph.microsoft.com/Sites.ReadWrite.All",
]
RESOURCE_SCOPES = {
    "imap": [MS_IMAP_SCOPE],
    "graph": MS_GRAPH_SCOPES,
}
APP_RESOURCE = {
    "imap": "https://outlook.office365.com/.default",
    "graph": "https://graph.microsoft.com/.default",
}

EXPIRY_MARGIN = 300  # refresh tokens five minutes before they expire


class AuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt_claims(token: str) -> dict:
    """Read an ID token's claims. It came straight from the provider over TLS,
    and is only used to show which account signed in."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def _post_token(url: str, data: dict, session: requests.Session | None = None) -> dict:
    sess = session or requests
    try:
        resp = sess.post(url, data=data, timeout=60)
    except requests.RequestException as exc:
        raise AuthError(f"Could not reach the sign-in service: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or "access_token" not in body:
        msg = body.get("error_description") or body.get("error") or resp.text[:300]
        raise AuthError(_friendly_auth_error(str(msg)))
    return body


def _friendly_auth_error(msg: str) -> str:
    hints = {
        "invalid_grant": "The sign-in has expired or was revoked. Please sign in again.",
        "unauthorized_client": "This app is not authorized. For admin mode, check that "
                               "domain-wide delegation lists all the required scopes.",
        "AADSTS65001": "An administrator must grant consent to this app first.",
        "AADSTS7000215": "The client secret is wrong or has expired.",
        "AADSTS700016": "The application ID was not found in this tenant.",
        "AADSTS90002": "The tenant was not found. Check the tenant ID or domain.",
    }
    for key, text in hints.items():
        if key in msg:
            return f"{text}\n\nDetails: {msg.splitlines()[0][:300]}"
    return msg


# --------------------------------------------------------------- browser flow

class _Handler(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        self.server.result = {k: v[0] for k, v in query.items()}  # type: ignore[attr-defined]
        ok = "code" in query
        text = ("You are signed in. You can close this tab and return to Mail Migrator."
                if ok else "Sign-in was not completed: " +
                html.escape(query.get("error_description", query.get("error", [""]))[0]))
        body = (f"<!doctype html><meta charset=utf-8><title>Mail Migrator</title>"
                f"<body style='font-family:system-ui;background:#0a1020;color:#e8eef9;"
                f"display:grid;place-items:center;height:90vh'><div style='text-align:center'>"
                f"<h2>{'Signed in' if ok else 'Sign-in failed'}</h2><p>{text}</p></div>"
                ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def _free_loopback_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.result = {}  # type: ignore[attr-defined]
    server.timeout = 0.5
    return server


def browser_sign_in(auth_url: str, params: dict, host: str,
                    cancel: threading.Event | None = None, timeout: float = 600,
                    open_url: Callable[[str], object] = webbrowser.open) -> tuple[str, str, str]:
    """Open the browser for sign-in; return (code, redirect_uri, code_verifier)."""
    cancel = cancel or threading.Event()
    server = _free_loopback_server()
    port = server.server_address[1]
    redirect_uri = f"http://{host}:{port}/"
    verifier = _b64url(secrets.token_bytes(48))
    state = _b64url(secrets.token_bytes(16))
    query = dict(params)
    query.update(redirect_uri=redirect_uri, response_type="code", state=state,
                 code_challenge=_b64url(hashlib.sha256(verifier.encode()).digest()),
                 code_challenge_method="S256")
    open_url(auth_url + "?" + urllib.parse.urlencode(query))
    deadline = time.monotonic() + timeout
    try:
        while not server.result:  # type: ignore[attr-defined]
            if cancel.is_set():
                raise Stopped()
            if time.monotonic() > deadline:
                raise AuthError("Sign-in timed out. Please try again.")
            server.handle_request()
    finally:
        server.server_close()
    result = server.result  # type: ignore[attr-defined]
    if result.get("state") != state:
        raise AuthError("Sign-in response did not match this request. Please try again.")
    if "error" in result:
        raise AuthError(_friendly_auth_error(result.get("error_description") or result["error"]))
    return result["code"], redirect_uri, verifier


# --------------------------------------------------------------- credentials

class Credential:
    """Hands out access tokens per resource, refreshing them when needed.

    Resources: "google" (all Google APIs and Gmail IMAP), "imap" (Exchange
    Online IMAP) and "graph" (Microsoft Graph).
    """

    provider = ""   # "google" or "microsoft"
    admin = False
    email = ""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, float]] = {}

    def token(self, resource: str) -> str:
        with self._lock:
            tok, exp = self._cache.get(resource, ("", 0.0))
            if tok and exp - EXPIRY_MARGIN > time.time():
                return tok
            tok, lifetime = self._fetch(resource)
            self._cache[resource] = (tok, time.time() + lifetime)
            return tok

    def invalidate(self, resource: str | None = None) -> None:
        with self._lock:
            if resource:
                self._cache.pop(resource, None)
            else:
                self._cache.clear()

    def source(self, resource: str) -> "TokenSource":
        return TokenSource(self, resource)

    def for_user(self, email: str) -> "Credential":
        """Admin credentials act on behalf of any user; user credentials only themselves."""
        if self.admin:
            raise NotImplementedError
        if email and self.email and email.lower() != self.email.lower():
            raise AuthError(f"Signed in as {self.email}, not {email}.")
        return self

    def _fetch(self, resource: str) -> tuple[str, float]:
        raise NotImplementedError


class TokenSource:
    """Callable token for the HTTP client, with a way to force a refresh."""

    def __init__(self, cred: Credential, resource: str) -> None:
        self.cred = cred
        self.resource = resource

    def __call__(self) -> str:
        return self.cred.token(self.resource)

    def invalidate(self) -> None:
        self.cred.invalidate(self.resource)


@dataclass
class _Refreshable:
    refresh_token: str = ""
    scopes: list[str] = field(default_factory=list)


class GoogleUser(Credential):
    provider = "google"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 email: str = "", name: str = "") -> None:
        super().__init__()
        self.client_id, self.client_secret = client_id, client_secret
        self.refresh_token = refresh_token
        self.email, self.name = email, name

    @classmethod
    def sign_in(cls, client_id: str, client_secret: str, login_hint: str = "",
                cancel: threading.Event | None = None, **kw) -> "GoogleUser":
        if not client_id:
            raise AuthError("No Google app ID is configured. Add one in Settings › "
                            "Google & Microsoft apps.")
        params = {"client_id": client_id, "scope": " ".join(GOOGLE_SCOPES),
                  "access_type": "offline", "prompt": "consent select_account",
                  "include_granted_scopes": "true"}
        if login_hint:
            params["login_hint"] = login_hint
        code, redirect, verifier = browser_sign_in(GOOGLE_AUTH, params, "127.0.0.1",
                                                   cancel, **kw)
        body = _post_token(GOOGLE_TOKEN, {
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "code_verifier": verifier, "grant_type": "authorization_code",
            "redirect_uri": redirect})
        granted = set(body.get("scope", "").split())
        missing = [s for s in GOOGLE_SCOPES[3:] if s not in granted]
        if missing:
            raise AuthError("Please allow every permission on Google's sign-in page. "
                            "Missing: " + ", ".join(s.rsplit("/", 1)[-1] or s
                                                    for s in missing))
        claims = _jwt_claims(body.get("id_token", ""))
        cred = cls(client_id, client_secret, body.get("refresh_token", ""),
                   claims.get("email", ""), claims.get("name", ""))
        cred._cache["google"] = (body["access_token"],
                                 time.time() + float(body.get("expires_in", 3600)))
        return cred

    def _fetch(self, resource: str) -> tuple[str, float]:
        if not self.refresh_token:
            raise AuthError("Google sign-in has expired. Please sign in again.")
        body = _post_token(GOOGLE_TOKEN, {
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        return body["access_token"], float(body.get("expires_in", 3600))


class GoogleServiceAccount(Credential):
    """Domain-wide delegation: act as any user in the Workspace domain."""

    provider = "google"
    admin = True

    def __init__(self, key: dict, subject: str = "", scopes: list[str] | None = None) -> None:
        super().__init__()
        for needed in ("client_email", "private_key"):
            if needed not in key:
                raise AuthError("This is not a Google service account key (JSON). "
                                f"It has no '{needed}'.")
        self.key = key
        self.subject = subject
        self.email = subject
        self.scopes = scopes or GOOGLE_ADMIN_SCOPES
        self.service_email = key["client_email"]

    @classmethod
    def from_file(cls, path: str, admin_email: str) -> "GoogleServiceAccount":
        try:
            with open(path, encoding="utf-8") as fh:
                key = json.load(fh)
        except (OSError, ValueError) as exc:
            raise AuthError(f"Could not read the service account key: {exc}") from exc
        return cls(key, admin_email)

    def for_user(self, email: str) -> "GoogleServiceAccount":
        return GoogleServiceAccount(self.key, email, self.scopes)

    def _fetch(self, resource: str) -> tuple[str, float]:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        if not self.subject:
            raise AuthError("Enter the Workspace user to act as.")
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if self.key.get("private_key_id"):
            header["kid"] = self.key["private_key_id"]
        claims = {"iss": self.service_email, "sub": self.subject,
                  "scope": " ".join(self.scopes),
                  "aud": self.key.get("token_uri", GOOGLE_TOKEN), "iat": now, "exp": now + 3600}
        signing_input = (_b64url(json.dumps(header).encode()) + "." +
                         _b64url(json.dumps(claims).encode())).encode()
        private_key = serialization.load_pem_private_key(self.key["private_key"].encode(),
                                                         password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = signing_input.decode() + "." + _b64url(signature)
        body = _post_token(self.key.get("token_uri", GOOGLE_TOKEN), {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion})
        return body["access_token"], float(body.get("expires_in", 3600))


class MicrosoftUser(Credential):
    provider = "microsoft"

    def __init__(self, client_id: str, tenant: str, refresh_token: str,
                 email: str = "", name: str = "") -> None:
        super().__init__()
        self.client_id, self.tenant = client_id, tenant or "common"
        self.refresh_token = refresh_token
        self.email, self.name = email, name

    @classmethod
    def sign_in(cls, client_id: str, tenant: str = "common", login_hint: str = "",
                cancel: threading.Event | None = None, **kw) -> "MicrosoftUser":
        if not client_id:
            raise AuthError("No Microsoft app ID is configured. Add one in Settings › "
                            "Google & Microsoft apps.")
        tenant = tenant or "common"
        # Consent to both Graph and Exchange IMAP in one go; tokens for each are
        # then obtained with the refresh token.
        params = {"client_id": client_id, "response_mode": "query",
                  "prompt": "select_account",
                  "scope": " ".join(["openid", "email", "profile", "offline_access"] +
                                    MS_GRAPH_SCOPES + [MS_IMAP_SCOPE])}
        if login_hint:
            params["login_hint"] = login_hint
        base = f"{MS_LOGIN}/{tenant}/oauth2/v2.0"
        code, redirect, verifier = browser_sign_in(base + "/authorize", params, "localhost",
                                                   cancel, **kw)
        body = _post_token(base + "/token", {
            "client_id": client_id, "code": code, "code_verifier": verifier,
            "grant_type": "authorization_code", "redirect_uri": redirect,
            "scope": " ".join(["openid", "email", "offline_access"] + MS_GRAPH_SCOPES)})
        claims = _jwt_claims(body.get("id_token", ""))
        email = (claims.get("preferred_username") or claims.get("email") or
                 claims.get("upn") or "")
        cred = cls(client_id, tenant, body.get("refresh_token", ""), email,
                   claims.get("name", ""))
        cred._cache["graph"] = (body["access_token"],
                                time.time() + float(body.get("expires_in", 3600)))
        return cred

    def _fetch(self, resource: str) -> tuple[str, float]:
        if not self.refresh_token:
            raise AuthError("Microsoft sign-in has expired. Please sign in again.")
        scopes = RESOURCE_SCOPES.get(resource)
        if not scopes:
            raise AuthError(f"Unknown Microsoft resource {resource}")
        body = _post_token(f"{MS_LOGIN}/{self.tenant}/oauth2/v2.0/token", {
            "client_id": self.client_id, "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": " ".join(scopes + ["offline_access"])})
        # Microsoft rotates refresh tokens; keep the newest.
        self.refresh_token = body.get("refresh_token", self.refresh_token)
        return body["access_token"], float(body.get("expires_in", 3600))


class MicrosoftApp(Credential):
    """App-only access to every mailbox and drive in a tenant (admin consent)."""

    provider = "microsoft"
    admin = True

    def __init__(self, tenant: str, client_id: str, client_secret: str,
                 user: str = "") -> None:
        super().__init__()
        if not tenant or tenant in ("common", "organizations", "consumers"):
            raise AuthError("Admin mode needs your tenant ID or primary domain "
                            "(for example contoso.onmicrosoft.com).")
        if not client_id or not client_secret:
            raise AuthError("Enter the application (client) ID and a client secret.")
        self.tenant, self.client_id, self.client_secret = tenant, client_id, client_secret
        self.email = user
        self._shared: dict | None = None

    def for_user(self, email: str) -> "MicrosoftApp":
        other = MicrosoftApp(self.tenant, self.client_id, self.client_secret, email)
        other._lock, other._cache = self._lock, self._cache  # one app token for all users
        return other

    def _fetch(self, resource: str) -> tuple[str, float]:
        scope = APP_RESOURCE.get(resource)
        if not scope:
            raise AuthError(f"Unknown Microsoft resource {resource}")
        body = _post_token(f"{MS_LOGIN}/{self.tenant}/oauth2/v2.0/token", {
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "client_credentials", "scope": scope})
        return body["access_token"], float(body.get("expires_in", 3600))


def imap_resource(cred: Credential) -> str:
    return "google" if cred.provider == "google" else "imap"


def api_resource(cred: Credential) -> str:
    return "google" if cred.provider == "google" else "graph"


def check(cred: Credential) -> str:
    """Fetch a token now so problems show up at sign-in time. Returns a description."""
    try:
        cred.token(api_resource(cred))
    except ApiError as exc:
        raise AuthError(str(exc)) from exc
    who = cred.email or "(no user)"
    kind = "admin access" if cred.admin else "signed in"
    return f"{who} — {kind}"


def port_is_free(port: int) -> bool:  # used by tests
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0
