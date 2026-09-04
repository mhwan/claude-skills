#!/usr/bin/env python3
"""
daily-news-brief — 근거 게이트 + 형식 lint

  verify.py {date}            요약(work/{date}.summary.json)을 원문에 대조
  verify.py {date} --lint     생성된 마크다운의 형식 검사까지

## 왜 이 파일이 있나

`references/rendering-rules.md` §8 의 lint 10항목은 **전부 형식 검사**다.
참조 링크·URL·행 폭·분량·구조·금지어·정렬. **사실성 검사가 하나도 없다.**

즉 이 게이트가 없으면 **한 줄 요약이 원문에 없는 말을 지어내도 전부 통과한다.**
표가 예쁘고 링크가 200이면 합격이다. 원래 계획서가 리스크 2번으로 꼽은
"환각 요약"을 막는 장치가 파이프라인 어디에도 없었다.

## 무엇을 검사하나

실제로 일어난 실패만 검사한다(v1 브리핑에서 내가 낸 오류들):

  숫자    「110조」가 원문에 있나          — 지어낸 금액·비율·건수
  날짜    「12월 3일」이 원문에 있나        — 지어낸 일정
  인용    따옴표 안 문구가 원문 그대로인가  — 지어낸 발언
  귀속    「중앙일보가 …」의 매체가 맞나    — v1 에서 현대차 파업을 한경으로 적었다

검사 대상 본문은 **그 이슈에 속한 기사들**로 한정한다. 다른 이슈 본문에서
우연히 발견되는 것을 근거로 인정하면 게이트가 무의미해진다.
"""
import json
import os
import re
import sys
import unicodedata

WORK_DIR = os.path.expanduser('~/.claude/news-briefs/work')
OUT_DIR = os.path.expanduser('~/.claude/news-briefs')

OUTLET_ALIASES = {
    '조선': '조선일보', '조선일보': '조선일보',
    '중앙': '중앙일보', '중앙일보': '중앙일보',
    '동아': '동아일보', '동아일보': '동아일보',
    '한겨레': '한겨레',
    '경향': '경향신문', '경향신문': '경향신문',
    '매경': '매일경제', '매일경제': '매일경제',
    '한경': '한국경제', '한국경제': '한국경제',
}

# 검사에서 빼는 숫자 — 「3사」「4갈래」「13건」 — 요약자가 세어서 쓰는 값. 뒤에 조사가 붙어도 잡아야 한다
# (「13건으로」가 안 걸려서 오탐이 났다).
COUNT_CONTEXT = re.compile(r'(\d+)\s*(?:개)?\s*(사|개사|곳|갈래|건|매체)(?![0-9])')
NUM_PAT = re.compile(r'\d[\d,]*(?:\.\d+)?')
DATE_PAT = re.compile(r'(1[0-2]|[1-9])\s*월\s*(3[01]|[12]\d|[1-9])\s*일')
QUOTE_PAT = re.compile(r'[“"]([^”"]{4,60})[”"]')


def clean_url(u):
    """§3-3 — 빅카인즈 추적 파라미터 제거. render.py 와 같은 규칙이어야 한다."""
    return re.sub(r'[?&]utm_[^&]*', '', u or '').rstrip('?&')


def norm(s):
    """비교용 정규화 — 공백·쉼표·유니코드 형태를 지운다."""
    s = unicodedata.normalize('NFKC', s or '')
    return re.sub(r'[\s,]', '', s)


def number_tokens(text):
    """숫자를 토큰 집합으로. 쉼표는 제거하고 앞자리 0 은 남긴다."""
    return {m.group(0).replace(',', '') for m in NUM_PAT.finditer(norm(text))}


def check_text(text, bodies, titles):
    """요약문 하나를 근거에 대조. 위반 목록을 돌려준다."""
    hay = norm(' '.join(bodies) + ' ' + ' '.join(titles))
    bad = []

    # 「3사」「4갈래」 같은 집계 표현은 요약자가 만든 값이므로 검사 대상이 아니다
    counted = {m.group(1) for m in COUNT_CONTEXT.finditer(text)}

    # 🔴 숫자는 부분 문자열로 찾으면 안 된다.
    # 「150조」가 본문의 「1500억」·「2150」 안에서 발견돼 통과했다(실측).
    # 토큰 집합 비교로 바꾼다 — 「110조」 vs 본문 「90조~110조원」 은
    # 양쪽 다 토큰 '110' 이라 정상 통과한다.
    have_nums = number_tokens(' '.join(bodies) + ' ' + ' '.join(titles))
    for tok in number_tokens(text):
        if tok in counted or tok in have_nums:
            continue
        bad.append(('숫자', tok))

    for m in DATE_PAT.finditer(text):
        if norm(m.group(0)) not in hay:
            bad.append(('날짜', m.group(0)))

    for m in QUOTE_PAT.finditer(text):
        if norm(m.group(1)) not in hay:
            bad.append(('인용', m.group(1)[:36]))

    return bad


