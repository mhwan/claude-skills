"""MFDS notice board: list reader and detail reader (only the deadline phrase is kept)."""
from __future__ import annotations

import re
from datetime import timedelta

import dates
import html_util
import notices
from html_util import clean, page_text, text_of

LIST_URL = 'https://www.mfds.go.kr/brd/m_209/list.do?page={page}'
VIEW_URL = 'https://www.mfds.go.kr/brd/m_209/view.do?seq={seq}'
MAX_PAGES = 30
LOOK_BACK_DAYS = 180
EVIDENCE_MAX = 80

_ANCHOR_RE = re.compile(r'<a\s([^>]*)>(.*?)</a>', re.S | re.I)
_HREF_RE = re.compile(r'href\s*=\s*"([^"]*view\.do\?[^"]*)"', re.I)
_TITLE_CLASS_RE = re.compile(r'class\s*=\s*"[^"]*\btitle\b[^"]*"', re.I)
_SEQ_RE = re.compile(r'[?&;]seq=(\d+)')
_RIGHT_RE = re.compile(r'<div\b[^>]*class="[^"]*\bright_column\b[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
_BASIS_RE = re.compile(r'행정절차법\s*」?\s*제\s*(41|46)\s*조')
_ARTICLE_RE = re.compile(r'<div\b[^>]*class="[^"]*\bbv_cont\b[^"]*"[^>]*>', re.I)
_FILE_BOX_RE = re.compile(r'<div\b[^>]*class="[^"]*\bbv_file_box\b[^"]*"', re.I)
_BASIS_KIND = {'41': '입법예고', '46': '행정예고'}


def _iso(d):
    return d.isoformat() if hasattr(d, 'isoformat') else str(d)


def parse_list(doc) -> list:
    """Rows {seq, posted, title, url} of one list page. Raises StructureError when no row is found."""
    anchors = []
    for m in _ANCHOR_RE.finditer(doc or ''):
        href = _HREF_RE.search(m.group(1))
        if not href or not _TITLE_CLASS_RE.search(m.group(1)):
            continue
        seq = _SEQ_RE.search(href.group(1).replace('&amp;', '&'))
        if seq:
            anchors.append((m, seq.group(1)))
    rows = []
    for i, (m, seq) in enumerate(anchors):
        end = anchors[i + 1][0].start() if i + 1 < len(anchors) else len(doc)
        right = _RIGHT_RE.search(doc[m.end():end])
        posted = dates.parse_loose(text_of(right.group(1))) if right else None
        if posted is None:
            continue
        rows.append({'seq': seq, 'posted': _iso(posted), 'title': text_of(m.group(2)),
                     'url': VIEW_URL.format(seq=seq)})
    if not rows:
        raise html_util.StructureError('식약처 목록에서 행을 찾지 못함')
    return rows


def _all_until_dates(text):
    """Every deadline phrase in text, in page order: [(date, matched substring, start)]."""
    hits = []
    for pat in dates._UNTIL_PATTERNS:
        for m in pat.finditer(text):
            try:
                d = dates.parse_date('%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except dates.DateError:
                continue
            hits.append((d, m.group(0), m.start()))
    hits.sort(key=lambda h: h[2])
    return hits


def _fragment(text, start, matched):
    """A short piece of the text around the matched phrase (never longer than EVIDENCE_MAX)."""
    end = start + len(matched)
    room = max(EVIDENCE_MAX - len(matched), 0)
    left = max(start - room // 2, 0)
    piece = text[left:end + (room - (start - left))]
    return clean(piece)[:EVIDENCE_MAX]


def parse_detail(doc, posted) -> dict:
    """{kind, open_to, evidence, body} from a detail page.

    The body is returned for the summary step only; the caller keeps it out of the data file.
    """
    start = _ARTICLE_RE.search(doc or '')
    if not start:
        raise html_util.StructureError('식약처 본문 영역을 찾지 못함')
    tail = doc[start.end():]
    box = _FILE_BOX_RE.search(tail)
    body = page_text(tail[:box.start()] if box else tail)
    basis = _BASIS_RE.search(body)
    kind = _BASIS_KIND.get(basis.group(1)) if basis else None
    open_to, evidence = None, None
    posted_iso = str(posted) if posted else ''
    for d, matched, pos in _all_until_dates(body):
        iso = _iso(d)
        if posted_iso and iso < posted_iso:
            continue
        open_to, evidence = iso, _fragment(body, pos, matched)
        break
    return {'kind': kind, 'open_to': open_to, 'evidence': evidence, 'body': body}


def window_start(frm) -> str:
    return _iso(dates.parse_date(frm) - timedelta(days=LOOK_BACK_DAYS))


def collect(session, frm, to):
    """Notices posted from (frm - 180 days) on, with kind and deadline read from each detail page.

    Returns (rows, status, error, warnings, evidence) where rows are {seq, posted, title, url, kind, open_to}
    and evidence is {seq: fragment}. Detail failures leave open_to None and add a warning.
    """
    floor = window_start(frm)
    rows, warnings, evidence = [], [], {}
    before = session.requests_made
    done = False
    page = 1
    while not done and page <= MAX_PAGES:
        try:
            doc = html_util.get_text(session, LIST_URL.format(page=page))
            page_rows = parse_list(doc)
        except html_util.FetchFailed as e:
            partial = bool(rows)
            return rows, ('failed' if partial else e.kind), \
                ('일부만 읽음: %s' % e if partial else '읽지 못함: %s' % e), warnings, evidence
        except html_util.StructureError as e:
            partial = bool(rows)
            return rows, 'failed', ('일부만 읽음: %s' % e if partial else '읽지 못함: %s' % e), warnings, evidence
        for r in page_rows:
            if r['posted'] < floor:
                done = True
                continue
            r = dict(r, kind=notices.notice_kind(r['title']), open_to=None)
            try:
                detail = parse_detail(html_util.get_text(session, r['url']), r['posted'])
            except (html_util.FetchFailed, html_util.StructureError) as e:
                warnings.append('식약처 %s: 본문을 읽지 못해 의견 마감일은 확인 필요 (%s)' % (r['seq'], e))
                rows.append(r)
                continue
            r['kind'] = r['kind'] or detail['kind']
            r['open_to'] = detail['open_to']
            r['body'] = detail['body']
            if detail['evidence']:
                evidence[r['seq']] = detail['evidence']
            rows.append(r)
        page += 1
    return rows, ('ok' if rows else 'empty'), None, warnings, evidence
