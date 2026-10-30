"""gov-notice-watch: Korean ministry press releases and notices open for comment.

Commands
  plan   print the period, the agencies, the sources and the input for the lawmaking-centre step.
         Makes no network request.
  build  collect, filter and write the report, the data file, the raw evidence and the run record.
  check  verify a data file, and its report when one is given, against the rules. Writes nothing.
  summarize  check the summaries the reader wrote (against the attachment text or the raw bodies),
         re-render the report and delete the raw bodies and attachments. Run once per build run.
  attach  download the HWP/PDF attachments listed by build. Run only after the user approves.

All commands take --home DIR (absolute output folder, the same as the GR_HOME variable).

Exit codes (envelope.exit_code): 0 clean, 1 nothing could be read or the folder is missing,
2 usage error, 3 partial result or something to look at.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import datetime
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

SKILL = 'gov-notice-watch'
SCHEMA = 'gr.gov-notice-watch/1'
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULTS = SKILL_DIR / 'assets' / 'defaults' / 'gov-notice-watch.json'

# The library modules import each other by top-level name, so both folders go on sys.path.
for _folder in (SKILL_DIR / 'scripts', SKILL_DIR / 'scripts' / 'lib'):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

import net  # noqa: E402
import html_util  # noqa: E402
import envelope  # noqa: E402
import dates  # noqa: E402
import paths  # noqa: E402
import rules  # noqa: E402
import notices  # noqa: E402
import korea_kr  # noqa: E402
import mfds  # noqa: E402
import ftc  # noqa: E402
import lawmaking_in  # noqa: E402
import attach  # noqa: E402

SRC_KR = 'korea.kr'
SRC_MFDS = '식약처'
SRC_FTC = '공정위'
SRC_LM = '국민참여입법센터'
MFDS_NAME = '식품의약품안전처'
FTC_NAME = '공정거래위원회'
PRESS = '보도자료'
UNKNOWN_KIND = '확인 필요'
KINDS = (PRESS, '입법예고', '행정예고', '재행정예고', UNKNOWN_KIND)
OPEN_MARK = '⏰'
CLOSED_MARK = '(접수 종료)'
ALLOWED_HOSTS = ('www.korea.kr', 'www.mfds.go.kr', 'www.ftc.go.kr', 'opinion.lawmaking.go.kr')
TITLE = '# 정부 발표·입법예고 현황 (%s)'
STATUS_WORD = {'ok': '정상', 'empty': '자료 없음', 'unread': '읽지 못함', 'no_key': '키 없음'}
TOP_WORD = dict(STATUS_WORD, partial='일부만 읽음', failed='읽지 못함')
WORD_PREFIX_RE = re.compile(r'^(일부만 읽음|읽지 못함)\s*:\s*')
MFDS_REASON_RE = re.compile(r'^식약처 (\d+):.*\((.*)\)$')
SUMMARY_CAP = 30          # per table: the first 30 press releases and the first 30 notices get a summary
TITLE_SHORT = 30          # characters of a title in the report's excluded list
SUMMARY_MAX = 200         # characters
QUOTE_MAX = 40            # characters in one quoted phrase
SUMMARY_MISSING = '요약 확인 필요'
SUMMARY_BEYOND = '요약은 앞 30건만'
TITLE_ONLY = '(제목 기준)'     # label of a summary made from the title alone (no body text was read)
_NUM_RE = re.compile(r'\d+(?:[.,:/-]\s*\d+)*')
_QUOTE_RE = re.compile(r'「([^」]+)」|"([^"]+)"|“([^”]+)”')
_SEP_RE = re.compile(r'[\s.,:/~-]')


# ------------------------------------------------------------------------------ small helpers

def say(key, value):
    sys.stdout.write('%s: %s\n' % (key, value))


def fail(message, code=1):
    sys.stderr.write(message + '\n')
    raise SystemExit(code)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        fail('사용법 오류: %s' % message, 2)


def cell(text):
    """One table cell: whitespace collapsed, pipes replaced so the table keeps its shape."""
    return ' '.join(str(text or '').split()).replace('|', '/')


def read_text(path):
    with open(str(path), encoding='utf-8') as fh:
        return fh.read()


def write_text(path, text):
    with open(str(path), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def read_json(path):
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError) as e:
        fail('설정 파일을 읽지 못함: %s (%s)' % (path, e), 2)


def item(key, title, dept, kind, open_from, open_to, url, evidence, seq=None):
    """One notice or press release in the shape the build works with."""
    return {'key': key, 'title': title, 'dept': dept, 'kind': kind, 'open_from': open_from,
            'open_to': open_to, 'url': url, 'evidence': evidence, 'seq': seq}


def row_of(it):
    """The envelope row of an item (exactly the fields the skill registers)."""
    return {'id': it['key'], 'title': it['title'], 'date': it['open_from'], 'url': it['url'],
            'dept': it['dept'], 'kind': it['kind'], 'open_to': it['open_to'], 'summary': None}


# ------------------------------------------------------------------------------ period and config

def parse_range(args):
    """The period from --from/--to; without them it is this week so far (Monday through today)."""
    try:
        today = dates.parse_date(args.today)
        frm = dates.parse_date(args.frm) if args.frm else today - datetime.timedelta(days=today.weekday())
        to = dates.parse_date(args.to) if args.to else today
        dates.check_range(frm, to, today)
    except dates.DateError as e:
        fail('기간이 맞지 않음: %s (날짜는 YYYY-MM-DD, 시작 ≤ 끝 ≤ 오늘)' % e, 2)
    return frm.isoformat(), to.isoformat(), today.isoformat()


def load_config():
    """The defaults, with the user's config (same shape) replacing agencies or food_related when present."""
    cfg = read_json(DEFAULTS)
    user = paths.config_path(SKILL, skill_dir=SKILL_DIR)
    if user.is_file():
        data = read_json(user)
        if not isinstance(data, dict) or data.get('schema') != SCHEMA:
            fail('설정 파일의 schema 가 %s 이 아님: %s' % (SCHEMA, user), 2)
        for key in ('agencies', 'food_related'):
            if key in data:
                cfg[key] = data[key]
    return cfg


