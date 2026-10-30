"""fiw.py prepare — work order for writing (writes work/todo.json or work/body_requests.json)."""
from __future__ import annotations

import sys

from lib import accum, cli, evidence, gates, korean, layout, runview, schema

from . import collect as collect_cmd

ISSUE_SECTIONS = ('gov', 'assembly', 'industry')
MAX_FLOW_CANDIDATES = 8


def register(sub):
    p = sub.add_parser('prepare', help='work order for writing (writes work/todo.json or work/body_requests.json)')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


def load_issues() -> dict:
    path = layout.state_dir() / 'issues.json'
    if not path.is_file():
        return {}
    return {i['id']: i for i in schema.load(path, 'fiw.state.issues/1')['issues']}


def load_format():
    return layout.load_format()


def lock_value(issue, kind):
    """Value of the latest lock of `kind` on a state issue, else None."""
    values = [lk['value'] for lk in (issue or {}).get('locks', []) if lk['type'] == kind]
    return values[-1] if values else None


def build_groups(clusters, relevance, state):
    """(issue groups, market groups), each a list of dicts {key, section|topic, cids, link}."""
    order = {c['cid']: k for k, c in enumerate(clusters)}
    entries = sorted((e for e in relevance['clusters'] if e['verdict'] == 'issue' and e['cid'] in order),
                     key=lambda e: order[e['cid']])
    issues, market = {}, {}
    for e in entries:
        if e['section'] == 'market':
            market.setdefault(e['market_topic'], {'topic': e['market_topic'], 'cids': []})['cids'].append(e['cid'])
            continue
        joined = e['issue_link'] if (e['issue_link'] or '').startswith('new:') else None
        link = accum.resolve(e['issue_link'], list(state.values())) if e['issue_link'] and not joined else None
        key = link or joined or f'new:{e["cid"]}'
        if key not in issues:
            pinned = lock_value(state.get(link), 'pin_section')
            issues[key] = {'key': key, 'link': link, 'section': pinned or e['section'], 'cids': []}
        # The cluster a new: key names comes first, whichever order the clusters are in.
        if key == f'new:{e["cid"]}':
            issues[key]['cids'].insert(0, e['cid'])
        else:
            issues[key]['cids'].append(e['cid'])
    topics = [market[t] for t in runview.MARKET_TOPICS if t in market]
    return list(issues.values()), topics


def select(group, by_cid, arts, failed, limit):
    """Add to group: owner (article id → cid), core, cands (URL candidates), chosen (selection order).

    Candidates: core articles with a URL, by (not a cluster rep, date desc, id). Two passes — new
    outlets first, then the rest — skipping articles whose body is known to have failed.
    """
    owner = {}
    for cid in group['cids']:
        for aid in by_cid[cid]['core_ids']:
            owner.setdefault(aid, cid)
    core = sorted(owner, key=lambda aid: (arts[aid]['date'], aid))
    reps = {by_cid[cid]['rep_id'] for cid in group['cids']}
    cands = [aid for aid in core if arts[aid]['url']]
    cands.sort(key=lambda aid: aid)
    cands.sort(key=lambda aid: arts[aid]['date'], reverse=True)
    cands.sort(key=lambda aid: aid not in reps)
    chosen, outlets = [], set()
    for first_pass in (True, False):
        for aid in cands:
            if len(chosen) >= limit:
                break
            if aid in chosen or aid in failed:
                continue
            if first_pass and arts[aid]['outlet'] in outlets:
                continue
            chosen.append(aid)
            outlets.add(arts[aid]['outlet'])
    group.update(owner=owner, core=core, cands=cands, chosen=chosen)


def excluded_entry(group, by_cid, arts):
    first = by_cid[group['cids'][0]]
    return {'title': arts[first['rep_id']]['title'],
            'outlets': sorted({arts[aid]['outlet'] for aid in group['core']})}


