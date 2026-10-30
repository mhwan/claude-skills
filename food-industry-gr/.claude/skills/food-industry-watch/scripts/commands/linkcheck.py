"""fiw.py linkcheck — link gate L over the reference definitions of report.md."""
from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from lib import cli, dates, gates, layout, net, schema

from . import collect as collect_cmd

CLASSES = ('ok', 'dead', 'link_blocked', 'robots_exempt', 'error')
MAX_WORKERS = 4
BLOCKED_SHARE = 0.5
_DEFINITION = re.compile(r'^\[([^\]\n]+)\]:[ \t]+(\S+)[ \t]*$', re.MULTILINE)


def register(sub):
    p = sub.add_parser('linkcheck', help='link gate L over the reference links of report.md')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--max', type=collect_cmd._positive_int, default=None, metavar='N',
                   help='check only the first N reference links (the rest are skipped, exit 3)')
    p.add_argument('--json', action='store_true', help='print the fiw.gate/1 JSON')
    p.set_defaults(func=main)


def definitions(text) -> list:
    """[(key, url)] of every reference definition line, in file order."""
    return [(m.group(1), m.group(2)) for m in _DEFINITION.finditer(text)]


def classify_status(status) -> str:
    if 200 <= status <= 299:
        return 'ok'
    if status in (404, 410):
        return 'dead'
    if status in (400, 401, 403, 429):
        return 'link_blocked'
    return 'error'                      # 0 (no response, redirect loop) and every other status


def classify_blocked(reason) -> str:
    if reason.startswith('policy:') or reason == 'disallowed':
        return 'robots_exempt'
    if reason.startswith('robots_unreadable:'):
        return 'link_blocked'
    return 'error'


def _check_one(session, url) -> dict:
    try:
        r = session.get(url)
    except net.Blocked as e:
        return {'class': classify_blocked(e.reason), 'status': None, 'error': e.reason}
    except Exception as e:              # offline mode, malformed URL, stub failure: one link never stops the gate
        return {'class': 'error', 'status': 0, 'error': f'{type(e).__name__}: {e}'}
    return {'class': classify_status(r.status), 'status': r.status, 'error': r.error}


def _check_group(entries, policy, interval, sleep, clock) -> dict:
    """One Session for one host: requests in order, one at a time. {key: result}."""
    session = net.Session(policy, interval, sleep=sleep, clock=clock)
    by_url, out = {}, {}
    for key, url in entries:
        if url not in by_url:
            by_url[url] = _check_one(session, url)
        out[key] = by_url[url]
    return out


def check_links(entries, policy, *, interval=0.5, sleep=None, clock=None, workers=MAX_WORKERS) -> dict:
    """{key: {class, status, error}} for [(key, url)]: one thread per host, at most `workers` at once."""
    groups = {}
    for key, url in entries:
        groups.setdefault(urllib.parse.urlsplit(url).netloc.lower(), []).append((key, url))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_check_group, g, policy, interval, sleep, clock) for g in groups.values()]
        for f in futures:
            results.update(f.result())
    return results


def _record_dead(rd, found):
    """Add the dead URLs of this check to work/dead_links.json (fiw.dead_links/1).

    The file only grows: once render leaves a dead link out, the next report no longer defines it and a
    later linkcheck would not see it, yet render must keep leaving it out.
    """
    if not found:
        return
    path = rd / 'work' / 'dead_links.json'
    doc = gates.load_work(rd, 'dead_links', 'fiw.dead_links/1', required=False)
    links = doc['links'] if doc else []
    known = {e['url'] for e in links}
    now = dates.now_iso()
    for e in found:
        if e['url'] not in known:
            known.add(e['url'])
            links.append(dict(e, first_seen=now))
    schema.dump(path, {'schema': 'fiw.dead_links/1', 'links': links}, 'fiw.dead_links/1')


def run_check(rd, manifest, report, max_links=None):
    """(gate, notes) of the link check over `report`; writes gates/linkcheck.json and dead_links of rd."""
    with open(report, encoding='utf-8') as f:
        defs = definitions(f.read())
    policy = schema.load(layout.defaults_dir() / 'link_policy.json', 'fiw.link_policy/1')['hosts']

    todo = defs if max_links is None else defs[:max_links]
    skipped = [f'{key} {url}' for key, url in defs[len(todo):]]
    results = check_links(todo, policy)

    classified = {c: 0 for c in CLASSES}
    findings, notes = [], []
    urls = dict(todo)
    for key, _url in todo:
        r = results[key]
        classified[r['class']] += 1
        if r['class'] in ('dead', 'error'):
            findings.append({'where': key, 'kind': r['class'], 'detail': f'{r["status"]} {urls[key]}'})
            if r['error']:
                notes.append(f'  {key}: {r["error"]}')

    _record_dead(rd, [{'url': urls[key], 'status': results[key]['status'], 'key': key}
                      for key, _url in todo if results[key]['class'] == 'dead'])

    judged = sum(classified[c] for c in ('ok', 'dead', 'link_blocked', 'error'))
    if classified['dead']:
        code = cli.EXIT_FAIL
    elif (classified['error'] or skipped
          or (judged and classified['link_blocked'] > BLOCKED_SHARE * judged)):
        code = cli.EXIT_WARN
    else:
        code = cli.EXIT_OK

    gate = gates.write_gate(rd, manifest, 'linkcheck', code, len(todo), findings,
                            classified=classified, skipped=skipped,
                            input_hash=gates.file_hash(report))
    return gate, notes


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    report = layout.report_file(rd)
    if not report.is_file():
        raise cli.UsageError('missing report.md — run render first')
    gate, notes = run_check(rd, manifest, report, args.max)
    if args.json:
        cli.print_json(gate)
    else:
        gates.print_findings(gate['findings'])
        for line in notes:
            print(line)
        counts = ' · '.join(f'{c} {gate["classified"][c]}' for c in CLASSES)
        print(f'linkcheck: {gate["checked"]} checked, {counts}, skipped {len(gate["skipped"])}, exit {gate["exit"]}')
    return gate['exit']