def select_agencies(cfg, wanted):
    """All configured agencies, or only those named with --agency (their name or an alias)."""
    agencies = cfg['agencies']
    if not wanted:
        return list(agencies)
    picked = []
    for word in wanted:
        hit = [a for a in agencies if word == a['name'] or word in a.get('aliases', [])]
        if not hit:
            fail('알 수 없는 부처 이름: %s (설정에 있는 이름이나 별칭으로 적는다)' % word, 2)
        if hit[0] not in picked:
            picked.append(hit[0])
    return [a for a in agencies if a in picked]


def agency_words(cfg):
    words = []
    for a in cfg['agencies']:
        words.append(a['name'])
        words.extend(a.get('aliases', []))
    return words


def source_names(agencies):
    names = [SRC_KR]
    selected = [a['name'] for a in agencies]
    if MFDS_NAME in selected:
        names.append(SRC_MFDS)
    if FTC_NAME in selected:
        names.append(SRC_FTC)
    names.append(SRC_LM)
    return names


# ------------------------------------------------------------------------------ collecting

def add_source(sources, name, status, error, requests):
    src = {'source': name, 'status': status, 'requests': requests}
    if error:
        src['error'] = error
    sources.append(src)


def partial_error(error):
    text = error or '일부만 읽음'
    if text.startswith('일부만 읽음'):
        return text
    return '일부만 읽음: ' + re.sub(r'^읽지 못함:\s*', '', text)


def collect_press(session, agencies, frm, to):
    """korea.kr press releases of each agency, one agency at a time; stops at the first failed read."""
    rows, seen, requests = [], set(), 0
    status, error = None, None
    for ag in agencies:
        got, st, err, req = korea_kr.collect(session, [ag], frm, to)
        requests += req
        for r in got:
            if r['id'] in seen:
                continue
            seen.add(r['id'])
            rows.append(dict(r, dept=ag['name']))
        if st in envelope.FAILED_STATUSES:
            status, error = st, err
            break
    if status is None:
        return rows, ('ok' if rows else 'empty'), None, requests
    if rows:
        return rows, 'failed', partial_error(error), requests
    return rows, status, error, requests


def collect_mfds(session, frm, to):
    before = session.requests_made
    rows, status, error, warnings, evidence = mfds.collect(session, frm, to)
    return rows, status, error, session.requests_made - before, warnings, evidence


def collect_ftc(session, frm, to):
    before = session.requests_made
    rows, status, error = ftc.collect(session, frm, to)
    return rows, status, error, session.requests_made - before


# ------------------------------------------------------------------------------ building items

def press_items(rows):
    return [item('kr-' + r['id'], r['title'], r['dept'], PRESS, r['date'], None, r['url'], None)
            for r in rows]


def mfds_items(rows, evidence, name):
    return [item('mfds-' + r['seq'], r['title'], name, r['kind'] or UNKNOWN_KIND, r['posted'],
                 r['open_to'], r['url'], evidence.get(r['seq']), seq=r['seq'])
            for r in rows]


def mfds_reasons(warnings):
    """Why a board detail page gave no deadline, by seq (the reason text the collector wrote)."""
    out = {}
    for text in warnings:
        m = MFDS_REASON_RE.match(text)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def ftc_items(rows, name):
    found, unreadable = [], []
    for r in rows:
        key = 'ftc-' + r['id']
        if not r['open_from']:
            unreadable.append('%s: 의견 기간을 목록 칸에서 읽지 못해 뺌 — 확인 필요' % key)
            continue
        found.append(item(key, r['title'], name, notices.notice_kind(r['title']) or '행정예고',
                          r['open_from'], r['open_to'], r['url'], None))
    return found, unreadable


