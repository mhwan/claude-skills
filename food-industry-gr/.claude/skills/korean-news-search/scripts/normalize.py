#!/usr/bin/env python3
"""Turn the collector's raw output into clean, ordered rows and the list of articles to summarize.

Usage: normalize.py --run RUN_DIR

Reads  raw/list.json, raw/bodies.json (optional), work/job.json
Writes work/rows.json and work/articles.json (both, every time; safe to run again)
Run it once after the list is ingested (it names the articles that need a body), then again after
the bodies are ingested (it then writes the bodies of those articles to work/articles.json).
"""
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from lib import dates  # noqa: E402

_YMD = re.compile(r'^(\d{4})(\d{2})(\d{2})$')
_SAFE_URL = re.compile(r'^https?://\S+$')


def clean_text(value):
    return ' '.join(unicodedata.normalize('NFC', str(value or '')).split())


def id_time(item_id):
    """The timestamp part of a BigKinds news id (<provider>.<YYYYMMDDhhmmssSSS>); '' when there is none."""
    parts = str(item_id).split('.', 1)
    return parts[1] if len(parts) == 2 else ''


def sort_key(row):
    stamp = id_time(row['id']) or row['date'].replace('-', '') + '000000000'
    return (stamp, row['id'])


def iso_date(raw):
    m = _YMD.match(str(raw or ''))
    if not m:
        return None
    text = '%s-%s-%s' % m.groups()
    try:
        return dates.fmt(dates.parse_date(text))
    except dates.DateError:
        return None


def build_rows(items, job, warnings):
    seen = set()
    rows = []
    post_filter = set(unicodedata.normalize('NFC', n) for n in job.get('post_filter') or [])
    for item in items:
        if not isinstance(item, dict) or not item.get('id'):
            warnings.append('아이디가 없는 기사 1건을 뺐습니다.')
            continue
        item_id = str(item['id'])
        if item_id in seen:
            continue
        seen.add(item_id)
        day = iso_date(item.get('date'))
        if day is None:
            warnings.append('날짜를 읽을 수 없어 뺐습니다: %s' % item_id)
            continue
        if not job['start'] <= day <= job['end']:
            warnings.append('검색 기간 밖이라 뺐습니다: %s (%s)' % (item_id, day))
            continue
        outlet = clean_text(item.get('outlet'))
        if post_filter and outlet not in post_filter:
            continue
        url = str(item.get('url') or '').strip()
        title = clean_text(item.get('title'))
        if not title:
            warnings.append('제목이 비어 있어 확인이 필요합니다: %s' % item_id)
            title = '(제목 확인 필요)'
        rows.append({'id': item_id, 'title': title, 'date': day, 'url': url if _SAFE_URL.match(url) else None,
                     'outlet': outlet})
    rows.sort(key=sort_key, reverse=True)
    return rows


def failure(run, job, status, error):
    doc = {'status': status, 'error': error, 'total': 0, 'matched': 0, 'listed': 0, 'truncated': False,
           'incomplete': False, 'split': 0, 'requests': 0, 'warnings': [], 'rows': []}
    common.write_json(run / 'work' / 'rows.json', doc)
    common.write_json(run / 'work' / 'articles.json', {'articles': []})
    common.record_step(run, 'normalize')
    return doc


