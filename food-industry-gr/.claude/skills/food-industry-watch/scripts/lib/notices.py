"""Helpers shared by notice sources: kind, instrument type, instrument name, dedupe keys."""
from __future__ import annotations

import re

_KINDS = ('재행정예고', '행정예고', '입법예고')
_LAW_TYPES = ('시행규칙', '시행령', '법률', '고시', '훈령', '예규', '규정', '지침', '기준')
_CUTS = ('일부개정', '전부개정', '제정', '개정', '폐지')
_BRACKET_RE = re.compile(r'「([^」]*)」')
_MIDDLE_DOTS = '·ㆍ‧・･'
_NORM_RE = re.compile(r'[\s' + _MIDDLE_DOTS + r']+')
_DECREE_TAIL_RE = re.compile(r'\s*(?:시행령|시행규칙)(?:\s*[' + _MIDDLE_DOTS + r']\s*(?:시행령|시행규칙))?$')


def notice_kind(title):
    """Re-announced administrative notice > administrative notice > legislative notice, or None when the title names none."""
    for kind in _KINDS:
        if kind in (title or ''):
            return kind
    return None


def law_type(title):
    """The first listed instrument keyword the title contains (priority order), else None."""
    for t in _LAW_TYPES:
        if t in (title or ''):
            return t
    return None


def law_name(title) -> str:
    """Instrument name: inner text of the first 「…」, else the title up to the first amendment word."""
    title = title or ''
    m = _BRACKET_RE.search(title)
    if m and m.group(1).strip():
        name = m.group(1)
    else:
        cut = min((i for i in (title.find(c) for c in _CUTS) if i > 0), default=len(title))
        name = title[:cut]
    return ' '.join(name.split())


def norm(s) -> str:
    """Remove whitespace and middle dots (comparison key)."""
    return _NORM_RE.sub('', s or '')


def _iso(v):
    return v.isoformat() if hasattr(v, 'isoformat') else v


def dup_key(name, open_from, posted) -> str:
    """norm(law_name) | (open_from or posted). name may be a title or a law name."""
    return norm(law_name(name)) + '|' + str(_iso(open_from) or _iso(posted))


def base_law(name) -> str:
    """law_name without a trailing enforcement-decree / enforcement-rule suffix (or both joined)."""
    ln = law_name(name)
    base = _DECREE_TAIL_RE.sub('', ln)
    return base if base.strip() else ln


def family_key(name, open_from, open_to):
    """norm(base law) | open_from~open_to; None when the period is not parsed."""
    if not open_from or not open_to:
        return None
    return norm(base_law(name)) + '|' + str(_iso(open_from)) + '~' + str(_iso(open_to))