def lm_items(rows, by_name):
    """Rows of the lawmaking-centre result for the selected agencies only (other departments are not asked for)."""
    found, unreadable = [], []
    for r in rows:
        if r['dept'] not in by_name:
            continue
        key = 'lm-' + r['id']
        if not r['open_from']:
            unreadable.append('%s: 의견 기간(%s)을 읽지 못해 뺌 — 확인 필요' % (key, r['period_text'] or '없음'))
            continue
        found.append(item(key, r['title'], r['dept'], notices.notice_kind(r['title']) or '입법예고',
                          r['open_from'], r['open_to'], r['url'], r['period_text']))
    return found, unreadable


def merge_lm(board, lm, warnings):
    """A lawmaking-centre row with the same instrument name as a board notice is the same notice.

    The board's deadline wins; the centre's period fills a missing one; a different period is a warning.
    """
    index = {}
    for it in board:
        index.setdefault((it['dept'], notices.dup_key(it['title'])), it)
    merged = list(board)
    for it in lm:
        hit = index.get((it['dept'], notices.dup_key(it['title'])))
        if hit is None:
            merged.append(it)
        elif hit['open_to'] is None and it['open_to']:
            hit['open_to'], hit['evidence'] = it['open_to'], it['evidence']
        elif it['open_to'] and it['open_to'] != hit['open_to']:
            warnings.append('%s: 같은 제목의 입법센터 자료 %s 의 의견 기간(%s)이 다른 기간이라 보드 기준으로 둠'
                            % (hit['key'], it['key'], it['open_to']))
    return merged


def food_reason(it, food, words, mode, no_match):
    """Why an item is left out, or None to keep it.

    off: nothing is left out. default: only items of a no_match_other agency with no food word.
    food_only: keeps items with a food word (include or mixed) and nothing else.
    """
    if mode == 'off':
        return None
    verdict = rules.food_verdict(it['title'], it['dept'], food, words)
    if mode == 'food_only':
        if verdict in ('include', 'mixed'):
            return None
        return '식품 단어가 없음' if verdict == 'undecided' else '식품과 무관한 단어가 있음'
    if verdict == 'other' and it['dept'] in no_match:
        return '식품 단어가 없는 부처의 항목'
    return None


def split_by_food(items, food, words, mode):
    keep, drop = [], []
    no_match = food.get('no_match_other') or []
    for it in items:
        reason = food_reason(it, food, words, mode, no_match)
        if reason:
            drop.append(dict(it, reason=reason))
        else:
            keep.append(it)
    return keep, drop


def add_unknown_deadline_warnings(listed, reasons, warnings):
    for it in listed:
        if it['open_to'] is None and not any(it['key'] in w for w in warnings):
            reason = reasons.get(it['seq'])
            warnings.append('%s: 의견 마감일을 찾지 못함 — 확인 필요%s'
                            % (it['key'], ' (%s)' % reason if reason else ''))


# ------------------------------------------------------------------------------ status words

def source_word(src):
    if src['status'] == 'failed':
        return '일부만 읽음' if str(src.get('error') or '').startswith('일부만 읽음') else '읽지 못함'
    return STATUS_WORD[src['status']]


def source_line(src):
    detail = WORD_PREFIX_RE.sub('', src.get('error') or '')
    return '%s %s%s' % (src['source'], source_word(src), (' — ' + detail) if detail else '')


def deadline_cell(open_to, today):
    state = rules.deadline_state(open_to, today)
    if state == 'open':
        return '%s %s' % (open_to, OPEN_MARK)
    if state == 'closed':
        return '%s %s' % (open_to, CLOSED_MARK)
    return UNKNOWN_KIND


# ------------------------------------------------------------------------------ report

def table(headers, rows):
    out = ['| %s |' % ' | '.join(headers), '|%s|' % '|'.join(' --- ' for _ in headers)]
    out += ['| %s |' % ' | '.join(str(c) for c in row) for row in rows]
    if not rows:
        out.append('해당 없음.')
    return out


def is_open(row, today):
    """A notice whose comment period has not ended by today (an unknown deadline is not open)."""
    return bool(row.get('open_to')) and row['open_to'] >= today


def summary_ids(rows, today):
    """IDs that may carry a summary: the first press releases, then the first notices, open comment periods first."""
    press = [r for r in rows if r.get('kind') == PRESS]
    notices = sorted((r for r in rows if r.get('kind') != PRESS), key=lambda r: not is_open(r, today))
    return {r.get('id') for r in press[:SUMMARY_CAP] + notices[:SUMMARY_CAP]}


def summary_cell(row, slots):
    """Summary cell: the summary, the missing-summary label when there is none, or the past-the-cap label."""
    if row['id'] not in slots:
        return SUMMARY_BEYOND
    if not row.get('summary'):
        return SUMMARY_MISSING
    return cell(row['summary'])


def dropped_label(d):
    """Dept and shortened title of one item left out as unrelated, as the report shows it."""
    title = ' '.join(str(d['title']).split())
    if len(title) > TITLE_SHORT:
        title = title[:TITLE_SHORT - 1] + '…'
    return '%s · %s' % (d['dept'], title)


