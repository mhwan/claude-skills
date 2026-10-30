#!/usr/bin/env python3
"""Turns saved National Assembly tool results into a per-member table (speeches, minutes link, bills) and a gr.envelope/1 file.

Subcommands:
  plan        --from D --to D [--topic T]        split the period into meeting-list queries of at most 62 days
  candidates  --from D --to D --search F...      list the meetings worth opening (22nd Assembly, inside the period)
  build       --from D --to D [inputs]           read saved tool results, write report + data + run folder
  check       --data FILE [--report FILE]        re-check a written result against the skill rules

Inputs of build (each flag may repeat; every file is one saved tool result):
  --search    assembly_search (one file per result page)
  --speeches  assembly_meeting_speeches (one file per opened meeting)
  --meetings  assembly_committee_meetings (maps conf_id to confer_num and the minutes link)
  --bills     assembly_bills (one file per member or query)
  --members   assembly_members (only name, party, district and committees are used)

This script never calls a tool and never reads a website. Standard library only.
Exit codes: 0 clean, 1 nothing could be read / check failed, 2 usage error, 3 look at the warnings.
"""
import sys

sys.dont_write_bytecode = True

import argparse
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

SKILL = 'assembly-member-research'
VERSION = '0.1.0'
MAX_DAYS = 62
OPEN_MAX = 5          # meetings whose minutes are opened and counted
TOP = 10              # members shown when only a topic is given
MAX_SPEECHES = 3      # representative speeches kept per member
MAX_QUOTE = 40
MAX_BILLS = 5         # bills kept per member in the data file
SHOW_BILLS = 3        # bills shown per member in the table
TEMPLATE = SKILL_DIR / 'assets' / 'report-template.md'
UNKNOWN = '확인 필요'

TOOLS = {
    'search': 'assembly_search',
    'speeches': 'assembly_meeting_speeches',
    'meetings': 'assembly_committee_meetings',
    'bills': 'assembly_bills',
    'members': 'assembly_members',
}
# the only fields copied from the member list (no e-mail, phone, homepage or office)
MEMBER_KEEP = ('name', 'party', 'district', 'committees', 'units')
TITLE_WORDS = ('국회의원', '소위원장', '부의장', '위원장', '간사', '의장', '위원', '의원')
TITLE_ENDINGS = ('위원장대리', '위원장직무대행', '의장대리', '직무대행', '위원장', '부의장', '의장', '간사')
TABLE_HEAD = '| 의원 | 정당 | 위원회 | 관련 발언 수(확인한 회의 기준) | 대표 발언 | 대표발의 법안 |'

_HOE_RE = re.compile(r'제\s*(\d+)\s*회')
_CHA_RE = re.compile(r'제\s*(\d+)\s*차')
_AGENDA_RE = re.compile(r'^\s*\[안건\s*\d+[^\]]*\]\s*')
_CUT_RE = re.compile(r'…\(\d+자 생략\)|…')
_ID_RE = re.compile(r'^member-(.+)$')
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE_RE = re.compile(r'\b0\d{1,2}[-. ]\d{3,4}[-. ]\d{4}\b')
_QUOTED_RE = re.compile('“[^”]*”')
FORBIDDEN = [
    (re.compile(r'\d+\s*위(?!원)'), '순위 표현'),
    (re.compile('가장 열심|가장 활발|최다|최고|열심히|적극적|소극적|가장 많이 한'), '평가 표현'),
    (re.compile('우파|좌파|친여|친야|보수 성향|진보 성향|보수적|진보적|극단'), '성향 표현'),
]


class LoadError(Exception):
    pass


# ------------------------------------------------------------------------------ small helpers

def split_period(first, last):
    out, cur = [], first
    while cur <= last:
        end = min(cur + timedelta(days=MAX_DAYS - 1), last)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


def norm(text):
    return unicodedata.normalize('NFC', str(text)).strip()


def cell(text):
    return ' '.join(str(text).split()).replace('|', '/')


def squash(text):
    return re.sub(r'\s+', '', norm(text))


def numbers(text):
    hoe, cha = _HOE_RE.search(text or ''), _CHA_RE.search(text or '')
    return (int(hoe.group(1)) if hoe else None, int(cha.group(1)) if cha else None)


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


def topic_words(topic):
    return [w for w in re.split(r'[\s,]+', topic or '') if w]


def committee_of_title(title):
    """Committee name taken from a meeting title; the session and meeting numbers and any sub-committee part are dropped."""
    t = norm(title or '')
    m = re.search(r'제\s*\d+\s*차\s+(.*)$', t)
    t = m.group(1) if m else t
    t = t.split(' - ')[0]
    t = re.sub(r'\s*\[[^\]]*\]\s*$', '', t)
    return t.strip()


def sub_of_title(title):
    parts = norm(title or '').split(' - ', 1)
    return re.sub(r'\s*\[[^\]]*\]\s*$', '', parts[1]).strip() if len(parts) > 1 else ''