# 매체 약칭 뒤에 붙을 수 있는 조사·구분자. 「매경이 보도한」을 잡으려면 필요하다.
PARTICLE = r'(?:은|는|이|가|을|를|의|도|만|과|와|에|에서|으로|로|처럼|보다|만이|까지)?'
# 매체 약칭으로 시작하는 다른 낱말. 이걸 막지 않으면 「중앙은행」이 중앙일보로 잡힌다.
NOT_OUTLET = re.compile(
    r'^(중앙(은행|정부|회|당|선관위|박물관|대|지법|지검|아시아)'
    r'|동아(시아|일렉|에스티|제약|대)'
    r'|조선(족|업|소|시대|왕조|호텔|해양|대)'
    r'|한경(대|협)'
    r'|경향(성|적)'
    r'|매경(닷컴)?이코노미)')


def check_attribution(text, outlets):
    """요약문이 든 매체명이 실제로 그 이슈에 있는 매체인가.

    v1 브리핑에서 현대차 파업 기사를 한경으로, 삼성 DX 노조를 한겨레로 적었다.
    **링크는 맞는데 본문 서술이 틀린** 유형이라 링크 검사로는 안 잡힌다.

    한국어라 두 방향으로 까다롭다:
      조사가 붙는다        「매경이」「한겨레가」 → 뒤에 한글을 무조건 막으면 못 잡는다
      복합어가 있다        「중앙은행」「동아시아」「조선업」 → 조사를 허용하면 오탐이 난다
    그래서 조사는 허용하되 복합어는 명시적으로 뺀다.
    """
    have = {OUTLET_ALIASES.get(o, o) for o in outlets}
    bad, seen = [], set()
    for alias, full in OUTLET_ALIASES.items():
        if full in have or full in seen:
            continue
        for m in re.finditer(rf'(?<![가-힣]){re.escape(alias)}{PARTICLE}(?![가-힣])', text):
            tail = text[m.start():m.start() + 12]
            if NOT_OUTLET.match(tail):
                continue
            seen.add(full)
            bad.append(('귀속', f'{alias} — 이 이슈에 없는 매체'))
            break
    return bad


COUNT_WORD = {'한': 1, '하나': 1, '두': 2, '둘': 2, '세': 3, '셋': 3,
              '네': 4, '넷': 4, '다섯': 5, '여섯': 6, '일곱': 7}
COUNT_CLAIM = re.compile(
    r'(한|하나|두|둘|세|셋|네|넷|다섯|여섯|일곱|\d+)\s*(?:개)?\s*(매체|곳|사)')