def render_report(env, today, topic, dropped):
    press = [r for r in env['rows'] if r['kind'] == PRESS]
    others = [r for r in env['rows'] if r['kind'] != PRESS]
    slots = summary_ids(env['rows'], today)
    head = [TITLE % env['period'], '', '- 스킬: %s' % SKILL, '- 만든 시각: %s' % dates.now_iso()]
    if topic:
        head.append('- 주제: %s' % topic)
    head += ['- 기준일: %s (마감 표시 기준)' % today,
             '- 전체 상태: %s' % TOP_WORD.get(env['status'], env['status']),
             '- 조회 상태: %s' % ' · '.join(source_line(s) for s in env['sources'])]
    body = ['', '## 보도자료', '']
    body += table(['부처', '제목', '요약', '게시일', '링크'],
                  [[cell(r['dept']), cell(r['title']), summary_cell(r, slots), r['date'],
                    r['url'] or ''] for r in press])
    body += ['', '## 입법·행정예고 (의견 접수)', '']
    body += table(['부처', '제목', '요약', '게시일', '구분', '의견 마감일', '링크'],
                  [[cell(r['dept']), cell(r['title']), summary_cell(r, slots), r['date'],
                    r['kind'], deadline_cell(r['open_to'], today), r['url'] or ''] for r in others])
    body += ['', '## 확인할 것', '']
    todo = []
    if dropped:
        todo.append('식품과 관련 없어 보여 뺀 것: %d건 (%s)' % (len(dropped), ', '.join(dropped_label(d) for d in dropped)))
    missing = [r['id'] for r in env['rows'] if r['id'] in slots and not r.get('summary')]
    if missing:
        todo.append('요약 확인 필요: %d건 — 원문 링크에서 직접 확인 (%s)' % (len(missing), ', '.join(missing)))
    todo += env['warnings']
    if not todo:
        todo = ['확인할 것 없음.']
    body += ['- %s' % t for t in todo]
    return '\n'.join(head + body) + '\n'


def rows_under(lines, heading):
    """Data rows (cell lists) of the first table under '## <heading>', without its header line."""
    start = next((i + 1 for i, line in enumerate(lines) if line.startswith('## ') and heading in line), None)
    if start is None:
        return []
    rows = []
    for line in lines[start:]:
        if line.startswith('## '):
            break
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if all(set(c) <= set('-: ') for c in cells):
            continue
        rows.append(cells)
    return rows[1:]


def header(lines, key):
    prefix = '- %s: ' % key
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


# ------------------------------------------------------------------------------ check

def allowed_url(url):
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.hostname in ALLOWED_HOSTS


def check_data(env, names, today):
    problems = ['데이터 형식: %s' % p for p in envelope.validate(env)]
    if not isinstance(env, dict) or not isinstance(env.get('rows'), list):
        return problems
    seen, seen_content = set(), set()
    for row in env['rows']:
        if not isinstance(row, dict):
            continue
        rid = row.get('id')
        content = (row.get('dept'), row.get('title'), row.get('date'))
        if rid in seen or content in seen_content:
            problems.append('중복 행: %s' % rid)
        seen.add(rid)
        seen_content.add(content)
        url = row.get('url')
        if url is None:
            problems.append('링크 없음: %s' % rid)
        elif not allowed_url(url):
            problems.append('허용되지 않은 주소: %s (%s)' % (rid, url))
        if row.get('kind') not in KINDS:
            problems.append('구분 값이 맞지 않음: %s (%s)' % (rid, row.get('kind')))
        if row.get('dept') not in names:
            problems.append('부처 이름이 맞지 않음: %s (%s)' % (rid, row.get('dept')))
        open_to, day = row.get('open_to'), row.get('date')
        if isinstance(open_to, str) and isinstance(day, str) and open_to < day:
            problems.append('날짜 순서가 맞지 않음: %s (의견 마감 %s 이 시작 %s 보다 앞)' % (rid, open_to, day))
    return problems


def compare_table(rows, table_rows, expect):
    problems, by_url, seen = [], {}, set()
    for cells in table_rows:
        by_url[cells[-1]] = cells
    for row in rows:
        url = row.get('url')
        if not url:
            continue
        seen.add(url)
        if url not in by_url:
            problems.append('보고서에 없음: %s' % row.get('id'))
        else:
            problems += expect(row, by_url[url])
    for url in by_url:
        if url not in seen:
            problems.append('데이터에 없음: %s' % url)
    return problems


def press_expect(slots):
    def expect(row, cells):
        want = [cell(row.get('dept')), cell(row.get('title')), summary_cell(row, slots), row.get('date')]
        if cells[:4] != want:
            return ['보고서 내용이 다름: %s (보고서 %s, 데이터 %s)' % (row.get('id'), cells[:4], want)]
        return []
    return expect


def notice_expect(today, slots):
    def expect(row, cells):
        want = [cell(row.get('dept')), cell(row.get('title')), summary_cell(row, slots),
                row.get('date'), row.get('kind')]
        deadline = deadline_cell(row.get('open_to'), today)
        if cells[:5] != want:
            return ['보고서 내용이 다름: %s (보고서 %s, 데이터 %s)' % (row.get('id'), cells[:5], want)]
        got = cells[5] if len(cells) > 5 else ''
        if got != deadline:
            return ['마감 표시가 맞지 않음: %s (보고서 %r, 데이터 기준 %r)' % (row.get('id'), got, deadline)]
        return []
    return expect


