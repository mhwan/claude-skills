# Taken from scripts/naver_list.py of the work folder (the emergency list for BigKinds downtime): same constants, outlet rules, paging and stop rules; search() takes the page fetcher as an argument so a caller can count or replay requests; errors carry a kind; offline mode refuses a real request.
"""Naver news search API: newest-first paging that keeps only title, link, outlet and time.

This is the original copy. A skill vendors it as scripts/lib/naver_news.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.
Standard library only, Python 3.9+.

Rules kept here on purpose:
  * the excerpt (description) of an answer is never read into an item, so it cannot be kept;
  * the User-Agent is never changed;
  * any HTTP error stops the search: no retry, no other request in its place;
  * GR_OFFLINE=1 refuses a real request (tests pass their own fetch function instead).
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

API_URL = 'https://naverapihub.apigw.ntruss.com/search/v1/news'
ID_ENV = 'NCP_APIGW_API_KEY_ID'
SECRET_ENV = 'NCP_APIGW_API_KEY'

KST = timezone(timedelta(hours=9))
PAGE_SIZE = 100
# API limits: start 1..1000, display 1..100, so one query can see about 1,100 articles, newest first.
PAGE_STARTS = list(range(1, 1000, PAGE_SIZE)) + [1000]
REQUEST_GAP_SEC = 0.2
DAILY_LIMIT = 25000

# A domain match alone would also take affiliates (biz.chosun.com, sports.chosun.com ...): the host prefix is checked too.
OUTLET_DOMAINS = {
    'chosun.com': '조선일보',
    'joongang.co.kr': '중앙일보',
    'donga.com': '동아일보',
    'hani.co.kr': '한겨레',
    'khan.co.kr': '경향신문',
    'mk.co.kr': '매일경제',
    'hankyung.com': '한국경제',
}
OUTLETS = list(OUTLET_DOMAINS.values())
MAIN_SUBDOMAINS = ('', 'www', 'm', 'news')

# How far one query got. complete = paged past the first day; truncated = hit the paging limit after seeing
# some articles of the period; unreached = hit the paging limit before reaching the period at all.
STATUSES = ('complete', 'truncated', 'unreached')
STATUS_LABELS = {'complete': '기간 끝까지 확인', 'truncated': '일부만 확인(잘림)', 'unreached': '기간에 닿지 못함'}

TAG_RE = re.compile(r'<[^>]+>')


class NaverError(Exception):
    """The API answered with an error or could not be reached. Stop; do not retry or work around it.

    kind: key (401), denied (403), limit (429), http (other HTTP errors), network, answer (not JSON), offline.
    """

    def __init__(self, kind, message, status=0):
        Exception.__init__(self, message)
        self.kind = kind
        self.status = status


class NoKeys(Exception):
    """The key environment variables are not set (their names are in the message, never their values)."""


def clean(text):
    """Strip <b> marks and HTML entities. An entity may hide a tag, so tags are stripped again afterwards."""
    return TAG_RE.sub('', html.unescape(TAG_RE.sub('', text or ''))).strip()


def outlet_of(url):
    """The outlet name when the address is one of the 7 main papers, else None (affiliates are None too)."""
    host = (urllib.parse.urlsplit(url or '').hostname or '').lower()
    for domain, name in OUTLET_DOMAINS.items():
        if host == domain:
            return name
        if host.endswith('.' + domain):
            return name if host[:-len(domain) - 1] in MAIN_SUBDOMAINS else None
    return None


def host_of(url):
    host = (urllib.parse.urlsplit(url or '').hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def parse_pubdate(value):
    """pubDate (RFC 822) as a KST datetime, or None."""
    try:
        return parsedate_to_datetime(value).astimezone(KST)
    except (TypeError, ValueError):
        return None


def keys_present():
    """(id set, secret set) as booleans; the values are never returned."""
    return bool(os.environ.get(ID_ENV)), bool(os.environ.get(SECRET_ENV))


def load_keys():
    client_id = os.environ.get(ID_ENV)
    secret = os.environ.get(SECRET_ENV)
    missing = [name for name, val in ((ID_ENV, client_id), (SECRET_ENV, secret)) if not val]
    if missing:
        raise NoKeys('missing: ' + ', '.join(missing))
    return client_id, secret


def describe_error(http_error):
    """The error code and message of an error answer (two answer shapes are documented)."""
    try:
        body = json.loads(http_error.read().decode('utf-8'))
    except (ValueError, OSError, AttributeError):
        return ''
    if not isinstance(body, dict):
        return ''
    err = body.get('error') if isinstance(body.get('error'), dict) else body
    code = err.get('errorCode', '')
    message = err.get('message') or err.get('errorMessage') or ''
    return ('%s %s' % (code, message)).strip()[:200]


def error_kind(status):
    return {401: 'key', 403: 'denied', 429: 'limit'}.get(status, 'http')


def fetch_page(query, start, keys, timeout=20):
    """One answer page as a dict. The User-Agent is left as it is."""
    if os.environ.get('GR_OFFLINE') == '1':
        raise NaverError('offline', 'offline mode (GR_OFFLINE=1): no request was sent (query: %s)' % query)
    params = urllib.parse.urlencode({
        'query': query, 'display': PAGE_SIZE, 'start': start,
        'sort': 'date', 'format': 'json',
    }, quote_via=urllib.parse.quote)
    req = urllib.request.Request('%s?%s' % (API_URL, params), headers={
        'X-NCP-APIGW-API-KEY-ID': keys[0],
        'X-NCP-APIGW-API-KEY': keys[1],
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise NaverError(error_kind(e.code), 'HTTP %d %s (query: %s)' % (e.code, describe_error(e), query), e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NaverError('network', '%s: %s (query: %s)' % (type(e).__name__, e, query))
    except ValueError as e:
        raise NaverError('answer', 'the answer is not JSON: %s (query: %s)' % (e, query))


def search(query, first_day, last_day, fetch, gap=REQUEST_GAP_SEC, sleep=time.sleep):
    """Page newest first and collect the articles published from first_day to last_day (KST dates).

    fetch(query, start) returns one answer page (a dict). Returns (items, status); status is one of STATUSES.
    An item is {title, url, original, published (datetime), query}; the excerpt is never read.
    """
    items = []
    seen_in_period = False
    for i, start in enumerate(PAGE_STARTS):
        if i and gap:
            sleep(gap)
        answer = fetch(query, start)
        page = (answer.get('items') if isinstance(answer, dict) else None) or []
        oldest = None
        for raw in page:
            if not isinstance(raw, dict):
                continue
            published = parse_pubdate(raw.get('pubDate'))
            if published is None:
                continue
            day = published.date()
            oldest = day if oldest is None or day < oldest else oldest
            if first_day <= day <= last_day:
                seen_in_period = True
                items.append({
                    'title': clean(raw.get('title')),
                    'url': raw.get('originallink') or raw.get('link') or '',
                    'original': raw.get('originallink') or '',
                    'published': published,
                    'query': query,
                })
        if len(page) < PAGE_SIZE or (oldest is not None and oldest < first_day):
            return items, 'complete'
    return items, 'truncated' if seen_in_period else 'unreached'


def dedupe(items):
    """Keep the first item of each url, in order."""
    seen = {}
    for item in items:
        seen.setdefault(item['url'], item)
    return list(seen.values())
