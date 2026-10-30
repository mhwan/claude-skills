"""fiw.py cluster — rule stage and clustering of BigKinds articles (writes work/clusters.json,
work/clusters_view.md and work/clusters_brief.md).

Port of the prototype `cluster_sian.py`: character-bigram TF-IDF over the cleaned title (twice)
and the excerpt head, cosine edges found through an inverted index, union-find components.
"""
from __future__ import annotations

import bisect
import collections
import math
import re

from lib import accum, cli, dates, entities, gates, korean, layout, schema

from . import collect as collect_cmd

EDGE_MIN = 0.42           # cosine for an edge and for core membership
PRIOR_MIN = 0.30          # title cosine for a prior-issue candidate
DF_MAX_SHARE = 0.2        # bigrams with df >= 20 % of N carry no weight
POSTINGS_MAX = 400        # longer postings lists are skipped when finding edges
EXCERPT_HEAD = 120
VIEW_EXTRA_LINES = 4      # core titles shown after the representative
AUTO_RULE = 'single-outlet-no-signal'

_NOT_TEXT = re.compile(r'[^가-힣A-Za-z0-9]')


def register(sub):
    p = sub.add_parser('cluster', help='rule stage and clustering of BigKinds articles (writes work/clusters.json)')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


# ---------------------------------------------------------------- rule stage

def rule_drop(title, exclude, signals):
    """'exclude:{word}' when the title has an exclude word and no include signal, else None."""
    word = next((w for w in exclude if korean.contains_word(title, w, left_guard=True)), None)
    if word is None or any(s in title for s in signals):
        return None
    return f'exclude:{word}'


def needs_review(members, signals, own_company) -> bool:
    """Review cut over all members (not only core — a related report outside core still counts).

    Reviewed when ≥ 2 outlets, a signal word starts a word in some title, or the own company is
    named in some title. Measured 2026-10-26 on the reference month: the earlier "came from a
    government / Assembly query" reason put 2,647 one-outlet clusters (mostly oil-price and
    drug news) into review and recalled no reference issue on its own.
    """
    if len({a['outlet'] for a in members}) >= 2:
        return True
    for a in members:
        if korean.contains_word(a['title'], own_company, left_guard=True):
            return True
        if any(korean.contains_word(a['title'], s, left_guard=True) for s in signals):
            return True
    return False


# ---------------------------------------------------------------- vectors

def _clean(text) -> str:
    return _NOT_TEXT.sub('', text or '')


def bigram_counts(text) -> collections.Counter:
    t = _clean(text)
    return collections.Counter(t[i:i + 2] for i in range(len(t) - 1))


def tfidf_vectors(articles) -> list:
    """L2-normalised {bigram: weight} per article; bigrams with df >= 20 % of N are dropped."""
    grams = []
    for a in articles:
        title = korean.strip_title_prefix(a['title'])
        grams.append(bigram_counts(title + ' ' + title + ' ' + (a['excerpt'] or '')[:EXCERPT_HEAD]))
    df = collections.Counter(g for c in grams for g in c)
    n = len(articles)
    vectors = []
    for c in grams:
        v = {g: (1 + math.log(tf)) * math.log(n / df[g]) for g, tf in c.items() if df[g] < DF_MAX_SHARE * n}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vectors.append({g: x / norm for g, x in v.items()})
    return vectors


def dot(a, b) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b[g] for g, x in a.items() if g in b)


def cosine_counts(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a.values()))
    nb = math.sqrt(sum(x * x for x in b.values()))
    if not na or not nb:
        return 0.0
    return dot(a, b) / (na * nb)


# ---------------------------------------------------------------- components

def components(vectors, ents) -> list:
    """Union-find over edges (accumulated score >= EDGE_MIN, entity guard); lists of indexes."""
    n = len(vectors)
    post_ids, post_w = collections.defaultdict(list), collections.defaultdict(list)
    for i, v in enumerate(vectors):
        for g, w in v.items():
            post_ids[g].append(i)
            post_w[g].append(w)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, v in enumerate(vectors):
        acc = collections.defaultdict(float)
        for g, x in v.items():
            ids = post_ids[g]
            if len(ids) > POSTINGS_MAX:
                continue
            ws = post_w[g]
            for k in range(bisect.bisect_right(ids, i), len(ids)):
                acc[ids[k]] += x * ws[k]
        for j, s in acc.items():
            if s < EDGE_MIN:
                continue
            if ents[i] and ents[j] and ents[i].isdisjoint(ents[j]):
                continue                          # entity guard
            parent[find(i)] = find(j)

    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def _tie_key(x) -> float:
    return round(x, 9)


