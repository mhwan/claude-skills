"""The only place that decides where a skill's output files go (contracts section 2.7).

This is the original copy. Each skill vendors it as scripts/lib/paths.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.

Root order:
  1. env var GR_HOME (absolute path; a temporary root for tests or a deliberate override)
  2. the skill lives in <workdir>/.claude/skills/<skill>/...  ->  <workdir>/output
  3. anything else: RootNotFound. Nothing is ever created in the home folder or the current
     directory as a fallback; the caller must stop and tell the user.

Resolving a path never creates anything; call ensure() when a folder is really needed.
Standard library only, Python 3.9+.
"""
from __future__ import annotations

import datetime
import os
import re
import unicodedata
from pathlib import Path

ENV_ROOT = 'GR_HOME'
TOP_FOLDERS = ('reports', 'data', 'state', 'history', 'runs', 'config')
SKILL_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
TOPIC_MAX = 30
FORBIDDEN_TOPIC_CHARS = '\\/:*?"<>|'

_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
_NAME_RE = re.compile(r'^(?P<d1>\d{4}-\d{2}-\d{2})(?:~(?P<d2>\d{4}-\d{2}-\d{2}))?(?:_(?P<topic>.+))?$')
_EXT_RE = re.compile(r'^[a-z0-9]{1,8}$')


class RootNotFound(RuntimeError):
    """The output root cannot be determined. The caller must stop and alert the user."""


class InvalidName(ValueError):
    """A skill name, file name, date or number breaks the naming rules."""


# ------------------------------------------------------------------------------ root

def _unresolvable(why):
    return RootNotFound(
        'Cannot determine the output folder (%s). Stop here and alert the user; do not write '
        'anywhere else and do not fall back to the home folder or the current folder. '
        'Fix: install the skill under <work folder>/.claude/skills/<skill>/, or set the '
        'environment variable %s to an absolute folder path.' % (why, ENV_ROOT))


def _same_place(a, b):
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _home_folders(env):
    homes = []
    for key in ('HOME', 'USERPROFILE'):
        value = env.get(key)
        if value:
            homes.append(Path(value).resolve())
    homes.append(Path(os.path.expanduser('~')).resolve())
    return homes


def _workdir_of(path):
    """Return <workdir> when path is inside <workdir>/.claude/skills/<skill>, else None."""
    parts = path.parts
    for i in range(2, len(parts) - 1):
        if parts[i].casefold() != 'skills' or parts[i - 1].casefold() != '.claude':
            continue
        if not SKILL_NAME_RE.match(parts[i + 1]):
            continue
        workdir_parts = parts[:i - 1]
        if len(workdir_parts) < 2:  # <workdir> would be the filesystem root
            continue
        return Path(*workdir_parts)
    return None


def resolve_root(skill_dir=None, env=None):
    """The output root folder (it may not exist yet). See the module docstring for the order."""
    env = os.environ if env is None else env
    configured = env.get(ENV_ROOT, '').strip()
    if configured:
        root = Path(os.path.expanduser(configured))
        if not root.is_absolute():
            raise _unresolvable('%s=%r is not an absolute path' % (ENV_ROOT, configured))
        return root
    here = Path(__file__) if skill_dir is None else Path(skill_dir)
    here = here.resolve()
    workdir = _workdir_of(here)
    if workdir is None:
        raise _unresolvable('%s is not inside <work folder>/.claude/skills/<skill>/ and %s is not set'
                            % (here, ENV_ROOT))
    for home in _home_folders(env):
        if _same_place(workdir, home):
            raise _unresolvable('%s is a personal copy under the home folder, not a work folder' % here)
    return workdir / 'output'


# ------------------------------------------------------------------------------ validation

def check_skill(skill):
    if not isinstance(skill, str) or not SKILL_NAME_RE.match(skill):
        raise InvalidName('bad skill name %r (lowercase letters, digits and single hyphens only)' % (skill,))
    return skill


def _to_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and _DATE_RE.match(value):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    raise InvalidName('bad date %r (expected a real YYYY-MM-DD date)' % (value,))


def _check_n(n):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise InvalidName('bad number %r (expected an integer >= 1)' % (n,))
    return n


