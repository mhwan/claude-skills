"""Run-file helpers shared by the stages after collection: hashes, gate records, stage records."""
from __future__ import annotations

import contextlib
import hashlib
import os
import time
from pathlib import Path

from . import cli, dates, layout, schema

STAGE_STATUS = {0: 'ok', 1: 'failed', 2: 'failed', 3: 'warn'}
LOCK_WAIT, LOCK_STALE = 30, 120     # seconds


def load_run(run_id):
    """(run folder, manifest); a missing run is a usage error."""
    rd = layout.run_dir(run_id)
    manifest_path = layout.manifest_path(rd)
    if not manifest_path.is_file():
        raise cli.UsageError(f'run not found: {run_id}')
    with _manifest_lock(rd):        # Windows cannot replace a file another process has open
        return rd, schema.load(manifest_path, 'fiw.manifest/1')


def file_hash(path):
    """sha256 hex of a file's bytes, or None when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return None
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_work(rd, name, contract, required=True):
    """work/{name}.json validated against contract; None when absent and not required."""
    path = Path(rd) / 'work' / f'{name}.json'
    if not path.is_file():
        if required:
            raise cli.UsageError(f'missing {path.name} — run the step that writes it first')
        return None
    return schema.load(path, contract)


def stage_entry(status, code, input_hash, started_at=None):
    """A manifest stage record; started_at defaults to when this command started."""
    return {'status': status, 'exit': code, 'input_hash': input_hash,
            'started_at': started_at or cli.started_at(), 'at': dates.now_iso()}


@contextlib.contextmanager
def _manifest_lock(rd):
    """Exclusive lock file next to manifest.json (collect may run in the background while ingest writes)."""
    lock = layout.lock_file(rd)
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink()               # left by a killed process
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise cli.UsageError(f'manifest is locked by another fiw.py command: {lock}') from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def save_manifest(rd, manifest, stages=(), sources=()):
    """Write only the named stage / source entries over the manifest on disk, under the lock.

    Another fiw.py process may have written the manifest since this one loaded it; copying just our own
    entries keeps theirs. The in-memory manifest then picks up the entries the other process wrote.
    """
    path = layout.manifest_path(Path(rd))
    with _manifest_lock(rd):
        disk = schema.load(path, 'fiw.manifest/1') if path.is_file() else manifest
        if disk is not manifest:
            for key in stages:
                disk['stages'][key] = manifest['stages'][key]
            for key in sources:
                disk['sources'][key] = manifest['sources'][key]
        schema.dump(path, disk, 'fiw.manifest/1')
    manifest['stages'] = disk['stages']
    manifest['sources'] = disk['sources']


def record_stage(rd, manifest, stage, code, input_hash, started_at=None):
    manifest['stages'][stage] = stage_entry(STAGE_STATUS[code], code, input_hash, started_at)
    save_manifest(rd, manifest, stages=[stage])


def write_gate(rd, manifest, gate, code, checked, findings, *, classified=None, skipped=(),
               input_hash=None):
    """Write gates/{gate}.json (fiw.gate/1) and the manifest stage; returns the gate object."""
    obj = {'schema': 'fiw.gate/1', 'gate': gate, 'exit': code, 'checked': checked,
           'findings': list(findings), 'classified': dict(classified or {}),
           'skipped': list(skipped), 'input_hash': input_hash}
    layout.gates_dir(Path(rd)).mkdir(exist_ok=True)
    schema.dump(layout.gates_dir(Path(rd)) / f'{gate}.json', obj, 'fiw.gate/1')
    record_stage(rd, manifest, gate, code, input_hash)
    return obj


def read_gate(rd, gate):
    """gates/{gate}.json, or None when it was never written."""
    path = layout.gates_dir(Path(rd)) / f'{gate}.json'
    if not path.is_file():
        return None
    return schema.load(path, 'fiw.gate/1')


def print_findings(findings):
    """Findings as a Markdown table on stdout."""
    if not findings:
        return
    print('| where | kind | detail |')
    print('|---|---|---|')
    for f in findings:
        detail = ' '.join(str(f['detail']).split()).replace('|', '/')
        print(f'| {f["where"]} | {f["kind"]} | {detail} |')
