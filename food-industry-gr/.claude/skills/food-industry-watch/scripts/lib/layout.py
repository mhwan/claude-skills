"""Where every file of this skill lives: a thin layer over the shared paths.py (contracts section 2.7).

All location decisions come from paths.py (output root, skill folders, run folders). This module only
adds the names inside them: the two config files, the state files, and the members of one run folder
(run.json, raw/, work/). Resolving a path never creates anything; ensure_home() does.
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path

from . import cli, paths, schema

SKILL = 'food-industry-watch'
RUN_ID_RE = r'^\d{4}-\d{2}-\d{2}-[1-9]\d*$'
_RUN_ID = re.compile(RUN_ID_RE)

SHARED_CONFIG = 'watch'                  # config/watch.json: companies, agencies, keywords (read by other skills too)
OWN_CONFIG = SKILL                       # config/food-industry-watch.json: this skill's own settings
COPIED_DEFAULTS = (SHARED_CONFIG, OWN_CONFIG)
NESTED_KEYS = ('format',)                # keys whose own keys are reconciled one by one
SNAPSHOTS = 'snapshots.json'
CONFIG_BASE = 'config-base.json'
FORMAT_HISTORY = 'format-history.json'


def skill_dir() -> Path:
    """The skill folder (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent.parent


def defaults_dir() -> Path:
    return skill_dir() / 'assets' / 'defaults'


def _stop(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (paths.RootNotFound, paths.InvalidName) as e:
        raise cli.UsageError(str(e))


def output_root() -> Path:
    """The output root (GR_HOME, else <work folder>/output). Stops with a usage error when it is unknown."""
    return _stop(paths.resolve_root, skill_dir())


# ------------------------------------------------------------------------------ config and state

def config_file(name) -> Path:
    return _stop(paths.config_path, name, skill_dir=skill_dir())


def config_dir() -> Path:
    return config_file(SHARED_CONFIG).parent


def _config_object(name, defaults=False) -> dict:
    """config/<name>.json of the data folder, else the shipped default (only the default when `defaults`);
    stops when it is not a JSON object."""
    path = defaults_dir() / f'{name}.json' if defaults else config_file(name)
    if not defaults and not path.is_file():
        path = defaults_dir() / f'{name}.json'
    obj = _read(path)
    if obj is None:
        raise cli.UsageError(f'{path}: not a readable JSON object')
    return obj


def load_watch(defaults=False) -> dict:
    """The watch lists as one fiw.watch/1 object: the shared config/watch.json (companies, agencies,
    keywords) plus the own file's provisional, bigkinds and stage1. `defaults`: the shipped files only."""
    merged = {k: v for k, v in _config_object(SHARED_CONFIG, defaults).items() if k != 'schema'}
    merged.update({k: v for k, v in _config_object(OWN_CONFIG, defaults).items()
                   if k not in ('schema', *NESTED_KEYS)})
    merged['schema'] = 'fiw.watch/1'
    try:
        schema.check(merged, 'fiw.watch/1')
    except schema.SchemaError as e:
        raise cli.UsageError(f'{SHARED_CONFIG}.json + {OWN_CONFIG}.json (config folder): {e}')
    return merged


def load_format(defaults=False) -> dict:
    """The report format (the `format` key of the own config file) as a fiw.format/1 object."""
    obj = dict(_config_object(OWN_CONFIG, defaults).get('format') or {})
    obj['schema'] = 'fiw.format/1'
    try:
        schema.check(obj, 'fiw.format/1')
    except schema.SchemaError as e:
        raise cli.UsageError(f'{OWN_CONFIG}.json (config folder) format: {e}')
    return obj


def _real_state_dir() -> Path:
    return _stop(paths.state_dir, SKILL, skill_dir=skill_dir())


_STATE_READ = None


def state_dir() -> Path:
    return _STATE_READ if _STATE_READ is not None else _real_state_dir()


@contextlib.contextmanager
def reading_state(folder):
    """state_dir() is `folder` inside the block (None: unchanged) — for read-only work such as re-rendering
    an earlier run from the state of its time; nothing may write state inside it."""
    global _STATE_READ
    old, _STATE_READ = _STATE_READ, (Path(folder) if folder is not None else None)
    try:
        yield
    finally:
        _STATE_READ = old


def save_format(fmt) -> None:
    """Write `fmt` (a fiw.format/1 object) as the `format` key of the own config file, keeping its other keys."""
    schema.check(fmt, 'fiw.format/1')
    own = _config_object(OWN_CONFIG)
    own['format'] = {k: v for k, v in fmt.items() if k != 'schema'}
    target = config_file(OWN_CONFIG)
    target.parent.mkdir(parents=True, exist_ok=True)
    schema.atomic_write(target, json.dumps(own, ensure_ascii=False, indent=2) + '\n')


def format_history_path() -> Path:
    """Earlier report formats, oldest first: {"versions": [fiw.format/1 objects]}."""
    return _real_state_dir() / FORMAT_HISTORY


def load_format_history() -> list:
    obj = _read(format_history_path())
    versions = obj.get('versions') if obj else None
    return [v for v in versions if isinstance(v, dict)] if isinstance(versions, list) else []


def save_format_history(versions) -> None:
    path = format_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema.atomic_write(path, json.dumps({'versions': list(versions)}, ensure_ascii=False, indent=2) + '\n')


def snapshots_path() -> Path:
    """Earlier states (the history of state/*.json) kept as text inside one file."""
    return _real_state_dir() / SNAPSHOTS


def config_base_path() -> Path:
    """The defaults the config files were last reconciled with."""
    return _real_state_dir() / CONFIG_BASE


# ------------------------------------------------------------------------------ reports and runs

def runs_dir() -> Path:
    """One folder per collection run (YYYY-MM-DD-N)."""
    return _stop(paths.run_dir, SKILL, '2000-01-01', 1, skill_dir=skill_dir()).parent


def reports_dir() -> Path:
    """Period reports built from the runs."""
    return _stop(paths.report_path, SKILL, '2000-01-01', skill_dir=skill_dir()).parent


def history_dir() -> Path:
    """Versions of earlier reports (paths.py history folder of this skill)."""
    return _stop(paths.history_path, SKILL, '2000-01-01', 2, skill_dir=skill_dir()).parent


def relative(path) -> str:
    """`path` below the output root, written with '/' (the form runs.json and seeds keep)."""
    return Path(path).relative_to(output_root()).as_posix()


def data_dir(skill) -> Path:
    """Where another skill keeps its gr.envelope/1 results (read here, never written)."""
    return _stop(paths.data_path, skill, '2000-01-01', skill_dir=skill_dir()).parent


def run_dir(run_id) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise cli.UsageError(f'malformed run id: {run_id!r} (expected YYYY-MM-DD-N)')
    return _stop(paths.run_dir, SKILL, run_id[:10], int(run_id[11:]), skill_dir=skill_dir())


def manifest_path(rd) -> Path:
    return Path(rd) / 'run.json'


def collect_dir(rd) -> Path:
    return Path(rd) / 'raw' / 'collect'


def work_dir(rd) -> Path:
    return Path(rd) / 'work'


def gates_dir(rd) -> Path:
    return work_dir(rd) / 'gates'


def logs_dir(rd) -> Path:
    return work_dir(rd) / 'logs'


def report_file(rd) -> Path:
    """The report of one run."""
    return work_dir(rd) / 'report.md'


def preview_file(rd) -> Path:
    return work_dir(rd) / 'report.preview.md'


def lock_file(rd) -> Path:
    return work_dir(rd) / '.manifest.lock'


def overlay_state_dir(rd) -> Path:
    return work_dir(rd) / 'state'   # gr-structure: ignore (a folder inside the run folder, not an output top folder)


def build_dir(run_id, a, b) -> Path:
    """Scratch folder of the period report a~b, laid out like a run folder; rebuilt on every report."""
    return work_dir(run_dir(run_id)) / 'build' / f'{a}~{b}'


# ------------------------------------------------------------------------------ home and config defaults

def ensure_home(reconcile=True) -> list:
    """Create the folders, copy missing config defaults and bring old copies up to date.

    Returns created paths. A key the reconcile kept although the defaults moved is reported on stderr.
    """
    created = []
    for d in (config_dir(), state_dir(), runs_dir(), reports_dir()):
        if not d.exists():
            d.mkdir(parents=True)
            created.append(d)
    for name in COPIED_DEFAULTS:
        target = config_file(name)
        if not target.exists():
            with open(defaults_dir() / f'{name}.json', encoding='utf-8') as f:
                schema.atomic_write(target, f.read())
            created.append(target)
    for rep in reconcile_config() if reconcile else ():
        line = reconcile_line(rep)
        if line:
            print(f'note: {line}', file=sys.stderr)
    return created


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _flat(obj) -> dict:
    """Top-level keys, except that the keys of NESTED_KEYS are listed one by one as 'format.key'."""
    out = {}
    for key, value in obj.items():
        if key in NESTED_KEYS and isinstance(value, dict):
            out.update({f'{key}.{k}': v for k, v in value.items()})
        else:
            out[key] = value
    return out


def _put(obj, flat_key, value):
    head, _, tail = flat_key.partition('.')
    if tail and head in NESTED_KEYS:
        if not isinstance(obj.get(head), dict):
            obj[head] = {}
        obj[head][tail] = value
    else:
        obj[flat_key] = value


def reconcile_config(take=()) -> list:
    """Three-way update of each copied config file against the shipped defaults.

    A key missing from the config is added; a key the user never changed (equal to the recorded base)
    takes the new default; a key in `take` takes the default; any other differing key is kept.
    Returns one report per file: {file, added, updated, kept, warn}. warn lists kept keys whose default
    moved since the last reconcile (or every kept key when no base was recorded).
    """
    reports = []
    bases = _read(config_base_path()) or {}
    new_bases = dict(bases)
    for name in COPIED_DEFAULTS:
        target = config_file(name)
        cfg, new = _read(target), _read(defaults_dir() / f'{name}.json')
        if cfg is None or new is None:
            continue
        base = bases.get(f'{name}.json')
        flat_cfg, flat_new = _flat(cfg), _flat(new)
        flat_base = _flat(base) if isinstance(base, dict) else None
        rep = {'file': f'{name}.json', 'added': [], 'updated': [], 'kept': [], 'warn': []}
        for key, value in flat_new.items():
            if key == 'schema':
                continue
            wanted = key in take or key.partition('.')[0] in take
            if key not in flat_cfg:
                _put(cfg, key, value)
                rep['added'].append(key)
            elif flat_cfg[key] == value:
                continue
            elif wanted or (flat_base is not None and key in flat_base and flat_cfg[key] == flat_base[key]):
                _put(cfg, key, value)
                rep['updated'].append(key)
            else:
                rep['kept'].append(key)
                if flat_base is None or flat_base.get(key) != value:
                    rep['warn'].append(key)
        if rep['added'] or rep['updated']:
            schema.atomic_write(target, json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
        new_bases[f'{name}.json'] = new
        reports.append(rep)
    if new_bases != bases:
        config_base_path().parent.mkdir(parents=True, exist_ok=True)
        schema.atomic_write(config_base_path(), json.dumps(new_bases, ensure_ascii=False, indent=2) + '\n')
    return reports


def reconcile_line(rep) -> str:
    """One line for a reconcile report, or '' when nothing worth saying happened."""
    parts = []
    if rep['added']:
        parts.append('added from defaults: ' + ', '.join(rep['added']))
    if rep['updated']:
        parts.append('updated to defaults: ' + ', '.join(rep['updated']))
    if rep['warn']:
        parts.append('kept your values (defaults differ): ' + ', '.join(rep['warn'])
                     + ' — fiw.py config --take-default KEY to take the default')
    return f"{rep['file']} (config folder): " + '; '.join(parts) if parts else ''
