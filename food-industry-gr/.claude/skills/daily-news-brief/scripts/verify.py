#!/usr/bin/env python3
"""
daily-news-brief — evidence gate + format lint

  verify.py {date}            check the summary (<run folder>/work/{date}.summary.json) against the article text
  verify.py {date} --lint     also run the format lint on the report
                              (<output root>/reports/daily-news-brief/{date}.md)
  --run <run folder>          defaults to the latest run folder of that date
  The result is also written to work/{date}.verify.json (no article text). The "summary check"
  line of the report header is built from the same check.

## Why this file exists

The 10 lint items of `references/rendering-rules.md` §8 are **all format checks**:
reference links, URLs, row width, length, structure, banned words, alignment.
**There is no factuality check at all.**

Without this gate, **a one-line summary can invent things the article never said and
still pass.** A tidy table with links that return 200 is a pass. The original plan named
"hallucinated summaries" as risk number 2, and nothing in the pipeline guarded against it.

## What is checked

Only failures that actually happened are checked (the errors made in the v1 briefing):

  number       is "110 jo" (110 trillion) in the article text?   — invented amounts, ratios, counts
  date         is "Dec 3" in the article text?  — invented schedules
  quote        is the quoted phrase verbatim?       — invented statements
  attribution  is the outlet in "Joongang Ilbo reported ..." right? — v1 credited a Hyundai strike to Hankyung

The article text checked against is limited to **the articles of that issue**. Accepting
something found by chance in another issue's text would make the gate meaningless.
"""
import json
import os
import re
import sys
import unicodedata

import common

OUTLET_ALIASES = {
    '조선': '조선일보', '조선일보': '조선일보',
    '중앙': '중앙일보', '중앙일보': '중앙일보',
    '동아': '동아일보', '동아일보': '동아일보',
    '한겨레': '한겨레',
    '경향': '경향신문', '경향신문': '경향신문',
    '매경': '매일경제', '매일경제': '매일경제',
    '한경': '한국경제', '한국경제': '한국경제',
}

# Numbers excluded from the check — "3 outlets", "4 strands", "13 items" — values the summariser counted
# itself. Must match even with a particle attached ("13-geon-euro" was missed and caused a
# false positive).
COUNT_CONTEXT = re.compile(r'(\d+)\s*(?:개)?\s*(사|개사|곳|갈래|건|매체)(?![0-9])')
NUM_PAT = re.compile(r'\d[\d,]*(?:\.\d+)?')
DATE_PAT = re.compile(r'(1[0-2]|[1-9])\s*월\s*(3[01]|[12]\d|[1-9])\s*일')
QUOTE_PAT = re.compile(r'[“"]([^”"]{4,60})[”"]')


def clean_url(u):
    """§3-3 — strip BigKinds tracking parameters. Must follow the same rule as render.py."""
    return re.sub(r'[?&]utm_[^&]*', '', u or '').rstrip('?&')


def norm(s):
    """Normalise for comparison — remove whitespace, commas and Unicode variant forms."""
    s = unicodedata.normalize('NFKC', s or '')
    return re.sub(r'[\s,]', '', s)


def number_tokens(text):
    """Numbers as a set of tokens. Commas are removed; leading zeros are kept."""
    return {m.group(0).replace(',', '') for m in NUM_PAT.finditer(norm(text))}


def check_text(text, bodies, titles):
    """Check one summary text against the evidence. Returns the list of violations."""
    hay = norm(' '.join(bodies) + ' ' + ' '.join(titles))
    bad = []

    # Counting expressions such as "3 outlets", "4 strands" are values the summariser made up
    # itself, so they are not checked.
    counted = {m.group(1) for m in COUNT_CONTEXT.finditer(text)}

    # 🔴 Numbers must not be looked up as substrings.
    # "150 jo" (150 trillion) was found inside "1500 eok" and "2150" of the body and passed (measured).
    # Compare token sets instead — "110 jo" vs the body's "90 jo~110 jo-won" both give
    # the token '110', so it passes correctly.
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


# Particles / separators that may follow an outlet abbreviation. Needed to catch "Maekyung-i bodohan" (Maekyung reported).
PARTICLE = r'(?:은|는|이|가|을|를|의|도|만|과|와|에|에서|으로|로|처럼|보다|만이|까지)?'
# Other words that start with an outlet abbreviation. Without this exclusion "jungang-eunhaeng" (central bank)
# would be read as Joongang Ilbo.
NOT_OUTLET = re.compile(
    r'^(중앙(은행|정부|회|당|선관위|박물관|대|지법|지검|아시아)'
    r'|동아(시아|일렉|에스티|제약|대)'
    r'|조선(족|업|소|시대|왕조|호텔|해양|대)'
    r'|한경(대|협)'
    r'|경향(성|적)'
    r'|매경(닷컴)?이코노미)')


