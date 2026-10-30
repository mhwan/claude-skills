"""fiw.py state — check the accumulated state (S gate) and the user-directed edits merge / split / lock / undo."""
from __future__ import annotations

import copy
import re

from lib import accum, cli, dates, gates, history, layout, schema

ID_RE = re.compile(r'^I-\d{4}$')
LOCK_TYPES = ('no_merge_with', 'exclude_pattern', 'rename', 'pin_section')


def register(sub):
    p = sub.add_parser('state', help='check or edit the accumulated issue state')
    s = p.add_subparsers(dest='state_command', metavar='STATE_COMMAND', required=True)

    c = s.add_parser('check', help='S gate over every state file')
    c.add_argument('--run', metavar='ID', help='also write the run\'s work/gates/state.json')
    c.add_argument('--json', action='store_true', help='print the result JSON')
    c.set_defaults(func=main_check)

    m = s.add_parser('merge', help='make issue B an alias of issue A')
    m.add_argument('a', metavar='A')
    m.add_argument('b', metavar='B')
    m.add_argument('--apply', action='store_true', help='change the state (default: print the plan)')
    m.add_argument('--json', action='store_true', help='print the result JSON')
    m.set_defaults(func=main_merge)

    sp = s.add_parser('split', help='move flow articles of issue A to a new issue')
    sp.add_argument('a', metavar='A')
    sp.add_argument('--articles', required=True, metavar='ID,ID', help='article ids of A\'s flow to move')
    sp.add_argument('--apply', action='store_true', help='change the state (default: print the plan)')
    sp.add_argument('--json', action='store_true', help='print the result JSON')
    sp.set_defaults(func=main_split)

    lk = s.add_parser('lock', help='add a user lock to an issue')
    lk.add_argument('a', metavar='A')
    lk.add_argument('--type', required=True, choices=LOCK_TYPES)
    lk.add_argument('--value', required=True)
    lk.add_argument('--apply', action='store_true', help='change the state (default: print the plan)')
    lk.add_argument('--json', action='store_true', help='print the result JSON')
    lk.set_defaults(func=main_lock)

    u = s.add_parser('undo', help='restore state/ from snapshot REV')
    u.add_argument('--to', required=True, type=int, metavar='REV')
    u.add_argument('--apply', action='store_true', help='change the state (default: print the plan)')
    u.add_argument('--json', action='store_true', help='print the result JSON')
    u.set_defaults(func=main_undo)

    h = s.add_parser('history', help='list the state snapshots')
    h.add_argument('--json', action='store_true', help='print the result JSON')
    h.set_defaults(func=main_history)


# ---------------------------------------------------------------- S gate

