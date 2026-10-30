# Changed from fiw scripts/lib/dates.py: Hangul removed from comments and docstrings (English only); code unchanged.
"""Date parsing, period arithmetic and deadline extraction.

This is the original copy. Each skill vendors it as scripts/lib/dates.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.
Standard library only, Python 3.9+.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

ONE_DAY = timedelta(days=1)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class DateError(ValueError):
    """A date string or a date range is invalid."""


def parse_date(s) -> date:
    """Parse exactly YYYY-MM-DD as a real calendar date."""
    if not isinstance(s, str) or not _ISO_RE.match(s):
        raise DateError(f'not a YYYY-MM-DD date: {s!r}')
    try:
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        raise DateError(f'not a calendar date: {s!r}') from None


def fmt(d: date) -> str:
    return d.isoformat()


def now_iso() -> str:
    """Current local time, ISO 8601 with offset, second precision."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def check_range(frm: date, to: date, today: date) -> None:
    if frm > to:
        raise DateError(f'from {fmt(frm)} is after to {fmt(to)}')
    if to > today:
        raise DateError('future')


def _month_end(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - ONE_DAY).day


def add_months(d: date, n: int) -> date:
    """Shift by calendar months; the day is clamped to the month end."""
    idx = d.year * 12 + (d.month - 1) + n
    year, month = divmod(idx, 12)
    month += 1
    return date(year, month, min(d.day, _month_end(year, month)))


def monthly_segments(frm: date, to: date) -> list:
    """Contiguous monthly segments covering [frm, to], anchored on `to`, oldest first."""
    if frm > to:
        raise DateError(f'from {fmt(frm)} is after to {fmt(to)}')
    segments = []
    k = 0
    while True:
        end = add_months(to, -k)
        start = max(add_months(to, -(k + 1)) + ONE_DAY, frm)
        segments.append((start, end))
        if start == frm:
            break
        k += 1
    segments.reverse()
    return segments


def quarter_proposal(today: date) -> list:
    """The three monthly segments of the quarter that ends today."""
    frm = add_months(today, -3) + ONE_DAY
    return monthly_segments(frm, today)


_LOOSE_PATTERNS = [
    re.compile(r'^(\d{4})-(\d{2})-(\d{2})$'),
    re.compile(r'^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?$'),
    re.compile(r'^(\d{4})/(\d{2})/(\d{2})$'),
    re.compile(r'^(\d{4})(\d{2})(\d{2})$'),
    re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$'),
    re.compile(r'^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$'),
]


def parse_loose(s):
    """Parse the date formats seen in collector sources; None when not a valid date."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    for pat in _LOOSE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        parts = [int(g) if g is not None else 0 for g in m.groups()]
        try:
            if len(parts) > 3:
                return datetime(*parts).date()
            return date(*parts)
        except ValueError:
            return None
    return None


_WEEKDAY = r'(?:\(\s*[월화수목금토일]\s*\)\s*)?'      # optional parenthesised weekday (one Hangul letter) before the deadline word
_UNTIL_PATTERNS = [
    re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*' + _WEEKDAY + '까지'),
    re.compile(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*' + _WEEKDAY + '까지'),
]


def find_until_date(text):
    """First 'until <date>' deadline phrase in text (the Korean deadline word): (date, matched substring) or None."""
    if not text:
        return None
    hits = []
    for pat in _UNTIL_PATTERNS:
        for m in pat.finditer(text):
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            hits.append((m.start(), d, m.group(0)))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    return hits[0][1], hits[0][2]


def short_day(iso, end_iso) -> str:
    """'MM-DD', or the full 'YYYY-MM-DD' when the year differs from end_iso's year."""
    return iso[5:] if iso[:4] == end_iso[:4] else iso


def short_span(frm, to, end_iso) -> str:
    """short_day(frm)~short_day(to), or one day when frm == to."""
    if frm == to:
        return short_day(frm, end_iso)
    return f'{short_day(frm, end_iso)}~{short_day(to, end_iso)}'


# A day written without its month (a bare "8th" or "from the 1st" in Korean): not "October 8", "10-08", "8~9"
# or "13.8", and not a span (N days, N days left, within N days, a suspension of N days).
MONTHLESS_DAY = re.compile(r'(?<![\d월.\-/~])(?<!월 )(?<!정지 )(?<!정지)\d{1,2}일(?!\s*(?:간|동안|이내|남음|째))')


def day_basis(text, ref_dates, end_iso) -> str:
    """'(MM-DD <report word>)': the article days of a sentence whose day has no month; '' when it has none."""
    if not ref_dates or not MONTHLESS_DAY.search(text):
        return ''
    return '(' + '·'.join(short_day(d, end_iso) for d in sorted(set(ref_dates))) + ' 보도)'


def with_basis(text, basis) -> str:
    """text with basis put before its final period."""
    if not basis:
        return text
    return text[:-1] + basis + '.' if text.endswith('.') else text + basis


def overlaps(a_from, a_to, b_from, b_to) -> bool:
    """Inclusive overlap test; None means open-ended on that side."""
    if a_from is not None and b_to is not None and a_from > b_to:
        return False
    if b_from is not None and a_to is not None and b_from > a_to:
        return False
    return True
