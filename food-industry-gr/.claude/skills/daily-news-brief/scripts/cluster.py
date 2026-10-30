#!/usr/bin/env python3
"""
daily-news-brief — issue clustering + category classification

Input:  <run folder>/raw/{date}.json            (output of normalize.py)
Output: <run folder>/work/{date}.clusters.json
        stdout  summary statistics
The run folder is given with --run; by default the most recent run folder for that date (runs/daily-news-brief/{date}-N).

BigKinds TMS_SIMILARITY is not a list of similar-article IDs but a 'term^weight' vector.
Grouping by cosine similarity gathers other outlets' articles on the same matter into one cluster.
A matter covered by several outlets at once is a signal of that day's real agenda, so
cluster size and the number of participating outlets are the primary basis for briefing priority.
"""
import json
import re
import math
import sys
import os
import collections
import difflib
from datetime import datetime

import common

TOP_CATS = ['정치', '경제', '사회', '국제', '문화', '스포츠', 'IT_과학', '지역']
SIM_THRESHOLD = 0.18   # Tuned from measurements. Below 0.16 is a cliff (the president cluster collapses, 2 wrong merges).
                       # 0.22 over-split the same matter (Jeju: 3 clusters). 0.18 fixes the over-splitting with 0 wrong merges.
INV_MAX_DF = 40        # Terms more common than this are left out of candidate generation (keeps comparisons down)

# Formulaic articles with no briefing value. The Maeil Business [photo] series is the typical case.
# The reply-notice and correction-notice tags (banron-bodo, jeongjeong-bodo) use angle brackets; the rest use square brackets.
NOISE_PAT = re.compile(
    r'^\s*(?:\[(포토|포토뉴스|화보|부고|부음|인사|동정|알림|표|사진|사진기획'
    r'|모집|공고|기고|카드뉴스|영상|오늘의\s*운세|운세)\b'
    r'|[<〈]\s*(반론보도|정정보도|알려드립니다)\s*[>〉])'
)

# Tagged titles that must not be dropped. Short or not, they are real news.
# Observed: a "[sokbo]" (breaking news) item on a monk's death has a 39-character body.
NEWSY_PREFIX_PAT = re.compile(
    r'^\s*\[\s*(속보|단독|긴급|종합|전문|일문일답|인터뷰|현장|르포|팩트체크)\b')

# Short-body threshold. Below this it is closer to a caption or notice than an 'article'.
STUB_BODY_LEN = 200
# Series test: this many or more titles from one outlet sharing the same phrase count as a formulaic series.
SERIES_MIN_COUNT = 5
SERIES_MIN_SUBSTR = 6      # minimum length of the shared phrase (characters)
SERIES_MAX_MEDIAN_BODY = 300   # safeguard so that genuine running coverage is not killed


