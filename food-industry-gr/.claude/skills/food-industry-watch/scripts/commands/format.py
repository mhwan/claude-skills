"""fiw.py format — change the report layout (the format key of the own config file) after a preview render + lint (S10, D59)."""
from __future__ import annotations

import copy
import json

from lib import accum, cli, gates, layout, schema

from . import lint as lint_cmd
from . import render as render_cmd
from ._render_model import Run

ISSUE_COLUMNS = ('쟁점', '기간', '내용', '{own_company}', '링크')
COLUMNS = {
    'three_lines': (), 'assembly': (),
    'gov': ISSUE_COLUMNS, 'assembly_issues': ISSUE_COLUMNS, 'industry': ISSUE_COLUMNS,
    'press': ('분류', '날짜', '보도자료', '링크'),
    'notices': ('부처', '구분', '법령·고시', '의견 접수기간', '남은 기간', '링크'),
    'assembly_bills': ('발의일', '법안', '대표발의', '소관위', '단계', '링크'),
    'assembly_schedule': ('날짜', '위원회', '회의', '링크'),
    'market': ('주제', '건수', '내용', '{own_company}', '링크'),
    'hygiene': ('구분', '건수', '내역', '링크'),
}
SECTION_KEYS = ('enabled', 'order', 'title', 'columns', 'max_rows')
ROW_CAPPED = ('gov', 'assembly_issues', 'industry')       # the renderer caps only these tables
TOP_KEYS = ('sections', 'sentence_limit', 'max_articles_per_issue', 'dormancy_weeks')
LIMIT_KEYS = ('min_sentences', 'max_sentences', 'min_chars', 'max_chars')
SAVED = 'config/food-industry-watch.json (format)'   # gr-structure: ignore (a label, not a path)


def register(sub):
    p = sub.add_parser('format', help='change the report layout after a preview')
    s = p.add_subparsers(dest='format_command', metavar='FORMAT_COMMAND', required=True)
    a = s.add_parser('apply', help='preview a format patch; with --apply save it')
    a.add_argument('patch', metavar='PATCH', help='a JSON file, or an inline JSON object')
    a.add_argument('--run', metavar='ID', help='run to preview (default: the latest published run)')
    a.add_argument('--apply', action='store_true', help='save the format after a passing preview')
    a.add_argument('--json', action='store_true', help='print the result JSON')
    a.set_defaults(func=main_apply)
    u = s.add_parser('undo', help='restore the previous format')
    u.add_argument('--apply', action='store_true', help='restore (default: print what would change)')
    u.add_argument('--json', action='store_true', help='print the result JSON')
    u.set_defaults(func=main_undo)


