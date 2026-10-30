"""Decision rules: comment-deadline state, period overlap and the food-related prefilter."""
from __future__ import annotations

import dates
import korean

# Agency names that appear inside titles and must not count as food words (the first one contains one).
AGENCY_NAMES = ('식품의약품안전처', '농림축산식품부', '농림축산식품해양수산위원회')


def deadline_state(open_to, today) -> str:
    """'open' when the comment deadline is today or later, 'closed' when past, 'unknown' when not found.

    open_to and today are ISO strings (YYYY-MM-DD) or None for open_to.
    """
    if not open_to:
        return 'unknown'
    return 'open' if str(open_to) >= str(today) else 'closed'


def period_overlaps(a_from, a_to, p_from, p_to) -> bool:
    """True when the comment period a_from..a_to touches the requested period p_from..p_to (ISO strings)."""
    return dates.overlaps(a_from, a_to, p_from, p_to)


def _blank_agency_names(title, extra=()) -> str:
    text = title or ''
    for name in tuple(AGENCY_NAMES) + tuple(extra):
        if name:
            text = text.replace(name, ' ')
    return text


def _any_word(text, words) -> bool:
    return any(w and korean.contains_word(text, w, left_guard=True) for w in words)


def food_verdict(title, dept, cfg, extra_agency_names=()) -> str:
    """'include' / 'other' / 'mixed' / 'undecided' for one item.

    include words only -> include; exclude words only -> other; both -> mixed;
    neither -> undecided, except an agency listed in no_match_other, where it is other.
    """
    text = _blank_agency_names(title, extra_agency_names)
    has_in = _any_word(text, cfg.get('include') or [])
    has_out = _any_word(text, cfg.get('exclude') or [])
    if has_in and has_out:
        return 'mixed'
    if has_in:
        return 'include'
    if has_out:
        return 'other'
    dept = dept or ''
    for name in cfg.get('no_match_other') or []:
        if name and dept and (name in dept or dept in name):
            return 'other'
    return 'undecided'