def describe(members, kept, vectors, ent_names, entity_order):
    """(rep index, core indexes, entity names) of one component."""
    def date_id(i):
        return (kept[i]['date'], kept[i]['id'])

    if len(members) == 1:
        rep = members[0]
    else:
        total = collections.defaultdict(float)
        for i in members:
            for g, w in vectors[i].items():
                total[g] += w
        mean = {}
        for i in members:
            self_sim = dot(vectors[i], vectors[i])
            mean[i] = (dot(vectors[i], total) - self_sim) / (len(members) - 1)
        # Highest mean similarity; ties: later date, then has a URL, then smaller id.
        best = max(_tie_key(mean[i]) for i in members)
        tied = [i for i in members if _tie_key(mean[i]) == best]
        latest = max(kept[i]['date'] for i in tied)
        tied = [i for i in tied if kept[i]['date'] == latest]
        with_url = [i for i in tied if kept[i]['url']]
        tied = with_url or tied
        rep = min(tied, key=lambda i: kept[i]['id'])
    core = [i for i in members if i == rep or dot(vectors[i], vectors[rep]) >= EDGE_MIN]
    core.sort(key=date_id)
    counts = collections.Counter(name for i in core for name in ent_names[i])
    names = sorted(counts, key=lambda name: (-counts[name], entity_order[name]))
    return rep, core, names


# ---------------------------------------------------------------- prior issues

def _clean_title(title) -> str:
    return _clean(korean.strip_title_prefix(title))


def prior_candidates(rep_title, names, tracked, issues) -> list:
    """State issue ids sharing a cluster entity (in the issue title) with title cosine >= 0.30."""
    if not issues or not names:
        return []
    rep_grams = bigram_counts(_clean_title(rep_title))
    hits = []
    for pos, issue in enumerate(issues):
        if not any(korean.match_entity(issue['title'], tracked[name]) for name in names):
            continue
        sim = cosine_counts(rep_grams, bigram_counts(_clean_title(issue['title'])))
        if sim >= PRIOR_MIN:
            hits.append((-sim, pos, issue['id']))
    hits.sort()
    return [h[2] for h in hits]


# ---------------------------------------------------------------- view file

def _one_line(text) -> str:
    return ' '.join((text or '').split('\n')).replace('\r', ' ')


def view_block(c, by_id, period_to) -> list:
    core = [by_id[i] for i in c['core_ids']]
    outlets = {a['outlet'] for a in core}
    head = (f'## {c["cid"]} · {len(outlets)}사 {len(core)}건 · '
            f'{dates.short_span(c["from"], c["to"], period_to)}')
    if c['entities']:
        head += ' · ' + ', '.join(c['entities'])
    rep = by_id[c['rep_id']]
    rest = sorted((a for a in core if a['id'] != rep['id']), key=lambda a: a['id'])
    rest.sort(key=lambda a: a['date'], reverse=True)
    shown, picked = {rep['outlet']}, []
    for a in rest:                                  # members with an outlet not shown yet first
        if len(picked) < VIEW_EXTRA_LINES and a['outlet'] not in shown:
            picked.append(a)
            shown.add(a['outlet'])
    for a in rest:                                  # then the rest
        if len(picked) < VIEW_EXTRA_LINES and a not in picked:
            picked.append(a)
    lines = [head]
    for a in [rep] + picked:
        lines.append(f'- {dates.short_day(a["date"], period_to)} {a["outlet"]} | {_one_line(a["title"])}')
    if c['prior_issue_candidates']:
        lines.append('- 이전 쟁점 후보: ' + ', '.join(c['prior_issue_candidates']))
    return lines


def brief_line(c, by_id, period_to) -> str:
    """One line per reviewed cluster: cid, coverage, span, representative title, entities, prior ids."""
    core = [by_id[i] for i in c['core_ids']]
    line = (f'{c["cid"]} {len({a["outlet"] for a in core})}사{len(core)}건 '
            f'{dates.short_span(c["from"], c["to"], period_to)} {_one_line(by_id[c["rep_id"]]["title"])}')
    if c['entities']:
        line += ' [' + ', '.join(c['entities']) + ']'
    if c['prior_issue_candidates']:
        line += ' (이전 쟁점 후보 ' + ', '.join(c['prior_issue_candidates']) + ')'
    return line


def issue_lines(issues, period_to) -> list:
    """Earlier issues to link to (prior candidates only fire when a tracked name is in the issue title)."""
    if not issues:
        return []
    out = ['# 지난 보고서 쟁점 — 이어지는 묶음은 issue_link 에 이 ID (잠금은 state/issues.json)']
    for i in issues:
        last = i.get('last_article') or i['first_seen']
        lock = ' 🔒' if i['locks'] else ''
        out.append(f'# {i["id"]} {i["section"]}·{i["status"]} 마지막 {dates.short_day(last, period_to)} {i["title"]}{lock}')
    return out


