"""State written by publish (M6): issues, notices, hygiene, seen ids (m6-m7-spec §1, addendum §C)."""
from __future__ import annotations

import copy
import re

from lib import accum, from_envelope, gates, runview

ID_RE = re.compile(r'^I-(\d{4})$')
SEEN_FIELDS = {'bigkinds': 'id', 'korea_kr': 'id', 'assembly_bills': 'bill_id',
               'lawmaking_web': 'id', 'mfds_notice': 'id', 'ftc_notice': 'id', 'mafra_notice': 'id'}


def lock_value(issue, kind):
    values = [lk['value'] for lk in (issue or {}).get('locks', []) if lk['type'] == kind]
    return values[-1] if values else None


def issue_cids(key, relevance) -> list:
    """Clusters of a todo issue: its own cid for new:…, then every issue entry linked to the key."""
    own = [key[4:]] if key.startswith('new:') else []
    return own + [c['cid'] for c in relevance['clusters']
                  if c['verdict'] == 'issue' and c['section'] != 'market' and c['issue_link'] == key]


class RunData:
    """The published run's files, loaded once."""

    def __init__(self, rd, manifest):
        self.rd, self.manifest = rd, manifest
        self.todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
        self.summary = gates.load_work(rd, 'summary', 'fiw.summary/1')
        self.relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1')
        self.prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1', required=False)
        self.clusters = {c['cid']: c for c in gates.load_work(rd, 'clusters', 'fiw.clusters/1')['clusters']}
        self.env = {s: runview.envelope(rd, s) for s in
                    ('bigkinds', 'korea_kr', 'assembly_bills', 'foodsafety') + runview.NOTICE_SOURCES}
        bk = self.env['bigkinds']
        # Naver substitute rows are not evidence: they never move an issue's dates or flow.
        self.news = {a['id']: a for a in (bk or {}).get('items', []) if not from_envelope.is_fallback(a['id'])}
        self.news_by_url = {}
        for a in self.news.values():
            if a['url']:
                self.news_by_url.setdefault(a['url'], a)

    def core_dates(self, key) -> list:
        days = []
        for cid in issue_cids(key, self.relevance):
            for aid in self.clusters.get(cid, {}).get('core_ids', []):
                if aid in self.news:
                    days.append(self.news[aid]['date'])
        return sorted(days)


def _resolver(data, todo_issue, state_issue):
    """ref -> {id or None, url, outlet, date, title} for one todo issue's refs."""
    out = {}
    for a in todo_issue['articles']:
        out[a['ref']] = {'id': a['id'], 'url': a['url'], 'outlet': a['outlet'], 'date': a['date'], 'title': a['title']}
    stored = {n['url']: n for n in (state_issue or {}).get('flow', [])}
    for fc in todo_issue['flow_candidates']:
        news = data.news_by_url.get(fc['url'])
        if news is not None:
            out[fc['ref']] = {'id': news['id'], 'url': fc['url'], 'outlet': news['outlet'], 'date': news['date'],
                              'title': news['title']}
        elif fc['url'] in stored:
            node = stored[fc['url']]
            out[fc['ref']] = {'id': node['article_id'], 'url': fc['url'], 'outlet': node.get('outlet'),
                              'date': node['date'], 'title': node['text'], 'stored': True}
        else:
            out[fc['ref']] = {'id': None, 'url': fc['url'], 'outlet': fc.get('outlet'), 'date': fc['date'],
                              'title': fc['title']}
    return out


def last_sentences(summary_issue, refs, now, run_id) -> list:
    out = []
    for s in summary_issue.get('sentences') or []:
        if s['kind'] != 'fact':
            continue
        infos = [refs[r] for r in s['refs'] if r in refs]
        out.append({'text': s['text'], 'article_ids': [i['id'] for i in infos if i['id']],
                    'urls': [i['url'] for i in infos if i['url']], 'verified_at': now, 'run_id': run_id})
    return out


def _node(info, text, now, run_id):
    node = {'date': info['date'], 'text': text, 'url': info['url'], 'article_id': info['id'],
            'verified_at': now, 'run_id': run_id}
    if info.get('outlet'):
        node['outlet'] = info['outlet']
    return node


def next_number(issues) -> int:
    nums = [int(m.group(1)) for i in issues for x in [i['id']] + i['aliases'] for m in [ID_RE.match(x)] if m]
    return max(nums, default=0) + 1


