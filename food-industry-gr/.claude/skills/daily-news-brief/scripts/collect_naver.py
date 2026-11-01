#!/usr/bin/env python3
"""Build raw/collected.json of a new run from the Naver news search API when BigKinds cannot be used.

Usage: collect_naver.py --date YYYY-MM-DD --evidence TEXT [--replay FILE]

The file has the shape collect.js exports, so normalize.py and every later step run unchanged
(they branch on source == 'naver'). The switch rules are the ones of korean-news-search:
  maintenance (a maintenance notice, HTTP 5xx, a non-JSON answer), connection failure, timeout -> switch to Naver
  403, 429, any other 4xx, a login or block sign                                         -> stop (exit 1)
  anything else                                                                           -> stop (exit 1, to be checked)
Only titles, links, outlets and times are kept: never the excerpt, never an article text. Nothing is
written (not even a run folder) when the Naver API answers with an error or the keys are missing: exit 1, no retry.
--replay FILE answers from saved pages instead of the API (tests): {"pages": {"QUERY": [page, ...]}};
a page may be {"http_error": CODE} to replay an error answer.
Exit code: 0 collected.json written, 1 stopped (nothing written), 2 usage error.
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
import unicodedata

import common
from lib import naver_news

NOTICE = '네이버 대체판 — 본문 미확인'
REASON_LABELS = {'maintenance': '빅카인즈 점검', 'connection': '빅카인즈 연결 실패', 'timeout': '빅카인즈 응답 시간 초과'}
EVIDENCE_MAX = 120
SOURCE_LINE = '출처: 네이버 뉴스 검색 API'

# The same query set as the emergency list of the work folder (scripts/naver_list.py, BRIEF_QUERIES).
BRIEF_QUERIES = ['사설', '칼럼', '정치', '대통령', '국회', '경제', '금융', '산업', '기업', '사회', '국제', '문화']
EDITORIAL_RE = re.compile(r'^\s*[\[【〈<(]\s*(사설|社說)\s*[\]】〉>)]')

# Copied from korean-news-search/scripts/collect_naver.py. Order matters: a block sign wins over everything else.
_BLOCK = re.compile(r'HTTP\s*4\d\d|\b4(?:0[0-9]|1[0-9]|2[0-9]|3[0-9]|5[01])\b|로그인|login|sign[ -]?in|차단|'
                    r'captcha|캡차|자동입력|forbidden|too many|unauthori[sz]ed|권한', re.I)
_TIMEOUT = re.compile(r'timeout|timed out|시간\s*초과|응답\s*없', re.I)
_MAINTENANCE = re.compile(r'점검|maintenance|서비스\s*중단|일시\s*중단|HTTP\s*5\d\d|\bstatus(?:\s*code)?\s*[:=]?\s*5\d\d\b|HTTP\s*200|'
                          r'parsererror|JSON|<html|<!doctype', re.I)
_CONNECTION = re.compile(r'failed to fetch|net::|ERR_|연결|connection|network|HTTP\s*0\b|\[object Object\]|'
                         r'^\s*error\s*$|abort', re.I)


class Stop(Exception):
    """Switching is not allowed, or the Naver search failed: nothing is written (exit 1)."""


def classify(evidence):
    """('maintenance' | 'connection' | 'timeout', None) when the switch is allowed, else (None, reason)."""
    text = unicodedata.normalize('NFC', evidence or '').strip()
    if not text:
        return None, 'no evidence was given: say what BigKinds showed (--evidence)'
    if _BLOCK.search(text):
        return None, 'BigKinds refused the request (a 4xx answer, a login or a block sign): stop, do not work around it'
    if _TIMEOUT.search(text):
        return 'timeout', None
    if _MAINTENANCE.search(text):
        return 'maintenance', None
    if _CONNECTION.search(text):
        return 'connection', None
    return None, 'the BigKinds problem is not a known maintenance, connection or timeout sign (확인 필요): stop'


def item_id(item):
    digest = hashlib.sha1(item['url'].encode('utf-8')).hexdigest()[:10]
    return 'naver-%s.%s000' % (digest, item['published'].strftime('%Y%m%d%H%M%S'))


def replay_fetch(path):
    """A fetch function answering from saved pages (tests). A missing page is an empty page."""
    pages = (common.read_json(path) or {}).get('pages') or {}
    served = {}

    def fetch(query, start):
        n = served.get(query, 0)
        served[query] = n + 1
        answers = pages.get(query) or []
        page = answers[n] if n < len(answers) else {'items': []}
        if isinstance(page, dict) and 'http_error' in page:
            status = int(page['http_error'])
            raise naver_news.NaverError(naver_news.error_kind(status), 'HTTP %d (query: %s)' % (status, query), status)
        return page
    return fetch


def collect(date, evidence, replay=None):
    reason, refusal = classify(evidence)
    if reason is None:
        raise Stop(refusal)
    if replay:
        fetch, gap = replay_fetch(replay), 0
    else:
        try:
            keys = naver_news.load_keys()
        except naver_news.NoKeys as e:
            raise Stop('the Naver API keys are not set (%s); nothing was written' % e)
        fetch, gap = (lambda q, s: naver_news.fetch_page(q, s, keys)), naver_news.REQUEST_GAP_SEC
    calls = {'n': 0}

    def counted(query, start):
        calls['n'] += 1
        return fetch(query, start)

    day = datetime.date.fromisoformat(date)
    found_all, per_query = [], []
    try:
        for query in BRIEF_QUERIES:
            items, status = naver_news.search(query, day, day, counted, gap=gap)
            # Only the 7 papers, matched by the original address (affiliates give None and are left out).
            kept = [dict(i, outlet=naver_news.outlet_of(i['original'])) for i in items
                    if i['url'].startswith(('http://', 'https://')) and naver_news.outlet_of(i['original'])]
            found_all.extend(kept)
            per_query.append({'query': query, 'status': status, 'found': len(naver_news.dedupe(kept))})
    except naver_news.NaverError as e:
        raise Stop('the Naver API answered with an error (%s); nothing was written, nothing was retried: %s' % (e.kind, e))

    unique = naver_news.dedupe(found_all)
    if not unique and all(q['status'] == 'unreached' for q in per_query):
        raise Stop('네이버 검색이 최신 기사 약 1,100건 안에서 이 날짜에 닿지 못했습니다. 없다고 단정할 수 없습니다(읽지 못함). '
                   'nothing was written')
    articles = [{
        'outlet': i['outlet'],
        'news_id': item_id(i),
        'title': i['title'],
        'body': '',
        'body_len': 0,
        'published_date': i['published'].strftime('%Y%m%d'),
        'published_at': i['published'].isoformat(),
        'url': i['url'],
        'is_editorial': bool(EDITORIAL_RE.match(i['title'])),
        'query': i['query'],
    } for i in unique]
    counts = {name: sum(1 for a in articles if a['outlet'] == name) for name in naver_news.OUTLETS}
    outlets = {name: {'totalCount': n, 'fetched': n, 'capped': False, 'error': None if n else '못 찾음'}
               for name, n in counts.items()}
    evidence_text = ' '.join(unicodedata.normalize('NFC', evidence).split())[:EVIDENCE_MAX]
    fallback = {'notice': NOTICE, 'reason': reason, 'reason_label': REASON_LABELS[reason],
                'evidence': evidence_text, 'queries': per_query, 'requests': calls['n'],
                'source_line': SOURCE_LINE, 'recollect_required': True}
    state = {
        'phase': 'done',
        'targetDate': date,
        'finishedAt': common.dates.now_iso(),
        'source': 'naver',
        'fallback': fallback,
        'outlets': outlets,
        'articles': articles,
        'errors': [],
    }
    # The run folder is made only now, after a successful search, so a stop leaves nothing behind.
    run = common.new_run(date)
    dest = common.write_json(run / 'raw' / 'collected.json', state)
    common.write_run_info(run, date, status='ingested', source='naver', fallback_reason=reason,
                          recollect_required=True)
    return {'switched': True, 'source': 'naver', 'notice': NOTICE, 'reason': reason,
            'reason_label': REASON_LABELS[reason], 'collected': str(dest), 'articles': len(articles),
            'outlets': counts, 'requests': calls['n'], 'queries': per_query, 'recollect_required': True}


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def main(argv=None):
    common.setup_output()
    parser = argparse.ArgumentParser(description='Collect the briefing articles from the Naver news search API '
                                                 'when BigKinds is down.')
    parser.add_argument('--date', required=True)
    parser.add_argument('--evidence', required=True, help='what BigKinds showed (error text or page notice)')
    parser.add_argument('--replay', default=None, help='saved Naver answer pages (tests)')
    args = parser.parse_args(argv)
    try:
        date = common.check_date(args.date)
        result = collect(date, args.evidence, args.replay)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    except Stop as e:
        emit({'switched': False, 'reason': str(e)})
        return common.fail('stopped: %s' % e, 1)
    emit(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