def check_attribution(text, outlets):
    """Is each outlet named in the summary text really an outlet of that issue?

    The v1 briefing credited a Hyundai strike article to Hankyung and the Samsung DX union
    to Hankyoreh. This is the **link is right but the text is wrong** type, so a link
    check cannot catch it.

    Korean makes it tricky in two directions:
      particles attach     "Maekyung-i", "Hankyoreh-ga" -> blocking any following Hangul misses them
      compound words exist "jungang-eunhaeng", "dong-asia", "joseon-eop" -> allowing particles gives false positives
    So particles are allowed and the compound words are excluded explicitly.
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
    """Check the claims of the 4 blocks against the table itself.

    A sentence such as "Chosun, Dong-A and Joongang wrote no editorial on this matter" can be judged
    true or false **by looking at the table.** A comparison with the article text cannot
    catch it, but a self-contradiction can be caught — saying an outlet that is in the
    table "did not write" is false.

    Measured: "Maekyung wrote no editorial on this matter" was put into an issue where Maekyung
    has a row, and it passed with 0 violations.
    """
    rows = block.get('rows', [])
    present_full = {r.get('outlet') for r in rows}
    present = {OUTLET_ALIASES.get(o, o) for o in present_full}
    bad = []

    # ① Did the text say an outlet that is in the table is absent?
    #
    # **Require** a subject/topic particle (eun/neun/do/i/ga). Otherwise
    # "there is no rebuttal to the Chosun editorial in the sample" is flagged — there Chosun is not the
    # subject of the absence but the target of the rebuttal (a measured false positive).
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
                # Does an absence statement follow, with that outlet as its subject?
                if ABSENT.search(text[m.end():m.end() + 40]):
                    bad.append(('모순', f'{field}: {alias} 는 이 쟁점 표에 있는데 '
                                        f'논평하지 않았다고 서술했다'))
                    break

    # ② Does the number in "all three outlets ..." match the number of table rows?
    for field in ('conflict', 'agree', 'gap', 'unopposed'):
        text = block.get(field) or ''
        for m in COUNT_CLAIM.finditer(text):
            raw, unit = m.group(1), m.group(2)
            n = COUNT_WORD.get(raw) or (int(raw) if raw.isdigit() else None)
            if n is None or unit not in ('매체', '곳', '사'):
                continue
            # Only when 'jeonbu/modu' (all) is attached must it equal the row count.
            # It may also point at a subset, as in "two outlets pointed at different spots".
            tail = text[m.end():m.end() + 8]
            if not re.search(r'모두|전부|다', tail) and '모두' not in text[:m.start()]:
                continue
            if n != len(rows):
                bad.append(('모순', f'{field}: 「{m.group(0)} 모두」인데 '
                                    f'표에는 {len(rows)}행'))
    return [(where, k, v) for k, v in bad]


def collect_findings(todo, summ):
    """Check every summary text against the article bodies in the work order.
    Returns (number of texts checked, findings [(where, kind, what)]). Prints nothing, writes nothing:
    render.py uses it for the report header, verify() for the gate output."""
    findings = []
    checked = 0

    # One-line summary of each issue
    by_ref = {}
    for cat, v in todo['categories'].items():
        for iss in v['issues']:
            by_ref[iss['ref']] = iss
    # 🔴 An issue with no summary at all must not pass as a blank.
    # The renderer prints an empty string as is — the table looks fine but only the
    # The "one line" cell is empty. The issue is on the table with no description written,
    # which is unfinished, not normal.
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

    # Tone — each outlet's argument
    #
    # Find the article text by **the article URL the row points at**, not by the category
    # pool. Opinion-article category misclassification still exists (BigKinds files a
    # "Future Response Fund" editorial under politics), so looking it up by category would
    # flag a sound argument as unsupported.
    # An argument must rest on the very piece that outlet wrote — the URL gives that
    # pairing exactly. URL comparison also follows the §3-3 hygiene: if the URL with
    # BigKinds tracking parameters and the stripped one counted as different URLs, a sound
    # argument would become 'no evidence'.
    # 🔴 A category that has editorials but set up no issue must not be skipped.
    # Tone comparison is a requirement of this briefing, so material that exists but was
    # not used is unfinished.
    # A category with columns only passes — a column is not the outlet's official position;
    # it is a different level.
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

            # 🔴 The narrative fields are checked too.
            # It used to look only at rows[].argument. But the 4 blocks and axis/target carry
            # **substantive claims** such as "all three outlets ..." and "Chosun, Dong-A and Joongang wrote no
            # editorial" and went through no check at all.
            # Even "reported exclusively that it was worth 9999 trillion won" got PASS (measured).
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

    # Lead — check only against the articles of the issue that lead points at.
    # Using the whole day as the reference would let a number found by chance in another
    # category's article pass. The gate's purpose is not 'it exists somewhere' but
    # 'it is in the evidence of this issue'.
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

    return checked, findings


FALLBACK_NOTICE = '네이버 대체판 — 본문 미확인'


def fallback_findings(summ, where='summary'):
    """A Naver fallback run has no article text to check a sentence against, so it may hold no
    summary sentence at all: every non-empty string anywhere in summary.json is a finding."""
    found = []
    if isinstance(summ, dict):
        for k, v in summ.items():
            found.extend(fallback_findings(v, f'{where}.{k}'))
    elif isinstance(summ, list):
        for i, v in enumerate(summ):
            found.extend(fallback_findings(v, f'{where}[{i}]'))
    elif isinstance(summ, str) and summ.strip():
        found.append((where, '대체 모드 요약 금지', summ.strip()[:30]))
    return found


def verify_fallback(date, run, summ_p):
    summ = {}
    if os.path.exists(summ_p):
        with open(summ_p, encoding='utf-8') as f:
            summ = json.load(f)
    findings = fallback_findings(summ)
    common.write_json(run / 'work' / f'{date}.verify.json', {
        'checked': 0,
        'source': 'naver',
        'findings': [{'where': w, 'kind': k, 'what': s} for w, k, s in findings],
    })
    print(f'# 근거 게이트 — {date} · {FALLBACK_NOTICE}')
    print(f'\n대체판이라 요약문을 쓰지 않는다 · 요약문 **{len(findings)}건**\n')
    if findings:
        print('| 위치 | 유형 | 내용 |')
        print('|---|---|---|')
        for where, kind, what in findings:
            print(f'| {where} | {kind} | {what} |')
        print('\n**FAIL** — 본문을 읽지 않은 대체판에 요약문이 있다. 요약을 지울 것(제목·매체·링크만 둔다).')
        return 1
    print('**PASS** — 요약문 없음(제목·매체·링크만).')
    return 0


def verify(date, run):
    todo_p = run / 'work' / f'{date}.todo.json'
    summ_p = run / 'work' / f'{date}.summary.json'
    if os.path.exists(todo_p) and (common.read_json(todo_p) or {}).get('source') == 'naver':
        return verify_fallback(date, run, summ_p)
    if not os.path.exists(summ_p):
        print(f'요약 파일 없음: {summ_p}', file=sys.stderr)
        return 2
    with open(todo_p, encoding='utf-8') as f:
        todo = json.load(f)
    with open(summ_p, encoding='utf-8') as f:
        summ = json.load(f)

    checked, findings = collect_findings(todo, summ)
    # No article text in this file: only counts and the positions/kinds of the findings.
    common.write_json(run / 'work' / f'{date}.verify.json', {
        'checked': checked,
        'findings': [{'where': w, 'kind': k, 'what': s} for w, k, s in findings],
    })

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
# Format lint (references/rendering-rules.md §8)
# --------------------------------------------------------------------------
BANNED = ['[보기]', '미보도']
MAX_ROW_CHARS = 300
MAX_PLAIN_CHARS = 10500


def lint(date):
    path = common.report_file(date)
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
        # URLs sit in the definition block, so they do not count toward row length
        if len(ln) > MAX_ROW_CHARS:
            bad.append(('행 폭', f'{n}행 {len(ln)}자 (상한 {MAX_ROW_CHARS})'))

    # Table column counts must agree
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
    if FALLBACK_NOTICE in lines[0]:
        # A Naver fallback report has lists only, and must never name BigKinds as its source.
        for need in ['## 검색별 결과', '## 사설', '## 기사 목록', '출처: 네이버 뉴스 검색 API']:
            if need not in text:
                bad.append(('구조', f'{need} 이(가) 없다'))
        for wrong in ['자료원: 빅카인즈', 'bigkinds.or.kr']:
            if wrong in text:
                bad.append(('자료원', f'대체판에 {wrong} 표기'))
    else:
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
    argv = sys.argv[1:]
    flag_date, run_arg = common.parse_run_flags(argv)
    do_lint = '--lint' in argv
    argv = [a for a in argv if a != '--lint']
    date = argv[0] if argv else flag_date
    if not date:
        print('usage: verify.py YYYY-MM-DD [--lint] [--run <실행 폴더>]', file=sys.stderr)
        return 2
    date = common.check_date(date)
    run = common.resolve_run(date, run_arg)
    rc = verify(date, run)
    if do_lint:
        print()
        rc = lint(date) or rc
    return rc


if __name__ == '__main__':
    common.setup_output()
    try:
        sys.exit(main())
    except common.UsageError as e:
        sys.exit(common.fail(str(e), 2))
