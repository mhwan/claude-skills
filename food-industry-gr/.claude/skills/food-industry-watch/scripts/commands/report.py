"""fiw.py report — a period report built from the raws (contracts.md 「Period report」).

The raws are never rewritten: the period report keeps the summary sentences of the raws whose every ref lies
inside the period, writes only the three-line summary anew (from the kept sentences), and passes lint, linkcheck and the
period gate before it is copied to reports/{from}~{to}.md.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import timedelta
from pathlib import Path

from lib import accum, cli, dates, entities, gates, korean, layout, runview, schema
from . import _verify_checks as vc
from . import linkcheck, lint, render
from ._render_model import SOURCE_LABELS, Run
from .check import item_key

GATE = 'period'


def register(sub):
    p = sub.add_parser('report', help='build a period report from the raws')
    p.add_argument('--from', dest='frm', required=True, type=cli.date_arg, metavar='YYYY-MM-DD')
    p.add_argument('--to', required=True, type=cli.date_arg, metavar='YYYY-MM-DD')
    p.add_argument('--three-lines', metavar='FILE',
                   help='JSON list of {issue, head {text, refs}, sentences} written from three_lines_brief.json')
    p.add_argument('--json', action='store_true', help='print the period gate JSON')
    p.set_defaults(func=main)


def _load(path, contract=None):
    if contract:
        return schema.load(path, contract)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _dump(path, obj, contract):
    path.parent.mkdir(parents=True, exist_ok=True)
    schema.dump(path, dict(obj, schema=contract), contract)


def _inside(day, a, b) -> bool:
    return bool(day) and a <= day[:10] <= b


# ---------------------------------------------------------------------------------------------- raws
def select_raws(a, b):
    """(raws, uncollected) for [a, b]; raws are {rd, m, idx} oldest first. Raises _Stop when not ready."""
    runs = (accum.load_state('runs.json') or {}).get('runs', [])
    published = {r['run_id'] for r in runs if r['report']}
    found = []
    if layout.runs_dir().is_dir():
        for rd in sorted(layout.runs_dir().iterdir()):
            mp = layout.manifest_path(rd)
            if not mp.is_file():
                continue
            m = schema.load(mp, 'fiw.manifest/1')
            if m.get('period_report') is None:
                found.append({'rd': rd, 'm': m})
    if not found:
        raise _Stop('no raw yet — collect first (fiw.py run)')
    found.sort(key=lambda r: (r['m']['period']['from'], r['m']['created_at']))
    latest_to = max(r['m']['period']['to'] for r in found)
    if b > latest_to:
        raise _Stop(f'the raws end at {latest_to} — collect {_next(latest_to)}~{b} first, then build the report')
    raws = [r for r in found if dates.overlaps(r['m']['period']['from'], r['m']['period']['to'], a, b)]
    if not raws:
        raise _Stop(f'no raw covers {a}~{b}')
    earliest = raws[0]['m']['period']['from']
    uncollected = None
    if a < earliest:
        uncollected = {'from': a, 'to': dates.fmt(dates.parse_date(earliest) - timedelta(days=1))}
    covered = earliest
    for r in raws:
        p = r['m']['period']
        if p['from'] > _next(covered) and p['from'] > a:
            raise _Stop(f'gap {_next(covered)}~{_prev(p["from"])} between raws — collect it first')
        covered = max(covered, p['to'])
    problems = []
    for r in raws:
        rid = r['m']['run_id']
        if rid not in published:
            problems.append(f'{rid}: not published — finish and publish that run first')
        for gate, work in (('verify', 'summary'), ('relevance', 'relevance')):
            g = gates.read_gate(r['rd'], gate)
            if g is None or g['exit'] != 0 or g['input_hash'] != gates.file_hash(r['rd'] / 'work' / f'{work}.json'):
                problems.append(f'{rid}: {gate} gate is not a pass over the current {work}.json')
    if problems:
        raise _Stop('; '.join(problems))
    for i, r in enumerate(raws):
        r['idx'] = i
    return raws, uncollected


def _next(day):
    return dates.fmt(dates.parse_date(day) + timedelta(days=1))


def _prev(day):
    return dates.fmt(dates.parse_date(day) - timedelta(days=1))


class _Stop(Exception):
    pass


# ---------------------------------------------------------------------------------------------- merge
class Merge:
    """The build folder's work files and the bookkeeping that ties them to the raws."""

    def __init__(self, raws, a, b, issues, watch):
        self.raws, self.a, self.b, self.watch = raws, a, b, watch
        self.state_issues = issues
        self.latest = max(raws, key=lambda r: (r['m']['period']['to'], r['m']['created_at']))
        self.newest_first = sorted(raws, key=lambda r: (r['m']['period']['to'], r['m']['created_at']),
                                   reverse=True)
        self.dropped = Counter()
        self.cid_map = {}           # (raw idx, old cid) -> new cid
        self.ref_map = {}           # (raw idx, old ref) -> new ref
        self.ref_date = {}          # new ref -> date
        self.ref_url = {}
        runs = {r['run_id']: r for r in (accum.load_state('runs.json') or {}).get('runs', [])}
        self.issue_ids = {r['idx']: runs[r['m']['run_id']].get('issue_ids') or {} for r in raws}
        self.work = {r['idx']: self._work(r['rd']) for r in raws}

    @staticmethod
    def _work(rd):
        dead = gates.load_work(rd, 'dead_links', 'fiw.dead_links/1', required=False)
        return {'clusters': gates.load_work(rd, 'clusters', 'fiw.clusters/1'),
                'relevance': gates.load_work(rd, 'relevance', 'fiw.relevance/1'),
                'prefilter': gates.load_work(rd, 'prefilter', 'fiw.prefilter/1'),
                'todo': gates.load_work(rd, 'todo', 'fiw.todo/1'),
                'summary': gates.load_work(rd, 'summary', 'fiw.summary/1'),
                'dead': dead['links'] if dead else []}

    def sid(self, idx, key):
        """The state id of a raw's todo issue key (aliases resolved), else None."""
        sid = self.issue_ids[idx].get(key)
        return accum.resolve(sid, self.state_issues) if sid else None

    # ------------------------------------------------------------------ collect
    def collect(self) -> dict:
        out = {}
        for source, _ in SOURCE_LABELS:
            envs = [(r, runview.envelope(r['rd'], source)) for r in self.raws]
            envs = [(r, e) for r, e in envs if e is not None]
            if envs:
                out[source] = self._envelope(source, envs)
        return out

    def _keep_item(self, inclusion, item) -> bool:
        if inclusion == 'notice_open_overlap':
            return _inside(item.get('posted'), self.a, self.b) or (
                item.get('open_from') is not None and item.get('open_to') is not None
                and dates.overlaps(item['open_from'], item['open_to'], self.a, self.b))
        return _inside(item.get('date') or item.get('posted'), self.a, self.b)

    def _envelope(self, source, envs):
        newest = max(envs, key=lambda p: (p[0]['m']['period']['to'], p[0]['m']['created_at']))[1]
        items, seen = [], set()
        ordered = sorted(envs, key=lambda p: (p[0]['m']['period']['to'], p[0]['m']['created_at']), reverse=True)
        if newest['inclusion'] == 'upcoming_4w':
            items = list(newest['items'])
        else:
            for _r, env in ordered:
                for it in env['items']:
                    k = item_key(source, it)
                    if k in seen:
                        self.dropped['collect_duplicate_item'] += 1
                        continue
                    seen.add(k)
                    if self._keep_item(env['inclusion'], it):
                        items.append(it)
        statuses = [e['status'] for _r, e in envs]
        status = ('ok' if all(s == 'ok' for s in statuses) else
                  'failed' if all(s == 'failed' for s in statuses) else
                  newest['status'] if len(set(statuses)) == 1 else 'partial')
        requests = list(newest['requests'])
        for _r, env in envs:
            if env is newest:
                continue
            requests += [q for q in env['requests'] if q['status'] != 'ok']
            if env['status'] == 'failed' and not any(q['status'] != 'ok' for q in env['requests']):
                requests.append({'target': f'{env["run_id"]}', 'total': None, 'received': 0, 'pages': 0,
                                 'status': 'failed'})
        errors = [e['error'] for _r, e in envs if e['error']]
        env = {k: v for k, v in newest.items() if k != 'schema'}
        env.update(period={'from': self.a, 'to': self.b}, status=status, requests=requests, items=items,
                   error='; '.join(errors) if errors and status != 'ok' else None)
        return env

    # ------------------------------------------------------------------ refs
    def _remap_ref(self, idx, ref) -> str:
        key = (idx, ref)
        if key in self.ref_map:
            return self.ref_map[key]
        part, _, tail = ref.rpartition(':')
        cid = self.cid_map.get((idx, part))
        return f'{cid}:{tail}' if cid else f'{part}@{idx + 1}:{tail}'

    def ref(self, idx, ref) -> str:
        return self.ref_map.get((idx, ref)) or self._remap_ref(idx, ref)

    def build_refs(self):
        """New cids for every raw cluster, then one ref per article id (the newest raw's) and per flow URL."""
        n = 0
        for r in self.raws:
            for c in self.work[r['idx']]['clusters']['clusters']:
                n += 1
                self.cid_map[(r['idx'], c['cid'])] = f'c{n:04d}'
        by_article, by_flow = {}, {}
        for r in self.newest_first:
            idx = r['idx']
            todo = self.work[idx]['todo']
            arts = [a for i in todo['issues'] for a in i['articles']] + [a for m in todo['market']
                                                                         for a in m['articles']]
            for a in arts:
                new = self._remap_ref(idx, a['ref'])
                if a['id'] in by_article:
                    if by_article[a['id']] != new:
                        self.dropped['article_in_two_raws'] += 1
                    new = by_article[a['id']]
                else:
                    by_article[a['id']] = new
                self.ref_map[(idx, a['ref'])] = new
                self.ref_date[new], self.ref_url[new] = a['date'], a['url']
            for fc in (f for i in todo['issues'] for f in i['flow_candidates']):
                new = self._remap_ref(idx, fc['ref'])
                new = by_flow.setdefault(fc['url'], new)
                self.ref_map[(idx, fc['ref'])] = new
                self.ref_date.setdefault(new, fc['date'])
                self.ref_url.setdefault(new, fc['url'])

    def in_period(self, ref) -> bool:
        return _inside(self.ref_date.get(ref), self.a, self.b)

    # ------------------------------------------------------------------ clusters / relevance / prefilter
    def clusters(self, articles) -> dict:
        tracked = entities.all_entities(self.watch)
        claimed, out, dropped_rules = set(), {}, []
        for r in self.newest_first:
            doc = self.work[r['idx']]['clusters']
            for c in doc['clusters']:
                ids = [i for i in c['article_ids'] if i in articles and i not in claimed]
                core = [i for i in c['core_ids'] if i in ids]
                new = self.cid_map[(r['idx'], c['cid'])]
                if not core:
                    self.dropped['cluster_outside_period'] += 1
                    continue
                claimed.update(ids)
                whole = len(ids) == len(c['article_ids'])
                days = sorted(articles[i]['date'][:10] for i in core)
                names = c['entities'] if whole else [
                    e['name'] for e in tracked
                    if any(korean.match_entity(articles[i]['title'] + ' ' + (articles[i].get('excerpt') or ''), e)
                           for i in core)]
                out[(r['idx'], c['cid'])] = dict(c, cid=new, article_ids=ids, core_ids=core,
                                                 rep_id=c['rep_id'] if c['rep_id'] in core else core[0],
                                                 entities=names, **{'from': days[0], 'to': days[-1]})
            dropped_rules += [d for d in doc['dropped'] if d['article_id'] in articles
                              and d['article_id'] not in claimed]
        ordered = [out[(r['idx'], c['cid'])] for r in self.raws for c in self.work[r['idx']]['clusters']['clusters']
                   if (r['idx'], c['cid']) in out]
        seen, dropped = set(), []
        for d in dropped_rules:
            if d['article_id'] not in seen:
                seen.add(d['article_id'])
                dropped.append(d)
        self.kept_clusters = out
        return {'rule_stage': {'input_articles': len(articles), 'dropped_by_rule': len(dropped),
                               'clusters': len(ordered)},
                'clusters': ordered, 'dropped': dropped}

    def relevance(self) -> dict:
        clusters = []
        for r in self.raws:
            idx = r['idx']
            for c in self.work[idx]['relevance']['clusters']:
                if (idx, c['cid']) not in self.kept_clusters:
                    continue
                old = c['cid']
                c = dict(c, cid=self.cid_map[(idx, old)])
                if c['verdict'] == 'issue' and c['section'] != 'market':
                    link = c['issue_link']
                    sid = self.sid(idx, f'new:{old}') if link == 'new' else (
                        self.sid(idx, link) or accum.resolve(link, self.state_issues))
                    c['issue_link'] = sid or 'new'
                clusters.append(c)
        items = self._items('relevance')
        return {'clusters': clusters, 'items': items}

    def _items(self, name) -> list:
        seen, out = set(), []
        for r in self.newest_first:
            for it in self.work[r['idx']][name]['items']:
                k = (it['source'], it['id'])
                if k not in seen:
                    seen.add(k)
                    out.append(it)
        return out

    def prefilter(self) -> dict:
        return {'items': self._items('prefilter')}


