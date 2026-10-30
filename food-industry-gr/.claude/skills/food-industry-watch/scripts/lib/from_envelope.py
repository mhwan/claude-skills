"""Turn the gr.envelope/1 results of the other skills into this skill's fiw.collect/1 files (contracts 2.2).

This skill collects nothing itself. The Assembly, notice, press-release, food-safety and news data are
read from <output>/data/<skill>/*.json (written by assembly-bill-tracker, assembly-hearing-watch,
gov-notice-watch, food-safety-records and korean-news-search) and re-shaped here, once, into the
fiw.collect/1 files the rest of the pipeline (check, cluster, render, publish) already reads.

Rules of the conversion:
  * a source is read from every envelope whose period overlaps the run period; rows are united by id
    (a newer file wins); nothing is invented, an unknown value stays null
  * an envelope source with status ok or empty is a finished request (empty = 0 items); unread, failed
    and no_key are failed requests
  * days of the run period that no ok envelope covers are listed as one failed request, so the source
    is partial instead of looking complete
  * no overlapping envelope at all: nothing is written and check.py reports the source as not collected
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from . import dates, envelope, inclusion, korean, layout, notices

# fiw source -> (producing skill, inclusion, prefixes of the envelope source names that feed it)
SPECS = {
    'bigkinds': ('korean-news-search', 'article_date', ('bigkinds',)),
    'korea_kr': ('gov-notice-watch', 'posted', ('korea.kr',)),
    'lawmaking_web': ('gov-notice-watch', 'notice_open_overlap', ('국민참여입법센터',)),
    'mfds_notice': ('gov-notice-watch', 'notice_open_overlap', ('식약처',)),
    'ftc_notice': ('gov-notice-watch', 'notice_open_overlap', ('공정위',)),
    'assembly_bills': ('assembly-bill-tracker', 'posted', ('assembly_bill',)),
    'assembly_schedule': ('assembly-hearing-watch', 'upcoming_4w', ('assembly_schedule',)),
    'foodsafety': ('food-safety-records', 'record_date', ('foodsafety',)),
}
SOURCES = tuple(SPECS)
ROW_PREFIX = {'korea_kr': 'kr-', 'lawmaking_web': 'lm-', 'mfds_notice': 'mfds-', 'ftc_notice': 'ftc-'}
NOTICE_KINDS = ('입법예고', '행정예고', '재행정예고')
# How the comment period of a notice was read, by notice source (the report footnote names it).
OPEN_SOURCE = {'lawmaking_web': 'lawmaking:입법의견 접수기간', 'ftc_notice': 'ftc:기간', 'mfds_notice': 'mfds:body_regex'}
BASIS = {'recall': '등록일', 'admin': '처분일(처분확정일자)', 'unfit': '등록일'}
DATE_FIELD = {'recall': 'hmpgpblict_prcsdtm', 'admin': 'dsps_dcsndt', 'unfit': 'cret_dtm'}
DETAIL_URL = {
    'recall': 'https://www.foodsafetykorea.go.kr/layer/suspensionDetail.do?search_keyword={seq}',
    'admin': 'https://www.foodsafetykorea.go.kr/layer/administMeasureDetail.do?seq={seq}',
    'unfit': 'https://www.foodsafetykorea.go.kr/layer/testUnfitDetail.do?rtrvldsuse_seq={seq}',
}
GOOD = ('ok', 'empty')
UPCOMING_DAYS = 28
_SPAN_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:~(\d{4}-\d{2}-\d{2}))?$')


def _day(v):
    try:
        return dates.parse_date(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _days(a, b) -> set:
    return {a + timedelta(days=i) for i in range((b - a).days + 1)} if a <= b else set()


def _ranges(days) -> str:
    """'2026-10-30~2026-11-02, 2026-11-05' for a set of days."""
    out, run = [], []
    for d in sorted(days):
        if run and d - run[-1] > timedelta(days=1):
            out.append(run)
            run = []
        run.append(d)
    if run:
        out.append(run)
    return ', '.join(dates.fmt(r[0]) if len(r) == 1 else f'{dates.fmt(r[0])}~{dates.fmt(r[-1])}' for r in out)


def _span(text):
    m = _SPAN_RE.match(text or '')
    if not m:
        return None
    a, b = _day(m.group(1)), _day(m.group(2) or m.group(1))
    return (a, b) if a and b and a <= b else None


def _text(v) -> str:
    return ' '.join(v.split()) if isinstance(v, str) else ''


def _opt(v):
    return _text(v) or None


# ------------------------------------------------------------------------------ rows -> items

def _belongs(source, row) -> bool:
    prefix = ROW_PREFIX.get(source)
    if prefix:
        return str(row.get('id', '')).startswith(prefix)
    if source == 'assembly_schedule':
        return row.get('kind') == 'schedule'
    return True


def _bill(row, **_):
    q = parse_qs(urlparse(row.get('url') or '').query).get('billId') or []
    bill_id = q[0] if q else str(row['id'])
    return {'bill_id': bill_id, 'name': _text(row['title']), 'proposer': _text(row.get('proposer')),
            'date': row['date'], 'committee': _opt(row.get('committee')), 'stage': _opt(row.get('stage')),
            'url': row.get('url') or ''}, bill_id


def _schedule(row, **_):
    item = {'date': row['date'], 'time': _opt(row.get('time')), 'committee': _text(row.get('committee')),
            'meeting': _text(row['title']), 'url': row.get('url') or ''}
    # Same key as the check gate: one meeting listed under two row ids is one item.
    return item, f'{item["date"]}|{item["committee"]}|{item["meeting"]}'


def _news(row, topic=None, **_):
    return {'id': str(row['id']), 'title': _text(row['title']), 'date': row['date'], 'outlet': _text(row.get('outlet')),
            'url': row.get('url') or '', 'excerpt': '', 'queries': [topic] if topic else []}, str(row['id'])


def _press(row, source=None, **_):
    rid = str(row['id'])[len(ROW_PREFIX[source]):]
    return {'id': rid, 'dept': _text(row.get('dept')), 'title': _text(row['title']), 'date': row['date'],
            'url': row.get('url') or ''}, rid


def _evidence(source, open_from, open_to):
    if source == 'mfds_notice':
        y, m, d = open_to.split('-')
        return f'{y}년 {int(m)}월 {int(d)}일까지'
    return f'{open_from}~{open_to}'


def _notice(row, source=None, **_):
    title = _text(row['title'])
    kind = row.get('kind') if row.get('kind') in NOTICE_KINDS else notices.notice_kind(title)
    if kind is None:
        return None, None
    rid = str(row['id'])[len(ROW_PREFIX[source]):]
    posted = row['date']
    open_to = row.get('open_to') if _day(row.get('open_to')) else None
    open_from = min(posted, open_to) if open_to else None
    item = {'id': rid, 'dept': _text(row.get('dept')), 'kind': kind, 'law_type': notices.law_type(title), 'title': title,
            'posted': posted, 'open_from': open_from, 'open_to': open_to,
            'open_parse': 'ok' if open_to else 'failed',
            'open_source': OPEN_SOURCE[source] if open_to else None,
            'open_evidence': _evidence(source, open_from, open_to) if open_to else None,
            'url': row.get('url') or '', 'dup_key': notices.dup_key(title, open_from, posted),
            'family_key': notices.family_key(title, open_from, open_to)}
    return item, rid


def _record(row, companies=(), **_):
    kind = row.get('kind')
    if kind not in BASIS:
        return None, None
    seq = str(row['id']).partition('-')[2]
    if not seq:
        return None, None
    company, product = _text(row.get('company')), _opt(row.get('product'))
    tracked = next((e['name'] for e in companies
                    if korean.match_entity(company, e) or korean.match_entity(product or '', e)), None)
    item = {'kind': kind, 'seq': seq, 'company': company, 'product': product, 'food_type': None,
            'reason': _opt(row.get('reason')), 'grade': _opt(row.get('grade')), 'action': _opt(row.get('action')),
            'law': None, 'date': row['date'], 'date_field': DATE_FIELD[kind],
            'url': row.get('url') or DETAIL_URL[kind].format(seq=seq),
            'personal': '*' in company or '○' in company, 'tracked_company': tracked}
    return item, f'{kind}-{seq}'


_MAPPERS = {'bigkinds': _news, 'korea_kr': _press, 'lawmaking_web': _notice, 'mfds_notice': _notice,
            'ftc_notice': _notice, 'assembly_bills': _bill, 'assembly_schedule': _schedule, 'foodsafety': _record}


# ------------------------------------------------------------------------------ envelopes

def load_envelopes(skill, notes) -> list:
    """[(path, envelope, (first day, last day))] of data/<skill>/*.json, oldest file first; unreadable ones go to notes."""
    folder = layout.data_dir(skill)
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob('*.json'), key=lambda p: (p.stat().st_mtime, p.name)):
        try:
            env = envelope.read_envelope(path)
        except (OSError, ValueError) as e:
            notes.append(f'{path.name}: not used — {str(e)[:150]}')
            continue
        span = _span(env['period'])
        if span is None:
            notes.append(f'{path.name}: not used — period {env["period"]!r} is not a date range')
            continue
        out.append((path, env, span))
    return out


def _topic(path) -> str:
    """The search words of a korean-news-search file name (YYYY-MM-DD~YYYY-MM-DD_<words>.json)."""
    return path.stem.partition('_')[2]


def build(source, period, run_id, today, companies=()):
    """(fiw.collect/1 dict, read_from, read_to, notes) for one source, or (None, None, None, notes) when no
    envelope of the producing skill overlaps what the run needs."""
    skill, incl, prefixes = SPECS[source]
    notes = []
    frm, to, now = _day(period['from']), _day(period['to']), _day(today)
    window = _days(now, now + timedelta(days=UPCOMING_DAYS)) if incl == 'upcoming_4w' else _days(frm, to)
    items, requests, reasons, statuses, covered = {}, [], [], [], set()
    for path, env, (a, b) in load_envelopes(skill, notes):
        span = _days(a, b) & window
        mine = [s for s in env['sources'] if s['source'].startswith(prefixes)]
        if not span or not mine:
            continue
        before = len(items)
        for row in env['rows']:
            if not _belongs(source, row):
                continue
            item, key = _MAPPERS[source](row, source=source, topic=_topic(path), companies=companies)
            if item is None:
                notes.append(f'{path.name}: row {row["id"]} skipped — no usable kind or id')
                continue
            if inclusion.included(incl, item, frm, to, now):
                if source == 'bigkinds' and key in items:
                    item['queries'] = sorted(set(items[key]['queries']) | set(item['queries']))
                items[key] = item
        good = all(s['status'] in GOOD for s in mine)
        if good:
            covered |= span
        for i, s in enumerate(mine):
            statuses.append(s['status'])
            requests.append({'target': s['source'], 'query': env['period'], 'total': None,
                             'received': len(items) - before if i == 0 else 0, 'pages': s.get('requests', 0),
                             'status': 'ok' if s['status'] in GOOD else 'failed'})
            if s['status'] not in GOOD:
                reasons.append(f'{s["source"]} ({path.name}): {s["status"]}' + (f' — {s["error"]}' if s.get('error') else ''))
    if not requests:
        return None, None, None, notes
    gap = window - covered
    if gap:
        requests.append({'target': f'미조회 구간 {_ranges(gap)}', 'query': None, 'total': None, 'received': 0,
                         'pages': 0, 'status': 'failed'})
        reasons.append(f'no ok result covers {_ranges(gap)}')
    good = [r for r in requests if r['status'] == 'ok']
    if len(good) == len(requests):
        status = 'ok'
    elif good:
        status = 'partial'
    else:
        status = 'no_key' if statuses and set(statuses) == {'no_key'} else 'failed'
    out = {'schema': 'fiw.collect/1', 'source': source, 'run_id': run_id,
           'period': {'from': period['from'], 'to': period['to']}, 'inclusion': incl, 'method': 'script',
           'status': status, 'probe': None, 'requests': requests, 'error': None,
           'items': [] if status in ('failed', 'no_key') else list(items.values())}
    if source == 'foodsafety':
        out['basis'] = dict(BASIS)
    if status in ('failed', 'no_key'):
        out['error'] = '; '.join(reasons)[:300]
        return out, None, None, notes
    if incl == 'upcoming_4w':
        return out, period['from'], period['to'], notes
    days = covered & window
    return out, dates.fmt(min(days)), dates.fmt(max(days)), notes
