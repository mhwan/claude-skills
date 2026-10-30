"""Primitives for the evidence gate: numbers, quotes, dates and fragments against source text."""
from __future__ import annotations

import re
import unicodedata

_DIGIT_COMMA = re.compile(r'(?<=\d),(?=\d)')
_NUMBER = re.compile(r'\d+(?:\.\d+)?')


def norm_space(s) -> str:
    s = unicodedata.normalize('NFKC', s or '')
    return re.sub(r'\s+', ' ', s).strip()


def number_tokens(text) -> set:
    """Number tokens; commas are removed only between digits. No unit is exempt."""
    s = unicodedata.normalize('NFKC', text or '')
    s = _DIGIT_COMMA.sub('', s)
    return set(_NUMBER.findall(s))


def missing_numbers(text, sources) -> list:
    have = set()
    for src in sources:
        have |= number_tokens(src)
    return sorted(number_tokens(text) - have)


QUOTE_STYLES = (
    ('"', '"'),
    ('“', '”'),
    ("'", "'"),
    ('‘', '’'),
    ('「', '」'),
)

_QUOTE_RE = re.compile('|'.join(
    f'({re.escape(o)}([^{re.escape(c)}\\n]{{1,120}}){re.escape(c)})' for o, c in QUOTE_STYLES))


def find_quotes(text) -> list:
    """(style, inner) pairs in order of appearance; style is open + close mark, e.g. '“”'."""
    found = []
    for m in _QUOTE_RE.finditer(text or ''):
        for i, (o, c) in enumerate(QUOTE_STYLES):
            inner = m.group(2 * i + 2)
            if inner is not None:
                found.append((o + c, inner))
                break
    return found


def missing_quotes(text, sources) -> list:
    hay = [norm_space(s) for s in sources]
    return [inner for _, inner in find_quotes(text)
            if not any(norm_space(inner) in h for h in hay)]


def quote_chars(text) -> int:
    return sum(len(inner) for _, inner in find_quotes(text))


_DATE_RE = re.compile(
    r'(?<!\d)(?:'
    r'\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일'
    r'|\d{1,2}\s*월\s*\d{1,2}\s*일'
    r'|\d{4}-\d{2}-\d{2}'
    r'|\d{4}\.\d{1,2}\.\d{1,2}'
    r')(?!\d)')


def find_dates(text) -> list:
    """Date expressions as written."""
    return _DATE_RE.findall(text or '')


def _no_space(s) -> str:
    return re.sub(r'\s+', '', s or '')


def missing_dates(text, sources) -> list:
    hay = [_no_space(s) for s in sources]
    return [d for d in find_dates(text) if not any(_no_space(d) in h for h in hay)]


def fragment_in(fragment, sources, max_len=80) -> tuple:
    """Is fragment a verbatim (whitespace-normalised) substring of a source?"""
    n = norm_space(fragment)
    if not n:
        return False, 'empty'
    if len(n) > max_len:
        return False, 'too_long'
    if any(n in norm_space(s) for s in sources):
        return True, 'ok'
    return False, 'not_found'


# ---------------------------------------------------------------- bodies that look cut (M8 0-C j)
CUT_MIN_LINE = 40        # a last line shorter than this may end without a sentence ending (caption, label)
BYLINE_MAX = 80          # only lines up to this length are taken for a byline or copyright line
BYLINE_MARKS = ('@', '기자', '특파원', 'ⓒ', '©', '(c)', '저작권', '무단')
_ENDING = re.compile(r'(?:[.!?…"\'”’)\]』」》>】]|[다요죠음함됨임까]\.?)$')


def _content_lines(body) -> list:
    """Non-empty lines without the trailing byline and copyright lines."""
    lines = [ln.strip() for ln in (body or '').splitlines() if ln.strip()]
    while lines and len(lines[-1]) <= BYLINE_MAX and any(m in lines[-1] for m in BYLINE_MARKS):
        lines.pop()
    return lines


def ends_sentence(body) -> bool:
    """True unless the last content line is long and has no sentence ending."""
    lines = _content_lines(body)
    if not lines:
        return False
    last = lines[-1]
    return len(last) < CUT_MIN_LINE or bool(_ENDING.search(last))


def cut_suspect(body, src_chars=None):
    """Why a body looks cut (no body text in the reason), else None.

    src_chars: the length the browser job read; a different stored length means the text was cut on the way.
    """
    if src_chars is not None and len(body or '') != src_chars:
        return f'{len(body or "")} chars stored, {src_chars} read'
    if not ends_sentence(body):
        lines = _content_lines(body)
        return f'last line ({len(lines[-1]) if lines else 0} chars) has no sentence ending'
    return None