def check_report(text, env, today):
    lines = text.splitlines()
    problems = []
    title = TITLE % env.get('period')
    if not lines or lines[0] != title:
        problems.append('보고서 기간이 맞지 않음: 제목 %r (데이터 기준 %r)' % (lines[0] if lines else '', title))
    top, want_top = header(lines, '전체 상태'), TOP_WORD.get(env.get('status'))
    if top is None:
        problems.append('보고서 머리글 없음: 전체 상태')
    elif top != want_top:
        problems.append('조회 상태가 맞지 않음: 전체 상태 %r (데이터 기준 %r)' % (top, want_top))
    shown = header(lines, '조회 상태') or ''
    for src in env.get('sources', []):
        need = '%s %s' % (src['source'], source_word(src))
        if need not in shown:
            problems.append('조회 상태가 맞지 않음: %r 이 보고서에 없음' % need)
    rows = [r for r in env.get('rows', []) if isinstance(r, dict)]
    slots = summary_ids(rows, today)
    press_rows = [r for r in rows if r.get('kind') == PRESS]
    notice_rows = [r for r in rows if r.get('kind') != PRESS]
    problems += compare_table(press_rows, rows_under(lines, PRESS), press_expect(slots))
    problems += compare_table(notice_rows, rows_under(lines, '입법·행정예고'),
                              notice_expect(today, slots))
    return problems


def check_summaries(env, today):
    """A summary sits only in the slots of its table and must fit one table cell."""
    rows = env.get('rows') if isinstance(env, dict) else None
    if not isinstance(rows, list):
        return []
    rows = [r for r in rows if isinstance(r, dict)]
    slots = summary_ids(rows, today)
    problems = []
    for r in rows:
        if r.get('summary') is None:
            continue
        if r.get('id') not in slots:
            problems.append('요약이 앞 30건 밖에 있음: %s' % r.get('id'))
        elif not isinstance(r['summary'], str) or len(r['summary']) > SUMMARY_MAX:
            problems.append('요약 형식이 맞지 않음: %s' % r.get('id'))
    return problems


def cmd_check(args):
    try:
        env = envelope.read_envelope(args.data, validate_after=False)
    except (OSError, ValueError) as e:
        fail('데이터 파일을 읽지 못함: %s (%s)' % (args.data, e), 2)
    try:
        cfg = load_config()
    except paths.RootNotFound:
        cfg = read_json(DEFAULTS)
    names = [a['name'] for a in cfg['agencies']]
    problems = check_data(env, names, args.today)
    problems += check_summaries(env, args.today)
    if args.report:
        try:
            text = read_text(args.report)
        except OSError as e:
            fail('보고서를 읽지 못함: %s (%s)' % (args.report, e), 2)
        problems += check_report(text, env, args.today)
    count = len(env['rows']) if isinstance(env, dict) and isinstance(env.get('rows'), list) else 0
    say('확인한 행', '%d건' % count)
    for p in problems:
        sys.stdout.write('- %s\n' % p)
    sys.stdout.write('불일치 %d건\n' % len(problems))
    return 0 if not problems else 1


# ------------------------------------------------------------------------------ summaries

def fetch_bodies(session, first, bodies, links, notes):
    """Visible text for the first rows that have none yet, and the one document link of each korea.kr row.

    The text is kept under raw/ only, never in the data file. A page that cannot be read leaves its
    reason in notes, so the attachment log can say why a row has no link.
    """
    for it in first:
        if not it['key'].startswith(('kr-', 'ftc-')) or it['key'] in bodies:
            continue
        try:
            page = html_util.get_text(session, it['url'])
        except html_util.FetchFailed as e:
            notes[it['key']] = str(e)
            continue
        bodies[it['key']] = html_util.page_text(page)
        if it['key'].startswith('kr-'):
            found = attach.file_links(page, it['url'], ALLOWED_HOSTS)
            if found:
                links[it['key']] = found


def write_bodies(raw, first, bodies):
    """Each body as raw/body/<key>.txt, for the first rows only. Returns how many were written."""
    folder = raw / 'body'
    folder.mkdir()
    count = 0
    for it in first:
        text = bodies.get(it['key'])
        if not text or not text.strip() or not re.fullmatch(r'[\w-]+', it['key']):
            continue
        write_text(folder / (it['key'] + '.txt'), text)
        count += 1
    return count


def attach_log(first, links, notes):
    """One entry per korea.kr row of the summary slots: the document link, or why there is none."""
    log = {}
    for it in first:
        key = it['key']
        if not key.startswith('kr-'):
            continue
        if key in links:
            log[key] = {'attach': 'listed', 'reason': None}
        elif key in notes:
            log[key] = {'attach': 'unread', 'reason': notes[key]}
        else:
            log[key] = {'attach': 'no_link', 'reason': '본문 페이지에 HWP·PDF 링크가 없음'}
    return log


