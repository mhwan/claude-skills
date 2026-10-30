"""Shared helpers for the daily-news-brief scripts: run-folder access and the --run / --date flags.

The output root and every folder name come from lib/paths.py; nothing here builds an output path by hand.
Layout of one run:  <root>/runs/daily-news-brief/<date>-N/{raw,work,run.json}
"""
import json
import os
import re
import sys
from pathlib import Path

SKILL = 'daily-news-brief'
VERSION = '0.2'
SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))
from lib import dates, paths  # noqa: E402

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class UsageError(Exception):
    """A problem with the way the script was called (exit code 2, nothing written)."""


def setup_output():
    """Write UTF-8 to stdout and stderr on every platform."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


def fail(message, code=1):
    sys.stderr.write(message.rstrip('\n') + '\n')
    sys.stderr.flush()
    return code


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


# ------------------------------------------------------------------------------ flags

def pull_flag(argv, name):
    """Remove `--name VALUE` or `--name=VALUE` from argv (a list, changed in place); return VALUE or None."""
    value = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == name:
            if i + 1 >= len(argv):
                raise UsageError('%s needs a value' % name)
            value = argv[i + 1]
            del argv[i:i + 2]
            continue
        if a.startswith(name + '='):
            value = a[len(name) + 1:]
            del argv[i]
            continue
        i += 1
    return value


def check_date(text):
    """A real YYYY-MM-DD date, or UsageError."""
    try:
        return dates.fmt(dates.parse_date(text))
    except dates.DateError as e:
        raise UsageError('bad date %r: %s' % (text, e))


# ------------------------------------------------------------------------------ run folders

def runs_base():
    """<root>/runs/daily-news-brief (may not exist yet)."""
    try:
        return paths.run_dir(SKILL, '2000-01-01', 1, skill_dir=SKILL_DIR).parent
    except paths.RootNotFound as e:
        raise UsageError(str(e))


def run_numbers(date):
    """The N of every existing <date>-N folder under the runs folder, ascending."""
    base = runs_base()
    if not base.is_dir():
        return []
    found = []
    pat = re.compile(r'^%s-([1-9]\d*)$' % re.escape(date))
    for entry in base.iterdir():
        m = pat.match(entry.name)
        if m and entry.is_dir():
            found.append(int(m.group(1)))
    return sorted(found)


def run_path(date, n):
    try:
        return paths.run_dir(SKILL, date, n, skill_dir=SKILL_DIR)
    except paths.RootNotFound as e:
        raise UsageError(str(e))


def latest_run(date):
    """The folder of the highest N for this date, or None when the date has no run."""
    numbers = run_numbers(date)
    return run_path(date, numbers[-1]) if numbers else None


def new_run(date):
    """Create runs/<skill>/<date>-N (raw/ and work/ inside) for the next free N and return it."""
    n = (run_numbers(date) or [0])[-1] + 1
    while True:
        run = run_path(date, n)
        try:
            run.mkdir(parents=True)
            break
        except FileExistsError:
            n += 1
    (run / 'raw').mkdir()
    (run / 'work').mkdir()
    return run


def resolve_run(date, run_arg=None):
    """The run folder to work in: --run when given (it must be a folder directly under this skill's runs
    folder and carry the same date), else the newest run of the date."""
    if run_arg:
        run = Path(run_arg)
        base = runs_base()
        try:
            run_real = run.resolve()
            base_real = base.resolve()
        except OSError as e:
            raise UsageError('cannot resolve %s: %s' % (run_arg, e))
        if run_real.parent != base_real:
            raise UsageError('%s is not a run folder of this skill (expected a folder directly under %s)' % (run_arg, base))
        if not run_real.is_dir():
            raise UsageError('%s is not a folder' % run_arg)
        if not run_real.name.startswith(date + '-'):
            raise UsageError('%s is a run of another date than %s' % (run_arg, date))
        return run_real
    run = latest_run(date)
    if run is None:
        raise UsageError('no run folder for %s under %s (collect and normalize first)' % (date, runs_base()))
    return run


def parse_run_flags(argv):
    """Take --run and --date out of argv (in place); return (date or None, run folder argument or None)."""
    run_arg = pull_flag(argv, '--run')
    date = pull_flag(argv, '--date')
    return (check_date(date) if date else None), run_arg


def body_files(run, date):
    """The files of one run that hold article bodies: raw/*.json and work/<date>.todo.json.
    These are the files prune.py deletes once the report is finished."""
    run = Path(run)
    found = sorted(p for p in (run / 'raw').glob('*.json') if p.is_file()) if (run / 'raw').is_dir() else []
    todo = run / 'work' / ('%s.todo.json' % date)
    if todo.is_file():
        found.append(todo)
    return found


def write_run_info(run, date, **extra):
    """Create or update run.json (run bookkeeping, never any article text)."""
    run = Path(run)
    info_path = run / 'run.json'
    info = read_json(info_path) if info_path.is_file() else {
        'skill': SKILL,
        'version': VERSION,
        'date': date,
        'run': run.name,
        'created_at': dates.now_iso(),
    }
    info.update(extra)
    write_json(info_path, info)
    return info


# ------------------------------------------------------------------------------ outputs

def report_file(date):
    """<root>/reports/daily-news-brief/<date>.md (the folder may not exist yet)."""
    try:
        return paths.report_path(SKILL, date, 'md', skill_dir=SKILL_DIR)
    except paths.RootNotFound as e:
        raise UsageError(str(e))


def output_root():
    """The output root (GR_HOME, the work folder's output/, ...)."""
    try:
        return paths.resolve_root(skill_dir=SKILL_DIR)
    except paths.RootNotFound as e:
        raise UsageError(str(e))


def state_folder():
    """<root>/state/daily-news-brief: the body-less summaries the stale check reads."""
    try:
        return paths.state_dir(SKILL, skill_dir=SKILL_DIR)
    except paths.RootNotFound as e:
        raise UsageError(str(e))