def check_block_claims(block, where):
    """4블록의 주장을 표 자체와 대조한다.

    「조선·동아·중앙은 이 사안에 사설을 쓰지 않았다」 같은 문장은
    **표를 보면 참·거짓을 알 수 있다.** 원문 대조로는 못 잡지만
    자기모순은 잡을 수 있다 — 표에 있는 매체를 '안 썼다'고 하면 거짓이다.

    실측: 「매경은 이 사안에 사설을 쓰지 않았다」를 매경이 행에 있는 쟁점에
    넣었는데 0건 위반으로 통과했다.
    """
    rows = block.get('rows', [])
    present_full = {r.get('outlet') for r in rows}
    present = {OUTLET_ALIASES.get(o, o) for o in present_full}
    bad = []

    # ① 표에 있는 매체를 '없다'고 하지 않았는가
    #
    # 주격·주제 조사(은/는/도/이/가)를 **요구한다**. 안 그러면
    # 「조선 사설에 대한 반론이 표본 안에 없다」가 걸린다 — 여기서 조선은
    # 부재의 주어가 아니라 반론의 대상이다(실측 오탐).
    ABSENT = re.compile(r'(쓰지\s*않|다루지\s*않|안\s*썼|사설이\s*없|논평(하지\s*않|이\s*없))')
    for field in ('gap', 'unopposed'):
        text = block.get(field) or ''
        for alias, full in OUTLET_ALIASES.items():
            if full not in present:
                continue
            for m in re.finditer(
                    rf'(?<![가-힣]){re.escape(alias)}(?:은|는|도|이|가)(?![가-힣])', text):
                if NOT_OUTLET.match(text[m.start():m.start() + 12]):
                    continue
                # 그 매체를 주어로 삼은 뒤 부재 서술이 이어지는가
                if ABSENT.search(text[m.end():m.end() + 40]):
                    bad.append(('모순', f'{field}: {alias} 는 이 쟁점 표에 있는데 '
                                        f'논평하지 않았다고 서술했다'))
                    break

    # ② 「세 매체 모두 …」의 수가 표의 행 수와 맞는가
    for field in ('conflict', 'agree', 'gap', 'unopposed'):
        text = block.get(field) or ''
        for m in COUNT_CLAIM.finditer(text):
            raw, unit = m.group(1), m.group(2)
            n = COUNT_WORD.get(raw) or (int(raw) if raw.isdigit() else None)
            if n is None or unit not in ('매체', '곳', '사'):
                continue
            # '전부/모두' 가 붙은 경우만 행 수와 같아야 한다.
            # 「두 매체가 다른 지점을 짚었다」처럼 일부를 가리킬 수도 있다.
            tail = text[m.end():m.end() + 8]
            if not re.search(r'모두|전부|다', tail) and '모두' not in text[:m.start()]:
                continue
            if n != len(rows):
                bad.append(('모순', f'{field}: 「{m.group(0)} 모두」인데 '
                                    f'표에는 {len(rows)}행'))
    return [(where, k, v) for k, v in bad]


