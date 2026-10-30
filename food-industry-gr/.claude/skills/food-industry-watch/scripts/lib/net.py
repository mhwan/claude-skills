# Changed from fiw scripts/lib/net.py: offline mode is also switched on by GR_OFFLINE=1 (FIW_OFFLINE=1 still works); one gr-compat ignore marker on the robots.txt URL line (a URL, not a path); nothing else.
"""The only network gateway: fetch(), test stubs, offline mode and robots.txt checks.

This is the original copy. Each skill vendors it as scripts/lib/net.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.
Standard library only, Python 3.9+.
"""
from __future__ import annotations

import contextlib
import http.client
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Optional


class OfflineError(RuntimeError):
    """A real request was attempted while GR_OFFLINE=1 (or the older FIW_OFFLINE=1)."""


@dataclass
class Response:
    url: str
    status: int                      # 0 = no HTTP response
    headers: dict = field(default_factory=dict)   # lower-case keys
    body: bytes = b''
    error: Optional[str] = None

    def text(self) -> str:
        charset = 'utf-8'
        ctype = self.headers.get('content-type', '')
        for part in ctype.split(';')[1:]:
            key, _, value = part.strip().partition('=')
            if key.lower() == 'charset' and value:
                charset = value.strip('"\' ')
        try:
            return self.body.decode(charset, errors='replace')
        except LookupError:
            return self.body.decode('utf-8', errors='replace')


_stub = None


def set_stub(fn):
    global _stub
    _stub = fn


def clear_stub():
    set_stub(None)


@contextlib.contextmanager
def stubbed(fn):
    previous = _stub
    set_stub(fn)
    try:
        yield
    finally:
        set_stub(previous)


def default_ua_token() -> str:
    return f'Python-urllib/{sys.version_info[0]}.{sys.version_info[1]}'


def _lower_headers(items) -> dict:
    return {k.lower(): v for k, v in items}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return a 3xx as an HTTPError (status + Location) instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen(req, *, timeout, context):
    """urllib urlopen with certificate verification and without automatic redirects."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect)
    return opener.open(req, timeout=timeout)


def _transport(url, method, body, headers, timeout) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return Response(url=r.geturl(), status=r.status, headers=_lower_headers(r.headers.items()), body=r.read())
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except (OSError, http.client.HTTPException):
            body = b''
        return Response(url=url, status=e.code, headers=_lower_headers((e.headers or {}).items()), body=body)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        return Response(url=url, status=0, error=str(e))


def fetch(url, *, method='GET', data=None, headers=None, timeout=20) -> Response:
    """One request, no retry. The default urllib User-Agent is never changed."""
    headers = dict(headers or {})
    if any(k.lower() == 'user-agent' for k in headers):
        raise ValueError('the User-Agent header must not be set')
    if _stub is not None:
        return _stub(url, method, data, headers)
    for flag in ('GR_OFFLINE', 'FIW_OFFLINE'):
        if os.environ.get(flag) == '1':
            raise OfflineError(f'{flag}=1: refusing to request {url}')
    body = data
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode('utf-8')
        if not any(k.lower() == 'content-type' for k in headers):
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
    return _transport(url, method, body, headers, timeout)


def _verdict(status, lines, url):
    if status == 200:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(lines)
        return (True, 'allowed') if rp.can_fetch(default_ua_token(), url) else (False, 'disallowed')
    if status in (404, 410):
        return True, 'no_robots'
    return False, f'robots_unreadable:{status}'


class RobotsCache:
    """robots.txt decisions for one run; one robots request per scheme://netloc."""

    def __init__(self, policy=None):
        self._policy = {h['host'].lower(): h for h in (policy or [])}
        self._robots = {}   # host key -> (status, lines)

    def check(self, url) -> tuple:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or '').lower()
        key = f'{parts.scheme}://{parts.netloc}'.lower()
        rule = self._policy.get(host)
        if rule is not None:
            mode = rule['mode']
            if mode in ('never_request', 'no_check'):
                return False, f'policy:{mode}'
            if mode == 'robots_seed':
                seed = rule['robots_seed']
                return _verdict(seed['status'], list(seed['lines']), url)
        if key not in self._robots:
            r = fetch(key + '/robots.txt')  # gr-compat: ignore=pathjoin  (a URL, not a file path)
            self._robots[key] = (r.status, r.text().splitlines() if r.status == 200 else [])
        status, lines = self._robots[key]
        return _verdict(status, lines, url)


class Blocked(Exception):
    """A request was refused before it was made (robots.txt or link policy)."""

    def __init__(self, reason, url):
        super().__init__(f'{reason}: {url}')
        self.reason = reason
        self.url = url


REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 3


def _host_key(url) -> str:
    parts = urllib.parse.urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'.lower()


class Session:
    """One per collect run: robots check before every request, per-host pacing, own redirects."""

    def __init__(self, policy=None, interval=0.5, *, sleep=None, clock=None):
        self.robots = RobotsCache(policy)
        self.interval = interval
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._last = {}            # host key -> clock() of the last request
        self.requests_made = 0     # data requests (robots.txt requests are not counted)

    def get(self, url, **kw) -> Response:
        return self._request('GET', url, None, **kw)

    def post(self, url, data, **kw) -> Response:
        return self._request('POST', url, data, **kw)

    def _wait(self, key):
        last = self._last.get(key)
        if last is not None and self.interval > 0:
            elapsed = self._clock() - last
            if elapsed < self.interval:
                self._sleep(self.interval - elapsed)

    def _request(self, method, url, data, headers=None, timeout=20) -> Response:
        for hop in range(MAX_REDIRECTS + 1):
            allowed, reason = self.robots.check(url)
            if not allowed:
                raise Blocked(reason, url)
            key = _host_key(url)
            self._wait(key)
            r = fetch(url, method=method, data=data, headers=headers, timeout=timeout)
            self._last[key] = self._clock()
            self.requests_made += 1
            location = r.headers.get('location') if r.status in REDIRECT_STATUSES else None
            if not location:
                r.url = url
                return r
            if hop == MAX_REDIRECTS:
                return Response(url=url, status=0, error='too many redirects')
            url = urllib.parse.urljoin(url, location)
            if r.status == 303 or (r.status in (301, 302) and method == 'POST'):
                method, data = 'GET', None
        raise AssertionError('unreachable')