def speaker_name(speaker):
    """Member name from a speaker label ("<name> <title word>" or "<title word> <name>"); a title word is removed
    only when it is a whole word at either end."""
    t = norm(speaker or '')
    changed = True
    while changed:
        changed = False
        for w in TITLE_WORDS:
            if t.endswith(' ' + w) or t.endswith(w) and len(t) > len(w) + 1 and t[-len(w) - 1] == ' ':
                t = t[:-len(w)].strip()
                changed = True
            elif t.startswith(w + ' '):
                t = t[len(w):].strip()
                changed = True
    parts = t.split(' ')
    while len(parts) > 1 and parts[0].endswith(TITLE_ENDINGS):      # a committee-name acting-chair label before the name
        parts = parts[1:]
    while len(parts) > 1 and parts[-1].endswith(TITLE_ENDINGS):
        parts = parts[:-1]
    return ' '.join(parts)


# ------------------------------------------------------------------------------ reading tool results

def unwrap(data):
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


def error_of(data):
    if isinstance(data, dict) and data.get('error'):
        raise LoadError('the tool returned an error: %s' % str(data['error'])[:120])


def read_search(path):
    """-> (page, has_more, meetings)"""
    data = load_file(path)
    error_of(data)
    if isinstance(data, list):
        return 1, False, [m for m in data if isinstance(m, dict)]
    remote = data.get('remote') if isinstance(data, dict) else None
    if isinstance(remote, dict) and isinstance(remote.get('meetings'), list):
        page = remote.get('page') if isinstance(remote.get('page'), int) else 1
        return page, bool(remote.get('has_more')), [m for m in remote['meetings'] if isinstance(m, dict)]
    if isinstance(data, dict) and isinstance(data.get('meetings'), list):
        return 1, False, [m for m in data['meetings'] if isinstance(m, dict)]
    if isinstance(data, dict) and isinstance(data.get('local'), dict) and data['local'].get('hits'):
        raise LoadError('%s: this is a local-index result (no remote.meetings); call assembly_search again with '
                        'remote "only" and save that result' % Path(path).name)
    raise LoadError('%s: unexpected result shape (no remote.meetings)' % Path(path).name)


def read_speeches(path):
    data = load_file(path)
    error_of(data)
    blocks = data if isinstance(data, list) else [data]
    out = []
    for b in blocks:
        if not isinstance(b, dict) or not isinstance(b.get('members'), list):
            raise LoadError('%s: unexpected result shape (no members list)' % Path(path).name)
        out.append(b)
    return out


def read_list(path, keys):
    data = load_file(path)
    error_of(data)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    raise LoadError('%s: unexpected result shape' % Path(path).name)


def read_bills(path):
    data = load_file(path)
    error_of(data)
    if isinstance(data, dict) and isinstance(data.get('bills'), list):
        total = data.get('total') if isinstance(data.get('total'), int) else len(data['bills'])
        returned = data.get('returned') if isinstance(data.get('returned'), int) else len(data['bills'])
        return total, returned, [b for b in data['bills'] if isinstance(b, dict)]
    if isinstance(data, list):
        return len(data), len(data), [b for b in data if isinstance(b, dict)]
    raise LoadError('%s: unexpected result shape (no bills list)' % Path(path).name)


def read_source(key, files, warn):
    """-> (source dict, items). items depend on the key."""
    items, bad, pages = [], [], []
    for f in files:
        try:
            if key == 'search':
                page, more, ms = read_search(f)
                pages.append((page, more))
                items.extend(ms)
            elif key == 'speeches':
                items.extend(read_speeches(f))
            elif key == 'meetings':
                items.extend(x for x in read_list(f, ('meetings', 'items')) if isinstance(x, dict))
            elif key == 'bills':
                items.append(read_bills(f))
            else:
                items.extend(x for x in read_list(f, ('members', 'items')) if isinstance(x, dict))
        except LoadError as exc:
            bad.append(str(exc))
    src = {'source': TOOLS[key], 'requests': len(files)}
    if len(bad) == len(files):
        src['status'] = 'unread'
    elif bad:
        # some files were read: keep them, and let the error text and the warning say what is missing
        src['status'] = 'ok' if items else 'failed'
    else:
        src['status'] = 'ok' if items else 'empty'
    if bad:
        src['error'] = '; '.join(bad)[:400]
        warn('%s: 일부 또는 전체를 읽지 못함 - %s' % (src['source'], src['error']))
    return src, items, pages


# ------------------------------------------------------------------------------ minutes text

def turns_of(transcript):
    """One text per turn: reply lines of other speakers and the agenda tag at the start are removed."""
    out = []
    for block in re.split(r'\n\s*\n', transcript or ''):
        lines = [l for l in block.split('\n') if not l.lstrip().startswith('↳')]
        text = _AGENDA_RE.sub('', ' '.join(' '.join(lines).split()))
        if text:
            out.append(text)
    return out


def has_words(text, words):
    flat = squash(text)
    return all(squash(w) in flat for w in words)


