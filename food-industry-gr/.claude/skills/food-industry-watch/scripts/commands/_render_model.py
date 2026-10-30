"""Read-only view of one run for the renderer: every value the report shows, derived from run files."""
from __future__ import annotations

import re
from datetime import date

from lib import accum, dates, entities, gates, layout, notices, runview, schema

# Source order of the ⚠ notes, with their report labels.
SOURCE_LABELS = (
    ('bigkinds', '빅카인즈'), ('korea_kr', '정책브리핑'), ('lawmaking_web', '국민참여입법센터'),
    ('mfds_notice', '식약처 예고 게시판'), ('ftc_notice', '공정위 예고 게시판'),
    ('assembly_bills', '열린국회정보 법안'), ('assembly_schedule', '열린국회정보 위원회 일정'),
    ('foodsafety', '식품안전나라'),
)
READ_OK = ('ok', 'partial')
ISSUE_SECTIONS = {'gov': 'gov', 'assembly_issues': 'assembly', 'industry': 'industry'}
_DECREE_LABEL = {'대통령령': '시행령', '부령': '시행규칙', '총리령': '시행규칙'}
# Action-name tail: durations and a parenthetical ("business suspension 5 days (period: 2026. 10. 28. ~ …)" -> "business suspension").
_ACTION_TAIL = re.compile(r'(?:\s*\(.*\)|\s*\d+\s*(?:일|개월))+\s*$')


def _day(iso) -> date:
    return dates.parse_date(iso)


def decree_label(item):
    """Enforcement-decree / enforcement-rule kind for a decree notice (title first, then the list's law type), else None."""
    t = notices.law_type(item.get('title'))
    if t in ('시행령', '시행규칙'):
        return t
    return _DECREE_LABEL.get(item.get('law_type') or '')


def top(values, n) -> list:
    """[(value, count)] by count desc, then first appearance; None values are not counted."""
    counts, order = {}, []
    for v in values:
        if v is None:
            continue
        if v not in counts:
            counts[v] = 0
            order.append(v)
        counts[v] += 1
    ranked = sorted(order, key=lambda v: -counts[v])        # stable: ties keep first appearance
    return [(v, counts[v]) for v in ranked[:n]]


