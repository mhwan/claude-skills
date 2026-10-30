#!/usr/bin/env python3
"""
daily-news-brief — scannable briefing renderer

Implements references/rendering-rules.md. It owns only the deterministic part;
summary text and tone judgments are left to the LLM (that boundary is the core of this file's design).

  render.py prepare {date}   <run folder>/work/{date}.clusters.json → work/{date}.todo.json
                             (a work order: what needs a summary, plus the evidence bodies picked out)
  render.py build {date}     work/{date}.summary.json + todo → <output root>/reports/daily-news-brief/{date}.md
  Both commands take --run <run folder> (default: the most recent run folder for that date).

Why two steps:
  1. Redoing only the summary must be possible. If rendering were one piece with it,
     everything would have to be rebuilt every time.
  2. Checking the summary's factuality by machine needs a **file to check**.
     verify.py compares summary.json against the original text in clusters.
     Without it, an invented summary would pass every format lint.
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

import common

# references/rendering-rules.md §4 — exactly 5 categories, fixed order
CATEGORIES = ['정치', '경제', '사회', '국제', '문화']
CAT_ID = {'정치': 'p', '경제': 'e', '사회': 's', '국제': 'i', '문화': 'c'}
CAT_ICON = {'정치': '🏛️', '경제': '💹', '사회': '👥', '국제': '🌍', '문화': '🎬'}

# Fold the source categories into the 5. Sports is not carried.
CAT_FOLD = {'지역': '사회', 'IT_과학': '경제', '미분류': None, '스포츠': None}

OUTLET_SHORT = {
    '조선일보': '조선', '중앙일보': '중앙', '동아일보': '동아',
    '한겨레': '한겨레', '경향신문': '경향', '매일경제': '매경', '한국경제': '한경',
}
ALL_OUTLETS = list(OUTLET_SHORT)

ROWS_PER_CATEGORY = 8      # §2-1 row cap per category
SCORE_CUTOFF = 20          # §2-1 rows below this score go to the "others" line
LEAD_MIN_SCORE = 40        # §1-5 minimum score for the 3rd place of the "today's three lines" (the lead lines)
SCHEDULE_WINDOW = 14       # §2-4 D+0 ~ D+14
SCHEDULE_ROWS = 16


# --------------------------------------------------------------------------
# Score (§1-2). An internal sort key that is never shown on the page.
# --------------------------------------------------------------------------
def score_row(row):
    outlets = set()
    S = E = C = 0
    L = 0
    X = False
    for c in row['clusters']:
        outlets.update(c['outlets'])
        S += c['size']
        E += c.get('editorial_count', 0)
        C += c.get('column_count', 0)
        for a in c['articles']:
            L = max(L, a.get('body_len', 0))
            if re.match(r'^\s*\[\s*단독\s*\]', a['title']):
                X = True
    O = len(outlets)
    P = 45 * math.log2(1 + O) / 3
    F = 10 * min(1, max(0, S - O) / 3)
    N = min(20, 10 * E + 4 * C)
    D = 15 * min(1, L / 2500)
    B = 10 if X else 0
    return {
        'score': round(P + F + N + D + B),
        'O': O, 'S': S, 'E': E, 'C': C, 'L': L, 'X': X,
        'outlets': sorted(outlets),
    }


def fold_category(cat):
    """Fold into the 5 categories. None means the row is not carried."""
    if cat in CATEGORIES:
        return cat
    return CAT_FOLD.get(cat, None)


def make_tags(m):
    """Tags next to the issue name, in a fixed order (§2-1)."""
    tags = []
    if m['S'] > m['O']:
        tags.append(f"{m['O']}사{m['S']}건")
    else:
        tags.append(f"{m['O']}사")
    if m['E']:
        tags.append(f"사설{m['E']}")
    if m['C']:
        tags.append(f"칼럼{m['C']}")
    if m['X']:
        tags.append('단독')
    return tags


def build_rows(data):
    """Turn clusters into issue rows. For now 1 cluster = 1 row (merging is the unpromoted P-1).

    merge_candidates are only shown, never merged automatically — the sample is still just two days.
    """
    rows = []
    for i, c in enumerate(data['clusters']):
        cat = fold_category(c.get('category'))
        if cat is None:
            continue
        row = {'clusters': [c], 'category': cat, 'idx': i,
               'orig_category': c.get('category')}
        row['metrics'] = score_row(row)
        row['moved'] = c.get('category') != cat        # for the ⟳ manual-correction mark
        rows.append(row)
    rows.sort(key=lambda r: (-r['metrics']['score'],
                             -r['metrics']['O'], -r['metrics']['L']))
    return rows


def sort_articles(articles):
    """Straight news first, then by outlet name within each group.

    The first article becomes the issue row's representative, so the order decides the issue name.
    Putting an opinion piece first makes **the issue name an editorial headline**,
    e.g. "[editorial] severe discipline for blocking a non-mainstream candidate's run ..." (observed). A factual report must represent the matter.
    """
    return sorted(articles, key=lambda a: (bool(a.get('opinion_kind')), a['outlet']))


def representative(row):
    return sort_articles(row['clusters'][0]['articles'])[0]


def is_opinion_only(row):
    """A row made up only of editorials and columns. §2-1 — it goes in the tone table, not the issue table."""
    arts = [a for c in row['clusters'] for a in c['articles']]
    return arts and all(a.get('opinion_kind') for a in arts)


# --------------------------------------------------------------------------
# prepare — the work order
# --------------------------------------------------------------------------
def prepare(date, run):
    src = run / 'work' / f'{date}.clusters.json'
    with open(src, encoding='utf-8') as f:
        data = json.load(f)
    raw_path = run / 'raw' / f'{date}.json'
    with open(raw_path, encoding='utf-8') as f:
        raw = json.load(f)
    # A pruned raw has no bodies. If we did not stop here, the work order would come out with every
    # body an empty string, and the evidence gate would judge every summary as invented — a hard cause to find.
    if raw.get('pruned'):
        print(f'정리된 수집 결과다: {raw_path}\n'
              f'  {raw.get("pruned_at")} 에 본문이 폐기됐다.\n'
              f'  이 날짜를 다시 만들려면 collect.js 부터 다시 돌릴 것.', file=sys.stderr)
        sys.exit(2)
    bodies = {a['news_id']: a.get('body', '') for a in raw['articles']}

    rows = build_rows(data)

    todo = {'target_date': date, 'categories': {}, 'lead': [], 'schedule': []}

    # Apply the cap and cutoff per category
    per_cat = {c: [] for c in CATEGORIES}
    for r in rows:
        per_cat[r['category']].append(r)

    for cat in CATEGORIES:
        picked, rest = [], []
        for r in per_cat[cat]:
            if is_opinion_only(r):
                continue                       # handled in the tone table
            if len(picked) < ROWS_PER_CATEGORY and r['metrics']['score'] >= SCORE_CUTOFF:
                picked.append(r)
            else:
                rest.append(r)
        todo['categories'][cat] = {
            'issues': [{
                'ref': f"{CAT_ID[cat]}{n + 1}",
                'tags': make_tags(r['metrics']),
                'score': r['metrics']['score'],     # internal; not used in the output
                'articles': [{
                    'outlet': a['outlet'], 'title': a['title'], 'url': a['url'],
                    'opinion_kind': a.get('opinion_kind', ''),
                    'body': bodies.get(a['news_id'], '')[:1800],
                } for a in sort_articles(r['clusters'][0]['articles'])],
                'keywords': r['clusters'][0].get('keywords', [])[:6],
                'moved_from': r['orig_category'] if r['moved'] else None,
            } for n, r in enumerate(picked)],
            'rest_count': len(rest),
            'rest_titles': [representative(x)['title'] for x in rest[:12]],
            'opinions': collect_opinions(per_cat[cat], bodies),
        }

    # "Today's three lines" (§1-5) — at most one per category.
    # Each lead must record which issue it came from (ref).
    # Without it the evidence gate would check against the whole day's bodies,
    # and a number found by chance in an article of another category would pass.
    ref_of = {}
    for cat, v in todo['categories'].items():
        for iss in v['issues']:
            ref_of[iss['articles'][0]['url']] = iss['ref']
    used = set()
    for r in rows:
        if len(todo['lead']) >= 3:
            break
        if r['category'] in used or is_opinion_only(r):
            continue
        if r['metrics']['score'] < LEAD_MIN_SCORE and len(todo['lead']) >= 1:
            break
        rep = representative(r)
        ref = ref_of.get(rep['url'])
        if ref is None:
            continue                      # a row missing from the issue table is not used as a lead
        used.add(r['category'])
        todo['lead'].append({
            'category': r['category'], 'ref': ref,
            'title': rep['title'], 'score': r['metrics']['score'],
        })

    todo['schedule'] = gather_schedule(data, date)
    todo['meta'] = {
        'collected_at': data.get('collected_at'),
        'outlet_stats': data.get('outlet_stats', {}),
        'gates': data.get('gates', []),
        'article_count': data.get('article_count'),
        'cluster_count': data.get('cluster_count'),
        'noise_count': data.get('noise_count'),
    }

    dest = run / 'work' / f'{date}.todo.json'
    common.write_json(dest, todo)
    common.write_run_info(run, date, status='prepared')

    n_iss = sum(len(v['issues']) for v in todo['categories'].values())
    n_op = sum(len(v['opinions']) for v in todo['categories'].values())
    print(f'# 작업지시서 — {date}')
    print(f'이슈 {n_iss}행 · 의견 기사 {n_op}건 · 일정 {len(todo["schedule"])}건 · 리드 {len(todo["lead"])}줄')
    print()
    print('| 카테고리 | 이슈 | 그 밖 | 사설 | 칼럼 |')
    print('|---|--:|--:|--:|--:|')
    for cat in CATEGORIES:
        v = todo['categories'][cat]
        ed = sum(1 for o in v['opinions'] if o['kind'] == 'editorial')
        co = sum(1 for o in v['opinions'] if o['kind'] == 'column')
        print(f'| {cat} | {len(v["issues"])} | {v["rest_count"]} | {ed} | {co} |')
    print(f'\n저장: {dest}')
    return 0


def collect_opinions(rows, bodies):
    """Every editorial and column in a category. The material for the tone table."""
    out = []
    for r in rows:
        for c in r['clusters']:
            for a in c['articles']:
                if not a.get('opinion_kind'):
                    continue
                out.append({
                    'outlet': a['outlet'], 'title': a['title'], 'url': a['url'],
                    'kind': a['opinion_kind'],
                    'body': bodies.get(a['news_id'], '')[:2500],
                })
    # Editorials first, then by outlet name
    out.sort(key=lambda x: (x['kind'] != 'editorial', x['outlet']))
    return out


def gather_schedule(data, date):
    """§2-4 — the D+0 ~ D+14 window, in ascending date order."""
    base = datetime.strptime(date, '%Y-%m-%d')
    limit = base + timedelta(days=SCHEDULE_WINDOW)
    seen, items = set(), []
    for c in data['clusters']:
        cat = fold_category(c.get('category'))
        for s in c.get('schedule', []):
            d = datetime.strptime(s['date'], '%Y-%m-%d')
            if d > limit or s['date'] in seen:
                continue
            seen.add(s['date'])
            items.append({**s, 'category': cat or '기타',
                          'imminent': (d - base).days <= 7})
    items.sort(key=lambda x: (x['date'],
                              CATEGORIES.index(x['category'])
                              if x['category'] in CATEGORIES else 9))
    return items[:SCHEDULE_ROWS]


# --------------------------------------------------------------------------
# build — assemble the markdown (§2·§3)
# --------------------------------------------------------------------------
TONE_ICON = {'찬성': '🟢', '조건부': '🟡', '우려': '🟠', '비판': '🔴', '유보': '⚪'}


def clean_url(u):
    """§3-3 URL hygiene — strip BigKinds tracking parameters."""
    return re.sub(r'[?&]utm_[^&]*', '', u or '').rstrip('?&')


class LinkBook:
    """§3-2 reference links. The body uses only [Chosun][p9]; URLs are gathered at the end of the document.

    Left inline, URLs made up 43% of all characters in the 2026-08-21 document
    and stretched a table row to 525 characters. That collides head-on with the scannable design.
    """

    def __init__(self):
        self.by_url = {}
        self.order = []

    def ref(self, cat, url):
        url = clean_url(url)
        if url in self.by_url:
            return self.by_url[url]
        n = sum(1 for k in self.order if k[0] == CAT_ID[cat]) + 1
        rid = f'{CAT_ID[cat]}{n}'
        self.by_url[url] = rid
        self.order.append((CAT_ID[cat], rid, url))
        return rid

    def block(self):
        out = ['<!-- 링크 정의 — 본문의 [매체][id] 가 여기를 가리킨다 -->']
        for _c, rid, url in self.order:
            out.append(f'[{rid}]: {url}')
        return '\n'.join(out)


def keyword_candidates(article, keywords):
    """Keyword candidates in priority order. §3-1 — 2 to 4 characters."""
    title = article['title']
    out = []
    if re.match(r'^\s*\[\s*단독', title):
        out.append('단독')
    if article.get('opinion_kind') == 'editorial':
        out.append('사설')
    elif article.get('opinion_kind') == 'column':
        out.append('칼럼')
    for k in keywords:
        base = k.split('^')[0].strip()
        if 2 <= len(base) <= 4 and base in title:
            out.append(base)
    # Proper-noun candidates from the title — strip the bracketed tag, then take from the front
    stripped = re.sub(r'^\s*\[[^\]]*\]\s*', '', title)
    out.extend(re.findall(r'[가-힣]{2,4}', stripped)[:6])
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def assign_labels(articles, keywords):
    """Decide all link labels in a row at once.

    Deciding one at a time lets two articles from the same outlet pick the same keyword,
    giving "[Hankyoreh·Jang Dong-hyeok] [Hankyoreh·Jang Dong-hyeok]" where nothing distinguishes them (observed).
    When a label collides, move on to the next candidate. What §3-1 asks for is
    a 'scannable label', not just any keyword attached.
    """
    by_outlet = {}
    for a in articles:
        by_outlet.setdefault(a['outlet'], []).append(a)

    labels, used = {}, set()
    for outlet, group in by_outlet.items():
        short = OUTLET_SHORT.get(outlet, outlet)
        if len(group) == 1:
            labels[id(group[0])] = short
            used.add(short)
            continue
        for a in group:
            for kw in keyword_candidates(a, keywords):
                cand = f'{short}·{kw}'
                if cand not in used:
                    labels[id(a)] = cand
                    used.add(cand)
                    break
            else:
                n = sum(1 for u in used if u.startswith(short)) + 1
                labels[id(a)] = f'{short}{n}'
                used.add(labels[id(a)])
    return labels


GATE_VERDICT = {'PASS': '정상', 'WARN': '주의', 'FAIL': '실패'}
ALL_OUTLETS_LABEL = '조선·중앙·동아·한겨레·경향·매경·한경'
BODY_LINE_PREFIX = '- 기사 본문은 이 보고서에 옮기지 않았습니다.'


def header_lines(date, meta, run, todo, summ):
    """The common five-line header (approved sample: see output/samples/).

    The last line is written before prune.py has run, so it says the body files WILL be deleted;
    prune.py rewrites only that line afterwards (see BODY_LINE_PREFIX)."""
    # The collection verdict is the worst of the gates normalize.py recorded.
    states = [g.get('status') for g in meta.get('gates', [])]
    worst = 'FAIL' if 'FAIL' in states else 'WARN' if 'WARN' in states else 'PASS' if states else None
    verdict = GATE_VERDICT.get(worst, '확인 필요')
    got = sum(1 for s in meta.get('outlet_stats', {}).values() if s.get('fetched', 1) > 0)

    import verify
    checked, findings = verify.collect_findings(todo, summ)
    if findings:
        check_line = f'요약 {checked}건을 원문과 대조했고, {len(findings)}건에서 문제가 나왔습니다.'
    else:
        check_line = f'요약 {checked}건을 원문과 대조했고, {checked}건 모두 통과했습니다.'

    n_bodies = len(common.body_files(run, date))
    return [
        f'- 스킬: {common.SKILL} {common.VERSION} · 만든 시각: {common.dates.now_iso()}',
        f'- 요청: 7개 일간지({ALL_OUTLETS_LABEL}) {date}자',
        f'- 자료원: 빅카인즈 — {verdict} (수집 {got}개사)',
        f'- 요약 검사: {check_line}',
        f'{BODY_LINE_PREFIX} 작업용 본문 {n_bodies}개 파일은 보고서를 만든 뒤 지울 예정입니다.',
    ]


def build(date, run):
    todo_p = run / 'work' / f'{date}.todo.json'
    summ_p = run / 'work' / f'{date}.summary.json'
    with open(todo_p, encoding='utf-8') as f:
        todo = json.load(f)
    if not os.path.exists(summ_p):
        print(f'요약 파일이 없다: {summ_p}\n'
              f'render.py prepare 로 만든 작업지시서를 채운 뒤 다시 실행할 것.',
              file=sys.stderr)
        return 2
    with open(summ_p, encoding='utf-8') as f:
        summ = json.load(f)

    lb = LinkBook()
    L = []
    meta = todo['meta']
    d = datetime.strptime(date, '%Y-%m-%d')
    dow = '월화수목금토일'[d.weekday()]

    # --- header -------------------------------------------------------
    L.append(f'# 조간 브리핑 — {date}({dow})')
    L.append('')
    L.extend(header_lines(date, meta, run, todo, summ))
    L.append('')
    capped = [n for n, s in meta['outlet_stats'].items() if s.get('capped')]
    total = meta['article_count']
    L.append(f'수집 {total}건 · {len(meta["outlet_stats"])}개사 · '
             f'클러스터 {meta["cluster_count"]}개 · 노이즈 제외 {meta["noise_count"]}건')
    if capped:
        L.append(f'> ⚠️ 매체당 상한 적용 — {", ".join(OUTLET_SHORT.get(c, c) for c in capped)}. '
                 f'그날 발행분 전체가 아니다.')
    L.append('> 논조 아이콘은 **같은 축 안에서만** 비교 가능하다. '
             '축이 다르면 색이 같아도 같은 입장이 아니다.')
    L.append('')

    # --- "Today's three lines" (the lead lines) --------------------------------
    lead = summ.get('lead') or []
    if lead:
        head = {1: '오늘 한 줄', 2: '오늘 두 줄', 3: '오늘 세 줄'}[min(3, len(lead))]
        L.append(f'## {head}')
        L.append('')
        for item in lead[:3]:
            L.append(f'- {item.get("text", "")}')
        L.append('')
        L.append('> 보도 규모와 논평 여부로 추린 것. 카테고리당 1개.')
        L.append('')

    # --- categories ---------------------------------------------------
    for cat in CATEGORIES:
        v = todo['categories'][cat]
        issues = v['issues']
        tone_blocks = (summ.get('tone') or {}).get(cat, [])
        if not issues and not tone_blocks:
            continue
        L.append('---')
        L.append('')
        L.append(f'# {CAT_ICON[cat]} {cat}')
        L.append('')

        # ① issues
        L.append('## ① 이슈')
        L.append('')
        L.append('| 이슈 | 한 줄 | 링크 |')
        L.append('|---|---|---|')
        moved = []
        for iss in issues:
            s = (summ.get('issues') or {}).get(iss['ref'], {})
            name = s.get('name') or iss['articles'][0]['title'][:34]
            tags = ' '.join(f'`{t}`' for t in iss['tags'])
            arts = iss['articles'][:8]
            labels = assign_labels(arts, iss.get('keywords', []))
            links = ' '.join(f'[{labels[id(a)]}][{lb.ref(cat, a["url"])}]'
                             for a in arts)
            mark = ' ⟳' if iss.get('moved_from') else ''
            if iss.get('moved_from'):
                moved.append(f'{name}({iss["moved_from"]}→{cat})')
            L.append(f'| **{name}**{mark} {tags} | {s.get("oneline", "")} | {links} |')
        if v['rest_count']:
            L.append('')
            L.append(f'> **그 밖** {v["rest_count"]}건 — '
                     + ' / '.join(t[:26] for t in v['rest_titles'][:6]))
        L.append('')

        # ② tone
        L.append('## ② 논조')
        L.append('')
        present = {o['outlet'] for o in v['opinions']}
        if not tone_blocks:
            # 🔴 Never write "there are no editorials or columns". Columns may exist while no
            # issue was set up, and then that sentence **becomes a lie** (observed: on 8/22 economy and
            # culture each had 6 columns, yet the report said there were none).
            eds = [o for o in v['opinions'] if o['kind'] == 'editorial']
            cols = [o for o in v['opinions'] if o['kind'] == 'column']
            if not eds and not cols:
                L.append('> 🕳️ **공백** — 이 분야 사설·칼럼이 표본에 없다.')
            elif not eds:
                L.append(f'> 🕳️ **공백** — 이 분야 **사설**이 표본에 없다. '
                         f'칼럼 {len(cols)}건은 개별 필자 의견이라 매체 논조로 읽지 않는다: '
                         + ' · '.join(f'{OUTLET_SHORT.get(o["outlet"], o["outlet"])} '
                                      f'「{o["title"][:24]}」' for o in cols[:4]))
            else:
                L.append(f'> 🕳️ **공백** — 사설 {len(eds)}건·칼럼 {len(cols)}건이 있으나 '
                         f'쟁점으로 묶이지 않았다.')
            L.append(missing_line(present))
            L.append('')
        for n, b in enumerate(tone_blocks, 1):
            L.append(f'**쟁점 {n} · {b.get("issue", "")}** — '
                     f'**축**: {b.get("axis", "")} · **대상**: {b.get("target", "")}')
            L.append('')
            L.append('| 매체 | 유형 | 입장 | 핵심 논거 | 링크 |')
            L.append('|---|---|:--:|---|---|')
            for r in b.get('rows', []):
                kind = r.get('kind', '')
                kindtxt = '**사설**' if kind == 'editorial' else (
                    '칼럼' if kind == 'column' else kind)
                icon = TONE_ICON.get(r.get('stance', ''), '⚪')
                url = r.get('url', '')
                ref = f'[{OUTLET_SHORT.get(r.get("outlet"), r.get("outlet"))}]' \
                      f'[{lb.ref(cat, url)}]' if url else ''
                L.append(f'| {OUTLET_SHORT.get(r.get("outlet"), r.get("outlet"))} '
                         f'| {kindtxt} | {icon} {r.get("stance", "")} '
                         f'| {r.get("argument", "")} | {ref} |')
            L.append('')
            for key, mark in (('conflict', '⚔️ **충돌점**'), ('agree', '🤝 **합의점**'),
                              ('gap', '🕳️ **공백**'), ('unopposed', '🚫 **반론 없음**')):
                L.append(f'> {mark} {b.get(key) or "해당 없음"}')
            L.append('')
            L.append(missing_line({r.get('outlet') for r in b.get('rows', [])}))
            L.append('')
        if moved:
            L.append(f'> ⟳ 수동 교정: {" · ".join(moved)}')
            L.append('')

    # --- upcoming schedule --------------------------------------------
    if todo['schedule']:
        L.append('---')
        L.append('')
        L.append('# 📅 다가오는 일정')
        L.append('')
        L.append('| 날짜 | 분야 | 일정 | 출처 |')
        L.append('|---|---|---|---|')
        sched_notes = {s.get('date'): s.get('text', '')
                       for s in (summ.get('schedule') or [])}
        for s in todo['schedule']:
            mark = '🔴 ' if s['imminent'] else ''
            cat = s['category'] if s['category'] in CATEGORIES else '정치'
            what = sched_notes.get(s['date']) or s['quote'][:40]
            src = f'[{OUTLET_SHORT.get(s["outlet"], s["outlet"])}][{lb.ref(cat, s["url"])}]' \
                  if s.get('url') else ''
            L.append(f'| {mark}{s["date"][5:]} | {s["category"]} | {what} | {src} |')
        L.append('')

    # --- tone scale (§2-3, carried every time) --------------------------
    L.append('---')
    L.append('')
    L.append('🟢 적극 찬성 · 🟡 조건부 · 🟠 우려 · 🔴 비판 · ⚪ 유보')
    L.append('')
    L.append('> 요약은 직접 작성한 것이며 원문 전재가 아니다. '
             '인용부호 안만 원문 그대로다. 논조 해석은 판단이 섞인 값이다.')
    L.append('> 출처: 한국언론진흥재단 빅카인즈(bigkinds.or.kr). 각 기사 링크는 해당 언론사 원문.')
    L.append('')
    L.append(lb.block())

    dest = common.report_file(date)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = '\n'.join(L) + '\n'
    with open(dest, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    common.write_run_info(run, date, status='built', report=dest.name)

    plain = re.sub(r'https?://\S+', '', text)
    plain = re.sub(r'[|*#>`\[\]()]', '', plain)
    print(f'# 브리핑 생성 — {date}')
    print(f'{len(text)}자 (순수 텍스트 {len(re.sub(r"[ \\t]+", " ", plain))}자) · '
          f'링크 {len(lb.order)}개 · 저장 `{dest}`')
    return 0


def missing_line(present):
    """§2-2 — always name the outlets that did not comment on this issue.

    Count **per issue**. Counting per category would make every outlet with even one editorial
    in that category 'captured', so the line would always read "all 7 outlets captured" (observed).
    Then the line would tell the reader nothing.

    Never write 'not covered' (mibodo) — the outlet may simply have missed the sample because of the per-outlet cap.
    """
    miss = [OUTLET_SHORT[o] for o in ALL_OUTLETS if o not in present]
    if not miss:
        return '> ⊘ 7개사 전부 이 쟁점에 논평했다.'
    return (f'> ⊘ **표본 내 미포착** {"·".join(miss)} — '
            f'표본에 안 잡혔을 뿐, 그 매체가 다루지 않았다는 뜻은 아니다.')


def main():
    argv = sys.argv[1:]
    flag_date, run_arg = common.parse_run_flags(argv)
    if len(argv) < 1 or (len(argv) < 2 and not flag_date):
        print('usage: render.py {prepare|build} YYYY-MM-DD [--run <실행 폴더>]', file=sys.stderr)
        return 2
    cmd = argv[0]
    date = common.check_date(argv[1] if len(argv) > 1 else flag_date)
    if cmd not in ('prepare', 'build'):
        print(f'unknown command: {cmd}', file=sys.stderr)
        return 2
    run = common.resolve_run(date, run_arg)
    return prepare(date, run) if cmd == 'prepare' else build(date, run)


if __name__ == '__main__':
    common.setup_output()
    try:
        sys.exit(main())
    except common.UsageError as e:
        sys.exit(common.fail(str(e), 2))
