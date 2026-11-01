"""fiw.py lint — format gate R over the run's work/report.md (M5, addendum §6).

Expected content is re-derived from the run files by _lint_expect (render is never imported);
the report is read back by _lint_parse, reference links are resolved to inline form and compared.
"""
from __future__ import annotations

import re
from collections import Counter

from lib import cli, dates, gates, layout

from . import _lint_parse as mdp
from ._lint_expect import CREDIT, Expected

FAMILIES = ('n', 'm', 't', 'k', 'l', 'b', 's', 'fl', 'f')
COMPUTED_COLUMNS = ('기간', '남은 기간', '건수', '내역', '의견 접수기간', '날짜', '발의일')
MARKERS = (('🧭 ', '🧭 edit marker'), ('(미확인)', '(미확인) marker'), ('✅ 직접', '✅ 직접 marker'),
           ('🆕', '🆕 badge'), ('▶ ', '▶ badge'))
TAG = re.compile(r'`([^`]+)`')
EXCLUDED_PREFIXES = ('> 링크 없어 제외', '> 근거 본문 미수신', '> 뉴스(네이버 대체판)')
DATED_ITEM = re.compile(r'^\s*- (?:\d{4}-)?\d{2}-\d{2} ')          # "- 10-08 title": the title's day is that date
DATED_TITLE = re.compile(r'(?:\d{4}-)?\d{2}-\d{2} 「[^」]*$')        # "10-08 title…" in a flow line
DAY_CELL = re.compile(r'^\**\d{1,3}일\**$')                        # remaining-days cell
SENTENCE_END = re.compile(r'\.(?=\s|$)')
BASIS_END = re.compile(r'\((?:\d{4}-)?\d{2}-\d{2}(?:·(?:\d{4}-)?\d{2}-\d{2})* 보도\)\.$')


def register(sub):
    p = sub.add_parser('lint', help='format gate R over the run\'s work/report.md')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def short(s, n=90):
    s = ' '.join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + '…'


def note_rule(line):
    for prefix, name in (('> ⏰', '⏰ line'), ('> ⚠', '⚠ line'), ('> 키 없음', '키 없음 line'),
                         ('> 소급 실행', '소급 실행 line'), ('> 지난 보고서', '지난 보고서 line'),
                         ('> 🧭', '🧭 note'), ('> 링크 없어 제외', '링크 없어 제외 line'),
                         ('> 근거 본문 미수신', '근거 본문 미수신 line'), ('> 미수집', '미수집 line'),
                         ('> 뉴스(네이버 대체판)', '네이버 대체판 line')):
        if line.startswith(prefix):
            return name
    return 'note line'


def note_key(line):
    rule = note_rule(line)
    return line.split(':')[0] if rule == '⚠ line' else rule