class Run:
    def __init__(self, rd, manifest, fmt=None):
        self.rd = rd
        self.manifest = manifest
        self.period = manifest['period']
        self.end = self.period['to']
        self.today = manifest['today']
        self.clusters_doc = gates.load_work(rd, 'clusters', 'fiw.clusters/1')
        self.relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1')
        self.prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1')
        self.todo = gates.load_work(rd, 'todo', 'fiw.todo/1')
        self.summary = gates.load_work(rd, 'summary', 'fiw.summary/1')
        dead = gates.load_work(rd, 'dead_links', 'fiw.dead_links/1', required=False)
        self.dead = {e['url'] for e in dead['links']} if dead else set()    # linkcheck dead: render leaves them out
        self.dead_omitted = 0               # set by render.build: distinct dead URLs actually left out
        self.dead_failures = []             # set by render.build: rows left with no link
        self.watch = layout.load_watch()
        self.fmt = fmt if fmt is not None else layout.load_format()
        self.runs_state = accum.load_state('runs.json')
        self.state_issues = accum.issues_list()
        self.state_notices = (accum.load_state('notices.json') or {}).get('items', [])
        self.coverage = accum.load_state('coverage.json')
        self.env = {s: runview.envelope(rd, s) for s, _ in SOURCE_LABELS}
        self.env['mafra_notice'] = runview.envelope(rd, 'mafra_notice')

        self.verdicts = runview.final_verdicts(self.prefilter, self.relevance)
        self.categories = runview.item_categories(self.relevance)
        self.articles = {a['id']: a for a in self.items('bigkinds')}
        self.clusters = {c['cid']: c for c in self.clusters_doc['clusters']}
        self.cluster_index = {c['cid']: i for i, c in enumerate(self.clusters_doc['clusters'])}
        self.rel = {c['cid']: c for c in self.relevance['clusters']}
        self.sum_issues = {s['key']: s for s in self.summary['issues']}
        self.sum_market = {m['topic']: m for m in self.summary['market']}
        self.labels = {n['id']: n['label'] for n in self.summary['notices']}
        self.refs = {}                      # ref -> {date, outlet, url, title, article: bool}
        for issue in self.todo['issues']:
            for a in issue['articles']:
                self.refs[a['ref']] = dict(a, article=True)
            for fc in issue['flow_candidates']:
                self.refs[fc['ref']] = dict(fc, outlet=fc.get('outlet') or '기사', article=False)
        for topic in self.todo['market']:
            for a in topic['articles']:
                self.refs[a['ref']] = dict(a, article=True)
        self.entity_kind = {e['name']: e['kind'] for e in entities.all_entities(self.watch)}

    # ------------------------------------------------------------------ sources
    def status(self, source):
        env = self.env.get(source)
        return None if env is None else env['status']

    def items(self, source) -> list:
        env = self.env.get(source)
        if env is None or env['status'] not in READ_OK:
            return []
        return env['items']

    def included(self, source, item_id) -> bool:
        return self.verdicts.get((source, item_id)) == 'include'

    def sections(self) -> list:
        return sorted((s for s in self.fmt['sections'] if s['enabled']), key=lambda s: s['order'])

    def columns(self, section) -> list:
        return [c.replace('{own_company}', self.watch['own_company']) for c in section['columns']]

    def short_day(self, iso) -> str:
        return dates.short_day(iso, self.end)

    def span(self, frm, to) -> str:
        return dates.short_span(frm, to, self.end)

    def day_basis(self, sentence) -> str:
        """A period report dates a sentence whose day has no month ("the 23rd", "the 23rd of last month") by its articles."""
        if not self.manifest.get('period_report'):
            return ''
        days = [self.refs[r]['date'] for r in sentence['refs'] if r in self.refs]
        return dates.day_basis(sentence['text'], days, self.end)

    def short_agency(self, name) -> str:
        return entities.short_name(self.watch, name, 'agencies')

    def committee_alias(self, committee):
        return entities.committee_alias(self.watch, committee)

    # ------------------------------------------------------------------ run history
    def retro(self) -> bool:
        if self.manifest.get('period_report'):
            return self.manifest['period_report']['retro']
        seg = self.manifest.get('plan_segment')
        path = layout.state_dir() / 'plan.json'
        if seg is None or not path.is_file():
            return False
        plan = schema.load(path, 'fiw.plan/1')
        return any(s['index'] > seg for s in plan['segments'])

    def previous_report(self):
        """Link to the latest earlier published report in state/runs.json, else None (and None in a period
        report)."""
        path = layout.state_dir() / 'runs.json'
        if self.manifest.get('period_report') or not path.is_file():
            return None
        runs = schema.load(path, 'fiw.state.runs/1')['runs']
        ids = [r['run_id'] for r in runs]
        rid = self.manifest['run_id']
        earlier = runs[:ids.index(rid)] if rid in ids else [
            r for r in runs if r['created_at'] < self.manifest['created_at']]
        for r in reversed(earlier):
            if r['report']:
                return accum.report_link(r['report'])
        return None

    # ------------------------------------------------------------------ clusters / issues
    def issue_cids(self, issue) -> list:
        key = issue['key']
        own = [key[4:]] if key.startswith('new:') else []
        cids = [c['cid'] for c in self.relevance['clusters']
                if c['verdict'] == 'issue' and c['section'] != 'market' and c['issue_link'] == key]
        return own + sorted(cids, key=lambda c: self.cluster_index.get(c, 1 << 30))

    def core(self, cids) -> list:
        ids = set()
        for cid in cids:
            ids.update(self.clusters.get(cid, {}).get('core_ids', []))
        known = [i for i in ids if i in self.articles]
        return sorted(known, key=lambda i: (self.articles[i]['date'], i))

    def core_stats(self, cids):
        """(distinct outlets, core size, from, to) over the union of the clusters' core."""
        core = self.core(cids)
        outlets = {self.articles[i]['outlet'] for i in core}
        if not core:
            return 0, 0, self.period['from'], self.period['from']
        return len(outlets), len(core), self.articles[core[0]]['date'], max(
            self.articles[i]['date'] for i in core)

    def cluster_entities(self, cids) -> list:
        out = []
        for cid in cids:
            for name in self.clusters.get(cid, {}).get('entities', []):
                if self.entity_kind.get(name) in ('companies', 'associations') and name not in out:
                    out.append(name)
        return out

    def section_issues(self, todo_section) -> list:
        rows = []
        for issue in self.todo['issues']:
            if issue['section'] != todo_section:
                continue
            n, m, _, _ = self.core_stats(self.issue_cids(issue))
            rows.append((-n, -m, issue['key'], issue))
        rows.sort(key=lambda r: r[:3])
        return [r[3] for r in rows]

    def other_cids(self, section) -> list:
        cids = [c['cid'] for c in self.relevance['clusters']
                if c['verdict'] == 'other' and c['section'] == section]
        return sorted(cids, key=lambda c: self.cluster_index.get(c, 1 << 30))

    def rep(self, cid):
        c = self.clusters.get(cid)
        return None if c is None else self.articles.get(c['rep_id'])

    def flow_nodes(self, issue_key) -> list:
        """[(node, ref info)] of the summary flow with a known ref."""
        s = self.sum_issues.get(issue_key) or {}
        return [(n, self.refs[n['ref']]) for n in s.get('flow') or [] if n['ref'] in self.refs]

    # ------------------------------------------------------------------ notices
    def notice_rows(self) -> list:
        entries = []
        for si, source in enumerate(runview.NOTICE_SOURCES):
            entries += [(si, item) for item in self.items(source) if self.included(source, item['id'])]
        return self.group_rows(entries)

    def carried_rows(self, rows) -> list:
        """Open rows from state/notices.json whose group is not one of this run's rows (addendum §D)."""
        keys = {r['key'] for r in rows}
        entries = [(len(runview.NOTICE_SOURCES), it) for it in self.state_notices
                   if it['open_to'] and it['open_to'] >= self.today
                   and (it['family_key'] or it['dup_key']) not in keys]
        return [r for r in self.group_rows(entries) if r['open']]

    def group_rows(self, entries) -> list:
        groups, order = {}, []
        for si, item in entries:
            key = item['family_key'] or item['dup_key']
            if key not in groups:
                groups[key] = []
                order.append(key)
            if all(i['url'] != item['url'] for _, i in groups[key]):
                groups[key].append((si, item))
        rank = {'시행령': 0, '시행규칙': 1}
        rows = []
        for key in order:
            members = sorted(groups[key], key=lambda p: (rank.get(decree_label(p[1]), 2), p[0], p[1]['id']))
            items = [i for _, i in members]
            first = items[0]
            labels = [decree_label(i) for i in items]
            family = len(items) >= 2 and '시행령' in labels and '시행규칙' in labels
            law_types = []
            for i in items:
                if i['law_type'] and i['law_type'] not in law_types:
                    law_types.append(i['law_type'])
            d = None
            if first['open_to']:
                d = (_day(first['open_to']) - _day(self.today)).days
            rows.append({
                'key': key, 'items': items, 'family': family, 'labels': labels,
                'title': (notices.base_law(first['title']) + ' 시행령·시행규칙') if family else first['title'],
                'short': (notices.base_law(first['title']) + ' 시행령·시행규칙') if family
                else notices.law_name(first['title']),
                'dept': self.short_agency(first['dept']),
                'kind': first['kind'] + (f'({"·".join(law_types)})' if law_types else ''),
                'from': first['open_from'], 'to': first['open_to'], 'd': d,
                'open': d is not None and d >= 0,
            })
        open_rows = sorted((r for r in rows if r['open']), key=lambda r: (r['to'], r['title']))
        closed = sorted((r for r in rows if r['d'] is not None and not r['open']), key=lambda r: r['title'])
        closed.sort(key=lambda r: r['to'], reverse=True)
        unparsed = sorted((r for r in rows if r['d'] is None), key=lambda r: r['title'])
        return open_rows + closed + unparsed

    def other_notices(self) -> list:
        out, seen = [], set()
        for source in runview.NOTICE_SOURCES:
            for item in self.items(source):
                if self.verdicts.get((source, item['id'])) == 'other' and item['url'] not in seen:
                    seen.add(item['url'])
                    out.append(item)
        out.sort(key=lambda i: i['title'])
        out.sort(key=lambda i: i['posted'], reverse=True)
        return out

    # ------------------------------------------------------------------ accumulation
    def badges(self) -> bool:
        """🆕 / ▶ only once an earlier run has a published report, and always in a period report."""
        return bool(self.manifest.get('period_report')) or accum.has_earlier_report(self.runs_state, self.manifest)

    def carried_and_dormant(self):
        weeks = self.fmt['dormancy_weeks']
        return accum.projected(self.manifest, self.todo, self.runs_state, self.coverage, self.env.get('bigkinds'),
                               self.state_issues, weeks)

    # ------------------------------------------------------------------ press
    def press(self, dept_name):
        """(all items, include items, other items) of one korea_kr department."""
        items = [i for i in self.items('korea_kr') if i['dept'] == dept_name]
        inc = [i for i in items if self.included('korea_kr', i['id'])]
        oth = [i for i in items if self.verdicts.get(('korea_kr', i['id'])) == 'other']
        return items, inc, oth

    def category_order(self) -> list:
        out = []
        for it in self.relevance['items']:
            if it['source'] == 'korea_kr' and it.get('category') and it['category'] not in out:
                out.append(it['category'])
        return out

    # ------------------------------------------------------------------ assembly
    def bill_rows(self) -> list:
        rows = [b for b in self.items('assembly_bills') if self.included('assembly_bills', b['bill_id'])
                and (b['committee'] is None or entities.committee_alias(self.watch, b['committee']))]
        rows.sort(key=lambda b: b['bill_id'])
        rows.sort(key=lambda b: b['date'], reverse=True)
        return rows

    def schedule_rows(self) -> list:
        return sorted(self.items('assembly_schedule'), key=lambda s: (s['date'], s['time'] or ''))

    # ------------------------------------------------------------------ hygiene
    def hygiene(self, kind) -> list:
        return [i for i in self.items('foodsafety') if i['kind'] == kind]

    @staticmethod
    def action_type(action):
        return None if action is None else _ACTION_TAIL.sub('', action)
