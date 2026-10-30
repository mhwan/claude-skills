#!/usr/bin/env python3
"""Lists food-safety records (recalls and sales stops, administrative actions, failed inspections)
from the public food-safety portal as a linked report and a gr.envelope/1 file.

Subcommands:
  run    [--from D] [--to D] [--topic T] [--max-pages N] [--replay DIR] [--today D]
         query the portal list pages (robots.txt is checked before every request), then the recall
         grade of each recall row, and write report + data + run folder
  check  --data FILE [--report FILE]
         re-check a written result against the skill rules

Without --from / --to the period is the last 14 days ending today (computer clock).
--replay DIR answers every request from recorded files in DIR instead of the network (used by the tests).

The portal's Open API host is never requested. No API key is needed. Standard library only.
Exit codes: 0 clean, 1 nothing could be read / check failed, 2 usage error, 3 look at the warnings.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import html
import json
import os
import re
import shutil
import unicodedata
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
sys.path.insert(0, str(HERE / 'lib'))

import dates  # noqa: E402
import envelope  # noqa: E402
import korean  # noqa: E402
import net  # noqa: E402
import paths  # noqa: E402

SKILL = 'food-safety-records'
VERSION = '0.1.0'
DEFAULT_DAYS = 14
MAX_PAGES = 30
ADMIN_REASON_MAX = 200
ELLIPSIS = '…'
NEEDS_CHECK = '확인 필요'
PERSONAL_MARK = '(개인, 이름 가림)'
TEMPLATE = SKILL_DIR / 'assets' / 'report-template.md'

HOST = 'https://www.foodsafetykorea.go.kr'
LIST_URL = HOST + '/portal/fooddanger/{endpoint}.do'
DETAIL_URLS = {
    'recall': HOST + '/layer/suspensionDetail.do?search_keyword={seq}',
    'admin': HOST + '/layer/administMeasureDetail.do?seq={seq}',
    'unfit': HOST + '/layer/testUnfitDetail.do?rtrvldsuse_seq={seq}',
}

# kind -> list endpoint, menu_no, page size, label
KINDS = {
    'recall': ('searchSuspensionList', '4408', 60, '회수·판매중지'),
    'admin': ('searchAdministMeasureList', '4411', 60, '행정처분'),
    'unfit': ('searchTestUnfitDomList', '4409', 50, '검사 부적합'),
}
SOURCES = {'recall': 'foodsafety_recall', 'admin': 'foodsafety_admin', 'unfit': 'foodsafety_unfit'}
BASIS = {'recall': '등록일', 'admin': '처분일(처분확정일자)', 'unfit': '등록일'}

# kind -> output field -> response key (None = always null). Allow-list: nothing else is copied.
# The raw rows also carry officials' names, IDs, phone numbers and representatives' names; those never
# leave this module.
FIELDS = {
    'recall': {'seq': 'rtrvldsuse_seq', 'company': 'bsshnm', 'product': 'prdtnm',
               'reason': 'rtrvlprvns', 'action': None, 'date': 'hmpgpblict_prcsdtm'},
    'admin': {'seq': 'dspsdtls_seq', 'company': 'prcscitypoint_bsshnm', 'product': None,
              'reason': 'viltcn', 'action': 'dsps_typecd_nm', 'date': 'dsps_dcsndt'},
    'unfit': {'seq': 'rtrvldsuse_seq', 'company': 'bsshnm', 'product': 'prdtnm',
              'reason': 'offense_detail', 'action': None, 'date': 'cret_dtm'},
}
ROW_KEYS = ('id', 'title', 'date', 'url', 'kind', 'company', 'product', 'reason', 'grade', 'action')

_GRADE_RE = re.compile(r'<dt>\s*회수등급\s*</dt>\s*<dd>([^<]+)</dd>')
_TAG_RE = re.compile(r'</?[A-Za-z][^>]*>')
_SEQ_RE = re.compile(r'^[0-9A-Za-z_-]{1,40}$')
_PHONE_RE = re.compile(r'(?<!\d)0\d{1,2}-\d{3,4}-\d{4}(?!\d)')


class FetchFailed(Exception):
    """A request did not give a usable answer. `stop_all` means: make no further requests."""

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def stop_all(self):
        return (self.kind in ('blocked', 'offline')
                or (self.kind == 'http' and self.status is not None and 400 <= self.status < 500))


class StructureError(Exception):
    """The portal answered, but not in the expected shape."""


# ------------------------------------------------------------------------------ small helpers

def utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


def clean(text):
    """Unescape entities, drop tags such as <br>, collapse whitespace."""
    text = html.unescape(unicodedata.normalize('NFC', str(text)))
    text = _TAG_RE.sub(' ', text)
    return ' '.join(text.split())


def cell(text):
    return ' '.join(str(text).split()).replace('|', '/')


def parse_day(value):
    try:
        return dates.parse_date(value)
    except dates.DateError as exc:
        raise argparse.ArgumentTypeError(str(exc))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write('%s: error: %s\n' % (self.prog, message))
        raise SystemExit(2)


# ------------------------------------------------------------------------------ portal

def form(kind, page):
    endpoint, menu_no, show_cnt, _ = KINDS[kind]
    return {'menu_no': menu_no, 'menu_grp': 'MENU_NEW02', 'start_idx': str(page),
            'search_type': '01', 'search_keyword': '', 'show_cnt': str(show_cnt)}


class Fetcher:
    """One session for the whole run plus a log of the data requests (no response bodies)."""

    def __init__(self, session):
        self.session = session
        self.log = []

    def text(self, url, data=None, note=''):
        self.log.append({'method': 'POST' if data is not None else 'GET', 'url': url, 'note': note})
        try:
            resp = self.session.post(url, data) if data is not None else self.session.get(url)
        except net.Blocked as exc:
            raise FetchFailed('blocked', 'robots.txt 또는 정책이 막음 (%s)' % exc.reason) from None
        except net.OfflineError:
            raise FetchFailed('offline', '오프라인 모드라 요청하지 않음') from None
        if resp.status == 0:
            raise FetchFailed('network', '응답 없음 (%s)' % (resp.error or '원인 불명'))
        if resp.status != 200:
            raise FetchFailed('http', 'HTTP %d' % resp.status, resp.status)
        return resp.text()


def parse_page(text):
    """(rows, total) of one list response. Recall/unfit: {total_cnt, list}; admin: a bare list."""
    try:
        data = json.loads(text)
    except ValueError:
        raise StructureError('목록 응답이 JSON이 아님') from None
    if isinstance(data, dict):
        rows, total = data.get('list'), data.get('total_cnt')
        if not isinstance(rows, list) or total is None:
            raise StructureError('목록 응답에 list / total_cnt 가 없음')
    elif isinstance(data, list):
        rows = data
        total = rows[0].get('total_count') if rows and isinstance(rows[0], dict) else 0
    else:
        raise StructureError('목록 응답이 객체도 목록도 아님')
    if not all(isinstance(r, dict) for r in rows):
        raise StructureError('목록 행이 객체가 아님')
    try:
        total = int(total)
    except (TypeError, ValueError):
        raise StructureError('전체 건수가 숫자가 아님') from None
    return rows, total


def parse_grade(doc):
    """Recall grade from the recall detail layer, or None."""
    m = _GRADE_RE.search(doc or '')
    if not m:
        return None
    return clean(m.group(1)) or None


def _value(row, key):
    if key is None:
        return None
    v = row.get(key)
    if v is None:
        return None
    return clean(v) or None


def _row_date(kind, row):
    raw = row.get(FIELDS[kind]['date'])
    raw = '' if raw is None else str(raw).strip()
    return dates.parse_loose(raw) or (dates.parse_loose(raw.split()[0]) if raw.split() else None)


def map_row(kind, row):
    """Allow-list mapping of one raw row (the grade is filled in later for recalls)."""
    f = FIELDS[kind]
    seq = _value(row, f['seq'])
    day = _row_date(kind, row)
    if seq is None or day is None or not _SEQ_RE.match(seq):
        raise StructureError('%s 행에 %s 또는 읽을 수 있는 %s 가 없음' % (KINDS[kind][3], f['seq'], f['date']))
    company = _value(row, f['company']) or NEEDS_CHECK
    product = _value(row, f['product'])
    reason = _value(row, f['reason'])
    if kind == 'admin' and reason and len(reason) > ADMIN_REASON_MAX:
        reason = reason[:ADMIN_REASON_MAX].rstrip() + ELLIPSIS
    return {'id': '%s-%s' % (kind, seq), 'title': ' '.join(x for x in (company, product) if x),
            'date': dates.fmt(day), 'url': DETAIL_URLS[kind].format(seq=seq), 'kind': kind,
            'company': company, 'product': product, 'reason': reason, 'grade': None,
            'action': _value(row, f['action']), '_seq': seq}


def collect_kind(fetcher, kind, frm, to, max_pages):
    """-> (source dict, rows, warnings, stop reason or None) for one kind of record."""
    endpoint, _, show_cnt, label = KINDS[kind]
    url = LIST_URL.format(endpoint=endpoint)
    first, last = dates.fmt(frm), dates.fmt(to)
    before = fetcher.session.requests_made
    rows, warnings, seen_all, in_period = [], [], set(), set()
    problem = None            # (status, text) when page 1 is unusable
    incomplete = None         # text when a later page is missing
    stop = None
    received, ended = 0, False
    for page in range(1, max_pages + 1):
        try:
            raw, total = parse_page(fetcher.text(url, form(kind, page), note='%s page %d' % (kind, page)))
            items = [map_row(kind, r) for r in raw]
        except (FetchFailed, StructureError) as exc:
            status = 'failed' if isinstance(exc, FetchFailed) else 'unread'
            if isinstance(exc, FetchFailed) and exc.stop_all:
                stop = '%s 목록 %d쪽: %s' % (label, page, exc)
            if page == 1:
                problem = (status, '%s 목록 1쪽: %s' % (label, exc))
            else:
                incomplete = '%s 목록 %d쪽을 읽지 못함 (%s) - 앞쪽만 보임' % (label, page, exc)
            break
        if page == 1 and not raw:
            problem = ('unread', '%s 목록 1쪽에 기록이 한 건도 없음 - 포털 구조가 바뀌었는지 확인 필요' % label)
            break
        received += len(raw)
        fresh = [it for it in items if it['_seq'] not in seen_all]
        if items and not fresh:
            incomplete = '%s 목록 %d쪽이 앞쪽과 같은 기록만 담고 있어 멈춤' % (label, page)
            break
        old = False
        for it in items:
            seen_all.add(it['_seq'])
            if it['date'] < first:
                old = True
            if first <= it['date'] <= last and it['_seq'] not in in_period:
                in_period.add(it['_seq'])
                rows.append(it)
        if old or not raw or received >= total:
            ended = True
            break
    else:
        pass
    if problem is None and incomplete is None and not ended:
        incomplete = '%s 목록을 %d쪽까지만 읽음 - 더 오래된 기록이 남았을 수 있음' % (label, max_pages)
    source = {'source': SOURCES[kind], 'status': 'ok', 'requests': fetcher.session.requests_made - before}
    if problem:
        source['status'], source['error'] = problem
        rows = []
    elif incomplete:
        source['error'] = incomplete
        if rows:
            warnings.append(incomplete)
        else:
            source['status'] = 'unread'
    elif not rows:
        source['status'] = 'empty'
    for it in rows:
        it.pop('_seq', None)
    return source, rows, warnings, stop


def fill_grades(fetcher, rows):
    """Reads the grade of every recall row. Any failed request stops the remaining grade requests."""
    recall = [r for r in rows if r['kind'] == 'recall']
    unread, stopped = 0, None
    for r in recall:
        if stopped:
            unread += 1
            continue
        seq = r['id'].split('-', 1)[1]
        try:
            r['grade'] = parse_grade(fetcher.text(DETAIL_URLS['recall'].format(seq=seq), note='grade'))
        except FetchFailed as exc:
            stopped = str(exc)
            unread += 1
            continue
        if r['grade'] is None:
            unread += 1
    warnings = []
    if unread:
        text = '회수 등급: 회수·판매중지 %d건 중 %d건의 등급을 읽지 못함 - 등급 확인 필요' % (len(recall), unread)
        if stopped:
            text += ' (요청 중단: %s)' % stopped
        warnings.append(text)
    return len(recall), unread, warnings


# ------------------------------------------------------------------------------ watch list

def load_watch():
    """-> (companies or None when there is no list, warning or None)."""
    try:
        path = paths.config_path('watch', skill_dir=SKILL_DIR, env=os.environ)
    except (paths.RootNotFound, paths.InvalidName):
        return None, None
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, '추적 회사 목록(config/watch.json)을 읽지 못해 ★ 강조를 하지 않음'
    companies = data.get('companies') if isinstance(data, dict) else None
    if not isinstance(companies, list):
        return None, '추적 회사 목록(config/watch.json)에 companies 목록이 없어 ★ 강조를 하지 않음'
    good = [c for c in companies if isinstance(c, dict) and isinstance(c.get('name'), str) and c['name']]
    warning = None
    if len(good) != len(companies):
        warning = '추적 회사 목록에서 형식이 맞지 않는 항목 %d개를 건너뜀' % (len(companies) - len(good))
    return good, warning


def tracked_names(rows, companies):
    """row id -> name of the tracked company that matches the company or product text."""
    out = {}
    for r in rows:
        for entity in companies or []:
            try:
                hit = korean.match_entity(r['company'], entity) or korean.match_entity(r['product'] or '', entity)
            except (KeyError, TypeError):
                hit = False
            if hit:
                out[r['id']] = entity['name']
                break
    return out


# ------------------------------------------------------------------------------ report

def read_template():
    try:
        return TEMPLATE.read_text(encoding='utf-8')
    except OSError:
        raise SystemExit('report template missing: %s' % TEMPLATE)


def link(url):
    return '[원문](%s)' % url


def is_personal(company):
    return '*' in company or '○' in company


def company_cell(r, tracked):
    text = cell(r['company'])
    if is_personal(r['company']):
        text += ' ' + PERSONAL_MARK
    return ('★ ' if r['id'] in tracked else '') + text


def kind_cell(r):
    if r['kind'] == 'recall':
        return '회수 (%s)' % (cell(r['grade']) if r['grade'] else '등급 확인 필요')
    return '처분' if r['kind'] == 'admin' else '부적합'


def section_note(source, label):
    status = source['status'] if source else None
    if status in ('unread', 'failed', 'no_key', None):
        why = ' (%s)' % source['error'] if source and source.get('error') else ''
        return '%s: 읽지 못함 - 확인 필요%s.' % (label, why)
    return '%s: 이번 조회에서는 이 기간 기록이 보이지 않음 (없다는 뜻이 아님).' % label


def render_table(kind, rows, source, tracked):
    label = KINDS[kind][3]
    if not rows:
        return section_note(source, label)
    rows = sorted(rows, key=lambda r: (r['date'], r['id']), reverse=True)
    if kind == 'admin':
        lines = ['| 구분 | 업체 | 제품 | 사유 | 처분 | 일자 | 링크 |', '|---|---|---|---|---|---|---|']
    else:
        lines = ['| 구분 | 업체 | 제품 | 사유 | 일자 | 링크 |', '|---|---|---|---|---|---|']
    for r in rows:
        cols = [kind_cell(r), company_cell(r, tracked), cell(r['product'] or NEEDS_CHECK),
                cell(r['reason'] or NEEDS_CHECK)]
        if kind == 'admin':
            cols.append(cell(r['action'] or NEEDS_CHECK))
        cols += [r['date'], link(r['url'])]
        lines.append('| ' + ' | '.join(cols) + ' |')
    return '\n'.join(lines)


def render_report(env, created, companies, tracked, grade_unread):
    by_source = {s['source']: s for s in env['sources']}
    rows = env['rows']
    tables = {k: render_table(k, [r for r in rows if r['kind'] == k], by_source.get(SOURCES[k]), tracked)
              for k in KINDS}
    parts = []
    for k in KINDS:
        s = by_source.get(SOURCES[k])
        text = '%s %s' % (KINDS[k][3], s['status'] if s else '-')
        if s and s.get('error') and s['status'] == 'ok':
            text += ' (일부만 읽음)'
        parts.append(text)
    basis = ', '.join('%s = %s' % (KINDS[k][3], BASIS[k]) for k in KINDS)

    if companies is None:
        star_note = '추적 회사 목록이 없어 ★ 강조는 하지 않았습니다.'
        tracked_line = '- 추적 회사 목록 없음 — 강조 안 함.'
    else:
        star_note = '업체 이름 앞의 ★ 은 추적 회사 목록(감시 목록)에 해당한다는 뜻입니다.'
        counts = {}
        for name in tracked.values():
            counts[name] = counts.get(name, 0) + 1
        detail = ', '.join('%s %d건' % (n, c) for n, c in sorted(counts.items()))
        tracked_line = '- 추적 회사에 해당하는 기록: 이 기간 %d건%s.' % (len(tracked), ' (%s)' % detail if detail else '')

    checks = ['- %s' % w for w in env['warnings']]
    checks += ['- 이 표는 식품안전나라에 올라온 기록을 그대로 옮긴 목록입니다. 위해 평가나 소비자 행동 안내는 담지 않습니다.',
               tracked_line,
               '- 회수 등급을 못 읽은 건: %d건 (읽지 못하면 "등급 확인 필요"로 적습니다).' % grade_unread,
               '- 행정처분 사유는 %d자까지만 가져오므로 줄임 표시(%s)가 있으면 원문 링크에서 전체를 확인해야 합니다.'
               % (ADMIN_REASON_MAX, ELLIPSIS),
               '- 제품명이 없는 기록은 "%s"로 둡니다. 개인 이름은 가려진 그대로 두었습니다.' % NEEDS_CHECK]
    text = read_template()
    for key, value in (('skill', SKILL), ('period', env['period']), ('created', created), ('version', VERSION),
                       ('status', env['status']), ('sources', ' · '.join(parts)), ('basis', basis),
                       ('star_note', star_note), ('recall_table', tables['recall']),
                       ('admin_table', tables['admin']), ('unfit_table', tables['unfit']),
                       ('checks', '\n'.join(checks))):
        text = text.replace('{{%s}}' % key, value)
    return re.sub(r'\n{3,}', '\n\n', text).rstrip() + '\n'


# ------------------------------------------------------------------------------ files

def p_report(name):
    return paths.report_path(SKILL, name, skill_dir=SKILL_DIR, env=os.environ)


def p_data(name):
    return paths.data_path(SKILL, name, skill_dir=SKILL_DIR, env=os.environ)


def p_history(name, n):
    return paths.history_path(SKILL, name, n, skill_dir=SKILL_DIR, env=os.environ)


def p_run(day, n):
    return paths.run_dir(SKILL, day, n, skill_dir=SKILL_DIR, env=os.environ)


def write_text_atomic(path, text):
    path = Path(path)
    paths.ensure(path, parent=True)
    tmp = path.with_name('.%s.tmp' % path.name)
    with open(str(tmp), 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


def move_to_history(report):
    if not report.exists():
        return None
    name = report.stem
    n = 1
    while p_history(name, n).exists():
        n += 1
    target = p_history(name, n)
    paths.ensure(target, parent=True)
    shutil.move(str(report), str(target))
    return target


def new_run_dir(today):
    n = 1
    while p_run(today, n).exists():
        n += 1
    return paths.ensure(p_run(today, n))


# ------------------------------------------------------------------------------ replay

def make_replay_stub(folder):
    """Answers requests from recorded files: robots.txt, <kind>-p<page>.json, grade-<seq>.html.

    A file NAME.status holding a number forces that HTTP status for the request. Every request is
    appended to _seen.log in the folder. A missing file answers 404; another host answers "no response".
    """
    folder = Path(folder)
    endpoints = {v[0]: k for k, v in KINDS.items()}

    def seen(line):
        with open(str(folder / '_seen.log'), 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(line + '\n')

    def reply(url, status, body=b'', ctype='application/json; charset=utf-8'):
        return net.Response(url=url, status=status, headers={'content-type': ctype}, body=body)

    def stub(url, method, data, headers):
        seen('%s %s %s' % (method, url, json.dumps(data, ensure_ascii=False, sort_keys=True) if data else ''))
        parts = urllib.parse.urlsplit(url)
        if ('%s://%s' % (parts.scheme, parts.netloc)).lower() != HOST:
            return net.Response(url=url, status=0, error='replay: host is not recorded')
        name = None
        if parts.path == '/robots.txt':
            name, ctype = 'robots.txt', 'text/plain; charset=utf-8'
        elif method == 'POST' and parts.path.startswith('/portal/fooddanger/') and isinstance(data, dict):
            kind = endpoints.get(parts.path.rsplit('/', 1)[-1][:-3])
            page = str(data.get('start_idx', ''))
            if kind and page.isdigit():
                name, ctype = '%s-p%s.json' % (kind, page), 'application/json; charset=utf-8'
        elif method == 'GET' and parts.path == '/layer/suspensionDetail.do':
            seq = urllib.parse.parse_qs(parts.query).get('search_keyword', [''])[0]
            if _SEQ_RE.match(seq):
                name, ctype = 'grade-%s.html' % seq, 'text/html; charset=utf-8'
        if name is None:
            return reply(url, 404)
        forced = folder / (name + '.status')
        if forced.is_file():
            return reply(url, int(forced.read_text(encoding='utf-8').strip()))
        target = folder / name
        if not target.is_file():
            return reply(url, 404)
        return reply(url, 200, target.read_bytes(), ctype)

    return stub


# ------------------------------------------------------------------------------ commands

def resolve_period(args, parser):
    today = args.today or date.today()
    to = args.to or today
    frm = args.frm or (to - timedelta(days=DEFAULT_DAYS - 1))
    try:
        dates.check_range(frm, to, today)
    except dates.DateError as exc:
        parser.error('bad period: %s' % exc)
    return frm, to, today


def cmd_run(args, parser):
    frm, to, today = resolve_period(args, parser)
    if args.max_pages < 1:
        parser.error('--max-pages must be at least 1')
    period = '%s~%s' % (frm.isoformat(), to.isoformat())
    try:
        name = paths.make_name(period=(frm, to), topic=args.topic)
        paths.resolve_root(SKILL_DIR, os.environ)
    except paths.InvalidName as exc:
        parser.error(str(exc))
    except paths.RootNotFound as exc:
        sys.stderr.write('%s\n' % exc)
        return 1
    if args.replay and not Path(args.replay).is_dir():
        parser.error('--replay folder not found: %s' % args.replay)

    companies, watch_warning = load_watch()
    fetcher = Fetcher(net.Session(interval=0 if args.replay else 0.5))
    sources, rows, warnings = [], [], []
    if args.replay:
        warnings.append('녹화해 둔 입력으로 만든 결과이며 실제 포털을 조회하지 않음')
    if watch_warning:
        warnings.append(watch_warning)

    def build(stub_in_place):
        stop = None
        for kind in KINDS:
            if stop:
                sources.append({'source': SOURCES[kind], 'status': 'unread', 'requests': 0,
                                'error': '요청하지 않음 - 앞선 요청에서 멈춤 (%s)' % stop})
                continue
            src, got, warns, stop = collect_kind(fetcher, kind, frm, to, args.max_pages)
            sources.append(src)
            rows.extend(got)
            warnings.extend(warns)
            if stop:
                warnings.append('요청 중단: %s - 우회하지 않고 멈춤' % stop)
        grade = (0, 0)
        if not stop:
            before = fetcher.session.requests_made
            total, unread, warns = fill_grades(fetcher, rows)
            sources[0]['requests'] += fetcher.session.requests_made - before
            warnings.extend(warns)
            grade = (total, unread)
        elif any(r['kind'] == 'recall' for r in rows):
            recall = sum(1 for r in rows if r['kind'] == 'recall')
            warnings.append('회수 등급: 요청을 멈춰 회수·판매중지 %d건의 등급을 읽지 못함 - 등급 확인 필요' % recall)
            grade = (recall, recall)
        return grade

    if args.replay:
        with net.stubbed(make_replay_stub(args.replay)):
            grade = build(True)
    else:
        grade = build(False)

    tracked = tracked_names(rows, companies)
    env = {'schema': envelope.SCHEMA, 'skill': SKILL, 'period': period,
           'status': envelope.summarize_status(sources), 'sources': sources,
           'rows': [{k: r[k] for k in ROW_KEYS} for r in rows], 'warnings': warnings}
    try:
        envelope.assert_valid(env)
    except envelope.EnvelopeError as exc:
        sys.stderr.write('internal error, result not written: %s\n' % exc)
        return 1

    created = dates.now_iso()
    run = new_run_dir(today)
    run_info = {'skill': SKILL, 'version': VERSION, 'created': created, 'period': period,
                'status': env['status'], 'replay': bool(args.replay), 'requests': fetcher.log,
                'sources': sources}
    write_text_atomic(run / 'run.json', json.dumps(run_info, ensure_ascii=False, indent=2) + '\n')

    print('status: %s' % env['status'])
    print('rows: %d' % len(env['rows']))
    for kind in KINDS:
        print('%s: %d' % (kind, sum(1 for r in env['rows'] if r['kind'] == kind)))
    print('requests: %d' % fetcher.session.requests_made)
    if env['status'] in ('unread', 'failed', 'no_key'):
        print('nothing could be read - the previous report (if any) was left in place')
        print('run: %s' % run)
    else:
        report = p_report(name)
        move_to_history(report)
        write_text_atomic(report, render_report(env, created, companies, tracked, grade[1]))
        data = envelope.write_envelope(p_data(name), env)
        print('report: %s' % report)
        print('data: %s' % data)
        print('run: %s' % run)
    print('warnings: %d' % len(env['warnings']))
    for w in env['warnings']:
        print('- %s' % w)
    return envelope.exit_code(env)


def check_problems(env, report_text):
    problems = []
    rows = env.get('rows') if isinstance(env, dict) else None
    ids = [r.get('id') if isinstance(r, dict) else None for r in (rows if isinstance(rows, list) else [])]
    for p in envelope.validate(env):
        m = re.match(r'rows\[(\d+)\]', p)
        if m and int(m.group(1)) < len(ids):
            p = 'row %s%s' % (ids[int(m.group(1))], p[m.end():])
        problems.append(p)
    if not isinstance(rows, list):
        return problems
    names = [s.get('source') for s in env.get('sources', []) if isinstance(s, dict)]
    if sorted(names) != sorted(SOURCES.values()):
        problems.append('sources must be exactly %s' % ', '.join(sorted(SOURCES.values())))
    first = last = None
    m = re.match(r'^(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$', str(env.get('period')))
    if m:
        try:
            first, last = dates.parse_date(m.group(1)), dates.parse_date(m.group(2))
        except dates.DateError:
            pass
    seen = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid, kind = r.get('id'), r.get('kind')
        if kind not in KINDS:
            problems.append('%s: unknown kind %r' % (rid, kind))
            continue
        if not (isinstance(rid, str) and rid.startswith(kind + '-')):
            problems.append('%s: id must start with "%s-"' % (rid, kind))
        if rid in seen:
            problems.append('%s: listed twice' % rid)
        seen.add(rid)
        url = r.get('url')
        if not (isinstance(url, str) and url.startswith(HOST + '/')):
            problems.append('%s: link does not point to the food-safety portal' % rid)
        if first:
            try:
                if not first <= dates.parse_date(r.get('date')) <= last:
                    problems.append('%s: date %s is outside the period' % (rid, r.get('date')))
            except dates.DateError:
                pass
        if kind != 'recall' and r.get('grade') is not None:
            problems.append('%s: only recall rows carry a grade' % rid)
        if kind != 'admin' and r.get('action') is not None:
            problems.append('%s: only administrative-action rows carry an action' % rid)
        if kind == 'admin' and r.get('product') is not None:
            problems.append('%s: administrative-action rows carry no product field' % rid)
        company, title = r.get('company'), r.get('title')
        if not (isinstance(company, str) and company):
            problems.append('%s: company is empty' % rid)
        elif isinstance(title, str) and not title.startswith(company):
            problems.append('%s: title does not start with the company' % rid)
        reason = r.get('reason')
        if isinstance(reason, str):
            if _TAG_RE.search(reason) or '&' in reason and html.unescape(reason) != reason:
                problems.append('%s: reason still holds markup' % rid)
            if kind == 'admin' and len(reason) > ADMIN_REASON_MAX + len(ELLIPSIS):
                problems.append('%s: administrative-action reason has %d chars (max %d)'
                                % (rid, len(reason), ADMIN_REASON_MAX))
        blob = json.dumps(r, ensure_ascii=False)
        if _PHONE_RE.search(blob):
            problems.append('%s: a phone-like number is in the row' % rid)
    if report_text is not None:
        if _PHONE_RE.search(report_text):
            problems.append('report holds a phone-like number')
        for r in rows:
            if not isinstance(r, dict) or r.get('kind') not in KINDS:
                continue
            rid = r.get('id')
            if r.get('url') and r['url'] not in report_text:
                problems.append('%s: its link is missing in the report' % rid)
            if isinstance(r.get('company'), str) and cell(r['company']) not in report_text:
                problems.append('%s: its company is missing in the report' % rid)
            if isinstance(r.get('company'), str) and is_personal(r['company']) and PERSONAL_MARK not in report_text:
                problems.append('%s: masked individual without the mark %s' % (rid, PERSONAL_MARK))
            if r.get('kind') == 'recall' and not r.get('grade') and '등급 확인 필요' not in report_text:
                problems.append('%s: unread grade is not marked in the report' % rid)
        for s in env.get('sources', []):
            if isinstance(s, dict) and s.get('status') in ('unread', 'failed', 'no_key') and '읽지 못함' not in report_text:
                problems.append('%s: unread source is not mentioned in the report' % s.get('source'))
    return problems


def cmd_check(args, parser):
    try:
        env = json.loads(Path(args.data).read_text(encoding='utf-8'))
    except OSError as exc:
        sys.stderr.write('cannot read %s (%s)\n' % (args.data, exc.strerror))
        return 2
    except ValueError:
        env = None
    report_text = None
    if args.report:
        try:
            report_text = Path(args.report).read_text(encoding='utf-8')
        except OSError as exc:
            sys.stderr.write('cannot read %s (%s)\n' % (args.report, exc.strerror))
            return 2
    problems = check_problems(env, report_text) if env is not None else ['data file is not valid JSON']
    print('problems: %d' % len(problems))
    for p in problems:
        print('- %s' % p)
    return 1 if problems else 0


def make_parser():
    top = Parser(prog='records.py', description='food-safety-records helper', allow_abbrev=False)
    sub = top.add_subparsers(dest='cmd', parser_class=Parser)
    sub.required = True

    run = sub.add_parser('run', allow_abbrev=False)
    run.add_argument('--from', dest='frm', type=parse_day)
    run.add_argument('--to', type=parse_day)
    run.add_argument('--topic')
    run.add_argument('--max-pages', type=int, default=MAX_PAGES)
    run.add_argument('--replay', metavar='DIR')
    run.add_argument('--today', type=parse_day, help='override the computer clock date (tests)')

    check = sub.add_parser('check', allow_abbrev=False)
    check.add_argument('--data', required=True)
    check.add_argument('--report')
    return top, sub


def main(argv=None):
    utf8_console()
    top, sub = make_parser()
    args = top.parse_args(argv)
    if args.cmd == 'run':
        return cmd_run(args, sub.choices['run'])
    return cmd_check(args, sub.choices['check'])


if __name__ == '__main__':
    sys.exit(main())
