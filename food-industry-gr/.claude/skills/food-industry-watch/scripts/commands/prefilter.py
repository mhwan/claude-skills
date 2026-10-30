"""fiw.py prefilter — food-relevance keyword stage for primary-source items (02-plan §4)."""
from __future__ import annotations

from lib import cli, korean, layout, schema

from . import check as check_cmd
from . import collect as collect_cmd

KEYWORD_SOURCES = ('korea_kr',) + check_cmd.NOTICE_SOURCES + ('assembly_bills',)
WHOLE_SOURCES = ('foodsafety',)     # the whole source is food


def register(sub):
    p = sub.add_parser('prefilter', help='keyword food-relevance verdicts for primary-source items')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the fiw.prefilter/1 JSON')
    p.set_defaults(func=main)


# Agency names that contain a food term but say nothing about the item (MFDS titles end with
# '(Ministry of Food and Drug Safety notice no. …)' even for drug notices). They are blanked before matching.
AGENCY_NAMES = ('식품의약품안전처', '농림축산식품부', '농림축산식품해양수산위원회')


def verdict(title, include, exclude) -> tuple:
    """(verdict, rule) for one title."""
    for name in AGENCY_NAMES:
        title = title.replace(name, ' ')
    inc = next((t for t in include if korean.contains_word(title, t, left_guard=True)), None)
    exc = next((t for t in exclude if korean.contains_word(title, t, left_guard=True)), None)
    if inc and not exc:
        return 'include', f'include:{inc}'
    if exc and not inc:
        return 'other', f'exclude:{exc}'
    if inc and exc:
        return 'undecided', f'include:{inc}+exclude:{exc}'
    return 'undecided', 'none'


def item_verdict(source, item, words, committees) -> tuple:
    """(verdict, rule) for one item of a keyword source.

    Bills referred to an untracked committee are 'other' (the bill table lists tracked
    committees only). Items of a `no_match_other` department that match no keyword at all are
    'other' (a non-food ministry: a food item would carry a food word).
    """
    if source == 'assembly_bills':
        committee = item.get('committee')
        if committee and not any(korean.match_entity(committee, c) for c in committees):
            return 'other', f'committee:{committee}'
        title = item['name']
    else:
        title = item['title']
    v, rule = verdict(title, words['include'], words['exclude'])
    dept = item.get('dept')
    if rule == 'none' and dept and dept in (words.get('no_match_other') or ()):
        return 'other', f'no_food_word:{dept}'
    return v, rule


def _view_line(source, key, item, verdict, rule) -> str:
    if source == 'assembly_bills':
        what = (f'{item.get("date")} {item.get("name")} — {item.get("proposer")} · '
                f'{item.get("committee") or "미배정"}')
    elif source == 'korea_kr':
        what = f'{item.get("date")} [{item.get("dept")}] {item.get("title")}'
    else:
        span = f'{item.get("open_from")}~{item.get("open_to")}' if item.get('open_from') else '기간 미확인'
        what = f'{item.get("posted")} [{item.get("dept")}] {item.get("kind")} {item.get("title")} ({span})'
    mark = '' if verdict == 'undecided' else f' ← {verdict} ({rule})'
    return f'- {key} {" ".join(str(what).split())}{mark}'


def view_text(rows) -> str:
    """items_view.md — every item that needs a relevance entry (undecided, or include of a keyword source)."""
    out = ['# 1차 자료 판단 목록 — undecided 전부 + 키워드로 include 된 항목(확인 또는 뒤집기)']
    for source in KEYWORD_SOURCES:
        lines = [r[1] for r in rows if r[0] == source]
        if lines:
            out += ['', f'## {source} · {len(lines)}건'] + lines
    return '\n'.join(out) + '\n'


def main(args) -> int:
    rd, _ = collect_cmd.load_run(args.run)
    watch = collect_cmd.load_watch()
    words, committees = watch['food_related'], watch['committees']
    items, read, view_rows = [], [], []
    for source in KEYWORD_SOURCES + WHOLE_SOURCES:
        path = layout.collect_dir(rd) / f'{source}.json'
        if not path.is_file():
            continue
        env = schema.load(path, 'fiw.collect/1')
        read.append(source)
        for item in env['items']:
            if source in WHOLE_SOURCES:
                items.append({'source': source, 'id': check_cmd.item_key(source, item),
                              'verdict': 'include', 'rule': f'source:{source}'})
                continue
            v, rule = item_verdict(source, item, words, committees)
            key = check_cmd.item_key(source, item)
            items.append({'source': source, 'id': key, 'verdict': v, 'rule': rule})
            if v != 'other':
                view_rows.append((source, _view_line(source, key, item, v, rule)))
    out = {'schema': 'fiw.prefilter/1', 'items': items}
    (rd / 'work').mkdir(exist_ok=True)
    schema.dump(rd / 'work' / 'prefilter.json', out, 'fiw.prefilter/1')
    schema.atomic_write(rd / 'work' / 'items_view.md', view_text(view_rows))
    code = cli.EXIT_OK if read else cli.EXIT_WARN
    if args.json:
        cli.print_json(out)
    else:
        counts = {}
        for it in items:
            counts[it['verdict']] = counts.get(it['verdict'], 0) + 1
        summary = ', '.join(f'{k} {counts.get(k, 0)}' for k in ('include', 'other', 'undecided'))
        print(f'prefilter: {len(items)} items from {", ".join(read) or "no collect files"} — {summary}')
        print(f'prefilter: exit {code}')
    return code
