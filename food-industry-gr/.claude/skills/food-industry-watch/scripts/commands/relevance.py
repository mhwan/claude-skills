"""fiw.py relevance --check — judgment gate J over work/relevance.json."""
from __future__ import annotations

from lib import cli, gates, layout, runview, schema

# Prefilter `include` items of these sources need a relevance entry (foodsafety never does).
REQUIRED_INCLUDE_SOURCES = ('korea_kr',) + runview.NOTICE_SOURCES + ('assembly_bills',)


def register(sub):
    p = sub.add_parser('relevance', help='judgment gate J over work/relevance.json (use --check)')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--check', action='store_true', help='run the judgment gate J')
    p.add_argument('--compile', metavar='FILE',
                   help='write work/relevance.json from a fiw.relevance_brief/1 file, then run the gate')
    p.add_argument('--json', action='store_true', help='print the fiw.gate/1 JSON')
    p.set_defaults(func=main)


def _noise(cid, reason) -> dict:
    return {'cid': cid, 'verdict': 'noise', 'section': None, 'market_topic': None, 'issue_link': None,
            'reason': reason}


def required_keys(prefilter) -> list:
    """(source, id) of the prefilter items that need a relevance entry, in file order."""
    return [(it['source'], it['id']) for it in prefilter['items']
            if it['verdict'] == 'undecided'
            or (it['verdict'] == 'include' and it['source'] in REQUIRED_INCLUDE_SOURCES)]


def compile_brief(brief, reviewed, required_items) -> dict:
    """fiw.relevance_brief/1 -> fiw.relevance/1, entries in file order (duplicates kept for the gate).

    reviewed: the cids of clusters.json that need an entry, in order. noise_rest must name the last one
    and the number of clusters it covers, else UsageError (the list was not read to the end).
    required_items: required_keys(prefilter), for items_rest.
    """
    clusters = []
    for e in brief['clusters']:
        link = e.get('issue_link', 'new' if e['verdict'] == 'issue' else None)
        clusters.append({'cid': e['cid'], 'verdict': e['verdict'], 'section': e['section'],
                         'market_topic': e.get('market_topic'), 'issue_link': link, 'reason': e['reason']})
    for group in brief['noise']:
        clusters.extend(_noise(cid, group['reason']) for cid in group['cids'])
    rest = brief.get('noise_rest')
    if rest is not None:
        listed = {e['cid'] for e in clusters}
        left = [cid for cid in reviewed if cid not in listed]
        if not reviewed or rest['last_cid'] != reviewed[-1]:
            raise cli.UsageError(f'noise_rest.last_cid {rest["last_cid"]!r} is not the last reviewed cluster '
                                 f'{reviewed[-1] if reviewed else None!r} — read work/clusters_brief.md to the end')
        if rest['count'] != len(left):
            raise cli.UsageError(f'noise_rest.count {rest["count"]} but {len(left)} reviewed clusters are not '
                                 'listed — check the lists for missing or mistyped cids')
        clusters.extend(_noise(cid, rest['reason']) for cid in left)
    items = [{'source': it['source'], 'id': it['id'], 'verdict': 'include', 'category': it['category'],
              'reason': it['reason']} for it in brief['items']]
    for group in brief['items_other']:
        for id_ in group['ids']:
            items.append({'source': group['source'], 'id': id_, 'verdict': 'other', 'category': None,
                          'reason': group['reason']})
    listed_items = {(it['source'], it['id']) for it in items}
    for group in brief.get('items_rest') or []:
        left = [id_ for (source, id_) in required_items if source == group['source']
                and (source, id_) not in listed_items]
        if group['count'] != len(left):
            raise cli.UsageError(f'items_rest {group["source"]}: count {group["count"]} but {len(left)} items '
                                 'that need an entry are not listed — check work/items_view.md')
        items.extend({'source': group['source'], 'id': id_, 'verdict': 'other', 'category': None,
                      'reason': group['reason']} for id_ in left)
    return {'schema': 'fiw.relevance/1', 'clusters': clusters, 'items': items}


def load_issues():
    path = layout.state_dir() / 'issues.json'
    if not path.is_file():
        return None
    return schema.load(path, 'fiw.state.issues/1')['issues']


def _rep_titles(rd):
    env = runview.envelope(rd, 'bigkinds')
    if env is None:
        raise cli.UsageError('missing collect/bigkinds.json — needed for the exclude_pattern lock check')
    return {a['id']: a['title'] for a in env['items']}


