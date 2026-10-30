"""Shared helpers for the korean-news-search scripts: run-folder access, JSON files, argument parsing."""
import argparse
import json
import os
import sys
from pathlib import Path

SKILL = 'korean-news-search'
VERSION = '0.1'
SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent

# Caps agreed for this skill: rows listed in the report, articles summarized per run, bodies fetched per search.
CAPS = {'list': 200, 'summaries': 30, 'fetch': 2000}

sys.path.insert(0, str(SCRIPTS_DIR))
from lib import paths  # noqa: E402


class UsageError(Exception):
    """A problem with the way the script was called (exit code 2, nothing written)."""


def setup_output():
    """Write UTF-8 to stdout and stderr on every platform."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


def make_parser(description):
    """An argument parser that does not accept abbreviated flags."""
    return argparse.ArgumentParser(description=description, allow_abbrev=False)


def read_json(path):
    with open(str(path), encoding='utf-8') as fh:
        return json.load(fh)


def write_json(path, obj):
    """Write JSON as UTF-8 with LF line ends, via a temporary file so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(str(tmp), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    os.replace(str(tmp), str(path))
    return path


def emit(obj):
    """Print one JSON object to stdout."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def fail(message, code=1):
    sys.stderr.write(message.rstrip('\n') + '\n')
    sys.stderr.flush()
    return code


def runs_base():
    """<root>/runs/korean-news-search (may not exist yet)."""
    try:
        return paths.run_dir(SKILL, '2000-01-01', 1, skill_dir=SKILL_DIR).parent
    except paths.RootNotFound as e:
        raise UsageError(str(e))


def resolve_run(arg):
    """The run folder named on the command line; it must be a prepared run under this skill's runs folder."""
    run = Path(arg)
    base = runs_base()
    try:
        run_real = run.resolve()
        base_real = base.resolve()
    except OSError as e:
        raise UsageError('cannot resolve %s: %s' % (arg, e))
    if run_real.parent != base_real:
        raise UsageError('%s is not a run folder of this skill (expected a folder directly under %s)' % (arg, base))
    if not run_real.is_dir() or not (run_real / 'run.json').is_file():
        raise UsageError('%s is not a prepared run folder (run.json is missing)' % arg)
    return run_real


def load_meta(run):
    return read_json(run / 'run.json')


def save_meta(run, meta):
    write_json(run / 'run.json', meta)


def record_step(run, name, **updates):
    """Append a step name to run.json (and merge any other fields)."""
    meta = load_meta(run)
    meta.setdefault('steps', []).append(name)
    meta.update(updates)
    save_meta(run, meta)
    return meta


def load_job(run):
    return read_json(run / 'work' / 'job.json')


def int_arg(text):
    """argparse type: a whole number, 0 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('expected a whole number, got %r' % text)
    if value < 0:
        raise argparse.ArgumentTypeError('expected 0 or more, got %d' % value)
    return value
