"""The standard result wrapper "gr.envelope/1" that every collecting skill writes (contracts section 2.2).

This is the original copy. Each skill vendors it as scripts/lib/envelope.py; the copy is checked
against this file by tests/_shared/test_vendored_copies.py, so never edit a vendored copy.

Shape of an envelope (a plain dict that is saved as JSON):
  schema    always "gr.envelope/1"
  skill     the producing skill, e.g. "assembly-bill-tracker"
  period    the requested period as one non-empty text (the producing skill decides its format)
  status    ok | empty | unread | no_key | failed | partial   (only a summary of `sources`)
  sources   [{source, status, requests, error?}]   status: ok | empty | unread | no_key | failed
  rows      [{id, title, date, url, <extra fields of the skill>}]
  warnings  [text]   anything a person should look at; a row without a link is listed here

Rules this module enforces:
  * unknown keys are errors, at the top level, in a source and in a row
  * a row carries id, title, date (YYYY-MM-DD) and url; url is null (never a placeholder text) and
    the row id must then appear in a warning
  * extra row fields are allowed only when listed in ROW_EXTRA_FIELDS for that skill
  * the top-level status must equal the summary of the source statuses

Exit codes for the calling script (usage errors, 2, are the caller's job):
  ok / empty -> 0,  partial -> 3,  every source unread / failed / no_key -> 1,
  any warning present on an otherwise ok or empty result -> 3.

Standard library only, Python 3.9+. Files are always UTF-8, never escaped to ASCII.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from pathlib import Path

SCHEMA = 'gr.envelope/1'

SOURCE_STATUSES = ('ok', 'empty', 'unread', 'no_key', 'failed')
FAILED_STATUSES = ('unread', 'no_key', 'failed')
TOP_STATUSES = SOURCE_STATUSES + ('partial',)

REQUIRED_KEYS = ('schema', 'skill', 'period', 'status', 'sources', 'rows', 'warnings')
SOURCE_KEYS = ('source', 'status', 'requests', 'error')
SOURCE_REQUIRED = ('source', 'status', 'requests')
COMMON_ROW_KEYS = ('id', 'title', 'date', 'url')

# Extra row fields per skill, on top of COMMON_ROW_KEYS. Only the coordinator edits this table.
# A skill that is not listed, or listed with an empty set, may not add any field.
# Names that are still undecided are left empty on purpose; the validator then rejects them
# instead of letting each skill invent its own spelling.
ROW_EXTRA_FIELDS = {
    'korean-news-search': frozenset({'outlet', 'summary'}),
    'assembly-hearing-watch': frozenset({'basis', 'kind', 'committee', 'time'}),
    'assembly-bill-tracker': frozenset({'stage', 'pass_class', 'committee', 'proposer',
                                        'proposer_kind', 'change', 'prev_stage'}),
    'assembly-member-research': frozenset({'member', 'party', 'committee', 'speech_count',
                                           'speeches', 'bills'}),
    'gov-notice-watch': frozenset({'dept', 'kind', 'open_to', 'summary'}),
    'food-safety-records': frozenset({'kind', 'company', 'product', 'reason', 'grade', 'action'}),
}

_SKILL_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_URL_RE = re.compile(r'^https?://\S+$')


class EnvelopeError(ValueError):
    """An envelope breaks the rules. `problems` holds every message found."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('invalid gr.envelope/1: ' + '; '.join(self.problems))


# ------------------------------------------------------------------------------ status

def summarize_status(sources):
    """The top-level status that follows from a list of source dicts (each with a 'status')."""
    statuses = [s.get('status') if isinstance(s, dict) else None for s in sources]
    if not statuses:
        return 'empty'
    if all(st == 'empty' for st in statuses):
        return 'empty'
    if all(st in ('ok', 'empty') for st in statuses):
        return 'ok'
    if all(st in FAILED_STATUSES for st in statuses):
        # every source failed: keep the shared reason when there is one
        return statuses[0] if len(set(statuses)) == 1 else 'failed'
    if any(st in ('ok', 'empty') for st in statuses):
        return 'partial'
    return 'failed'


