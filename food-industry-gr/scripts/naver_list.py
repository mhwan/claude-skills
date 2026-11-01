#!/usr/bin/env python3
"""
빅카인즈 점검 때 쓰는 비상 기사 목록 (네이버 공식 뉴스 검색 API). 사용법과 한계: naver_list.md

  naver_list.py check                         키가 들어 있는지만 True/False 로 찍는다
  naver_list.py brief YYYY-MM-DD              7개사 조간 비상 목록
  naver_list.py watch YYYY-MM-DD YYYY-MM-DD   대관 검색어 비상 목록 (시작일 종료일)

출력: <이 폴더>/output/비상목록/*.md (--out 으로 변경)
종료코드: 0 = 완료, 1 = API 오류로 중단, 2 = 입력·키 문제

설계 원칙: 이 목록은 정식 브리핑이 아니다. 본문을 못 봤으므로 요약·논조 비교를 하지 않고,
제목·매체·날짜·원문 링크만 남긴다. 네이버 발췌(description)는 저장하지 않는다.
어디까지 못 찾았는지(잘림·미도달)를 반드시 목록에 적는다 — 조용한 누락이 가장 위험하다.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = 'https://naverapihub.apigw.ntruss.com/search/v1/news'
ID_ENV = 'NCP_APIGW_API_KEY_ID'
SECRET_ENV = 'NCP_APIGW_API_KEY'

KST = timezone(timedelta(hours=9))
PAGE_SIZE = 100
# API 상한: start 1~1000, display 1~100. 그래서 검색어 하나로 볼 수 있는 기사는 약 1,100건이다.
PAGE_STARTS = list(range(1, 1000, PAGE_SIZE)) + [1000]
REQUEST_GAP_SEC = 0.2

# 도메인만 맞추면 조선비즈(biz.chosun.com)·스포츠조선 같은 계열사가 섞인다. 호스트 앞부분까지 본다.
OUTLET_DOMAINS = {
    'chosun.com': '조선일보',
    'joongang.co.kr': '중앙일보',
    'donga.com': '동아일보',
    'hani.co.kr': '한겨레',
    'khan.co.kr': '경향신문',
    'mk.co.kr': '매일경제',
    'hankyung.com': '한국경제',
}
OUTLETS = list(OUTLET_DOMAINS.values())
MAIN_SUBDOMAINS = ('', 'www', 'm', 'news')

# API 는 검색어가 필수이고 신문사로 거를 수 없다. 넓은 말로 찾아 원문 주소로 거른다.
BRIEF_QUERIES = ['사설', '칼럼', '정치', '대통령', '국회', '경제', '금융', '산업',
                 '기업', '사회', '국제', '문화']

WATCH_SECTION_LABELS = {
    'gov': '정부·부처',
    'industry': '업계',
    'market': '시장',
    'assembly_issues': '국회',
}
WATCH_CONFIG = os.path.expanduser('~/.claude/food-industry-watch/config/watch.json')
WATCH_DEFAULT = os.path.join(ROOT, '.claude', 'skills', 'food-industry-watch',
                             'assets', 'defaults', 'watch.json')

NOTICE = ('> ⚠️ **비상 목록입니다. 정식 브리핑이 아닙니다.** 기사 본문을 확인하지 않았고, '
          '제목·매체·날짜·원문 링크만 담았습니다. 내용은 원문에서 확인하세요. '
          '날짜는 기사가 네이버에 올라온 시각 기준이라 지면 날짜와 다를 수 있습니다.')

# 파이썬 re 는 한글을 단어문자로 치므로 경계를 명시한다.
EDITORIAL_RE = re.compile(r'^\s*[\[【〈<(]\s*(사설|社說)\s*[\]】〉>)]')
TAG_RE = re.compile(r'<[^>]+>')
TOKEN_RE = re.compile(r'[가-힣A-Za-z0-9]+')
PARTICLE_RE = re.compile(r'(에서|으로|은|는|이|가|을|를|의|도|만|과|와|에|로)$')

GROUP_MIN_SHARED = 2
GROUP_MIN_OVERLAP = 0.5


class ApiError(Exception):
    """API 가 오류로 답했다. 재시도하거나 요청을 바꾸지 않고 멈춘다."""


class InputProblem(Exception):
    """입력·키 문제. 종료코드 2 로 끝낸다."""


def clean(text):
    """<b> 강조 태그와 HTML 엔티티를 벗긴다. 엔티티 안에 태그가 있을 수 있어 벗긴 뒤 한 번 더 지운다."""
    return TAG_RE.sub('', html.unescape(TAG_RE.sub('', text or ''))).strip()


def outlet_of(url):
    """7개사 본지 주소면 신문사 이름을, 아니면 None 을 돌려준다."""
    host = (urllib.parse.urlsplit(url or '').hostname or '').lower()
    for domain, name in OUTLET_DOMAINS.items():
        if host == domain:
            return name
        if host.endswith('.' + domain):
            return name if host[:-len(domain) - 1] in MAIN_SUBDOMAINS else None
    return None


def host_of(url):
    host = (urllib.parse.urlsplit(url or '').hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def safe_link(url):
    """http(s) 주소만 링크로 쓴다. 괄호·공백은 마크다운 링크를 깨므로 인코딩한다."""
    if urllib.parse.urlsplit(url or '').scheme not in ('http', 'https'):
        return ''
    return url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')


def parse_pubdate(value):
    try:
        return parsedate_to_datetime(value).astimezone(KST)
    except (TypeError, ValueError):
        return None


def load_keys():
    client_id = os.environ.get(ID_ENV)
    secret = os.environ.get(SECRET_ENV)
    missing = [name for name, val in ((ID_ENV, client_id), (SECRET_ENV, secret)) if not val]
    if missing:
        raise InputProblem('키가 없습니다: ' + ', '.join(missing) + ' (값은 채팅에 붙여 넣지 마세요)')
    return client_id, secret


def fetch_page(query, start, keys):
    """한 페이지를 받는다. User-Agent 는 건드리지 않는다(기본값 유지)."""
    params = urllib.parse.urlencode({
        'query': query, 'display': PAGE_SIZE, 'start': start,
        'sort': 'date', 'format': 'json',
    }, quote_via=urllib.parse.quote)
    req = urllib.request.Request(f'{API_URL}?{params}', headers={
        'X-NCP-APIGW-API-KEY-ID': keys[0],
        'X-NCP-APIGW-API-KEY': keys[1],
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise ApiError(f'HTTP {e.code} {describe_error(e)} (검색어: {query})')
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ApiError(f'{type(e).__name__}: {e} (검색어: {query})')


def describe_error(http_error):
    """오류 본문에서 코드와 설명만 뽑는다. 두 가지 응답 형식이 문서에 있다."""
    try:
        body = json.loads(http_error.read().decode('utf-8'))
    except (ValueError, OSError):
        return ''
    err = body.get('error') if isinstance(body.get('error'), dict) else body
    code = err.get('errorCode', '')
    message = err.get('message') or err.get('errorMessage') or ''
    return f'{code} {message}'.strip()[:200]


def search(query, first_day, last_day, keys):
    """최신순으로 페이지를 넘기며 기간 안 기사를 모은다.

    상태: complete = 시작일 이전까지 내려갔다 / truncated = 상한에 걸려 시작일까지 못 내려갔는데
    기간 안 기사는 일부 봤다 / unreached = 상한까지 봤는데 기간에 닿지도 못했다.
    """
    items = []
    seen_in_period = False
    for i, start in enumerate(PAGE_STARTS):
        if i:
            time.sleep(REQUEST_GAP_SEC)
        page = fetch_page(query, start, keys).get('items') or []
        oldest = None
        for raw in page:
            published = parse_pubdate(raw.get('pubDate'))
            if published is None:
                continue
            day = published.date()
            oldest = day if oldest is None or day < oldest else oldest
            if first_day <= day <= last_day:
                seen_in_period = True
                items.append({
                    'title': clean(raw.get('title')),
                    'url': raw.get('originallink') or raw.get('link') or '',
                    'original': raw.get('originallink') or '',
                    'published': published,
                    'query': query,
                })
        if len(page) < PAGE_SIZE or (oldest is not None and oldest < first_day):
            return items, 'complete'
    return items, 'truncated' if seen_in_period else 'unreached'


def dedupe(items):
    seen = {}
    for item in items:
        seen.setdefault(item['url'], item)
    return list(seen.values())


def tokens(title):
    words = set()
    for word in TOKEN_RE.findall(EDITORIAL_RE.sub('', title)):
        word = PARTICLE_RE.sub('', word)
        if len(word) >= 2:
            words.add(word)
    return words


def group_issues(items):
    """제목 단어가 겹치는 기사를 묶어, 2개사 이상이 다룬 묶음만 돌려준다. 내용 판단은 하지 않는다."""
    groups = []
    for item in items:
        words = tokens(item['title'])
        for group in groups:
            shared = words & group['words']
            smaller = min(len(words), len(group['words'])) or 1
            if len(shared) >= GROUP_MIN_SHARED and len(shared) / smaller >= GROUP_MIN_OVERLAP:
                group['items'].append(item)
                group['words'] |= words
                break
        else:
            groups.append({'words': set(words), 'items': [item]})
    multi = [g['items'] for g in groups if len({i['outlet'] for i in g['items']}) >= 2]
    return sorted(multi, key=len, reverse=True)


def cell(text):
    return text.replace('|', '\\|').replace('\n', ' ')


def row(item, source):
    link = safe_link(item['url'])
    link_cell = f'[원문]({link})' if link else '링크 없음'
    when = item['published'].strftime('%m-%d %H:%M')
    return f"| {when} | {cell(item['title'])} | {cell(source)} | {link_cell} |"


def table(items, source_of):
    lines = ['| 시각 | 제목 | 매체 | 링크 |', '|---|---|---|---|']
    ordered = sorted(items, key=lambda i: i['published'], reverse=True)
    return lines + [row(i, source_of(i)) for i in ordered]


def status_section(statuses):
    """검색어마다 어디까지 봤는지 적는다. 잘린 검색어가 있으면 목록이 전부가 아니라는 뜻이다."""
    bad = [q for q, s, _ in statuses if s != 'complete']
    lines = ['## 얼마나 찾았나', '']
    if bad:
        lines += [f'⚠️ 검색어 {len(statuses)}개 중 {len(bad)}개는 조회 상한(약 1,100건)에 걸려 '
                  '기간 전체를 보지 못했습니다. **빠진 기사가 있습니다.**', '']
    labels = {'complete': '기간 끝까지 확인', 'truncated': '일부만 확인(잘림)',
              'unreached': '기간에 닿지 못함'}
    lines += ['| 검색어 | 기간 내 기사 | 상태 |', '|---|---|---|']
    lines += [f'| {cell(q)} | {n} | {labels[s]} |' for q, s, n in statuses]
    return lines


def collect(queries, first_day, last_day, keys):
    items, statuses = [], []
    for i, query in enumerate(queries):
        if i:
            time.sleep(REQUEST_GAP_SEC)
        found, status = search(query, first_day, last_day, keys)
        items.extend(found)
        statuses.append((query, status, len(found)))
    return items, statuses


def build_brief(target, items, statuses):
    picked = []
    for item in dedupe(items):
        name = outlet_of(item['original'])
        if name:
            picked.append(dict(item, outlet=name))
    editorials = [i for i in picked if EDITORIAL_RE.search(i['title'])]
    news = [i for i in picked if not EDITORIAL_RE.search(i['title'])]
    source = lambda i: i['outlet']

    lines = [f'# 조간 비상 목록 — {target}', '', NOTICE, '', '## 신문사별 건수', '',
             '| 신문사 | 건수 |', '|---|---|']
    counts = {name: sum(1 for i in picked if i['outlet'] == name) for name in OUTLETS}
    lines += [f'| {name} | {counts[name]} |' for name in OUTLETS]
    empty = [name for name in OUTLETS if not counts[name]]
    if empty:
        lines += ['', f"⚠️ {', '.join(empty)}: 0건입니다. 기사가 없었다는 뜻이 아니라 못 찾았다는 뜻입니다."]
    lines += ['', '## 사설', '']
    lines += table(editorials, source) if editorials else ['찾지 못했습니다.']
    lines += ['', '## 여러 신문이 함께 다룬 이슈', '',
              '제목 단어가 겹치는 기사를 기계로 묶은 것입니다. 같은 사안이 아닐 수 있습니다.', '']
    groups = group_issues(news)
    if not groups:
        lines += ['찾지 못했습니다.', '']
    for n, group in enumerate(groups, 1):
        lines += [f'### 이슈 {n}', ''] + table(group, source) + ['']
    lines += ['## 신문사별 기사', '']
    for name in OUTLETS:
        own = [i for i in news if i['outlet'] == name]
        lines += [f'### {name}', '']
        lines += (table(own, source) if own else ['찾지 못했습니다.']) + ['']
    lines += status_section(statuses)
    lines += ['', '출처: 네이버 뉴스 검색 API']
    return '\n'.join(lines) + '\n', counts


def load_watch_queries():
    path = WATCH_CONFIG if os.path.exists(WATCH_CONFIG) else WATCH_DEFAULT
    with open(path, encoding='utf-8') as f:
        return json.load(f)['bigkinds']['queries']


def naver_query(query):
    """빅카인즈 검색식의 AND 를 공백으로 바꾼다. 따옴표는 그대로 둔다."""
    return re.sub(r'\s+AND\s+', ' ', query).strip()


def build_watch(first_day, last_day, entries, items, statuses):
    section_of = {}
    for query, sections in entries:
        section_of[query] = sections[0] if sections else ''
    unique = dedupe(items)
    source = lambda i: host_of(i['url'])
    lines = [f'# 대관 비상 목록 — {first_day} ~ {last_day}', '', NOTICE, '',
             '이 목록은 정식 대관 보고서에 합쳐지지 않습니다. 빅카인즈가 돌아오면 그 기간을 정식으로 다시 수집합니다.',
             '', f'기사 {len(unique)}건 (매체는 원문 주소로 표시)', '']
    order = list(WATCH_SECTION_LABELS)
    used = {section_of[i['query']] for i in unique}
    for section in order + sorted(used - set(order)):
        own = [i for i in unique if section_of[i['query']] == section]
        if own:
            lines += [f'## {WATCH_SECTION_LABELS.get(section, section)}', '']
            lines += table(own, source) + ['']
    if not unique:
        lines += ['찾지 못했습니다. 기사가 없었다는 뜻이 아니라 못 찾았다는 뜻입니다.', '']
    lines += status_section(statuses)
    lines += ['', '출처: 네이버 뉴스 검색 API']
    return '\n'.join(lines) + '\n', len(unique)


def write_out(out_dir, name, text):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def parse_day(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise InputProblem(f'날짜 형식이 틀렸습니다: {value} (예: 2026-11-01)')


def run(args):
    if args.command == 'check':
        print(f'{ID_ENV}: {bool(os.environ.get(ID_ENV))}')
        print(f'{SECRET_ENV}: {bool(os.environ.get(SECRET_ENV))}')
        return 0

    keys = load_keys()
    out_dir = args.out or os.path.join(ROOT, 'output', '비상목록')
    if args.command == 'brief':
        target = parse_day(args.date)
        items, statuses = collect(BRIEF_QUERIES, target, target, keys)
        text, counts = build_brief(target, items, statuses)
        path = write_out(out_dir, f'조간-{target}.md', text)
        print(' · '.join(f'{name} {counts[name]}' for name in OUTLETS))
    else:
        first_day, last_day = parse_day(args.first), parse_day(args.last)
        if first_day > last_day:
            raise InputProblem('시작일이 종료일보다 늦습니다')
        entries = [(naver_query(q['query']), q.get('sections') or []) for q in load_watch_queries()]
        items, statuses = collect([q for q, _ in entries], first_day, last_day, keys)
        text, total = build_watch(first_day, last_day, entries, items, statuses)
        path = write_out(out_dir, f'대관-{first_day}_{last_day}.md', text)
        print(f'기사 {total}건')
    incomplete = sum(1 for _, s, _ in statuses if s != 'complete')
    print(f'검색어 {len(statuses)}개 중 기간을 다 못 본 검색어 {incomplete}개')
    print(path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='빅카인즈 점검 때 쓰는 비상 기사 목록')
    parser.add_argument('--out', help='저장 폴더 (기본: 이 폴더의 output/비상목록)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    brief = sub.add_parser('brief')
    brief.add_argument('date')
    watch = sub.add_parser('watch')
    watch.add_argument('first')
    watch.add_argument('last')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ApiError as e:
        print(f'네이버 API 오류로 멈췄습니다. 저장한 목록은 없습니다.\n{e}', file=sys.stderr)
        return 1
    except InputProblem as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