def brief_text(clusters, by_id, period, issues=()) -> str:
    """clusters_brief.md — the reading list for relevance (about a quarter of clusters_view.md)."""
    reviewed = [c for c in clusters if c['auto'] is None]
    out = [f'# 묶음 한 줄 목록 — {period["from"]} ~ {period["to"]} · 검토 {len(reviewed)}개 / 전체 {len(clusters)}개',
           '# cid · 매체 수·기사 수 · 기간 · 대표 기사 제목 · [추적 엔티티] — 자세히는 clusters_view.md 의 같은 cid']
    out.extend(issue_lines(issues, period['to']))
    out.extend(brief_line(c, by_id, period['to']) for c in reviewed)
    return '\n'.join(out) + '\n'


def view_text(clusters, by_id, period) -> str:
    reviewed = [c for c in clusters if c['auto'] is None]
    out = [f'# 묶음 검토 — {period["from"]} ~ {period["to"]} · 검토 {len(reviewed)}개 / 전체 {len(clusters)}개']
    for c in reviewed:
        out.append('')
        out.extend(view_block(c, by_id, period['to']))
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------- main

def load_issues():
    path = layout.state_dir() / 'issues.json'
    if not path.is_file():
        return []
    return schema.load(path, 'fiw.state.issues/1')['issues']


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    bk_path = layout.collect_dir(rd) / 'bigkinds.json'
    if not bk_path.is_file():
        raise cli.UsageError('missing collect/bigkinds.json — run the bigkinds_list browser job and ingest first')
    env = schema.load(bk_path, 'fiw.collect/1')
    watch = collect_cmd.load_watch()
    issues = load_issues()

    tracked_list = entities.all_entities(watch)
    tracked = {e['name']: e for e in tracked_list}
    entity_order = {e['name']: k for k, e in enumerate(tracked_list)}
    exclude, signals = watch['stage1']['exclude'], watch['stage1']['include_signals']

    articles = env['items']
    by_id = {a['id']: a for a in articles}
    seen = accum.load_state('seen_ids.json')
    seen_ids = set((seen or {}).get('sources', {}).get('bigkinds', []))
    kept, dropped = [], []
    for a in articles:
        if a['id'] in seen_ids:                       # already reported by the previous run (D14)
            dropped.append({'article_id': a['id'], 'rule': f'seen:{seen["run_id"]}'})
            continue
        rule = rule_drop(a['title'], exclude, signals)
        if rule:
            dropped.append({'article_id': a['id'], 'rule': rule})
        else:
            kept.append(a)

    ent_names = [[e['name'] for e in tracked_list
                  if korean.match_entity(a['title'] + ' ' + (a['excerpt'] or ''), e)] for a in kept]
    vectors = tfidf_vectors(kept)
    comps = components(vectors, [set(n) for n in ent_names])

    raw = []
    for members in comps:
        rep, core, names = describe(members, kept, vectors, ent_names, entity_order)
        core_arts = [kept[i] for i in core]
        member_arts = [kept[i] for i in members]
        outlets = {a['outlet'] for a in core_arts}
        reviewed = needs_review(member_arts, signals, watch['own_company'])
        member_ids = [kept[i]['id'] for i in sorted(members, key=lambda i: (kept[i]['date'], kept[i]['id']))]
        raw.append({
            'cid': '', 'article_ids': member_ids, 'core_ids': [a['id'] for a in core_arts],
            'rep_id': kept[rep]['id'], 'from': core_arts[0]['date'], 'to': core_arts[-1]['date'],
            'entities': names,
            'prior_issue_candidates': prior_candidates(kept[rep]['title'], names, tracked, issues),
            'auto': None if reviewed else AUTO_RULE,
            '_outlets': len(outlets),
        })
    raw.sort(key=lambda c: (c['auto'] is not None, -c['_outlets'], -len(c['core_ids']),
                            -len(c['article_ids']), c['from'], c['rep_id']))
    clusters = []
    for k, c in enumerate(raw, 1):
        c.pop('_outlets')
        c['cid'] = f'c{k:04d}'
        clusters.append(c)

    out = {'schema': 'fiw.clusters/1',
           'rule_stage': {'input_articles': len(articles), 'dropped_by_rule': len(dropped),
                          'clusters': len(clusters)},
           'clusters': clusters, 'dropped': dropped}
    (rd / 'work').mkdir(exist_ok=True)
    schema.dump(rd / 'work' / 'clusters.json', out, 'fiw.clusters/1')
    schema.atomic_write(rd / 'work' / 'clusters_view.md', view_text(clusters, by_id, manifest['period']))
    schema.atomic_write(rd / 'work' / 'clusters_brief.md', brief_text(clusters, by_id, manifest['period'], issues))
    code = cli.EXIT_OK
    gates.record_stage(rd, manifest, 'cluster', code, gates.file_hash(bk_path))

    if args.json:
        cli.print_json(out)
    else:
        reviewed = sum(1 for c in clusters if c['auto'] is None)
        print(f'cluster: {len(articles)} articles, {len(dropped)} dropped by rule, '
              f'{len(clusters)} clusters, {reviewed} to review')
        print(f'cluster: exit {code}')
    return code