def _shared_substrings(title, n):
    """The set of character n-grams of length n in a title."""
    t = re.sub(r'\s+', ' ', title).strip()
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def detect_series(articles):
    """Return the set of news_ids of formulaic series that one outlet published in bulk with the same template.

    On 2026-08-22 Kyunghyang Shinmun ran 20 photo captions of musical-competition entrants,
    such as one naming a student entrant singing a song; they had no [photo] tag,
    so they passed the tag rule and became that day's largest cluster (20 items).

    Length alone cannot filter them — the same day Maeil Business ran a "[sokbo]" item that was a
    genuine 39-character-body breaking item. So all three conditions are required:
      1. same outlet  2. at least SERIES_MIN_COUNT articles share a phrase in the title
      3. the median body length of that group is under SERIES_MAX_MEDIAN_BODY
    Condition 3 protects genuine running coverage (long articles on one event from several angles).
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


# --- Upcoming-schedule extraction (P-2) -----------------------------------
# Pulls the briefing's "upcoming schedule" section from the body text.
#
# Principle: **do not extract a date that can only be resolved by looking outside its sentence.**
# Better to omit than to give a wrong date. If a date listed as upcoming is really a past event, trust collapses.
#
# Dropped (every rule comes from a wrong answer observed in practice):
#   "the 23rd of the same month"   month inherited from the previous sentence        → ELIDED_PAT
#   "in the same year, arrested on Nov 24"  year inherited                    → YEAR_ELIDED_PAT
#   "Dec 3, 2024" / "Sep 25 last year"  past, but computed as this year → EXPLICIT/LAST_YEAR_PAT
#   "2023 ..., 2025 ..." two years in one sentence                    → unclear which date belongs to which year
#   "On Oct 11 he phoned Mr. A again ... it was found"  past narration        → has_past_ending()
#
# Kept:
#   "oneun 27-il" (on the coming 27th)  no month, but the convention that 'oneun' means this month is firm → NEAR_TERM_PAT
#   "oneun Oct 23 ... gave notice"  past-tense ending but a future marker is present → FUTURE_MARK_PAT
SENT_SPLIT = re.compile(r'(?<=[.!?。])\s+|\n+')
MONTH_DAY_PAT = re.compile(r'(?<![\d])(1[0-2]|[1-9])\s*월\s*(3[01]|[12]\d|[1-9])\s*일')
MONTH_ONLY_PAT = re.compile(r'(?<![\d])(1[0-2]|[1-9])\s*월(?!\s*\d*\s*일)')
DAY_ONLY_PAT = re.compile(r'(?<![\d])(3[01]|[12]\d|[1-9])\s*일(?!\s*간|\s*째|\s*자|\s*분|\s*동안)')
# Month-elision signals — if one of these is present, do not trust that sentence's day-of-month number
ELIDED_PAT = re.compile(r'(같은\s*달|이달|지난달|다음\s*달|당월|해당\s*월)')
NEXT_YEAR_PAT = re.compile(r'내년|이듬해')
# If the sentence states a year, use it. Without this, a past event gets listed as a future one —
# observed wrong answers: "Dec 3, 2024 ...", "died on Sep 25 last year ...", "phone call on Aug 3~24, 2023".
EXPLICIT_YEAR_PAT = re.compile(r'((?:19|20)\d{2})\s*년')
LAST_YEAR_PAT = re.compile(r'지난해|작년|전년')
# Expressions that inherit the year from outside the sentence. Unresolvable, for the same reason as month elision.
# Observed wrong answer: "According to court records he was arrested on Nov 24 of the same year ..."
YEAR_ELIDED_PAT = re.compile(r'같은\s*해|당해|그해|해당\s*연도')
# Signal for near-term expressions written without a month. With 'oneun' the convention that it means this month is firm.
#
# 🔴 No Hangul may precede it. In "kakao-neun 21-il isahoe-reul yeolgo..." (Kakao held a board meeting on the 21st) the text was cut as
# 'kaka|oneun' and matched, so the past August 21 board meeting became a September 21 schedule item.
# There are countless such traps: "naoneun", "boineun", "deureooneun" and so on.
NEAR_TERM_PAT = re.compile(
    r'(?<![가-힣])(오는|내달|다음\s*달|다음\s*주)\s*(3[01]|[12]\d|[1-9])\s*일')
# 'naedal' always means next month, whatever the size of the day number
NEXT_MONTH_WORDS = ('내달', '다음달', '다음 달')
# Past narration. This alone must not cause a drop —
# "oneun Oct 23 ... announced the sentencing date" ends in the past tense but is a future schedule item.
# So drop only when there is no future marker.
#
# 🔴 Do not enumerate syllables. Korean past forms contract differently depending on the stem, so
# listing only the syllables haess/yeoss/dwaess misses balkhyeotda (balkhi+eot→hyeot), kyeotda, pyeotda and chyeotda wholesale.
# Observed: "... said it had decided ..." (gyeoruihaetdago balkhyeotda) was not caught as past narration.
#
# The only thing they have in common is that **the final consonant is the double-s jamo (U+3146)**. For a composed Hangul syllable
#   code = 0xAC00 + (initial×21 + medial)×28 + final
# so (code - 0xAC00) % 28 == 20 means the final is double-s (U+3146). That is what we test directly.
PAST_TAIL_PAT = re.compile(r'([가-힣])(다|다고|다는|으며|며|고|지만|던|음)(?![가-힣])')


def _has_ss_final(ch):
    """Whether the character is a composed Hangul syllable whose final consonant is double-s (U+3146)."""
    c = ord(ch)
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 == 20


# Stems with a double-s final that are not past tense. "swil su itda" (can rest) was caught as past narration
# and a perfectly good future schedule item was dropped. "isseotda" is still caught because its preceding syllable is 'eot'.
NOT_PAST_STEMS = frozenset(['있', '없'])


def has_past_ending(sent):
    return any(_has_ss_final(m.group(1)) and m.group(1) not in NOT_PAST_STEMS
               for m in PAST_TAIL_PAT.finditer(sent))


# 'oneun' blocks a preceding Hangul syllable for the same reason as NEAR_TERM_PAT (the "kakao-neun" trap).
FUTURE_MARK_PAT = re.compile(
    r'(?<![가-힣])오는\s|예정|계획|전망|앞두고|열린다|시행된다|출범|개막|만료|마감|시작된다'
    r'|치러지|예상|목표|추진|열릴|할\s*방침|예고|공고')


def extract_schedule(body, target_date):
    """Pull scheduled dates after the target date out of the body.

    Returns: [{'date': 'YYYY-MM-DD', 'quote': 'the supporting sentence'}]
    Anything that cannot be pinned down is dropped.
    """
    if not body:
        return []
    base = datetime.strptime(target_date, '%Y-%m-%d')
    found, seen = [], set()

    for sent in SENT_SPLIT.split(body):
        sent = sent.strip()
        if not sent or len(sent) > 300:
            continue
        # A year inherited from outside the sentence cannot be resolved — drop the whole sentence
        if YEAR_ELIDED_PAT.search(sent):
            continue
        # Past narration with no future marker is an event that has already happened
        if has_past_ending(sent) and not FUTURE_MARK_PAT.search(sent):
            continue

        elided = bool(ELIDED_PAT.search(sent))

        # Year precedence: stated year > last year / next year > the target date's year.
        # With two or more years in one sentence (e.g. "2023 ..., 2025 ...") there is no telling which date
        # goes with which year, so the whole sentence is dropped.
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

        # If the sentence has only a month number and a separate day number, pass the month on to the day.
        # But if there is an elision signal such as 'same month', it refers outside the sentence, so give up.
        if not pairs and not elided:
            months = [int(m.group(1)) for m in MONTH_ONLY_PAT.finditer(sent)]
            if len(months) == 1:
                pairs = [(months[0], int(m.group(1))) for m in DAY_ONLY_PAT.finditer(sent)]
            elif not months and not years:
                # A near-term expression written without a month, like "oneun 27-il". Very common in Korean news.
                # With 'oneun' attached, by convention it is this month (next month if that day has already passed).
                #
                # Look only at the date right after 'oneun'. If every day number in the sentence were picked up,
                # the article's own reporting date would turn into a future schedule item — observed wrong answer:
                # in "the authorities will continue the search until the 26th, it said on the 21st"
                # the 21st (= the reporting date) was extracted as September 21.
                for m in NEAR_TERM_PAT.finditer(sent):
                    marker = re.sub(r'\s+', '', m.group(1))
                    day = int(m.group(2))
                    if marker in ('내달', '다음달'):
                        mon = (base.month % 12) + 1      # regardless of the day number
                    else:
                        mon = base.month if day > base.day else (base.month % 12) + 1
                    pairs.append((mon, day))

        for mon, day in pairs:
            try:
                d = datetime(year, mon, day)
            except ValueError:
                continue
            # On or before the target date it is a past mention. But the December→January year-end boundary counts as next year.
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
    """Combine the scheduled dates from the articles in a cluster. Each date only once."""
    out, seen = [], set()
    for art in group:
        for item in extract_schedule(art.get('body'), target_date):
            if item['date'] in seen:
                continue
            seen.add(item['date'])
            out.append({**item, 'outlet': art['outlet'], 'url': art.get('url', '')})
    out.sort(key=lambda x: x['date'])
    return out


# --- Second-pass merge candidates (P-1) -----------------------------------
# Check whether two clusters belong to the same higher-level event. Centroid cosine alone is not usable —
# at 61% recall the measured precision was 65%.
# With a rare-keyword guard, the same threshold gives 100% precision / 67% recall (the guard is everything).
#
# Even so, nothing is merged automatically. The sample is 6 pairs from one day, so the evidence is thin.
# Candidates are only shown, and promotion is decided after the 3rd round.
MERGE_SIM = 0.10           # lower bound of cosine between cluster centroids
RARE_MAX_CLUSTERS = 3      # a top keyword is 'rare' only if it appears in this many clusters or fewer
RARE_MIN_SHARED = 2        # two clusters are candidates only if they share this many rare keywords


def find_merge_candidates(clusters, vecs):
    """List of (i, j, cosine, shared rare keywords). Does not merge."""
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


RESEND_TITLE_RATIO = 0.85   # only titles at least this similar count as a re-send of the same article


def _norm_title(t):
    """A title for comparison, with the tag, punctuation and spaces stripped."""
    t = re.sub(r'^\s*\[[^\]]{1,12}\]\s*', '', t or '')
    return re.sub(r'[\s·,…‘’“”"\'\-–—\[\]()]+', '', t)


def same_story(a, b):
    """Whether two titles are a re-send of the same article.

    The purpose of this function is to stop a correction from being mistaken for the original report and deleted.
    "... body found ... ID card ..." vs "'... body found' not true ... police search continues" are different articles
    even though the openings overlap, so the result must be False.
    """
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= RESEND_TITLE_RATIO


def pick_outlet_representatives(group):
    """Keep one representative per outlet in a cluster and return (representatives, dropped count).

    An outlet re-sends the same matter. Observed: Dong-A Ilbo ran
    a headline on special disaster-zone designation for flood-hit areas
    twice, an 18:46 first edition and a 20:24 updated one. The news_id, URL and body length all differ,
    so de-duplication does not catch it (exact-title duplicates were in fact 0).

    Leaving this in inflates the issue table's 'N outlets' count and its link list.
    As of 2026-08-21: 10 extra items across 7 clusters.

    🔴 Never merge two articles just because they come from the same outlet. Observed incident:
    on 2026-08-22 Maeil Business ran three items on the Jeju missing-person case
      21:08 "[sokbo] ... presumed body found ... ID card ..."   (732 chars)
      21:39 "'... presumed body found' not true ... police search continues" (718 chars)  ← a correction
    Choosing by body length **keeps the false report and deletes the correction.** The briefing would then carry a wrong fact.

    So treat them as a re-send **only when the titles are effectively the same**. Different titles mean
    different follow-up reports, so both stay. The representative is the later edition — the timestamp is direct
    evidence of 'what updated what', and body length is only a proxy for it.

    Opinion pieces (editorials, columns) are left out entirely, since each is an independent voice in the tone comparison.
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
        # Order by publication time ascending; fold into an earlier one when the title is effectively the same (an updated edition)
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
            reps.append(b[-1])          # the latest edition
            dropped += len(b) - 1
    return reps + opinions, dropped


