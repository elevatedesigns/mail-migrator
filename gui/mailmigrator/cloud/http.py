"""HTTP client shared by the Google and Microsoft Graph adapters.

Retries throttling (429) and transient server errors with the delay the
service asks for, follows paging, and turns errors into readable messages.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Callable, Iterator

import requests

USER_AGENT = "MailMigrator (+https://github.com/elevatedesigns/mail-migrator)"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8


class Stopped(Exception):
    """The user pressed Stop."""


class ApiError(Exception):
    def __init__(self, status: int, message: str, url: str = "", code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.url = url
        self.code = code

    def __str__(self) -> str:
        return f"{self.message} (HTTP {self.status})" if self.status else self.message


def _error_message(resp: requests.Response) -> tuple[str, str]:
    try:
        data = resp.json()
    except ValueError:
        return (resp.text.strip()[:300] or resp.reason or "Request failed"), ""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):  # Google and Graph both use {"error": {...}}
        return (str(err.get("message") or err.get("status") or err.get("code") or data),
                str(err.get("code") or err.get("status") or ""))
    if isinstance(err, str):  # OAuth token endpoints
        return str(data.get("error_description") or err), err
    return str(data)[:300], ""


class Client:
    """A requests session bound to a token source (callable returning a bearer token)."""

    def __init__(self, token: Callable[[], str] | None = None,
                 stop: threading.Event | None = None, timeout: float = 120,
                 session: requests.Session | None = None) -> None:
        self._token = token
        self.stop = stop or threading.Event()
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._sleep = time.sleep

    def check_stop(self) -> None:
        if self.stop.is_set():
            raise Stopped()

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_stop()
            self._sleep(min(0.5, max(0.0, end - time.monotonic())))

    def request(self, method: str, url: str, *, auth: bool = True, retry_auth: bool = True,
                **kw) -> requests.Response:
        headers = dict(kw.pop("headers", None) or {})
        kw.setdefault("timeout", self.timeout)
        body = kw.get("data")
        attempt = 0
        while True:
            self.check_stop()
            attempt += 1
            if auth and self._token:
                headers["Authorization"] = f"Bearer {self._token()}"
            if hasattr(body, "seek"):
                body.seek(0)
            try:
                resp = self.session.request(method, url, headers=headers, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= MAX_ATTEMPTS:
                    raise ApiError(0, f"Network error: {exc}", url) from exc
                self._wait(min(60, 2 ** attempt) + random.random())
                continue
            if resp.status_code == 401 and auth and retry_auth and attempt == 1 and \
                    hasattr(self._token, "invalidate"):
                self._token.invalidate()  # token revoked or expired early: refresh once
                continue
            if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                delay = resp.headers.get("Retry-After")
                try:
                    wait = float(delay) if delay else min(60, 2 ** attempt)
                except ValueError:
                    wait = min(60, 2 ** attempt)
                self._wait(wait + random.random())
                continue
            if resp.status_code >= 400:
                message, code = _error_message(resp)
                raise ApiError(resp.status_code, message, url, code)
            return resp

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def json(self, method: str, url: str, **kw):
        resp = self.request(method, url, **kw)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def pages(self, url: str, items_key: str, params: dict | None = None,
              next_key: str = "nextPageToken", **kw) -> Iterator[dict]:
        """Yield items across pages for both paging styles.

        Google: ``nextPageToken`` passed back as ``pageToken``.
        Graph: ``@odata.nextLink`` is the full URL of the next page.
        """
        params = dict(params or {})
        while url:
            data = self.json("GET", url, params=params, **kw)
            for item in data.get(items_key, []) or []:
                yield item
            if next_key == "@odata.nextLink":
                url, params = data.get("@odata.nextLink"), None
            else:
                token = data.get(next_key)
                if not token:
                    return
                params["pageToken"] = token
