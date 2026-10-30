"""fiw.py correct — apply a user's correction to a published run after the evidence gate (S1, D33).

m6-m7-spec §3 and m6-m7-addendum §E: the change is checked against the source text (re-fetched when
the run was pruned), the raw's report.md is re-rendered and linted in place and the correction is kept in
the issue's state. Period reports built from the raw are rebuilt with fiw.py report.
"""
from __future__ import annotations

import copy
import difflib
import json
import re

from lib import accum, cli, dates, evidence, gates, history, layout, runview, schema

from . import _accumulate as acc
from . import _lint_parse as mdp
from . import _verify_checks as vc
from . import lint as lint_cmd
from . import render as render_cmd
from . import state as state_cmd
from ._render_model import Run

MAX_REFS = 10
TOKEN = re.compile(r'^([A-Za-z_]+)(?:\[(\d+)\])?$')


def register(sub):
    p = sub.add_parser('correct', help='apply a correction file to a published run after the evidence gate')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument('--file', metavar='FILE', help='a fiw.correction/1 file (work/corrections/N.json)')
    what.add_argument('--reviewed', action='store_true',
                      help='publish the verified work/summary.json as a revision (fixes from the review after publish)')
    p.add_argument('--request', metavar='TEXT', help='with --reviewed: what the fixes answer (kept in the state)')
    p.add_argument('--apply', action='store_true', help='write the change (default: check and print the diff)')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


class Refused(Exception):
    def __init__(self, code, message, findings=()):
        super().__init__(message)
        self.code, self.findings = code, list(findings)


# ---------------------------------------------------------------- target and field

def target_entry(summary, issue, issue_ids):
    """(kind, name, entry) — kind issue / market / three_lines."""
    if issue.startswith('market:'):
        topic = issue[len('market:'):]
        for m in summary['market']:
            if m['topic'] == topic:
                return 'market', topic, m
        raise cli.UsageError(f'no market topic {topic!r} in summary.json')
    if issue.startswith('three_lines:'):
        n = issue[len('three_lines:'):]
        if not n.isdigit() or int(n) >= len(summary['three_lines']):
            raise cli.UsageError(f'no three_lines entry {n!r} (there are {len(summary["three_lines"])})')
        return 'three_lines', n, summary['three_lines'][int(n)]
    keys = {i['key']: i for i in summary['issues']}
    key = issue if issue in keys else {v: k for k, v in (issue_ids or {}).items()}.get(issue)
    if key not in keys:
        raise cli.UsageError(f'no summary issue {issue!r} (keys: {", ".join(keys)})')
    return 'issue', key, keys[key]


def _tokens(field):
    parts = field.split('.')
    out = []
    for part in parts:
        m = TOKEN.match(part)
        if not m:
            raise cli.UsageError(f'bad field path {field!r} — use name, name[N], name.name[N]')
        out.append((m.group(1), None if m.group(2) is None else int(m.group(2))))
    return out


def set_field(entry, field, new):
    """(changed copy of entry, old value or None). index == length appends; new None deletes."""
    out = copy.deepcopy(entry)
    tokens = _tokens(field)
    node = out
    for name, idx in tokens[:-1]:
        if not isinstance(node, dict) or name not in node:
            raise cli.UsageError(f'{field}: {name!r} does not exist')
        node = node[name]
        if idx is not None:
            if not isinstance(node, list) or idx >= len(node):
                raise cli.UsageError(f'{field}: {name}[{idx}] does not exist')
            node = node[idx]
    name, idx = tokens[-1]
    if not isinstance(node, dict):
        raise cli.UsageError(f'{field}: parent is not an object')
    if idx is None:
        old = node.get(name)
        if new is None:
            node.pop(name, None)
        else:
            node[name] = new
        return out, old
    lst = node.setdefault(name, [])
    if not isinstance(lst, list) or idx > len(lst):
        raise cli.UsageError(f'{field}: index {idx} is beyond the end of {name} ({len(lst) if isinstance(lst, list) else "-"})')
    if idx == len(lst):
        if new is None:
            raise cli.UsageError(f'{field}: nothing to delete at index {idx}')
        lst.append(new)
        return out, None
    old = lst[idx]
    if new is None:
        del lst[idx]
    else:
        lst[idx] = new
    return out, old


