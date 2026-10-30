"""Notice helpers: kind from a title and the key used to match one notice across sources."""
from __future__ import annotations

import re

_KINDS = ('재행정예고', '행정예고', '입법예고')
_CUTS = ('일부개정', '전부개정', '제정', '개정', '폐지')
_BRACKET_RE = re.compile(r'「([^」]*)」')
_MIDDLE_DOTS = '·ㆍ‧・･'
_NORM_RE = re.compile(r'[\s' + _MIDDLE_DOTS + r']+')


def notice_kind(title):
    """The first kind in _KINDS (re-administrative, administrative, legislative) the title names, or None."""
    for kind in _KINDS:
        if kind in (title or ''):
            return kind
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


def dup_key(title) -> str:
    """Key that matches the same notice in two sources: the normalised instrument name."""
    return norm(law_name(title))