def is_stub(article):
    """A very short item with a bracketed tag — photo corners, notices and the like. News-type tags are excluded.

    Observed: a Dong-A "[Cat's Eye] special seat" photo-corner item at 119 chars is dropped,
    while a Maeil Business "[sokbo] President Lee ..." item at 39 chars is kept.
    """
    title = article.get('title', '')
    if NEWSY_PREFIX_PAT.match(title):
        return False
    if not re.match(r'^\s*\[', title):
        return False
    return article.get('body_len', 0) < STUB_BODY_LEN
# --- Opinion-article detection --------------------------------------------
# The accuracy of the tone comparison is the briefing's quality, so two signals are used together.
#   1. URL path — Chosun (/opinion/…) and Hankyoreh (/arti/opinion/…) are certain from this alone.
#   2. Title tag — Kyunghyang, Maekyung and Hankyung have a flat /article/{id} URL, so they depend on the title.
# Editorials (the outlet's official position) and columns (signed by an individual writer) are told apart. They carry different weight in the tone comparison.
OPINION_URL_PAT = re.compile(r'/(opinion|editorial|column)(/|$)', re.I)

# Maeil Business uses a trailing label, not a leading tag:
#   "... [editorial]" (a title that ends in the editorial label)
# The earlier pattern looked only at the front and missed all 3 Maekyung editorials.
EDITORIAL_PAT = re.compile(r'(^\s*\[\s*(사설|社說)\s*\]|\[\s*(사설|社說)\s*\]\s*$)')
# Catch it whether it is a leading tag or a trailing label. The position differs by outlet —
# Chosun "[Mansangmul] …" is at the front, Hankyoreh "... [Park Gwon-il's Dynamic Donut]" at the end,
# and Dong-A "... [Lee Jun-sik's Hansi Hansu]<382>" in the middle.
COLUMN_PAT = re.compile(
    r'\[[^\]]*('
    r'칼럼|시론|논단|기고|발언대|만물상|데스크|窓|창'
    r'|기자의\s*시각|취재파일|취재수첩|기자수첩|현장에서|아침을\s*열며|세상읽기'
    r')[^\]]*\]'
)
# Serial columns led by the writer's name: "[Park Jin-bae's Space and Style]", "[Choi Ji-hye's Trends of the Day]"
NAMED_COLUMN_PAT = re.compile(r'\[\s*[가-힣]{2,4}의\s+[^\]]{2,}\]')
# A time expression is not a writer's name. A programme notice like "[Today's Channel A]…" is caught by the pattern above (observed).
TIME_WORD_PAT = re.compile(r'\[\s*(오늘|내일|어제|이번주|금주|이달|금월|올해)의\s')


