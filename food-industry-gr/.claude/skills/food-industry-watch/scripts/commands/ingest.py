"""fiw.py ingest — reassemble export.js chunks from saved tool results and write the article bodies."""
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import json
import re
import zlib
from pathlib import Path

from lib import cli, dates, gates, schema

from . import collect as collect_cmd

# The only browser job left: article bodies. The lists of every source come from the other skills' results.
JOBS = ('bigkinds_bodies',)

_CAPTURED_RE = re.compile(r'\s*\(captured at origin [^\n]*\)\s*$')
_HEAD_RE = re.compile(r'^FIWCHUNK\|([a-z_]+)\|(\d+)\|(\d+)\|(\d+)\|')
_PAD = '|PAD|'


class IngestError(Exception):
    """The chunks or the job result are not what export.js produces; maps to exit 1."""


def _positive_int(s):
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not an integer: {s!r}') from None
    if n < 1:
        raise argparse.ArgumentTypeError(f'must be at least 1: {n}')
    return n


def register(sub):
    p = sub.add_parser('ingest', help='reassemble browser-job chunks into the article bodies of the run')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--job', required=True, choices=JOBS, help='browser job that was exported')
    p.add_argument('--expect-len', required=True, type=_positive_int, metavar='N',
                   help='b64_len reported by window.__fiw.export(job)')
    p.add_argument('files', nargs='+', metavar='FILE', help='saved tool-result files, any order')
    p.add_argument('--json', action='store_true', help='print the updated fiw.manifest/1 JSON')
    p.set_defaults(func=main)


# ---------------------------------------------------------------- chunks

def read_chunk(path) -> tuple:
    """(job, index, count, b64_len, payload) of one saved tool-result file."""
    name = Path(path).name
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except ValueError as e:
        raise IngestError(f'{name}: not JSON ({e})') from None
    # The saved file is a list of {type, text}; element 0 is the result, later ones (tab context)
    # are ignored. Do not assume a single element.
    if (not isinstance(data, list) or not data or not isinstance(data[0], dict)
            or not isinstance(data[0].get('text'), str)):
        raise IngestError(f'{name}: not a saved tool result (expected a list of {{type, text}})')
    text = _CAPTURED_RE.sub('', data[0]['text']).strip()
    if text.startswith('"'):
        try:
            text = json.loads(text)
        except ValueError:
            raise IngestError(f'{name}: element 0 is not a JSON string') from None
    m = _HEAD_RE.match(text) if isinstance(text, str) else None
    if not m:
        raise IngestError(f'{name}: element 0 is not an export chunk (no FIWCHUNK header)')
    payload = text[m.end():].split(_PAD, 1)[0]
    return m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), payload


def assemble(files, job, expect_len):
    """Decoded job result object from chunk files; IngestError on any mismatch."""
    chunks, count, b64_len = {}, None, None
    for path in files:
        c_job, i, n, length, payload = read_chunk(path)
        name = Path(path).name
        if c_job != job:
            raise IngestError(f'{name}: chunk of job {c_job!r}, expected {job!r}')
        if count is None:
            count, b64_len = n, length
        elif (n, length) != (count, b64_len):
            raise IngestError(f'{name}: chunk header {n} chunks / {length} chars differs from '
                              f'{count} / {b64_len} of the other chunks')
        if i in chunks:
            raise IngestError(f'{name}: duplicate chunk {i}')
        chunks[i] = payload
    missing = [i for i in range(count) if i not in chunks]
    if missing or len(chunks) != count:
        raise IngestError(f'missing chunk(s) {missing} of {count}')
    if b64_len != expect_len:
        raise IngestError(f'--expect-len {expect_len} but the chunks say {b64_len}')
    b64 = ''.join(chunks[i] for i in range(count))
    if len(b64) != b64_len:
        raise IngestError(f'reassembled length {len(b64)} != {b64_len}')
    try:
        raw = gzip.decompress(base64.b64decode(b64, validate=True))
        obj = json.loads(raw.decode('utf-8'))
    except (binascii.Error, zlib.error, OSError, EOFError, ValueError) as e:
        raise IngestError(f'cannot decode the reassembled data: {e}') from None
    if not isinstance(obj, dict) or obj.get('job') != job:
        raise IngestError(f'decoded result is not a {job} result')
    return obj