class Lint:
    def __init__(self):
        self.findings = []
        self.checked = 0

    def check(self, ok, where, kind, detail):
        self.checked += 1
        if not ok:
            self.findings.append({'where': where, 'kind': kind, 'detail': detail})
        return ok

    # ---------------------------------------------------------------- text comparison
    @staticmethod
    def why(exp, got):
        """(kind or None, detail) naming what differs between two cell/line texts."""
        msgs, kind = [], None
        et, gt = TAG.findall(exp), TAG.findall(got)
        if et != gt:
            msgs.append(f'tags expected {" ".join(et) or "none"}, found {" ".join(gt) or "none"}')
            kind = '계산'
        for mark, name in MARKERS:
            e, g = exp.count(mark), got.count(mark)
            if e != g:
                msgs.append(f'{name}: expected {e}, found {g}')
                kind = kind or '표시'
        if not msgs:
            msgs.append(f'expected {short(exp)!r}, found {short(got)!r}')
        return kind, '; '.join(msgs)

    def same(self, exp, got, where, default_kind):
        if exp == got:
            self.checked += 1
            return True
        kind, detail = self.why(exp, got)
        self.check(False, where, kind or default_kind, detail)
        return False

    def notes(self, exp, got, where):
        missing = list((Counter(exp) - Counter(got)).elements())
        extra = list((Counter(got) - Counter(exp)).elements())
        self.checked += max(1, len(exp))
        for line in list(missing):
            pair = next((x for x in extra if note_key(x) == note_key(line)), None)
            if pair is not None:
                missing.remove(line)
                extra.remove(pair)
                kind, detail = self.why(line, pair)
                self.check(False, where, kind or '표시', f'{note_rule(line)} differs: {detail}')
        for line in missing:
            self.check(False, where, '표시', f'{note_rule(line)} missing: {short(line)}')
        for line in extra:
            self.check(False, where, '표시', f'{note_rule(line)} present but not expected: {short(line)}')

    # ---------------------------------------------------------------- blocks
    def table(self, where, sid, exp, act, resolve):
        _, cols, rows = exp
        header, sep_ok, arows = act
        self.check(header == cols, f'{where} header', '표', f'columns expected {cols}, found {header}')
        self.check(sep_ok, f'{where} header', '표', 'separator row missing or malformed')
        index = {c: i for i, c in enumerate(header)}
        for i, erow in enumerate(rows):
            if i >= len(arows):
                self.check(False, f'{where} row {i + 1}', '표', 'row missing')
                continue
            cells = [resolve(c) for c in arows[i][1]]
            for j, col in enumerate(cols):
                k = index.get(col)
                if k is None or k >= len(cells):
                    continue
                kind = '계산' if col in COMPUTED_COLUMNS else ('링크' if col == '링크' else '표')
                self.same(erow[j], cells[k], f'{where} row {i + 1} {col}', kind)
        for i in range(len(rows), len(arows)):
            self.check(False, f'{where} row {i + 1}', '표', f'unexpected row: {short(arows[i][0])}')

    def table_shape(self, where, sid, act):
        header, _, arows = act
        for i, (raw, cells) in enumerate(arows):
            w = f'{where} row {i + 1}'
            self.check(len(cells) == len(header), w, '표', f'{len(cells)} cells, header has {len(header)}')
            for j, c in enumerate(cells):
                col = header[j] if j < len(header) else f'#{j + 1}'
                self.check(c != '', w, '표', f'empty cell in column {col}')
            if sid != 'hygiene':
                self.check('][' in raw, w, '링크', 'row has no reference link (D10)')

    def details(self, where, exp, act):
        if exp is None and act is None:
            return
        if act is None:
            self.check(False, where, '표시', f'그 밖 block missing — {len(exp)} item(s) belong there')
            return
        declared, items = act
        if exp is None:
            self.check(False, where, '표시', f'그 밖 block present but nothing belongs there ({len(items)} lines)')
            return
        self.check(declared == len(exp), where, '계산', f'그 밖 count expected {len(exp)}, found {declared}')
        self.check(declared == len(items), where, '표', f'그 밖 says {declared}건 but lists {len(items)} lines')
        for i in range(max(len(exp), len(items))):
            if i >= len(items):
                self.check(False, where, '표', f'그 밖 line missing: {short(exp[i])}')
            elif i >= len(exp):
                self.check(False, where, '표', f'그 밖 line unexpected: {short(items[i])}')
            else:
                self.same(exp[i], items[i], f'{where} 그 밖 line {i + 1}', '표')

    def blocks(self, where, sid, exp_blocks, act_blocks, resolve):
        def of(kind, blocks):
            return [b for b in blocks if b[0] == kind]
        et, at = of('table', exp_blocks), of('table', act_blocks)
        for i in range(max(len(et), len(at))):
            w = f'{where} table' + (f' {i + 1}' if max(len(et), len(at)) > 1 else '')
            if i >= len(at):
                self.check(False, w, '표', f'table missing ({len(et[i][2])} rows expected)')
            elif i >= len(et):
                self.check(False, w, '표', 'unexpected table')
            else:
                self.table(w, sid, et[i], mdp.table(at[i][1]), resolve)
        for b in at:
            self.table_shape(f'{where} table', sid, mdp.table(b[1]))
        ep = [b[1] for b in of('para', exp_blocks)]
        ap = [resolve(' '.join(b[1])) for b in of('para', act_blocks)]
        for i in range(max(len(ep), len(ap))):
            if i >= len(ap):
                self.check(False, where, '표', f'line missing: {short(ep[i])}')
            elif i >= len(ep):
                self.check(False, where, '표', f'unexpected line: {short(ap[i])}')
            else:
                self.same(ep[i], ap[i], f'{where} line', '표')
        el = [ln for b in of('list', exp_blocks) for ln in b[1]]
        al = [resolve(ln) for b in of('list', act_blocks) for ln in b[1]]
        for i in range(max(len(el), len(al))):
            if i >= len(al):
                self.check(False, where, '표', f'bullet missing: {short(el[i])}')
            elif i >= len(el):
                self.check(False, where, '표', f'unexpected bullet: {short(al[i])}')
            else:
                self.same(el[i], al[i], f'{where} bullet {i + 1}', '표')
        self.notes([ln for b in of('notes', exp_blocks) for ln in b[1]],
                   [resolve(ln) for b in of('notes', act_blocks) for ln in b[1]], where)
        ed = [b[1] for b in of('details', exp_blocks)]
        ad = [mdp.details([resolve(x) for x in b[1]]) for b in of('details', act_blocks)]
        for i in range(max(len(ed), len(ad), 1)):
            self.details(where, ed[i] if i < len(ed) else None, ad[i] if i < len(ad) else None)