def classify_opinion(title, url, is_editorial=False):
    """Return 'editorial', 'column' or ''.

    is_editorial means BigKinds indexed the article as an editorial (obtained in the
    collection step by the dedicated editorialIs:true query). It had 0 false positives in a full
    check, so it is treated as the most reliable signal. The title and URL rules are still
    needed to catch columns, which do not carry this flag.
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
        # The path says opinion but the title has no tag: treat it as a signed column
        return 'column'
    return ''


def parse_categories(raw):
    """'politics>assembly_party<font...>|</font>politics>election' -> ['politics', 'politics'] (top level only, order kept)"""
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
    """'Lee^1.32 OR Choo^1.21' -> {term: weight}"""
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
    """collect.js sometimes leaves newline-separated entity names as one element, so split them again here"""
    out = []
    for v in values or []:
        for piece in re.split(r'[\n,;|]', str(v)):
            piece = piece.strip()
            if len(piece) > 1:
                out.append(piece)
    return out


def resolve_category(articles):
    """Representative category of a cluster — a vote weighted by slot order"""
    score = collections.Counter()
    for art in articles:
        for rank, cat in enumerate(art['_cats'][:3]):
            score[cat] += 3 - rank
    if not score:
        return '미분류'
    return score.most_common(1)[0][0]


def build_clusters(articles, threshold):
    """Narrow candidates with an inverted index, then cluster them with union-find"""
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
    argv = sys.argv[1:]
    flag_date, run_arg = common.parse_run_flags(argv)
    date = argv[0] if argv else flag_date
    if not date:
        print('usage: cluster.py <YYYY-MM-DD> [--run <실행 폴더>]', file=sys.stderr)
        return 2
    date = common.check_date(date)
    run = common.resolve_run(date, run_arg)
    src = str(run / 'raw' / f'{date}.json')
    if not os.path.exists(src):
        print(f'수집 결과 없음: {src}', file=sys.stderr)
        return 2

    with open(src, encoding='utf-8') as f:
        raw = json.load(f)
    # A pruned raw has no body, category or similarity. Running on it as-is
    # passes with exit 0 while every cluster comes out 'uncategorized' — exactly
    # the silent failure this skill exists to prevent. Observed: 201 of 226 came out uncategorized.
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
            # Number folded away as same-outlet re-sends. If not 0, original article count = size + resend_dropped
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

    # Find candidates before sorting; indexes change after the sort, so mark the clusters themselves
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

    # The more outlets covered a matter, the closer it is to that day's agenda
    clusters.sort(key=lambda c: (-c['outlet_count'], -c['size']))

    dest = run / 'work' / f'{date}.clusters.json'
    # P-6: carry the collection metadata through as-is so the renderer can complete the header
    # from the clusters file alone, without reopening raw.
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
        # Record what was dropped and why. Silent truncation is this project's biggest risk.
        'noise': [{'outlet': a['outlet'], 'title': a['title'],
                   'reason': a['_noise_reason'], 'body_len': a.get('body_len', 0)}
                  for a in noise],
        'cluster_count': len(clusters),
        # No automatic merge — display only. The evidence (shared rare keywords) is kept alongside
        # so a person can judge. Revisit promotion once 3 rounds of data have accumulated.
        'merge_candidates': merge_report,
        'clusters': clusters,
    }
    common.write_json(dest, out)

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
    common.setup_output()
    try:
        sys.exit(main())
    except common.UsageError as e:
        sys.exit(common.fail(str(e), 2))