# ---------------------------------------------------------------------------------------------- summary
class Select:
    """Todo + summary of the period: the raws' sentences whose every ref lies inside it."""

    def __init__(self, mg: Merge, dead_urls):
        self.mg, self.dead_urls = mg, set(dead_urls)
        self.e17 = vc.Verifier({'issues': [], 'market': [], 'excluded_no_link': [], 'lwf_mentions': {}},
                               {'three_lines': [], 'issues': [], 'notices': [], 'market': []},
                               mg.watch, {'sentence_limit': {}}, {}, {'items': []}, {'clusters': [], 'items': []},
                               period_to=mg.b)

    def _sentence(self, idx, s):
        mg = self.mg
        refs = [mg.ref(idx, x) for x in s['refs']]
        out = dict(s, refs=refs)
        if s.get('evidence'):
            out['evidence'] = [dict(e, ref=mg.ref(idx, e['ref'])) for e in s['evidence']]
        if s.get('links'):
            out['links'] = [mg.sid(idx, k) or k for k in s['links']]
        if not refs or not all(mg.in_period(x) for x in refs):
            mg.dropped['sentence_ref_outside_period'] += 1
            return None
        if any(mg.ref_url.get(x) in self.dead_urls for x in refs):
            mg.dropped['sentence_dead_source'] += 1
            return None
        self.e17.findings = []
        self.e17.e17('x', s['text'], [{'date': vc._iso(mg.ref_date[x])} for x in refs])
        if self.e17.findings:
            mg.dropped['sentence_e17_at_period_end'] += 1
            return None
        return out

    def build(self, articles_by_id):
        mg = self.mg
        issues, order = {}, []          # sid -> merged todo issue
        sums = {}                        # sid -> list of (idx, summary issue)
        for r in mg.raws:
            idx = r['idx']
            work = mg.work[idx]
            by_key = {s['key']: s for s in work['summary']['issues']}
            for it in work['todo']['issues']:
                sid = mg.sid(idx, it['key'])
                if sid is None:
                    raise _Stop(f'{r["m"]["run_id"]}: todo issue {it["key"]} has no state id in runs.json')
                if sid not in issues:
                    issues[sid] = {'key': sid, 'section': it['section'], 'articles': [], 'flow_candidates': [],
                                   'prior': None, 'locks': it['locks']}
                    order.append(sid)
                m = issues[sid]
                m['section'], m['locks'] = it['section'], it['locks']
                have = {a['id'] for a in m['articles']}
                for a in it['articles']:
                    if a['id'] not in have and _inside(a['date'], mg.a, mg.b):
                        have.add(a['id'])
                        m['articles'].append(dict(a, ref=mg.ref(idx, a['ref'])))
                urls = {f['url'] for f in m['flow_candidates']}
                for f in it['flow_candidates']:
                    if f['url'] not in urls and _inside(f['date'], mg.a, mg.b):
                        urls.add(f['url'])
                        m['flow_candidates'].append(dict(f, ref=mg.ref(idx, f['ref'])))
                if it['key'] in by_key:
                    sums.setdefault(sid, []).append((idx, by_key[it['key']]))

        facts = {sid: [] for sid in order}
        edits = {sid: [] for sid in order}
        for sid in order:
            for idx, s in sums.get(sid, []):
                for x in s['sentences']:
                    y = self._sentence(idx, x)
                    if y is not None:
                        (edits if y['kind'] == 'edit' else facts)[sid].append(y)
        alive = {sid for sid in order if facts[sid]}
        while True:
            changed = False
            for sid in order:
                kept = [e for e in edits[sid] if all(k in alive for k in e.get('links') or [])]
                self.mg.dropped['edit_link_lost'] += len(edits[sid]) - len(kept)
                edits[sid] = kept
            new_alive = {sid for sid in order if facts[sid] or edits[sid]}
            if new_alive != alive:
                alive, changed = new_alive, True
            if not changed:
                break

        out_issues, out_sum, self.removed = [], [], []
        for sid in order:
            if sid not in alive:
                self.removed.append(sid)
                mg.dropped['issue_no_sentence_in_period'] += 1
                continue
            sentences = self._ordered(sid, sums[sid], facts[sid] + edits[sid])
            entry = {'key': sid, 'title': self._title(sid, sums[sid]), 'sentences': sentences}
            actors = self._actors(sums[sid])
            if actors:
                entry['actors'] = actors
            self._flow(entry, sums[sid], alive)
            lwf = self._lwf(sums[sid])
            if lwf:
                entry['lwf'] = lwf
            out_issues.append(issues[sid])
            out_sum.append(entry)

        market, mk_sum = self._market()
        notices, seen = [], set()
        for r in mg.newest_first:
            for n in mg.work[r['idx']]['summary']['notices']:
                if n['id'] not in seen:
                    seen.add(n['id'])
                    notices.append(n)
        lwf_mentions = {}
        for r in mg.raws:
            for ref, v in mg.work[r['idx']]['todo']['lwf_mentions'].items():
                new = mg.ref(r['idx'], ref)
                if mg.in_period(new):
                    lwf_mentions[new] = lwf_mentions.get(new, False) or v
        excluded = {'excluded_no_link': [], 'excluded_no_body': []}
        for r in mg.raws:
            p = r['m']['period']
            if mg.a <= p['from'] and p['to'] <= mg.b:
                todo = mg.work[r['idx']]['todo']
                for k in excluded:
                    excluded[k] += [e for e in todo.get(k) or [] if e not in excluded[k]]
        todo = {'issues': out_issues, 'market': market, 'lwf_mentions': lwf_mentions,
                'excluded_no_link': excluded['excluded_no_link']}
        if excluded['excluded_no_body']:
            todo['excluded_no_body'] = excluded['excluded_no_body']
        summary = {'three_lines': [], 'issues': out_sum, 'notices': notices, 'market': mk_sum}
        return todo, summary

    @staticmethod
    def _ordered(sid, sums, kept):
        """Kept sentences in raw order (oldest raw first, the raw's own order inside)."""
        rank = {}
        for n, (_idx, s) in enumerate(sums):
            for m, x in enumerate(s['sentences']):
                rank.setdefault(x['text'], (n, m))
        seen, out = set(), []
        for x in sorted(kept, key=lambda x: rank.get(x['text'], (1 << 30, 0))):
            if x['text'] not in seen:
                seen.add(x['text'])
                out.append(x)
        return out

    def _title(self, sid, sums):
        mg = self.mg
        for idx, s in reversed(sums):
            refs = [mg.ref(idx, x) for x in s['title']['refs']]
            if refs and all(mg.in_period(x) for x in refs):
                return {'text': s['title']['text'], 'refs': refs}
        for idx, s in reversed(sums):
            refs = [x for x in (mg.ref(idx, y) for y in s['title']['refs']) if mg.in_period(x)]
            if refs:
                mg.dropped['title_refs_trimmed'] += 1
                return {'text': s['title']['text'], 'refs': refs}
        raise _Stop(f'{sid}: no title has a ref inside the period')

    def _actors(self, sums):
        mg, out, seen = self.mg, [], set()
        for idx, s in sums:
            for a in s.get('actors') or []:
                refs = [x for x in (mg.ref(idx, y) for y in a['refs']) if mg.in_period(x)]
                if refs and a['text'] not in seen:
                    seen.add(a['text'])
                    out.append({'text': a['text'], 'refs': refs})
        return out

    def _flow(self, entry, sums, alive):
        mg = self.mg
        for idx, s in reversed(sums):
            if not s.get('flow'):
                continue
            nodes = [dict(n, ref=mg.ref(idx, n['ref'])) for n in s['flow']]
            nodes = [n for n in nodes if mg.in_period(n['ref'])]
            if not nodes:
                continue
            entry['flow'] = nodes
            fn = s.get('flow_name')
            if fn and len(nodes) >= 2:
                links = [mg.sid(idx, k) or k for k in fn['links']]
                if all(k in alive for k in links):
                    entry['flow_name'] = dict(fn, links=links)
            return

    def _lwf(self, sums):
        mg = self.mg
        for idx, s in reversed(sums):
            lwf = s.get('lwf')
            if not lwf or not lwf.get('ref'):
                continue
            ref = mg.ref(idx, lwf['ref'])
            if not mg.in_period(ref):
                continue
            out = dict(lwf, ref=ref)
            if lwf.get('sentences') is not None:
                kept = [y for y in (self._sentence(idx, x) for x in lwf['sentences']) if y is not None]
                if lwf.get('direct') and not kept:
                    return {'direct': False, 'ref': ref,
                            'not_shown_reason': '기간 안 근거로 남은 문장이 없다 (기간 보고서)'}
                out['sentences'] = kept
            return out
        return None

    def _market(self):
        mg = self.mg
        topics, order, sums = {}, [], {}
        for r in mg.raws:
            idx = r['idx']
            by_topic = {m['topic']: m for m in mg.work[idx]['summary']['market']}
            for m in mg.work[idx]['todo']['market']:
                t = topics.setdefault(m['topic'], {'topic': m['topic'], 'articles': []})
                if m['topic'] not in order:
                    order.append(m['topic'])
                have = {a['id'] for a in t['articles']}
                for a in m['articles']:
                    if a['id'] not in have and _inside(a['date'], mg.a, mg.b):
                        have.add(a['id'])
                        t['articles'].append(dict(a, ref=mg.ref(idx, a['ref'])))
                if m['topic'] in by_topic:
                    sums.setdefault(m['topic'], []).append((idx, by_topic[m['topic']]))
        todo, out = [], []
        self.removed_topics = []
        for topic in order:
            kept = [y for idx, s in sums.get(topic, []) for y in (self._sentence(idx, x) for x in s['sentences'])
                    if y is not None]
            kept = self._ordered(topic, sums.get(topic, []), kept)
            if not kept:
                self.removed_topics.append(topic)
                mg.dropped['market_topic_no_sentence'] += 1
                continue
            entry = {'topic': topic, 'sentences': kept}
            lwf = self._lwf(sums[topic])
            if lwf:
                entry['lwf'] = lwf
            todo.append(topics[topic])
            out.append(entry)
        return todo, out