def check_state() -> list:
    """Findings [{where, kind, detail}] over state/ (addendum §D)."""
    findings = []

    def find(where, kind, detail):
        findings.append({'where': where, 'kind': kind, 'detail': detail})

    loaded = {}
    sd = layout.state_dir()
    for name, contract in accum.STATE_FILES.items():
        path = sd / name
        if not path.is_file():
            continue
        try:
            loaded[name] = schema.load(path, contract)
        except schema.SchemaError as e:
            find(name, 'schema', '; '.join([str(e)] + e.errors[:3]))
    runs = loaded.get('runs.json')
    latest, latest_entry = accum.latest_published_run(runs)
    weeks = layout.load_format()['dormancy_weeks']

    issues = (loaded.get('issues.json') or {}).get('issues', [])
    ids = [i['id'] for i in issues]
    live = set(ids)
    for k, iid in enumerate(ids):
        if not ID_RE.match(iid):
            find(f'issues/{iid}', 'id', 'id must be I- and four digits')
        if ids.count(iid) > 1 and ids.index(iid) == k:
            find(f'issues/{iid}', 'id', f'id used {ids.count(iid)} times')
    owner = {}
    for i in issues:
        for a in i['aliases']:
            if a in live:
                find(f'issues/{i["id"]}', 'alias', f'alias {a} is also a live id')
            if a in owner and owner[a] != i['id']:
                find(f'issues/{i["id"]}', 'alias', f'alias {a} also belongs to {owner[a]}')
            owner[a] = i['id']
    known = live | set(owner)
    periods = accum.bigkinds_periods(loaded.get('coverage.json'))
    for i in issues:
        where = f'issues/{i["id"]}'
        for lock in i['locks']:
            t, v = lock['type'], lock['value']
            if t == 'no_merge_with' and v not in known:
                find(where, 'lock', f'no_merge_with names an unknown issue {v!r}')
            elif t == 'no_merge_with' and v == i['id']:
                find(where, 'lock', 'no_merge_with names the issue itself')
            elif t == 'pin_section' and v not in accum.ISSUE_SECTIONS:
                find(where, 'lock', f'pin_section {v!r} is not one of {"/".join(accum.ISSUE_SECTIONS)}')
            elif t in ('rename', 'exclude_pattern') and not v.strip():
                find(where, 'lock', f'{t} value is empty')
        for n, node in enumerate(i['flow']):
            if not node['url'].strip():
                find(f'{where}/flow[{n}]', 'flow', 'flow node without url')
        want = 'dormant' if accum.is_dormant(i, periods, weeks, latest) else 'active'
        if i['status'] != want:
            find(where, 'dormancy', f'status {i["status"]}, recomputed {want} '
                 f'(dormancy_weeks {weeks}, last_article {i.get("last_article")})')

    notices = loaded.get('notices.json')
    if notices and latest is not None:
        today = _run_today(latest)
        for it in notices['items']:
            if today is not None and (it['open_to'] is None or it['open_to'] < today):
                find(f'notices/{it["id"]}', 'notice', f'open_to {it["open_to"]} is before {today} '
                     f'(today of {latest}) — it should have been removed')

    for r in (runs or {}).get('runs', []):
        if r['report'] and r['report'] != accum.report_ref(r['run_id']):
            find(f'runs/{r["run_id"]}', 'raw', f'report {r["report"]!r} is not {accum.report_ref(r["run_id"])}')  # gr-structure: ignore
        elif r['report'] and not (layout.output_root() / r['report']).is_file():
            find(f'runs/{r["run_id"]}', 'raw', f'{r["report"]} is missing')  # gr-structure: ignore

    return findings


def _run_today(run_id):
    path = layout.manifest_path(layout.run_dir(run_id))
    if not path.is_file():
        return None
    return schema.load(path, 'fiw.manifest/1')['today']


def main_check(args) -> int:
    findings = check_state()
    code = cli.EXIT_FAIL if findings else cli.EXIT_OK
    if args.run:
        rd, manifest = gates.load_run(args.run)
        n = len(list(layout.state_dir().glob('*.json'))) if layout.state_dir().is_dir() else 0
        gates.write_gate(rd, manifest, 'state', code, n, findings)
    if args.json:
        cli.print_json({'exit': code, 'findings': findings})
    else:
        gates.print_findings(findings)
        print(f'state check: {len(findings)} findings, exit {code}')
    return code


# ---------------------------------------------------------------- edits

def _load_issues():
    obj = accum.load_state('issues.json')
    if obj is None:
        raise cli.UsageError('no state/issues.json — nothing to edit yet')
    return obj


def _weeks():
    return layout.load_format()['dormancy_weeks']


def _recompute_status(issue):
    latest, _ = accum.latest_published_run(accum.load_state('runs.json'))
    periods = accum.bigkinds_periods(accum.load_state('coverage.json'))
    issue['status'] = 'dormant' if accum.is_dormant(issue, periods, _weeks(), latest) else 'active'


def _next_id(issues) -> str:
    nums = [int(x[2:]) for i in issues for x in [i['id']] + i['aliases'] if ID_RE.match(x)]
    return f'I-{max(nums, default=0) + 1:04d}'


def _refuse(msg, as_json):
    if as_json:
        cli.print_json({'exit': cli.EXIT_FAIL, 'error': msg})
    else:
        print(f'state: refused — {msg}')
    return cli.EXIT_FAIL


