"""Read-only views over a run's files used by several stages (relevance, prepare, verify, render, lint)."""
from __future__ import annotations

from pathlib import Path

from . import layout, schema

# Sources whose items get a food-relevance verdict (prefilter + relevance items).
VERDICT_SOURCES = ('korea_kr', 'lawmaking_web', 'mfds_notice', 'ftc_notice', 'mafra_notice',
                   'assembly_bills', 'foodsafety')
NOTICE_SOURCES = ('lawmaking_web', 'mfds_notice', 'ftc_notice', 'mafra_notice')
MARKET_TOPICS = ('수출·해외', '저당·제로', '원가·환율·가격', '투자·설비', '실적·주가', '인사·지배구조')


def envelope(rd, source):
    """collect/{source}.json or None when the source was not collected."""
    path = layout.collect_dir(Path(rd)) / f'{source}.json'
    if not path.is_file():
        return None
    return schema.load(path, 'fiw.collect/1')


def final_verdicts(prefilter, relevance) -> dict:
    """{(source, id): 'include' | 'other' | 'undecided'} — a relevance item overrides the prefilter.

    'undecided' remains only for items the relevance file did not judge (the J gate fails those).
    """
    out = {}
    for it in (prefilter or {}).get('items', []):
        out[(it['source'], it['id'])] = it['verdict']
    for it in (relevance or {}).get('items', []):
        out[(it['source'], it['id'])] = it['verdict']
    return out


def item_categories(relevance) -> dict:
    """{(source, id): category} from relevance items (None when not given)."""
    return {(it['source'], it['id']): it.get('category') for it in (relevance or {}).get('items', [])}