# ---------------------------------------------------------------------------------------------- state overlay
def overlay_state(folder, todo, a, b, raw_ends):
    """state/ of the period as the renderer reads it: the period's issues active, nothing after b."""
    src = layout.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('notices.json', 'hygiene.json', 'seen_ids.json'):
        if (src / name).is_file():
            shutil.copyfile(src / name, folder / name)
    _dump(folder / 'runs.json', {'runs': []}, 'fiw.state.runs/1')
    cov = accum.load_state('coverage.json') or {'sources': {}}
    clipped = {s: [dict(p, to=min(p['to'], b)) for p in ps if p['from'] <= b] for s, ps in cov['sources'].items()}
    _dump(folder / 'coverage.json', {'sources': clipped}, 'fiw.state.coverage/1')
    in_todo = {i['key'] for i in todo['issues']}
    out = []
    for issue in accum.issues_list():
        if issue['first_seen'] > b:
            continue
        flow = [f for f in issue['flow'] if f['date'] <= b]
        last = issue.get('last_article')
        if last and last > b:
            last = max([f['date'] for f in flow] or [issue['first_seen']])
        sentences = [s for s in issue['last_sentences'] if raw_ends.get(s['run_id'], '9999') <= b]
        out.append(dict(issue, status='active', flow=flow, last_article=last,
                        last_sentences=sentences or issue['last_sentences']))
    _dump(folder / 'issues.json', {'issues': out}, 'fiw.state.issues/1')
    return in_todo


