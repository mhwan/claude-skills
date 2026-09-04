#!/usr/bin/env python3
"""
daily-news-brief — 스캔형 브리핑 렌더러

references/rendering-rules.md 의 구현체다. 결정론 부분만 담당하고,
요약문·논조 판단은 LLM 에게 넘긴다(그 경계가 이 파일의 설계 핵심).

  render.py prepare {date}   clusters/{date}.json → work/{date}.todo.json
                             (요약이 필요한 것 + 근거 본문을 추린 작업지시서)
  render.py build {date}     work/{date}.summary.json + clusters → news-briefs/{date}.md

왜 두 단계로 쪼갰나:
  ① 요약만 다시 할 수 있어야 한다. 렌더와 한 덩어리면 매번 전부 다시 만들어야 한다.
  ② 요약의 사실성을 기계로 검사하려면 **검사할 파일**이 있어야 한다.
     verify.py 가 summary.json 을 clusters 의 원문과 대조한다.
     이게 없으면 지어낸 요약이 형식 lint 를 전부 통과한다.
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

CLUSTER_DIR = os.path.expanduser('~/.claude/news-briefs/clusters')
WORK_DIR = os.path.expanduser('~/.claude/news-briefs/work')
OUT_DIR = os.path.expanduser('~/.claude/news-briefs')

# references/rendering-rules.md §4 — 5개 고정, 순서도 고정
CATEGORIES = ['정치', '경제', '사회', '국제', '문화']
CAT_ID = {'정치': 'p', '경제': 'e', '사회': 's', '국제': 'i', '문화': 'c'}
CAT_ICON = {'정치': '🏛️', '경제': '💹', '사회': '👥', '국제': '🌍', '문화': '🎬'}

# 원 카테고리 → 5개로 접기. 스포츠는 싣지 않는다.
CAT_FOLD = {'지역': '사회', 'IT_과학': '경제', '미분류': None, '스포츠': None}

OUTLET_SHORT = {
    '조선일보': '조선', '중앙일보': '중앙', '동아일보': '동아',
    '한겨레': '한겨레', '경향신문': '경향', '매일경제': '매경', '한국경제': '한경',
}
ALL_OUTLETS = list(OUTLET_SHORT)

ROWS_PER_CATEGORY = 8      # §2-1 카테고리당 행 수 상한
SCORE_CUTOFF = 20          # §2-1 이 점수 미만은 「그 밖」으로
LEAD_MIN_SCORE = 40        # §1-5 「오늘 세 줄」 3위 하한
SCHEDULE_WINDOW = 14       # §2-4 D+0 ~ D+14
SCHEDULE_ROWS = 16


# --------------------------------------------------------------------------
# 점수 (§1-2). 화면에 표시하지 않는 내부 정렬 키다.
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
    """5개 카테고리로 접는다. None 이면 싣지 않는다."""
    if cat in CATEGORIES:
        return cat
    return CAT_FOLD.get(cat, None)


def make_tags(m):
    """이슈 이름 옆 태그. 순서 고정 (§2-1)"""
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
    """클러스터를 이슈 행으로. 지금은 1클러스터 = 1행 (병합은 P-1 미승격).

    merge_candidates 는 표시만 하고 자동 병합하지 않는다 — 표본이 아직 이틀치다.
    """
    rows = []
    for i, c in enumerate(data['clusters']):
        cat = fold_category(c.get('category'))
        if cat is None:
            continue
        row = {'clusters': [c], 'category': cat, 'idx': i,
               'orig_category': c.get('category')}
        row['metrics'] = score_row(row)
        row['moved'] = c.get('category') != cat        # ⟳ 수동 교정 표시용
        rows.append(row)
    rows.sort(key=lambda r: (-r['metrics']['score'],
                             -r['metrics']['O'], -r['metrics']['L']))
    return rows


def sort_articles(articles):
    """스트레이트를 앞에, 그 안에서 매체명 순.

    첫 기사가 곧 이슈 행의 대표가 되므로 순서가 곧 이슈 이름을 정한다.
    의견 기사를 앞에 두면 「[사설]비주류 총선 출마 봉쇄 중징계…」 처럼
    **이슈 이름이 논평 제목이 된다**(실측). 사실 보도가 사안을 대표해야 한다.
    """
    return sorted(articles, key=lambda a: (bool(a.get('opinion_kind')), a['outlet']))


def representative(row):
    return sort_articles(row['clusters'][0]['articles'])[0]


def is_opinion_only(row):
    """사설·칼럼만으로 이뤄진 행. §2-1 — 이슈 표가 아니라 논조 표에 놓는다."""
    arts = [a for c in row['clusters'] for a in c['articles']]
    return arts and all(a.get('opinion_kind') for a in arts)


# --------------------------------------------------------------------------
# prepare — 작업지시서
# --------------------------------------------------------------------------
def prepare(date):
    src = os.path.join(CLUSTER_DIR, f'{date}.json')
    with open(src, encoding='utf-8') as f:
        data = json.load(f)
    raw_path = os.path.expanduser(f'~/.claude/news-briefs/raw/{date}.json')
    with open(raw_path, encoding='utf-8') as f:
        raw = json.load(f)
    # 정리된 raw 는 본문이 없다. 막지 않으면 body 가 전부 빈 문자열인 작업지시서가
    # 나오고, 근거 게이트가 요약 전부를 '지어냄'으로 판정한다. 원인을 찾기 어렵다.
    if raw.get('pruned'):
        print(f'정리된 수집 결과다: {raw_path}\n'
              f'  {raw.get("pruned_at")} 에 본문이 폐기됐다.\n'
              f'  이 날짜를 다시 만들려면 collect.js 부터 다시 돌릴 것.', file=sys.stderr)
        sys.exit(2)
    bodies = {a['news_id']: a.get('body', '') for a in raw['articles']}

    rows = build_rows(data)

    todo = {'target_date': date, 'categories': {}, 'lead': [], 'schedule': []}

    # 카테고리별로 상한·컷오프 적용
    per_cat = {c: [] for c in CATEGORIES}
    for r in rows:
        per_cat[r['category']].append(r)

    for cat in CATEGORIES:
        picked, rest = [], []
        for r in per_cat[cat]:
            if is_opinion_only(r):
                continue                       # 논조 표에서 다룬다
            if len(picked) < ROWS_PER_CATEGORY and r['metrics']['score'] >= SCORE_CUTOFF:
                picked.append(r)
            else:
                rest.append(r)
        todo['categories'][cat] = {
            'issues': [{
                'ref': f"{CAT_ID[cat]}{n + 1}",
                'tags': make_tags(r['metrics']),
                'score': r['metrics']['score'],     # 내부용. 산출물에는 안 쓴다
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

    # 오늘 세 줄 (§1-5) — 카테고리당 최대 1개
    # 리드는 어느 이슈에서 왔는지(ref)를 반드시 남긴다.
    # 없으면 근거 게이트가 하루치 전체 본문을 대조 대상으로 삼게 되고,
    # 그러면 다른 카테고리 기사에서 우연히 발견된 숫자도 통과한다.
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
            continue                      # 이슈 표에 없는 행은 리드로 쓰지 않는다
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

    os.makedirs(WORK_DIR, exist_ok=True)
    dest = os.path.join(WORK_DIR, f'{date}.todo.json')
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(todo, f, ensure_ascii=False, indent=1)

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
    """카테고리 안의 사설·칼럼 전부. 논조 표의 재료."""
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
    # 사설 먼저, 그 안에서 매체명 순
    out.sort(key=lambda x: (x['kind'] != 'editorial', x['outlet']))
    return out


def gather_schedule(data, date):
    """§2-4 — D+0 ~ D+14 창, 날짜 오름차순."""
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
# build — 마크다운 조립 (§2·§3)
# --------------------------------------------------------------------------
TONE_ICON = {'찬성': '🟢', '조건부': '🟡', '우려': '🟠', '비판': '🔴', '유보': '⚪'}


def clean_url(u):
    """§3-3 URL 위생 — 빅카인즈 추적 파라미터 제거"""
    return re.sub(r'[?&]utm_[^&]*', '', u or '').rstrip('?&')


class LinkBook:
    """§3-2 참조 링크. 본문은 [조선][p9] 만 쓰고 URL 은 문서 끝에 모은다.

    인라인으로 두면 2026-08-21 기준 URL 이 문서 전체 문자의 43% 를 차지해
    표 행이 525자까지 늘어난다. 스캔형의 정체성과 정면으로 충돌한다.
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
    """핵심어 후보를 우선순위대로. §3-1 — 2~4자."""
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
    # 제목의 고유명사 후보 — 말머리를 떼고 앞에서부터
    stripped = re.sub(r'^\s*\[[^\]]*\]\s*', '', title)
    out.extend(re.findall(r'[가-힣]{2,4}', stripped)[:6])
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def assign_labels(articles, keywords):
    """행 안의 링크 라벨을 한꺼번에 정한다.

    한 건씩 정하면 같은 매체 두 기사가 같은 핵심어를 골라
    「[한겨레·장동혁] [한겨레·장동혁]」처럼 변별이 사라진다(실측).
    라벨이 겹치면 다음 후보로 넘어간다. §3-1 이 요구하는 것은
    '스캔 가능한 라벨'이지 아무 핵심어나 붙이는 게 아니다.
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


def build(date):
    todo_p = os.path.join(WORK_DIR, f'{date}.todo.json')
    summ_p = os.path.join(WORK_DIR, f'{date}.summary.json')
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

    # --- 헤더 ---------------------------------------------------------
    L.append(f'# 조간 브리핑 — {date}({dow})')
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

    # --- 오늘 세 줄 ---------------------------------------------------
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

    # --- 카테고리 -----------------------------------------------------
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

        # ① 이슈
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

        # ② 논조
        L.append('## ② 논조')
        L.append('')
        present = {o['outlet'] for o in v['opinions']}
        if not tone_blocks:
            # 🔴 "사설·칼럼이 없다"고 쓰면 안 된다. 칼럼은 있는데 쟁점을 안 세운
            # 경우가 있고, 그때 이 문장은 **거짓말이 된다**(실측: 8/22 경제·문화에
            # 칼럼이 6건씩 있는데 '없다'고 나왔다).
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

    # --- 다가오는 일정 -------------------------------------------------
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

    # --- 논조 척도 (§2-3 매번 싣는다) -----------------------------------
    L.append('---')
    L.append('')
    L.append('🟢 적극 찬성 · 🟡 조건부 · 🟠 우려 · 🔴 비판 · ⚪ 유보')
    L.append('')
    L.append('> 요약은 직접 작성한 것이며 원문 전재가 아니다. '
             '인용부호 안만 원문 그대로다. 논조 해석은 판단이 섞인 값이다.')
    L.append('> 출처: 한국언론진흥재단 빅카인즈(bigkinds.or.kr). 각 기사 링크는 해당 언론사 원문.')
    L.append('')
    L.append(lb.block())

    os.makedirs(OUT_DIR, exist_ok=True)
    dest = os.path.join(OUT_DIR, f'{date}.md')
    text = '\n'.join(L) + '\n'
    with open(dest, 'w', encoding='utf-8') as f:
        f.write(text)

    plain = re.sub(r'https?://\S+', '', text)
    plain = re.sub(r'[|*#>`\[\]()]', '', plain)
    print(f'# 브리핑 생성 — {date}')
    print(f'{len(text)}자 (순수 텍스트 {len(re.sub(r"[ \\t]+", " ", plain))}자) · '
          f'링크 {len(lb.order)}개 · 저장 `{dest}`')
    return 0


def missing_line(present):
    """§2-2 — 이 쟁점에 논평하지 않은 매체를 반드시 명시한다.

    **쟁점 단위**로 센다. 카테고리 단위로 세면 그 카테고리에 사설이 하나라도
    있는 매체는 전부 '포착'이 돼서 항상 「7개사 모두 포착」이 나온다(실측).
    그러면 이 줄이 아무것도 알려주지 않는다.

    '미보도'라고 쓰지 않는다 — 매체당 상한 때문에 표본에 안 잡혔을 뿐일 수 있다.
    """
    miss = [OUTLET_SHORT[o] for o in ALL_OUTLETS if o not in present]
    if not miss:
        return '> ⊘ 7개사 전부 이 쟁점에 논평했다.'
    return (f'> ⊘ **표본 내 미포착** {"·".join(miss)} — '
            f'표본에 안 잡혔을 뿐, 그 매체가 다루지 않았다는 뜻은 아니다.')


def main():
    if len(sys.argv) < 3:
        print('usage: render.py {prepare|build} YYYY-MM-DD', file=sys.stderr)
        return 2
    cmd, date = sys.argv[1], sys.argv[2]
    if cmd == 'prepare':
        return prepare(date)
    if cmd == 'build':
        return build(date)
    print(f'unknown command: {cmd}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
