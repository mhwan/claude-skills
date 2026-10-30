"""fiw.py check — collection gate C (C1–C8) over a run's collect files."""
from __future__ import annotations

import hashlib

from lib import cli, dates, gates, inclusion, layout, schema

from . import collect as collect_cmd

NOTICE_SOURCES = ('lawmaking_web', 'mfds_notice', 'ftc_notice', 'mafra_notice')
# Sources whose request total counts every record ever (no date filter on the server):
# received < total is expected there; truncation shows as a partial request instead.
UNFILTERED_TOTAL = ('foodsafety', 'lawmaking_web', 'assembly_bills', 'assembly_schedule')
STALE_MIN = 5


def register(sub):
    p = sub.add_parser('check', help='collection gate C over the collect files of a run')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the fiw.gate/1 JSON')
    p.set_defaults(func=main)


def item_key(source, item) -> str:
    if source == 'foodsafety':
        return f'{item.get("kind")}:{item.get("seq")}'
    if source == 'assembly_bills':
        return str(item.get('bill_id'))
    if source == 'assembly_schedule':
        return f'{item.get("date")}|{item.get("committee")}|{item.get("meeting")}'
    return str(item.get('id'))


def _item_day(item):
    return item.get('date') or item.get('posted')


def _ids_off_boundary(env, boundary) -> set:
    return {item_key(env['source'], it) for it in env['items'] if _item_day(it) not in boundary}


def previous_envelope(run_id, created_at, source):
    """The source's envelope of the latest earlier run in state/runs.json that has one."""
    runs_path = layout.state_dir() / 'runs.json'
    if not runs_path.exists():
        return None
    runs = schema.load(runs_path, 'fiw.state.runs/1')['runs']
    ids = [r['run_id'] for r in runs]
    earlier = runs[:ids.index(run_id)] if run_id in ids else [r for r in runs if r['created_at'] < created_at]
    for r in reversed(earlier):
        path = layout.collect_dir(layout.run_dir(r['run_id'])) / f'{source}.json'
        if path.is_file():
            try:
                return schema.load(path, 'fiw.collect/1')
            except schema.SchemaError:
                continue
    return None


_food_terms = {}


def non_food_rule(source, item):
    """The prefilter rule when the item is 'other' (non-food by keyword), else None.

    An unparsed comment period only blocks the run (C8 = 1) for items that may be food-related:
    'other' items are listed by title and link only, so their period is never shown.
    """
    from . import prefilter as prefilter_cmd      # prefilter imports this module
    if 'watch' not in _food_terms:
        try:
            _food_terms['watch'] = collect_cmd.load_watch()
        except (OSError, schema.SchemaError):
            _food_terms['watch'] = None
    watch = _food_terms['watch']
    if watch is None:
        return None
    verdict, rule = prefilter_cmd.item_verdict(source, item, watch['food_related'], watch['committees'])
    return rule if verdict == 'other' else None