# ---------------------------------------------------------------------------------------------- three-line summary
# An authority's own action against companies, read from a government issue's title: not a word that only
# names the subject (e.g. a collusion damages suit, a fine mentioned as an example, a fine rate, a conditional non-prosecution, a business suspension drive).
# An Assembly issue does not count: its 'detected: 1 case' is a lawmaker's figure, not an action of the period.
SANCTION = re.compile(r'현장조사|적발|심의 개시|고발|송치|압수수색|(?<!조건부 )기소(?!유예)|시정명령|영업정지 처분'
                      r'|과징금[^,…]{0,12}(?:부과|처분)(?!율)')


def is_sanction(section, title) -> bool:
    return section == 'gov' and bool(SANCTION.search(title))


def impact(e) -> tuple:
    """Three-line summary order: outlets + 3 for a sanction + 2 for the own company; then articles, flow candidates, newest day."""
    score = e['outlets'] + 3 * e['sanction'] + 2 * e['own_company']
    return -score, -e['articles'], -e['flow_candidates'], -dates.parse_date(e['dates'][-1]).toordinal(), e['issue']


def three_lines_brief(todo, summary, articles, clusters, relevance):
    """outlets/articles count the issue's cluster core, the same count as the report's outlets/articles tag."""
    by_key = {i['key']: i for i in todo['issues']}
    core_ids = {c['cid']: c['core_ids'] for c in clusters['clusters']}
    out = []
    for s in summary['issues']:
        it = by_key[s['key']]
        key = s['key']
        cids = ([key[4:]] if key.startswith('new:') else []) + [
            c['cid'] for c in relevance['clusters']
            if c['verdict'] == 'issue' and c['section'] != 'market' and c['issue_link'] == key]
        core = {i for cid in cids for i in core_ids.get(cid, []) if i in articles}
        outlets = {articles[i]['outlet'] for i in core}
        out.append({'issue': s['key'], 'section': it['section'], 'title': s['title']['text'],
                    'outlets': len(outlets), 'articles': len(core),
                    'sanction': is_sanction(it['section'], s['title']['text']),
                    'own_company': bool((s.get('lwf') or {}).get('direct')),
                    'flow_candidates': len(it['flow_candidates']),
                    'dates': sorted({a['date'] for a in it['articles']}),
                    'sentences': [{'text': x['text'], 'kind': x['kind'], 'refs': x['refs']} for x in s['sentences']]})
    out.sort(key=impact)
    return {'rule': 'issues are sorted by impact: outlets + 3 when the title of a government (gov) issue '
                    'is an authority\'s own action against companies — on-site inspection, a finding, a hearing, '
                    'a charge, an order, a fine imposed (sanction) + 2 when it names the own company (own_company); ties: '
                    'articles, flow_candidates, newest day. Take the first min(3, issues); head {text, refs} from '
                    'the listed sentences only (every number, date, name and quote must be in them; no day '
                    'without its month, e.g. 10월 23일 not 23일 or 지난달 23일); sentences: exact copies of listed '
                    'sentences of that issue', 'want': min(3, len(out)), 'issues': out}