# ---------------------------------------------------------------- bodies (pure)

def _date_or_none(v):
    try:
        return dates.parse_date(v)
    except dates.DateError:
        return None


def _str(v):
    return '' if v is None else str(v)


def merge_bodies(existing, res, run_id) -> dict:
    """fiw.bodies/1 with the result's articles merged by id over an existing file's."""
    articles = {a['id']: a for a in (existing or {}).get('articles', [])}
    errors = {e['id']: e for e in (existing or {}).get('errors', [])}
    for a in res.get('articles') or []:
        aid = _str(a.get('id'))
        day = _date_or_none(a.get('date'))
        if not aid:
            continue
        if day is None or not _str(a.get('body')).strip():
            errors[aid] = {'id': aid, 'status': None,
                           'detail': 'empty body' if day is not None else f'bad date {a.get("date")!r}'}
            continue
        articles[aid] = {'id': aid, 'title': _str(a.get('title')), 'outlet': _str(a.get('outlet')),
                         'date': dates.fmt(day), 'url': _str(a.get('url')), 'body': _str(a.get('body'))}
        chars = a.get('chars')
        if isinstance(chars, int) and not isinstance(chars, bool):
            articles[aid]['chars'] = chars       # the length the browser job read; prepare compares it
        errors.pop(aid, None)
    for e in res.get('errors') or []:
        eid = _str(e.get('id'))
        if eid and eid not in articles:
            st = e.get('status')
            errors[eid] = {'id': eid, 'status': st if isinstance(st, int) and not isinstance(st, bool) else None,
                           'detail': _str(e.get('detail'))[:200]}
    return {'schema': 'fiw.bodies/1', 'run_id': run_id,
            'articles': sorted(articles.values(), key=lambda a: a['id']),
            'errors': sorted(errors.values(), key=lambda e: e['id'])}


# ---------------------------------------------------------------- command

def _failed_job(rd, manifest, res, args) -> int:
    """A browser job that ended in phase 'failed': nothing is stored, the stage records the failure."""
    details = [str(e.get('detail', e)) if isinstance(e, dict) else str(e) for e in res.get('errors') or []]
    manifest['stages']['ingest:bigkinds_bodies'] = gates.stage_entry('failed', cli.EXIT_WARN, None)
    gates.save_manifest(rd, manifest, stages=['ingest:bigkinds_bodies'], sources=[])
    if args.json:
        cli.print_json(manifest)
    else:
        print(f'ingest {args.job}: the browser job failed — {"; ".join(details)[:200] or "no detail"}')
        print(f'ingest {args.job}: exit {cli.EXIT_WARN}')
    return cli.EXIT_WARN


def main(args) -> int:
    rd, manifest = collect_cmd.load_run(args.run)
    for f in args.files:
        if not Path(f).is_file():
            raise cli.UsageError(f'file not found: {f}')
    try:
        res = assemble(args.files, args.job, args.expect_len)
        if res.get('phase') == 'failed':
            return _failed_job(rd, manifest, res, args)
        bodies_path = rd / 'work' / 'bodies.json'
        existing = schema.load(bodies_path, 'fiw.bodies/1') if bodies_path.is_file() else None
        bodies = merge_bodies(existing, res, manifest['run_id'])
        schema.check(bodies, 'fiw.bodies/1')
    except IngestError as e:
        print(f'ingest: FAIL — {e}')
        return cli.EXIT_FAIL

    (rd / 'work').mkdir(exist_ok=True)
    schema.dump(rd / 'work' / 'bodies.json', bodies, 'fiw.bodies/1')
    code = cli.EXIT_WARN if bodies['errors'] else cli.EXIT_OK
    manifest['stages']['ingest:bigkinds_bodies'] = gates.stage_entry(
        'ok' if code == cli.EXIT_OK else 'warn', code, None)
    gates.save_manifest(rd, manifest, stages=['ingest:bigkinds_bodies'], sources=[])
    if args.json:
        cli.print_json(manifest)
    else:
        print(f'bodies: {len(bodies["articles"])} articles, {len(bodies["errors"])} errors')
        print(f'ingest {args.job}: exit {code}')
    return code