def _read_patch(arg):
    text = arg
    if not arg.lstrip().startswith('{'):
        try:
            with open(arg, encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            raise cli.UsageError(f'cannot read {arg}: {e}') from None
    try:
        patch = json.loads(text)
    except ValueError as e:
        raise cli.UsageError(f'PATCH is not JSON: {e}') from None
    if not isinstance(patch, dict) or not patch:
        raise cli.UsageError('PATCH must be a non-empty JSON object')
    return patch


def merged(fmt, patch, own_company):
    """fmt with patch applied; UsageError naming the first problem (addendum §E)."""
    out = copy.deepcopy(fmt)
    for key in patch:
        if key not in TOP_KEYS:
            raise cli.UsageError(f'unknown format key {key!r} (allowed: {", ".join(TOP_KEYS)})')
    by_id = {s['id']: s for s in out['sections']}
    for sp in patch.get('sections') or []:
        if not isinstance(sp, dict) or 'id' not in sp:
            raise cli.UsageError('every entry of sections needs an id')
        sec = by_id.get(sp['id'])
        if sec is None:
            raise cli.UsageError(f'unknown section id {sp["id"]!r} (known: {", ".join(by_id)})')
        for k, v in sp.items():
            if k == 'id':
                continue
            if k not in SECTION_KEYS:
                raise cli.UsageError(f'section {sp["id"]}: key {k!r} cannot be changed '
                                     f'(allowed: {", ".join(SECTION_KEYS)})')
            sec[k] = v
        allowed = COLUMNS.get(sec['id'])
        if allowed is not None and 'columns' in sp:
            cols = sec['columns']
            names = [c.replace(own_company, '{own_company}') if isinstance(c, str) else c for c in cols]
            bad = [c for c in names if c not in allowed]
            if bad:
                raise cli.UsageError(f'section {sec["id"]}: unknown column(s) {bad} (allowed: {", ".join(allowed)})')
            if allowed and not cols:
                raise cli.UsageError(f'section {sec["id"]}: a table needs at least one column')
            if allowed and '링크' not in cols:
                raise cli.UsageError(f'section {sec["id"]}: 링크 must stay — every row is linked to its source')
            if len(set(names)) != len(names):
                raise cli.UsageError(f'section {sec["id"]}: a column appears twice')
        if 'max_rows' in sp and sec['id'] not in ROW_CAPPED:
            raise cli.UsageError(f'section {sec["id"]}: max_rows is only used by {", ".join(ROW_CAPPED)} '
                                 '(rows beyond it are folded under 그 밖)')
        if 'max_rows' in sp and sp['max_rows'] is not None and (not isinstance(sp['max_rows'], int)
                                                                 or sp['max_rows'] < 1):
            raise cli.UsageError(f'section {sec["id"]}: max_rows must be null or ≥ 1')
        if 'title' in sp and (not isinstance(sp['title'], str) or not sp['title'].strip()):
            raise cli.UsageError(f'section {sec["id"]}: title must be non-empty text')
    if 'sentence_limit' in patch:
        lim = patch['sentence_limit']
        if not isinstance(lim, dict) or any(k not in LIMIT_KEYS for k in lim):
            raise cli.UsageError(f'sentence_limit takes {", ".join(LIMIT_KEYS)}')
        out['sentence_limit'].update(lim)
        sl = out['sentence_limit']
        if not all(isinstance(sl[k], int) and sl[k] >= 1 for k in LIMIT_KEYS) or \
                sl['min_sentences'] > sl['max_sentences'] or sl['min_chars'] > sl['max_chars']:
            raise cli.UsageError('sentence_limit: positive integers with min ≤ max')
    for k in ('max_articles_per_issue', 'dormancy_weeks'):
        if k in patch:
            if not isinstance(patch[k], int) or isinstance(patch[k], bool) or patch[k] < 1:
                raise cli.UsageError(f'{k} must be an integer ≥ 1')
            out[k] = patch[k]
    errors = schema.validate(out, 'fiw.format/1')
    if errors:
        raise cli.UsageError('patched format is invalid: ' + '; '.join(errors[:3]))
    return out


def changes(old, new, own_company) -> list:
    """Human-readable heading / column / value changes."""
    lines = []
    o = {s['id']: s for s in old['sections']}
    for s in sorted(new['sections'], key=lambda x: x['order']):
        p = o[s['id']]
        for k in SECTION_KEYS:
            if p[k] != s[k]:
                lines.append(f'{s["id"]}.{k}: {json.dumps(p[k], ensure_ascii=False)} → '
                             f'{json.dumps(s[k], ensure_ascii=False)}')
    for k in ('sentence_limit', 'max_articles_per_issue', 'dormancy_weeks'):
        if old[k] != new[k]:
            lines.append(f'{k}: {json.dumps(old[k], ensure_ascii=False)} → {json.dumps(new[k], ensure_ascii=False)}')
    return lines


def target_run(run_id):
    if run_id:
        return run_id
    latest, _ = accum.latest_published_run(accum.load_state('runs.json'))
    return latest


def preview(run_id, fmt):
    """(path, lint findings) of the run's work/report.preview.md rendered and linted with fmt."""
    rd, manifest = gates.load_run(run_id)
    text, _ = render_cmd.build(Run(rd, manifest, fmt))
    path = layout.preview_file(rd)
    schema.atomic_write(path, text)
    return path, lint_cmd.evaluate(rd, manifest, text, fmt).findings


def main_apply(args) -> int:
    old = layout.load_format()
    own = layout.load_watch()['own_company']
    new = merged(old, _read_patch(args.patch), own)
    diff = changes(old, new, own)
    notes = []
    if old['sentence_limit'] != new['sentence_limit'] or old['max_articles_per_issue'] != new['max_articles_per_issue']:
        notes.append('문장·기사 수 변경은 다음에 쓰는 요약부터 적용된다 — 지난 보고서의 본문은 정리돼 다시 쓸 수 없다')
    if old['dormancy_weeks'] != new['dormancy_weeks']:
        notes.append('휴면 기준 변경은 다음 발행 때 상태에 반영된다')
    run_id = target_run(args.run)
    findings, preview_path, code = [], None, cli.EXIT_OK
    if run_id is None:
        notes.append('발행된 실행이 없어 미리보기를 만들지 못했다')
        code = cli.EXIT_WARN
    else:
        preview_path, findings = preview(run_id, new)
        if findings:
            code = cli.EXIT_FAIL
    applied = False
    if args.apply and code != cli.EXIT_FAIL and diff:
        layout.save_format_history(layout.load_format_history() + [old])
        layout.save_format(new)
        applied = True
    result = {'exit': code, 'applied': applied, 'changes': diff, 'notes': notes, 'findings': findings,
              'preview': str(preview_path) if preview_path else None}
    if args.json:
        cli.print_json(result)
    else:
        for line in diff or ['no change']:
            print(f'format: {line}')
        for n in notes:
            print(f'format: note — {n}')
        gates.print_findings(findings)
        if preview_path:
            print(f'format: preview {preview_path} — lint {len(findings)} findings')
        print(f'format: saved {SAVED}' if applied else 'format: nothing saved'
              + ('' if args.apply else ' (add --apply)'))
        print(f'format: exit {code}')
    return code


def main_undo(args) -> int:
    versions = layout.load_format_history()
    if not versions:
        raise cli.UsageError('no saved format to restore (' + layout.format_history_path().name + ' is empty)')
    old = versions[-1]
    schema.check(old, 'fiw.format/1')
    cur = layout.load_format()
    own = layout.load_watch()['own_company']
    diff = changes(cur, old, own)
    if args.apply:
        layout.save_format(old)
        layout.save_format_history(versions[:-1])
    label = f'the previous format ({len(versions)} saved)'
    if args.json:
        cli.print_json({'exit': cli.EXIT_OK, 'applied': args.apply, 'changes': diff, 'from': len(versions)})
    else:
        for line in diff or ['no change']:
            print(f'format undo: {line}')
        print(f'format undo: {"restored" if args.apply else "would restore"} {label}'
              + ('' if args.apply else ' (add --apply)'))
    return cli.EXIT_OK