def clean_snippet(snippet):
    """Remove the 【】 hit marks; return (text, position of the first hit or None)."""
    text, pos = '', None
    for ch in norm(snippet or ''):
        if ch == '【':
            if pos is None:
                pos = len(text)
            continue
        if ch == '】':
            continue
        text += ch
    return ' '.join(text.split()), pos


def make_quote(text, words, hit=None):
    """A verbatim piece of at most MAX_QUOTE characters that never spans an omitted-text marker."""
    text = ' '.join(norm(text).split())
    if hit is None:
        hits = [text.find(w) for w in words if text.find(w) >= 0]
        hit = min(hits) if hits else 0
    segs, pos = [], 0
    for m in list(_CUT_RE.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        segs.append((pos, end))
        pos = m.end() if m else len(text)
    seg = next(((a, b) for a, b in segs if a <= hit <= b and b > a), None) or \
        next(((a, b) for a, b in segs if b > a), None)
    if seg is None:
        return ''
    a, b = seg
    while a < b and text[a] == ' ':
        a += 1
    if b - a <= MAX_QUOTE:
        return text[a:b].strip()
    start = max(a, hit - 10)
    if start > a and text[start - 1] != ' ':
        back = text.rfind(' ', a, start)
        start = back + 1 if back >= 0 else a
    end = start + MAX_QUOTE
    if end < b and text[end] != ' ':
        back = text.rfind(' ', start + 10, end)
        end = back if back > 0 else end
    quote = text[start:end].strip()
    if words and hit >= end:
        quote = text[hit:hit + MAX_QUOTE].strip()
    return quote[:MAX_QUOTE]


# ------------------------------------------------------------------------------ gathering

class Gather:
    """Everything known about each member, from search snippets and from opened minutes."""

    def __init__(self, first, last, words, open_max):
        self.first, self.last, self.words, self.open_max = first, last, words, open_max
        self.warnings = []
        self.meetings = {}        # conf_id -> search meeting (merged)
        self.opened = []          # opened minutes (dicts)
        self.confmap = {}         # conf_id -> committee_meetings entry
        self.party, self.district = {}, {}
        self.bills = {}           # name -> {BILL_NO: bill}
        self.bills_given = False
        self.pages = []
        self.dropped = 0
        self.dup_names = set()

    def warn(self, text):
        self.warnings.append(text)

    def in_period(self, day):
        try:
            d = dates.parse_date(day)
        except dates.DateError:
            return None
        return self.first <= d <= self.last

    # --- search ---
    def add_search(self, items, pages):
        self.pages.extend(pages)
        for m in items:
            title = norm(m.get('title') or '')
            ok = self.in_period(m.get('date'))
            if not title.startswith('제22대') or not ok:
                self.dropped += 1
                continue
            conf = m.get('conf_id') or '%s|%s' % (m.get('date'), title)
            cur = self.meetings.setdefault(conf, {'conf_id': m.get('conf_id'), 'title': title, 'date': m['date'],
                                                  'rank': 0, 'matches': {}})
            cur['rank'] = max(cur['rank'], int(m.get('matched_in_detail') or m.get('matched_speeches') or 0))
            for x in m.get('matches') or []:
                if not isinstance(x, dict):
                    continue
                sid = str(x.get('speaker_id') or '')
                if not sid.isdigit():
                    continue      # not a member (government official, staff)
                name = speaker_name(x.get('speaker'))
                if name:
                    cur['matches'].setdefault(name, {})[x.get('seq_no', len(cur['matches'].get(name, {})))] = \
                        x.get('snippet') or ''

    # --- committee_meetings ---
    def add_meetings(self, items):
        for m in items:
            if m.get('conf_id'):
                self.confmap[m['conf_id']] = m

    # --- speeches ---
    def add_speeches(self, blocks):
        for b in blocks:
            ok = self.in_period(b.get('date'))
            title = norm(b.get('title') or '')
            if not ok or (title and not title.startswith('제22대')):
                self.warn('기간 밖이거나 22대가 아닌 회의록이라 뺌: %s %s' % (b.get('date'), cell(title)))
                continue
            people = {}
            for p in b.get('members') or []:
                if not isinstance(p, dict) or not p.get('name'):
                    continue
                name = norm(p['name'])
                turns = turns_of(p.get('transcript'))
                matched = [t for t in turns if not self.words or has_words(t, self.words)]
                people[name] = {'turns': turns, 'matched': matched, 'truncated': bool(p.get('truncated'))}
                if p.get('party'):
                    self.party.setdefault(name, norm(p['party']))
                if p.get('district'):
                    self.district.setdefault(name, norm(p['district']))
            self.opened.append({'confer_num': b.get('confer_num'), 'title': title, 'date': b['date'],
                                'committee': norm(b.get('committee') or '') or committee_of_title(title),
                                'url': b.get('source_url') or None, 'people': people})

    def find_opened(self, conf_id, meeting):
        """The opened minutes that belong to a search meeting, or None."""
        entry = self.confmap.get(conf_id)
        if entry is not None:
            for o in self.opened:
                if str(o['confer_num']) == str(entry.get('confer_num')):
                    return o
        for o in self.opened:
            if o['date'] != meeting['date'] or numbers(o['title']) != numbers(meeting['title']):
                continue
            if o['committee'] and o['committee'] not in meeting['title']:
                continue
            if sub_of_title(o['title']) != sub_of_title(meeting['title']):
                continue
            if numbers(meeting['title']) == (None, None):
                continue
            return o
        return None

    # --- bills ---
    def add_bills(self, results):
        self.bills_given = True
        for total, returned, rows in results:
            if total > returned:
                self.warn('법안 조회 결과가 일부만 왔습니다(%d건 중 %d건). 나머지는 확인 필요' % (total, returned))
            for b in rows:
                who = norm(b.get('RST_PROPOSER') or '')
                no = str(b.get('BILL_NO') or '')
                if who and no:
                    self.bills.setdefault(who, {})[no] = b

    def add_members(self, items):
        seen = {}
        for m in items:
            name = norm(m.get('name') or '')
            if not name:
                continue
            if name in seen and (seen[name].get('party') != m.get('party')):
                self.dup_names.add(name)
            seen.setdefault(name, m)
        for name, m in seen.items():
            if name in self.dup_names:
                self.warn('같은 이름의 의원이 둘 이상이라 정당을 확정하지 못함: %s - 확인 필요' % name)
                continue
            if m.get('party'):
                self.party.setdefault(name, norm(m['party']))
            if m.get('district'):
                self.district.setdefault(name, norm(m['district']))


def evidence_for(g, name):
    """-> (count, lower_bound, evidence list sorted newest first)"""
    count, lower = 0, False
    evid = []
    for conf, m in g.meetings.items():
        mine = m['matches'].get(name)
        o = g.find_opened(m['conf_id'], m)
        snippets = list((mine or {}).values())
        if o is not None and name in o['people']:
            p = o['people'][name]
            n = len(p['matched'])
            if p['truncated']:
                lower = True
                n = max(n, len(snippets))
            count += n
            if p['matched']:
                evid.append({'date': o['date'], 'meeting': o['title'], 'url': o['url'], 'committee': o['committee'],
                             'quote': make_quote(p['matched'][0], g.words), 'opened': True, 'key': conf})
        elif o is not None:
            continue                  # opened, but this member said nothing in it
        elif snippets:
            lower = True
            count += len(snippets)
            entry = g.confmap.get(m['conf_id']) or {}
            text, hit = clean_snippet(snippets[0])
            evid.append({'date': m['date'], 'meeting': m['title'], 'url': entry.get('minutes_url') or None,
                         'committee': committee_of_title(m['title']), 'quote': make_quote(text, g.words, hit),
                         'opened': False, 'key': conf})
    seen = {e['key'] for e in evid}
    for o in g.opened:                # opened minutes that no search meeting points to
        if name in o['people'] and not any(g.find_opened(m['conf_id'], m) is o for m in g.meetings.values()):
            p = o['people'][name]
            count += len(p['matched'])
            lower = lower or p['truncated']
            if p['matched']:
                evid.append({'date': o['date'], 'meeting': o['title'], 'url': o['url'], 'committee': o['committee'],
                             'quote': make_quote(p['matched'][0], g.words), 'opened': True,
                             'key': 'open-%s' % o['confer_num']})
    del seen
    evid.sort(key=lambda e: (e['date'], e['opened']), reverse=True)
    return count, lower, evid


def member_names(g, wanted):
    names = []
    for m in g.meetings.values():
        for n in m['matches']:
            if n not in names:
                names.append(n)
    for o in g.opened:
        for n in o['people']:
            if n not in names:
                names.append(n)
    for n in wanted:
        if n not in names:
            names.append(n)
    return names


def bill_entries(g, name, row_id):
    own = sorted(g.bills.get(name, {}).values(), key=lambda b: str(b.get('PROPOSE_DT') or ''), reverse=True)
    out = []
    for b in own[:MAX_BILLS]:
        link = b.get('DETAIL_LINK') or None
        if link is None:
            g.warn('%s: 법안 %s 링크 없음' % (row_id, b.get('BILL_NO')))
        out.append({'no': str(b.get('BILL_NO')), 'name': cell(b.get('BILL_NAME') or ''), 'url': link})
    if len(own) > MAX_BILLS:
        g.warn('%s: 대표발의 법안 %d건 중 %d건만 보관' % (row_id, len(own), MAX_BILLS))
    return out


def make_rows(g, wanted, top, show_all):
    rows, lowers = [], {}
    not_matched = []
    for name in member_names(g, wanted):
        if wanted and name not in wanted:
            continue                  # named request: only the named members get a row
        count, lower, evid = evidence_for(g, name)
        if not wanted and not evid and count == 0:
            not_matched.append(name)  # the search listed this member, but the opened minutes have no such speech
            continue
        row_id = 'member-%s' % name
        speeches = []
        for e in evid:
            if len(speeches) >= MAX_SPEECHES:
                break
            if not e['url']:
                g.warn('%s: %s 회의록 링크를 찾지 못해 이 발언은 뺌(확인 필요)' % (row_id, e['date']))
                continue
            if not e['quote']:
                continue
            speeches.append({'quote': e['quote'], 'date': e['date'], 'meeting': cell(e['meeting']), 'url': e['url']})
        committees = []
        for e in evid:
            if e['committee'] and e['committee'] not in committees:
                committees.append(e['committee'])
        day = evid[0]['date'] if evid else g.last.isoformat()
        if not evid:
            g.warn('%s: 확인한 범위에서 안 보임(확인 필요) - 날짜는 기간 끝으로 채움' % row_id)
        party = g.party.get(name) or UNKNOWN
        rows.append({'id': row_id, 'title': name, 'date': day, 'url': speeches[0]['url'] if speeches else None,
                     'member': name, 'party': party, 'committee': ', '.join(committees) or UNKNOWN,
                     'speech_count': count, 'speeches': speeches, 'bills': bill_entries(g, name, row_id)})
        lowers[name] = lower
    if not_matched:
        g.warn('검색에는 이름이 나왔지만 열어 본 회의록에서 해당 낱말이 든 발언을 찾지 못해 표에서 뺀 의원 %d명: %s (말하지 않았다는 뜻이 아니라 확인 필요)'
               % (len(not_matched), ', '.join(not_matched)))
    if wanted:
        order = {n: i for i, n in enumerate(wanted)}
        rows.sort(key=lambda r: (order.get(r['member'], len(order)), -r['speech_count']))
    else:
        rows.sort(key=lambda r: (-r['speech_count'], tuple(-int(x) for x in r['date'].split('-')), r['member']))
        if len(rows) > top and not show_all:
            g.warn('의원 %d명 중 %d명만 표시(기본 정렬: 발언 수 많은 순). 전부 보려면 "다 보여줘"' % (len(rows), top))
            rows = rows[:top]
    return rows, lowers


# ------------------------------------------------------------------------------ report

def read_template():
    try:
        return TEMPLATE.read_text(encoding='utf-8')
    except OSError:
        raise SystemExit('report template missing: %s' % TEMPLATE)


def link(url, label='링크'):
    return '[%s](%s)' % (label, url) if url else '링크 없음'


def bills_cell(row, bills_asked):
    if not bills_asked:
        return '조회하지 않음'
    if not row['bills']:
        return UNKNOWN
    shown = ['[%s](%s)' % (cell(b['name']), b['url']) if b['url'] else cell(b['name']) for b in row['bills'][:SHOW_BILLS]]
    text = '<br>'.join(shown)
    rest = len(row['bills']) - SHOW_BILLS
    return text + (' 외 %d건' % rest if rest > 0 else '')


def render_table(rows, lowers, bills_asked):
    if not rows:
        return '확인한 범위에서 의원이 보이지 않았습니다(확인 필요).'
    lines = [TABLE_HEAD, '|---|---|---|---|---|---|']
    for r in rows:
        if r['speeches']:
            s = r['speeches'][0]
            speech = '“%s” (%s, %s)' % (cell(s['quote']), s['date'], link(s['url'], '회의록'))
        else:
            speech = UNKNOWN
        count = ('최소 %d' if lowers.get(r['member']) else '%d') % r['speech_count']
        lines.append('| %s | %s | %s | %s | %s | %s |' % (cell(r['member']), cell(r['party']), cell(r['committee']),
                                                          count, speech, bills_cell(r, bills_asked)))
    return '\n'.join(lines)


def render_report(env, name, created, ctx):
    rows = env['rows']
    shown = len(rows)
    sort_note = ('기본 정렬: 발언 수 많은 순. 순위나 평가가 아닙니다.' if not ctx['wanted']
                 else '요청하신 의원 순서입니다. 순위나 평가가 아닙니다.')
    subject = ctx['topic'] or ', '.join(ctx['wanted']) or '의원 조사'
    if rows:
        conclusion = '의원 %d명을 표에 담았습니다.' % shown
        if ctx['found'] > shown:
            conclusion += ' (찾은 %d명 중 일부)' % ctx['found']
    else:
        conclusion = '확인한 범위에서 의원이 보이지 않았습니다(확인 필요).'
    cov = []
    if ctx['pages']:
        last = max(p for p, _ in ctx['pages'])
        more = any(m for p, m in ctx['pages'] if p == last)
        cov.append('- 검색 결과 %d쪽까지 확인했습니다(%s). %s' % (
            last, ', '.join('%d쪽' % p for p in sorted({p for p, _ in ctx['pages']})),
            '다음 쪽은 확인 필요.' if more else '마지막 쪽까지 봤습니다.'))
        cov.append('- 검색 도구는 기간 조건을 한 쪽(10건) 안에서만 적용합니다. 본 쪽의 회의 가운데 22대 국회가 아니거나 기간 밖이라 뺀 것은 %d건입니다.'
                   % ctx['dropped'])
    else:
        cov.append('- 검색 결과 없이 만들었습니다. 다음 쪽·다른 회의는 확인 필요.')
    cov.append('- 회의 후보 %d건 중 회의록을 열어 센 회의 %d건. 열지 않은 회의는 검색에 보인 발언만 세어 "최소"로 표시했습니다.'
               % (ctx['candidates'], ctx['opened']))
    if ctx['words']:
        cov.append('- 발언 수는 낱말 "%s"이(가) 모두 들어간 발언(차례) 수입니다.' % ' '.join(ctx['words']))
    else:
        cov.append('- 주제어가 없어 발언 수는 열어 본 회의에서 그 의원이 말한 차례 수입니다(주제와 관계없이 센 값).')
    cov.append('- 위원회는 발언이 나온 회의의 위원회이며, 정당은 도구 결과에 적힌 그대로입니다.')
    checks = ['- %s' % w for w in env['warnings']] or ['- 알려 드릴 경고 없음.']
    text = read_template()
    status_line = ', '.join('%s %s' % (s['source'], s['status']) for s in env['sources']) or '조회한 자료 없음'
    for key, value in (('skill', SKILL), ('subject', cell(subject)), ('period', env['period']), ('created', created),
                       ('version', VERSION), ('status', env['status']), ('sources', status_line),
                       ('conclusion', conclusion), ('sort_note', sort_note),
                       ('table', render_table(rows, ctx['lowers'], ctx['bills_asked'])),
                       ('coverage', '\n'.join(cov)), ('checks', '\n'.join(checks))):
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
    n = 1
    while p_history(report.stem, n).exists():
        n += 1
    target = p_history(report.stem, n)
    paths.ensure(target, parent=True)
    shutil.move(str(report), str(target))
    return target


def new_run_dir(today):
    n = 1
    while p_run(today, n).exists():
        n += 1
    return paths.ensure(p_run(today, n))


def sanitized_members(path):
    """Only the allowed fields of a member list, as JSON text."""
    try:
        items = read_list(path, ('members', 'items'))
    except LoadError:
        return None
    keep = [{k: m[k] for k in MEMBER_KEEP if isinstance(m, dict) and k in m} for m in items if isinstance(m, dict)]
    return json.dumps(keep, ensure_ascii=False, indent=2) + '\n'


# ------------------------------------------------------------------------------ commands

def cmd_plan(args):
    segs = split_period(args.frm, args.to)
    print('segments: %d' % len(segs))
    for i, (a, b) in enumerate(segs, 1):
        print('segment %d: %s~%s' % (i, a.isoformat(), b.isoformat()))
    print('topic_words: %s' % (' '.join(topic_words(args.topic)) or '-'))
    print('open_max: %d' % OPEN_MAX)
    print('top: %d' % TOP)
    return 0


def load_search_for_candidates(files, g):
    pages = []
    for f in files:
        try:
            page, more, ms = read_search(f)
        except LoadError as exc:
            sys.stderr.write('%s\n' % exc)
            return None
        pages.append((page, more))
        g.add_search(ms, [(page, more)])
    return pages


def cmd_candidates(args):
    g = Gather(args.frm, args.to, topic_words(args.topic), args.open_max)
    pages = load_search_for_candidates(args.search, g)
    if pages is None:
        return 1
    seen = sorted({p for p, _ in pages})
    last = max(seen)
    more = any(m for p, m in pages if p == last)
    print('pages_seen: %s' % ','.join(str(p) for p in seen))
    gaps = [p for p in range(1, last + 1) if p not in seen]
    if gaps:
        print('pages_skipped: %s' % ','.join(str(p) for p in gaps))
    print('next_page: %s' % ('확인 필요' if more else '없음(마지막 쪽)'))
    print('dropped: %d (not 22nd Assembly or outside the period)' % g.dropped)
    order = sorted(g.meetings.values(), key=lambda m: (-m['rank'], tuple(-int(x) for x in m['date'].split('-')), m['title']))
    print('meetings: %d' % len(order))
    for i, m in enumerate(order, 1):
        names = ','.join(m['matches']) or '-'
        print('%d. %s conf_id=%s rank=%d members=%s %s%s' % (i, m['date'], m['conf_id'], m['rank'], names, m['title'],
                                                           '  <- open' if i <= args.open_max else ''))
    return 0


def cmd_build(args, parser):
    if args.to < args.frm:
        parser.error('--to is before --from')
    given = {k: getattr(args, k) or [] for k in TOOLS}
    if not any(given[k] for k in ('search', 'speeches', 'bills')):
        parser.error('give at least one saved tool result (--search, --speeches or --bills)')
    period = '%s~%s' % (args.frm.isoformat(), args.to.isoformat())
    try:
        name = paths.make_name(period=(args.frm, args.to), topic=args.topic)
        paths.resolve_root(SKILL_DIR, os.environ)
    except paths.InvalidName as exc:
        parser.error(str(exc))
    except paths.RootNotFound as exc:
        sys.stderr.write('%s\n' % exc)
        return 1

    words = topic_words(args.topic)
    g = Gather(args.frm, args.to, words, args.open_max)
    sources, loaded = [], {}
    for key in ('search', 'speeches', 'meetings', 'bills', 'members'):
        if given[key]:
            src, items, pages = read_source(key, given[key], g.warn)
            sources.append(src)
            loaded[key] = (items, pages)

    if 'search' in loaded:
        g.add_search(loaded['search'][0], loaded['search'][1])
        pages = sorted({p for p, _ in loaded['search'][1]})
        if pages:
            gaps = [p for p in range(1, max(pages) + 1) if p not in pages]
            if gaps:
                g.warn('검색 결과 %s쪽을 건너뜀 - 확인 필요' % ','.join('%d' % p for p in gaps))
            if any(m for p, m in loaded['search'][1] if p == max(pages)):
                g.warn('검색 결과 %d쪽 다음에 더 있음 - 다음 쪽은 확인 필요' % max(pages))
    if 'meetings' in loaded:
        g.add_meetings(loaded['meetings'][0])
    if 'speeches' in loaded:
        blocks = sorted(loaded['speeches'][0], key=lambda b: (str(b.get('date')), str(b.get('confer_num'))), reverse=True)
        if len(blocks) > args.open_max:
            g.warn('회의록 %d건을 받았지만 최근 %d건만 셈(나머지 %d건은 뺌)' % (len(blocks), args.open_max, len(blocks) - args.open_max))
            blocks = blocks[:args.open_max]
        g.add_speeches(blocks)
    if 'bills' in loaded:
        g.add_bills(loaded['bills'][0])
    if 'members' in loaded:
        g.add_members(loaded['members'][0])
    if g.dropped:
        g.warn('검색 결과 중 22대가 아니거나 기간 밖인 회의 %d건을 뺌' % g.dropped)
    if args.member and not words:
        g.warn('이름 검색은 그 이름이 나온 발언이 있는 회의만 찾습니다. 이름을 말하지 않고 한 발언만 있는 회의는 빠질 수 있음 - 확인 필요')

    rows, lowers = make_rows(g, [norm(m) for m in args.member or []], args.top, args.all)
    try:
        env = envelope.make_envelope(SKILL, period, sources, rows, g.warnings)
    except envelope.EnvelopeError as exc:
        sys.stderr.write('internal error, result not written: %s\n' % exc)
        return 1

    created = dates.now_iso()
    run = new_run_dir(date.today())
    paths.ensure(run / 'raw')
    inputs = []
    for key in ('search', 'speeches', 'meetings', 'bills', 'members'):
        for i, f in enumerate(given[key], 1):
            entry = {'source': TOOLS[key], 'file': Path(f).name}
            if Path(f).is_file():
                copy = Path('raw') / ('%s-%d.json' % (TOOLS[key], i))
                try:
                    if key == 'members':
                        text = sanitized_members(f)
                        if text is not None:
                            write_text_atomic(run / copy, text)
                            entry['copied_as'] = copy.as_posix()
                    else:
                        shutil.copyfile(str(f), str(run / copy))
                        entry['copied_as'] = copy.as_posix()
                except OSError:
                    pass
            inputs.append(entry)
    pages_seen = loaded['search'][1] if 'search' in loaded else []
    run_info = {'skill': SKILL, 'version': VERSION, 'created': created, 'period': period, 'status': env['status'],
                'inputs': inputs, 'transcribed': bool(args.transcribed),
                'coverage': {'search_pages': sorted({p for p, _ in pages_seen}),
                             'candidates': len(g.meetings), 'opened': len(g.opened), 'dropped': g.dropped,
                             'lower_bound': sorted(n for n, v in lowers.items() if v)}}
    write_text_atomic(run / 'run.json', json.dumps(run_info, ensure_ascii=False, indent=2) + '\n')

    ctx = {'topic': args.topic, 'wanted': [norm(m) for m in args.member or []], 'words': words,
           'pages': pages_seen, 'dropped': g.dropped, 'candidates': len(g.meetings), 'opened': len(g.opened),
           'lowers': lowers, 'bills_asked': bool(given['bills']), 'found': len(member_names(g, [])) if not args.member else len(rows)}
    report = p_report(name)
    move_to_history(report)
    write_text_atomic(report, render_report(env, name, created, ctx))
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


def outside_quotes(text):
    return _QUOTED_RE.sub('', text)


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
    table = {}
    if report_text is not None:
        for line in report_text.splitlines():
            parts = [c.strip() for c in line.strip().strip('|').split('|')]
            if line.startswith('|') and len(parts) == 6 and parts[0] not in ('의원', '---'):
                table[parts[0]] = parts
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get('id')
        name = r.get('member')
        if not isinstance(name, str) or rid != 'member-%s' % name or r.get('title') != name:
            problems.append('%s: id/title must be member-<member name> and the member name' % rid)
        if not isinstance(r.get('party'), str) or not r.get('party'):
            problems.append('%s: party must be a text' % rid)
        if not isinstance(r.get('committee'), str) or not r.get('committee'):
            problems.append('%s: committee must be a text' % rid)
        count = r.get('speech_count')
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append('%s: speech_count must be a whole number >= 0' % rid)
        if first:
            try:
                if not first <= dates.parse_date(r.get('date')) <= last:
                    problems.append('%s: date %s is outside the period' % (rid, r.get('date')))
            except dates.DateError:
                pass
        sp = r.get('speeches')
        if not isinstance(sp, list):
            problems.append('%s: speeches must be a list' % rid)
            sp = []
        if len(sp) > MAX_SPEECHES:
            problems.append('%s: %d speeches (max %d)' % (rid, len(sp), MAX_SPEECHES))
        for i, s in enumerate(sp):
            if not isinstance(s, dict) or not all(k in s for k in ('quote', 'date', 'meeting', 'url')):
                problems.append('%s: speeches[%d] needs quote, date, meeting, url' % (rid, i))
                continue
            if len(str(s['quote'])) > MAX_QUOTE:
                problems.append('%s: quote has %d chars (max %d)' % (rid, len(str(s['quote'])), MAX_QUOTE))
            if not isinstance(s['url'], str) or not re.match(r'^https?://\S+$', s['url']):
                problems.append('%s: speeches[%d] has no minutes link' % (rid, i))
            if first:
                try:
                    if not first <= dates.parse_date(s['date']) <= last:
                        problems.append('%s: speeches[%d] date %s is outside the period' % (rid, i, s['date']))
                except dates.DateError:
                    problems.append('%s: speeches[%d] date is not a real date' % (rid, i))
        bills = r.get('bills')
        if not isinstance(bills, list):
            problems.append('%s: bills must be a list' % rid)
            bills = []
        for i, b in enumerate(bills):
            if not isinstance(b, dict) or not all(k in b for k in ('no', 'name', 'url')):
                problems.append('%s: bills[%d] needs no, name, url' % (rid, i))
        if report_text is not None:
            if r.get('url') and r['url'] not in report_text:
                problems.append('%s: its minutes link is missing in the report' % rid)
            for b in bills[:SHOW_BILLS]:
                if isinstance(b, dict) and b.get('url') and b['url'] not in report_text:
                    problems.append('%s: bill %s link is missing in the report' % (rid, b.get('no')))
            line = table.get(name)
            if line is None:
                problems.append('%s: not found in the report table' % rid)
            else:
                m2 = re.match(r'^(?:최소 )?(\d+)$', line[3])
                if not m2 or int(m2.group(1)) != count:
                    problems.append('%s: report shows %r but the data says %s' % (rid, line[3], count))
    if report_text is not None:
        body = outside_quotes(report_text)
        for rx, label in FORBIDDEN:
            hit = rx.search(body)
            if hit:
                problems.append('report has %s: %r' % (label, hit.group(0)))
        if any(s.get('source') == TOOLS['search'] for s in env.get('sources', []) if isinstance(s, dict)):
            if '쪽' not in report_text or '다음 쪽' not in report_text and '마지막 쪽' not in report_text:
                problems.append('report does not say how many search pages were seen')
        if TABLE_HEAD not in report_text and rows:
            problems.append('report table header differs from the approved columns')
    blob = json.dumps(env, ensure_ascii=False) + (report_text or '')
    if _EMAIL_RE.search(blob) or _PHONE_RE.search(blob):
        problems.append('an e-mail address or phone number appears in the result')
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
    top = Parser(prog='member.py', description='assembly-member-research helper', allow_abbrev=False)
    sub = top.add_subparsers(dest='cmd', parser_class=Parser)
    sub.required = True

    def common(p):
        p.add_argument('--from', dest='frm', required=True, type=parse_day)
        p.add_argument('--to', required=True, type=parse_day)
        p.add_argument('--topic')

    plan = sub.add_parser('plan', allow_abbrev=False)
    common(plan)

    cand = sub.add_parser('candidates', allow_abbrev=False)
    common(cand)
    cand.add_argument('--search', action='append', required=True, metavar='FILE')
    cand.add_argument('--open-max', type=int, default=OPEN_MAX)

    build = sub.add_parser('build', allow_abbrev=False)
    common(build)
    for key in TOOLS:
        build.add_argument('--' + key, action='append', metavar='FILE')
    build.add_argument('--member', action='append')
    build.add_argument('--top', type=int, default=TOP)
    build.add_argument('--all', action='store_true')
    build.add_argument('--open-max', type=int, default=OPEN_MAX)
    build.add_argument('--transcribed', action='store_true')

    check = sub.add_parser('check', allow_abbrev=False)
    check.add_argument('--data', required=True)
    check.add_argument('--report')
    return top, sub


def main(argv=None):
    utf8_console()
    top, sub = make_parser()
    args = top.parse_args(argv)
    if args.cmd in ('plan', 'candidates', 'build') and args.to < args.frm:
        top.error('--to is before --from')
    if args.cmd == 'plan':
        return cmd_plan(args)
    if args.cmd == 'candidates':
        return cmd_candidates(args)
    if args.cmd == 'build':
        if args.top < 1 or args.open_max < 1:
            top.error('--top and --open-max must be 1 or more')
        return cmd_build(args, sub.choices['build'])
    return cmd_check(args, sub.choices['check'])


if __name__ == '__main__':
    sys.exit(main())