def take_three_lines(lines, summary, findings) -> list:
    """The three-line summary with every sentence replaced by the kept sentence it copies (text, kind, refs); a sentence that
    copies none is a finding."""
    by_key = {s['key']: s for s in summary['issues']}
    out = []
    for i, tl in enumerate(lines):
        s = by_key.get(tl.get('issue'))
        if s is None:
            findings.append({'where': f'three_lines[{i}]', 'kind': '세 줄',
                             'detail': f'issue {tl.get("issue")} is not an issue of this period report'})
            continue
        kept = {(x['text'], x['kind'], tuple(x['refs'])): x for x in s['sentences']}
        sentences = []
        for j, x in enumerate(tl['sentences']):
            hit = kept.get((x['text'], x['kind'], tuple(x['refs'])))
            if hit is None:
                findings.append({'where': f'three_lines[{i}]/sentences[{j}]', 'kind': '세 줄',
                                 'detail': 'not an exact copy of a kept sentence of this issue'})
            else:
                sentences.append(hit)
        out.append({'issue': tl['issue'], 'head': tl['head'], 'sentences': sentences})
    return out


def check_heads(lines, summary, verifier, findings):
    """E-checks of each three-line summary head against the kept summary text only (never the article bodies)."""
    by_key = {s['key']: s for s in summary['issues']}
    texts = {}
    for s in summary['issues']:
        for x in s['sentences']:
            for r in x['refs']:
                texts.setdefault(r, []).append(x['text'])
        for fld in [s['title']] + list(s.get('actors') or []):
            for r in fld['refs']:
                texts.setdefault(r, []).append(fld['text'])
        for n in s.get('flow') or []:
            texts.setdefault(n['ref'], []).append(n['text'])
    for ref, info in verifier.refs.items():
        body = '\n'.join(texts.get(ref, []))
        info['body'], info['text'] = body, info['title'] + '\n' + body
    before = len(verifier.findings)
    for i, tl in enumerate(lines):
        s = by_key[tl['issue']]
        own = {r for x in s['sentences'] for r in x['refs']}
        verifier.field(f'three_lines[{i}]/head', tl['head'], own)
    findings.extend(verifier.findings[before:])


