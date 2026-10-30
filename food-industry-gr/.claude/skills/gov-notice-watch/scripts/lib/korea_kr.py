"""korea.kr ministry press releases: list-page reader (dated queries, one agency code at a time)."""
from __future__ import annotations

import re
from urllib.parse import urlencode

import dates
import html_util
from html_util import clean, text_of

LIST_URL = 'https://www.korea.kr/briefing/pressReleaseList.do'
VIEW_URL = 'https://www.korea.kr/briefing/pressReleaseView.do?newsId={id}'
MAX_PAGES = 30

_ANCHOR_RE = re.compile(r'<a\s([^>]*)>(.*?)</a>', re.S | re.I)
_HREF_RE = re.compile(r'href\s*=\s*"([^"]*pressReleaseView\.do\?[^"]*)"', re.I)
_NEWS_ID_RE = re.compile(r'[?&;]newsId=(\d+)')
_STRONG_RE = re.compile(r'<strong\b[^>]*>(.*?)</strong>', re.S | re.I)
_SOURCE_RE = re.compile(r'<span\s+class="source"[^>]*>(.*)', re.S | re.I)
_SPAN_RE = re.compile(r'<span\b[^>]*>([^<]*)</span>', re.S | re.I)
_TOTAL_RE = re.compile(r'검색결과\s*총\s*<strong>\s*([\d,]+)\s*</strong>\s*건')


def list_url(code, page, frm=None, to=None) -> str:
    """List-page URL for one agency code; a dated query when frm is given."""
    params = [('repCodeType', 'D'), ('repCode', code)]
    if frm:
        params += [('startDate', frm), ('endDate', to)]
    params.append(('pageIndex', str(page)))
    return LIST_URL + '?' + urlencode(params)


def parse_total(doc):
    """Result count printed on the page; None when the marker is absent (the page layout changed)."""
    m = _TOTAL_RE.search(doc or '')
    if not m:
        return None
    return int(m.group(1).replace(',', ''))


def parse_list(doc) -> list:
    """Rows {id, title, date, dept, url} of one list page; [] when the page has no recognisable rows."""
    rows = []
    for m in _ANCHOR_RE.finditer(doc or ''):
        href = _HREF_RE.search(m.group(1))
        src = _SOURCE_RE.search(m.group(2))
        if not href or not src:
            continue
        nid = _NEWS_ID_RE.search(href.group(1).replace('&amp;', '&'))
        strong = _STRONG_RE.search(m.group(2))
        if not nid or not strong:
            continue
        day, dept = None, ''
        for span in _SPAN_RE.finditer(src.group(1)):
            value = clean(span.group(1))
            if not value:
                continue
            if day is None:
                parsed = dates.parse_loose(value)
                if parsed:
                    day = parsed if isinstance(parsed, str) else parsed.isoformat()
                continue
            dept = value
            break
        if day is None:
            continue
        rows.append({'id': nid.group(1), 'title': text_of(strong.group(1)), 'date': day, 'dept': dept,
                     'url': VIEW_URL.format(id=nid.group(1))})
    return rows


def collect(session, agencies, frm, to):
    """Press releases of the given agencies in frm..to.

    agencies: [{'name', 'korea_kr'}]. Returns (rows, status, error, requests); rows keep the first agency
    that listed a joint release. status is 'ok' / 'empty' / 'unread' / 'failed'.
    """
    rows, seen = [], set()
    requests_before = session.requests_made
    first_error = None
    for ag in agencies:
        code = ag.get('korea_kr')
        if not code:
            continue
        received, total, page = 0, None, 1
        while page <= MAX_PAGES:
            try:
                doc = html_util.get_text(session, list_url(code, page, frm, to))
            except html_util.FetchFailed as e:
                kind = e.kind if received == 0 and not rows else 'failed'
                msg = '읽지 못함: %s' % e if received == 0 and not rows else '일부만 읽음: %s' % e
                return rows, kind, msg, session.requests_made - requests_before
            total = parse_total(doc) if total is None else total
            if total is None:
                return rows, 'failed', '읽지 못함: 목록 모양이 달라짐 (%s)' % ag.get('name', code), \
                    session.requests_made - requests_before
            page_rows = parse_list(doc)
            received += len(page_rows)
            for r in page_rows:
                if r['id'] in seen:
                    continue
                seen.add(r['id'])
                rows.append(r)
            if received >= total or not page_rows:
                break
            page += 1
    status = 'ok' if rows else 'empty'
    return rows, status, first_error, session.requests_made - requests_before
