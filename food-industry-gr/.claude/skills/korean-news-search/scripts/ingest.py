#!/usr/bin/env python3
"""Rebuild the collector's output from the chunk files saved out of the browser tab.

Usage: ingest.py --run RUN_DIR --kind list|bodies --expect-len N FILE [FILE ...]

The browser cannot write files, so search_bigkinds.js hands its result over as text chunks
(gzip + base64, one chunk per tool call). The app saves each long answer to a file; this script puts
the chunks back together, checks them and writes raw/list.json or raw/bodies.json.
Nothing is written unless every check passes.
"""
import base64
import binascii
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

MAGIC = 'KNSCHUNK'
PAD_MARK = '|PAD|'
LIST_STATUSES = ('ok', 'empty', 'unread', 'failed')


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
    except (binascii.Error, ValueError, OSError, EOFError, UnicodeError) as e:
        raise IngestError('the data is damaged and cannot be unpacked: %s' % e)


def check_list(doc):
    if not isinstance(doc, dict) or doc.get('status') not in LIST_STATUSES or not isinstance(doc.get('items'), list):
        raise IngestError('the list has no known status (expected a dict with status %s and items)' % '/'.join(LIST_STATUSES))


def check_bodies(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get('bodies'), dict) or not isinstance(doc.get('failed', {}), dict):
        raise IngestError('the bodies file must be a dict with bodies and failed')


def merge_bodies(existing, new):
    bodies = dict(existing.get('bodies') or {})
    failed = dict(existing.get('failed') or {})
    bodies.update(new['bodies'])
    failed.update(new.get('failed') or {})
    for key in new['bodies']:
        failed.pop(key, None)
    for key in list(failed):
        if key in bodies:
            failed.pop(key)
    return {'bodies': bodies, 'failed': failed}


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Rebuild raw/list.json or raw/bodies.json from saved chunk files.')
    parser.add_argument('--run', required=True)
    parser.add_argument('--kind', required=True, choices=['list', 'bodies'])
    parser.add_argument('--expect-len', dest='expect_len', required=True, type=common.int_arg)
    parser.add_argument('files', nargs='+')
    args = parser.parse_args(argv)
    if args.expect_len < 1:
        return common.fail('usage error: --expect-len must be 1 or more', 2)
    try:
        run = common.resolve_run(args.run)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    try:
        doc = assemble(args.kind, args.expect_len, args.files)
        if args.kind == 'list':
            check_list(doc)
            out_path = run / 'raw' / 'list.json'
            result = {'kind': 'list', 'items': len(doc['items']), 'status': doc['status'], 'total': doc.get('total')}
        else:
            check_bodies(doc)
            out_path = run / 'raw' / 'bodies.json'
            if out_path.is_file():
                try:
                    existing = common.read_json(out_path)
                except ValueError:
                    existing = {}
                doc_out = merge_bodies(existing if isinstance(existing, dict) else {}, doc)
            else:
                doc_out = merge_bodies({}, doc)
            result = {'kind': 'bodies', 'items': len(doc['bodies']), 'failed': len(doc.get('failed') or {}),
                      'bodies_total': len(doc_out['bodies'])}
            doc = doc_out
    except IngestError as e:
        return common.fail('ingest refused: %s' % e, 1)
    common.write_json(out_path, doc)
    common.record_step(run, 'ingest')
    common.emit(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
