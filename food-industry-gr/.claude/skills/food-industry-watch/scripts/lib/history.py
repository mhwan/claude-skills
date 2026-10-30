"""Snapshots of state/ taken before every state-changing command (02-plan §6).

All snapshots live in one file, snapshots.json in this skill's state folder: {"revs": [{rev, at, command, run_id,
files: {name: file text}}]}. Only the files of accum.STATE_FILES are kept, as their exact text, so a restore
is byte-identical.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from . import accum, dates, layout, schema

_TEMP_DIRS = []


def _cleanup():
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def load_revs() -> dict:
    path = layout.snapshots_path()
    if not path.is_file():
        return {'schema': 'fiw.history/1', 'revs': []}
    return schema.load(path, 'fiw.history/1')


def rev_numbers() -> list:
    """Existing snapshot revs, ascending."""
    return sorted(r['rev'] for r in load_revs()['revs'])


def files_of(rev) -> list:
    """File names held by snapshot `rev` (sorted); FileNotFoundError when it does not exist."""
    return sorted(_find(load_revs(), rev)['files'])


def _find(revs, rev) -> dict:
    for r in revs['revs']:
        if r['rev'] == rev:
            return r
    raise FileNotFoundError(f'snapshot {rev} does not exist')


def snapshot(command, run_id=None) -> int:
    """Keep the text of state/'s files as a new snapshot and return its rev."""
    revs = load_revs()
    rev = max((r['rev'] for r in revs['revs']), default=0) + 1
    files = {}
    src = layout.state_dir()
    for name in sorted(accum.STATE_FILES):
        f = src / name
        if f.is_file():
            with open(f, encoding='utf-8', newline='') as fh:
                files[name] = fh.read()
    revs['revs'].append({'rev': rev, 'at': dates.now_iso(), 'command': command, 'run_id': run_id, 'files': files})
    layout.snapshots_path().parent.mkdir(parents=True, exist_ok=True)
    schema.dump(layout.snapshots_path(), revs, 'fiw.history/1')
    return rev


def _materialize(entry) -> Path:
    folder = Path(tempfile.mkdtemp(prefix='fiw-state-'))
    _TEMP_DIRS.append(folder)
    for name, text in entry['files'].items():
        with open(folder / name, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
    return folder


def state_as_of(run_id, runs_state):
    """A temporary folder holding state/ as it was while `run_id` was the latest published run, or None when it still is.

    That is the snapshot the first publish of a later run (runs.json order) took. LookupError when a later run is
    published but that snapshot is gone. The folder is removed when the program ends.
    """
    runs = (runs_state or {}).get('runs', [])
    ids = [r['run_id'] for r in runs]
    later = {r['run_id'] for r in runs[ids.index(run_id) + 1:] if r['report']} if run_id in ids else set()
    if not later:
        return None
    revs = load_revs()
    first = next((r for r in revs['revs'] if r['command'] == 'publish' and r['run_id'] in later), None)
    if first is not None:
        return _materialize(first)
    raise LookupError(f'later run(s) {", ".join(sorted(later))} already published and the state snapshot from before '
                      f'that publish is gone ({layout.snapshots_path().name}) — {run_id} cannot be '
                      're-rendered as of its time (스냅샷 없음); nothing changed')


def restore(rev):
    """Make state/'s files byte-identical to snapshot `rev` (files absent there are removed)."""
    entry = _find(load_revs(), rev)
    state = layout.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    for name in accum.STATE_FILES:
        if name not in entry['files'] and (state / name).is_file():
            (state / name).unlink()
    for name, text in sorted(entry['files'].items()):
        schema.atomic_write(state / name, text)
