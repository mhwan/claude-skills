#!/usr/bin/env python3
"""
daily-news-brief — 이슈 클러스터링 + 카테고리 분류

입력: ~/.claude/news-briefs/raw/{date}.json  (normalize.py 산출물)
출력: ~/.claude/news-briefs/clusters/{date}.json
      stdout  요약 통계

빅카인즈 TMS_SIMILARITY 는 유사 기사 ID 목록이 아니라 '용어^가중치' 벡터다.
이걸 코사인 유사도로 묶으면 같은 사안을 다룬 타사 기사가 한 클러스터로 모인다.
여러 매체가 동시에 다룬 사안 = 그날의 실제 의제라는 신호이므로,
클러스터 크기와 참여 매체 수가 브리핑 우선순위의 1차 근거가 된다.
"""
import json
import re
import math
import sys
import os
import collections
import difflib
from datetime import datetime

RAW_DIR = os.path.expanduser('~/.claude/news-briefs/raw')
OUT_DIR = os.path.expanduser('~/.claude/news-briefs/clusters')

TOP_CATS = ['정치', '경제', '사회', '국제', '문화', '스포츠', 'IT_과학', '지역']
SIM_THRESHOLD = 0.18   # 실측 튜닝값. 0.16 아래는 절벽(대통령 클러스터 붕괴, 오병합 2건).
                       # 0.22 는 같은 사안을 과분할했다(제주 3클러스터). 0.18 이 과분할 해소 + 오병합 0.
INV_MAX_DF = 40        # 이 이상 흔한 용어는 후보 생성에서 제외(비교량 억제)

# 브리핑 가치가 없는 정형 기사. 매경 [포토] 연속물이 대표 사례.
# <반론보도>·<정정보도>는 꺾쇠, 나머지는 대괄호를 쓴다.
NOISE_PAT = re.compile(
    r'^\s*(?:\[(포토|포토뉴스|화보|부고|부음|인사|동정|알림|표|사진|사진기획'
    r'|모집|공고|기고|카드뉴스|영상|오늘의\s*운세|운세)\b'
    r'|[<〈]\s*(반론보도|정정보도|알려드립니다)\s*[>〉])'
)

# 말머리가 있어도 버리면 안 되는 것. 짧아도 진짜 뉴스다.
# 실측: 「[속보] 이 대통령, 명진스님 입적에…」 는 본문 39자짜리 속보다.
NEWSY_PREFIX_PAT = re.compile(
    r'^\s*\[\s*(속보|단독|긴급|종합|전문|일문일답|인터뷰|현장|르포|팩트체크)\b')

# 짧은 본문 기준. 이 아래면 '기사'가 아니라 캡션·안내에 가깝다.
STUB_BODY_LEN = 200
# 연속물 판정: 같은 매체에서 같은 문구를 공유하는 제목이 이 수 이상이면 정형 시리즈로 본다.
SERIES_MIN_COUNT = 5
SERIES_MIN_SUBSTR = 6      # 공유 문구 최소 길이(자)
SERIES_MAX_MEDIAN_BODY = 300   # 진짜 뉴스 연속 보도를 죽이지 않기 위한 안전장치