def exit_code(envelope):
    """Process exit code for a finished run: 0 clean, 1 nothing could be read, 3 look at it."""
    status = envelope.get('status') if isinstance(envelope, dict) else None
    if status in FAILED_STATUSES:
        return 1
    if status == 'partial':
        return 3
    if status in ('ok', 'empty'):
        return 3 if envelope.get('warnings') else 0
    raise EnvelopeError(['unknown status %r' % (status,)])


# ------------------------------------------------------------------------------ build

def make_envelope(skill, period, sources, rows, warnings=None, validate_result=True):
    """Build a gr.envelope/1 dict. The status is derived from `sources`.

    A row whose url is missing or empty gets url=None and a warning that names its id.
    The result is validated unless validate_result is False.
    """
    out_warnings = list(warnings or [])
    out_rows = []
    for row in rows:
        row = dict(row)
        if not row.get('url'):
            row['url'] = None
            out_warnings.append('url missing: %s' % (row.get('id'),))
        out_rows.append(row)
    out_sources = [dict(s) for s in sources]
    envelope = {
        'schema': SCHEMA,
        'skill': skill,
        'period': period,
        'status': summarize_status(out_sources),
        'sources': out_sources,
        'rows': out_rows,
        'warnings': out_warnings,
    }
    if validate_result:
        assert_valid(envelope)
    return envelope


