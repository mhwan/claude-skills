"""fiw.py parts — the summary written in parts of about 20,000 body chars (M8 0-C k, l).

  split  work/todo.json → work/parts/NN/{todo.json, bodies.md} + parts.json + index.md, in todo order
         (issues, then market topics); an issue or topic larger than --max-chars is a part of its own.
  check  --part NN: facts.json against the part's bodies (F1–F7), then the evidence checks of the part's
         summary.json entries and E15; writes parts/NN/check.json (fiw.gate/1).
  merge  --head FILE: every part checked (exit 0, unchanged since) → work/summary.json in todo order.

  F1 count / last_ref disagree with the part      F5 quote shorter than QUOTE_MIN chars
  F2 body missing, repeated or not of the part    F6 summary entries ≠ the part, or cites a body marked unused
  F3 not exactly one of facts (1–3) / unused (a reason)    F7 a number of a fact that is not in its quote
  F4 quote not in its own body (a mixed-up tag with <ref> when it is another body's, else a no-evidence tag)

No output line carries body text: bodies.md is read with Read, in pieces.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from lib import cli, evidence, gates, layout, runview, schema

from . import _verify_checks as vc

MAX_CHARS = 20000
LINE_MAX = 500
QUOTE_MIN = 12
MAX_FACTS = 3
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


def register(sub):
    p = sub.add_parser('parts', help='split the todo into parts, check a part, merge the parts into the summary')
    p.add_argument('action', choices=('split', 'check', 'merge'))
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--max-chars', type=int, default=MAX_CHARS, metavar='N', help='split: body chars per part')
    p.add_argument('--part', metavar='NN', help='check: the part')
    p.add_argument('--head', metavar='FILE', help='merge: fiw.summary_head/1 (relative to the run folder)')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    if args.action == 'split':
        return split(args, rd)
    if args.action == 'check':
        if not args.part:
            raise cli.UsageError('parts check needs --part NN')
        return check(args, rd, manifest)
    if not args.head:
        raise cli.UsageError('parts merge needs --head FILE')
    return merge(args, rd)


# ============================================================================= split

def _units(todo):
    """(kind, name, entry) in todo order: issues, then market topics."""
    return [('issue', i['key'], i) for i in todo['issues']] + [('market', m['topic'], m) for m in todo['market']]


def _chars(entry) -> int:
    return sum(len(a['body']) for a in entry['articles'])


def _lines(body) -> list:
    """One sentence per line; a piece over LINE_MAX is cut at its last space before LINE_MAX (else hard)."""
    out = []
    for para in body.splitlines():
        for piece in _SENTENCE_END.split(para.strip()):
            while len(piece) > LINE_MAX:
                cut = piece.rfind(' ', 0, LINE_MAX + 1)
                cut = cut if cut > 0 else LINE_MAX
                out.append(piece[:cut].rstrip())
                piece = piece[cut:].lstrip()
            if piece:
                out.append(piece)
    return out


def _bodies_md(units) -> str:
    out = []
    for kind, name, entry in units:
        out.append(f'## {"쟁점" if kind == "issue" else "시장"} {name}\n')
        for a in entry['articles']:
            out.append(f'### {a["ref"]} — {a["title"]} ({a["outlet"]}, {a["date"]})\n')
            out.extend(_lines(a['body']))
            out.append('')
    return '\n'.join(out) + '\n'


def split(args, rd) -> int:
    if args.max_chars < 1:
        raise cli.UsageError('--max-chars must be at least 1')
    todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
    groups, cur, cur_chars = [], [], 0
    for u in _units(todo):
        n = _chars(u[2])
        if cur and cur_chars + n > args.max_chars:
            groups.append(cur)
            cur, cur_chars = [], 0
        cur.append(u)
        cur_chars += n
    if cur:
        groups.append(cur)

    root = rd / 'work' / 'parts'
    if root.exists():
        shutil.rmtree(root)            # a new split makes every earlier part, its facts and its check stale
    root.mkdir(parents=True)
    index = []
    for n, units in enumerate(groups, 1):
        part = f'{n:02d}'
        d = root / part
        d.mkdir()
        refs = [a['ref'] for _, _, e in units for a in e['articles']]
        ptodo = {'schema': 'fiw.todo/1',
                 'issues': [e for k, _, e in units if k == 'issue'],
                 'excluded_no_link': [],
                 'market': [e for k, _, e in units if k == 'market'],
                 'lwf_mentions': {r: v for r, v in todo['lwf_mentions'].items() if r in refs}}
        schema.dump(d / 'todo.json', ptodo, 'fiw.todo/1')
        schema.atomic_write(d / 'bodies.md', _bodies_md(units))
        index.append({'part': part, 'issues': [nm for k, nm, _ in units if k == 'issue'],
                      'market': [nm for k, nm, _ in units if k == 'market'], 'refs': refs,
                      'chars': sum(_chars(e) for _, _, e in units)})
    obj = {'schema': 'fiw.parts/1', 'todo_hash': gates.file_hash(rd / 'work' / 'todo.json'),
           'max_chars': args.max_chars, 'parts': index}
    schema.dump(root / 'parts.json', obj, 'fiw.parts/1')
    rows = ['| part | issues | market | bodies | chars |', '|---|---|---|---|---|']
    rows += [f'| {p["part"]} | {", ".join(p["issues"]) or "—"} | {", ".join(p["market"]) or "—"} | '
             f'{len(p["refs"])} | {p["chars"]} |' for p in index]
    # The summary head's order (writing-rules § Summary head) without opening todo.json, which holds bodies.
    rows += ['', '| issue | coverage | refs |', '|---|---|---|']
    no_cov = {'outlets': 0, 'articles': 0}          # a todo written before coverage existed
    ranked = sorted(todo['issues'], key=lambda e: (-e.get('coverage', no_cov)['outlets'],
                                                   -e.get('coverage', no_cov)['articles']))
    rows += [f'| {e["key"]} | '
             + (f'{e["coverage"]["outlets"]}사 {e["coverage"]["articles"]}건' if 'coverage' in e else '—')
             + f' | {" ".join(a["ref"] for a in e["articles"])} |' for e in ranked]
    schema.atomic_write(root / 'index.md', '\n'.join(rows) + '\n')
    if args.json:
        cli.print_json(obj)
    else:
        print(f'parts split: {len(index)} parts of up to {args.max_chars} body chars — {root / "index.md"}')
    return cli.EXIT_OK


# ============================================================================= check

def _index(rd):
    path = rd / 'work' / 'parts' / 'parts.json'
    if not path.is_file():
        raise cli.UsageError('missing parts.json — run parts split first')
    return schema.load(path, 'fiw.parts/1')


def _part_files(rd, part):
    d = rd / 'work' / 'parts' / part
    return d / 'todo.json', d / 'facts.json', d / 'summary.json'


def _input_hash(rd, part, index) -> str:
    h = hashlib.sha256(index['todo_hash'].encode())
    for path in _part_files(rd, part):
        h.update(b'\0' + (gates.file_hash(path) or '-').encode())
    return h.hexdigest()


def _load(path, contract):
    if not path.is_file():
        raise cli.UsageError(f'missing {path.parent.name}/{path.name} — write it first')  # gr-compat: ignore=pathjoin (a message text)
    return schema.load(path, contract)


def _summary_refs(entry) -> set:
    """Every ref a part summary entry cites."""
    refs = set()

    def sent(s):
        refs.update(s['refs'])
        refs.update(e['ref'] for e in s.get('evidence') or [])
    if 'title' in entry:
        refs.update(entry['title']['refs'])
    for f in entry.get('actors') or []:
        refs.update(f['refs'])
    for s in entry['sentences']:
        sent(s)
    for node in entry.get('flow') or []:
        refs.add(node['ref'])
    lwf = entry.get('lwf') or {}
    for s in lwf.get('sentences') or []:
        sent(s)
    if lwf.get('ref'):
        refs.add(lwf['ref'])
    return refs


class PartCheck:
    def __init__(self, entry, ptodo, facts, psum):
        self.entry, self.ptodo, self.facts, self.psum = entry, ptodo, facts, psum
        self.refs = entry['refs']
        self.text = {a['ref']: evidence.norm_space(a['title'] + '\n' + a['body'])
                     for _, _, e in _units(ptodo) for a in e['articles']}
        self.findings = []
        self.checked = 0

    def find(self, where, kind, detail):
        f = {'where': where, 'kind': kind, 'detail': str(detail)}
        if f not in self.findings:
            self.findings.append(f)

    def run(self):
        arts = self.facts['articles']
        self.checked += 2
        if self.facts['count'] != len(self.refs):
            self.find('facts/count', 'F1', f'count {self.facts["count"]}, the part has {len(self.refs)} bodies')
        if self.facts['last_ref'] != self.refs[-1]:
            self.find('facts/last_ref', 'F1', f'last_ref {self.facts["last_ref"]}, the part ends at {self.refs[-1]}')
        seen = set()
        for a in arts:
            ref = a['ref']
            if ref not in self.text:
                self.find(ref, 'F2', 'not a body of this part')
                continue
            if ref in seen:
                self.find(ref, 'F2', 'repeated')
                continue
            seen.add(ref)
            self.article(a)
        for ref in self.refs:
            if ref not in seen:
                self.find(ref, 'F2', 'no facts or unused entry for this body')
        self.summary(arts)

    def article(self, a):
        ref = a['ref']
        self.checked += 1
        has_facts, has_unused = 'facts' in a, 'unused' in a
        if has_facts == has_unused:
            self.find(ref, 'F3', 'give either facts or unused, not both or neither')
        if has_unused and not a['unused'].strip():
            self.find(ref, 'F3', 'unused needs a reason')
        facts = a.get('facts') or []
        if has_facts and not 1 <= len(facts) <= MAX_FACTS:
            self.find(ref, 'F3', f'{len(facts)} facts (allowed 1–{MAX_FACTS})')
        for j, f in enumerate(facts):
            self.checked += 1
            quote = evidence.norm_space(f['quote'])
            if len(quote) < QUOTE_MIN:
                self.find(ref, 'F5', f'facts[{j}]: quote of {len(quote)} chars (min {QUOTE_MIN})')
            if quote and quote not in self.text[ref]:
                other = [r for r in self.refs if r != ref and quote in self.text.get(r, '')]
                self.find(ref, 'F4', f'facts[{j}]: 섞임 — the quote is in {", ".join(other)}, not in this body'
                          if other else f'facts[{j}]: 근거 없음 — the quote is in no body of this part')
            missing = evidence.missing_numbers(f['text'], [f['quote']])
            if missing:
                self.find(ref, 'F7', f'facts[{j}]: numbers not in its quote: {", ".join(missing)}')

    def summary(self, arts):
        unused = {a['ref'] for a in arts if 'unused' in a and 'facts' not in a}
        for label, want, got in (('issue', self.entry['issues'], [i['key'] for i in self.psum['issues']]),
                                 ('market', self.entry['market'], [m['topic'] for m in self.psum['market']])):
            where = (lambda k: k) if label == 'issue' else (lambda t: f'market:{t}')
            for k in want:
                if k not in got:
                    self.find(where(k), 'F6', f'part {label} has no summary entry')
            for k in set(got):
                if k not in want:
                    self.find(where(k), 'F6', f'not a {label} of this part')
                if got.count(k) > 1:
                    self.find(where(k), 'F6', 'duplicate summary entry')
        for e in self.psum['issues'] + self.psum['market']:
            where = e['key'] if 'key' in e else f'market:{e["topic"]}'
            for ref in sorted(_summary_refs(e) & unused):
                self.find(where, 'F6', f'cites {ref}, which facts.json marks unused')


def check(args, rd, manifest) -> int:
    index = _index(rd)
    entry = next((p for p in index['parts'] if p['part'] == args.part), None)
    if entry is None:
        raise cli.UsageError(f'no part {args.part} — parts: {", ".join(p["part"] for p in index["parts"])}')
    todo_path, facts_path, sum_path = _part_files(rd, args.part)
    ptodo = _load(todo_path, 'fiw.todo/1')
    facts = _load(facts_path, 'fiw.facts/1')
    psum = _load(sum_path, 'fiw.part_summary/1')

    pc = PartCheck(entry, ptodo, facts, psum)
    if gates.file_hash(rd / 'work' / 'todo.json') != index['todo_hash']:
        pc.find('parts', 'F0', 'work/todo.json changed since parts split — split again')
    pc.run()

    watch = layout.load_watch()
    fmt = layout.load_format()
    envelopes = {s: runview.envelope(rd, s) for s in ('bigkinds', 'korea_kr') + runview.NOTICE_SOURCES}
    prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1', required=False)
    relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1', required=False)
    part_summary = {'three_lines': [], 'issues': psum['issues'], 'notices': [], 'market': psum['market']}
    ver = vc.Verifier(ptodo, part_summary, watch, fmt, envelopes, prefilter, relevance,
                      period_to=manifest['period']['to'])
    for i in psum['issues']:
        if i['key'] in entry['issues']:
            ver.run_entry('issue', i['key'])
    for m in psum['market']:
        if m['topic'] in entry['market']:
            ver.run_entry('market', m['topic'])
    ver.e15()

    findings = pc.findings + ver.findings
    code = cli.EXIT_FAIL if findings else cli.EXIT_OK
    obj = {'schema': 'fiw.gate/1', 'gate': f'parts/{args.part}', 'exit': code,
           'checked': pc.checked + ver.checked, 'findings': findings, 'classified': {}, 'skipped': [],
           'input_hash': _input_hash(rd, args.part, index)}
    schema.dump(todo_path.parent / 'check.json', obj, 'fiw.gate/1')
    if args.json:
        cli.print_json(obj)
    else:
        gates.print_findings(findings)
        print(f'parts check {args.part}: {len(entry["refs"])} bodies, {obj["checked"]} fields, '
              f'{len(findings)} findings, exit {code}')
    return code


# ============================================================================= merge

def merge(args, rd) -> int:
    index = _index(rd)
    head_path = Path(args.head)
    if not head_path.is_absolute():
        head_path = rd / head_path
    head = _load(head_path, 'fiw.summary_head/1')
    problems = []
    if gates.file_hash(rd / 'work' / 'todo.json') != index['todo_hash']:
        problems.append('work/todo.json changed since parts split — split again')
    sums = {}
    for p in index['parts']:
        part = p['part']
        gate = rd / 'work' / 'parts' / part / 'check.json'
        if not gate.is_file():
            problems.append(f'part {part}: not checked')
            continue
        obj = schema.load(gate, 'fiw.gate/1')
        if obj['exit'] != cli.EXIT_OK:
            problems.append(f'part {part}: check exit {obj["exit"]}')
        elif obj['input_hash'] != _input_hash(rd, part, index):
            problems.append(f'part {part}: changed since its check — check it again')
        else:
            sums[part] = _load(rd / 'work' / 'parts' / part / 'summary.json', 'fiw.part_summary/1')
    if problems:
        for line in problems:
            print(f'parts merge: {line}')
        print('parts merge: not merged, exit 1')
        return cli.EXIT_FAIL

    todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
    issues = {i['key']: i for s in sums.values() for i in s['issues']}
    market = {m['topic']: m for s in sums.values() for m in s['market']}
    summary = {'schema': 'fiw.summary/1', 'three_lines': head['three_lines'],
               'issues': [issues[i['key']] for i in todo['issues']],
               'notices': head['notices'],
               'market': [market[m['topic']] for m in todo['market']]}
    out = rd / 'work' / 'summary.json'
    schema.dump(out, summary, 'fiw.summary/1')
    if args.json:
        cli.print_json({'exit': 0, 'summary': str(out), 'parts': len(sums)})
    else:
        print(f'parts merge: {out} from {len(sums)} parts — next: fiw.py pipeline --run {args.run} --from summary')
    return cli.EXIT_OK
