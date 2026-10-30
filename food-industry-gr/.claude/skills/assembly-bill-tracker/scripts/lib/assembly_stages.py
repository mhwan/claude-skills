"""Bill stage values seen so far in assembly_bill_detail (PASSGUBN, PROC_STAGE_CD).

This is the original copy, promoted from assembly-bill-tracker's scripts/abt_stages.py.
The strings are the National Assembly's own wording and are never translated or ranked here.
A pair that is not listed is simply "not seen yet": the caller shows it as it came and adds a
warning. Add a pair only after seeing it in a real assembly_bill_detail result.
Standard library only, Python 3.9+.
"""
from __future__ import annotations

CLOSED_PASS_CLASS = '처리의안'

KNOWN_PAIRS = frozenset({
    ('계류의안', '접수'),
    ('계류의안', '소관위접수'),
    ('계류의안', '소관위심사'),
    ('처리의안', '본회의불부의'),
    ('처리의안', '철회'),
    ('처리의안', '공포'),
})


def is_known(pass_class, stage):
    """True when this exact (PASSGUBN, PROC_STAGE_CD) pair has been looked at before."""
    return (pass_class, stage) in KNOWN_PAIRS