# ------------------------------------------------------------------------------ validate

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _real_date(text):
    if not isinstance(text, str) or not _DATE_RE.match(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _check_sources(sources, problems):
    if not isinstance(sources, list):
        problems.append('sources must be a list')
        return
    seen = set()
    for i, src in enumerate(sources):
        where = 'sources[%d]' % i
        if not isinstance(src, dict):
            problems.append('%s must be an object' % where)
            continue
        for key in sorted(k for k in src if k not in SOURCE_KEYS):
            problems.append('%s: unknown key %r' % (where, key))
        for key in SOURCE_REQUIRED:
            if key not in src:
                problems.append('%s: missing key %r' % (where, key))
        name = src.get('source')
        if 'source' in src:
            if not isinstance(name, str) or not name:
                problems.append('%s: source must be a non-empty text' % where)
            elif name in seen:
                problems.append('%s: duplicate source %r' % (where, name))
            else:
                seen.add(name)
        if 'status' in src and src['status'] not in SOURCE_STATUSES:
            problems.append('%s: bad status %r (allowed: %s)' % (where, src['status'], ', '.join(SOURCE_STATUSES)))
        if 'requests' in src and (not _is_int(src['requests']) or src['requests'] < 0):
            problems.append('%s: requests must be an integer >= 0' % where)
        if 'error' in src and (not isinstance(src['error'], str) or not src['error']):
            problems.append('%s: error must be a non-empty text when present' % where)


def _check_rows(skill, rows, warnings, problems):
    if not isinstance(rows, list):
        problems.append('rows must be a list')
        return
    allowed_extra = ROW_EXTRA_FIELDS.get(skill, frozenset())
    warning_texts = [w for w in warnings if isinstance(w, str)] if isinstance(warnings, list) else []
    seen = set()
    for i, row in enumerate(rows):
        where = 'rows[%d]' % i
        if not isinstance(row, dict):
            problems.append('%s must be an object' % where)
            continue
        for key in COMMON_ROW_KEYS:
            if key not in row:
                problems.append('%s: missing key %r' % (where, key))
        for key in sorted(k for k in row if k not in COMMON_ROW_KEYS and k not in allowed_extra):
            problems.append('%s: unknown key %r (extra fields of %r are registered in ROW_EXTRA_FIELDS '
                            'by the coordinator: %s)' % (where, key, skill,
                                                         ', '.join(sorted(allowed_extra)) or 'none'))
        row_id = row.get('id')
        if 'id' in row:
            if not isinstance(row_id, str) or not row_id:
                problems.append('%s: id must be a non-empty text' % where)
            elif row_id in seen:
                problems.append('%s: duplicate id %r' % (where, row_id))
            else:
                seen.add(row_id)
        if 'title' in row and (not isinstance(row['title'], str) or not row['title']):
            problems.append('%s: title must be a non-empty text' % where)
        if 'date' in row and not _real_date(row['date']):
            problems.append('%s: date %r must be a real YYYY-MM-DD date' % (where, row['date']))
        if 'url' in row:
            url = row['url']
            if url is None:
                if not (isinstance(row_id, str) and row_id and any(row_id in w for w in warning_texts)):
                    problems.append('%s: url is null but no warning names the row id %r' % (where, row_id))
            elif not isinstance(url, str) or not _URL_RE.match(url):
                problems.append('%s: url %r must be null or an http(s) link (never a placeholder text)'
                                % (where, url))


def validate(envelope):
    """Return the list of problems (empty when the envelope is valid). Never raises on bad content."""
    problems = []
    if not isinstance(envelope, dict):
        return ['envelope must be an object']
    for key in sorted(k for k in envelope if k not in REQUIRED_KEYS):
        problems.append('unknown key %r' % (key,))
    for key in REQUIRED_KEYS:
        if key not in envelope:
            problems.append('missing key %r' % (key,))
    if 'schema' in envelope and envelope['schema'] != SCHEMA:
        problems.append('schema must be %r, got %r' % (SCHEMA, envelope['schema']))
    skill = envelope.get('skill')
    if 'skill' in envelope and (not isinstance(skill, str) or not _SKILL_RE.match(skill)):
        problems.append('skill %r must be lowercase letters, digits and single hyphens' % (skill,))
    if 'period' in envelope and (not isinstance(envelope['period'], str) or not envelope['period'].strip()):
        problems.append('period must be a non-empty text')
    if 'status' in envelope and envelope['status'] not in TOP_STATUSES:
        problems.append('bad status %r (allowed: %s)' % (envelope['status'], ', '.join(TOP_STATUSES)))
    if 'warnings' in envelope:
        warnings = envelope['warnings']
        if not isinstance(warnings, list):
            problems.append('warnings must be a list')
        else:
            for i, w in enumerate(warnings):
                if not isinstance(w, str) or not w:
                    problems.append('warnings[%d] must be a non-empty text' % i)
    if 'sources' in envelope:
        _check_sources(envelope['sources'], problems)
    if 'rows' in envelope:
        _check_rows(skill if isinstance(skill, str) else '', envelope['rows'], envelope.get('warnings', []), problems)
    if ('status' in envelope and envelope['status'] in TOP_STATUSES
            and isinstance(envelope.get('sources'), list) and envelope['status'] != summarize_status(envelope['sources'])):
        problems.append('status %r does not match the sources (they sum up to %r)'
                        % (envelope['status'], summarize_status(envelope['sources'])))
    return problems


def assert_valid(envelope):
    """Raise EnvelopeError listing every problem; return the envelope when it is valid."""
    problems = validate(envelope)
    if problems:
        raise EnvelopeError(problems)
    return envelope


# ------------------------------------------------------------------------------ files

def write_envelope(path, envelope, validate_first=True):
    """Write the envelope as UTF-8 JSON (no ASCII escaping, LF newlines) via a temp file + replace."""
    if validate_first:
        assert_valid(envelope)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(envelope, ensure_ascii=False, indent=2) + '\n'
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix='.envelope-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.replace(tmp_name, str(path))
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return path


def read_envelope(path, validate_after=True):
    """Read an envelope file (UTF-8 JSON) and validate it unless told not to."""
    with open(str(path), encoding='utf-8') as handle:
        envelope = json.load(handle)
    if validate_after:
        assert_valid(envelope)
    return envelope
