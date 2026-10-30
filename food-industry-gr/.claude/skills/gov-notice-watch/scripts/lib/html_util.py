"""Small HTML helpers and the fetch wrapper shared by the page readers (no page text is kept)."""
from __future__ import annotations

import html
import re

import net


class StructureError(Exception):
    """The expected markers of a source page are absent (the page layout changed)."""


class FetchFailed(Exception):
    """A request did not give HTTP 200. `kind` is 'unread' (robots / offline) or 'failed' (HTTP error)."""

    def __init__(self, message, kind='failed', status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status


_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')
_SCRIPT_RE = re.compile(r'<(script|style)\b.*?</\1\s*>|<!--.*?-->', re.S | re.I)


def clean(s) -> str:
    """HTML-unescape and collapse whitespace."""
    return _WS_RE.sub(' ', html.unescape(s or '')).strip()


def text_of(fragment) -> str:
    """Tags replaced by spaces, then entities unescaped and whitespace collapsed."""
    return clean(_TAG_RE.sub(' ', fragment or ''))


def page_text(doc) -> str:
    """Visible text of a whole page (script/style/comments removed)."""
    return text_of(_SCRIPT_RE.sub(' ', doc or ''))


def get_text(session, url) -> str:
    """GET through the session; anything but HTTP 200 raises FetchFailed."""
    try:
        r = session.get(url)
    except net.Blocked as e:
        raise FetchFailed('robots.txt 가 허용하지 않음 (%s)' % e.reason, kind='unread') from None
    except net.OfflineError:
        raise FetchFailed('오프라인 설정이라 요청하지 않음', kind='unread') from None
    if r.status != 200:
        raise FetchFailed('HTTP %s' % r.status + (' (%s)' % r.error if r.error else ''), kind='failed', status=r.status)
    return r.text()
