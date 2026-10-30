"""Which collected items belong to a period (02-plan section 5.1): one rule per fiw.collect/1 inclusion value."""
from __future__ import annotations

from datetime import timedelta

from . import dates


def _d(v):
    return dates.parse_date(v) if isinstance(v, str) else v


def included(inclusion, item, frm, to, today=None) -> bool:
    """True when item belongs to the period under the inclusion rule."""
    frm, to = _d(frm), _d(to)
    if inclusion == 'notice_open_overlap':
        posted = _d(item.get('posted'))
        if posted is not None and frm <= posted <= to:
            return True
        if item.get('open_parse') == 'ok':
            return _d(item.get('open_to')) >= frm
        return posted is not None and posted < frm
    day = _d(item.get('date'))
    if day is None:
        return False
    if inclusion == 'upcoming_4w':
        today = _d(today)
        return today <= day <= today + timedelta(days=28)
    return frm <= day <= to
