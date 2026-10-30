"""KFTC notice board: list reader (the comment period sits in the list row)."""
from __future__ import annotations

import re
from datetime import timedelta

import dates
import html_util
from html_util import text_of

PAGE_UNIT = 50
MAX_PAGES = 30
LOOK_BACK_DAYS = 180
LIST_URL = ('https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=105&key=193'
            '&pageUnit={unit}&pageIndex={page}')
VIEW_URL = 'https://www.ftc.go.kr/www/selectBbsNttView.do?key=193&bordCd=105&nttSn={id}'

_TBODY_RE = re.compile(r'<tbody\b[^>]*class="[^"]*\btext-center\b[^"]*"[^>]*>(.*?)</tbody>', re.S | re.I)
_TR_RE = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.S | re.I)
_TD_RE = re.compile(r'<td\b[^>]*>(.*?)</td>', re.S | re.I)
_NTT_RE = re.compile(r'[?&;]nttSn=(\d+)')
_TITLE_RE = re.compile(r'<span\b[^>]*class="[^"]*\bp-table__text\b[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
_DATE_RE = re.compile(r'(\d{4})\s*[-.]\s*(\d{1,2})\s*[-.]\s*(\d{1,2})')


def _dates_in(text):
    """ISO dates found in text; [] when any of them is not a real date."""
    out = []
    for m in _DATE_RE.finditer(text or ''):
        try:
            out.append(dates.parse_date('%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))).isoformat())
        except (dates.DateError, ValueError, AttributeError):
            return []
    return out


def parse_period_cell(cell):
    """(from, to) ISO strings from the period cell; None unless it holds exactly two valid dates in order."""
    found = _dates_in(text_of(cell))
    if len(found) != 2 or found[0] > found[1]:
        return None
    return found[0], found[1]


def parse_list(doc) -> list:
    """Rows {id, title, open_from, open_to, url}; open_* are None for an unreadable period cell."""
    body = _TBODY_RE.search(doc or '')
    if not body:
        raise html_util.StructureError('공정위 목록 표를 찾지 못함')
    rows = []
    for tr in _TR_RE.finditer(body.group(1)):
        ntt = _NTT_RE.search(tr.group(1).replace('&amp;', '&'))
        if not ntt:
            continue
        cells = _TD_RE.findall(tr.group(1))
        if len(cells) < 4:
            raise html_util.StructureError('공정위 목록 행의 칸 수가 달라짐')
        title = _TITLE_RE.search(cells[1])
        period = parse_period_cell(cells[3])
        rows.append({'id': ntt.group(1), 'title': text_of(title.group(1) if title else cells[1]),
                     'open_from': period[0] if period else None, 'open_to': period[1] if period else None,
                     'url': VIEW_URL.format(id=ntt.group(1))})
    return rows


def window_start(frm) -> str:
    return (dates.parse_date(frm) - timedelta(days=LOOK_BACK_DAYS)).isoformat()


def collect(session, frm, to):
    """Notices whose list row starts on or after (frm - 180 days). Returns (rows, status, error)."""
    floor = window_start(frm)
    rows = []
    page = 1
    done = False
    while not done and page <= MAX_PAGES:
        try:
            page_rows = parse_list(html_util.get_text(session, LIST_URL.format(unit=PAGE_UNIT, page=page)))
        except html_util.FetchFailed as e:
            partial = bool(rows)
            return rows, ('failed' if partial else e.kind), \
                ('일부만 읽음: %s' % e if partial else '읽지 못함: %s' % e)
        except html_util.StructureError as e:
            partial = bool(rows)
            return rows, 'failed', ('일부만 읽음: %s' % e if partial else '읽지 못함: %s' % e)
        if not page_rows:
            break
        for r in page_rows:
            if r['open_from'] and r['open_from'] < floor:
                done = True
                continue
            rows.append(r)
        if len(page_rows) < PAGE_UNIT:
            break
        page += 1
    return rows, ('ok' if rows else 'empty'), None