def article_obj(ref, aid, arts, bodies):
    a, body = arts[aid], bodies[aid]['body']
    conflicts = evidence.number_tokens(a['title']) - evidence.number_tokens(body)
    check = {'chars': len(body), 'src_chars': bodies[aid].get('chars'), 'ends_sentence': evidence.ends_sentence(body)}
    return {'ref': ref, 'id': aid, 'title': a['title'], 'outlet': a['outlet'], 'date': a['date'],
            'url': a['url'], 'body': body, 'title_body_conflicts': sorted(conflicts), 'body_check': check}


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    clusters = gates.load_work(rd, 'clusters', 'fiw.clusters/1')['clusters']
    relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1')
    rel_hash = gates.file_hash(rd / 'work' / 'relevance.json')
    gate = gates.read_gate(rd, 'relevance')
    if gate is None or gate['exit'] != 0 or gate['input_hash'] != rel_hash:
        print('error: gates/relevance.json does not pass for the current relevance.json '
              '— run relevance --check first', file=sys.stderr)
        gates.record_stage(rd, manifest, 'prepare', cli.EXIT_FAIL, rel_hash)
        print(f'prepare: exit {cli.EXIT_FAIL}')
        return cli.EXIT_FAIL
    env = runview.envelope(rd, 'bigkinds')
    if env is None:
        raise cli.UsageError('missing collect/bigkinds.json — run the bigkinds_list browser job and ingest first')
    watch = collect_cmd.load_watch()
    limit = load_format()['max_articles_per_issue']
    state = load_issues()
    bodies_obj = gates.load_work(rd, 'bodies', 'fiw.bodies/1', required=False)

    arts = {a['id']: a for a in env['items']}
    by_cid = {c['cid']: c for c in clusters}
    bodies = {b['id']: b for b in (bodies_obj or {}).get('articles', [])}
    failed = {e['id'] for e in (bodies_obj or {}).get('errors', [])}
    failed |= {aid for aid, b in bodies.items() if not b['body'].strip()}
    # A body that looks cut (shorter than the browser read, or no sentence ending) is a failed body too.
    cut = sorted(aid for aid, b in bodies.items()
                 if aid not in failed and evidence.cut_suspect(b['body'], b.get('chars')))
    failed |= set(cut)
    cut_line = f'{len(cut)} bodies look cut — skipped: {", ".join(cut)}' if cut else None

    issue_groups, market_groups = build_groups(clusters, relevance, state)
    excluded_no_link, kept_issues, kept_market = [], [], []
    for group in issue_groups + market_groups:
        select(group, by_cid, arts, failed, limit)
        if not group['cands']:
            excluded_no_link.append(excluded_entry(group, by_cid, arts))
        else:
            (kept_market if 'topic' in group else kept_issues).append(group)

    missing = sorted({aid for g in kept_issues + kept_market for aid in g['chosen'] if aid not in bodies})
    if missing:
        req = {'schema': 'fiw.body_requests/1', 'ids': missing}
        (rd / 'work').mkdir(exist_ok=True)
        schema.dump(rd / 'work' / 'body_requests.json', req, 'fiw.body_requests/1')
        code = cli.EXIT_WARN
        gates.record_stage(rd, manifest, 'prepare', code, rel_hash)
        if args.json:
            cli.print_json(req)
        else:
            if cut_line:
                print(cut_line)
            print(f'bodies needed: {len(missing)} — run the bigkinds_bodies browser job, ingest, then prepare again')
            print(f'prepare: exit {code}')
        return code

    excluded_no_body = []
    lost_by_section = {}
    for group in kept_issues + kept_market:
        if not group['chosen']:
            excluded_no_body.append(excluded_entry(group, by_cid, arts))
            if 'section' in group:
                lost_by_section[group['section']] = lost_by_section.get(group['section'], 0) + 1
    kept_issues = [g for g in kept_issues if g['chosen']]
    kept_market = [g for g in kept_market if g['chosen']]

    own = watch['own_company']
    lwf_mentions = {}
    numbering = {}

    def articles_of(group):
        out = []
        for aid in group['chosen']:
            cid = group['owner'][aid]
            numbering[cid] = numbering.get(cid, 0) + 1
            ref = f'{cid}:{numbering[cid]}'
            obj = article_obj(ref, aid, arts, bodies)
            lwf_mentions[ref] = korean.contains_word(obj['title'] + '\n' + obj['body'], own)
            out.append(obj)
        return out

    todo_issues = []
    for group in kept_issues:
        linked = state.get(group['link']) if group['link'] else None
        if linked is not None:
            flow = [{'ref': f'{linked["id"]}:f{n}', 'title': node['text'], 'date': node['date'],
                     'url': node['url'], 'excerpt': node['text']}
                    for n, node in enumerate(linked['flow'], 1)]
            prior = {'title': linked['title'], 'last_sentences': [s['text'] for s in linked['last_sentences']]}
            if linked['corrections']:
                prior['corrections'] = [c['diff'] for c in linked['corrections']]
        else:
            rest = [aid for aid in group['core'] if arts[aid]['url'] and aid not in group['chosen']]
            flow = [{'ref': f'{group["cids"][0]}:f{n}', 'title': arts[aid]['title'], 'date': arts[aid]['date'],
                     'url': arts[aid]['url'], 'excerpt': arts[aid]['excerpt'], 'outlet': arts[aid]['outlet']}
                    for n, aid in enumerate(rest[:MAX_FLOW_CANDIDATES], 1)]
            prior = None
        todo_issues.append({
            'key': group['key'], 'section': group['section'], 'articles': articles_of(group),
            # the cluster core, as the report's outlets/articles tag counts it
            'coverage': {'outlets': len({arts[aid]['outlet'] for aid in group['core']}),
                         'articles': len(group['core'])},
            'flow_candidates': flow, 'prior': prior,
            'locks': {'rename': lock_value(linked, 'rename'), 'pin_section': lock_value(linked, 'pin_section')},
        })
    todo_market = [{'topic': g['topic'], 'articles': articles_of(g)} for g in kept_market]

    todo = {'schema': 'fiw.todo/1', 'issues': todo_issues, 'excluded_no_link': excluded_no_link,
            'excluded_no_body': excluded_no_body, 'market': todo_market, 'lwf_mentions': lwf_mentions}
    (rd / 'work').mkdir(exist_ok=True)
    schema.dump(rd / 'work' / 'todo.json', todo, 'fiw.todo/1')

    # Exit 1 when some section had issue groups and every one of them was lost to body failures.
    totals = {}
    for group in issue_groups:
        totals[group['section']] = totals.get(group['section'], 0) + 1
    emptied = [s for s in ISSUE_SECTIONS if totals.get(s) and lost_by_section.get(s) == totals[s]]
    if emptied:
        code = cli.EXIT_FAIL
    elif excluded_no_body or cut:
        code = cli.EXIT_WARN
    else:
        code = cli.EXIT_OK
    gates.record_stage(rd, manifest, 'prepare', code, rel_hash)

    if args.json:
        cli.print_json(todo)
    else:
        print(f'prepare: {len(todo_issues)} issues, {len(todo_market)} market topics, '
              f'{len(excluded_no_link)} excluded (no link), {len(excluded_no_body)} excluded (no body)')
        for e in excluded_no_body:
            print(f'근거 본문 미수신: {e["title"]} ({"·".join(e["outlets"])})')
        if cut_line:
            print(cut_line)
        if emptied:
            print('every issue of section ' + ', '.join(emptied) + ' lost its source bodies')
        print(f'prepare: exit {code}')
    return code