def review_summary(entry, body):
    """Why a summary is refused against its body, or None when it may be used.

    Each number, each quoted phrase and each term in the summary must appear in the body.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get('summary'), str):
        return '형식이 맞지 않음 (summary 글자 필요)'
    text = ' '.join(entry['summary'].split())
    if not text:
        return '요약이 비어 있음'
    if len(text) > SUMMARY_MAX:
        return '요약이 %d자 (최대 %d자)' % (len(text), SUMMARY_MAX)
    spaced = ' '.join(body.split())
    flat = _SEP_RE.sub('', body)
    for num in _NUM_RE.findall(text):
        if _SEP_RE.sub('', num) not in flat:
            return '숫자 %s 가 원문에 없음' % num
    for m in _QUOTE_RE.finditer(text):
        quoted = ' '.join(next(g for g in m.groups() if g).split())
        if len(quoted) > QUOTE_MAX:
            return '인용 「%s」 가 %d자 (최대 %d자)' % (quoted, len(quoted), QUOTE_MAX)
        if quoted not in spaced:
            return '인용 「%s」 가 원문에 없음' % quoted
    for term in entry.get('terms') or []:
        if ' '.join(str(term).split()) not in spaced:
            return '고유명사 %s 가 원문에 없음' % term
    return None


def title_only(entry):
    """True when the summary is labelled as made from the title alone."""
    return isinstance(entry, dict) and TITLE_ONLY in str(entry.get('summary', ''))


def read_source(run, key):
    """(source, text, reason): the text a summary is checked against, or why there is none.

    Extracted attachment text wins over the page text. A downloaded attachment with no extracted text
    gives no source, so its summary is refused rather than checked against the page.
    """
    raw = run / 'raw'
    text_path = raw / 'attach' / 'text' / (key + '.txt')
    if text_path.is_file():
        text = read_text(text_path)
        if not text.strip():
            return None, None, '첨부 글 추출 결과가 비어 있음'
        return 'attach', text, None
    if any((raw / 'attach' / (key + ext)).is_file() for ext in ('.pdf', '.hwp', '.hwpx')):
        return None, None, '첨부는 받았으나 글로 추출하지 않음'
    body_path = raw / 'body' / (key + '.txt')
    if body_path.is_file():
        return 'page', read_text(body_path), None
    return None, None, '원문이 없음'


def read_log(run):
    path = run / 'raw' / 'attach-log.json'
    return read_json(path) if path.is_file() else {}


def cmd_summarize(args):
    run = Path(args.run)
    info = read_json(run / 'run.json')
    given_path = run / 'raw' / 'summaries.json'
    if not given_path.is_file():
        fail('요약 파일이 없음: %s (raw/summaries.json 을 먼저 쓴다)' % given_path, 2)
    given = read_json(given_path)
    if not isinstance(given, dict):
        fail('요약 파일의 최상위가 객체({키: {summary, terms}})가 아님: %s' % given_path, 2)
    data = Path(info['data'])
    try:
        env = envelope.read_envelope(data, validate_after=False)
    except (OSError, ValueError) as e:
        fail('데이터 파일을 읽지 못함: %s (%s)' % (data, e), 2)
    index = {r['id']: i for i, r in enumerate(env['rows'])}
    slots = summary_ids(env['rows'], info['today'])
    body_dir = run / 'raw' / 'body'
    cleaned = not body_dir.is_dir()
    if cleaned:
        sys.stdout.write('이미 정리됨: raw/body 가 없어 다시 읽지 않고 기존 요약을 그대로 둔다\n')
    log = {} if cleaned else read_log(run)
    refused = []
    for key, entry in given.items():
        i = index.get(key)
        source, reason = None, None
        if i is None:
            reason = '데이터에 없는 행'
        elif key not in slots:
            reason = '앞 30건 밖'
        elif cleaned:
            if not env['rows'][i].get('summary'):
                reason = '원문이 없어 확인 못 함'
        else:
            source, text, reason = read_source(run, key)
            if reason is None and source == 'attach' and title_only(entry):
                reason = '첨부 원문이 있어 제목 기준 요약은 쓸 수 없음'
            if reason is None:
                reason = review_summary(entry, text)
            if reason is None:
                env['rows'][i]['summary'] = ' '.join(entry['summary'].split())
        if reason:
            refused.append((key, reason))
        if not cleaned:
            log[key] = dict(log.get(key, {}), body=source, summary='refused' if reason else 'accepted',
                            summary_reason=reason)
    envelope.write_envelope(data, env)
    dropped = read_json(run / 'raw' / 'filtered.json')
    write_text(Path(info['report']), render_report(env, info['today'], info['topic'], dropped))
    if not cleaned:
        write_json(run / 'raw' / 'attach-log.json', log)
        shutil.rmtree(str(body_dir), ignore_errors=True)
        shutil.rmtree(str(run / 'raw' / 'attach'), ignore_errors=True)
    for key, reason in refused:
        sys.stdout.write('- 요약 버림 %s: %s\n' % (key, reason))
    say('summaries', str(sum(1 for r in env['rows'] if r.get('summary'))))
    say('refused', str(len(refused)))
    say('report', info['report'])
    return 3 if refused else 0


# ------------------------------------------------------------------------------ commands

def cmd_attach(args, sleep):
    run = Path(args.run)
    read_json(run / 'run.json')
    listed = run / 'raw' / 'attachments.json'
    if not listed.is_file():
        fail('첨부 목록이 없음: %s (build 가 만든 실행 폴더를 넣는다)' % listed, 2)
    folder = run / 'raw' / 'attach'
    if folder.exists():
        fail('이미 내려받은 첨부가 있음: %s (새 실행을 만든다)' % folder, 2)
    # summarize deletes raw/attach, so the log is the record that a download already ran.
    if any('bytes' in entry for entry in read_log(run).values()):
        fail('이미 첨부를 내려받은 실행임: raw/attach-log.json (새 실행을 만든다)', 2)
    candidates = read_json(listed)
    folder.mkdir()
    (folder / 'text').mkdir()
    session = net.Session(sleep=sleep)
    results = attach.download(session, candidates, folder, ALLOWED_HOSTS)
    log = read_log(run)
    for key, result in results.items():
        log[key] = dict(log.get(key, {}), **result)
    write_json(run / 'raw' / 'attach-log.json', log)
    done = [key for key, result in results.items() if result['attach'] == 'ok']
    say('attached', str(len(done)))
    say('not_attached', str(len(results) - len(done)))
    say('folder', str(folder))
    for key, result in results.items():
        if result['attach'] != 'ok':
            sys.stdout.write('- 첨부 못 받음 %s: %s\n' % (key, result['reason']))
    say('next', 'raw/attach/ 의 파일을 kordoc parse_document 로 읽어 raw/attach/text/<키>.txt 에 저장한 뒤 summarize')
    return 0 if len(done) == len(results) else 3


def cmd_plan(args):
    frm, to, _today = parse_range(args)
    agencies = select_agencies(load_config(), args.agency)
    say('period', '%s~%s' % (frm, to))
    say('agencies', ', '.join(a['name'] for a in agencies))
    say('sources', ', '.join(source_names(agencies)))
    lm_config = {'from': frm, 'to': to, 'window_start': mfds.window_start(frm),
                 'departments': [{'code': a['lawmaking'], 'name': a['name']} for a in agencies]}
    say('lawmaking_config', json.dumps(lm_config, ensure_ascii=False))
    return 0


def keep_previous(report, name):
    """Copy an earlier report of the same name to history/<name>.vN.md before it is replaced."""
    if not report.is_file():
        return
    n = 1
    while paths.history_path(SKILL, name, n, skill_dir=SKILL_DIR).exists():
        n += 1
    hist = paths.history_path(SKILL, name, n, skill_dir=SKILL_DIR)
    paths.ensure(hist, parent=True)
    hist.write_bytes(report.read_bytes())


def new_run_dir(today):
    n = 1
    while True:
        run = paths.run_dir(SKILL, today, n, skill_dir=SKILL_DIR)
        run.parent.mkdir(parents=True, exist_ok=True)
        try:
            run.mkdir()
            return run
        except FileExistsError:
            n += 1


def cmd_build(args, sleep):
    frm, to, today = parse_range(args)
    cfg = load_config()
    agencies = select_agencies(cfg, args.agency)
    by_name = {a['name']: a for a in agencies}
    order = {a['name']: i for i, a in enumerate(agencies)}
    food = cfg.get('food_related') or {}
    words = agency_words(cfg)
    mode = 'food_only' if args.food_only else ('off' if args.agency else 'default')
    session = net.Session(sleep=sleep)
    sources, warnings, board, reasons = [], [], [], {}
    bodies, links, notes = {}, {}, {}

    rows, status, error, requests = collect_press(session, agencies, frm, to)
    add_source(sources, SRC_KR, status, error, requests)
    press = press_items(rows)

    if MFDS_NAME in by_name:
        rows, status, error, requests, mfds_warnings, evidence = collect_mfds(session, frm, to)
        bodies.update({'mfds-' + r['seq']: r['body'] for r in rows if r.get('body')})
        add_source(sources, SRC_MFDS, status, error, requests)
        board += mfds_items(rows, evidence, MFDS_NAME)
        reasons = mfds_reasons(mfds_warnings)
    if FTC_NAME in by_name:
        rows, status, error, requests = collect_ftc(session, frm, to)
        add_source(sources, SRC_FTC, status, error, requests)
        found, unreadable = ftc_items(rows, FTC_NAME)
        board += found
        warnings += unreadable

    lm = lawmaking_in.load(args.lawmaking, frm, to)
    add_source(sources, SRC_LM, lm['status'], lm['error'], lm['requests'])
    found, unreadable = lm_items(lm['rows'], by_name)
    warnings += unreadable
    listed = merge_lm(board, found, warnings)
    listed = [it for it in listed if rules.period_overlaps(it['open_from'], it['open_to'], frm, to)]

    press, dropped_press = split_by_food(press, food, words, mode)
    listed, dropped_notices = split_by_food(listed, food, words, mode)
    dropped = dropped_press + dropped_notices

    press.sort(key=lambda it: it['open_from'], reverse=True)
    press.sort(key=lambda it: order[it['dept']])
    listed.sort(key=lambda it: (it['open_to'] is None, it['open_to'] or ''))
    add_unknown_deadline_warnings(listed, reasons, warnings)

    period = '%s~%s' % (frm, to)
    env = envelope.make_envelope(SKILL, period, sources, [row_of(it) for it in press + listed], warnings)

    name = paths.make_name(period=(frm, to), topic=args.topic)
    report = paths.report_path(SKILL, name, skill_dir=SKILL_DIR)
    data = paths.data_path(SKILL, name, skill_dir=SKILL_DIR)
    run = new_run_dir(today)
    raw = run / 'raw'
    raw.mkdir()
    slots = summary_ids(env['rows'], today)
    first = [it for it in press + listed if it['key'] in slots]
    fetch_bodies(session, first, bodies, links, notes)
    body_count = write_bodies(raw, first, bodies)
    write_json(raw / 'attachments.json', links)
    write_json(raw / 'attach-log.json', attach_log(first, links, notes))

    filtered = [{'id': d['key'], 'title': d['title'], 'dept': d['dept'], 'kind': d['kind'], 'reason': d['reason']}
                for d in dropped]
    keep_previous(report, name)
    paths.ensure(report, parent=True)
    write_text(report, render_report(env, today, args.topic, filtered))
    paths.ensure(data, parent=True)
    envelope.write_envelope(data, env)
    write_json(raw / 'deadlines.json', {
        it['key']: {'open_from': it['open_from'], 'open_to': it['open_to'], 'evidence': it['evidence']}
        for it in listed})
    write_json(raw / 'filtered.json', filtered)
    write_json(run / 'run.json', {'skill': SKILL, 'period': period, 'made_at': dates.now_iso(),
                                  'sources': sources, 'status': env['status'], 'today': today,
                                  'topic': args.topic, 'data': str(data), 'report': str(report)})

    say('status', env['status'])
    say('rows', str(len(env['rows'])))
    say('food_filtered', str(len(dropped)))
    say('report', str(report))
    say('data', str(data))
    say('bodies', str(body_count))
    say('attachments', str(len(links)))
    say('run', str(run))
    for w in env['warnings']:
        sys.stdout.write('- %s\n' % w)
    return envelope.exit_code(env)


def build_parser():
    today = datetime.date.today().isoformat()
    parser = Parser(prog='govnotice.py', description='정부 발표와 의견 접수 중인 입법·행정예고 목록')
    sub = parser.add_subparsers(dest='cmd', required=True)
    home = argparse.ArgumentParser(add_help=False)
    home.add_argument('--home', help='output folder (absolute path); same as the GR_HOME variable')

    plan = sub.add_parser('plan', parents=[home], help='기간·부처·출처와 국민참여입법센터 입력 확인 (네트워크 없음)')
    plan.add_argument('--from', dest='frm')
    plan.add_argument('--to')
    plan.add_argument('--today', default=today)
    plan.add_argument('--agency', action='append')

    build = sub.add_parser('build', parents=[home], help='수집·거르기·보고서·데이터 쓰기')
    build.add_argument('--from', dest='frm')
    build.add_argument('--to')
    build.add_argument('--today', default=today)
    build.add_argument('--agency', action='append')
    build.add_argument('--topic')
    build.add_argument('--food-only', action='store_true')
    build.add_argument('--lawmaking')

    check = sub.add_parser('check', parents=[home], help='데이터(와 보고서)를 규칙으로 검사 (쓰지 않음)')
    check.add_argument('--data', required=True)
    check.add_argument('--report')
    check.add_argument('--today', default=today)
    summarize = sub.add_parser('summarize', parents=[home], help='요약 검사·반영, 보고서 다시 쓰기, 원문(raw/body) 삭제')
    summarize.add_argument('--run', required=True)
    attach_p = sub.add_parser('attach', parents=[home], help='보도자료 첨부(HWP·PDF) 내려받기 — 사용자 승인 후에만')
    attach_p.add_argument('--run', required=True)
    return parser


def main(argv=None, sleep=None):
    args = build_parser().parse_args(argv)
    if args.home:
        if not os.path.isabs(args.home):
            fail('--home must be an absolute path: %s' % args.home, 2)
        os.environ[paths.ENV_ROOT] = args.home
    try:
        if args.cmd == 'plan':
            return cmd_plan(args)
        if args.cmd == 'build':
            return cmd_build(args, sleep)
        if args.cmd == 'summarize':
            return cmd_summarize(args)
        if args.cmd == 'attach':
            return cmd_attach(args, sleep)
        return cmd_check(args)
    except paths.RootNotFound as e:
        fail(str(e), 1)
    except paths.InvalidName as e:
        fail(str(e), 2)


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
