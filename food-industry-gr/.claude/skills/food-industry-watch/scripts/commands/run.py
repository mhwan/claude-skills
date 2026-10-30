"""fiw.py run new / run show — create and inspect run folders."""
from __future__ import annotations

import re
import sys
from datetime import date

from lib import cli, dates, gates, layout, schema

from . import period as period_cmd


def register(sub):
    p = sub.add_parser('run', help='create or show a run')
    run_sub = p.add_subparsers(dest='run_command', metavar='RUN_COMMAND', required=True)

    new = run_sub.add_parser('new', help='create a new run folder for the next period')
    new.add_argument('--today', type=cli.date_arg, metavar='YYYY-MM-DD', help='override the local date')
    new.add_argument('--from', dest='frm', type=cli.date_arg, metavar='YYYY-MM-DD',
                     help='explicit period start (needs --to)')
    new.add_argument('--to', type=cli.date_arg, metavar='YYYY-MM-DD',
                     help='explicit period end (needs --from)')
    new.add_argument('--json', action='store_true', help='print the fiw.manifest/1 JSON')
    new.set_defaults(func=main_new)

    show = run_sub.add_parser('show', help='print the manifest of a run')
    show.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    show.add_argument('--json', action='store_true', help='print the fiw.manifest/1 JSON')
    show.set_defaults(func=main_show)


def next_run_id(today: date, runs_state) -> str:
    """1 + the highest n used for today in run folder names or runs.json."""
    prefix = dates.fmt(today)
    pat = re.compile(rf'^{re.escape(prefix)}-([1-9]\d*)$')
    used = [0]
    if layout.runs_dir().is_dir():
        used += [int(m.group(1)) for m in (pat.match(d.name) for d in layout.runs_dir().iterdir()) if m]
    for r in (runs_state or {}).get('runs', []):
        m = pat.match(r['run_id'])
        if m:
            used.append(int(m.group(1)))
    return f'{prefix}-{max(used) + 1}'


def main_new(args) -> int:
    today = args.today or date.today()
    plan, coverage = period_cmd.load_state()
    plan_segment = None
    if (args.frm is None) != (args.to is None):
        raise cli.UsageError('--from and --to must be given together')
    if args.frm is not None:
        try:
            dates.check_range(args.frm, args.to, today)
        except dates.DateError as e:
            raise cli.UsageError(f'bad period {args.frm} ~ {args.to}: {e}') from None
        period = {'from': dates.fmt(args.frm), 'to': dates.fmt(args.to)}
        source = 'explicit'
    else:
        decision = period_cmd.decide(today, plan, coverage)
        if decision['status'] != 'ready':
            print('run: the period needs a decision — run `fiw.py period`, ask the user, '
                  'and rerun it with the chosen flag. Nothing was created.', file=sys.stderr)
            return cli.EXIT_WARN
        period, source, plan_segment = decision['period'], decision['period_source'], decision['plan_segment']

    layout.ensure_home()
    runs_path = layout.state_dir() / 'runs.json'
    runs_state = schema.load(runs_path, 'fiw.state.runs/1') if runs_path.exists() else None
    run_id = next_run_id(today, runs_state)
    created_at = dates.now_iso()
    manifest = {'schema': 'fiw.manifest/1', 'run_id': run_id, 'created_at': created_at,
                'today': dates.fmt(today), 'period': period, 'period_source': source,
                'plan_segment': plan_segment, 'stages': {}, 'sources': {}}
    schema.check(manifest, 'fiw.manifest/1')

    rd = layout.run_dir(run_id)
    rd.mkdir(exist_ok=False)
    for folder in (layout.collect_dir(rd), layout.gates_dir(rd)):
        folder.mkdir(parents=True)
    schema.dump(layout.manifest_path(rd), manifest, 'fiw.manifest/1')

    if plan_segment is not None:
        for seg in plan['segments']:
            if seg['index'] == plan_segment:
                seg.update(status='running', run_id=run_id)
        schema.dump(layout.state_dir() / period_cmd.PLAN_FILE, plan, 'fiw.plan/1')

    runs_state = runs_state or {'schema': 'fiw.state.runs/1', 'runs': []}
    runs_state['runs'].append({'run_id': run_id, 'created_at': created_at, 'period': period,
                               'period_source': source, 'report': None, 'gates': {}})
    schema.dump(runs_path, runs_state, 'fiw.state.runs/1')

    if args.json:
        cli.print_json(manifest)
    else:
        seg = '' if plan_segment is None else f', plan segment {plan_segment}'
        print(f'run: created {run_id} — period {period["from"]} ~ {period["to"]} ({source}{seg})')
        print(f'run: folder {rd}')
    return cli.EXIT_OK


def main_show(args) -> int:
    _, manifest = gates.load_run(args.run)
    cli.print_json(manifest)
    return cli.EXIT_OK
