"""Accumulation rules shared by publish, render, lint and `state check` (m6-m7-addendum §B)."""
from __future__ import annotations

from datetime import timedelta

from . import dates, layout, schema

STATE_FILES = {
    'issues.json': 'fiw.state.issues/1', 'notices.json': 'fiw.state.notices/1',
    'hygiene.json': 'fiw.state.hygiene/1', 'seen_ids.json': 'fiw.state.seen_ids/1',
    'coverage.json': 'fiw.state.coverage/1', 'runs.json': 'fiw.state.runs/1', 'plan.json': 'fiw.plan/1',
    'doctor.json': 'fiw.state.doctor/1',
}
ISSUE_SECTIONS = ('gov', 'assembly', 'industry')


def load_state(name, default=None):
    """state/{name} validated against its contract, or `default` when the file is absent."""
    path = layout.state_dir() / name
    if not path.is_file():
        return default
    return schema.load(path, STATE_FILES[name])


def issues_list() -> list:
    return (load_state('issues.json') or {}).get('issues', [])


def bigkinds_periods(coverage) -> list:
    return list(((coverage or {}).get('sources') or {}).get('bigkinds') or [])


def covered_days_after(day, periods) -> int:
    """Distinct days d > day inside the union of the periods."""
    days = set()
    start = dates.parse_date(day) + timedelta(days=1)
    for p in periods:
        d, end = max(dates.parse_date(p['from']), start), dates.parse_date(p['to'])
        while d <= end:
            days.add(d)
            d += timedelta(days=1)
    return len(days)


def is_dormant(issue, periods, weeks, latest_run) -> bool:
    """D61: not updated in the latest published run and ≥ weeks × 7 covered days without an article."""
    if latest_run is not None and issue.get('last_run') == latest_run:
        return False
    last = issue.get('last_article') or issue['first_seen']
    return covered_days_after(last, periods) >= weeks * 7


def latest_published_run(runs_state):
    """(run_id, today-less entry) of the last run in runs.json with a report, else (None, None)."""
    for r in reversed((runs_state or {}).get('runs', [])):
        if r['report']:
            return r['run_id'], r
    return None, None


def has_earlier_report(runs_state, manifest) -> bool:
    """True when a run before this one in runs.json has a report (the previous-report test)."""
    runs = (runs_state or {}).get('runs', [])
    ids = [r['run_id'] for r in runs]
    rid = manifest['run_id']
    earlier = runs[:ids.index(rid)] if rid in ids else [r for r in runs if r['created_at'] < manifest['created_at']]
    return any(r['report'] for r in earlier)


def report_link(report) -> str:
    """The link to an earlier report from a run's report.md: runs/<skill>/{id}/work/report.md → ../../{id}/work/report.md."""
    path = report.replace('\\', '/')
    parts = path.split('/')
    if len(parts) >= 3 and parts[-2] == 'work':
        return '../../' + '/'.join(parts[-3:])  # gr-compat: ignore=pathjoin (a report link is always forward-slash relative)
    return parts[-1]


def report_ref(run_id) -> str:
    """What runs.json keeps as `report` for a published run: the path of its report below the output root."""
    return layout.report_file(layout.run_dir(run_id)).relative_to(layout.output_root()).as_posix()


def period_row(manifest, key):
    """(badge, first-report day or None) for an issue row of a period report; None for a raw's report.
    ▶ only for an issue first reported before the period."""
    pr = manifest.get('period_report')
    if not pr:
        return None
    first = pr['first_seen'].get(key)
    if first is None or first >= manifest['period']['from']:
        return '🆕 ', None
    return '▶ ', first


CARRIED_HEADING = '## 지난 보고서 쟁점 — 새 기사 없음'
PERIOD_CARRIED_HEADING = '## 기간 전 쟁점 — 이번 기간 새 기사 없음'


def carried_heading(manifest):
    """The heading over issues with no article in this report: a period report's one is relative to its period."""
    return PERIOD_CARRIED_HEADING if manifest.get('period_report') else CARRIED_HEADING


def uncollected_note(manifest):
    """The not-collected line of a period report that starts before the earliest raw, else None."""
    gap = (manifest.get('period_report') or {}).get('uncollected')
    if not gap:
        return None
    return f'> 미수집 — {gap["from"]}~{gap["to"]}: 원본 없음, 이 기간 내용은 싣지 않았다'


def alias_map(issues) -> dict:
    return {a: i['id'] for i in issues for a in i['aliases']}


def resolve(link, issues):
    """A state id for an issue_link (aliases resolve to their target), else None."""
    ids = {i['id'] for i in issues}
    if link in ids:
        return link
    return alias_map(issues).get(link)


def projected(manifest, todo, runs_state, coverage, bigkinds_env, issues, weeks):
    """(carried, dormant) state issues as the report of this run shows them (addendum §B).

    carried: active issues not in this run; dormant: dormant ones not in this run. Both ordered by
    last_article desc, then id.
    """
    periods = bigkinds_periods(coverage)
    if bigkinds_env is not None and bigkinds_env['status'] == 'ok':
        p = bigkinds_env['period']
        if {'from': p['from'], 'to': p['to']} not in periods:
            periods = periods + [{'from': p['from'], 'to': p['to']}]
    in_run = {issue['key'] for issue in todo['issues'] if not issue['key'].startswith('new:')}
    in_run = {resolve(k, issues) or k for k in in_run}
    entry = next((r for r in (runs_state or {}).get('runs', []) if r['run_id'] == manifest['run_id']), None)
    if entry is not None:
        in_run |= set((entry.get('issue_ids') or {}).values())
    carried, dormant = [], []
    for issue in issues:
        if issue['id'] in in_run:
            continue
        # The issue's stored status counts once this run is published (re-render of a published run).
        if issue['status'] == 'dormant' or is_dormant(issue, periods, weeks, None):
            dormant.append(issue)
        else:
            carried.append(issue)

    def order(xs):
        xs = sorted(xs, key=lambda i: i['id'])
        return sorted(xs, key=lambda i: i.get('last_article') or i['first_seen'], reverse=True)
    return order(carried), order(dormant)