def update_issues(data, issues_obj, now, periods, weeks, run_id, issue_ids=None):
    """(new issues object, {summary key: state id}). issue_ids: a previous mapping to reuse (revisions)."""
    obj = copy.deepcopy(issues_obj) if issues_obj else {'schema': 'fiw.state.issues/1', 'issues': []}
    issues = obj['issues']
    by_id = {i['id']: i for i in issues}
    todo_by_key = {t['key']: t for t in data.todo['issues']}
    mapping = dict(issue_ids or {})
    n = next_number(issues)
    updated = set()
    for s in data.summary['issues']:
        key = s['key']
        t = todo_by_key.get(key)
        if t is None:
            continue
        target = mapping.get(key) or (None if key.startswith('new:') else accum.resolve(key, issues))
        state_issue = by_id.get(target) if target else None
        core = data.core_dates(key)
        if state_issue is None:
            target = target or f'I-{n:04d}'
            if target == f'I-{n:04d}':
                n += 1
            state_issue = {'id': target, 'title': '', 'section': t['section'], 'status': 'active',
                           'first_seen': core[0] if core else data.manifest['period']['from'],
                           'last_article': None, 'aliases': [], 'flow': [], 'last_sentences': [],
                           'locks': [], 'corrections': []}
            issues.append(state_issue)
            by_id[target] = state_issue
        mapping[key] = target
        refs = _resolver(data, t, state_issue)
        state_issue['title'] = lock_value(state_issue, 'rename') or s['title']['text']
        state_issue['section'] = lock_value(state_issue, 'pin_section') or t['section']
        if core:
            last = state_issue.get('last_article')
            state_issue['last_article'] = max(last, core[-1]) if last else core[-1]
        seen = {node['article_id'] for node in state_issue['flow']}
        additions = []
        for a in t['articles']:
            additions.append(_node(refs[a['ref']], a['title'], now, run_id))
        for fn in s.get('flow') or []:
            info = refs.get(fn['ref'])
            if info is not None and info['id'] and not info.get('stored'):
                additions.append(_node(info, fn['text'] if fn['mode'] == 'summary' else info['title'], now, run_id))
        for node in additions:
            if node['article_id'] not in seen:
                seen.add(node['article_id'])
                state_issue['flow'].append(node)
        state_issue['flow'].sort(key=lambda x: (x['date'], x['article_id']))
        state_issue['last_sentences'] = last_sentences(s, refs, now, run_id)
        state_issue['last_run'] = run_id
        state_issue['status'] = 'active'
        updated.add(target)
    for i in issues:
        if i['id'] not in updated:
            i['status'] = 'dormant' if accum.is_dormant(i, periods, weeks, run_id) else 'active'
    return obj, mapping


def update_notices(data, notices_obj, prefilter_verdicts, today, run_id):
    carried = [dict(it) for it in (notices_obj or {}).get('items', []) if it['open_to'] and it['open_to'] >= today]
    items, urls = [], set()
    for it in carried:
        if it['url'] not in urls:
            urls.add(it['url'])
            items.append(it)
    for source in runview.NOTICE_SOURCES:
        env = data.env.get(source)
        if env is None or env['status'] not in ('ok', 'partial'):
            continue
        for it in env['items']:
            if prefilter_verdicts.get((source, it['id'])) != 'include':
                continue
            if it['open_parse'] != 'ok' or not it['open_to'] or it['open_to'] < today or it['url'] in urls:
                continue
            urls.add(it['url'])
            items.append(dict(it, first_run=run_id))
    return {'schema': 'fiw.state.notices/1', 'items': items}


def update_hygiene(data, hygiene_obj, run_id, period):
    obj = copy.deepcopy(hygiene_obj) if hygiene_obj else {'schema': 'fiw.state.hygiene/1', 'periods': []}
    env = data.env.get('foodsafety')
    if env is None or env['status'] != 'ok':
        return obj
    counts = {k: sum(1 for i in env['items'] if i['kind'] == k) for k in ('recall', 'admin', 'unfit')}
    grades = {}
    for i in env['items']:
        if i['kind'] == 'recall':
            g = i['grade'] or '미확인'
            grades[g] = grades.get(g, 0) + 1
    entry = {'run_id': run_id, 'period': {'from': period['from'], 'to': period['to']}, 'counts': counts,
             'grades': grades, 'basis': dict(env.get('basis') or {})}
    obj['periods'] = [p for p in obj['periods'] if p['run_id'] != run_id] + [entry]
    return obj


def seen_ids(data, run_id):
    sources = {}
    for source, env in data.env.items():
        if env is None or env['status'] not in ('ok', 'partial'):
            continue
        if source == 'foodsafety':
            sources[source] = [f'{i["kind"]}:{i["seq"]}' for i in env['items']]
        elif source in SEEN_FIELDS:
            sources[source] = [i[SEEN_FIELDS[source]] for i in env['items']
                               if source != 'bigkinds' or not from_envelope.is_fallback(i['id'])]
    return {'schema': 'fiw.state.seen_ids/1', 'run_id': run_id, 'sources': sources}