def check_clusters(rd, clusters, entries, issues, find):
    by_cid = {c['cid']: c for c in clusters}
    state = {i['id']: i for i in issues or []}
    alias_of = {a: i['id'] for i in issues or [] for a in i['aliases']}
    flow_ids = {i['id']: {n['article_id'] for n in i['flow']} for i in issues or []}
    titles = None
    # New issues of this run another cluster may join with issue_link "new:<cid>".
    leads = {e['cid']: e for e in entries
             if e['verdict'] == 'issue' and e['issue_link'] == 'new' and e['section'] != 'market'}

    seen = {}
    for e in entries:
        seen[e['cid']] = seen.get(e['cid'], 0) + 1
    for cid, n in seen.items():
        if n > 1:
            find(f'cluster/{cid}', 'duplicate', f'{n} entries for one cluster')
    for c in clusters:
        if c.get('auto') is None and c['cid'] not in seen:
            find(f'cluster/{c["cid"]}', 'missing', 'reviewed cluster has no relevance entry')

    for e in entries:
        where, cid, verdict, section = f'cluster/{e["cid"]}', e['cid'], e['verdict'], e['section']
        cluster = by_cid.get(cid)
        if cluster is None:
            find(where, 'unknown', 'cid is not in clusters.json')
        if verdict in ('issue', 'other') and section is None:
            find(where, 'section', f'verdict {verdict} needs a section')
        topic = e['market_topic']
        if section == 'market' and topic not in runview.MARKET_TOPICS:
            find(where, 'market_topic', f'section market needs one of {" / ".join(runview.MARKET_TOPICS)}; '
                 f'got {topic!r}')
        elif section != 'market' and topic is not None:
            find(where, 'market_topic', f'market_topic {topic!r} is only allowed with section market')

        link = e['issue_link']
        if verdict != 'issue':
            if link is not None:
                find(where, 'issue_link', f'verdict {verdict} must have issue_link null; got {link!r}')
            continue
        if link == 'new':
            continue
        if isinstance(link, str) and link.startswith('new:'):
            lead = leads.get(link[4:])
            if lead is None:
                find(where, 'issue_link', f'{link!r} — {link[4:]} is not an issue of this run with issue_link '
                     '"new" outside market')
            elif lead['section'] != section:
                find(where, 'issue_link', f'{link!r} is in section {lead["section"]}; entry has {section}')
            continue
        if link not in state:
            if link in alias_of:
                detail = f'{link!r} is an alias — use the current id {alias_of[link]}'
            elif issues is None:
                detail = f'{link!r} — no state/issues.json, only "new" is allowed'
            else:
                detail = f'{link!r} is neither "new" nor an issue id of state/issues.json'
            find(where, 'issue_link', detail)
            continue
        if cluster is None:
            continue
        for lock in state[link]['locks']:
            kind, value = lock['type'], lock['value']
            if kind == 'pin_section' and value != section:
                find(where, 'lock', f'{link} is pinned to section {value}; entry has {section}')
            elif kind == 'exclude_pattern':
                if titles is None:
                    titles = _rep_titles(rd)
                rep_title = titles.get(cluster['rep_id'], '')
                if value in rep_title:
                    find(where, 'lock', f'{link} excludes 「{value}」, found in the representative title')
            elif kind == 'no_merge_with':
                shared = set(cluster['article_ids']) & flow_ids.get(value, set())
                if shared:
                    find(where, 'lock', f'{link} must not merge with {value}; shared articles: '
                         + ', '.join(sorted(shared)))


def check_items(prefilter, relevance, find):
    known = {(it['source'], it['id']): it for it in prefilter['items']}
    required = required_keys(prefilter)
    counts = {}
    for it in relevance['items']:
        key = (it['source'], it['id'])
        counts[key] = counts.get(key, 0) + 1
    for key in required:
        if key not in counts:
            find(f'item/{key[0]}/{key[1]}', 'missing',
                 f'prefilter verdict {known[key]["verdict"]} needs a relevance entry')
    for key, n in counts.items():
        if key not in known:
            find(f'item/{key[0]}/{key[1]}', 'unknown', 'item is not in prefilter.json')
        if n > 1:
            find(f'item/{key[0]}/{key[1]}', 'duplicate', f'{n} entries for one item')
    final = runview.final_verdicts(prefilter, relevance)
    categories = runview.item_categories(relevance)
    for key in counts:                    # items without an entry are reported as missing above
        if key[0] == 'korea_kr' and final.get(key) == 'include' and not (categories.get(key) or '').strip():
            find(f'item/{key[0]}/{key[1]}', 'category', 'included press release needs a category label')


def main(args) -> int:
    if not args.check and not args.compile:
        raise cli.UsageError('relevance needs --check or --compile FILE '
                             '(Claude writes work/relevance.json or a brief file; this command checks it)')
    rd, manifest = gates.load_run(args.run)
    if args.compile:
        try:
            brief = schema.load(args.compile, 'fiw.relevance_brief/1')
        except OSError as e:
            raise cli.UsageError(f'cannot read {args.compile}: {e}')
        except schema.SchemaError as e:
            raise cli.UsageError(f'{args.compile}: {e}' + (f' — {e.errors[0]}' if e.errors else ''))
        reviewed = [c['cid'] for c in gates.load_work(rd, 'clusters', 'fiw.clusters/1')['clusters']
                    if c.get('auto') is None]
        required = required_keys(gates.load_work(rd, 'prefilter', 'fiw.prefilter/1'))
        compiled = compile_brief(brief, reviewed, required)
        schema.dump(rd / 'work' / 'relevance.json', compiled, 'fiw.relevance/1')
    clusters = gates.load_work(rd, 'clusters', 'fiw.clusters/1')
    relevance = gates.load_work(rd, 'relevance', 'fiw.relevance/1')
    prefilter = gates.load_work(rd, 'prefilter', 'fiw.prefilter/1')
    issues = load_issues()

    findings = []

    def find(where, kind, detail):
        findings.append({'where': where, 'kind': kind, 'detail': detail})

    check_clusters(rd, clusters['clusters'], relevance['clusters'], issues, find)
    check_items(prefilter, relevance, find)

    checked = len(relevance['clusters']) + len(relevance['items'])
    code = cli.EXIT_FAIL if findings else cli.EXIT_OK
    classified = {}
    for f in findings:
        classified[f['kind']] = classified.get(f['kind'], 0) + 1
    gate = gates.write_gate(rd, manifest, 'relevance', code, checked, findings, classified=classified,
                            input_hash=gates.file_hash(rd / 'work' / 'relevance.json'))
    if args.json:
        cli.print_json(gate)
    else:
        gates.print_findings(findings)
        print(f'relevance: {checked} entries, {len(findings)} findings, exit {code}')
    return code
