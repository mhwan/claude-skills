"""Logic of the assembly-bill-tracker skill (standard library plus scripts/lib only).

The model calls the Assembly tools and drops each result into the run folder; this module reads those
files, compares the bills with the previous snapshot, and writes the envelope, the report and the new
snapshot. Nothing here talks to the network, and no stage string is interpreted: the National
Assembly's own words are carried through untouched (see lib/assembly_stages.py).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

from lib import dates, envelope as env_lib, paths
from lib.assembly_stages import CLOSED_PASS_CLASS, is_known

SKILL = 'assembly-bill-tracker'
DEFAULT_STALL_DAYS = 60
DEFAULT_DETAIL_CAP = 30
DEFAULT_LIMIT = 300
STATE_VERSION = 1

NEW = '새로 생김'
PROGRESS = '진척'
STALLED = '멈춤'
UNCHANGED = '변화 없음'
ENDED = '종료'
FIRST_LOOK = '처음 조회'
UNCONFIRMED = '단계 미확인'
CHANGE_ORDER = (NEW, PROGRESS, ENDED, STALLED, FIRST_LOOK, UNCHANGED, UNCONFIRMED)

REPORT_TEMPLATE = Path(__file__).resolve().parent.parent / 'assets' / 'report-template.md'


class InputError(Exception):
    """The run folder or the arguments are unusable (the CLI exits with 2)."""


# --------------------------------------------------------------------------- small files

def read_json(path):
    with open(str(path), 'r', encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
    tmp = path.with_name(path.name + '.tmp')
    with open(str(tmp), 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(str(tmp), 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


# --------------------------------------------------------------------------- ranges

def split_range(start, end):
    """Split an inclusive day range into two halves (the first one is the longer); [] for a single day."""
    first = dates.parse_date(start)
    last = dates.parse_date(end)
    if first > last:
        raise ValueError('from %s is after to %s' % (start, end))
    if first == last:
        return []
    days = (last - first).days + 1
    middle = first + timedelta(days=(days + 1) // 2 - 1)
    return [(dates.fmt(first), dates.fmt(middle)), (dates.fmt(middle + timedelta(days=1)), dates.fmt(last))]


# --------------------------------------------------------------------------- change labels

def _days_between(earlier, later):
    try:
        return (dates.parse_date(later) - dates.parse_date(earlier)).days
    except dates.DateError:
        return None


def classify(state, no, stage, pass_class, propose, today, stall_days):
    """Return (label, previous stage). Only the stage string and the saved dates are compared."""
    if not stage:
        return UNCONFIRMED, None
    entry = ((state or {}).get('bills') or {}).get(str(no))
    if pass_class == CLOSED_PASS_CLASS:
        return ENDED, (entry or {}).get('stage')
    if not state:
        return FIRST_LOOK, None
    if not entry:
        if isinstance(propose, str) and propose >= str(state.get('updated') or ''):
            return NEW, None
        return FIRST_LOOK, None
    if entry.get('stage') != stage:
        return PROGRESS, entry.get('stage')
    since = entry.get('stage_since') or entry.get('first_seen')
    days = _days_between(since, today) if since else None
    if days is not None and days >= stall_days:
        return STALLED, None
    return UNCHANGED, None


def select_targets(bills, state, cap):
    """Choose which bills get a detail lookup: unseen ones first, then tracked open ones, up to cap."""
    tracked = (state or {}).get('bills') or {}
    fresh, known, closed, seen = [], [], [], set()
    for bill in bills:
        no = str(bill.get('BILL_NO'))
        if no in seen:
            continue
        seen.add(no)
        entry = tracked.get(no)
        if entry and entry.get('pass_class') == CLOSED_PASS_CLASS:
            closed.append(no)
        elif entry:
            known.append(no)
        else:
            fresh.append(no)
    wanted = fresh + known
    cap = max(int(cap), 0)
    return {'fetch': wanted[:cap], 'over_cap': wanted[cap:], 'closed': closed}


# --------------------------------------------------------------------------- reading the run folder

def load_queries(run):
    path = Path(run) / 'work' / 'queries.json'
    try:
        data = read_json(path)
    except (OSError, ValueError) as ex:
        raise InputError('work/queries.json 을 읽을 수 없습니다: %s' % ex)
    queries = data.get('queries') if isinstance(data, dict) else None
    if not isinstance(queries, list) or not queries:
        raise InputError('work/queries.json 에 queries 목록이 없습니다')
    seen = set()
    out = []
    for q in queries:
        if not isinstance(q, dict) or not isinstance(q.get('id'), str) or not q['id'] or q['id'] in seen:
            raise InputError('work/queries.json 의 조회 항목에는 겹치지 않는 id 가 필요합니다')
        seen.add(q['id'])
        q = dict(q)
        q.setdefault('proposer_kind', '의원')
        out.append(q)
    return out


def _limit_of(q):
    value = q.get('limit', DEFAULT_LIMIT)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_LIMIT


def load_list(run, q):
    """Read one assembly_bills result file. Anything not clearly a complete list is 'unread'."""
    res = {'id': q['id'], 'status': 'unread', 'error': None, 'bills': [], 'capped': False}
    if q.get('error'):
        res['error'] = str(q['error'])
        return res
    ref = q.get('file')
    if not ref:
        res['error'] = '결과 파일이 지정되지 않았습니다'
        return res
    path = Path(str(ref))
    if not path.is_absolute():
        path = Path(run) / path
    try:
        data = read_json(path)
    except (OSError, ValueError) as ex:
        res['error'] = '결과 파일을 읽을 수 없습니다 (%s)' % ex
        return res
    bills = data.get('bills') if isinstance(data, dict) else None
    if not isinstance(bills, list) or not all(isinstance(b, dict) for b in bills):
        res['error'] = '결과 파일에 bills 목록이 없습니다'
        return res
    returned = data.get('returned')
    if isinstance(returned, int) and not isinstance(returned, bool) and returned != len(bills):
        res['error'] = '결과 파일의 returned(%d)와 실제 건수(%d)가 다릅니다' % (returned, len(bills))
        return res
    res['bills'] = bills
    res['status'] = 'ok' if bills else 'empty'
    res['capped'] = len(bills) >= _limit_of(q)
    return res


def cap_issues(queries, loaded):
    """Which capped queries are not covered by finished split children."""
    by_id = {q['id']: q for q in queries}
    children = {}
    for q in queries:
        parent = q.get('parent')
        if parent in by_id:
            children.setdefault(parent, []).append(q)
    issues = {'need_split': [], 'unsplittable': [], 'missing': []}

    def visit(q):
        res = loaded[q['id']]
        if res['status'] == 'unread' or not res['capped']:
            return True
        if not q.get('split'):
            try:
                splittable = bool(split_range(q.get('from'), q.get('to')))
            except ValueError:
                splittable = False
            issues['need_split' if splittable else 'unsplittable'].append(q['id'])
            return False
        try:
            halves = split_range(q.get('from'), q.get('to'))
        except ValueError:
            halves = []
        if not halves:
            issues['unsplittable'].append(q['id'])
            return False
        done = True
        for low, high in halves:
            child = None
            for c in children.get(q['id'], []):
                if (c.get('from') == low and c.get('to') == high and c.get('proposer_kind') == q.get('proposer_kind')
                        and c.get('name') == q.get('name')):
                    child = c
                    break
            if child is None:
                issues['missing'].append({'parent': q['id'], 'from': low, 'to': high})
                done = False
            elif not visit(child):
                done = False
        return done

    for q in queries:
        if q.get('parent') not in by_id:
            visit(q)
    return issues


def merge_bills(queries, loaded):
    """All list rows of all readable queries, one per bill number (first one seen wins)."""
    seen = {}
    without_number = 0
    for q in queries:
        for row in loaded[q['id']]['bills']:
            no = str(row.get('BILL_NO') or '').strip()
            if not no:
                without_number += 1
                continue
            if no not in seen:
                item = dict(row)
                item['BILL_NO'] = no
                item['QUERY_KIND'] = q.get('proposer_kind')
                seen[no] = item
    return list(seen.values()), without_number


class Collected(object):
    def __init__(self, run):
        self.queries = load_queries(run)
        self.loaded = {q['id']: load_list(run, q) for q in self.queries}
        self.issues = cap_issues(self.queries, self.loaded)
        self.bills, self.without_number = merge_bills(self.queries, self.loaded)

    def is_open(self):
        return bool(self.issues['need_split'] or self.issues['unsplittable'] or self.issues['missing'])

    def unread(self):
        return [(q, self.loaded[q['id']]['error']) for q in self.queries if self.loaded[q['id']]['status'] == 'unread']


def usable_bills(bills):
    """Drop rows that cannot become envelope rows (no real date, no title); say why."""
    good, notes = [], []
    for bill in bills:
        no = bill['BILL_NO']
        try:
            dates.parse_date(bill.get('PROPOSE_DT'))
        except dates.DateError:
            notes.append('제안일을 읽을 수 없어 뺐습니다: 의안번호 %s (%r)' % (no, bill.get('PROPOSE_DT')))
            continue
        if not str(bill.get('BILL_NAME') or '').strip():
            notes.append('의안명이 없어 뺐습니다: 의안번호 %s' % no)
            continue
        good.append(bill)
    return good, notes


def read_detail(run, no):
    """(detail, None) or (None, reason) for raw/detail-<no>.json."""
    path = Path(run) / 'raw' / ('detail-%s.json' % no)
    try:
        data = read_json(path)
    except (OSError, ValueError) as ex:
        return None, '상세 결과 파일을 읽을 수 없습니다 (%s)' % ex
    bill = data.get('bill') if isinstance(data, dict) else None
    if not isinstance(bill, dict):
        return None, '상세 결과에 bill 항목이 없습니다'
    if bill.get('BILL_NO') is not None and str(bill.get('BILL_NO')) != str(no):
        return None, '상세 결과의 의안번호(%s)가 다릅니다' % bill.get('BILL_NO')
    stage = bill.get('PROC_STAGE_CD')
    if not isinstance(stage, str) or not stage.strip():
        return None, '상세 결과에 단계 값이 없습니다'
    alt = data.get('alternative')
    alternatives = []
    for item in (alt if isinstance(alt, list) else [alt]):
        if isinstance(item, dict) and (item.get('bill_no') or item.get('BILL_NO')):
            alternatives.append(str(item.get('bill_no') or item.get('BILL_NO')))
    pass_class = bill.get('PASSGUBN')
    return {'stage': stage.strip(), 'pass_class': pass_class.strip() if isinstance(pass_class, str) and pass_class.strip() else None,
            'committee': bill.get('JRCMIT_NM') or None, 'kind': bill.get('PPSR_KND') or None,
            'link': bill.get('LINK_URL') or None, 'alt': alternatives}, None


# --------------------------------------------------------------------------- snapshot

def state_path():
    return paths.state_dir(SKILL) / 'bills.json'


def load_state():
    """(state or None, warning or None). A damaged snapshot counts as no snapshot, with a warning."""
    path = state_path()
    if not path.exists():
        return None, None
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return None, '이전 조회 기록을 읽을 수 없어 처음 조회로 처리했습니다'
    if (not isinstance(data, dict) or data.get('version') != STATE_VERSION or not isinstance(data.get('bills'), dict)
            or not isinstance(data.get('updated'), str)):
        return None, '이전 조회 기록의 형식이 달라 처음 조회로 처리했습니다'
    return data, None


def next_state(old, confirmed, today, keep_updated):
    bills = dict(old['bills']) if old else {}
    for item in confirmed:
        prev = bills.get(item['no'])
        if prev and prev.get('stage') == item['stage']:
            since = prev.get('stage_since') or prev.get('first_seen') or today
        else:
            since = today
        bills[item['no']] = {'stage': item['stage'], 'pass_class': item['pass_class'],
                             'first_seen': (prev or {}).get('first_seen') or today, 'stage_since': since,
                             'last_seen': today, 'title': item['title']}
    updated = old['updated'] if (keep_updated and old) else today
    return {'version': STATE_VERSION, 'updated': updated, 'bills': bills}


# --------------------------------------------------------------------------- text helpers

def _numbers(nos, shown=20):
    nos = list(nos)
    text = ', '.join(nos[:shown])
    if len(nos) > shown:
        text += ' 외 %d건' % (len(nos) - shown)
    return text


def _cell(value):
    text = '' if value is None else str(value)
    return text.replace('\r', ' ').replace('\n', ' ').replace('|', '\\|')


def _title_cell(title, url):
    text = _cell(title).replace('[', '\\[').replace(']', '\\]')
    return '[%s](%s)' % (text, url) if url else text


def _template():
    with open(str(REPORT_TEMPLATE), 'r', encoding='utf-8') as handle:
        return handle.read()


def render_report(fields, rows, extras):
    lines = []
    for row in rows:
        stage = row['stage']
        if stage is None:
            shown = UNCONFIRMED
        else:
            shown = stage if row['pass_class'] is None else '%s (%s)' % (stage, row['pass_class'])
            if row['change'] == PROGRESS and row['prev_stage']:
                shown = '%s → %s' % (row['prev_stage'], shown)
        if extras.get(row['id']):
            shown += ' / 대안 ' + ', '.join(extras[row['id']])
        lines.append('| %s | %s | %s | %s | %s | %s | %s |' % (
            _cell(row['change']), _cell(row['id']), _title_cell(row['title'], row['url']), row['date'],
            _cell(row['proposer']), _cell(row['committee']), _cell(shown)))
    text = _template()
    values = dict(fields)
    values['rows'] = '\n'.join(lines) if lines else '| — | — | 조건에 맞는 의안이 없습니다 | — | — | — | — |'
    for key, value in values.items():
        text = text.replace('{{%s}}' % key, str(value))
    return text


# --------------------------------------------------------------------------- the whole build

def build(run, start, end, today, keywords, committee, proposer, stall_days, detail_cap, transcribed, save_state):
    run = Path(run)
    col = Collected(run)
    state, state_warning = load_state()
    first = dates.parse_date(start)
    last = dates.parse_date(end)
    try:
        dates.check_range(first, last, dates.parse_date(today))
    except dates.DateError as ex:
        raise InputError('기간이 올바르지 않습니다 (%s~%s, 조회일 %s): %s' % (start, end, today, ex))
    warnings = []
    if state_warning:
        warnings.append(state_warning)
    if col.without_number:
        warnings.append('의안번호가 없는 항목 %d건을 뺐습니다' % col.without_number)

    # sources: one per proposer kind, plus the problems found while reading them
    sources = []
    kinds = []
    for q in col.queries:
        if q['proposer_kind'] not in kinds:
            kinds.append(q['proposer_kind'])
    for kind in kinds:
        mine = [q for q in col.queries if q['proposer_kind'] == kind]
        read = [q for q in mine if col.loaded[q['id']]['status'] != 'unread']
        lost = [q for q in mine if col.loaded[q['id']]['status'] == 'unread']
        name = 'assembly_bills:%s' % kind
        if read:
            sources.append({'source': name, 'status': 'ok' if any(col.loaded[q['id']]['bills'] for q in read) else 'empty',
                            'requests': len(read)})
            for q in lost:
                sources.append({'source': '%s#%s' % (name, q['id']), 'status': 'unread', 'requests': 1,
                                'error': col.loaded[q['id']]['error']})
        else:
            sources.append({'source': name, 'status': 'unread', 'requests': len(lost),
                            'error': col.loaded[lost[0]['id']]['error']})
        for q in lost:
            warnings.append('의안 목록을 읽지 못했습니다: 조회 %s (%s, %s~%s) — %s' % (
                q['id'], kind, q.get('from'), q.get('to'), col.loaded[q['id']]['error']))

    by_id = {q['id']: q for q in col.queries}
    reasons = {}
    for qid in col.issues['need_split']:
        reasons.setdefault(qid, []).append('아직 기간을 나눠 다시 조회하지 않았습니다')
    for qid in col.issues['unsplittable']:
        reasons.setdefault(qid, []).append('하루치인데도 상한에 걸려 더 나눌 수 없습니다')
    for item in col.issues['missing']:
        reasons.setdefault(item['parent'], []).append('나눠 조회할 %s~%s 구간의 결과가 없습니다' % (item['from'], item['to']))
    for qid, why in reasons.items():
        q = by_id[qid]
        note = ('한 번에 가져올 수 있는 상한(%d건)에 걸려 일부만 조회되었습니다: 조회 %s (%s, %s~%s) — %s'
                % (_limit_of(q), qid, q['proposer_kind'], q.get('from'), q.get('to'), '; '.join(why)))
        warnings.append(note)
        sources.append({'source': 'assembly_bills:상한 도달 %s' % qid, 'status': 'unread', 'requests': 0, 'error': note})

    good, notes = usable_bills(col.bills)
    warnings.extend(notes)
    targets = select_targets(good, state, detail_cap)
    details, failures = {}, {}
    for no in targets['fetch']:
        found, why = read_detail(run, no)
        if found is None:
            failures[no] = why
        else:
            details[no] = found
    if targets['fetch']:
        if details:
            sources.append({'source': 'assembly_bill_detail', 'status': 'ok', 'requests': len(details)})
            if failures:
                sources.append({'source': 'assembly_bill_detail:읽지 못함', 'status': 'unread', 'requests': len(failures),
                                'error': next(iter(failures.values()))})
        else:
            sources.append({'source': 'assembly_bill_detail', 'status': 'unread', 'requests': len(failures),
                            'error': next(iter(failures.values()))})
    if failures:
        warnings.append('단계 미확인 %d건: 상세 조회 결과를 읽지 못했습니다 (의안번호 %s)' % (len(failures), _numbers(failures)))
    if targets['over_cap']:
        warnings.append('단계 미확인 %d건: 상세 조회 상한 %d건을 넘어 확인하지 않았습니다 (의안번호 %s)'
                        % (len(targets['over_cap']), detail_cap, _numbers(targets['over_cap'])))

    # rows
    rows, extras, confirmed, unknown = [], {}, [], {}
    tracked = (state or {}).get('bills') or {}
    for bill in good:
        no = bill['BILL_NO']
        found = details.get(no)
        entry = tracked.get(no)
        if found:
            stage, pass_class = found['stage'], found['pass_class']
            committee_name = found['committee'] or bill.get('COMMITTEE') or None
            kind = found['kind'] or bill.get('QUERY_KIND')
            url = bill.get('DETAIL_LINK') or found['link']
            extras[no] = found['alt']
            label, previous = classify(state, no, stage, pass_class, bill['PROPOSE_DT'], today, stall_days)
        elif no in targets['closed']:
            stage, pass_class = entry.get('stage'), entry.get('pass_class')
            committee_name = bill.get('COMMITTEE') or None
            kind = bill.get('QUERY_KIND')
            url = bill.get('DETAIL_LINK')
            label, previous = ENDED, None
        else:
            stage, pass_class = None, None
            committee_name = bill.get('COMMITTEE') or None
            kind = bill.get('QUERY_KIND')
            url = bill.get('DETAIL_LINK')
            label, previous = UNCONFIRMED, None
        if stage is not None:
            confirmed.append({'no': no, 'stage': stage, 'pass_class': pass_class, 'title': bill['BILL_NAME'].strip()})
            if not is_known(pass_class, stage):
                unknown.setdefault((pass_class, stage), []).append(no)
        rows.append({'id': no, 'title': bill['BILL_NAME'].strip(), 'date': bill['PROPOSE_DT'], 'url': url or None,
                     'stage': stage, 'pass_class': pass_class, 'committee': committee_name,
                     'proposer': bill.get('PROPOSER') or None, 'proposer_kind': kind, 'change': label,
                     'prev_stage': previous})
    for (pass_class, stage), nos in unknown.items():
        warnings.append('미해석 단계값 「%s / %s」: 의안번호 %s — 해석하지 않고 국회 표기 그대로 보여줍니다'
                        % (pass_class, stage, _numbers(nos)))
    rows.sort(key=lambda r: (CHANGE_ORDER.index(r['change']), [-ord(ch) for ch in r['date']], r['id']))
    no_link = [r['id'] for r in rows if not r['url']]

    period = '%s~%s' % (start, end)
    built = env_lib.make_envelope(SKILL, period, sources, rows, warnings, validate_result=False)
    built['warnings'] = [w for w in built['warnings'] if not w.startswith('url missing: ')]
    if no_link:
        built['warnings'].append('링크 없음: 의안번호 %s — 의안 페이지 링크를 받지 못했습니다' % ', '.join(no_link))
    env_lib.assert_valid(built)

    status = built['status']
    has_unconfirmed = any(r['change'] == UNCONFIRMED for r in rows)
    state_saved = False
    keep_updated = bool(state) and (status == 'partial' or has_unconfirmed)
    if save_state and status in ('ok', 'empty', 'partial'):
        state_saved = True
    counts = {}
    for r in rows:
        counts[r['change']] = counts.get(r['change'], 0) + 1
    count_text = ' · '.join('%s %d' % (label, counts[label]) for label in CHANGE_ORDER if label in counts)

    notices = ['- 변화 표시: 「%s」 지난 조회 이후 제안됨 · 「%s」 국회 단계 표기가 지난 조회와 다름 · 「%s」 같은 단계로 %d일 이상 · '
               '「%s」 처리의안(공포·철회 등) · 「%s」 지난 기록에 없던 이전 의안' % (NEW, PROGRESS, STALLED, stall_days, ENDED, FIRST_LOOK)]
    if not state:
        tail = '이번 결과를 기록했으므로 다음 조회부터 비교합니다.' if state_saved else '이번 결과는 기록하지 않았습니다.'
        notices.append('- 지난 조회 기록이 없어 「멈춤」 여부는 판단하지 않았습니다 (멈춤 여부 확인 필요). ' + tail)
    for w in built['warnings']:
        if '일부만' in w:
            notices.append('- ※ ' + w)
    if has_unconfirmed:
        notices.append('- ※ 「%s」 의안은 국회 단계를 확인하지 못해 변화를 판단하지 않았습니다.' % UNCONFIRMED)
    fields = {
        'keywords': ', '.join(keywords) if keywords else '지정 안 함', 'period': period,
        'committee': committee or '전체', 'proposer': proposer or '전체', 'today': today, 'stall_days': stall_days,
        'detail_cap': detail_cap, 'count': len(rows), 'change_counts': '(%s)' % count_text if count_text else '',
        'notices': '\n'.join(notices),
        'warnings': '\n'.join('- ' + w for w in built['warnings']) if built['warnings'] else '없음',
    }
    text = render_report(fields, rows, extras)

    # files: report (older one with the same name goes to history), data, snapshot, run.json
    topic = '+'.join(keywords) if keywords else None
    try:
        name = paths.make_name(period=(start, end), topic=topic)
    except paths.InvalidName:
        name = paths.make_name(period=(start, end))
    report = paths.report_path(SKILL, name)
    if report.exists():
        n = 1
        while paths.history_path(SKILL, name, n).exists():
            n += 1
        old = paths.history_path(SKILL, name, n)
        paths.ensure(old, parent=True)
        shutil.copyfile(str(report), str(old))
    write_text(report, text)
    data = paths.data_path(SKILL, name)
    env_lib.write_envelope(data, built)
    state_file = None
    if state_saved:
        write_json(state_path(), next_state(state, confirmed, today, keep_updated))
        state_file = state_path()
    meta_path = run / 'run.json'
    try:
        meta = read_json(meta_path)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    meta.update({'skill': SKILL, 'transcribed': bool(transcribed), 'stall_days': stall_days, 'detail_cap': detail_cap,
                 'keywords': list(keywords), 'period': period, 'built': dates.now_iso(), 'envelope': str(data),
                 'report': str(report), 'state_saved': state_saved})
    write_json(meta_path, meta)
    return {'envelope': str(data), 'report': str(report), 'state': str(state_file) if state_file else None,
            'status': status, 'rows': len(rows), 'warnings': built['warnings'], 'exit_code': env_lib.exit_code(built)}
