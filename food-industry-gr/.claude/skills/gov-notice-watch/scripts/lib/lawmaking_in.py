"""Lawmaking-centre result: reads the JSON file the browser collector wrote (this script never fetches the site)."""
from __future__ import annotations

import json
import os
import re

import dates

_DATE_RE = re.compile(r'(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?')
_OK_REQUEST = 'ok'


def parse_period(text):
    """('YYYY-MM-DD', 'YYYY-MM-DD') from '2026. 10. 15. ~ 2026. 11. 14.'; None unless two valid dates in order."""
    found = []
    for m in _DATE_RE.finditer(text or ''):
        d = dates.parse_loose('%s. %s. %s.' % m.groups())
        if d is None:
            return None
        found.append(d.isoformat())
    if len(found) != 2 or found[0] > found[1]:
        return None
    return found[0], found[1]


def load(path, frm, to):
    """Read the browser result for the requested period.

    Returns {'status', 'error', 'requests', 'rows'}; status is 'ok' / 'empty' / 'unread' / 'failed'.
    rows: {id, title, dept, law_type, period_text, url, open_from, open_to} (open_* None when unreadable).
    """
    out = {'status': 'ok', 'error': None, 'requests': 0, 'rows': []}
    if not path or not os.path.isfile(path):
        out.update(status='unread', error='브라우저 결과 없음: 입법센터 수집 결과 파일이 없음 (%s)' % (path or '지정 안 함'))
        return out
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        out.update(status='failed', error='브라우저 결과를 읽지 못함: %s' % e)
        return out
    if not isinstance(data, dict) or data.get('job') != 'lawmaking' or not isinstance(data.get('rows'), list):
        out.update(status='failed', error='브라우저 결과 모양이 맞지 않음 (job/rows 없음)')
        return out
    period = data.get('period') or {}
    if period.get('from') != frm or period.get('to') != to:
        out.update(status='failed', error='브라우저 결과가 다른 기간(%s~%s)의 것이라 쓰지 않음; 요청 기간 %s~%s'
                   % (period.get('from'), period.get('to'), frm, to))
        return out
    requests = data.get('requests') or []
    out['requests'] = sum(int(r.get('pages') or 0) for r in requests if isinstance(r, dict))
    for row in data['rows']:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        parsed = parse_period(row.get('period_text'))
        out['rows'].append({
            'id': str(row['id']), 'title': row.get('title') or '', 'dept': row.get('dept') or '',
            'law_type': row.get('law_type'), 'period_text': row.get('period_text') or '',
            'url': row.get('url'), 'open_from': parsed[0] if parsed else None,
            'open_to': parsed[1] if parsed else None})
    bad = [r for r in requests if isinstance(r, dict) and r.get('status') != _OK_REQUEST]
    errors = data.get('errors') or []
    if bad or errors:
        detail = errors[0].get('detail') if errors and isinstance(errors[0], dict) else None
        out.update(status='failed', error='일부만 읽음: 입법센터 수집 중 오류 %d건%s'
                   % (len(errors) + len(bad), ' (%s)' % detail if detail else ''))
    elif not out['rows']:
        out['status'] = 'empty'
    return out