def verify(date):
    todo_p = os.path.join(WORK_DIR, f'{date}.todo.json')
    summ_p = os.path.join(WORK_DIR, f'{date}.summary.json')
    if not os.path.exists(summ_p):
        print(f'요약 파일 없음: {summ_p}', file=sys.stderr)
        return 2
    with open(todo_p, encoding='utf-8') as f:
        todo = json.load(f)
    with open(summ_p, encoding='utf-8') as f:
        summ = json.load(f)

    findings = []
    checked = 0

    # 이슈 한 줄 요약
    by_ref = {}
    for cat, v in todo['categories'].items():
        for iss in v['issues']:
            by_ref[iss['ref']] = iss
    # 🔴 요약이 아예 없는 이슈를 빈칸으로 통과시키면 안 된다.
    # 렌더러는 빈 문자열을 그냥 찍는다 — 표는 멀쩡해 보이는데 「한 줄」만 비어 있다.
    # 이슈 표에 올려놓고 설명을 안 쓴 것이므로 미완성이지 정상이 아니다.
    for ref, iss in by_ref.items():
        s = (summ.get('issues') or {}).get(ref)
        if not s or not (s.get('oneline') or '').strip():
            findings.append((ref, '누락',
                             f'한 줄 요약 없음 — {iss["articles"][0]["title"][:34]}'))

    for ref, s in (summ.get('issues') or {}).items():
        iss = by_ref.get(ref)
        if iss is None:
            findings.append((ref, '구조', f'작업지시서에 없는 ref: {ref}'))
            continue
        bodies = [a['body'] for a in iss['articles']]
        titles = [a['title'] for a in iss['articles']]
        outs = [a['outlet'] for a in iss['articles']]
        for field in ('name', 'oneline'):
            text = s.get(field, '')
            if not text:
                continue
            checked += 1
            for kind, tok in check_text(text, bodies, titles):
                findings.append((ref, kind, f'{field}: {tok}'))
            for kind, tok in check_attribution(text, outs):
                findings.append((ref, kind, f'{field}: {tok}'))

    # 논조 — 매체별 논거
    #
    # 카테고리 풀이 아니라 **그 행이 가리키는 기사 URL** 로 원문을 찾는다.
    # 의견 기사 카테고리 오분류가 아직 남아 있어서(빅카인즈가 「미래대응기금 사설」을
    # 정치로 분류) 카테고리로 찾으면 멀쩡한 논거가 근거 없음으로 잡힌다.
    # 논거는 그 매체가 쓴 그 글에 근거해야 한다 — URL 이 그 대응을 정확히 준다.
    # URL 비교도 §3-3 위생을 따른다. 빅카인즈 추적 파라미터가 붙은 쪽과
    # 떼어낸 쪽이 서로 다른 URL 로 취급되면 멀쩡한 논거가 '근거 없음'이 된다.
    # 🔴 사설이 있는데 쟁점을 안 세운 카테고리를 그냥 넘기면 안 된다.
    # 논조 비교가 이 브리핑의 요건이므로, 재료가 있는데 안 쓴 것은 미완성이다.
    # 칼럼만 있는 경우는 통과시킨다 — 칼럼은 매체 공식 입장이 아니라 층위가 다르다.
    for cat, v in todo['categories'].items():
        eds = [o for o in v['opinions'] if o['kind'] == 'editorial']
        if eds and not (summ.get('tone') or {}).get(cat):
            findings.append((cat, '누락',
                             f'사설 {len(eds)}건이 있는데 논조 쟁점이 없다: '
                             + ', '.join(o['outlet'] for o in eds[:4])))

    by_url = {}
    for v in todo['categories'].values():
        for o in v['opinions']:
            by_url[clean_url(o['url'])] = o
    for cat, blocks in (summ.get('tone') or {}).items():
        for bi, b in enumerate(blocks, 1):
            block_bodies, block_titles = [], []
            for rowi, r in enumerate(b.get('rows', []), 1):
                where = f'{cat}/쟁점{bi}/{r.get("outlet")}'
                src = by_url.get(clean_url(r.get('url')))
                if src is None:
                    findings.append((where, '구조', '논조 행의 url 이 의견 기사 목록에 없다'))
                    continue
                block_bodies.append(src['body'])
                block_titles.append(src['title'])
                if src['outlet'] != r.get('outlet'):
                    findings.append((where, '귀속',
                                     f'url 의 실제 매체는 {src["outlet"]}'))
                text = r.get('argument', '')
                if not text:
                    continue
                checked += 1
                for kind, tok in check_text(text, [src['body']], [src['title']]):
                    findings.append((where, kind, tok))

            # 🔴 서술 필드도 검사한다.
            # 이전에는 rows[].argument 만 봤다. 그런데 4블록과 축·대상은
            # 「세 매체 모두 …」「조선·동아·중앙은 사설을 쓰지 않았다」처럼
            # **실질 주장**을 담는데 아무 검사도 안 거쳤다.
            # 「9999조원 규모라고 단독 보도했다」를 넣어도 PASS 가 났다(실측).
            where_b = f'{cat}/쟁점{bi}'
            for field in ('issue', 'axis', 'target',
                          'conflict', 'agree', 'gap', 'unopposed'):
                text = (b.get(field) or '').strip()
                if not text:
                    if field in ('issue', 'axis', 'target'):
                        findings.append((where_b, '누락', f'{field} 없음'))
                    continue
                checked += 1
                for kind, tok in check_text(text, block_bodies, block_titles):
                    findings.append((where_b, kind, f'{field}: {tok}'))
            findings.extend(check_block_claims(b, where_b))

    # 리드 — 그 리드가 가리키는 이슈의 기사로만 대조한다.
    # 하루치 전체를 대조 대상으로 삼으면 다른 카테고리 기사에서 우연히
    # 발견된 숫자가 통과한다. 게이트의 목적은 '어딘가에 있음'이 아니라
    # '이 사안의 근거에 있음'이다.
    todo_lead = {l.get('ref'): l for l in todo.get('lead', [])}
    for i, s in enumerate(summ.get('lead') or []):
        text = s.get('text', '')
        if not text:
            continue
        checked += 1
        ref = s.get('ref') or (list(todo_lead)[i] if i < len(todo_lead) else None)
        iss = by_ref.get(ref)
        where = f'리드{i + 1}'
        if iss is None:
            findings.append((where, '구조', 'ref 가 없어 대조 대상을 특정할 수 없다'))
            continue
        for kind, tok in check_text(text, [a['body'] for a in iss['articles']],
                                    [a['title'] for a in iss['articles']]):
            findings.append((where, kind, tok))
        for kind, tok in check_attribution(text, [a['outlet'] for a in iss['articles']]):
            findings.append((where, kind, tok))

    print(f'# 근거 게이트 — {date}')
    print(f'\n검사한 요약문 {checked}개 · 위반 **{len(findings)}건**\n')
    if findings:
        print('| 위치 | 유형 | 내용 |')
        print('|---|---|---|')
        for where, kind, what in findings:
            print(f'| {where} | {kind} | {what} |')
        print('\n**FAIL** — 원문에서 확인되지 않는 표현이 있다. 해당 요약을 고치거나 근거를 확인할 것.')
        return 1
    print('**PASS** — 요약의 숫자·날짜·인용·매체 귀속이 모두 원문에서 확인됨.')
    return 0