def main(args) -> int:
    _food_terms.clear()
    rd, manifest = collect_cmd.load_run(args.run)
    findings = []
    severity = {'value': cli.EXIT_OK}

    def find(where, kind, detail, code):
        findings.append({'where': where, 'kind': kind, 'detail': detail})
        if code == cli.EXIT_FAIL or severity['value'] == cli.EXIT_FAIL:
            severity['value'] = cli.EXIT_FAIL
        else:
            severity['value'] = max(severity['value'], code)

    period = manifest['period']
    files = sorted(layout.collect_dir(rd).glob('*.json')) if layout.collect_dir(rd).is_dir() else []
    digest = hashlib.sha256()
    checked = 0
    present = set()
    for path in files:
        with open(path, 'rb') as f:
            digest.update(f.read())
        source = path.stem
        present.add(source)
        checked += 1
        try:
            env = schema.load(path, 'fiw.collect/1')
        except schema.SchemaError as e:
            find(source, 'C1', f'{e}' + (f' — {e.errors[0]}' if e.errors else ''), cli.EXIT_FAIL)
            continue
        mismatch = [k for k, want in (('source', source), ('run_id', manifest['run_id']),
                                      ('period', period)) if env[k] != want]
        if mismatch:
            find(source, 'C1', 'envelope does not match the run: ' + ', '.join(mismatch), cli.EXIT_FAIL)
            continue
        items = env['items']
        checked += len(items)

        if env['status'] == 'no_key':
            find(source, 'C7', 'no API key — the source was not read', cli.EXIT_WARN)
        if env['status'] == 'failed' or (env['probe'] is not None and not env['probe']['ok']):
            find(source, 'C2', f'collection failed: {env["error"] or "probe not ok"}', cli.EXIT_WARN)

        seen = set()
        for item in items:
            key = item_key(source, item)
            if not inclusion.included(env['inclusion'], item, period['from'], period['to'], manifest['today']):
                find(f'{source}/{key}', 'C3',
                     f'outside inclusion rule {env["inclusion"]} for {period["from"]}~{period["to"]}',
                     cli.EXIT_FAIL)
            if key in seen:
                find(f'{source}/{key}', 'C4', 'duplicate item id', cli.EXIT_FAIL)
            seen.add(key)
            if source in NOTICE_SOURCES and item.get('open_parse') == 'failed':
                other_rule = non_food_rule(source, item)
                if other_rule:
                    find(f'{source}/{key}', 'C8',
                         f'comment period not parsed — non-food by keyword ({other_rule}), '
                         'listed under 그 밖 without a period', cli.EXIT_WARN)
                else:
                    find(f'{source}/{key}', 'C8',
                         'comment period not parsed — fix the parser or enter the fragment by hand '
                         '(open_source: quote)', cli.EXIT_FAIL)

        for r in env['requests']:
            truncated = (r['total'] is not None and r['received'] < r['total']
                         and source not in UNFILTERED_TOTAL)
            if r['status'] != 'ok' or truncated:
                total = '?' if r['total'] is None else r['total']
                find(f'{source}/{r["target"]}', 'C6',
                     f'request {r["status"]}: received {r["received"]} of {total}', cli.EXIT_WARN)

        prev = previous_envelope(manifest['run_id'], manifest['created_at'], source)
        if prev is not None and prev['period'] != env['period']:
            boundary = {env['period']['from'], env['period']['to'], prev['period']['from'], prev['period']['to']}
            cur_ids, prev_ids = _ids_off_boundary(env, boundary), _ids_off_boundary(prev, boundary)
            if len(cur_ids) >= STALE_MIN and len(prev_ids) >= STALE_MIN and cur_ids == prev_ids:
                find(source, 'C5', f'same {len(cur_ids)} item ids as run {prev["run_id"]} '
                     f'although the periods differ (stale source?)', cli.EXIT_WARN)

    skipped = [s for s in collect_cmd.SOURCES if s not in present]
    if skipped:
        severity['value'] = cli.EXIT_FAIL if severity['value'] == cli.EXIT_FAIL else max(severity['value'], cli.EXIT_WARN)

    code = severity['value']
    classified = {}
    for f in findings:
        classified[f['kind']] = classified.get(f['kind'], 0) + 1
    gate = {'schema': 'fiw.gate/1', 'gate': 'check', 'exit': code, 'checked': checked,
            'findings': findings, 'classified': classified, 'skipped': skipped,
            'input_hash': digest.hexdigest() if files else None}
    layout.gates_dir(rd).mkdir(parents=True, exist_ok=True)
    schema.dump(layout.gates_dir(rd) / 'check.json', gate, 'fiw.gate/1')
    gates.record_stage(rd, manifest, 'check', code, gate['input_hash'])

    if args.json:
        cli.print_json(gate)
    else:
        for f in findings:
            print(f'[{f["kind"]}] {f["where"]} — {f["detail"]}')
        for s in skipped:
            print(f'[skipped] {s} — no collect file')
        print(f'check: {checked} examined, {len(findings)} findings, exit {code}')
    return code
