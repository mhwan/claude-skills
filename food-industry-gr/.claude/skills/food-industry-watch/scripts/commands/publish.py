"""fiw.py publish — record the checked run (its work/report.md) in state: plan, coverage, runs, issues,
notices, hygiene, seen ids — every state change after a snapshot (state/snapshots.json). Period reports for
the user are built from the runs by fiw.py report."""
from __future__ import annotations

from lib import accum, cli, dates, gates, history, layout, runview, schema

from . import _accumulate as acc
from . import period as period_cmd
from . import state as state_cmd

GATE_EXITS = (('check', (0, 3)), ('relevance', (0,)), ('verify', (0,)), ('lint', (0,)),
              ('linkcheck', (0, 3)))


def register(sub):
    p = sub.add_parser('publish', help='record the checked raw in state')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def _preconditions(rd, published) -> tuple:
    """(findings, gate objects by name, report hash). Every failed precondition is one finding."""
    findings = []

    def fail(where, kind, detail):
        findings.append({'where': where, 'kind': kind, 'detail': detail})

    hashes = {'report': gates.file_hash(layout.report_file(rd)),
              'relevance': gates.file_hash(rd / 'work' / 'relevance.json'),
              'verify': gates.file_hash(rd / 'work' / 'summary.json')}
    if hashes['report'] is None:
        fail('report.md', 'missing', 'run render first')
    want_hash = {'relevance': hashes['relevance'], 'verify': hashes['verify'],
                 'lint': hashes['report'], 'linkcheck': hashes['report']}
    objs = {}
    for name, ok_exits in GATE_EXITS:
        gate = gates.read_gate(rd, name)
        objs[name] = gate
        if gate is None:
            fail(name, 'missing', f'gates/{name}.json does not exist — run {name} first')
            continue
        if gate['exit'] not in ok_exits:
            fail(name, 'exit', f'gate exit is {gate["exit"]}, need {" or ".join(str(e) for e in ok_exits)}')
        if name in want_hash:
            if want_hash[name] is None:
                fail(name, 'missing', 'the file this gate was computed from does not exist')
            elif gate['input_hash'] != want_hash[name]:
                fail(name, 'stale', 'gate input hash does not match the current file — rerun the gate')
    if published:
        fail('runs.json', 'exists', 'this raw is already published; change it only through correct')
    return findings, objs, hashes['report']


def _coverage_update(rd, coverage) -> dict:
    """coverage with this run's fully read (ok) sources added.

    partial / failed / no_key sources are not added, so the next run reads that period again
    (02-plan §8.3: a failed query's period must not count as covered; coverage is per source).
    """
    cov = coverage or {'schema': 'fiw.state.coverage/1', 'sources': {}}
    for path in sorted(layout.collect_dir(rd).glob('*.json')) if layout.collect_dir(rd).is_dir() else []:
        env = schema.load(path, 'fiw.collect/1')
        if env['status'] == 'ok':
            periods = cov['sources'].setdefault(env['source'], [])
            if env['period'] not in periods:
                periods.append({'from': env['period']['from'], 'to': env['period']['to']})
    return cov


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    period = manifest['period']
    runs_path = layout.state_dir() / 'runs.json'
    runs_state = (schema.load(runs_path, 'fiw.state.runs/1') if runs_path.exists()
                  else {'schema': 'fiw.state.runs/1', 'runs': []})
    published = any(r['run_id'] == manifest['run_id'] and r.get('report') for r in runs_state['runs'])

    findings, objs, report_hash = _preconditions(rd, published)
    if findings:
        result = {'run_id': manifest['run_id'], 'exit': cli.EXIT_FAIL, 'report': None, 'findings': findings}
        if args.json:
            cli.print_json(result)
        else:
            gates.print_findings(findings)
            print(f'publish: refused, {len(findings)} precondition(s) failed, exit {cli.EXIT_FAIL}')
        return cli.EXIT_FAIL

    # Load every state file before the first write so a bad file leaves nothing half-done.
    plan, coverage = period_cmd.load_state()
    coverage = _coverage_update(rd, coverage)
    data = acc.RunData(rd, manifest)
    issues_obj = accum.load_state('issues.json')
    notices_obj = accum.load_state('notices.json')
    hygiene_obj = accum.load_state('hygiene.json')
    weeks = layout.load_format()['dormancy_weeks']
    notes = []

    layout.state_dir().mkdir(parents=True, exist_ok=True)
    rev = history.snapshot('publish', manifest['run_id'])

    segment = manifest['plan_segment']
    segs = [s for s in (plan or {}).get('segments', []) if s['index'] == segment]
    if segment is not None and segs:
        segs[0]['status'] = 'done'
        schema.dump(layout.state_dir() / period_cmd.PLAN_FILE, plan, 'fiw.plan/1')
    elif segment is not None:
        notes.append(f'plan segment {segment} not found in state/plan.json — not marked done')

    schema.dump(layout.state_dir() / period_cmd.COVERAGE_FILE, coverage, 'fiw.state.coverage/1')

    entry = next((r for r in runs_state['runs'] if r['run_id'] == manifest['run_id']), None)
    if entry is None:
        entry = {'run_id': manifest['run_id'], 'created_at': manifest['created_at'], 'period': period,
                 'period_source': manifest['period_source'], 'report': None, 'gates': {}}
        runs_state['runs'].append(entry)
    entry['report'] = accum.report_ref(manifest['run_id'])
    entry['gates'] = {name: objs[name]['exit'] for name, _ in GATE_EXITS}

    now, run_id = dates.now_iso(), manifest['run_id']
    periods = accum.bigkinds_periods(coverage)
    issues_obj, entry['issue_ids'] = acc.update_issues(data, issues_obj, now, periods, weeks, run_id)
    verdicts = runview.final_verdicts(data.prefilter, data.relevance)
    schema.dump(runs_path, runs_state, 'fiw.state.runs/1')
    schema.dump(layout.state_dir() / 'issues.json', issues_obj, 'fiw.state.issues/1')
    schema.dump(layout.state_dir() / 'notices.json',
                acc.update_notices(data, notices_obj, verdicts, manifest['today'], run_id), 'fiw.state.notices/1')
    schema.dump(layout.state_dir() / 'hygiene.json', acc.update_hygiene(data, hygiene_obj, run_id, period),
                'fiw.state.hygiene/1')
    schema.dump(layout.state_dir() / 'seen_ids.json', acc.seen_ids(data, run_id), 'fiw.state.seen_ids/1')

    state_findings = state_cmd.check_state()
    gates.write_gate(rd, manifest, 'state', cli.EXIT_FAIL if state_findings else cli.EXIT_OK,
                     len(list(layout.state_dir().glob('*.json'))), state_findings)
    if state_findings:
        history.restore(rev)
        result = {'run_id': run_id, 'exit': cli.EXIT_FAIL, 'report': None, 'findings': state_findings}
        if args.json:
            cli.print_json(result)
        else:
            gates.print_findings(state_findings)
            print(f'publish: state check failed — state restored from snapshot {rev}, '
                  f'exit {cli.EXIT_FAIL}')
        return cli.EXIT_FAIL

    gates.record_stage(rd, manifest, 'publish', cli.EXIT_OK, report_hash)

    result = {'run_id': manifest['run_id'], 'exit': cli.EXIT_OK, 'report': entry['report'], 'findings': []}
    if args.json:
        cli.print_json(result)
    else:
        for note in notes:
            print(f'publish: note — {note}')
        print(f'publish: recorded {entry["report"]}')
        print(f'publish: exit {cli.EXIT_OK}')
    return cli.EXIT_OK