# --------------------------------------------------------------------------
# 형식 lint (references/rendering-rules.md §8)
# --------------------------------------------------------------------------
BANNED = ['[보기]', '미보도']
MAX_ROW_CHARS = 300
MAX_PLAIN_CHARS = 10500


def lint(date):
    path = os.path.join(OUT_DIR, f'{date}.md')
    if not os.path.exists(path):
        print(f'브리핑 파일 없음: {path}', file=sys.stderr)
        return 2
    text = open(path, encoding='utf-8').read()
    lines = text.split('\n')
    bad = []

    defs = dict(re.findall(r'^\[([a-z]\d+)\]:\s*(\S+)$', text, re.M))
    used = set(re.findall(r'\]\[([a-z]\d+)\]', text))
    for u in sorted(used - set(defs)):
        bad.append(('참조', f'정의되지 않은 참조 [{u}]'))
    for d in sorted(set(defs) - used):
        bad.append(('참조', f'쓰이지 않은 정의 [{d}]'))

    seen_url = {}
    for rid, url in defs.items():
        if 'utm_' in url:
            bad.append(('URL', f'[{rid}] 추적 파라미터 잔존'))
        if len([p for p in url.split('/')[3:] if p]) < 2:
            bad.append(('URL', f'[{rid}] 홈 주소로 보인다: {url}'))
        if url in seen_url:
            bad.append(('URL', f'[{rid}] 와 [{seen_url[url]}] 이 같은 URL'))
        seen_url[url] = rid

    for n, ln in enumerate(lines, 1):
        if not ln.startswith('|'):
            continue
        # URL 은 정의 블록에 있으므로 행 길이에 안 잡힌다
        if len(ln) > MAX_ROW_CHARS:
            bad.append(('행 폭', f'{n}행 {len(ln)}자 (상한 {MAX_ROW_CHARS})'))

    # 표 열 수 정합
    tbl = []
    for n, ln in enumerate(lines, 1):
        if ln.startswith('|'):
            tbl.append((n, ln.count('|')))
        elif tbl:
            widths = {w for _, w in tbl}
            if len(widths) > 1:
                bad.append(('표', f'{tbl[0][0]}행부터 열 수 불일치 {sorted(widths)}'))
            tbl = []

    for b in BANNED:
        if b in text:
            bad.append(('금지어', b))

    plain = re.sub(r'https?://\S+', '', text)
    plain = re.sub(r'[|*#>`\[\]()]', '', plain)
    plain = re.sub(r'[ \t]+', ' ', plain)
    if len(plain) > MAX_PLAIN_CHARS:
        bad.append(('분량', f'순수 텍스트 {len(plain)}자 (상한 {MAX_PLAIN_CHARS})'))

    for cat in ['정치', '경제', '사회', '국제', '문화']:
        if f'# 🏛️ {cat}' not in text and f'{cat}\n' not in text:
            pass
    for need in ['## ① 이슈', '## ② 논조']:
        if need not in text:
            bad.append(('구조', f'{need} 섹션이 없다'))

    for n, ln in enumerate(lines, 1):
        if ln.startswith('**쟁점') and ('**축**' not in ln or '**대상**' not in ln):
            bad.append(('논조', f'{n}행 쟁점에 축·대상 표기 없음'))

    print(f'# 형식 lint — {date}')
    print(f'\n참조 {len(defs)}정의/{len(used)}사용 · 표 행 {sum(1 for l in lines if l.startswith("|"))} · '
          f'순수 텍스트 {len(plain)}자\n')
    if bad:
        print('| 항목 | 내용 |')
        print('|---|---|')
        for k, v in bad:
            print(f'| {k} | {v} |')
        print(f'\n**FAIL** — {len(bad)}건')
        return 1
    print('**PASS** — 형식 검사 통과.')
    return 0


def main():
    if len(sys.argv) < 2:
        print('usage: verify.py YYYY-MM-DD [--lint]', file=sys.stderr)
        return 2
    date = sys.argv[1]
    rc = verify(date)
    if '--lint' in sys.argv:
        print()
        rc = lint(date) or rc
    return rc


if __name__ == '__main__':
    sys.exit(main())