def _commit(obj, command, plan_lines, args):
    """Print the plan; with --apply snapshot, write, check (restore on findings)."""
    if not args.apply:
        if args.json:
            cli.print_json({'exit': cli.EXIT_OK, 'applied': False, 'plan': plan_lines})
        else:
            for line in plan_lines:
                print(line)
            print('state: preview only — nothing changed (add --apply)')
        return cli.EXIT_OK
    rev = history.snapshot(command)
    schema.dump(layout.state_dir() / 'issues.json', obj, 'fiw.state.issues/1')
    findings = check_state()
    if findings:
        history.restore(rev)
        if args.json:
            cli.print_json({'exit': cli.EXIT_FAIL, 'applied': False, 'findings': findings})
        else:
            gates.print_findings(findings)
            print(f'state: check failed after the change — restored snapshot {rev}, exit 1')
        return cli.EXIT_FAIL
    if args.json:
        cli.print_json({'exit': cli.EXIT_OK, 'applied': True, 'rev': rev, 'plan': plan_lines})
    else:
        for line in plan_lines:
            print(line)
        print(f'state: applied (previous state saved as snapshot {rev}), exit 0')
    return cli.EXIT_OK


def merge(obj, a, b):
    """New issues object with B merged into A, or a refusal string."""
    issues = obj['issues']
    by = {i['id']: i for i in issues}
    if a == b:
        return f'cannot merge {a} with itself'
    for x in (a, b):
        if x not in by:
            alias = accum.alias_map(issues).get(x)
            return f'{x} is an alias of {alias} — use {alias}' if alias else f'unknown issue {x}'
    A, B = by[a], by[b]
    if any(lk['type'] == 'no_merge_with' and lk['value'] in (a, b) for lk in A['locks'] + B['locks']):
        return f'a no_merge_with lock forbids merging {a} and {b}'
    new = copy.deepcopy(obj)
    nb = {i['id']: i for i in new['issues']}
    A, B = nb[a], nb[b]
    A['aliases'] = A['aliases'] + [b] + [x for x in B['aliases'] if x not in A['aliases']]
    seen = {n['article_id'] for n in A['flow']}
    A['flow'] = A['flow'] + [n for n in B['flow'] if n['article_id'] not in seen]
    A['flow'].sort(key=lambda n: (n['date'], n['article_id']))
    A['first_seen'] = min(A['first_seen'], B['first_seen'])
    lasts = [x for x in (A.get('last_article'), B.get('last_article')) if x]
    A['last_article'] = max(lasts) if lasts else None
    runs = [x for x in (A.get('last_run'), B.get('last_run')) if x]
    if runs:
        A['last_run'] = max(runs, key=_run_key)
    A['last_sentences'] = A['last_sentences'] + B['last_sentences']
    A['locks'] = A['locks'] + B['locks']
    A['corrections'] = A['corrections'] + B['corrections']
    new['issues'] = [i for i in new['issues'] if i['id'] != b]
    for i in new['issues']:
        for lk in i['locks']:
            if lk['type'] == 'no_merge_with' and lk['value'] == b:
                lk['value'] = a
    _recompute_status(A)
    return new


def _run_key(run_id):
    return (run_id[:10], int(run_id[11:]))


def split(obj, a, article_ids):
    issues = obj['issues']
    by = {i['id']: i for i in issues}
    if a not in by:
        alias = accum.alias_map(issues).get(a)
        return (f'{a} is an alias of {alias} — use {alias}' if alias else f'unknown issue {a}'), None
    flow_ids = [n['article_id'] for n in by[a]['flow']]
    missing = [x for x in article_ids if x not in flow_ids]
    if missing:
        return f'not in the flow of {a}: {", ".join(missing)}', None
    if set(flow_ids) <= set(article_ids):
        return f'every flow article of {a} would move — nothing would be left', None
    new = copy.deepcopy(obj)
    A = next(i for i in new['issues'] if i['id'] == a)
    nid = _next_id(new['issues'])
    moved = [n for n in A['flow'] if n['article_id'] in article_ids]
    A['flow'] = [n for n in A['flow'] if n['article_id'] not in article_ids]
    moved_ids = set(article_ids)
    mine = [s for s in A['last_sentences'] if s['article_ids'] and set(s['article_ids']) <= moved_ids]
    A['last_sentences'] = [s for s in A['last_sentences'] if s not in mine]
    now = dates.now_iso()
    moved.sort(key=lambda n: (n['date'], n['article_id']))
    B = {'id': nid, 'title': moved[0]['text'], 'section': A['section'], 'status': 'active',
         'first_seen': moved[0]['date'], 'last_article': moved[-1]['date'], 'aliases': [],
         'flow': moved, 'last_sentences': mine,
         'locks': [{'type': 'no_merge_with', 'value': a, 'by': 'user', 'at': now}], 'corrections': []}
    if A.get('last_run'):
        B['last_run'] = A['last_run']
    A['locks'].append({'type': 'no_merge_with', 'value': nid, 'by': 'user', 'at': now})
    A['first_seen'] = min(n['date'] for n in A['flow'])
    A['last_article'] = max(n['date'] for n in A['flow'])
    new['issues'].append(B)
    _recompute_status(A)
    _recompute_status(B)
    return new, nid


