"""Command line of the assembly-bill-tracker skill.

  run-new   make a numbered run folder under runs/assembly-bill-tracker/
  ranges    split a date range in two (for a query that hit the 300-bill cap)
  keywords  decide which keywords to search (request first, then config/watch.json, else ask)
  merge     join the saved assembly_bills results and report capped queries that still need splitting
  targets   list which bills need an assembly_bill_detail call (cap per run, default 30)
  build     write the data envelope, the report and the new snapshot
  check     validate an envelope file

Exit codes: 0 done, 1 nothing usable or invalid, 2 wrong usage, 3 done but with something to look at.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import dates, envelope as env_lib, paths  # noqa: E402
import abt_core as core  # noqa: E402

EXIT_OK, EXIT_BAD, EXIT_USAGE, EXIT_NOTE = 0, 1, 2, 3


def say(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def fail(message):
    sys.stderr.write(message.rstrip() + '\n')
    return EXIT_USAGE


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('사용법 오류: %s\n' % message)
        raise SystemExit(EXIT_USAGE)


def positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('숫자가 필요합니다: %r' % text)
    if value < 0:
        raise argparse.ArgumentTypeError('0 이상의 숫자가 필요합니다: %r' % text)
    return value


def make_parser():
    parser = Parser(prog='abt.py', description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest='command', parser_class=Parser)
    sub.required = True

    p = sub.add_parser('run-new', allow_abbrev=False)
    p.add_argument('--today')

    p = sub.add_parser('ranges', allow_abbrev=False)
    p.add_argument('--from', dest='start', required=True)
    p.add_argument('--to', dest='end', required=True)

    p = sub.add_parser('keywords', allow_abbrev=False)
    p.add_argument('--keyword', action='append', default=[])

    p = sub.add_parser('merge', allow_abbrev=False)
    p.add_argument('--run', required=True)

    p = sub.add_parser('targets', allow_abbrev=False)
    p.add_argument('--run', required=True)
    p.add_argument('--today', required=True)
    p.add_argument('--detail-cap', type=positive_int, default=core.DEFAULT_DETAIL_CAP)

    p = sub.add_parser('build', allow_abbrev=False)
    p.add_argument('--run', required=True)
    p.add_argument('--from', dest='start', required=True)
    p.add_argument('--to', dest='end', required=True)
    p.add_argument('--today', required=True)
    p.add_argument('--keyword', action='append', default=[])
    p.add_argument('--committee')
    p.add_argument('--proposer')
    p.add_argument('--stall-days', type=positive_int, default=core.DEFAULT_STALL_DAYS)
    p.add_argument('--detail-cap', type=positive_int, default=core.DEFAULT_DETAIL_CAP)
    p.add_argument('--transcribed', action='store_true')
    p.add_argument('--no-save-state', action='store_true')

    p = sub.add_parser('check', allow_abbrev=False)
    p.add_argument('file')
    return parser


# --------------------------------------------------------------------------- commands

def cmd_run_new(args):
    today = args.today or dates.fmt(datetime.date.today())
    dates.parse_date(today)
    n = 1
    while True:
        folder = paths.run_dir(core.SKILL, today, n)
        try:
            folder.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
    (folder / 'raw').mkdir()
    (folder / 'work').mkdir()
    core.write_json(folder / 'run.json', {'skill': core.SKILL, 'created': dates.now_iso(), 'today': today})
    say({'run_dir': str(folder)})
    return EXIT_OK


def cmd_ranges(args):
    halves = core.split_range(args.start, args.end)
    say({'splittable': bool(halves), 'ranges': [list(h) for h in halves]})
    return EXIT_OK


def config_keywords():
    """Keywords from config/watch.json, read only; [] when the file or the keys are missing."""
    path = paths.config_path('watch')
    if not path.is_file():
        return []
    try:
        data = core.read_json(path)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    found = data.get('keywords')
    if not found and isinstance(data.get('food_related'), dict):
        found = data['food_related'].get('include')
    if not isinstance(found, list):
        return []
    return [k.strip() for k in found if isinstance(k, str) and k.strip()]


def cmd_keywords(args):
    asked = [k.strip() for k in args.keyword if k and k.strip()]
    if asked:
        say({'keywords': asked, 'source': 'request', 'ask': None})
        return EXIT_OK
    saved = config_keywords()
    if saved:
        say({'keywords': saved, 'source': 'config', 'ask': None})
        return EXIT_OK
    say({'keywords': [], 'source': 'none',
         'ask': '검색할 키워드를 알려 주세요. 요청에도 설정 파일(config/watch.json)에도 키워드가 없어 임의로 정하지 않았습니다.'})
    return EXIT_NOTE


def cmd_merge(args):
    run = Path(args.run)
    col = core.Collected(run)
    core.write_json(run / 'work' / 'bills.json', {'bills': col.bills, 'without_number': col.without_number})
    unread = [{'id': q['id'], 'error': err} for q, err in col.unread()]
    report = {'bills': len(col.bills), 'need_split': col.issues['need_split'],
              'unsplittable': col.issues['unsplittable'], 'missing_children': col.issues['missing'],
              'unread': unread}
    say(report)
    return EXIT_NOTE if (col.is_open() or unread) else EXIT_OK


def cmd_targets(args):
    run = Path(args.run)
    dates.parse_date(args.today)
    col = core.Collected(run)
    state, _ = core.load_state()
    good, _ = core.usable_bills(col.bills)
    chosen = core.select_targets(good, state, args.detail_cap)
    say({'cap': args.detail_cap, 'fetch': chosen['fetch'], 'over_cap': chosen['over_cap'], 'closed': chosen['closed']})
    return EXIT_OK


def cmd_build(args):
    keywords = [k.strip() for k in args.keyword if k and k.strip()]
    result = core.build(args.run, args.start, args.end, args.today, keywords, args.committee, args.proposer,
                        args.stall_days, args.detail_cap, args.transcribed, not args.no_save_state)
    code = result.pop('exit_code')
    say(result)
    return code


def cmd_check(args):
    try:
        env_lib.read_envelope(args.file)
    except env_lib.EnvelopeError as ex:
        sys.stderr.write('형식 오류:\n' + '\n'.join('- %s' % p for p in ex.problems) + '\n')
        return EXIT_BAD
    except (OSError, ValueError) as ex:
        sys.stderr.write('파일을 읽을 수 없습니다: %s\n' % ex)
        return EXIT_BAD
    say({'valid': True, 'file': args.file})
    return EXIT_OK


COMMANDS = {'run-new': cmd_run_new, 'ranges': cmd_ranges, 'keywords': cmd_keywords, 'merge': cmd_merge,
            'targets': cmd_targets, 'build': cmd_build, 'check': cmd_check}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except paths.RootNotFound as ex:
        return fail('결과 폴더를 찾을 수 없습니다: %s' % ex)
    except core.InputError as ex:
        return fail(str(ex))
    except dates.DateError as ex:
        return fail('날짜 형식이 올바르지 않습니다 (YYYY-MM-DD): %s' % ex)
    except (paths.InvalidName, ValueError) as ex:
        return fail('값이 올바르지 않습니다: %s' % ex)


if __name__ == '__main__':
    sys.exit(main())
