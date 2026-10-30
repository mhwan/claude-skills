"""fiw.py config — bring config/ up to date with the shipped defaults, keeping the user's own values."""
from __future__ import annotations

import json

from lib import cli, layout


def register(sub):
    p = sub.add_parser('config', help='update config/ from the shipped defaults')
    p.add_argument('--take-default', nargs='+', default=[], metavar='KEY',
                   help='replace these top-level keys with the default value')
    p.add_argument('--json', action='store_true', help='print the reports JSON')
    p.set_defaults(func=main)


def main(args) -> int:
    known = set()
    for name in layout.COPIED_DEFAULTS:
        with open(layout.defaults_dir() / f'{name}.json', encoding='utf-8') as f:
            known |= set(json.load(f)) - {'schema'}
    unknown = sorted(set(args.take_default) - known)
    if unknown:
        raise cli.UsageError(f'--take-default: not a key of the defaults: {", ".join(unknown)}')
    layout.ensure_home(reconcile=False)
    reports = layout.reconcile_config(take=set(args.take_default))
    if args.json:
        cli.print_json({'schema': 'fiw.config/1', 'reports': reports})
    else:
        for rep in reports:
            line = layout.reconcile_line(rep)
            kept = [k for k in rep['kept'] if k not in rep['warn']]
            if kept:
                line = (line + '; ' if line else f"config/{rep['file']}: ") + 'your values: ' + ', '.join(kept)  # gr-structure: ignore
            print(line or f"config/{rep['file']}: up to date")  # gr-structure: ignore
    return cli.EXIT_WARN if any(rep['warn'] for rep in reports) else cli.EXIT_OK
