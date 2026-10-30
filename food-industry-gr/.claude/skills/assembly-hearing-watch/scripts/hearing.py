#!/usr/bin/env python3
"""Turns saved National Assembly tool results into a linked hearing/committee report and a gr.envelope/1 file.

Subcommands:
  plan   --from D --to D                 split a period into meeting-list queries of at most 62 days
  build  --from D --to D [inputs]        read saved tool results, write report + data + run folder
  check  --data FILE [--report FILE]     re-check a written result against the skill rules

Inputs of build (each flag may repeat; every file is one saved tool result):
  --meetings   assembly_committee_meetings (one file per plan segment)
  --schedule   assembly_schedule
  --clips      assembly_speech_clips
  --captions   assembly_live_captions
  --audit      assembly_audit_reports
  --summaries  paraphrased speech summaries written by the assistant (not a tool result)

This script never calls a tool and never starts a live-caption recording. Standard library only.
Exit codes: 0 clean, 1 nothing could be read / check failed, 2 usage error, 3 look at the warnings.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
sys.path.insert(0, str(HERE / 'lib'))

import dates  # noqa: E402
import envelope  # noqa: E402
import paths  # noqa: E402

SKILL = 'assembly-hearing-watch'
VERSION = '0.1.0'
MAX_DAYS = 62
MAX_ITEMS = 5
MAX_QUOTE = 40
CAPTION_MARK = '「자막 기준·확인 필요」'
TEMPLATE = SKILL_DIR / 'assets' / 'report-template.md'

TOOLS = {
    'meetings': 'assembly_committee_meetings',
    'schedule': 'assembly_schedule',
    'clips': 'assembly_speech_clips',
    'captions': 'assembly_live_captions',
    'audit': 'assembly_audit_reports',
}
KIND_BASIS = {
    'schedule': ('schedule',),
    'speech': ('caption', 'minutes', 'clips'),
    'audit_report': ('report',),
}
_QUOTE_RE = re.compile('“([^”]*)”')
_HOE_RE = re.compile(r'제\s*(\d+)\s*회')
_CHA_RE = re.compile(r'제\s*(\d+)\s*차')


class LoadError(Exception):
    pass


# ------------------------------------------------------------------------------ small helpers

def split_period(first, last):
    """Consecutive windows of at most MAX_DAYS days (both ends counted) that cover first..last."""
    out = []
    cur = first
    while cur <= last:
        end = min(cur + timedelta(days=MAX_DAYS - 1), last)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


def short_hash(*parts):
    return hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:6]


def norm(text):
    return unicodedata.normalize('NFC', str(text)).strip()


def numbers(text):
    hoe = _HOE_RE.search(text or '')
    cha = _CHA_RE.search(text or '')
    return (int(hoe.group(1)) if hoe else None, int(cha.group(1)) if cha else None)


def cell(text):
    return ' '.join(str(text).split()).replace('|', '/')


def utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


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


# ------------------------------------------------------------------------------ reading tool results

def unwrap(data):
    """Unwrap the MCP text wrapper [{"type": "text", "text": "<json>"}] when a saved file carries it."""
    for _ in range(3):
        if isinstance(data, dict) and isinstance(data.get('content'), list) and 'type' not in data:
            data = data['content']
        if (isinstance(data, list) and data and
                all(isinstance(x, dict) and x.get('type') == 'text' and isinstance(x.get('text'), str) for x in data)):
            try:
                data = json.loads(''.join(x['text'] for x in data))
            except ValueError:
                raise LoadError('the tool text is not JSON')
            continue
        break
    return data


def load_file(path):
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise LoadError('cannot read %s (%s)' % (p.name, exc.strerror or exc.__class__.__name__))
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        raise LoadError('%s is not valid JSON' % p.name)
    return unwrap(data)


def list_of(data, keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return data[key]
        if data.get('error'):
            raise LoadError('the tool returned an error: %s' % str(data['error'])[:120])
    raise LoadError('unexpected result shape (wanted a list or one of: %s)' % ', '.join(keys))


SHAPES = {
    'meetings': ('items', 'meetings'),
    'schedule': ('items', 'schedule', 'schedules'),
    'captions': ('recordings',),
    'audit': ('items',),
}


def read_source(key, files):
    """-> (source dict for the envelope, items). Mixed readable/unreadable files give status failed."""
    items, bad = [], []
    for f in files:
        try:
            data = load_file(f)
            if key == 'clips':
                blocks = data if isinstance(data, list) else [data]
                for block in blocks:
                    if not isinstance(block, dict):
                        raise LoadError('unexpected result shape (clips)')
                    meetings_ = list_of(block, ('meetings',))
                    for m in meetings_:
                        items.append((block.get('date'), m))
            elif key == 'captions' and isinstance(data, dict) and isinstance(data.get('recordings'), list):
                items.extend(data['recordings'])
            else:
                items.extend(list_of(data, SHAPES[key]))
        except LoadError as exc:
            bad.append('%s: %s' % (Path(f).name, exc))
    src = {'source': TOOLS[key], 'requests': len(files)}
    readable = len(files) - len(bad)
    if readable == 0:
        src['status'] = 'unread'
    elif bad:
        src['status'] = 'failed'
    else:
        src['status'] = 'ok' if items else 'empty'
    if bad:
        src['error'] = '; '.join(bad)[:400]
    return src, items


# ------------------------------------------------------------------------------ rows

class Collector:
    def __init__(self, first, last):
        self.first, self.last = first, last
        self.rows, self.warnings, self._ids = [], [], set()

    def warn(self, text):
        self.warnings.append(text)

    def in_period(self, day_text):
        try:
            day = dates.parse_date(day_text)
        except dates.DateError:
            return None
        return self.first <= day <= self.last

    def add(self, row_id, kind, basis, title, day, url, committee, time):
        base, n = row_id, 1
        while row_id in self._ids:
            n += 1
            row_id = '%s-%d' % (base, n)
        self._ids.add(row_id)
        row = {'id': row_id, 'title': title, 'date': day, 'url': url or None,
               'basis': basis, 'kind': kind, 'committee': committee, 'time': time}
        self.rows.append(row)
        return row


def index_meetings(items):
    seen, out = set(), []
    for m in items:
        if not isinstance(m, dict):
            continue
        key = (m.get('conf_id'), m.get('confer_num'), m.get('date'), m.get('title'))
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def match_meeting(item, meetings):
    """-> (meeting or None, reason). Date, committee, session and meeting number must all be equal."""
    if meetings is None:
        return None, '회의 목록을 받지 않아 링크를 붙이지 못함'
    day, committee = item.get('SCH_DT'), norm(item.get('CMIT_NM') or '')
    hoe, cha = numbers(item.get('SCH_CN') or '')
    same_day = [m for m in meetings if m.get('date') == day and norm(m.get('committee') or '') == committee]
    if not same_day:
        return None, '같은 날짜·위원회의 회의 목록 항목이 없음'
    hits = []
    for m in same_day:
        m_hoe, m_cha = numbers(m.get('title') or '')
        if hoe is not None and cha is not None and (m_hoe, m_cha) == (hoe, cha):
            hits.append(m)
    if not hits:
        return None, '같은 날짜·위원회의 회의는 있으나 회차·차수가 일치하지 않음(링크 붙이지 않음)'
    if len(hits) > 1:
        return None, '일치하는 회의가 %d건이라 모호함(ambiguous), 링크 붙이지 않음' % len(hits)
    return hits[0], ''


def meeting_link(m):
    return m.get('minutes_url') or m.get('video_url') or None


def schedule_rows(col, items, meetings):
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            col.warn('일정 항목 형식이 달라 건너뜀')
            continue
        day = item.get('SCH_DT')
        ok = col.in_period(day)
        if ok is None:
            col.warn('일정 날짜를 읽지 못해 건너뜀: %s' % cell(item.get('SCH_CN') or ''))
            continue
        if not ok:
            col.warn('기간 밖 일정이라 뺌: %s %s' % (day, cell(item.get('SCH_CN') or '')))
            continue
        committee = item.get('CMIT_NM') or None
        time = item.get('SCH_TM') or None
        text = cell(item.get('SCH_CN') or '')
        div = cell(item.get('CONF_DIV') or '')
        key = (day, time, committee, text, div)
        if key in seen:
            continue
        seen.add(key)
        title = text or div or '일정'
        if div and div not in title:
            title = '%s (%s)' % (title, div)
        digits = re.sub(r'\D', '', time or '') or 'na'
        row_id = 'schedule-%s-%s-%s' % (day, digits, short_hash(committee, text, div))
        meeting, reason = match_meeting(item, meetings)
        row = col.add(row_id, 'schedule', 'schedule', title, day, meeting_link(meeting) if meeting else None,
                      committee, time)
        if row['url'] is None:
            col.warn('%s: 링크 없음 - %s' % (row['id'], reason or '회의 목록 항목에 링크가 없음'))


def clips_rows(col, items):
    for day, m in items:
        if not isinstance(m, dict):
            col.warn('발언 클립 항목 형식이 달라 건너뜀')
            continue
        ok = col.in_period(day)
        name = cell(m.get('meeting') or '')
        if ok is None or not name:
            col.warn('발언 클립 날짜 또는 회의명을 읽지 못해 건너뜀')
            continue
        if not ok:
            col.warn('기간 밖 발언 클립이라 뺌: %s %s' % (day, name))
            continue
        members = [x for x in (m.get('members') or []) if isinstance(x, dict)]
        total = sum(int(x.get('clips') or 0) for x in members)
        minutes = round(sum(float(x.get('minutes') or 0) for x in members), 1)
        link = None
        for x in members:
            for it in x.get('items') or []:
                if isinstance(it, dict) and it.get('video'):
                    link = it['video']
                    break
            if link:
                break
        title = '발언한 의원 %d명, 클립 %d개' % (len(members), total)
        if minutes > 0:
            title += ', 약 %s분' % minutes
        title += ' (영상 링크만 제공, 발언 내용은 아님)'
        row_id = 'clips-%s-%s' % (day, short_hash(name))
        row = col.add(row_id, 'speech', 'clips', title, day, link, name, None)
        if row['url'] is None:
            col.warn('%s: 링크 없음 - 의원별 영상 링크를 조회하지 않음' % row['id'])


def audit_rows(col, items):
    for it in items:
        if not isinstance(it, dict):
            col.warn('국정감사 문서 항목 형식이 달라 건너뜀')
            continue
        year = it.get('year')
        title = cell(it.get('title') or '')
        if not (isinstance(year, int) and 1900 <= year <= 2100) or not title:
            col.warn('국정감사 문서의 연도 또는 제목을 읽지 못해 건너뜀')
            continue
        doc = it.get('doc_id') or short_hash(title, year)
        row = col.add('audit-%s' % doc, 'audit_report', 'report', title, '%d-01-01' % year,
                      it.get('pdf_url') or it.get('hwp_url'), it.get('committee') or None, None)
        if row['url'] is None:
            col.warn('%s: 링크 없음 - 문서 주소가 없음' % row['id'])


def summary_rows(col, data, meetings):
    entries = data.get('meetings') if isinstance(data, dict) else None
    if not isinstance(entries, list):
        col.warn('발언 요약 파일 형식이 달라 쓰지 못함(meetings 목록이 없음)')
        return
    by_id = {m.get('conf_id'): m for m in (meetings or []) if m.get('conf_id')}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('basis') not in ('minutes', 'caption'):
            col.warn('발언 요약 항목의 basis가 minutes/caption이 아니라 건너뜀')
            continue
        basis = entry['basis']
        conf = entry.get('conf_id')
        meeting = by_id.get(conf) if conf else None
        if conf and meeting is None:
            col.warn('%s: 회의 목록에 없는 회의번호라 날짜·위원회를 entry 값으로만 씀' % conf)
        committee = entry.get('committee') or (meeting or {}).get('committee')
        day = entry.get('date') or (meeting or {}).get('date')
        time = entry.get('time') or None
        url = entry.get('url') or ((meeting or {}).get('minutes_url') if basis == 'minutes' else None)
        ok = col.in_period(day)
        key = conf or 'cap-%s' % short_hash(day, committee, time)
        if ok is None:
            col.warn('%s: 발언 요약의 날짜를 알 수 없어 건너뜀' % key)
            continue
        if not ok:
            col.warn('%s: 기간 밖 발언 요약이라 뺌 (%s)' % (key, day))
            continue
        items = entry.get('items') if isinstance(entry.get('items'), list) else []
        if len(items) > MAX_ITEMS:
            col.warn('%s: 발언 요약 %d건 중 %d건만 씀' % (key, len(items), MAX_ITEMS))
        for n, item in enumerate(items[:MAX_ITEMS], 1):
            text = cell(item.get('summary') or '') if isinstance(item, dict) else ''
            row_id = 'speech-%s-%d' % (key, n)
            if not text:
                col.warn('%s: 요약 문장이 없어 건너뜀' % row_id)
                continue
            quote = cell(item.get('quote') or '')
            title = text
            if quote:
                if len(quote) <= MAX_QUOTE:
                    title = '%s (“%s”)' % (text, quote)
                else:
                    col.warn('%s: 인용이 %d자를 넘어 인용만 뺌' % (row_id, MAX_QUOTE))
            if basis == 'caption':
                title = '%s %s' % (CAPTION_MARK, title)
            row = col.add(row_id, 'speech', basis, title, day, url, committee, time)
            if row['url'] is None:
                col.warn('%s: 링크 없음 - 요약의 출처 주소가 없음' % row['id'])


# ------------------------------------------------------------------------------ report

def read_template():
    try:
        return TEMPLATE.read_text(encoding='utf-8')
    except OSError:
        raise SystemExit('report template missing: %s' % TEMPLATE)


def link(url):
    return '[링크](%s)' % url if url else '링크 없음'


def section_note(sources, names, label):
    states = [sources[n]['status'] for n in names if n in sources]
    if not states:
        return '%s: 이번에는 조회하지 않음.' % label
    if all(s in ('unread', 'failed', 'no_key') for s in states):
        return '%s: 읽지 못함 - 확인 필요.' % label
    return '%s: 이번 조회 쪽에서는 보이지 않음(다음 쪽·다른 조건은 확인 필요).' % label


def render_report(env, name, created, summaries_given=False):
    sources = {s['source']: s for s in env['sources']}
    rows = env['rows']
    sched = sorted((r for r in rows if r['kind'] == 'schedule'), key=lambda r: (r['date'], r['time'] or ''))
    speech = sorted((r for r in rows if r['kind'] == 'speech'), key=lambda r: (r['date'], r['id']))
    audit = [r for r in rows if r['kind'] == 'audit_report']

    if sched:
        lines = ['| 일시 | 위원회 | 회의 | 링크 |', '|---|---|---|---|']
        for r in sched:
            when = r['date'] + (' ' + r['time'] if r['time'] else '')
            lines.append('| %s | %s | %s | %s |' % (when, cell(r['committee'] or '-'), cell(r['title']), link(r['url'])))
        sched_table = '\n'.join(lines)
    else:
        sched_table = section_note(sources, ['assembly_schedule'], '위원회 일정')

    if speech:
        speech_list = '\n'.join('- %s %s: %s (%s)' % (r['date'], cell(r['committee'] or '-'), r['title'], link(r['url']))
                                for r in speech)
    else:
        # the meeting list alone says nothing about speeches; it counts only when summaries were handed in
        speech_sources = ['assembly_speech_clips', 'assembly_live_captions']
        if summaries_given:
            speech_sources.insert(0, 'assembly_committee_meetings')
        speech_list = section_note(sources, speech_sources, '발언 요약')
    notice = ''
    if any(r['basis'] == 'caption' for r in speech):
        notice = '%s 실시간 자막은 인식 오류가 있을 수 있어 원문 확인이 필요합니다.' % CAPTION_MARK

    if audit:
        lines = ['| 위원회 | 문서 | 연도 | 링크 |', '|---|---|---|---|']
        for r in audit:
            lines.append('| %s | %s | %s | %s |' % (cell(r['committee'] or '-'), cell(r['title']), r['date'][:4] + '년',
                                                    link(r['url'])))
        audit_table = '\n'.join(lines)
    else:
        audit_table = section_note(sources, ['assembly_audit_reports'], '국정감사 시정·처리요구')

    checks = ['- %s' % w for w in env['warnings']]
    checks += ['- 회의록은 회의가 끝나고 약 1주일 뒤에 올라옵니다. 그 전에는 회의록 링크가 없을 수 있습니다.',
               '- 국정감사 결과보고서는 날짜 없이 연도만 제공되어 연도로 표시했습니다.',
               '- "보이지 않음"은 이번 조회 쪽에서 안 보였다는 뜻이며, 없다는 뜻이 아닙니다.']
    status_line = ', '.join('%s %s' % (s['source'], s['status']) for s in env['sources']) or '조회한 자료 없음'

    text = read_template()
    for key, value in (('skill', SKILL), ('period', env['period']), ('created', created), ('version', VERSION),
                       ('status', env['status']), ('sources', status_line), ('schedule_table', sched_table),
                       ('speech_notice', notice), ('speech_list', speech_list), ('audit_table', audit_table),
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


# ------------------------------------------------------------------------------ commands

def cmd_plan(args):
    segs = split_period(args.frm, args.to)
    print('segments: %d' % len(segs))
    for i, (a, b) in enumerate(segs, 1):
        print('segment %d: %s~%s' % (i, a.isoformat(), b.isoformat()))
    return 0


def cmd_build(args, parser):
    if args.to < args.frm:
        parser.error('--to is before --from')
    given = {k: getattr(args, k) or [] for k in TOOLS}
    if not any(given.values()):
        parser.error('give at least one saved tool result (--meetings, --schedule, --clips, --captions or --audit)')
    period = '%s~%s' % (args.frm.isoformat(), args.to.isoformat())
    try:
        name = paths.make_name(period=(args.frm, args.to), topic=args.topic)
        paths.resolve_root(SKILL_DIR, os.environ)
    except paths.InvalidName as exc:
        parser.error(str(exc))
    except paths.RootNotFound as exc:
        sys.stderr.write('%s\n' % exc)
        return 1

    col = Collector(args.frm, args.to)
    sources, loaded = [], {}
    for key in ('meetings', 'schedule', 'clips', 'captions', 'audit'):
        if given[key]:
            src, items = read_source(key, given[key])
            sources.append(src)
            loaded[key] = items
            if src['status'] in ('unread', 'failed'):
                col.warn('%s: 일부 또는 전체를 읽지 못함 - %s' % (src['source'], src.get('error', '')))
    meetings = index_meetings(loaded['meetings']) if 'meetings' in loaded else None

    if meetings is not None:
        need = len(split_period(args.frm, args.to))
        have = len(given['meetings'])
        if have < need:
            col.warn('회의 목록은 %d구간 중 %d구간만 조회해 일부만 보임 (62일 이하로 나눠 조회 필요)' % (need, have))

    if 'schedule' in loaded:
        schedule_rows(col, loaded['schedule'], meetings)
    if 'clips' in loaded:
        clips_rows(col, loaded['clips'])
    if args.summaries:
        try:
            summary_rows(col, load_file(args.summaries), meetings)
        except LoadError as exc:
            col.warn('발언 요약 파일을 읽지 못함 - %s' % exc)
    if 'audit' in loaded:
        audit_rows(col, loaded['audit'])

    for row in col.rows:
        if row['url'] is None and not any(row['id'] in w for w in col.warnings):
            col.warn('%s: 링크 없음' % row['id'])

    env = {'schema': envelope.SCHEMA, 'skill': SKILL, 'period': period,
           'status': envelope.summarize_status(sources), 'sources': sources,
           'rows': col.rows, 'warnings': col.warnings}
    try:
        envelope.assert_valid(env)
    except envelope.EnvelopeError as exc:
        sys.stderr.write('internal error, result not written: %s\n' % exc)
        return 1

    created = dates.now_iso()
    run = new_run_dir(date.today())
    raw = paths.ensure(run / 'raw')
    inputs = []
    for key in ('meetings', 'schedule', 'clips', 'captions', 'audit'):
        for i, f in enumerate(given[key], 1):
            entry = {'source': TOOLS[key], 'file': Path(f).name}
            if Path(f).is_file():
                copy = Path('raw') / ('%s-%d.json' % (TOOLS[key], i))
                try:
                    shutil.copyfile(str(f), str(run / copy))
                    entry['copied_as'] = copy.as_posix()
                except OSError:
                    pass
            inputs.append(entry)
    run_info = {'skill': SKILL, 'version': VERSION, 'created': created, 'period': period,
                'status': env['status'], 'inputs': inputs}
    write_text_atomic(run / 'run.json', json.dumps(run_info, ensure_ascii=False, indent=2) + '\n')
    del raw

    report = p_report(name)
    move_to_history(report)
    write_text_atomic(report, render_report(env, name, created, bool(args.summaries)))
    data = envelope.write_envelope(p_data(name), env)

    print('status: %s' % env['status'])
    print('rows: %d' % len(env['rows']))
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
    first = last = None
    m = re.match(r'^(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$', str(env.get('period')))
    if m:
        try:
            first, last = dates.parse_date(m.group(1)), dates.parse_date(m.group(2))
        except dates.DateError:
            pass
    stems = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid, kind, basis = r.get('id'), r.get('kind'), r.get('basis')
        title = r.get('title') if isinstance(r.get('title'), str) else ''
        if kind not in KIND_BASIS or basis not in KIND_BASIS.get(kind, ()):
            problems.append('%s: kind %r and basis %r do not fit' % (rid, kind, basis))
        if first and kind != 'audit_report':
            try:
                day = dates.parse_date(r.get('date'))
                if not first <= day <= last:
                    problems.append('%s: date %s is outside the period' % (rid, r.get('date')))
            except dates.DateError:
                pass
        if basis == 'caption' and CAPTION_MARK not in title:
            problems.append('%s: caption row lacks the mark %s' % (rid, CAPTION_MARK))
        for q in _QUOTE_RE.findall(title):
            if len(q) > MAX_QUOTE:
                problems.append('%s: quote has %d chars (max %d)' % (rid, len(q), MAX_QUOTE))
        if isinstance(rid, str) and rid.startswith('speech-') and rid.count('-') >= 2:
            stems[rid.rsplit('-', 1)[0]] = stems.get(rid.rsplit('-', 1)[0], 0) + 1
    for stem, count in sorted(stems.items()):
        if count > MAX_ITEMS:
            problems.append('%s: %d summary rows for one meeting (max %d)' % (stem, count, MAX_ITEMS))
    if report_text is not None:
        for r in rows:
            if isinstance(r, dict) and r.get('url') and r['url'] not in report_text:
                problems.append('%s: its link is missing in the report' % r.get('id'))
        if any(isinstance(r, dict) and r.get('basis') == 'caption' for r in rows) and CAPTION_MARK not in report_text:
            problems.append('report lacks the caption mark %s' % CAPTION_MARK)
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
    top = Parser(prog='hearing.py', description='assembly-hearing-watch helper', allow_abbrev=False)
    sub = top.add_subparsers(dest='cmd', parser_class=Parser)
    sub.required = True

    plan = sub.add_parser('plan', allow_abbrev=False)
    plan.add_argument('--from', dest='frm', required=True, type=parse_day)
    plan.add_argument('--to', required=True, type=parse_day)

    build = sub.add_parser('build', allow_abbrev=False)
    build.add_argument('--from', dest='frm', required=True, type=parse_day)
    build.add_argument('--to', required=True, type=parse_day)
    for key in TOOLS:
        build.add_argument('--' + key, action='append', metavar='FILE')
    build.add_argument('--summaries', metavar='FILE')
    build.add_argument('--topic')

    check = sub.add_parser('check', allow_abbrev=False)
    check.add_argument('--data', required=True)
    check.add_argument('--report')
    return top, sub


def main(argv=None):
    utf8_console()
    top, sub = make_parser()
    args = top.parse_args(argv)
    if args.cmd == 'plan':
        if args.to < args.frm:
            top.error('--to is before --from')
        return cmd_plan(args)
    if args.cmd == 'build':
        return cmd_build(args, sub.choices['build'])
    return cmd_check(args, sub.choices['check'])


if __name__ == '__main__':
    sys.exit(main())
