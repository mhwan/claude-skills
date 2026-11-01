#!/usr/bin/env python3
"""Build raw/list.json from the Naver news search API when BigKinds cannot be used (the Naver fallback).

Usage: collect_naver.py --run RUN_DIR --evidence TEXT [--replay FILE]

--evidence is what BigKinds showed: the collector's status().error, or the words of the page notice.
It decides whether switching is allowed:
  maintenance (a maintenance notice, HTTP 5xx, a non-JSON answer), connection failure, timeout -> switch to Naver
  403, 429, any other 4xx, a login or block sign                                         -> stop (exit 1)
  anything else                                                                           -> stop (exit 1, to be checked)
Only titles, links, outlets and times are kept: never the excerpt, never an article text. Nothing is
written when the Naver API answers with an error (wrong key, limit reached ...): exit 1, no retry.
--replay FILE answers from saved pages instead of the API (tests): {"pages": {"QUERY": [page, ...]}};
a page may be {"http_error": CODE} to replay an error answer.
Exit code: 0 list written, 1 stopped (nothing written), 2 usage error.
"""
import datetime
import hashlib
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from lib import naver_news  # noqa: E402
from run_search import KNOWN_PROVIDER_CODES  # noqa: E402

NOTICE = '네이버 대체판 — 본문 미확인'
REASON_LABELS = {'maintenance': '빅카인즈 점검', 'connection': '빅카인즈 연결 실패', 'timeout': '빅카인즈 응답 시간 초과'}
MAX_QUERIES = 10
EVIDENCE_MAX = 120

# Order matters: a block sign wins over everything else, so a 403 page that also mentions maintenance still stops.
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


def _tokens(text):
    """Split on spaces, keeping a double-quoted phrase as one token."""
    return re.findall(r'-?"[^"]*"|\S+', text)


def translate(query):
    """(Naver queries, notes). Top-level OR / ',' becomes several queries; AND becomes a space."""
    notes = []
    text = unicodedata.normalize('NFC', query or '').strip()
    alternatives = []
    current = []
    for token in _tokens(text.replace(',', ' , ')):
        if token in (',', 'OR'):
            alternatives.append(current)
            current = []
        elif token == 'AND':
            continue
        else:
            current.append(token)
    alternatives.append(current)
    alternatives = [a for a in alternatives if a]
    queries = []
    for words in alternatives:
        q = ' '.join(words)
        if q not in queries:
            queries.append(q)
    if len(alternatives) > 1:
        notes.append('「OR」·「,」는 검색을 %d개로 나눠 결과를 합쳤습니다. 다른 말과 섞인 식은 묶는 순서가 빅카인즈와 같은지 확인 필요'
                     % len(queries))
    if any(len(a) > 1 for a in alternatives):
        notes.append('빅카인즈의 공백(AND)을 네이버가 같은 뜻(모든 말이 든 기사)으로 처리하는지 확인 필요')
    if any(t.startswith('-') and len(t) > 1 for a in alternatives for t in a):
        notes.append('「-말」(빼기)을 그대로 넘겼습니다. 네이버가 같은 뜻으로 처리하는지 확인 필요')
    if '"' in text:
        notes.append('따옴표(정확한 구절)를 그대로 넘겼습니다. 네이버가 같은 뜻으로 처리하는지 확인 필요')
    if '|' in text:
        notes.append('「|」를 그대로 넘겼습니다. 빅카인즈에서도 OR 가 아니었고, 네이버에서 뜻은 확인 필요')
    if '(' in text or ')' in text:
        notes.append('괄호를 그대로 넘겼습니다. 네이버가 묶음으로 처리하는지 확인 필요')
    return queries, notes


def wanted_outlets(job):
    """Outlet names to keep (empty = keep every outlet). Raises UsageError for names Naver results cannot be matched to."""
    by_code = dict((code, name) for name, code in KNOWN_PROVIDER_CODES.items())
    names = [by_code.get(c, c) for c in job.get('provider_codes') or []] + list(job.get('post_filter') or [])
    unknown = [n for n in names if n not in naver_news.OUTLETS]
    if unknown:
        raise common.UsageError('the Naver fallback can only keep the 7 known outlets by their address; '
                                'not possible for: %s' % ', '.join(unknown))
    return names


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


