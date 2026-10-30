"""fiw.py review — checklists for the human source check (02-plan §9.6, m6-m7-addendum §E)."""
from __future__ import annotations

import random

from lib import cli, entities, evidence, gates, korean

from ._render_model import Run


def register(sub):
    p = sub.add_parser('review', help='checklist of report statements with their sources, for a human check')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--all', action='store_true', help='every narrative field')
    mode.add_argument('--risk', action='store_true', help='every risk field plus --n random others')
    mode.add_argument('--dropped', type=int, metavar='N', help='N rule-dropped or auto-cut articles')
    mode.add_argument('--other', type=int, metavar='N', help='N clusters judged other or noise')
    mode.add_argument('--cluster-members', type=int, metavar='N', help='N core titles per issue')
    p.add_argument('--n', type=int, metavar='N', help='with --risk: number of random other fields')
    p.add_argument('--seed', type=int, metavar='S', help='random seed for --risk, --dropped, --other')
    p.add_argument('--json', action='store_true', help='print the lines as JSON')
    p.set_defaults(func=main)


def _ref_part(run, refs) -> str:
    out = []
    for ref in refs:
        info = run.refs.get(ref)
        if info is None:
            continue
        out.append(f' — {info["outlet"]} 「{" ".join(info["title"].split())}」 {info["url"]}')
    return ''.join(out)


def fields(run) -> list:
    """[(where, text, refs, risk flags set)] for every narrative field of the summary."""
    out = []

    def add(where, text, refs, flags=(), tail=''):
        out.append((where, text, list(refs), set(flags), tail))

    def sentence_flags(s):
        flags = set()
        if s['kind'] == 'edit':
            flags.add('edit')
        if s.get('evidence'):
            flags.add('evidence')
        if len(evidence.number_tokens(s['text'])) >= 2 and len(_entities(run, s['text'])) >= 2:
            flags.add('dense')
        return flags

    for i, tl in enumerate(run.summary['three_lines']):
        add(f'three_lines[{i}]', tl['head']['text'], tl['head']['refs'], {'three_lines'})
        for s in tl['sentences']:
            add(f'three_lines[{i}]', s['text'], s['refs'], {'three_lines'} | sentence_flags(s))
    for iss in run.summary['issues']:
        key = iss['key']
        add(f'{key}/title', iss['title']['text'], iss['title']['refs'])
        for j, a in enumerate(iss.get('actors') or []):
            add(f'{key}/actors[{j}]', a['text'], a['refs'])
        for j, s in enumerate(iss['sentences']):
            add(f'{key}/sentences[{j}]', s['text'], s['refs'], sentence_flags(s))
        for j, s in enumerate(((iss.get('lwf') or {}).get('sentences')) or []):
            add(f'{key}/lwf[{j}]', s['text'], s['refs'], {'lwf'} | sentence_flags(s))
        if iss.get('flow_name'):
            add(f'{key}/flow_name', iss['flow_name']['text'], [], {'edit'})
        for j, node in enumerate(iss.get('flow') or []):
            add(f'{key}/flow[{j}]', node['text'], [node['ref']])
    for m in run.summary['market']:
        for j, s in enumerate(m['sentences']):
            add(f'market:{m["topic"]}/sentences[{j}]', s['text'], s['refs'], sentence_flags(s))
        for j, s in enumerate(((m.get('lwf') or {}).get('sentences')) or []):
            add(f'market:{m["topic"]}/lwf[{j}]', s['text'], s['refs'], {'lwf'} | sentence_flags(s))
    press = {i['id']: i for i in run.items('korea_kr')}
    for n in run.summary['notices']:
        item = press.get(n['id'])
        if n['label'] and item is not None:        # a grouping label Claude wrote for a press release
            title = ' '.join(item['title'].split())
            add(f'notices/{n["id"]}', n['label'], [], tail=f' — 정책브리핑 「{title}」 {item["url"]}')
    return out


def _entities(run, text) -> list:
    return [e['name'] for e in entities.all_entities(run.watch) if korean.match_entity(text, e)]


def risk_rows(run) -> list:
    """Notice and bill rows: every notice row's period (⏰ rows flagged) and every bill row."""
    out = []
    for r in run.notice_rows():
        lead = r['items'][0]
        when = '미확인' if r['d'] is None else f'{r["from"]} ~ {r["to"]}'
        mark = '⏰ ' if r['open'] else ''
        sources = ' · '.join(f'「{i["title"]}」 {i["url"]}' for i in r['items'])   # a family row stands for every notice in it
        out.append(f'[notices/{lead["id"]}] {mark}{r["title"]} 의견 {when} — {r["dept"]} {sources}')
    if run.status('assembly_bills') in ('ok', 'partial'):
        for b in run.bill_rows():
            out.append(f'[bills/{b["bill_id"]}] {b["date"]} {b["name"]} ({b["proposer"]}) — 열린국회정보 '
                       f'「{b["name"]}」 {b["url"]}')
    return out


def _article_line(where, a) -> str:
    title = ' '.join(a['title'].split())
    return f'[{where}] {title} — {a["outlet"]} 「{title}」 {a["url"]}'


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    run = Run(rd, manifest)
    if args.risk and args.n is None:
        raise cli.UsageError('--risk needs --n N (random fields besides the risk ones)')
    if (args.risk or args.dropped is not None or args.other is not None) and args.seed is None:
        raise cli.UsageError('--risk, --dropped and --other need --seed S (so the list can be repeated)')
    for v in (args.n, args.dropped, args.other, args.cluster_members):
        if v is not None and v < 0:
            raise cli.UsageError('counts must be ≥ 0')
    lines = []
    if args.all or args.risk:
        rows = fields(run)
        if args.risk:
            rest = [k for k, r in enumerate(rows) if not r[3]]
            pick = set(random.Random(args.seed).sample(rest, min(args.n, len(rest))))
            rows = [r for k, r in enumerate(rows) if r[3] or k in pick]       # report order
        lines = [f'[{w}] {t}{_ref_part(run, refs)}{tail}' for w, t, refs, _, tail in rows]
        if args.risk:
            lines += risk_rows(run)
    elif args.dropped is not None:
        pool = [('dropped/' + d['article_id'], run.articles[d['article_id']]) for d in run.clusters_doc['dropped']  # gr-compat: ignore=pathjoin (an item label, not a file path)
                if d['article_id'] in run.articles]
        pool += [(f'auto/{c["cid"]}', run.articles[c['rep_id']]) for c in run.clusters_doc['clusters']
                 if c.get('auto') and c['rep_id'] in run.articles]
        chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.dropped, len(pool))))
        lines = [_article_line(pool[k][0], pool[k][1]) for k in chosen]
    elif args.other is not None:
        pool = [(f'{c["verdict"]}/{c["cid"]}', run.rep(c['cid'])) for c in run.relevance['clusters']
                if c['verdict'] in ('other', 'noise') and run.rep(c['cid']) is not None]
        chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.other, len(pool))))
        lines = [_article_line(pool[k][0], pool[k][1]) for k in chosen]
    else:
        for issue in run.todo['issues']:
            ids = run.core(run.issue_cids(issue))[:args.cluster_members]
            lines += [_article_line(f'members/{issue["key"]}', run.articles[i]) for i in ids]
    numbered = [f'{n}. {line}' for n, line in enumerate(lines, 1)]
    if args.json:
        cli.print_json({'run': manifest['run_id'], 'lines': numbered})
    else:
        for line in numbered:
            print(line)
        print(f'review: {len(numbered)} item(s)')
    return cli.EXIT_OK