def _shared_substrings(title, n):
    """제목에서 길이 n 문자 n-gram 집합"""
    t = re.sub(r'\s+', ' ', title).strip()
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def detect_series(articles):
    """같은 매체가 같은 틀로 대량 발행한 정형 연속물의 news_id 집합을 돌려준다.

    2026-08-22 경향신문이 뮤지컬 콩쿠르 참가자 사진 캡션 20건을 냈는데
    「'고해'를 열창하는 대학·일반부 이무경」처럼 [포토] 말머리가 없어
    말머리 규칙을 통과했고, 그날 최대 클러스터(20건)가 됐다.

    길이만으로는 못 거른다 — 같은 날 매일경제 「[속보] 이 대통령…」 은 본문 39자짜리
    진짜 속보다. 그래서 세 조건을 모두 요구한다:
      ① 같은 매체 ② 제목에 같은 문구를 공유하는 기사가 SERIES_MIN_COUNT 이상
      ③ 그 무리의 본문 길이 중앙값이 SERIES_MAX_MEDIAN_BODY 미만
    ③ 이 진짜 연속 보도(같은 사건을 여러 각도로 쓴 긴 기사들)를 보호한다.
    """
    out = set()
    byoutlet = collections.defaultdict(list)
    for a in articles:
        byoutlet[a.get('outlet', '')].append(a)

    for _outlet, arts in byoutlet.items():
        if len(arts) < SERIES_MIN_COUNT:
            continue
        gram_hits = collections.defaultdict(list)
        for a in arts:
            for g in _shared_substrings(a.get('title', ''), SERIES_MIN_SUBSTR):
                gram_hits[g].append(a)
        for _g, hits in gram_hits.items():
            if len(hits) < SERIES_MIN_COUNT:
                continue
            bodies = sorted(x.get('body_len', 0) for x in hits)
            median = bodies[len(bodies) // 2]
            if median < SERIES_MAX_MEDIAN_BODY:
                out.update(x['news_id'] for x in hits)
    return out


# --- 예정 일정 추출 (P-2) -------------------------------------------------
# 브리핑의 「다가오는 일정」을 본문에서 뽑는다.
#
# 원칙: **문장 밖을 참조해야 풀리는 날짜는 추출하지 않는다.** 틀린 날짜를 내느니 누락한다.
# 브리핑에 "다가오는 일정"으로 올라간 날짜가 사실은 과거 사건이면 신뢰가 무너진다.
#
# 버리는 것 (전부 실측 오답에서 나온 규칙):
#   「같은달 23일」   월을 앞 문장에서 물려받음        → ELIDED_PAT
#   「같은 해 11월 24일 체포됐으며」  연도를 물려받음  → YEAR_ELIDED_PAT
#   「2024년 12월 3일」「지난해 9월25일」  과거인데 올해로 계산됨 → EXPLICIT/LAST_YEAR_PAT
#   「2023년 …, 2025년 …」 한 문장에 연도 둘         → 어느 날짜가 어느 해인지 불명
#   「10월 11일 다시 A씨에게 전화해 … 파악했다」  과거 서술 → has_past_ending()
#
# 살리는 것:
#   「오는 27일」  월이 없지만 '오는' 이 이번 달을 뜻하는 관례가 확고하다 → NEAR_TERM_PAT
#   「오는 10월23일 … 예고했다」  과거 종결이지만 미래 표지가 있다 → FUTURE_MARK_PAT
SENT_SPLIT = re.compile(r'(?<=[.!?。])\s+|\n+')
MONTH_DAY_PAT = re.compile(r'(?<![\d])(1[0-2]|[1-9])\s*월\s*(3[01]|[12]\d|[1-9])\s*일')
MONTH_ONLY_PAT = re.compile(r'(?<![\d])(1[0-2]|[1-9])\s*월(?!\s*\d*\s*일)')
DAY_ONLY_PAT = re.compile(r'(?<![\d])(3[01]|[12]\d|[1-9])\s*일(?!\s*간|\s*째|\s*자|\s*분|\s*동안)')
# 월 생략 신호 — 이런 표현이 있으면 그 문장의 '○일'은 신뢰하지 않는다
ELIDED_PAT = re.compile(r'(같은\s*달|이달|지난달|다음\s*달|당월|해당\s*월)')
NEXT_YEAR_PAT = re.compile(r'내년|이듬해')
# 문장에 연도가 명시돼 있으면 그걸 쓴다. 이게 없으면 과거 사건이 미래 일정으로 올라간다 —
# 실측 오답: 「2024년 12월 3일 …」, 「지난해 9월25일 세상을 떠난 …」, 「2023년 8월3~24일 통화」.
EXPLICIT_YEAR_PAT = re.compile(r'((?:19|20)\d{2})\s*년')
LAST_YEAR_PAT = re.compile(r'지난해|작년|전년')
# 연도를 문장 밖에서 물려받는 표현. 월 생략과 같은 이유로 해소가 불가능하다.
# 실측 오답: 「법원 기록에 따르면 그는 같은 해 11월 24일 체포됐으며 …」
YEAR_ELIDED_PAT = re.compile(r'같은\s*해|당해|그해|해당\s*연도')
# 월 없이 쓰는 근일 표현의 신호. '오는' 이 있으면 이번 달을 뜻하는 관례가 확고하다.
#
# 🔴 앞에 한글이 오면 안 된다. 「카카오는 21일 이사회를 열고…」 에서
# '카카|오는' 으로 잘려 매치돼, 과거 사실인 8월 21일 이사회가 9월 21일 일정이 됐다.
# 「나오는」·「보이는」·「들어오는」 등 같은 함정이 무수히 많다.
NEAR_TERM_PAT = re.compile(
    r'(?<![가-힣])(오는|내달|다음\s*달|다음\s*주)\s*(3[01]|[12]\d|[1-9])\s*일')
# '내달' 은 날짜 크기와 무관하게 무조건 다음 달이다
NEXT_MONTH_WORDS = ('내달', '다음달', '다음 달')
# 과거 서술. 이것만으로 버리면 안 된다 —
# 「오는 10월23일 전후로 선고기일을 정하겠다고 예고했다」 는 과거 종결이지만 미래 일정이다.
# 그래서 미래 표지가 없을 때만 버린다.
#
# 🔴 음절을 나열하면 안 된다. 한국어 과거형은 어간에 따라 축약형이 제각각이라
# 「했·였·됐」만 적으면 「밝혔다」(밝히+었→혔)·「켰다」·「폈다」·「쳤다」를 통째로 놓친다.
# 실측: 「… 결의했다고 밝혔다.」가 과거 서술로 잡히지 않았다.
#
# 공통점은 **받침이 ㅆ** 이라는 것 하나뿐이다. 완성형 한글은
#   코드 = 0xAC00 + (초성×21 + 중성)×28 + 종성
# 이므로 (코드 - 0xAC00) % 28 == 20 이면 종성이 ㅆ이다. 이걸 직접 판정한다.
PAST_TAIL_PAT = re.compile(r'([가-힣])(다|다고|다는|으며|며|고|지만|던|음)(?![가-힣])')


def _has_ss_final(ch):
    """받침이 ㅆ인 완성형 한글인지"""
    c = ord(ch)
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 == 20


# 받침이 ㅆ이지만 과거형이 아닌 것. 「쉴 수 있다」가 과거 서술로 잡혀
# 멀쩡한 미래 일정이 버려졌다. 「있었다」는 앞 글자가 '었'이라 그대로 잡힌다.
NOT_PAST_STEMS = frozenset(['있', '없'])


def has_past_ending(sent):
    return any(_has_ss_final(m.group(1)) and m.group(1) not in NOT_PAST_STEMS
               for m in PAST_TAIL_PAT.finditer(sent))


# '오는' 은 NEAR_TERM_PAT 과 같은 이유로 앞 한글을 막는다(「카카오는」 함정).
FUTURE_MARK_PAT = re.compile(
    r'(?<![가-힣])오는\s|예정|계획|전망|앞두고|열린다|시행된다|출범|개막|만료|마감|시작된다'
    r'|치러지|예상|목표|추진|열릴|할\s*방침|예고|공고')


def extract_schedule(body, target_date):
    """본문에서 대상일 이후의 예정 일자를 뽑는다.

    반환: [{'date': 'YYYY-MM-DD', 'quote': '근거 문장'}]
    확정할 수 없는 것은 버린다.
    """
    if not body:
        return []
    base = datetime.strptime(target_date, '%Y-%m-%d')
    found, seen = [], set()

    for sent in SENT_SPLIT.split(body):
        sent = sent.strip()
        if not sent or len(sent) > 300:
            continue
        # 연도를 문장 밖에서 물려받으면 해소 불가 — 통째로 버린다
        if YEAR_ELIDED_PAT.search(sent):
            continue
        # 과거 서술인데 미래 표지가 없으면 지나간 사건이다
        if has_past_ending(sent) and not FUTURE_MARK_PAT.search(sent):
            continue

        elided = bool(ELIDED_PAT.search(sent))

        # 연도 결정 순서: 명시 연도 > 지난해/내년 > 대상일 연도.
        # 한 문장에 연도가 둘 이상이면(예: 「2023년 …, 2025년 …」) 어느 날짜가
        # 어느 연도에 붙는지 알 수 없으므로 그 문장은 통째로 버린다.
        years = {int(m.group(1)) for m in EXPLICIT_YEAR_PAT.finditer(sent)}
        if len(years) > 1:
            continue
        if years:
            year = years.pop()
        elif LAST_YEAR_PAT.search(sent):
            year = base.year - 1
        elif NEXT_YEAR_PAT.search(sent):
            year = base.year + 1
        else:
            year = base.year

        pairs = [(int(m.group(1)), int(m.group(2))) for m in MONTH_DAY_PAT.finditer(sent)]

        # 문장에 '○월'만 있고 '○일'이 따로 있으면 그 월을 물려준다.
        # 단 '같은 달' 류 생략 신호가 있으면 문장 밖을 참조하는 것이므로 포기한다.
        if not pairs and not elided:
            months = [int(m.group(1)) for m in MONTH_ONLY_PAT.finditer(sent)]
            if len(months) == 1:
                pairs = [(months[0], int(m.group(1))) for m in DAY_ONLY_PAT.finditer(sent)]
            elif not months and not years:
                # 「오는 27일」처럼 월 없이 쓰는 근일 표현. 한국 기사에서 매우 흔하다.
                # '오는' 이 붙어 있으면 관례상 이번 달(이미 지났으면 다음 달)이다.
                #
                # 반드시 '오는' 바로 뒤의 날짜만 본다. 문장 안의 모든 '○일'을 주우면
                # 기사 자신의 보도 시점이 미래 일정으로 둔갑한다 — 실측 오답:
                # 「당국은 오는 26일까지 수색을 이어간다고 21일 밝혔다」 에서
                # '21일'(= 보도일)이 9월 21일로 추출됐다.
                for m in NEAR_TERM_PAT.finditer(sent):
                    marker = re.sub(r'\s+', '', m.group(1))
                    day = int(m.group(2))
                    if marker in ('내달', '다음달'):
                        mon = (base.month % 12) + 1      # 날짜 크기와 무관
                    else:
                        mon = base.month if day > base.day else (base.month % 12) + 1
                    pairs.append((mon, day))

        for mon, day in pairs:
            try:
                d = datetime(year, mon, day)
            except ValueError:
                continue
            # 대상일 이전이면 과거 언급이다. 단 12월→1월 연말 경계는 다음 해로 본다.
            if d <= base:
                if base.month >= 11 and mon <= 2 and not NEXT_YEAR_PAT.search(sent):
                    d = datetime(year + 1, mon, day)
                else:
                    continue
            if (d - base).days > 400:
                continue
            key = d.strftime('%Y-%m-%d')
            if key in seen:
                continue
            seen.add(key)
            found.append({'date': key, 'quote': sent[:120]})
    return found


def merge_schedule(group, target_date):
    """클러스터 안 기사들의 예정 일자를 합친다. 같은 날짜는 한 번만."""
    out, seen = [], set()
    for art in group:
        for item in extract_schedule(art.get('body'), target_date):
            if item['date'] in seen:
                continue
            seen.add(item['date'])
            out.append({**item, 'outlet': art['outlet'], 'url': art.get('url', '')})
    out.sort(key=lambda x: x['date'])
    return out


# --- 2차 병합 후보 (P-1) --------------------------------------------------
# 클러스터끼리 같은 상위 사건인지 본다. 중심 코사인 단독은 못 쓴다 —
# 실측 재현율 61% 지점에서 정밀도가 65% 였다.
# 희소 키워드 가드를 붙이면 같은 값에서 정밀도 100%/재현율 67% 로 뛴다(가드가 전부다).
#
# 그럼에도 자동 병합하지 않는다. 표본이 하루치 6쌍이라 근거가 얇다.
# 후보만 표시하고 3회차 후에 승격 여부를 판단한다.
MERGE_SIM = 0.10           # 클러스터 중심 간 코사인 하한
RARE_MAX_CLUSTERS = 3      # 상위 키워드로 이 수 이하 클러스터에만 등장해야 '희소'
RARE_MIN_SHARED = 2        # 희소 키워드를 이만큼 공유해야 후보


def find_merge_candidates(clusters, vecs):
    """(i, j, 코사인, 공유 희소 키워드) 목록. 병합은 하지 않는다."""
    topk = [set(c['keywords'][:10]) for c in clusters]
    df = collections.Counter()
    for s in topk:
        for t in s:
            df[t] += 1
    rare = {t for t, n in df.items() if n <= RARE_MAX_CLUSTERS}

    out = []
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            shared = topk[i] & topk[j] & rare
            if len(shared) < RARE_MIN_SHARED:
                continue
            sim = cosine(vecs[i], vecs[j])
            if sim >= MERGE_SIM:
                out.append((i, j, round(sim, 3), sorted(shared)))
    out.sort(key=lambda x: -x[2])
    return out


RESEND_TITLE_RATIO = 0.85   # 이 이상 닮은 제목만 같은 기사의 재송고로 본다


def _norm_title(t):
    """말머리·기호·공백을 걷어낸 비교용 제목"""
    t = re.sub(r'^\s*\[[^\]]{1,12}\]\s*', '', t or '')
    return re.sub(r'[\s·,…‘’“”"\'\-–—\[\]()]+', '', t)


def same_story(a, b):
    """두 제목이 같은 기사의 재송고인지.

    정정 보도가 원 보도로 오인돼 삭제되는 것을 막는 게 이 함수의 목적이다.
    「… 시신 발견…신분증 나와」 vs 「'… 시신 발견' 사실 아냐…경찰 수색 계속」 은
    앞부분이 겹쳐도 서로 다른 기사이므로 반드시 False 여야 한다.
    """
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= RESEND_TITLE_RATIO


def pick_outlet_representatives(group):
    """클러스터 안에서 매체당 1건만 대표로 남기고 (대표목록, 버린건수) 를 돌려준다.

    같은 매체가 같은 사안을 재송고한다. 실측: 동아일보가
    「李, '호우 피해' 거제·통영 산양읍·봉평동 특별재난지역 선포」를
    18:46 초판·20:24 갱신판으로 두 번 냈다. news_id·URL·본문 길이가 모두 달라서
    중복 제거에 안 걸린다(제목 완전 일치 중복은 실제로 0건이었다).

    이걸 두면 이슈 표의 'N사' 집계와 링크 목록이 부풀려진다.
    2026-08-21 기준 7개 클러스터에 초과 10건.

    🔴 같은 매체의 두 기사라고 무조건 합치면 안 된다. 실측 사고:
    2026-08-22 매일경제가 제주 실종 사건으로 세 건을 냈는데
      21:08 「[속보] … 추정 시신 발견…신분증 나와」   (732자)
      21:39 「'… 추정 시신 발견' 사실 아냐…경찰 수색 계속」 (718자)  ← 정정 보도
    본문 길이로 고르면 **오보가 남고 정정이 삭제된다.** 브리핑이 틀린 사실을 싣게 된다.

    그래서 **제목이 사실상 같을 때만** 재송고로 본다. 제목이 다르면 서로 다른
    후속 보도이므로 둘 다 남긴다. 대표는 나중 발행분 — 시각이 '무엇이 무엇을
    갱신했는가'의 직접 증거이고, 본문 길이는 그 대리 지표일 뿐이다.

    의견 기사(사설·칼럼)는 논조 비교에서 각각이 독립된 목소리이므로 아예 제외한다.
    """
    byoutlet = collections.defaultdict(list)
    opinions = []
    for a in group:
        if a.get('_opinion_kind'):
            opinions.append(a)
        else:
            byoutlet[a['outlet']].append(a)

    reps, dropped = [], 0
    for _outlet, arts in byoutlet.items():
        if len(arts) == 1:
            reps.append(arts[0])
            continue
        # 발행 시각 오름차순으로 두고, 앞선 것과 제목이 사실상 같으면 갱신판으로 접는다
        arts.sort(key=lambda x: x.get('published_ts', ''))
        buckets = []
        for art in arts:
            for b in buckets:
                if same_story(b[-1]['title'], art['title']):
                    b.append(art)
                    break
            else:
                buckets.append([art])
        for b in buckets:
            reps.append(b[-1])          # 가장 나중 발행분
            dropped += len(b) - 1
    return reps + opinions, dropped


def is_stub(article):
    """말머리가 붙은 초단문 — 사진 코너·안내 등. 뉴스성 말머리는 제외한다.

    실측: 동아 「[고양이 눈]특별석」 119자(사진 코너)는 버리고,
    매경 「[속보] 이 대통령…」 39자는 남긴다.
    """
    title = article.get('title', '')
    if NEWSY_PREFIX_PAT.match(title):
        return False
    if not re.match(r'^\s*\[', title):
        return False
    return article.get('body_len', 0) < STUB_BODY_LEN
# --- 의견 기사 판별 -------------------------------------------------------
# 논조 비교의 정확도가 곧 브리핑 품질이므로 두 신호를 함께 쓴다.
#   ① URL 경로 — 조선(/opinion/…)·한겨레(/arti/opinion/…)는 이것만으로 확실하다.
#   ② 제목 말머리 — 경향·매경·한경은 URL 이 평평한 /article/{id} 라 제목에 의존해야 한다.
# 사설(매체 공식 입장)과 칼럼(개인 필자 서명)을 나눈다. 논조 비교의 무게가 다르다.
OPINION_URL_PAT = re.compile(r'/(opinion|editorial|column)(/|$)', re.I)

# 매일경제는 말머리가 아니라 꼬리표를 쓴다:
#   「추가세수로 미래대응기금, 나랏빚 관리도 소홀해선 안돼 [사설]」
# 앞쪽만 보던 이전 패턴은 매경 사설 3건을 통째로 놓쳤다.
EDITORIAL_PAT = re.compile(r'(^\s*\[\s*(사설|社說)\s*\]|\[\s*(사설|社說)\s*\]\s*$)')
# 말머리든 꼬리표든 잡는다. 매체마다 위치가 다르다 —
# 조선 「[만물상] …」 은 앞, 한겨레 「'수저 자랑' 시대의 아픔 [박권일의 다이내믹 도넛]」 은 뒤,
# 동아 「잃고 얻은 것[이준식의 한시 한수]〈382〉」 는 중간이다.
COLUMN_PAT = re.compile(
    r'\[[^\]]*('
    r'칼럼|시론|논단|기고|발언대|만물상|데스크|窓|창'
    r'|기자의\s*시각|취재파일|취재수첩|기자수첩|현장에서|아침을\s*열며|세상읽기'
    r')[^\]]*\]'
)
# 필자명을 앞세운 연재 칼럼: [박진배의 공간과 스타일], [최지혜의 요즘 트렌드]
NAMED_COLUMN_PAT = re.compile(r'\[\s*[가-힣]{2,4}의\s+[^\]]{2,}\]')
# 시간 표현은 필자명이 아니다. 「[오늘의 채널A]…」 편성 안내가 위 패턴에 걸린다(실측).
TIME_WORD_PAT = re.compile(r'\[\s*(오늘|내일|어제|이번주|금주|이달|금월|올해)의\s')


def classify_opinion(title, url, is_editorial=False):
    """'editorial' | 'column' | '' 을 돌려준다.

    is_editorial 은 빅카인즈가 사설로 색인했다는 뜻이다(수집 단계에서
    editorialIs:true 전용 조회로 확보). 실측 전수 검사에서 오탐 0 이었으므로
    가장 신뢰도가 높은 신호로 취급한다. 제목·URL 규칙은 이 플래그가 없는
    칼럼을 잡기 위해 계속 필요하다.
    """
    if is_editorial:
        return 'editorial'
    if EDITORIAL_PAT.search(title):
        return 'editorial'
    if not TIME_WORD_PAT.search(title) and (
            COLUMN_PAT.search(title) or NAMED_COLUMN_PAT.search(title)):
        return 'column'
    path = re.sub(r'^https?://[^/]+', '', url or '')
    if OPINION_URL_PAT.search(path):
        # 경로가 opinion 인데 제목 말머리가 없으면 서명 칼럼으로 본다
        return 'column'
    return ''


def parse_categories(raw):
    """'정치>국회_정당<font...>|</font>정치>선거' -> ['정치', '정치'] (최상위, 순서 유지)"""
    if not raw:
        return []
    s = re.sub(r'<[^>]+>', '|', str(raw))
    out = []
    for part in s.split('|'):
        part = part.strip()
        if not part:
            continue
        top = part.split('>')[0].strip()
        if top in TOP_CATS:
            out.append(top)
    return out


def parse_vector(sim):
    """'이재명^1.32 OR 추미애^1.21' -> {용어: 가중치}"""
    vec = {}
    if not sim:
        return vec
    for tok in str(sim).split(' OR '):
        tok = tok.strip()
        if '^' not in tok:
            continue
        term, _, weight = tok.rpartition('^')
        term = term.strip()
        if not term:
            continue
        try:
            vec[term] = float(weight)
        except ValueError:
            continue
    return vec


def normalize_vec(vec):
    mag = math.sqrt(sum(w * w for w in vec.values()))
    if not mag:
        return {}
    return {t: w / mag for t, w in vec.items()}


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def split_entities(values):
    """collect.js 가 개행 구분 개체명을 한 원소로 남기는 경우가 있어 여기서 재분리"""
    out = []
    for v in values or []:
        for piece in re.split(r'[\n,;|]', str(v)):
            piece = piece.strip()
            if len(piece) > 1:
                out.append(piece)
    return out


def resolve_category(articles):
    """클러스터 대표 카테고리 — 슬롯 순서에 가중치를 둔 투표"""
    score = collections.Counter()
    for art in articles:
        for rank, cat in enumerate(art['_cats'][:3]):
            score[cat] += 3 - rank
    if not score:
        return '미분류'
    return score.most_common(1)[0][0]


def build_clusters(articles, threshold):
    """역색인으로 후보를 좁힌 뒤 union-find 로 응집"""
    inverted = collections.defaultdict(list)
    for art in articles:
        for term in art['_vec']:
            inverted[term].append(art['_i'])

    parent = list(range(len(articles)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    comparisons = 0
    for art in articles:
        candidates = set()
        for term in art['_vec']:
            bucket = inverted[term]
            if len(bucket) <= INV_MAX_DF:
                candidates.update(bucket)
        for j in candidates:
            if j <= art['_i']:
                continue
            comparisons += 1
            if cosine(art['_vec'], articles[j]['_vec']) >= threshold:
                union(art['_i'], j)

    groups = collections.defaultdict(list)
    for art in articles:
        groups[find(art['_i'])].append(art)
    return list(groups.values()), comparisons


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    if not date:
        print('usage: cluster.py <YYYY-MM-DD>', file=sys.stderr)
        return 2
    src = os.path.join(RAW_DIR, f'{date}.json')
    if not os.path.exists(src):
        print(f'수집 결과 없음: {src}', file=sys.stderr)
        return 2

    with open(src, encoding='utf-8') as f:
        raw = json.load(f)
    # 정리된 raw 에는 본문·카테고리·유사도가 없다. 그대로 돌리면 exit 0 으로
    # 통과하면서 전부 '미분류' 클러스터가 된다 — 정확히 이 스킬이 막으려는
    # 조용한 실패다. 실측: 226건 중 201건이 미분류로 나왔다.
    if raw.get('pruned'):
        print(f'정리된 수집 결과다: {src}\n'
              f'  {raw.get("pruned_at")} 에 본문이 폐기됐다. 제목·링크만 남아 있다.\n'
              f'  이 날짜를 다시 만들려면 collect.js 부터 다시 돌릴 것.',
              file=sys.stderr)
        return 2
    all_articles = raw['articles']

    series_ids = detect_series(all_articles)

    kept, noise = [], []
    for art in all_articles:
        title = art.get('title', '')
        why = ('말머리' if NOISE_PAT.match(title) else
               '연속물' if art['news_id'] in series_ids else
               '초단문' if is_stub(art) else
               '본문없음' if art.get('body_len', 0) == 0 else '')
        if why:
            art['_noise_reason'] = why
            noise.append(art)
            continue
        art['_i'] = len(kept)
        art['_vec'] = normalize_vec(parse_vector(art.get('similarity')))
        art['_cats'] = parse_categories(art.get('category'))
        art['_opinion_kind'] = classify_opinion(
            title, art.get('url'), art.get('is_editorial', False))
        art['_opinion'] = bool(art['_opinion_kind'])
        kept.append(art)

    groups, comparisons = build_clusters(kept, SIM_THRESHOLD)

    clusters = []
    centroids = []
    for group in groups:
        group, resend = pick_outlet_representatives(group)
        outlets = sorted({a['outlet'] for a in group})
        agg = collections.Counter()
        for art in group:
            for term, weight in art['_vec'].items():
                agg[term] += weight
        centroids.append(normalize_vec(dict(agg)))
        entities = collections.Counter()
        for art in group:
            for name in split_entities(art.get('ne_person')):
                entities[name] += 1
        clusters.append({
            'category': resolve_category(group),
            'size': len(group),
            'outlet_count': len(outlets),
            'outlets': outlets,
            'keywords': [t for t, _ in agg.most_common(8)],
            'top_persons': [n for n, _ in entities.most_common(5)],
            'editorial_count': sum(1 for a in group if a['_opinion_kind'] == 'editorial'),
            'column_count': sum(1 for a in group if a['_opinion_kind'] == 'column'),
            # 같은 매체 재송고로 접은 건수. 0 이 아니면 원 기사 수 = size + resend_dropped
            'resend_dropped': resend,
            'schedule': merge_schedule(group, date),
            'articles': [{
                'outlet': a['outlet'],
                'title': a['title'],
                'url': a['url'],
                'body_len': a['body_len'],
                'sentiment': (a.get('sentiment') or '').split()[0] if a.get('sentiment') else '',
                'is_opinion': a['_opinion'],
                'opinion_kind': a['_opinion_kind'],
                'news_id': a['news_id'],
            } for a in sorted(group, key=lambda x: x['outlet'])],
        })

    # 정렬 전에 후보를 찾고, 정렬 후 인덱스가 바뀌므로 클러스터에 표식을 심어 둔다
    cand = find_merge_candidates(clusters, centroids)
    for n, (i, j, sim, shared) in enumerate(cand):
        for k in (i, j):
            clusters[k].setdefault('merge_candidates', []).append(
                {'group': n, 'with': j if k == i else i, 'sim': sim, 'shared': shared})
    merge_report = [{
        'sim': sim, 'shared': shared,
        'a': {'category': clusters[i]['category'], 'size': clusters[i]['size'],
              'title': clusters[i]['articles'][0]['title']},
        'b': {'category': clusters[j]['category'], 'size': clusters[j]['size'],
              'title': clusters[j]['articles'][0]['title']},
    } for i, j, sim, shared in cand]

    # 여러 매체가 다룬 사안일수록 그날의 의제에 가깝다
    clusters.sort(key=lambda c: (-c['outlet_count'], -c['size']))

    os.makedirs(OUT_DIR, exist_ok=True)
    dest = os.path.join(OUT_DIR, f'{date}.json')
    # P-6: 렌더러가 clusters/{date}.json 하나만 읽고 헤더를 완성할 수 있게
    # 수집 메타를 그대로 실어 보낸다. raw 를 다시 열지 않아도 되도록.
    out = {
        'target_date': date,
        'collected_at': raw.get('collected_at'),
        'source': raw.get('source'),
        'outlet_stats': raw.get('outlet_stats', {}),
        'gates': raw.get('gates', []),
        'threshold': SIM_THRESHOLD,
        'article_count': len(all_articles),
        'clustered_count': len(kept),
        'noise_count': len(noise),
        # 무엇을 왜 버렸는지 남긴다. 조용한 절삭이 이 프로젝트의 최대 리스크다.
        'noise': [{'outlet': a['outlet'], 'title': a['title'],
                   'reason': a['_noise_reason'], 'body_len': a.get('body_len', 0)}
                  for a in noise],
        'cluster_count': len(clusters),
        # 자동 병합하지 않는다 — 표시만. 근거(공유 희소 키워드)를 함께 남겨
        # 사람이 판단할 수 있게 한다. 3회차 데이터가 쌓이면 승격 여부를 재검토.
        'merge_candidates': merge_report,
        'clusters': clusters,
    }
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    multi = [c for c in clusters if c['size'] > 1]
    cross = [c for c in clusters if c['outlet_count'] >= 3]
    by_cat = collections.Counter(c['category'] for c in clusters)

    print(f'# 클러스터링 — {date}')
    print(f'기사 {len(all_articles)}건 (노이즈 제외 {len(kept)}건, 제외 {len(noise)}건) '
          f'· 비교 {comparisons}쌍 · 임계값 {SIM_THRESHOLD}')
    print(f'클러스터 {len(clusters)}개 — 복수기사 {len(multi)}개, 3개사 이상 공통 {len(cross)}개')
    print()
    print('| 카테고리 | 클러스터 | 기사 |')
    print('|---|---|---|')
    for cat, n in by_cat.most_common():
        arts = sum(c['size'] for c in clusters if c['category'] == cat)
        print(f'| {cat} | {n} | {arts} |')
    print(f'\n저장: {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