def structure(blocks, section_heads):
    """{'top': blocks, 'sections': {sid: [(sub, blocks)]}, 'headings': [...], 'tail': [...], 'rules': [...]}"""
    top, sections, headings, tail, rules, dormant = [], {}, [], [], [], []
    cur = None
    for kind, lines in blocks:
        if kind == 'defs':
            continue
        if kind == 'details' and mdp.DORMANT_HEAD.match(lines[0].strip()):
            dormant.append(lines)
            continue
        if kind == 'heading':
            line = lines[0]
            headings.append(line)
            sid = section_heads.get(line)
            if sid is not None and sid not in sections:
                cur = sid
                sections[sid] = [(None, [])]
            elif cur is not None and line.startswith('### '):
                sections[cur].append((line, []))
            continue
        if kind == 'rule':
            rules.append(lines)
            continue
        if kind == 'notes':
            keep = [ln for ln in lines if not ln.startswith(EXCLUDED_PREFIXES)]
            tail += [ln for ln in lines if ln.startswith(EXCLUDED_PREFIXES)]
            if not keep:
                continue
            lines = keep
        if cur is None:
            top.append((kind, lines))
        else:
            sections[cur][-1][1].append((kind, lines))
    return {'top': top, 'sections': sections, 'headings': headings, 'tail': tail, 'rules': rules,
            'dormant': dormant}


def check_headings(lint, exp, actual):
    expected = [exp['title']]
    for s in exp['sections']:
        expected.append(s['heading'])
        expected += [sub for sub, _ in s['body'] if sub]
    missing = Counter(expected) - Counter(actual)
    extra = Counter(actual) - Counter(expected)
    lint.checked += len(expected)
    for h in missing.elements():
        lint.check(False, 'headings', '섹션', f'missing heading: {h}')
    for h in extra.elements():
        lint.check(False, 'headings', '섹션', f'unexpected heading: {h}')
    ids = {s['heading']: s['id'] for s in exp['sections']}
    want = [s['id'] for s in exp['sections'] if s['heading'] in actual]
    found = [ids[h] for h in actual if h in ids]
    if lint.check(found == want, 'headings', '섹션',
                  f'sections out of order: expected {", ".join(want)}; found {", ".join(found)}'):
        common = [h for h in actual if h in expected]
        lint.check(common == [h for h in expected if h in actual], 'headings', '섹션',
                   'sub-headings (### …) out of order')


def check_dormant(lint, exp, act):
    """The folded 💤 list: present exactly when dormant issues exist, with their lines."""
    if exp is None:
        lint.check(not act, 'dormant', '표시', f'💤 block present but no issue is dormant ({len(act)} block(s))')
        return
    if not lint.check(len(act) == 1, 'dormant', '표시', f'💤 block: expected 1, found {len(act)}'):
        return
    declared, items = mdp.details(act[0], mdp.DORMANT_HEAD)
    n, lines = exp
    lint.check(declared == n, 'dormant', '계산', f'💤 count expected {n}, found {declared}')
    for i in range(max(len(lines), len(items))):
        if i >= len(items):
            lint.check(False, 'dormant', '표', f'💤 line missing: {short(lines[i])}')
        elif i >= len(lines):
            lint.check(False, 'dormant', '표', f'💤 line unexpected: {short(items[i])}')
        else:
            lint.same(lines[i], items[i], f'dormant line {i + 1}', '표')


def check_refs(lint, text, blocks):
    defs = mdp.definitions(blocks)
    def_lines = {f'[{k}]: {u}' for k, u in defs}
    body = '\n'.join(ln for ln in text.split('\n') if ln.strip() not in def_lines)
    uses = [m.group(2) for m in mdp.REF_USE.finditer(body)]
    keys = [k for k, _ in defs]
    defined = dict(defs)
    for k, n in Counter(keys).items():
        lint.check(n == 1, f'ref {k}', '링크', f'defined {n} times')
    for k in sorted(set(uses)):
        lint.check(k in defined, f'ref {k}', '링크', 'used but not defined')
    for k in keys:
        lint.check(k in set(uses), f'ref {k}', '링크', 'defined but never used')
    by_url = {}
    for k, u in defs:
        by_url.setdefault(u, []).append(k)
    for u, ks in by_url.items():
        lint.check(len(set(ks)) == 1, f'ref {"/".join(ks)}', '링크', f'same URL under several keys: {u}')
    first = []
    for k in uses:
        if k not in first:
            first.append(k)
    per = {}
    for k in first:
        m = re.match(r'^([A-Za-z]+)(\d+)$', k)
        fam, n = m.group(1), int(m.group(2))
        lint.check(fam in FAMILIES, f'ref {k}', '링크', f'unknown key family {fam!r}')
        per.setdefault(fam, []).append(n)
    for fam, nums in per.items():
        lint.check(nums == list(range(1, len(nums) + 1)), f'refs {fam}*', '링크',
                   f'keys not numbered in order of first use: {nums[:12]}')
    lint.check([k for k in keys if k in set(first)] == [k for k in first if k in defined],
               'refs', '링크', 'definitions are not in order of first use')
    return defined