# ---------------------------------------------------------------------------------------------- period gate
def period_findings(mg, todo, summary, text, uncollected, collect) -> list:
    out = []

    def bad(where, detail):
        out.append({'where': where, 'kind': '기간', 'detail': detail})

    def refs_of(where, refs):
        for r in refs:
            if not mg.in_period(r):
                bad(where, f'ref {r} ({mg.ref_date.get(r)}) is outside {mg.a}~{mg.b}')

    for i, tl in enumerate(summary['three_lines']):
        refs_of(f'three_lines[{i}]/head', tl['head']['refs'])
        for j, x in enumerate(tl['sentences']):
            refs_of(f'three_lines[{i}]/sentences[{j}]', x['refs'])
    for s in summary['issues'] + summary['market']:
        base = s.get('key') or f'market:{s["topic"]}'
        if 'title' in s:
            refs_of(f'{base}/title', s['title']['refs'])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        for a in s.get('actors') or []:
            refs_of(f'{base}/actors', a['refs'])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        for j, x in enumerate(s['sentences']):
            refs_of(f'{base}/sentences[{j}]', x['refs'])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
            if x.get('evidence'):
                refs_of(f'{base}/sentences[{j}]/evidence', [e['ref'] for e in x['evidence']])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        for n in s.get('flow') or []:
            refs_of(f'{base}/flow', [n['ref']])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        lwf = s.get('lwf') or {}
        if lwf.get('ref'):
            refs_of(f'{base}/lwf', [lwf['ref']])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        for j, x in enumerate(lwf.get('sentences') or []):
            refs_of(f'{base}/lwf/sentences[{j}]', x['refs'])  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
    for it in todo['issues'] + todo['market']:
        ids = [a['id'] for a in it['articles']]
        for k, n in Counter(ids).items():
            if n > 1:
                bad(it.get('key') or it['topic'], f'article {k} listed {n} times')
        for a in it['articles']:
            if not _inside(a['date'], mg.a, mg.b):
                bad(it.get('key') or it['topic'], f'article {a["id"]} dated {a["date"]}')
    bk = collect.get('bigkinds')
    if bk is not None:
        for k, n in Counter(a['id'] for a in bk['items']).items():
            if n > 1:
                bad('collect/bigkinds', f'article {k} listed {n} times')
    note = accum.uncollected_note({'period_report': {'uncollected': uncollected}})
    if note and note not in text:
        bad('report', f'missing the line 「{note}」')
    if not note and '> 미수집' in text:
        bad('report', 'a 「미수집」 line but the raws cover the whole period')
    return out


