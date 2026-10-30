"""fiw.py collect — read the other skills' gr.envelope/1 results and write this run's fiw.collect/1 files."""
from __future__ import annotations

import argparse

from lib import cli, dates, from_envelope, gates, layout, schema

SOURCES = from_envelope.SOURCES                 # order of `--source all`
STAGE_STATUS = {'ok': 'ok', 'partial': 'warn', 'failed': 'failed', 'no_key': 'warn'}


def register(sub):
    p = sub.add_parser('collect', help="build a run's source files from the other skills' results in data/")
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--source', required=True, choices=SOURCES + ('all',),
                   help='source id, or all (= ' + ' '.join(SOURCES) + ')')
    p.add_argument('--json', action='store_true', help='print the updated fiw.manifest/1 JSON')
    p.set_defaults(func=main)


load_run = gates.load_run


def _positive_int(s):
    """argparse type shared with linkcheck --max."""
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not an integer: {s!r}') from None
    if n < 1:
        raise argparse.ArgumentTypeError(f'must be at least 1: {n}')
    return n


def load_watch():
    return layout.load_watch()


def main(args) -> int:
    rd, manifest = load_run(args.run)
    watch = load_watch()
    period = manifest['period']
    sources = SOURCES if args.source == 'all' else (args.source,)
    code = cli.EXIT_OK
    layout.collect_dir(rd).mkdir(parents=True, exist_ok=True)
    for source in sources:
        started = dates.now_iso()
        env, read_from, read_to, notes = from_envelope.build(
            source, period, manifest['run_id'], manifest['today'], watch.get('companies') or [])
        if env is None:
            status, count = 'failed', 0
            manifest['sources'][source] = {'status': status, 'read_from': None, 'read_to': None}
        else:
            schema.dump(layout.collect_dir(rd) / f'{source}.json', env, 'fiw.collect/1')
            status, count = env['status'], len(env['items'])
            manifest['sources'][source] = {'status': status, 'read_from': read_from, 'read_to': read_to}
        exit_code = cli.EXIT_OK if status == 'ok' else cli.EXIT_WARN
        stage = f'collect:{source}'
        manifest['stages'][stage] = gates.stage_entry(STAGE_STATUS[status], exit_code, None, started)
        gates.save_manifest(rd, manifest, stages=[stage], sources=[source])
        code = max(code, exit_code)
        if args.json:
            continue
        if env is None:
            skill = from_envelope.SPECS[source][0]
            print(f'{source}: not collected — no {skill} result in data/ overlaps {period["from"]}~{period["to"]}')
        else:
            print(f'{source}: {status} — {count} items ({len(env["requests"])} requests)')
            for r in env['requests']:
                if r['status'] != 'ok':
                    print(f'  {r["target"]}: {r["status"]}')
            if env['error']:
                print(f'  error: {env["error"]}')
        for note in notes:
            print(f'  note: {note}')
    if args.json:
        cli.print_json(manifest)
    else:
        print(f'collect: exit {code}')
    return code