def normalize(run):
    job = common.load_job(run)
    list_path = run / 'raw' / 'list.json'
    if not list_path.is_file():
        failure(run, job, 'unread', 'raw/list.json이 없습니다. 수집 결과를 먼저 가져와야 합니다.')
        return None, 'raw/list.json is missing: collect and ingest the list first'
    try:
        raw = common.read_json(list_path)
    except ValueError as e:
        failure(run, job, 'unread', 'raw/list.json을 읽을 수 없습니다.')
        return None, 'raw/list.json is not valid JSON: %s' % e
    items = raw.get('items') if isinstance(raw, dict) else None
    status = raw.get('status') if isinstance(raw, dict) else None
    if not isinstance(items, list) or status not in ('ok', 'empty', 'unread', 'failed'):
        failure(run, job, 'unread', 'raw/list.json의 형식을 알 수 없습니다.')
        return None, 'raw/list.json has no known status or items'

    if status in ('unread', 'failed'):
        error = clean_text(raw.get('error')) or '빅카인즈에서 목록을 읽지 못했습니다.'
        failure(run, job, status, error)
        return None, 'the collector reported %s: %s' % (status, error)
    if status == 'ok' and not items:
        failure(run, job, 'unread', '수집이 정상이라고 했지만 기사가 0건입니다. 없는 것으로 단정할 수 없습니다.')
        return None, 'status ok with no items cannot be proven empty'
    if status == 'empty' and items:
        failure(run, job, 'unread', '수집이 "없음"이라고 했지만 기사 목록이 있습니다. 결과를 믿을 수 없습니다.')
        return None, 'status empty but the list has items'

    warnings = []
    rows = build_rows(items, job, warnings)
    matched = len(rows)
    caps = job.get('caps') or common.CAPS
    listed = rows[:caps['list']]
    total = raw.get('total')
    if not isinstance(total, int) or isinstance(total, bool):
        total = len(items)
    incomplete = bool(raw.get('truncated'))
    naver = raw.get('source') == 'naver'
    if incomplete and naver:
        warnings.append('네이버 검색은 검색어 하나에 최신 기사 약 1,100건까지만 보여 줍니다. 기간 끝까지 닿지 못한 검색이 있어 '
                        '일부만 모았습니다. 빅카인즈가 돌아오면 다시 모아야 합니다.')
    elif incomplete:
        warnings.append('검색 결과가 %d건이라 한 번에 가져올 수 있는 한도(2,000건)를 넘었습니다. 일부만 수집했습니다. '
                        '기간을 나누어 다시 찾아야 빠짐없이 볼 수 있습니다.' % total)

    start = int(job.get('summary_from') or 0)
    # The Naver fallback has no article text at all: nothing is summarized and no body file is read.
    chosen = [] if naver else listed[start:start + caps['summaries']]
    bodies_path = run / 'raw' / 'bodies.json'
    bodies_present = bodies_path.is_file() and not naver
    articles, no_body = [], []
    if bodies_present:
        try:
            bodies = (common.read_json(bodies_path) or {}).get('bodies') or {}
        except (ValueError, AttributeError):
            bodies = {}
        for row in chosen:
            entry = bodies.get(row['id'])
            text = entry.get('text') if isinstance(entry, dict) else None
            if isinstance(text, str) and text.strip():
                articles.append({'id': row['id'], 'title': row['title'], 'text': text})
            else:
                no_body.append(row['id'])
                warnings.append('본문을 가져오지 못해 요약하지 않았습니다: %s' % row['id'])

    doc = {'status': 'ok' if listed else 'empty', 'error': None, 'total': total, 'matched': matched,
           'listed': len(listed), 'truncated': matched > len(listed), 'incomplete': incomplete,
           'split': int(raw.get('split') or 0), 'requests': int(raw.get('requests') or 0),
           'warnings': warnings, 'rows': listed}
    if naver:
        doc['source'] = 'naver'
        doc['fallback'] = raw.get('fallback') if isinstance(raw.get('fallback'), dict) else {}
    common.write_json(run / 'work' / 'rows.json', doc)
    common.write_json(run / 'work' / 'articles.json', {'articles': articles})
    common.record_step(run, 'normalize')
    result = {'status': doc['status'], 'rows': len(listed), 'matched': matched, 'total': total,
              'to_summarize': [r['id'] for r in chosen], 'no_body': no_body, 'bodies_present': bodies_present,
              'articles': len(articles), 'warnings': warnings}
    if naver:
        result['source'] = 'naver'
        result['notice'] = (doc['fallback'].get('notice') or '네이버 대체판 — 본문 미확인')
    return result, None


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Normalize the raw BigKinds list into report rows.')
    parser.add_argument('--run', required=True)
    args = parser.parse_args(argv)
    try:
        run = common.resolve_run(args.run)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    result, reason = normalize(run)
    if result is None:
        return common.fail('nothing to report: %s' % reason, 1)
    common.emit(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
