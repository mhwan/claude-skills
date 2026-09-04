#!/usr/bin/env python3
"""
daily-news-brief — 수집 결과 정규화 + 검증 게이트

입력: collect.js 가 회수한 원본 JSON (state 객체 그대로)
출력:
  ~/.claude/news-briefs/raw/{date}.json   정규화 결과
  stdout                                   검증 리포트 (마크다운)

종료코드: 0 = PASS/WARN, 1 = FAIL (수집이 신뢰 불가)

설계 원칙: 조용한 수집 실패를 막는 것이 이 파일의 존재 이유다.
브리핑이 그럴듯해 보여도 수집이 반쪽이면 FAIL 을 띄운다. 절삭은 반드시 명시한다.
"""
import json
import sys
import os
import glob
import ssl
import urllib.request
from datetime import datetime, timedelta

RAW_DIR = os.path.expanduser('~/.claude/news-briefs/raw')
OUTLETS = ['조선일보', '중앙일보', '동아일보', '한겨레', '경향신문', '매일경제', '한국경제']

# 게이트 임계값
MIN_COVERAGE_RATIO = 0.8      # 성공 매체 비율 하한
MIN_ARTICLES_PER_OUTLET = 5   # 매체당 최소 건수 (평일)
MIN_ARTICLES_WEEKEND = 3      # 토·일은 발행량 자체가 적다. 평일 하한을 그대로 쓰면 상시 WARN 이 된다
HISTORY_WINDOW_DAYS = 7       # 스테일 비교 창. 3일이면 연휴 공백에 게이트가 꺼진다
MIN_FRESHNESS_RATIO = 0.9     # 대상일 기사 비율
MIN_BODY_RATIO = 0.9          # 본문 200자 이상 확보 비율
BODY_MIN_LEN = 200

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# 정직한 UA 를 쓴다. 브라우저인 척하지 않는다.
#
# 이전 구현은 'Mozilla/5.0' 으로 위장했는데, 이 프로젝트가 처음부터 세운
# "UA 위장으로 우회하지 않는다"는 원칙을 내 코드가 스스로 어기고 있었다.
# 게다가 실측상 위장이 손해였다 — 매일경제는 'Mozilla/5.0' 에 403 을 주고
# 정직한 UA 에 200 을 준다(뻔한 위장 UA 를 거르는 것으로 보인다).
USER_AGENT = 'daily-news-brief/1.0 (personal news brief; link check only)'


def link_status(url):
    """'ok' | 'blocked' | 'dead' | 'error' 를 돌려준다.

    차단(403/429)과 무효(404/410)를 구분한다. 차단은 링크가 죽은 게 아니라
    매체가 자동 접근을 거부한 것이므로, 사용자가 브라우저로 열면 정상이다.
    이걸 '무효'로 뭉뚱그리면 멀쩡한 기사를 브리핑에서 떨어뜨리게 된다.
    """
    try:
        req = urllib.request.Request(url, method='HEAD',
                                     headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=15, context=_ctx) as r:
            return 'ok' if 200 <= r.status < 400 else 'dead'
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return 'blocked'
        if e.code in (404, 410):
            return 'dead'
        return 'error'
    except Exception:
        return 'error'


