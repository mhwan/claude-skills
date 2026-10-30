#!/usr/bin/env python3
"""
daily-news-brief — normalize the collected articles and run the collection gates

Input: the raw JSON that collect.js exported (the state object as it is)
Output:
  <output root>/runs/daily-news-brief/{date}-N/raw/{date}.json   normalized result (creates a new run folder)
  stdout                                                         verification report (markdown)
Stale-comparison history: <output root>/state/daily-news-brief/{d}.json (the body-free summary prune.py leaves behind)

Exit code: 0 = PASS/WARN, 1 = FAIL (the collection cannot be trusted)

Design principle: this file exists to stop a silent collection failure.
A briefing can look plausible even when only half the articles were collected, so a FAIL is raised.
Any truncation must be stated openly.
"""
import json
import re
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import common

OUTLETS = ['조선일보', '중앙일보', '동아일보', '한겨레', '경향신문', '매일경제', '한국경제']

# Gate thresholds
MIN_COVERAGE_RATIO = 0.8      # lower bound for the share of outlets collected successfully
MIN_ARTICLES_PER_OUTLET = 5   # minimum articles per outlet (weekday)
MIN_ARTICLES_WEEKEND = 3      # Saturday and Sunday publish less; the weekday floor would warn every weekend
HISTORY_WINDOW_DAYS = 7       # stale-comparison window. With 3 days the gate goes dark over a long holiday
MIN_FRESHNESS_RATIO = 0.9     # share of articles dated the target day
MIN_BODY_RATIO = 0.9          # share of articles whose body reached 200 characters
BODY_MIN_LEN = 200