def check_month_days(lint, text):
    """A period report: a day written without its month ("the 8th of last month") must have its date
    basis — its sentence ends with the dated-report tag, or it is in an article title shown after its date."""
    for n, line in enumerate(text.split('\n'), 1):
        if DATED_ITEM.match(line):
            continue
        for cell in line.split('|'):
            if DAY_CELL.match(cell.strip()):
                continue
            for m in dates.MONTHLESS_DAY.finditer(cell):
                if DATED_TITLE.search(cell[:m.start()]):
                    continue
                end = SENTENCE_END.search(cell, m.end())
                sentence = cell[m.start():end.end()] if end else cell[m.start():]
                lint.check(bool(BASIS_END.search(sentence)), f'line {n}', '날짜',
                           f'day without month and without a date basis: {short(cell[max(0, m.start() - 30):m.end() + 20])}')


def evaluate(rd, manifest, text, fmt=None) -> Lint:
    """Gate R over a report text (fmt: a format object other than config/format.json — format preview)."""
    exp = Expected(rd, manifest, fmt).build()
    lint = Lint()
    blocks = mdp.raw_blocks(text)
    defined = dict(mdp.definitions(blocks))

    def resolve(s):
        return mdp.REF_USE.sub(lambda m: f'[{m.group(1)}]({defined[m.group(2)]})' if m.group(2) in defined
                               else m.group(0), s)

    heads = {s['heading']: s['id'] for s in exp['sections']}
    act = structure(blocks, heads)
    check_headings(lint, exp, act['headings'])
    lint.check(act['headings'][:1] == [exp['title']], 'title', '섹션', f'first heading must be {exp["title"]!r}')

    top_paras = [' '.join(b[1]) for b in act['top'] if b[0] == 'para']
    if lint.check(bool(top_paras), 'header', '계산', 'header numbers line missing'):
        got = top_paras[0]
        if got != exp['header']:
            parts_e = re.split(r'\s*(?:·|→)\s*', exp['header'])
            parts_g = re.split(r'\s*(?:·|→)\s*', got)
            diff = [f'{e!r} → {g!r}' for e, g in zip(parts_e, parts_g) if e != g] or [short(got)]
            lint.check(False, 'header', '계산', 'header numbers differ: ' + '; '.join(diff))
        else:
            lint.checked += 1
    for b in act['top']:
        if b[0] not in ('para', 'notes'):
            lint.check(False, 'top', '표', f'unexpected {b[0]} before the first section')
    lint.notes(exp['notes'], [resolve(ln) for b in act['top'] if b[0] == 'notes' for ln in b[1]], 'notes')

    for sec in exp['sections']:
        subs = act['sections'].get(sec['id'])
        if subs is None:
            continue
        got = {}
        for sub, bl in subs:
            got.setdefault(sub, bl)
        for sub, bl in sec['body']:
            if sub in got:
                where = sec['id'] + (f'/{sub[4:]}' if sub else '')
                lint.blocks(where, sec['id'], bl, got[sub], resolve)

    check_dormant(lint, exp['dormant'], act['dormant'])
    lint.notes(exp['tail'], [resolve(x) for x in act['tail']], 'excluded')
    want = exp.get('credit', CREDIT)
    credit = any(ln == want for r in act['rules'] for ln in r) or any(
        ln == want for kind, lines in blocks if kind == 'para' for ln in lines)
    lint.check(credit and any(r[0].strip() == '---' for r in act['rules']), 'footer', '표시',
               'source credit line (after ---) missing')
    if manifest.get('period_report'):
        check_month_days(lint, text)
    check_refs(lint, text, blocks)
    return lint


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    path = layout.report_file(rd)
    if not path.is_file():
        raise cli.UsageError('missing report.md — run render first')
    with open(path, encoding='utf-8', newline='') as f:
        text = f.read()
    lint = evaluate(rd, manifest, text)
    findings = lint.findings
    code = cli.EXIT_FAIL if findings else cli.EXIT_OK
    classified = dict(Counter(f['kind'] for f in findings))
    gate = gates.write_gate(rd, manifest, 'lint', code, lint.checked, findings, classified=classified,
                            input_hash=gates.file_hash(path))
    if args.json:
        cli.print_json(gate)
    else:
        gates.print_findings(findings)
        print(f'lint: {lint.checked} checks, {len(findings)} findings, exit {code}')
    return code
