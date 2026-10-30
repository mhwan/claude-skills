"""fiw.py verify — evidence gate E over work/summary.json (checks E0–E17 and the structure check)."""
from __future__ import annotations

from lib import accum, cli, gates, layout, runview

from . import _verify_checks as vc


def register(sub):
    p = sub.add_parser('verify', help='evidence gate E over work/summary.json')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def _pruned_published(manifest, todo) -> bool:
    """The run is published and prune emptied every body and flow excerpt of its todo."""
    runs = accum.load_state('runs.json')
    entry = next((r for r in (runs or {}).get('runs', []) if r['run_id'] == manifest['run_id']), None)
    if entry is None or not entry['report']:
        return False
    articles = [a for g in todo['issues'] + todo['market'] for a in g['articles']]
    flows = [f for g in todo['issues'] for f in g.get('flow_candidates', [])]
    return bool(articles) and not any(a['body'] for a in articles) and not any(f['excerpt'] for f in flows)


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
    if _pruned_published(manifest, todo):
        raise cli.UsageError(f'run {manifest["run_id"]} is published and its article bodies were pruned — verify '
                             'would fail every ref and overwrite the passing gate; change a published run through '
                             '`correct` (it re-fetches the bodies it needs)')
    summary = gates.load_work(rd, 'summary', 'fiw.summary/1')
    relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1', required=False)
    prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1', required=False)
    dead = gates.load_work(rd, 'dead_links', 'fiw.dead_links/1', required=False)
    watch = layout.load_watch()
    fmt = layout.load_format()
    envelopes = {s: runview.envelope(rd, s)
                 for s in ('bigkinds', 'korea_kr') + runview.NOTICE_SOURCES}

    gate = vc.Verifier(todo, summary, watch, fmt, envelopes, prefilter, relevance,
                       dead_urls=[e['url'] for e in dead['links']] if dead else (),
                       period_to=manifest['period']['to'])
    gate.run()
    code = cli.EXIT_FAIL if gate.findings else cli.EXIT_OK
    notices = gate.notices_checked
    obj = gates.write_gate(rd, manifest, 'verify', code, gate.checked, gate.findings,
                           classified={'notice_periods_reparsed': notices},
                           input_hash=gates.file_hash(rd / 'work' / 'summary.json'))
    if args.json:
        cli.print_json(obj)
    else:
        gates.print_findings(gate.findings)
        print(f'verify: E11 re-parsed {notices} notice comment periods')
        print(f'verify: {gate.checked} fields, {len(gate.findings)} findings, exit {code}')
    return code