def collect(run, evidence, replay=None):
    job = common.load_job(run)
    reason, refusal = classify(evidence)
    if reason is None:
        raise Stop(refusal)
    list_path = run / 'raw' / 'list.json'
    if list_path.is_file():
        try:
            old = common.read_json(list_path)
        except ValueError:
            old = {}
        if isinstance(old, dict) and old.get('status') in ('ok', 'empty') and old.get('source') != 'naver':
            raise common.UsageError('raw/list.json already holds a BigKinds list (%s): there is nothing to replace'
                                    % old.get('status'))
    outlets = wanted_outlets(job)
    queries, notes = translate(job['query'])
    if not queries:
        raise common.UsageError('the query has no word left to search on Naver')
    if len(queries) > MAX_QUERIES:
        raise common.UsageError('the query makes %d Naver searches (at most %d): narrow it' % (len(queries), MAX_QUERIES))

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

    first = datetime.date.fromisoformat(job['start'])
    last = datetime.date.fromisoformat(job['end'])
    found_all, per_query = [], []
    try:
        for query in queries:
            items, status = naver_news.search(query, first, last, counted, gap=gap)
            kept = []
            for item in items:
                if not item['url'].startswith(('http://', 'https://')):
                    continue
                name = naver_news.outlet_of(item['original'])
                if outlets and name not in outlets:
                    continue
                kept.append(dict(item, outlet=name or naver_news.host_of(item['url'])))
            found_all.extend(kept)
            per_query.append({'query': query, 'status': status, 'found': len(naver_news.dedupe(kept))})
    except naver_news.NaverError as e:
        raise Stop('the Naver API answered with an error (%s); nothing was written, nothing was retried: %s' % (e.kind, e))

    unique = naver_news.dedupe(found_all)
    rows = [{'id': item_id(i), 'title': i['title'], 'date': i['published'].strftime('%Y%m%d'),
             'outlet': i['outlet'], 'url': i['url']} for i in unique]
    truncated = any(q['status'] != 'complete' for q in per_query)
    if rows:
        status, error = 'ok', None
    elif all(q['status'] == 'unreached' for q in per_query):
        status, error = 'unread', '네이버 검색이 최신 기사 약 1,100건 안에서 이 기간에 닿지 못했습니다. 없다고 단정할 수 없습니다.'
    else:
        status, error = 'empty', None
    evidence_text = ' '.join(unicodedata.normalize('NFC', evidence).split())[:EVIDENCE_MAX]
    doc = {'status': status, 'error': error, 'total': len(rows), 'matched': len(rows), 'requests': calls['n'],
           'split': 0, 'truncated': truncated, 'items': rows, 'source': 'naver',
           'fallback': {'notice': NOTICE, 'reason': reason, 'reason_label': REASON_LABELS[reason],
                        'evidence': evidence_text, 'queries': per_query, 'notes': notes,
                        'outlets': outlets, 'recollect_required': True}}
    common.write_json(list_path, doc)
    common.record_step(run, 'collect_naver', source='naver', fallback_reason=reason)
    return {'switched': True, 'source': 'naver', 'notice': NOTICE, 'reason': reason, 'status': status,
            'items': len(rows), 'requests': calls['n'], 'queries': per_query, 'notes': notes,
            'recollect_required': True}


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Build the article list from the Naver news search API when BigKinds is down.')
    parser.add_argument('--run', required=True)
    parser.add_argument('--evidence', required=True, help='what BigKinds showed (error text or page notice)')
    parser.add_argument('--replay', default=None, help='saved Naver answer pages (tests)')
    args = parser.parse_args(argv)
    try:
        run = common.resolve_run(args.run)
        result = collect(run, args.evidence, args.replay)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    except Stop as e:
        common.emit({'switched': False, 'reason': str(e)})
        return common.fail('stopped: %s' % e, 1)
    common.emit(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
