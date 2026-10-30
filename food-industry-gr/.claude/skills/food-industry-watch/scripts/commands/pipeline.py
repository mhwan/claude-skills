"""fiw.py pipeline — run a fixed chain of stages with one short line of output.

  --until judge   check → prefilter → cluster      (everything before Claude's relevance judgement)
  --from summary  verify → render → lint → linkcheck → publish   (everything after Claude's summary)

Each stage runs as its own fiw.py process; its full output goes to runs/food-industry-watch/<id>/work/logs/<stage>.txt. Exit 0 and 3 go on,
1 and 2 stop the chain at that stage. The chain exits with the stopping stage's code, else the highest code seen.
"""
from __future__ import annotations

import subprocess
import sys

from lib import cli, gates, layout

CHAINS = {'judge': ('check', 'prefilter', 'cluster'),
          'summary': ('verify', 'render', 'lint', 'linkcheck', 'publish')}
STOP = (cli.EXIT_FAIL, cli.EXIT_USAGE)
REASON_LEN = 200


def register(sub):
    p = sub.add_parser('pipeline', help='run the stages before relevance or after the summary, quietly')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    chain = p.add_mutually_exclusive_group(required=True)
    chain.add_argument('--until', choices=('judge',), help='check, prefilter, cluster')
    chain.add_argument('--from', dest='from_', choices=('summary',), help='verify, render, lint, linkcheck, publish')
    p.add_argument('--json', action='store_true', help='print the per-stage result JSON')
    p.set_defaults(func=main)


def _last_line(text) -> str:
    """The stage's last non-empty output line (its own summary line), cut short; never a whole body."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    line = lines[-1] if lines else '(no output)'
    return line if len(line) <= REASON_LEN else line[:REASON_LEN] + '…'


def run_stage(stage, run_id, log_path) -> int:
    cmd = [sys.executable, '-B', str(layout.skill_dir() / 'scripts' / 'fiw.py'), stage, '--run', run_id]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                       errors='replace')
    log_path.write_text(r.stdout, encoding='utf-8')
    return r.returncode


def main(args) -> int:
    rd, _ = gates.load_run(args.run)
    stages = CHAINS[args.until or args.from_]
    logs = layout.logs_dir(rd)
    logs.mkdir(parents=True, exist_ok=True)
    done, stopped, reason = [], None, None
    for stage in stages:
        log_path = logs / f'{stage}.txt'
        code = run_stage(stage, args.run, log_path)
        done.append({'stage': stage, 'exit': code, 'log': str(log_path)})
        if code in STOP:
            stopped = stage
            reason = _last_line(log_path.read_text(encoding='utf-8'))
            break
    code = done[-1]['exit'] if stopped else max(d['exit'] for d in done)
    if args.json:
        cli.print_json({'exit': code, 'stopped_at': stopped, 'reason': reason, 'stages': done,
                        'logs': str(logs)})
        return code
    if stopped:
        print(f'pipeline: stopped at {stopped} (exit {code}) — {reason}')
        print(f'pipeline: details {logs / (stopped + ".txt")}')
    else:
        warn = [d['stage'] for d in done if d['exit'] == cli.EXIT_WARN]
        steps = ' · '.join(f'{d["stage"]} {d["exit"]}' for d in done)
        tail = f' (warn: {", ".join(warn)} — read its log)' if warn else ''
        print(f'pipeline: {steps} — exit {code}{tail}')
        print(f'pipeline: details {logs}')
    return code