def main_merge(args) -> int:
    obj = _load_issues()
    new = merge(obj, args.a, args.b)
    if isinstance(new, str):
        return _refuse(new, args.json)
    A = next(i for i in new['issues'] if i['id'] == args.a)
    plan = [f'merge: {args.b} becomes an alias of {args.a}', f'{args.a} flow: {len(A["flow"])} nodes, '
            f'aliases {", ".join(A["aliases"])}, status {A["status"]}']
    return _commit(new, f'state merge {args.a} {args.b}', plan, args)


def main_split(args) -> int:
    obj = _load_issues()
    ids = [x.strip() for x in args.articles.split(',') if x.strip()]
    if not ids:
        raise cli.UsageError('--articles needs at least one article id')
    new, nid = split(obj, args.a, ids)
    if isinstance(new, str):
        return _refuse(new, args.json)
    plan = [f'split: {len(ids)} flow node(s) of {args.a} move to new issue {nid}',
            f'no_merge_with lock both ways between {args.a} and {nid}']
    return _commit(new, f'state split {args.a}', plan, args)


def main_lock(args) -> int:
    obj = _load_issues()
    by = {i['id']: i for i in obj['issues']}
    if args.a not in by:
        alias = accum.alias_map(obj['issues']).get(args.a)
        return _refuse(f'{args.a} is an alias of {alias} — use {alias}' if alias else f'unknown issue {args.a}',
                       args.json)
    value = args.value.strip()
    if not value:
        return _refuse('lock value is empty', args.json)
    if args.type == 'pin_section' and value not in accum.ISSUE_SECTIONS:
        return _refuse(f'pin_section must be one of {"/".join(accum.ISSUE_SECTIONS)}', args.json)
    if args.type == 'no_merge_with':
        known = set(by) | set(accum.alias_map(obj['issues']))
        if value not in known or value == args.a:
            return _refuse(f'no_merge_with needs another existing issue id, got {value!r}', args.json)
    new = copy.deepcopy(obj)
    target = next(i for i in new['issues'] if i['id'] == args.a)
    target['locks'].append({'type': args.type, 'value': value, 'by': 'user', 'at': dates.now_iso()})
    plan = [f'lock: {args.a} {args.type} = {value}']
    return _commit(new, f'state lock {args.a} {args.type}', plan, args)


def main_undo(args) -> int:
    if args.to not in history.rev_numbers():
        return _refuse(f'snapshot {args.to} does not exist', args.json)
    files = history.files_of(args.to)
    plan = [f'undo: state/ becomes snapshot {args.to} ({", ".join(files) or "empty"})']
    if not args.apply:
        if args.json:
            cli.print_json({'exit': cli.EXIT_OK, 'applied': False, 'plan': plan})
        else:
            print(plan[0])
            print('state: preview only — nothing changed (add --apply)')
        return cli.EXIT_OK
    rev = history.snapshot(f'state undo --to {args.to}')
    history.restore(args.to)
    if args.json:
        cli.print_json({'exit': cli.EXIT_OK, 'applied': True, 'rev': rev, 'plan': plan})
    else:
        print(plan[0])
        print(f'state: restored snapshot {args.to} (the state before undo is snapshot {rev}), exit 0')
    return cli.EXIT_OK


def main_history(args) -> int:
    revs = history.load_revs()['revs']
    if args.json:
        cli.print_json({'revs': revs})
    else:
        for r in revs:
            print(f'{r["rev"]}\t{r["at"]}\t{r["command"]}\t{r["run_id"] or ""}')
        print(f'state history: {len(revs)} snapshot(s)')
    return cli.EXIT_OK
