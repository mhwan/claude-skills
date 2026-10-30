"""fiw.py import-legacy — copy the data folder of the old layout (~/.claude/food-industry-watch/) into the output root.

Copy only: the old folder is never changed or deleted, and nothing in the output root is overwritten (an
unchanged shipped default is the one exception). Old to new:
  reports/*.md           -> reports/food-industry-watch/
  state/*.json           -> state/food-industry-watch/   (runs.json: the `report` paths are rewritten)
  history/N/ + revs.json -> state/food-industry-watch/snapshots.json (the exact text of each snapshot file)
  raw/<run id>/          -> runs/food-industry-watch/<run id>/ (run.json, raw/collect/, work/)
  config/watch.json      -> config/watch.json (shared keys) + config/food-industry-watch.json (own keys + format)
Keys the old watch.json had for collectors that no longer exist (assembly, korea_kr, lawmaking) are dropped
and listed. Without --apply the plan is printed only; after --apply every written file is read back and compared.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from lib import accum, cli, layout, schema

DEFAULT_SOURCE = Path('~/.claude/food-industry-watch')   # gr-structure: ignore (the old data folder, only read)  # gr-compat: ignore=tilde
# Folder names inside the old data folder (the source side of the copy); the new side comes from layout.py.
OLD_REPORTS, OLD_STATE, OLD_HISTORY, OLD_RAW, OLD_CONFIG = 'reports', 'state', 'history', 'raw', 'config'  # gr-structure: ignore
SHARED_KEYS = ('own_company', 'companies', 'associations', 'agencies', 'committees', 'food_related')
OWN_KEYS = ('provisional', 'bigkinds', 'stage1')
DROPPED_KEYS = ('assembly', 'korea_kr', 'lawmaking')
_OLD_REPORT = re.compile(r'("report": ")raw/(\d{4}-\d{2}-\d{2}-[1-9]\d*)/report\.md(")')


def register(sub):
    p = sub.add_parser('import-legacy', help='copy the data of the old layout into the output folder (copy only)')
    p.add_argument('--from', dest='src', default=str(DEFAULT_SOURCE), metavar='DIR',
                   help='the old data folder (default: the old per-user folder of this skill)')
    p.add_argument('--apply', action='store_true', help='copy (default: print the plan)')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


class Plan:
    """dest (posix, relative to the output root) -> bytes, with the source it came from."""

    def __init__(self):
        self.files = {}
        self.sources = {}
        self.mtimes = {}
        self.kinds = {}
        self.problems = []
        self.notes = []

    def add(self, dest, data, source, kind, mtime=None):
        if dest in self.files:
            self.problems.append(f'{dest}: two sources ({self.sources[dest]}, {source})')
            return
        self.files[dest], self.sources[dest], self.kinds[dest] = data, source, kind
        if mtime is not None:
            self.mtimes[dest] = mtime

    def add_file(self, dest, path, kind):
        self.add(dest, Path(path).read_bytes(), str(path), kind, Path(path).stat().st_mtime)

    def counts(self) -> dict:
        out = {}
        for kind in self.kinds.values():
            out[kind] = out.get(kind, 0) + 1
        return out


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _text(path) -> str:
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def _visible(path, root) -> bool:
    return not any(part.startswith('.') for part in Path(path).relative_to(root).parts)


def rewrite_report_refs(text) -> str:
    """runs.json: raw/<id>/report.md -> the new place of that run's report."""
    return _OLD_REPORT.sub(lambda m: f'{m.group(1)}{accum.report_ref(m.group(2))}{m.group(3)}', text)


def run_dest(rel) -> str:
    """Where a file of an old raw/<id>/ folder goes inside the run folder."""
    if rel == 'manifest.json':
        return 'run.json'
    if rel.split('/')[0] == 'collect':
        return 'raw/' + rel  # gr-compat: ignore=pathjoin (plan paths are always forward-slash relative)
    # gates/, logs/, report.md, report.preview.md, work/ and anything else the old run folder held
    return 'work/' + rel  # gr-compat: ignore=pathjoin (plan paths are always forward-slash relative)


def split_watch(old_watch, old_format, dropped=None) -> tuple:
    """(shared watch object, own settings object) from the old watch.json and format.json objects."""
    shared = {'schema': 'gr.watch/1'}
    shared.update({k: old_watch[k] for k in SHARED_KEYS if k in old_watch})
    own = {'schema': 'fiw.settings/1'}
    own.update({k: old_watch[k] for k in OWN_KEYS if k in old_watch})
    if old_format is not None:
        own['format'] = {k: v for k, v in old_format.items() if k != 'schema'}
    if dropped is not None:
        dropped += [k for k in DROPPED_KEYS if k in old_watch]
    return shared, own