def link_status(url):
    """Return 'ok' | 'dead'. Looks only at the shape of the address; it never connects to the site.

    An earlier version sent a HEAD request to the outlet's address. That conflicts with this work folder's rule
    (do not open newspaper sites directly), so the connection was removed. Whether the address is alive is no
    longer checked -- only a malformed address (not http/https, no host, contains whitespace) is caught.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return 'dead'
    ok = parts.scheme in ('http', 'https') and bool(parts.netloc) and not re.search(r'\s', url)
    return 'ok' if ok else 'dead'


def in_run_input(src, target):
    """The run folder when src is <run>/raw/collected.json of a run of this skill for the target date
    (what ingest.py writes); None for any other file."""
    p = Path(src).resolve()
    if p.name != 'collected.json' or p.parent.name != 'raw':
        return None
    run = p.parent.parent
    try:
        if run.parent != common.runs_base().resolve():
            return None
    except OSError:
        return None
    return run if run.name.startswith(target + '-') else None


def load_history(target_date, days=HISTORY_WINDOW_DAYS):
    """Headline sets of the previous N days -- used to detect a stale (cache-stuck) collection

    Returns (found, broken):
      found  = [(date, frozenset of titles), ...]  history that was read successfully
      broken = [date, ...]                         the file exists but could not be read

    "No file" and "unreadable" are told apart. An earlier version dropped both silently,
    so lost history could not be told from a first run.
    """
    found, broken = [], []
    base = datetime.strptime(target_date, '%Y-%m-%d')
    history_dir = common.state_folder()
    for i in range(1, days + 1):
        d = (base - timedelta(days=i)).strftime('%Y-%m-%d')
        p = os.path.join(str(history_dir), f'{d}.json')
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                prev = json.load(f)
            found.append((d, frozenset(a['title'] for a in prev.get('articles', []))))
        except Exception:
            broken.append(d)
    return found, broken


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print('usage: normalize.py <collected.json>', file=sys.stderr)
        return 2
    with open(src, encoding='utf-8') as f:
        state = json.load(f)

    target = state.get('targetDate')
    if not target:
        raise common.UsageError('%s has no targetDate' % src)
    target = common.check_date(target)
    articles = state.get('articles', [])
    outlets = state.get('outlets', {})

    gates = []       # (name, status, detail)  status: PASS | WARN | FAIL
    def gate(name, status, detail):
        gates.append((name, status, detail))

    # --- G1 coverage -------------------------------------------------
    # An outlet whose collection stopped midway (an error is recorded) delivered only part of its articles,
    # so it does not count as a success.
    ok_outlets = [n for n in OUTLETS
                  if outlets.get(n, {}).get('fetched', 0) > 0 and not outlets.get(n, {}).get('error')]
    ratio = len(ok_outlets) / len(OUTLETS)
    missing = [n for n in OUTLETS if n not in ok_outlets]
    gate('커버리지',
         'PASS' if ratio >= MIN_COVERAGE_RATIO else 'FAIL',
         f'{len(ok_outlets)}/{len(OUTLETS)} 매체 수집 성공 ({ratio:.0%})'
         + (f' — 실패: {", ".join(missing)}' if missing else ''))

    # --- G2 minimum articles per outlet ------------------------------
    # The floor is lowered when the target day is a weekend. Saturday and Sunday publish less,
    # so applying the weekday floor would raise a WARN every week even when nothing is wrong.
    weekday = datetime.strptime(target, '%Y-%m-%d').weekday() if target else 0
    is_weekend = weekday >= 5
    floor = MIN_ARTICLES_WEEKEND if is_weekend else MIN_ARTICLES_PER_OUTLET
    daylabel = '월화수목금토일'[weekday] if target else '?'
    thin = [f'{n}({outlets.get(n, {}).get("fetched", 0)})'
            for n in OUTLETS if outlets.get(n, {}).get('fetched', 0) < floor]
    gate('매체별 최소 건수',
         'PASS' if not thin else 'WARN',
         f'하한 {floor}건({daylabel}요일 · {"주말" if is_weekend else "평일"} 기준)'
         + (f' — 미달: {", ".join(thin)}' if thin else ' — 전 매체 충족'))

    # --- G3 freshness ------------------------------------------------
    tgt_compact = target.replace('-', '') if target else ''
    fresh = sum(1 for a in articles if a.get('published_date') == tgt_compact)
    fr = fresh / len(articles) if articles else 0
    gate('신선도',
         'PASS' if fr >= MIN_FRESHNESS_RATIO else 'FAIL',
         f'{fresh}/{len(articles)}건이 대상일({target}) 기사 ({fr:.0%})')

    # --- G4 stale (cache stuck) --------------------------------------
    # This gate is the cheapest way to catch a silent breakage, so it must not
    # switch itself off silently. With no history to compare it reports WARN, not PASS
    # -- the days right after a holiday are exactly when a stuck cache is most likely.
    today_set = frozenset(a['title'] for a in articles)
    found, broken = load_history(target)
    identical = [d for d, s in found if s and s == today_set]
    near = [d for d, s in found
            if s and s != today_set and today_set
            and len(s & today_set) / len(s | today_set) >= 0.9]

    win = f'직전 {HISTORY_WINDOW_DAYS}일 창'
    brokennote = f' · 읽기 실패 {len(broken)}일({", ".join(broken)})' if broken else ''

    if not found:
        g4 = ('WARN', f'{win}에서 비교 가능한 이력 0일 — 스테일 탐지가 작동하지 않았다{brokennote}')
    elif len(identical) >= 2:
        g4 = ('FAIL', f'{win} 중 {len(found)}일과 비교 — 헤드라인 세트가 '
                      f'{len(identical)}일과 완전 동일({", ".join(identical)}). 캐시 고착 의심{brokennote}')
    elif identical:
        g4 = ('WARN', f'{win} 중 {len(found)}일과 비교 — {identical[0]} 과 헤드라인 세트 완전 동일{brokennote}')
    elif near:
        g4 = ('WARN', f'{win} 중 {len(found)}일과 비교 — {", ".join(near)} 와 90% 이상 중복{brokennote}')
    else:
        g4 = ('PASS', f'{win} 중 {len(found)}일과 비교({", ".join(d for d, _ in found)}) — 동일 세트 없음{brokennote}')
    gate('스테일 탐지', g4[0], g4[1])

    # --- G5 link validity --------------------------------------------
    # Take at least one article per outlet. With 3 random articles, one outlet's links could break
    # entirely without the sample catching it, which is exactly the failure this gate must catch.
    # The site is never contacted (link_status looks only at the address shape).
    import random
    bysrc = {}
    for a in articles:
        if a.get('url'):
            bysrc.setdefault(a['outlet'], []).append(a)
    sample = [random.choice(v) for v in bysrc.values()]
    tally = {'ok': 0, 'dead': 0}
    bad = []
    for a in sample:
        st = link_status(a['url'])
        tally[st] += 1
        if st == 'dead':
            bad.append(a['outlet'])
    detail = (f'표본 {len(sample)}건(매체당 1건) 주소 형식 확인(사이트 접속 없음) — 정상 {tally["ok"]}'
              + (f' · 형식 오류 {tally["dead"]}: {", ".join(bad)}' if bad else ''))
    gate('링크 유효성', 'WARN' if bad else 'PASS', detail)

    # --- G6 body coverage --------------------------------------------
    # This gate asks whether detailView delivers the body, i.e. it detects a technical collection failure.
    # But obituaries and photo captions are short by nature; mixed in, they raise a FAIL on a healthy collection.
    # Measured (2026-08-22): 20 of the 24 articles under 200 characters were photo captions of one
    # Kyunghyang musical-competition piece. So "0 characters (a real failure)" and
    # "short but present (short by nature)" are kept apart.
    empty = [a for a in articles if a.get('body_len', 0) == 0]
    short = [a for a in articles if 0 < a.get('body_len', 0) < BODY_MIN_LEN]
    n = len(articles) or 1
    empty_ratio = len(empty) / n
    okr = 1 - empty_ratio
    detail = (f'본문 있음 {n - len(empty)}/{n} ({okr:.0%})'
              f' · 0자 {len(empty)}건 · {BODY_MIN_LEN}자 미만 {len(short)}건')
    if short:
        bycount = {}
        for a in short:
            bycount[a['outlet']] = bycount.get(a['outlet'], 0) + 1
        detail += ' (' + ', '.join(f'{k} {v}' for k, v in sorted(
            bycount.items(), key=lambda x: -x[1])) + ')'
    gate('본문 확보율',
         'FAIL' if okr < MIN_BODY_RATIO else ('WARN' if empty else 'PASS'),
         detail)

    # --- G7 truncation notice (no silent truncation) ------------------
    capped = {n: (outlets[n]['totalCount'], outlets[n]['fetched'])
              for n in OUTLETS if outlets.get(n, {}).get('capped')}
    gate('절삭 고지',
         'WARN' if capped else 'PASS',
         '상한 적용: ' + ', '.join(f'{n} {f}/{t}건' for n, (t, f) in capped.items())
         if capped else '절삭 없음')

    # --- save the normalized result ------------------------------------
    # If ingest.py already created the run folder and put the file in raw/collected.json, use that folder.
    # For any other path (a hand-made file), create a new run folder.
    run = in_run_input(src, target)
    intake = run is not None
    if run is None:
        run = common.new_run(target)
    # The gate verdicts travel with the output. The renderer needs these figures for the header's
    # coverage and truncation notice; without them the briefing step would recompute them,
    # and the report and the briefing could then say different things.
    out = {
        'target_date': target,
        'collected_at': state.get('finishedAt'),
        'source': 'bigkinds',
        'outlet_stats': {n: outlets.get(n, {}) for n in OUTLETS},
        'article_count': len(articles),
        'gates': [{'name': n, 'status': s, 'detail': d} for n, s, d in gates],
        'articles': articles,
    }
    dest = run / 'raw' / f'{target}.json'
    common.write_json(dest, out)
    if intake:
        # The fetched original (collected.json) is fully contained in the raw/{date}.json just written. Keep a single body copy.
        Path(src).unlink()
    worst ='FAIL' if any(s == 'FAIL' for _, s, _ in gates) else \
            ('WARN' if any(s == 'WARN' for _, s, _ in gates) else 'PASS')
    common.write_run_info(run, target, status='collected', collect_verdict=worst)

    # --- report --------------------------------------------------------
    icon = {'PASS': '✅', 'WARN': '⚠️', 'FAIL': '❌'}

    print(f'# 수집 리포트 — {target}')
    print(f'\n**종합 판정: {icon[worst]} {worst}** · 총 {len(articles)}건 · 저장 `{dest}`\n')
    print(f'실행 폴더: `{run}`  (다음 단계 명령에 `--run` 으로 넘기지 않으면 {target} 의 가장 최근 실행 폴더를 쓴다)\n')

    print('## 검증 게이트\n')
    print('| 게이트 | 판정 | 내용 |')
    print('|---|---|---|')
    for name, status, detail in gates:
        print(f'| {name} | {icon[status]} {status} | {detail} |')

    print('\n## 매체별 수집 현황 (발행량 비율 그대로 표기)\n')
    total_all = sum(outlets.get(n, {}).get('totalCount', 0) for n in OUTLETS) or 1
    print('| 매체 | 당일 발행 | 본문 수집 | 발행 비중 |')
    print('|---|---|---|---|')
    for n in OUTLETS:
        o = outlets.get(n, {})
        tc, fc = o.get('totalCount', 0), o.get('fetched', 0)
        print(f'| {n} | {tc} | {fc} | {tc / total_all:.1%} |')
    print(f'\n> 발행량 격차는 보정하지 않고 그대로 노출한다. 비중이 큰 매체가 브리핑을 지배할 수 있으므로 요약 단계에서 이 표를 함께 제시할 것.')

    if state.get('errors'):
        print(f'\n## 오류 {len(state["errors"])}건\n')
        for e in state['errors'][:10]:
            print(f'- {e}')

    return 1 if worst == 'FAIL' else 0


if __name__ == '__main__':
    common.setup_output()
    try:
        sys.exit(main())
    except common.UsageError as e:
        sys.exit(common.fail(str(e), 2))
