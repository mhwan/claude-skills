"""fiw.py period — decide which period the next run covers (D13 · D14)."""
from __future__ import annotations

from datetime import date

from lib import cli, dates, layout, schema

PLAN_FILE = 'plan.json'
COVERAGE_FILE = 'coverage.json'


def register(sub):
    p = sub.add_parser('period', help='decide the period of the next run')
    p.add_argument('--today', type=cli.date_arg, metavar='YYYY-MM-DD',
                   help='override the local date')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--accept-quarter', action='store_true',
                   help='first run: accept the proposed quarter and write the plan')
    g.add_argument('--catch-up', choices=('all', 'recent-quarter'),
                   help='gap over a quarter: write the chosen catch-up plan')
    p.add_argument('--json', action='store_true', help='print fiw.period/1 JSON')
    p.set_defaults(func=main)


def _period(frm, to) -> dict:
    return {'from': dates.fmt(frm), 'to': dates.fmt(to)}


def _periods(segments) -> list:
    return [_period(a, b) for a, b in segments]


def decide(today, plan, coverage) -> dict:
    """Pure decision. plan: fiw.plan/1 or None; coverage: fiw.state.coverage/1 or None.

    Returns a fiw.period/1 object.
    """
    if isinstance(today, str):
        today = dates.parse_date(today)
    out = {'schema': 'fiw.period/1', 'status': 'ready', 'today': dates.fmt(today),
           'period': None, 'period_source': None, 'plan_segment': None, 'decision': None}

    open_segments = [s for s in (plan or {}).get('segments', []) if s.get('status') != 'done']
    if open_segments:
        seg = min(open_segments, key=lambda s: s['index'])
        out.update(period={'from': seg['from'], 'to': seg['to']}, period_source='plan',
                   plan_segment=seg['index'])
        return out

    last_per_source = [max(dates.parse_date(p['to']) for p in periods)
                       for periods in ((coverage or {}).get('sources') or {}).values() if periods]
    if last_per_source:
        frm = min(last_per_source)
        if frm < dates.add_months(today, -3) + dates.ONE_DAY:
            out.update(status='needs_decision', decision={
                'kind': 'gap_over_quarter', 'options': ['all', 'recent-quarter'],
                'proposals': {'all': _periods(dates.monthly_segments(frm, today)),
                              'recent-quarter': _periods(dates.quarter_proposal(today))}})
            return out
        out.update(period=_period(frm, today), period_source='incremental')
        return out

    out.update(status='needs_decision', decision={
        'kind': 'first_run_quarter', 'options': ['accept-quarter'],
        'proposals': {'accept-quarter': _periods(dates.quarter_proposal(today))}})
    return out


def load_state():
    """(plan, coverage) from state/, each None when the file does not exist."""
    plan_path = layout.state_dir() / PLAN_FILE
    cov_path = layout.state_dir() / COVERAGE_FILE
    plan = schema.load(plan_path, 'fiw.plan/1') if plan_path.exists() else None
    coverage = schema.load(cov_path, 'fiw.state.coverage/1') if cov_path.exists() else None
    return plan, coverage


def write_plan(reason, periods) -> dict:
    plan = {'schema': 'fiw.plan/1', 'created_at': dates.now_iso(), 'reason': reason,
            'segments': [{'index': i, 'from': p['from'], 'to': p['to'], 'status': 'pending',
                          'run_id': None} for i, p in enumerate(periods)]}
    layout.ensure_home()
    schema.dump(layout.state_dir() / PLAN_FILE, plan, 'fiw.plan/1')
    return plan


def main(args) -> int:
    today = args.today or date.today()
    plan, coverage = load_state()
    result = decide(today, plan, coverage)
    kind = (result['decision'] or {}).get('kind')

    if args.accept_quarter:
        if kind != 'first_run_quarter':
            raise cli.UsageError('--accept-quarter is only valid on the first run '
                                 f'(current state: {kind or result["period_source"]})')
        plan = write_plan('first_run', result['decision']['proposals']['accept-quarter'])
        result = decide(today, plan, coverage)
    elif args.catch_up:
        if kind != 'gap_over_quarter':
            raise cli.UsageError('--catch-up is only valid when the gap since the last report '
                                 f'is over a quarter (current state: {kind or result["period_source"]})')
        reason = 'catch_up_all' if args.catch_up == 'all' else 'catch_up_recent_quarter'
        plan = write_plan(reason, result['decision']['proposals'][args.catch_up])
        result = decide(today, plan, coverage)

    schema.check(result, 'fiw.period/1')
    code = cli.EXIT_OK if result['status'] == 'ready' else cli.EXIT_WARN
    if args.json:
        cli.print_json(result)
        return code
    if result['status'] == 'ready':
        p = result['period']
        seg = '' if result['plan_segment'] is None else f', plan segment {result["plan_segment"]}'
        print(f'period: ready {p["from"]} ~ {p["to"]} ({result["period_source"]}{seg})')
    else:
        d = result['decision']
        print(f'period: needs_decision ({d["kind"]}) — ask the user, then rerun with the chosen flag')
        for option in d['options']:
            print(f'  --{"accept-quarter" if option == "accept-quarter" else "catch-up " + option}:')
            for p in d['proposals'][option]:
                print(f'    {p["from"]} ~ {p["to"]}')
    print(f'period: exit {code}')
    return code