def _read_obj(path):
    try:
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
    except (OSError, ValueError) as e:
        return None, f'{path}: not readable JSON ({e})'
    if not isinstance(obj, dict):
        return None, f'{path}: not a JSON object'
    return obj, None


def _plan_config(plan, src):
    cfg = src / OLD_CONFIG
    watch_path, format_path = cfg / 'watch.json', cfg / 'format.json'
    if not watch_path.is_file():
        plan.notes.append(f'{OLD_CONFIG}/watch.json: not found in the old folder — nothing to split')  # gr-compat: ignore=pathjoin (a message text)
        return
    watch, err = _read_obj(watch_path)
    if err:
        plan.problems.append(err)
        return
    fmt = None
    if format_path.is_file():
        fmt, err = _read_obj(format_path)
        if err:
            plan.problems.append(err)
            return
    dropped = []
    shared, own = split_watch(watch, fmt, dropped)
    merged = {k: v for k, v in {**shared, **own}.items() if k not in ('schema', 'format')}
    merged['schema'] = 'fiw.watch/1'
    for obj, name in ((merged, 'fiw.watch/1'), (own.get('format'), 'fiw.format/1')):
        if obj is None:
            continue
        if name == 'fiw.format/1':
            obj = {'schema': name, **obj}
        plan.problems += [f'config ({name}): {e}' for e in schema.validate(obj, name)]
    plan.add(layout.relative(layout.config_file(layout.SHARED_CONFIG)), _dump(shared), str(watch_path), 'config')
    plan.add(layout.relative(layout.config_file(layout.OWN_CONFIG)), _dump(own), f'{watch_path} + {format_path}',
             'config')
    if dropped:
        plan.notes.append('dropped from watch.json (their collectors no longer exist): ' + ', '.join(dropped))
    base_dir = cfg / '.base'
    if (base_dir / 'watch.json').is_file():
        bw, err = _read_obj(base_dir / 'watch.json')
        bf, err2 = _read_obj(base_dir / 'format.json') if (base_dir / 'format.json').is_file() else (None, None)
        if err or err2:
            plan.problems.append(err or err2)
            return
        b_shared, b_own = split_watch(bw, bf)
        base = {f'{layout.SHARED_CONFIG}.json': b_shared, f'{layout.OWN_CONFIG}.json': b_own}
        plan.add(layout.relative(layout.config_base_path()), _dump(base), str(base_dir), 'config')


def _plan_state(plan, src):
    state = src / OLD_STATE
    if not state.is_dir():
        return
    for f in sorted(state.iterdir()):
        if not f.is_file() or f.name.startswith('.'):
            continue
        name_schema = accum.STATE_FILES.get(f.name)
        if name_schema is None:
            plan.notes.append(f'{OLD_STATE}/{f.name}: not a state file of this skill — not copied')
            continue
        text = _text(f)
        if f.name == 'runs.json':
            text = rewrite_report_refs(text)
        try:
            plan.problems += [f'{OLD_STATE}/{f.name}: {e}' for e in schema.validate(json.loads(text), name_schema)]
        except ValueError as e:
            plan.problems.append(f'{OLD_STATE}/{f.name}: not JSON ({e})')
            continue
        plan.add(layout.relative(layout.state_dir() / f.name), text.encode('utf-8'), str(f), 'state',
                 f.stat().st_mtime)


def _plan_history(plan, src):
    hist = src / OLD_HISTORY
    revs_path = hist / 'revs.json'
    if not revs_path.is_file():
        return
    revs, err = _read_obj(revs_path)
    if err:
        plan.problems.append(err)
        return
    out = []
    for entry in revs.get('revs', []):
        folder = hist / str(entry.get('rev'))
        files = {}
        for name in sorted(accum.STATE_FILES):
            f = folder / name
            if f.is_file():
                text = _text(f)
                files[name] = rewrite_report_refs(text) if name == 'runs.json' else text
        out.append({'rev': entry.get('rev'), 'at': entry.get('at'), 'command': entry.get('command'),
                    'run_id': entry.get('run_id'), 'files': files})
    snap = {'schema': 'fiw.history/1', 'revs': out}
    problems = schema.validate(snap, 'fiw.history/1')
    plan.problems += [f'history: {e}' for e in problems]
    if not problems:
        plan.add(layout.relative(layout.snapshots_path()), _dump(snap), str(hist), 'history')


