#!/usr/bin/env python3
"""Rebuild the collector's output from the chunk files saved out of the browser tab.

Usage: ingest.py --expect-len N FILE [FILE ...]

The browser cannot write files, so collect.js hands its result over as text chunks
(gzip + base64, one chunk per tool call). The app saves each long answer to a file; this script puts
the chunks back together, checks them, opens a new run folder for the collected date and writes
raw/collected.json into it. normalize.py takes that file next.
Nothing is written unless every check passes.

The chunk reading and checking are copied from korean-news-search/scripts/ingest.py (a skill never
imports another skill); only the part after the checks differs, because this collector hands over one
whole result (kind "collect") and the run folder does not exist yet.
"""
import argparse
import base64
import binascii
import gzip
import json
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

MAGIC = 'KNSCHUNK'
PAD_MARK = '|PAD|'
KIND = 'collect'


class IngestError(Exception):
    pass


def read_chunk_text(path):
    """The chunk string inside a saved tool-result file."""
    try:
        with open(str(path), encoding='utf-8') as fh:
            saved = json.load(fh)
    except (OSError, ValueError) as e:
        raise IngestError('%s is not a saved tool result: %s' % (path, e))
    if not isinstance(saved, list) or not saved or not isinstance(saved[0], dict) or not isinstance(saved[0].get('text'), str):
        raise IngestError('%s is not a saved tool result (no text in its first element)' % path)
    # the text is a JSON string followed by a "captured at origin" note
    try:
        value, _ = json.JSONDecoder().raw_decode(saved[0]['text'].lstrip())
    except ValueError as e:
        raise IngestError('%s does not hold a chunk string: %s' % (path, e))
    if not isinstance(value, str):
        raise IngestError('%s does not hold a chunk string' % path)
    return value


def parse_chunk(text, path):
    parts = text.split('|', 5)
    if len(parts) != 6 or parts[0] != MAGIC:
        raise IngestError('%s is not a collector chunk' % path)
    try:
        index, count, total = int(parts[2]), int(parts[3]), int(parts[4])
    except ValueError:
        raise IngestError('%s has a damaged chunk header' % path)
    data = parts[5].split(PAD_MARK)[0]
    return {'kind': parts[1], 'index': index, 'count': count, 'total': total, 'data': data, 'path': str(path)}


def assemble(kind, expect_len, files):
    chunks = [parse_chunk(read_chunk_text(f), f) for f in files]
    for c in chunks:
        if c['kind'] != kind:
            raise IngestError('%s holds a %r chunk, not %r' % (c['path'], c['kind'], kind))
    counts = set(c['count'] for c in chunks)
    totals = set(c['total'] for c in chunks)
    if len(counts) != 1 or len(totals) != 1:
        raise IngestError('the chunks come from different exports (chunk counts %s, lengths %s)' % (sorted(counts), sorted(totals)))
    count, total = counts.pop(), totals.pop()
    if total != expect_len:
        raise IngestError('the exported length is %d but %d was expected' % (total, expect_len))
    seen = {}
    for c in chunks:
        if not 0 <= c['index'] < count:
            raise IngestError('%s has chunk number %d outside 0..%d' % (c['path'], c['index'], count - 1))
        if c['index'] in seen:
            raise IngestError('chunk %d was given twice (%s and %s)' % (c['index'], seen[c['index']]['path'], c['path']))
        seen[c['index']] = c
    missing = [i for i in range(count) if i not in seen]
    if missing:
        raise IngestError('missing chunk(s): %s of %d' % (', '.join(str(i) for i in missing), count))
    b64 = ''.join(seen[i]['data'] for i in range(count))
    if len(b64) != total:
        raise IngestError('the chunks add up to %d characters, expected %d' % (len(b64), total))
    try:
        packed = base64.b64decode(b64.encode('ascii'), validate=True)
        text = gzip.decompress(packed).decode('utf-8')
        return json.loads(text)
    except (binascii.Error, ValueError, OSError, EOFError, UnicodeError, zlib.error) as e:
        raise IngestError('the data is damaged and cannot be unpacked: %s' % e)


def check_collect(doc):
    """The collector's whole result: a finished run with a date, the outlets and the articles."""
    if not isinstance(doc, dict) or not isinstance(doc.get('articles'), list) or not isinstance(doc.get('outlets'), dict):
        raise IngestError('the result must be a dict with outlets and articles')
    if doc.get('phase') != 'done':
        raise IngestError('the collector had not finished (phase %r)' % doc.get('phase'))
    try:
        return common.check_date(doc.get('targetDate') or '')
    except common.UsageError as e:
        raise IngestError('the result has no usable targetDate: %s' % e)


def main(argv=None):
    common.setup_output()
    parser = argparse.ArgumentParser(description='Rebuild raw/collected.json of a new run from saved chunk files.')
    parser.add_argument('--expect-len', dest='expect_len', required=True, type=int,
                        help='the b64_len number that window.__collect.export() returned')
    parser.add_argument('files', nargs='+')
    args = parser.parse_args(argv)
    if args.expect_len < 1:
        return common.fail('usage error: --expect-len must be 1 or more', 2)
    try:
        doc = assemble(KIND, args.expect_len, args.files)
        date = check_collect(doc)
    except IngestError as e:
        return common.fail('ingest refused: %s' % e, 1)
    try:
        run = common.new_run(date)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    dest = common.write_json(run / 'raw' / 'collected.json', doc)
    common.write_run_info(run, date, status='ingested')
    print(json.dumps({'run': str(run), 'file': str(dest), 'date': date, 'articles': len(doc['articles']),
                      'requests': doc.get('requests'), 'stopped': doc.get('stopped')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
