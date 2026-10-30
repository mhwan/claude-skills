"""The watch.json dictionary of tracked names: lookup, mentions in text, short labels."""
from __future__ import annotations

import re

from . import korean

KINDS = ('companies', 'associations', 'agencies', 'committees')


def all_entities(watch) -> list:
    """Every tracked entity as a copy with an extra 'kind' key, in watch.json order."""
    out = []
    for kind in KINDS:
        for e in watch.get(kind) or []:
            out.append(dict(e, kind=kind))
    return out


def names(entity) -> list:
    """name first, then aliases."""
    return [entity['name']] + list(entity.get('aliases') or [])


def mentioned(text, entity) -> bool:
    """Any name or alias of entity occurs in text as a word (requires_any is not applied)."""
    excludes = tuple(entity.get('exclude_compounds') or ())
    return any(korean.match_name(text, n, exclude_compounds=excludes) for n in names(entity))


def mentions(text, entities) -> list:
    """(start, end, entity name, written form) of every name/alias occurrence, in text order.

    Overlapping hits keep the longest one (the full ministry name over a shorter food-like name).
    """
    hits = []
    for e in entities:
        excludes = tuple(e.get('exclude_compounds') or ())
        for n in names(e):
            pattern = korean._NOT_BEFORE + re.escape(n) + korean._PARTICLE_RE + korean._NOT_AFTER
            for m in re.finditer(pattern, text or ''):
                if any(c and text.startswith(c, m.start()) for c in excludes):
                    continue
                hits.append((m.start(), m.start() + len(n), e['name'], n))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept, last_end = [], -1
    for h in hits:
        if h[0] >= last_end:
            kept.append(h)
            last_end = h[1]
    return kept


def find(watch, text):
    """The first tracked entity whose name or alias matches text as a whole word, else None."""
    for e in all_entities(watch):
        if korean.match_entity(text, e):
            return e
    return None


def short_name(watch, full_name, kind) -> str:
    """First alias of the entity of `kind` named full_name (e.g. the full name of the fair-trade commission -> its short alias), else full_name."""
    for e in watch.get(kind) or []:
        if e['name'] == full_name:
            aliases = e.get('aliases') or []
            return aliases[0] if aliases else e['name']
    return full_name


def committee_alias(watch, committee):
    """Short alias of a tracked committee named by `committee` (full name or alias), else None."""
    if not committee:
        return None
    for e in watch.get('committees') or []:
        if committee in names(e) or korean.match_entity(committee, e):
            aliases = e.get('aliases') or []
            return aliases[0] if aliases else e['name']
    return None