def _plan_reports(plan, src):
    base = src / OLD_REPORTS
    if not base.is_dir():
        return
    for f in sorted(base.iterdir()):
        if f.is_file() and not f.name.startswith('.') and f.suffix == '.md':
            plan.add(layout.relative(layout.reports_dir() / f.name), f.read_bytes(), str(f), 'report',
                     f.stat().st_mtime)


def _plan_runs(plan, src):
    base = src / OLD_RAW
    if not base.is_dir():
        return
    for rd in sorted(base.iterdir()):
        if not rd.is_dir() or rd.name.startswith('.'):
            continue
        if not re.match(layout.RUN_ID_RE, rd.name):
            plan.notes.append(f'{OLD_RAW}/{rd.name}: not a run folder (YYYY-MM-DD-N) — not copied')
            continue
        new_run = layout.run_dir(rd.name)
        for f in sorted(rd.rglob('*')):
            if f.is_file() and _visible(f, rd):
                rel = f.relative_to(rd).as_posix()
                plan.add(layout.relative(new_run / run_dest(rel)), f.read_bytes(), str(f), 'run', f.stat().st_mtime)


def build_plan(src) -> Plan:
    plan = Plan()
    _plan_config(plan, src)
    _plan_state(plan, src)
    _plan_history(plan, src)
    _plan_reports(plan, src)
    _plan_runs(plan, src)
    return plan


def _is_shipped(dest, home) -> bool:
    """True when the existing file is a shipped default nobody changed (it may be replaced)."""
    path = home / dest
    if dest.startswith(layout.relative(layout.config_dir()) + '/'):  # gr-compat: ignore=pathjoin (plan paths are always forward-slash relative)
        default = layout.defaults_dir() / Path(dest).name
        return default.is_file() and path.read_bytes() == default.read_bytes()
    if dest.endswith('/' + layout.CONFIG_BASE):  # gr-compat: ignore=pathjoin (plan paths are always forward-slash relative)
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            return False
        shipped = {}
        for name in layout.COPIED_DEFAULTS:
            with open(layout.defaults_dir() / f'{name}.json', encoding='utf-8') as f:
                shipped[f'{name}.json'] = json.load(f)
        return current == shipped
    return False


def classify(plan, home) -> tuple:
    """(to write, identical already there, conflicts) as lists of dest paths."""
    write, same, conflict = [], [], []
    for dest, data in sorted(plan.files.items()):
        path = home / dest
        if not path.exists():
            write.append(dest)
        elif path.is_file() and path.read_bytes() == data:
            same.append(dest)
        elif path.is_file() and _is_shipped(dest, home):
            write.append(dest)
        else:
            conflict.append(dest)
    return write, same, conflict


def _write(path, data, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    schema.atomic_write(path, data.decode('utf-8'))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def main(args) -> int:
    src = Path(args.src).expanduser()   # gr-structure: ignore (the user's own --from argument)
    if not src.is_dir():
        raise cli.UsageError(f'--from {src} is not a folder')
    home = layout.output_root()
    plan = build_plan(src)
    write, same, conflict = classify(plan, home)
    problems = list(plan.problems) + [f'{d}: already exists with different content — nothing is overwritten'
                                      for d in conflict]
    verified = 0
    applied = bool(args.apply and not problems)
    if applied:
        layout.ensure_home(reconcile=False)
        for dest in write:
            _write(home / dest, plan.files[dest], plan.mtimes.get(dest))
        for dest in write + same:
            if (home / dest).read_bytes() == plan.files[dest]:
                verified += 1
            else:
                problems.append(f'{dest}: differs from the plan after writing')
        applied = not problems
    code = cli.EXIT_OK if not problems else cli.EXIT_FAIL
    if args.json:
        cli.print_json({'exit': code, 'applied': applied, 'source': str(src), 'counts': plan.counts(),
                        'write': write, 'identical': same, 'verified': verified, 'dropped_or_skipped': plan.notes,
                        'problems': problems})
        return code
    print(f'import-legacy: source {src} (not changed)')
    for kind, n in sorted(plan.counts().items()):
        print(f'  {kind}: {n} file(s)')
    print(f'  to write {len(write)}, already identical {len(same)}')
    for line in plan.notes:
        print(f'note: {line}')
    for line in problems:
        print(f'problem: {line}')
    if applied:
        print(f'import-legacy: wrote {len(write)} file(s); verified {verified}/{len(write) + len(same)} by reading them back')
    elif not problems:
        print('import-legacy: nothing written (add --apply)')
    print(f'import-legacy: exit {code}')
    return code