def load_history(target_date, days=HISTORY_WINDOW_DAYS):
    """직전 N일 헤드라인 세트 — 스테일(캐시 고착) 탐지용

    반환: (found, broken)
      found  = [(날짜, 제목 frozenset), ...]  읽는 데 성공한 이력
      broken = [날짜, ...]                    파일은 있으나 읽기 실패

    '파일 없음'과 '읽기 실패'를 구분한다. 예전 구현은 둘 다 조용히 버려서
    이력 유실을 최초 실행과 구별할 수 없었다.
    """
    found, broken = [], []
    base = datetime.strptime(target_date, '%Y-%m-%d')
    for i in range(1, days + 1):
        d = (base - timedelta(days=i)).strftime('%Y-%m-%d')
        p = os.path.join(RAW_DIR, f'{d}.json')
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
    articles = state.get('articles', [])
    outlets = state.get('outlets', {})

    gates = []       # (이름, 상태, 설명)  상태: PASS | WARN | FAIL
    def gate(name, status, detail):
        gates.append((name, status, detail))

    # --- G1 커버리지 -------------------------------------------------
    ok_outlets = [n for n in OUTLETS if outlets.get(n, {}).get('fetched', 0) > 0]
    ratio = len(ok_outlets) / len(OUTLETS)
    missing = [n for n in OUTLETS if n not in ok_outlets]
    gate('커버리지',
         'PASS' if ratio >= MIN_COVERAGE_RATIO else 'FAIL',
         f'{len(ok_outlets)}/{len(OUTLETS)} 매체 수집 성공 ({ratio:.0%})'
         + (f' — 실패: {", ".join(missing)}' if missing else ''))

    # --- G2 매체별 최소 건수 -----------------------------------------
    # 대상일이 주말이면 하한을 낮춘다. 토·일은 발행량 자체가 적어
    # 평일 하한을 그대로 적용하면 정상 상황에서도 매주 WARN 이 뜬다.
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

    # --- G3 신선도 ---------------------------------------------------
    tgt_compact = target.replace('-', '') if target else ''
    fresh = sum(1 for a in articles if a.get('published_date') == tgt_compact)
    fr = fresh / len(articles) if articles else 0
    gate('신선도',
         'PASS' if fr >= MIN_FRESHNESS_RATIO else 'FAIL',
         f'{fresh}/{len(articles)}건이 대상일({target}) 기사 ({fr:.0%})')

    # --- G4 스테일(캐시 고착) ----------------------------------------
    # 이 게이트는 조용한 고장을 잡는 가장 싼 수단이다. 따라서 스스로 조용히
    # 꺼지면 안 된다. 비교 이력이 없으면 PASS 가 아니라 WARN 으로 보고한다
    # — 연휴 직후가 캐시 고착이 가장 의심스러운 시점이기 때문이다.
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

    # --- G5 링크 유효성 ----------------------------------------------
    # 매체별로 최소 1건씩 뽑는다. 무작위 3건이면 한 매체의 링크가 통째로
    # 깨져도 표본에 안 걸릴 수 있는데, 그게 정확히 이 게이트가 잡아야 할 사고다.
    import random
    bysrc = {}
    for a in articles:
        if a.get('url'):
            bysrc.setdefault(a['outlet'], []).append(a)
    sample = [random.choice(v) for v in bysrc.values()]
    tally = {'ok': 0, 'blocked': 0, 'dead': 0, 'error': 0}
    bad = []
    for a in sample:
        st = link_status(a['url'])
        tally[st] += 1
        if st in ('dead', 'error'):
            bad.append(f'{a["outlet"]}({st})')
    detail = (f'표본 {len(sample)}건(매체당 1건) — 정상 {tally["ok"]}'
              + (f' · 자동접근 차단 {tally["blocked"]}' if tally['blocked'] else '')
              + (f' · 무효 {tally["dead"] + tally["error"]}: {", ".join(bad)}' if bad else ''))
    gate('링크 유효성', 'WARN' if bad else 'PASS', detail)

    # --- G6 본문 확보율 ----------------------------------------------
    # 이 게이트의 목적은 detailView 가 본문을 주는지 = 수집 기술 실패 탐지다.
    # 그런데 부음·사진 캡션처럼 원래 짧은 기사가 섞이면 정상 수집인데도 FAIL 이 뜬다.
    # 실측(2026-08-22): 200자 미만 24건 중 20건이 경향신문 뮤지컬 콩쿠르 사진 캡션이었다.
    # 그래서 '본문 0자(진짜 실패)' 와 '짧지만 존재(원래 그런 기사)' 를 분리한다.
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

    # --- G7 절삭 고지 (무언의 절삭 금지) ------------------------------
    capped = {n: (outlets[n]['totalCount'], outlets[n]['fetched'])
              for n in OUTLETS if outlets.get(n, {}).get('capped')}
    gate('절삭 고지',
         'WARN' if capped else 'PASS',
         '상한 적용: ' + ', '.join(f'{n} {f}/{t}건' for n, (t, f) in capped.items())
         if capped else '절삭 없음')

    # --- 정규화 저장 --------------------------------------------------
    os.makedirs(RAW_DIR, exist_ok=True)
    # 게이트 판정을 산출물에 실어 보낸다. 렌더러가 헤더의 커버리지·절삭 고지를
    # 만들려면 이 수치가 필요한데, 없으면 브리핑 단계에서 다시 계산하게 되고
    # 그러면 리포트와 브리핑이 서로 다른 말을 할 수 있다.
    out = {
        'target_date': target,
        'collected_at': state.get('finishedAt'),
        'source': 'bigkinds',
        'outlet_stats': {n: outlets.get(n, {}) for n in OUTLETS},
        'article_count': len(articles),
        'gates': [{'name': n, 'status': s, 'detail': d} for n, s, d in gates],
        'articles': articles,
    }
    dest = os.path.join(RAW_DIR, f'{target}.json')
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # --- 리포트 -------------------------------------------------------
    worst = 'FAIL' if any(s == 'FAIL' for _, s, _ in gates) else \
            ('WARN' if any(s == 'WARN' for _, s, _ in gates) else 'PASS')
    icon = {'PASS': '✅', 'WARN': '⚠️', 'FAIL': '❌'}

    print(f'# 수집 리포트 — {target}')
    print(f'\n**종합 판정: {icon[worst]} {worst}** · 총 {len(articles)}건 · 저장 `{dest}`\n')

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
    sys.exit(main())
