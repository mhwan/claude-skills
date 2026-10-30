# Changed from fiw scripts/lib/korean.py: the docstring no longer points at a fiw-only reference file; no code change.
"""Korean text matching that respects syllable boundaries.

This is the original copy. Each skill vendors it as scripts/lib/korean.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.
Standard library only, Python 3.9+.
"""
from __future__ import annotations

import re

HANGUL = '가-힣'
_NOT_BEFORE = f'(?<![{HANGUL}])'
_NOT_AFTER = f'(?![{HANGUL}])'

PARTICLES = ('은', '는', '이', '가', '을', '를', '의', '도', '만', '과', '와',
             '에', '에서', '으로', '로', '처럼', '보다', '까지')
# Longer particles first so the alternation prefers them.
_PARTICLE_RE = '(?:' + '|'.join(re.escape(p) for p in sorted(PARTICLES, key=len, reverse=True)) + ')?'


def contains_word(text, term, *, left_guard=True, right_guard=False) -> bool:
    """term occurs in text; guards forbid a Hangul syllable right before / after it."""
    if not text or not term:
        return False
    pattern = (_NOT_BEFORE if left_guard else '') + re.escape(term) + (_NOT_AFTER if right_guard else '')
    return re.search(pattern, text) is not None


def match_name(text, name, *, exclude_compounds=()) -> bool:
    """name as a standalone word, optionally followed by one particle."""
    if not text or not name:
        return False
    pattern = _NOT_BEFORE + re.escape(name) + _PARTICLE_RE + _NOT_AFTER
    for m in re.finditer(pattern, text):
        if any(c and text.startswith(c, m.start()) for c in exclude_compounds):
            continue
        return True
    return False


def match_entity(text, entity: dict) -> bool:
    """entity has the watch.json shape {name, aliases?, requires_any?, exclude_compounds?}."""
    excludes = tuple(entity.get('exclude_compounds') or ())
    names = [entity['name']] + list(entity.get('aliases') or [])
    if not any(match_name(text, n, exclude_compounds=excludes) for n in names):
        return False
    requires = entity.get('requires_any') or []
    if not requires:
        return True
    return any(contains_word(text, t, left_guard=True) for t in requires)


def has_ss_final(ch) -> bool:
    """A Hangul syllable whose final consonant is the double s (index 20 of the 28 finals)."""
    if not isinstance(ch, str) or len(ch) != 1:
        return False
    c = ord(ch)
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 == 20


_PREFIX_RE = re.compile(r'^\s*(?:\[[^\]]{1,15}\]|【[^】]{1,15}】|〈[^〉]{1,15}〉|<[^>]{1,15}>)\s*')
_SUFFIX_RE = re.compile(r'\s*\((?:종합(?:\d+보)?|상보|속보)\)\s*$')


def strip_title_prefix(title) -> str:
    """Remove leading bracket tags and one trailing news-wire tag such as (round-up N) or (breaking)."""
    s = title or ''
    while True:
        new = _PREFIX_RE.sub('', s, count=1)
        if new == s:
            break
        s = new
    s = _SUFFIX_RE.sub('', s, count=1)
    return s.strip()