# ---------------------------------------------------------------------------------------------- main
def link_line(classified) -> str:
    """The last line of the copy the user receives: what linkcheck found."""
    line = (f'> 링크 확인: 정상 {classified.get("ok", 0)} · 자동 확인 불가 {classified.get("link_blocked", 0)}'
            f' · 검사 면제 {classified.get("robots_exempt", 0)}')
    if classified.get('error', 0) > 0:
        line += f' · 오류 {classified["error"]}'
    return line


def main(args) -> int:
    a, b = dates.fmt(args.frm), dates.fmt(args.to)
    if a > b:
        raise cli.UsageError('--from is after --to')
    try:
        raws, uncollected = select_raws(a, b)
    except _Stop as e:
        print(f'report: {e}')
        return cli.EXIT_WARN
    watch = layout.load_watch()
    mg = Merge(raws, a, b, accum.issues_list(), watch)
    mg.build_refs()

    build = layout.build_dir(mg.latest['m']['run_id'], a, b)
    # Read before the build folder is cleared: the documented place of the three-line summary file is inside it.
    three_lines = _load(Path(args.three_lines)) if args.three_lines else None
    old_dead = gates.load_work(build, 'dead_links', 'fiw.dead_links/1', required=False) if build.is_dir() else None
    if build.is_dir():
        shutil.rmtree(build)
    (build / 'work').mkdir(parents=True)
    layout.collect_dir(build).mkdir(parents=True)

    collect = mg.collect()
    for source, env in collect.items():
        env['run_id'] = mg.latest['m']['run_id']
        _dump(layout.collect_dir(build) / f'{source}.json', env, 'fiw.collect/1')
    articles = {it['id']: it for it in (collect.get('bigkinds') or {}).get('items', [])}

    dead, seen = [], set()
    for e in (old_dead or {}).get('links', []) + [e for r in raws for e in mg.work[r['idx']]['dead']]:
        if e['url'] not in seen:
            seen.add(e['url'])
            dead.append(e)
    if dead:
        _dump(build / 'work' / 'dead_links.json', {'links': dead}, 'fiw.dead_links/1')

    clusters = mg.clusters(articles)
    relevance = mg.relevance()
    try:
        sel = Select(mg, [e['url'] for e in dead])
        todo, summary = sel.build(articles)
    except _Stop as e:
        print(f'report: {e}')
        return cli.EXIT_FAIL
    gone = set(sel.removed)
    for c in relevance['clusters']:
        if c['verdict'] == 'issue' and c['section'] != 'market' and c['issue_link'] in gone:
            c.update(verdict='other', issue_link=None,
                     reason='period report: no verified sentence inside the period')
        elif c['verdict'] == 'issue' and c['section'] == 'market' and c['market_topic'] in sel.removed_topics:
            c.update(verdict='other', market_topic=None,
                     reason='period report: no verified sentence inside the period')
    _dump(build / 'work' / 'clusters.json', clusters, 'fiw.clusters/1')
    _dump(build / 'work' / 'relevance.json', relevance, 'fiw.relevance/1')
    _dump(build / 'work' / 'prefilter.json', mg.prefilter(), 'fiw.prefilter/1')
    _dump(build / 'work' / 'todo.json', todo, 'fiw.todo/1')

    brief = layout.work_dir(build) / 'three_lines_brief.json'
    with open(brief, 'w', encoding='utf-8') as f:
        json.dump(three_lines_brief(todo, summary, articles, clusters, relevance), f, ensure_ascii=False, indent=1)
    if three_lines is None:
        _dump(build / 'work' / 'summary.json', summary, 'fiw.summary/1')
        print(f'report: write 세 줄 from {brief} and rerun with --three-lines FILE')
        print(f'report: kept {len(summary["issues"])} issues, {len(summary["market"])} market topics; '
              f'dropped {dict(mg.dropped)}')
        return cli.EXIT_WARN
    with open(layout.work_dir(build) / 'three_lines.json', 'w', encoding='utf-8') as f:
        json.dump(three_lines, f, ensure_ascii=False, indent=1)
    findings = []
    lines = take_three_lines(three_lines, summary, findings)
    for i, tl in enumerate(lines):
        if dates.MONTHLESS_DAY.search(tl['head']['text']):
            findings.append({'where': f'three_lines[{i}]/head', 'kind': '날짜',
                             'detail': 'a day without its month in a period report'})
    summary['three_lines'] = lines
    _dump(build / 'work' / 'summary.json', summary, 'fiw.summary/1')

    latest = mg.latest['m']
    first_seen = {i['id']: i['first_seen'] for i in accum.issues_list()}
    sources = {}
    for r in raws:
        for s, v in r['m']['sources'].items():
            cur = sources.get(s)
            rf, rt = max(v['read_from'] or a, a) if v['read_from'] else None, \
                min(v['read_to'] or b, b) if v['read_to'] else None
            if cur is None:
                sources[s] = {'status': v['status'], 'read_from': rf, 'read_to': rt}
            else:
                cur['status'] = cur['status'] if cur['status'] == v['status'] else 'partial'
                cur['read_from'] = min(x for x in (cur['read_from'], rf) if x) if (cur['read_from'] or rf) else None
                cur['read_to'] = max(x for x in (cur['read_to'], rt) if x) if (cur['read_to'] or rt) else None
    for s, env in collect.items():
        if s in sources:
            sources[s]['status'] = env['status']
    manifest = {'run_id': latest['run_id'], 'created_at': dates.now_iso(), 'today': latest['today'],
                'period': {'from': a, 'to': b}, 'period_source': 'explicit', 'plan_segment': None,
                'stages': {}, 'sources': sources,
                'period_report': {'raws': [r['m']['run_id'] for r in raws],
                                  'first_seen': {i['key']: first_seen[i['key']] for i in todo['issues']},
                                  'retro': b < latest['period']['to'], 'uncollected': uncollected}}
    _dump(layout.manifest_path(build), manifest, 'fiw.manifest/1')
    manifest = schema.load(layout.manifest_path(build), 'fiw.manifest/1')

    raw_ends = {r['m']['run_id']: r['m']['period']['to'] for r in raws}
    raw_ends.update({e['run_id']: e['period']['to'] for e in (accum.load_state('runs.json') or {}).get('runs', [])})
    overlay_state(layout.overlay_state_dir(build), todo, a, b, raw_ends)

    report = layout.report_file(build)
    with layout.reading_state(layout.overlay_state_dir(build)):
        verifier = vc.Verifier(todo, summary, watch, layout.load_format(),
                               {s: collect.get(s) for s in ('bigkinds', 'korea_kr') + runview.NOTICE_SOURCES},
                               mg.prefilter(), relevance, dead_urls=[e['url'] for e in dead], period_to=b)
        verifier.structure()
        findings += verifier.findings
        check_heads(lines, summary, verifier, findings)
        run = Run(build, manifest)
        text, _ndefs = render.build(run)
        if run.dead_failures:
            for row in run.dead_failures:
                findings.append({'where': 'render', 'kind': 'E16', 'detail': f'row left with no link: {row}'})
        schema.atomic_write(report, text)
        lt = lint.evaluate(build, manifest, text)
        lint_code = cli.EXIT_FAIL if lt.findings else cli.EXIT_OK
        gates.write_gate(build, manifest, 'lint', lint_code, lt.checked, lt.findings,
                         classified=dict(Counter(f['kind'] for f in lt.findings)), input_hash=gates.file_hash(report))
    findings += period_findings(mg, todo, summary, text, uncollected, collect)
    code = cli.EXIT_FAIL if findings else cli.EXIT_OK
    gate = gates.write_gate(build, manifest, GATE, code, len(summary['issues']) + len(summary['market']), findings,
                            classified=dict(mg.dropped), input_hash=gates.file_hash(report))
    gates.print_findings(lt.findings + findings)
    print(f'lint: {lt.checked} checks, {len(lt.findings)} findings, exit {lint_code}')
    print(f'period: {len(findings)} findings, dropped {dict(mg.dropped)}, exit {code}')
    if lint_code or code:
        return cli.EXIT_FAIL

    lk, notes = linkcheck.run_check(build, manifest, report)
    for line in notes:
        print(line)
    print(f'linkcheck: {lk["checked"]} checked, {lk["classified"]}, exit {lk["exit"]}')
    if lk['exit'] == cli.EXIT_FAIL:
        print('report: dead links found — they are recorded; rerun the same command to leave them out')
        return cli.EXIT_FAIL
    layout.reports_dir().mkdir(parents=True, exist_ok=True)
    out = layout.reports_dir() / f'{a}~{b}.md'
    schema.atomic_write(out, text.rstrip('\n') + '\n' + link_line(lk['classified']) + '\n')
    if args.json:
        cli.print_json(gate)
    print(f'report: {out}')
    return lk['exit']
