"""fiw.py seed — share the runs + state + config without article text (D55), and start from a shared seed.

A seed holds this skill's runs folder, its state folder and the two config files, laid out as under the output
root. Period reports are not part of a seed: a receiving PC builds its own from the runs (`fiw.py report`)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from lib import accum, cli, layout, schema

from . import prune as prune_cmd

TEXT_KEYS = ('body', 'excerpt')


def runs_part() -> str:
    return layout.relative(layout.runs_dir())


def state_part() -> str:
    return layout.relative(layout.state_dir())


def part_folders() -> tuple:
    return (runs_part(), state_part())


def part_files() -> tuple:
    return tuple(layout.relative(layout.config_file(name)) for name in layout.COPIED_DEFAULTS)


def machine_files() -> tuple:
    return (layout.relative(layout.state_dir() / 'doctor.json'),)


def register(sub):
    p = sub.add_parser('seed', help='export or import the runs, state and config (never article text)')
    s = p.add_subparsers(dest='seed_command', metavar='SEED_COMMAND', required=True)
    e = s.add_parser('export', help='copy the runs, state and config into an empty folder')
    e.add_argument('--out', required=True, metavar='DIR')
    e.add_argument('--apply', action='store_true', help='copy (default: print what would be copied)')
    e.add_argument('--json', action='store_true', help='print the result JSON')
    e.set_defaults(func=main_export)
    i = s.add_parser('import', help='copy a seed into an empty data folder')
    i.add_argument('--from', dest='src', required=True, metavar='DIR')
    i.add_argument('--apply', action='store_true', help='copy (default: print what would be copied)')
    i.add_argument('--json', action='store_true', help='print the result JSON')
    i.set_defaults(func=main_import)


def text_fields(obj, path='$') -> list:
    """JSON paths of non-empty body / excerpt values anywhere in obj."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in TEXT_KEYS and isinstance(v, str) and v.strip():
                out.append(f'{path}.{k}')
            out += text_fields(v, f'{path}.{k}')
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            out += text_fields(v, f'{path}[{n}]')
    return out


def files_of(root) -> list:
    """(relative path, absolute path) of the files a seed holds under root (subfolders included)."""
    out = []
    for part in part_folders():
        base = Path(root) / part
        if base.is_dir():
            out += [(f.relative_to(root).as_posix(), f) for f in sorted(base.rglob('*')) if f.is_file()]
    out += [(rel, Path(root) / rel) for rel in part_files() if (Path(root) / rel).is_file()]
    # Hidden files and folders (tool leftovers, .DS_Store) are not data.
    out = [(rel, f) for rel, f in out if not any(p.startswith('.') for p in rel.split('/'))]
    # The app check belongs to one machine: a receiving PC must run its own (doctor).
    machine = machine_files()
    return [(rel, f) for rel, f in out if rel not in machine]


def problems(files) -> list:
    out = []
    for rel, f in files:
        if f.suffix != '.json':
            continue
        try:
            with open(f, encoding='utf-8') as fh:
                obj = json.load(fh)
        except ValueError as e:
            out.append(f'{rel}: not JSON ({e})')
            continue
        out += [f'{rel}: {p} has article text' for p in text_fields(obj)]
    return out


def unpruned(root) -> list:
    """Runs that still hold article text, also outside JSON fields (part bodies.md, stage logs): prune them first."""
    part = runs_part()
    base = Path(root) / part
    out = []
    for rd in (sorted(base.iterdir()) if base.is_dir() else []):
        if not rd.is_dir() or rd.name.startswith('.'):
            continue
        try:
            left = {k: v for k, v in prune_cmd.scan(rd, []).items() if v}
        except schema.SchemaError as e:
            out.append(f'{part}/{rd.name}: {e}')
            continue
        if left:
            out.append(f'{part}/{rd.name}: not pruned — '
                       + ', '.join(f'{k} {v}' for k, v in left.items()))
    return out


def _report(args, code, verb, files, found, applied):
    if args.json:
        cli.print_json({'exit': code, 'applied': applied, 'files': [r for r, _ in files], 'problems': found})
    else:
        for line in found:
            print(f'seed: {line}')
        print(f'seed: {verb} {len(files)} file(s)' + ('' if applied or found else ' (add --apply)'))
        print(f'seed: exit {code}')
    return code


def _copy(files, dest):
    for rel, f in files:
        target = Path(dest) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def main_export(args) -> int:
    out = Path(args.out).expanduser()   # gr-structure: ignore (the user's own --out argument)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise cli.UsageError(f'--out {out} must be absent or an empty folder')
    files = files_of(layout.output_root())
    found = problems(files) + unpruned(layout.output_root())
    if found:
        return _report(args, cli.EXIT_FAIL, 'refused —', files, found, False)
    if args.apply:
        out.mkdir(parents=True, exist_ok=True)
        _copy(files, out)
    return _report(args, cli.EXIT_OK, 'exported' if args.apply else 'would export', files, [], args.apply)


def main_import(args) -> int:
    src = Path(args.src).expanduser()   # gr-structure: ignore (the user's own --from argument)
    if not src.is_dir():
        raise cli.UsageError(f'--from {src} is not a folder')
    busy = [layout.relative(d) for d in (layout.state_dir(), layout.runs_dir(), layout.reports_dir(),
                                         layout.history_dir())
            if d.is_dir() and any(p.is_file() for p in d.rglob('*'))]
    if busy:
        print(f'seed: refused — the data folder already has files in {", ".join(busy)}; import needs an empty one')
        return cli.EXIT_FAIL
    files = files_of(src)
    found = problems(files) + unpruned(src)
    for rel, f in files:
        name = Path(rel).name
        if rel.startswith(state_part() + '/'):  # gr-compat: ignore=pathjoin (plan paths are always forward-slash relative)
            name_schema = accum.STATE_FILES.get(name) or ('fiw.history/1' if name == layout.SNAPSHOTS else None)
            try:
                if name_schema:
                    schema.load(f, name_schema)
            except schema.SchemaError as e:
                found.append(f'{rel}: {e}')
    if found:
        return _report(args, cli.EXIT_FAIL, 'refused —', files, found, False)
    if args.apply:
        layout.ensure_home()
        _copy(files, layout.output_root())
    return _report(args, cli.EXIT_OK, 'imported' if args.apply else 'would import', files, [], args.apply)
