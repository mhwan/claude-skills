"""fiw.py browser-config / browser-script — what the agent evaluates in the built-in browser."""
from __future__ import annotations

import json
from pathlib import Path

from lib import cli, layout, schema

from . import collect as collect_cmd
from . import doctor as doctor_cmd

# job -> (script file in scripts/browser/, page of the site to evaluate it on)
# The only browser job left: the article bodies of the news the report cites. Every list is read from
# the results of the other skills (see lib/from_envelope.py).
JOBS = {
    'bigkinds_bodies': ('bigkinds.js', 'https://www.bigkinds.or.kr/v2/news/index.do'),
}


def browser_dir() -> Path:
    return layout.skill_dir() / 'scripts' / 'browser'


def register(sub):
    p = sub.add_parser('browser-config', help='print the start() argument of a browser job')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--job', required=True, choices=tuple(JOBS), help='browser job')
    p.set_defaults(func=main_config)

    p = sub.add_parser('browser-script', help='print the files to evaluate for a browser job')
    p.add_argument('job', choices=tuple(JOBS), help='browser job')
    p.add_argument('--json', action='store_true', help='print {script, export, page} as JSON')
    p.set_defaults(func=main_script)


def job_config(job, rd) -> dict:
    req_path = rd / 'work' / 'body_requests.json'
    if not req_path.is_file():
        raise cli.UsageError(f'no body request list: {req_path} (written by the prepare step)')
    return {'ids': schema.load(req_path, 'fiw.body_requests/1')['ids']}


def main_config(args) -> int:
    rd, _ = collect_cmd.load_run(args.run)
    # One compact line: it is pasted verbatim into the start(...) call.
    print(json.dumps(job_config(args.job, rd), ensure_ascii=False, separators=(',', ':')))
    return cli.EXIT_OK


def main_script(args) -> int:
    name, page = JOBS[args.job]
    script, export = browser_dir() / name, browser_dir() / 'export.js'
    for f in (script, export):
        if not f.is_file():
            raise cli.UsageError(f'browser script missing: {f}')
    chunk = doctor_cmd.export_chunk_chars()
    if args.json:
        cli.print_json({'script': str(script), 'export': str(export), 'page': page, 'chunk_chars': chunk})
    else:
        print(f'page: {page}')
        print(f'script: {script}')
        print(f'export: {export}')
        print(f'chunk: {chunk}')
    return cli.EXIT_OK
