#!/usr/bin/env python3
"""Prepare a news search run: check the request, create the run folder and write the work order.

Usage: run_search.py prepare --query TEXT --start YYYY-MM-DD --end YYYY-MM-DD [--outlets A,B] [--summary-from N]

The browser step (search_bigkinds.js) and the other scripts read the work order this writes.
"""
import datetime
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from lib import dates, paths  # noqa: E402

# Outlets whose BigKinds provider code was seen in earlier work (the code can be sent to the server).
# Any other outlet is matched by name after the search.
KNOWN_PROVIDER_CODES = {
    '조선일보': '01100801',
    '중앙일보': '01100901',
    '동아일보': '01100401',
    '한겨레': '01101001',
    '경향신문': '01100101',
    '매일경제': '02100101',
    '한국경제': '02100601',
}


def split_outlets(text):
    names = []
    for part in (text or '').split(','):
        name = unicodedata.normalize('NFC', part).strip()
        if name and name not in names:
            names.append(name)
    return names


def outlet_filters(names):
    """(provider codes for the server, names to filter by after the search)."""
    if not names:
        return [], []
    if all(n in KNOWN_PROVIDER_CODES for n in names):
        return [KNOWN_PROVIDER_CODES[n] for n in names], []
    # One unknown name: ask for everything and filter by name, or the known names would hide the unknown one.
    return [], list(names)


def next_run_dir(base, day):
    """Create runs/<skill>/<day>-N for the next free N and return it."""
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = base / ('%s-%d' % (day, n))
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            n += 1


def prepare(args):
    query = unicodedata.normalize('NFC', args.query or '')
    if not query.strip():
        raise common.UsageError('--query is empty')
    try:
        start = dates.parse_date(args.start)
        end = dates.parse_date(args.end)
        today = dates.parse_date(args.today) if args.today else datetime.date.today()
        dates.check_range(start, end, today)
    except dates.DateError as e:
        if str(e) == 'future':
            raise common.UsageError('the end date %s is in the future (today is %s)' % (args.end, today))
        raise common.UsageError(str(e))
    codes, post_filter = outlet_filters(split_outlets(args.outlets))
    base = common.runs_base()
    run = next_run_dir(base, dates.fmt(today))
    try:
        (run / 'raw').mkdir()
        (run / 'work').mkdir()
        common.write_json(run / 'work' / 'job.json', {
            'query': query,
            'start': dates.fmt(start),
            'end': dates.fmt(end),
            'provider_codes': codes,
            'post_filter': post_filter,
            'caps': dict(common.CAPS),
            'summary_from': args.summary_from,
        })
        common.write_json(run / 'run.json', {
            'skill': common.SKILL,
            'skill_version': common.VERSION,
            'skill_dir': str(common.SKILL_DIR),
            'transcribed': False,
            'created': dates.now_iso(),
            'request': {'query': query, 'start': dates.fmt(start), 'end': dates.fmt(end)},
            'steps': ['prepare'],
        })
    except Exception:
        # leave no half-made run folder behind
        import shutil
        shutil.rmtree(str(run), ignore_errors=True)
        raise
    out = {'run_dir': str(run), 'collector': str(common.SCRIPTS_DIR / 'search_bigkinds.js')}
    if '|' in query:
        # seen on the real site (2026-10-29): '|' is not OR there and the answer is 0 articles
        out['warnings'] = ["the query has '|': on BigKinds it is not OR (seen: 0 articles). Use 'OR' or ',' between words; "
                           "an empty answer to this query is not proof that there are no articles"]
    common.emit(out)
    return 0


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Prepare a BigKinds news search run.')
    sub = parser.add_subparsers(dest='command')
    sub.required = True
    p = sub.add_parser('prepare', help='check the request and create the run folder', allow_abbrev=False)
    p.add_argument('--query', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--outlets', default='')
    p.add_argument('--summary-from', dest='summary_from', type=common.int_arg, default=0)
    p.add_argument('--today', default=None, help='override today (YYYY-MM-DD); for tests')
    args = parser.parse_args(argv)
    try:
        return prepare(args)
    except (common.UsageError, paths.InvalidName) as e:
        return common.fail('usage error: %s' % e, 2)


if __name__ == '__main__':
    sys.exit(main())