def _clean_topic(topic):
    """NFC-normalise and validate a topic that holds no spaces; returns it."""
    if not isinstance(topic, str):
        raise InvalidName('bad topic %r (expected text)' % (topic,))
    topic = unicodedata.normalize('NFC', topic)
    if not topic:
        raise InvalidName('empty topic')
    for ch in topic:
        if ch in FORBIDDEN_TOPIC_CHARS or ord(ch) < 32 or ch.isspace():
            raise InvalidName('bad character %r in topic %r (forbidden: %s, spaces, control characters)'
                              % (ch, topic, FORBIDDEN_TOPIC_CHARS))
    if len(topic) > TOPIC_MAX:
        raise InvalidName('topic %r has %d characters (max %d)' % (topic, len(topic), TOPIC_MAX))
    if topic.endswith('.'):
        raise InvalidName('topic %r ends with a dot (not allowed in Windows file names)' % (topic,))
    return topic


def parse_name(name):
    """Validate a report/data file stem; returns {'kind', 'start', 'end', 'topic'}.

    kind is 'day' or 'period'; start/end are datetime.date; topic is None or the topic text.
    """
    m = _NAME_RE.match(name) if isinstance(name, str) else None
    if not m:
        raise InvalidName('bad name %r (expected YYYY-MM-DD, YYYY-MM-DD~YYYY-MM-DD, optionally followed '
                          'by _topic)' % (name,))
    start = _to_date(m.group('d1'))
    end = _to_date(m.group('d2')) if m.group('d2') else start
    if end < start:
        raise InvalidName('bad name %r (period ends before it starts)' % (name,))
    topic = _clean_topic(m.group('topic')) if m.group('topic') is not None else None
    return {'kind': 'period' if m.group('d2') else 'day', 'start': start, 'end': end, 'topic': topic}


def make_name(date=None, period=None, topic=None):
    """Build a file stem: a day, a period (start, end) or 'a~b', optionally with a topic."""
    if (date is None) == (period is None):
        raise InvalidName('give exactly one of date or period')
    if date is not None:
        stem = _to_date(date).isoformat()
    else:
        if isinstance(period, str):
            bits = period.split('~')
        else:
            bits = list(period)
        if len(bits) != 2:
            raise InvalidName('bad period %r (expected start and end)' % (period,))
        start, end = _to_date(bits[0]), _to_date(bits[1])
        if end < start:
            raise InvalidName('bad period %r (ends before it starts)' % (period,))
        stem = '%s~%s' % (start.isoformat(), end.isoformat())
    if topic is None:
        return stem
    if not isinstance(topic, str):
        raise InvalidName('bad topic %r (expected text)' % (topic,))
    topic = unicodedata.normalize('NFC', topic).strip()
    for ch in topic:
        if ch in FORBIDDEN_TOPIC_CHARS or ord(ch) < 32:
            raise InvalidName('bad character %r in topic %r (forbidden: %s)' % (ch, topic, FORBIDDEN_TOPIC_CHARS))
    topic = re.sub(r'\s+', '-', topic)
    return '%s_%s' % (stem, _clean_topic(topic))


# ------------------------------------------------------------------------------ paths

def report_path(skill, name, ext='md', *, skill_dir=None, env=None):
    check_skill(skill)
    parse_name(name)
    if not isinstance(ext, str) or not _EXT_RE.match(ext):
        raise InvalidName('bad extension %r' % (ext,))
    return resolve_root(skill_dir, env) / 'reports' / skill / ('%s.%s' % (name, ext))


def data_path(skill, name, *, skill_dir=None, env=None):
    check_skill(skill)
    parse_name(name)
    return resolve_root(skill_dir, env) / 'data' / skill / ('%s.json' % name)


def state_dir(skill, *, skill_dir=None, env=None):
    check_skill(skill)
    return resolve_root(skill_dir, env) / 'state' / skill


def history_path(skill, name, n, *, skill_dir=None, env=None):
    check_skill(skill)
    parse_name(name)
    _check_n(n)
    return resolve_root(skill_dir, env) / 'history' / skill / ('%s.v%d.md' % (name, n))


def run_dir(skill, date, n, *, skill_dir=None, env=None):
    check_skill(skill)
    day = _to_date(date).isoformat()
    _check_n(n)
    return resolve_root(skill_dir, env) / 'runs' / skill / ('%s-%d' % (day, n))


def config_path(name, *, skill_dir=None, env=None):
    """config/watch.json (shared) or config/<skill>.json."""
    if name != 'watch':
        check_skill(name)
    return resolve_root(skill_dir, env) / 'config' / ('%s.json' % name)


def ensure(path, parent=False):
    """Create the folder `path` (or, with parent=True, the folder that holds the file `path`)."""
    path = Path(path)
    target = path.parent if parent else path
    target.mkdir(parents=True, exist_ok=True)
    return path