def refs_in(value) -> list:
    """Every ref string inside a summary value (keys ref, refs, and evidence refs)."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == 'ref' and isinstance(v, str):
                out.append(v)
            elif k == 'refs' and isinstance(v, list):
                out += [x for x in v if isinstance(x, str)]
            else:
                out += refs_in(v)
    elif isinstance(value, list):
        for v in value:
            out += refs_in(v)
    return out


def show(value) -> str:
    if value is None:
        return '(없음)'
    if isinstance(value, dict) and isinstance(value.get('text'), str):
        return value['text']
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


# ---------------------------------------------------------------- source texts

class Sources:
    """Source text per ref of the run (article body / flow excerpt, else the fetched body)."""

    def __init__(self, rd, todo):
        env = runview.envelope(rd, 'bigkinds')
        self.by_url = {a['url']: a['id'] for a in (env or {}).get('items', []) if a['url']}
        # a flow candidate is often an article of an earlier run: a URL this run lacks is looked up in the other
        # runs' bigkinds envelopes (newest run first). URLs match exactly — the skill has no URL normaliser.
        flow_urls = {f['url'] for g in todo['issues'] for f in g.get('flow_candidates', []) if f['url']}
        if flow_urls - set(self.by_url):
            for d in sorted((p for p in layout.runs_dir().iterdir() if p.is_dir() and p != rd), reverse=True):
                for a in (runview.envelope(d, 'bigkinds') or {}).get('items', []):
                    if a['url']:
                        self.by_url.setdefault(a['url'], a['id'])
        bodies = gates.load_work(rd, 'bodies', 'fiw.bodies/1', required=False)
        self.bodies = {b['id']: b['body'] for b in (bodies or {}).get('articles', []) if b['body'].strip()}
        self.errors = {e['id'] for e in (bodies or {}).get('errors', [])}
        self.kind, self.ref_id, self.text = {}, {}, {}
        for group in todo['issues'] + todo['market']:
            for a in group['articles']:
                self.kind[a['ref']], self.ref_id[a['ref']] = 'article', a['id']
                self.text[a['ref']] = a['body'] or self.bodies.get(a['id'], '')
            for f in group.get('flow_candidates', []):
                self.kind[f['ref']] = 'flow'
                self.ref_id[f['ref']] = self.by_url.get(f['url'])
                self.text[f['ref']] = f['excerpt'] or self.bodies.get(self.by_url.get(f['url']), '')

    def missing(self, refs) -> list:
        return [r for r in refs if r in self.kind and not self.text.get(r)]

    def fill(self, todo, refs):
        """todo copy with the source text of refs written into article bodies / flow excerpts."""
        out = copy.deepcopy(todo)
        want = set(refs)
        for group in out['issues'] + out['market']:
            for a in group['articles']:
                if a['ref'] in want and not a['body']:
                    a['body'] = self.text.get(a['ref'], '')
            for f in group.get('flow_candidates', []):
                if f['ref'] in want and not f['excerpt']:
                    f['excerpt'] = self.text.get(f['ref'], '')
        return out


def own_refs(kind, name, entry, todo) -> set:
    by_key = {t['key']: t for t in todo['issues']}
    if kind == 'market':
        group = next((t for t in todo['market'] if t['topic'] == name), None)
        return {a['ref'] for a in (group or {}).get('articles', [])}
    key = entry['issue'] if kind == 'three_lines' else name
    t = by_key.get(key) or {}
    return {a['ref'] for a in t.get('articles', [])} | {f['ref'] for f in t.get('flow_candidates', [])}


# ---------------------------------------------------------------- gate

def evidence_gate(rd, todo, summary, kind, name, scope, period_to):
    relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1', required=False)
    prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1', required=False)
    envelopes = {s: runview.envelope(rd, s) for s in ('bigkinds', 'korea_kr') + runview.NOTICE_SOURCES}
    v = vc.Verifier(todo, summary, layout.load_watch(), layout.load_format(),
                    envelopes, prefilter, relevance, period_to=period_to)
    if scope == 'full':
        v.run()
    else:
        v.run_entry(kind, name)
    return v


def report_urls(text) -> set:
    return {u for _, u in mdp.definitions(mdp.raw_blocks(text))}


# ---------------------------------------------------------------- main

def check(rd, manifest, corr, entry_info, todo, summary):
    """(new summary, scope, verifier, diff); raises Refused or UsageError."""
    kind, name, entry = entry_info
    new_entry, old = set_field(entry, corr['field'], corr['new'])
    if corr['new'] is None and old is None:
        raise cli.UsageError(f'{corr["field"]}: nothing to delete')
    own = own_refs(kind, name, new_entry, todo)
    if len(corr['refs']) > MAX_REFS:
        raise cli.UsageError(f'at most {MAX_REFS} refs per correction (02-plan §1.1); split it')
    inside = set(refs_in(corr['new']))
    for r in corr['refs']:
        if r not in own:
            raise cli.UsageError(f'ref {r} does not belong to {name} (its refs: {", ".join(sorted(own))})')
        if r not in inside:
            raise cli.UsageError(f'ref {r} is not used in the new value')
    if corr['new'] is not None and not corr['evidence']:
        raise cli.UsageError('evidence needs at least one {ref, quote} from the source text')
    for e in corr['evidence']:
        if e['ref'] not in corr['refs']:
            raise cli.UsageError(f'evidence ref {e["ref"]} is not in refs')
    if not corr['request'].strip():
        raise cli.UsageError('request (the user\'s words) is empty')

    src = Sources(rd, todo)
    needed = sorted(set(refs_in(new_entry)) | set(corr['refs']))
    unknown = [r for r in needed if r not in src.kind]
    if unknown:
        raise Refused(cli.EXIT_FAIL, f'refs not in todo.json: {", ".join(unknown)}')
    missing = src.missing(needed)
    if missing:
        ids = sorted({src.ref_id[r] for r in missing if src.ref_id[r]})
        no_id = [r for r in missing if not src.ref_id[r]]
        failed = sorted(i for i in ids if i in src.errors)
        if no_id or failed:
            raise Refused(cli.EXIT_FAIL, '근거 확인 불가 — 원문을 다시 받지 못했다: '
                          + ', '.join(failed + no_id))
        if len(ids) > MAX_REFS:
            raise cli.UsageError(f'{len(ids)} bodies to re-fetch; at most {MAX_REFS} per correction')
        schema.dump(rd / 'work' / 'body_requests.json', {'schema': 'fiw.body_requests/1', 'ids': ids},
                    'fiw.body_requests/1')
        raise Refused(cli.EXIT_WARN, f'bodies needed: {len(ids)} — run the bigkinds_bodies browser job, ingest, '
                      'then correct again')
    bad = [e for e in corr['evidence'] if evidence.norm_space(e['quote']) not in evidence.norm_space(src.text[e['ref']])]
    if bad:
        raise Refused(cli.EXIT_FAIL, 'evidence quote not in the source text: '
                      + '; '.join(f'{e["ref"]} 「{e["quote"]}」' for e in bad))

    new_summary = copy.deepcopy(summary)
    if kind == 'issue':
        new_summary['issues'] = [new_entry if i['key'] == name else i for i in new_summary['issues']]
    elif kind == 'market':
        new_summary['market'] = [new_entry if m['topic'] == name else m for m in new_summary['market']]
    else:
        new_summary['three_lines'][int(name)] = new_entry
    # full scope runs every check, E15 over every todo article cited or not: it needs (and fills) all of them
    article_refs = {a['ref'] for g in todo['issues'] + todo['market'] for a in g['articles']}
    full_refs = sorted(r for r in set(refs_in(new_summary)) | article_refs if r in src.kind)
    scope = 'full' if not src.missing(full_refs) else 'entry'
    filled = src.fill(todo, full_refs if scope == 'full' else needed)
    if kind == 'issue' and corr['field'] == 'title' and corr['new'] is not None:
        for t in filled['issues']:
            if t['key'] == name:
                t['locks']['rename'] = corr['new']['text']        # the correction becomes the user's title
    if scope == 'entry':
        g = gates.read_gate(rd, 'verify')
        if g is None or g['exit'] != 0 or g['input_hash'] != gates.file_hash(rd / 'work' / 'summary.json'):
            raise Refused(cli.EXIT_FAIL, 'the current summary.json has no passing verify gate — the unchanged '
                          'fields cannot be trusted; rerun verify first')
    v = evidence_gate(rd, filled, new_summary, kind, name, scope, manifest['period']['to'])
    diff = f'- {show(old)}\n+ {show(corr["new"])}'
    return new_summary, scope, v, diff


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    runs_state = accum.load_state('runs.json')
    entry = next((r for r in (runs_state or {}).get('runs', []) if r['run_id'] == manifest['run_id']), None)
    if entry is None or not entry['report']:
        raise cli.UsageError(f'run {manifest["run_id"]} is not published — fix the summary and run verify instead')
    try:
        as_of = history.state_as_of(manifest['run_id'], runs_state)
    except LookupError as e:
        return _out(args, cli.EXIT_FAIL, str(e), [], None, False)
    if args.reviewed:
        return reviewed(args, rd, manifest, runs_state, entry, as_of)
    try:
        corr = schema.load(args.file, 'fiw.correction/1')
    except OSError as e:
        raise cli.UsageError(f'cannot read {args.file}: {e}') from None
    todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
    summary = gates.load_work(rd, 'summary', 'fiw.summary/1')
    info = target_entry(summary, corr['issue'], entry.get('issue_ids'))
    try:
        new_summary, scope, v, diff = check(rd, manifest, corr, info, todo, summary)
    except Refused as e:
        return _out(args, e.code, str(e), [], None, False)
    if v.findings:
        return _out(args, cli.EXIT_FAIL, 'the corrected text fails the evidence gate — nothing changed',
                    v.findings, diff, False)
    if not args.apply:
        return _out(args, cli.EXIT_OK, f'evidence gate passed ({scope}, {v.checked} fields) — preview only, '
                    'nothing changed (add --apply)', [], diff, False)
    return apply(args, rd, manifest, runs_state, entry, corr, info, new_summary, scope, v, diff, as_of)


def rendered_and_linted(rd, manifest, as_of):
    """render + lint of the run, reading state/ as of its time (`as_of`: a history folder, None = current state)."""
    with layout.reading_state(as_of):
        text, _ = render_cmd.build(Run(rd, manifest))
        return text, lint_cmd.evaluate(rd, manifest, text)


def apply(args, rd, manifest, runs_state, entry, corr, info, new_summary, scope, v, diff, as_of) -> int:
    kind, name, _ = info
    files = {p: (p.read_bytes() if p.is_file() else None) for p in (
        rd / 'work' / 'summary.json', layout.report_file(rd), layout.gates_dir(rd) / 'verify.json', layout.gates_dir(rd) / 'lint.json')}
    old_report = (files[layout.report_file(rd)] or b'').decode('utf-8')

    def restore():
        for p, data in files.items():
            if data is None:
                if p.is_file():
                    p.unlink()
            else:
                p.write_bytes(data)

    rev = history.snapshot(f'correct {corr["issue"]} {corr["field"]}', manifest['run_id'])
    schema.dump(rd / 'work' / 'summary.json', new_summary, 'fiw.summary/1')
    gates.write_gate(rd, manifest, 'verify', cli.EXIT_OK, v.checked, [],
                     classified={f'correction_scope_{scope}': 1, 'notice_periods_reparsed': v.notices_checked},
                     input_hash=gates.file_hash(rd / 'work' / 'summary.json'))
    text, lint = rendered_and_linted(rd, manifest, as_of)
    schema.atomic_write(layout.report_file(rd), text)
    gates.record_stage(rd, manifest, 'render', cli.EXIT_OK, gates.file_hash(rd / 'work' / 'summary.json'))
    gates.write_gate(rd, manifest, 'lint', cli.EXIT_FAIL if lint.findings else cli.EXIT_OK, lint.checked,
                     lint.findings, input_hash=gates.file_hash(layout.report_file(rd)))
    if lint.findings:
        restore()
        history.restore(rev)
        return _out(args, cli.EXIT_FAIL, 'the re-rendered report fails lint — restored', lint.findings, diff, False)
    new_urls = sorted(report_urls(text) - report_urls(old_report))
    if new_urls:
        restore()
        history.restore(rev)
        return _out(args, cli.EXIT_FAIL, '새 링크 — a correction may only cite links already checked in this report: '
                    + ', '.join(new_urls[:5]), [], diff, False)

    target = layout.report_file(rd)                       # the raw is corrected in place; period reports are rebuilt
    issues_obj = accum.load_state('issues.json') or {'schema': 'fiw.state.issues/1', 'issues': []}
    key = new_summary['three_lines'][int(name)]['issue'] if kind == 'three_lines' else name
    sid = (entry.get('issue_ids') or {}).get(key)
    state_issue = next((i for i in issues_obj['issues'] if i['id'] == sid), None)
    if state_issue is not None:
        state_issue['corrections'].append({'rev': rev, 'request': corr['request'], 'diff': diff,
                                           'gate': 'verify PASS'})
        if kind == 'issue':
            data = acc.RunData(rd, manifest)
            todo_issue = next(t for t in data.todo['issues'] if t['key'] == key)
            s = next(i for i in new_summary['issues'] if i['key'] == key)
            refs = acc._resolver(data, todo_issue, state_issue)
            state_issue['last_sentences'] = acc.last_sentences(s, refs, dates.now_iso(), manifest['run_id'])
            if corr['field'] == 'title' and corr['new'] is not None:
                state_issue['title'] = corr['new']['text']
                state_issue['locks'] = [lk for lk in state_issue['locks'] if lk['type'] != 'rename'] + [
                    {'type': 'rename', 'value': corr['new']['text'], 'by': 'user', 'at': dates.now_iso()}]
    schema.dump(layout.state_dir() / 'runs.json', runs_state, 'fiw.state.runs/1')
    schema.dump(layout.state_dir() / 'issues.json', issues_obj, 'fiw.state.issues/1')
    findings = state_cmd.check_state()
    if findings:
        restore()
        history.restore(rev)
        return _out(args, cli.EXIT_FAIL, 'state check failed after the correction — restored', findings, diff, False)
    note = '' if state_issue is not None else ' (no state issue for this entry — report only)'
    return _out(args, cli.EXIT_OK, f'applied — {accum.report_ref(manifest["run_id"])} re-rendered, state saved as snapshot {rev}{note}; '
                'rebuild any period report that covers this raw (fiw.py report)', [], diff,
                True, target)


# ---------------------------------------------------------------- --reviewed

def sentence_diff(old, new) -> str:
    """`- old` / `+ new` lines for the sentences that differ (the format of a correction diff)."""
    lines = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op != 'equal':
            lines += [f'- {t}' for t in old[i1:i2]] + [f'+ {t}' for t in new[j1:j2]]
    return '\n'.join(lines)


def reviewed(args, rd, manifest, runs_state, entry, as_of) -> int:
    """Publish the summary fixed after the review (parts fixed, merged, verify passed) as a revision.

    The changed fields were already gated by verify on the whole summary; this only checks that the gate
    belongs to this exact summary.json, then re-renders, lints and keeps each changed issue's sentences
    as a correction so the next run's prior starts from the reviewed text.
    """
    if not (args.request or '').strip():
        raise cli.UsageError('--reviewed needs --request (what the fixes answer)')
    summary_path = rd / 'work' / 'summary.json'
    g = gates.read_gate(rd, 'verify')
    if g is None or g['exit'] != 0 or g['input_hash'] != gates.file_hash(summary_path):
        return _out(args, cli.EXIT_FAIL, 'work/summary.json has no passing verify gate — run verify first; '
                    'nothing changed', [], None, False)
    summary = gates.load_work(rd, 'summary', 'fiw.summary/1')
    published = layout.output_root() / entry['report']
    old_report = published.read_text(encoding='utf-8')
    text, lint = rendered_and_linted(rd, manifest, as_of)
    if text == old_report:
        return _out(args, cli.EXIT_FAIL, 'the summary renders to the published report — nothing to publish',
                    [], None, False)

    issues_obj = accum.load_state('issues.json') or {'schema': 'fiw.state.issues/1', 'issues': []}
    by_id = {i['id']: i for i in issues_obj['issues']}
    changed = []                                   # (key, state issue, diff)
    for s in summary['issues']:
        it = by_id.get((entry.get('issue_ids') or {}).get(s['key']))
        if it is None:
            continue
        d = sentence_diff([x['text'] for x in it['last_sentences']], [x['text'] for x in s['sentences']])
        if d:
            changed.append((s['key'], it, d))
    report_diff = '\n'.join(l for l in difflib.unified_diff(old_report.splitlines(), text.splitlines(),
                                                            lineterm='', n=0)
                            if l[:1] in '+-' and not l.startswith(('+++', '---')))
    diff = '\n'.join([f'[{k}]\n{d}' for k, _, d in changed] + [f'[report]\n{report_diff}'])
    new_urls = sorted(report_urls(text) - report_urls(old_report))
    if new_urls:
        return _out(args, cli.EXIT_FAIL, '새 링크 — a revision may only cite links already checked in this report: '
                    + ', '.join(new_urls[:5]), [], diff, False)
    if not args.apply:
        return _out(args, cli.EXIT_OK, f'verify gate matches; {len(changed)} issue(s) changed — preview only, '
                    'nothing changed (add --apply)', [], diff, False)

    files = {p: (p.read_bytes() if p.is_file() else None) for p in (layout.report_file(rd), layout.gates_dir(rd) / 'lint.json')}

    def restore():
        for p, data in files.items():
            if data is None:
                if p.is_file():
                    p.unlink()
            else:
                p.write_bytes(data)

    rev = history.snapshot(f'correct reviewed {manifest["run_id"]}', manifest['run_id'])
    schema.atomic_write(layout.report_file(rd), text)
    gates.record_stage(rd, manifest, 'render', cli.EXIT_OK, gates.file_hash(summary_path))
    gates.write_gate(rd, manifest, 'lint', cli.EXIT_FAIL if lint.findings else cli.EXIT_OK, lint.checked,
                     lint.findings, input_hash=gates.file_hash(layout.report_file(rd)))
    if lint.findings:
        restore()
        history.restore(rev)
        return _out(args, cli.EXIT_FAIL, 'the re-rendered report fails lint — restored', lint.findings, diff, False)
    target = layout.report_file(rd)
    data = acc.RunData(rd, manifest)
    for key, it, d in changed:
        it['corrections'].append({'rev': rev, 'request': args.request, 'diff': d, 'gate': 'verify PASS'})
        todo_issue = next(t for t in data.todo['issues'] if t['key'] == key)
        s = next(i for i in summary['issues'] if i['key'] == key)
        it['last_sentences'] = acc.last_sentences(s, acc._resolver(data, todo_issue, it), dates.now_iso(),
                                                  manifest['run_id'])
    schema.dump(layout.state_dir() / 'runs.json', runs_state, 'fiw.state.runs/1')
    schema.dump(layout.state_dir() / 'issues.json', issues_obj, 'fiw.state.issues/1')
    findings = state_cmd.check_state()
    if findings:
        restore()
        history.restore(rev)
        return _out(args, cli.EXIT_FAIL, 'state check failed after the revision — restored', findings, diff, False)
    return _out(args, cli.EXIT_OK, f'applied — {accum.report_ref(manifest["run_id"])} re-rendered, {len(changed)} issue correction(s), '
                f'state saved as snapshot {rev}; rebuild any period report that covers this raw (fiw.py report)', [], diff, True, target)


def _out(args, code, message, findings, diff, applied, report=None) -> int:
    if args.json:
        cli.print_json({'exit': code, 'applied': applied, 'message': message, 'diff': diff, 'findings': findings,
                        'report': str(report) if report else None})
    else:
        if diff:
            print(diff)
        gates.print_findings(findings)
        print(f'correct: {message}')
        print(f'correct: exit {code}')
    return code
