# Scan-style briefing rendering rules (format 3)

A rule specification for **rebuilding `03-scan-table.md` the same way every day**. These are rules to implement, not prose description.
The input is `work/{date}.clusters.json` and `raw/{date}.json` in the run folder (`runs/daily-news-brief/{date}-N/`).
Words in 「」, backticks and the tables below that are Korean are the report's own wording and stay as they are.

## Contents
- [1. Priority score](#1-priority-score-internal-sort-key--not-shown-on-screen)
- [2. Table structure](#2-table-structure)
- [3. Link rules](#3-link-rules)
- [4. Categories](#4-categories)
- [5. Length caps](#5-length-caps)
- [6. Trust marks](#6-trust-marks)
- [7. Failure modes](#7-failure-modes)
- [8. Lint of the output](#8-lint-of-the-output)
- [9. Deterministic / non-deterministic boundary](#9-deterministic--non-deterministic-boundary)
- [Appendix. What is left out of the output](#appendix-what-is-left-out-of-the-output)

---

## 1. Priority score (internal sort key — **not shown on screen**)

> The score is an **internal value for deciding the order** of issues. No number is printed in a table.
> Only the order and the tags beside an issue name (`3사` `사설2` `단독`) are shown.

### 1-1. Unit of calculation

The score is computed per **issue row**. An issue row is a set of one or more clusters (manual and automatic merging are allowed).
One article belongs to **exactly one issue row**. It may be quoted in the tone block of another row, but is not counted twice in the score.

Aggregates per row:

| Symbol | Definition |
|---|---|
| `O` | size of the **union of outlets** of the clusters in the row |
| `S` | total number of articles |
| `E` | sum of `editorial_count` |
| `C` | sum of `column_count` |
| `L` | largest `body_len` in the row |
| `X` | true if any article in the row has a title starting with `[단독]` |

### 1-2. Formula

```
P = 45 × log2(1 + O) / 3            # 0~45,  O=1→15.0  O=3→30.0  O=7→45.0
F = 10 × min(1, max(0, S - O) / 3)  # 0~10
N = min(20, 10×E + 4×C)             # 0~20
D = 15 × min(1, L / 2500)           # 0~15
B = 10 if X else 0                  # 0~10
score = round(P + F + N + D + B, 0)   # 0~100
```

### 1-3. Why these weights

| Component | Points | Why this value |
|---|:--:|---|
| `P` number of outlets | 45 | The strongest signal that a topic is the day's common agenda, so the largest weight. Why a **logarithm**: going from 1 to 2 outlets (a scoop spreading) means something different from going from 6 to 7 (already a front-page topic). A linear scale would let only big issues hold the top. `÷3` is the normalization that makes O=7 exactly 45 (`log2(8)=3`) |
| `F` follow-up volume | 10 | An outlet writing several pieces means it gave the topic a lot of space. It saturates when `S−O` is 3. It correlates strongly with the outlet count, so the weight is low |
| `N` editorial or column attached | 20 | A signal of controversy. One editorial's 10 points equal 2.5 columns (an editorial is the outlet's official position, a column carries a person's name). It saturates at 20 with two editorials, since more than that is already reflected in the outlet count |
| `D` body length | 15 | Separates a brief from an in-depth piece. 2,500 characters was taken as the floor of in-depth (the top 10% boundary of the 2026-08-21 sample) |
| `B` scoop | 10 | A scoop is **in principle O=1**, so it is structurally at a disadvantage in `P`. 10 points are given to exceed the 8.8-point gap between 1 outlet (15 points) and 2 outlets (23.8 points) |

### 1-4. Check against the 2026-08-21 data

Because of the `min(1, ...)` saturation, the upper bound of 100 does not actually occur. The top score that day was 68.

**Top 8 overall (order before the improvement → after)**

| Rank | After (score) | First column before | Position before |
|:--:|---|---|---|
| 1 | 국힘 당협위원장 전원 교체 (68) | `3사` | 정치 5th |
| 2 | 삼성전자 110조 주주환원 (67) | `6사 9건` | 경제 1st |
| 3 | 노동 — 하루에 겹친 네 곳 (64) | `4갈래` | 사회 2nd |
| 4 | 제주 실종 사건 후속 (61) | `5사 3갈래` | 사회 3rd |
| 5 | 미래대응기금 100조 (58) | `사설5` (one row with 교부금) | 경제 2nd |
| 6 | 5년 만의 반부패정책협의회 (57) | `4사` | 정치 4th |
| 7 | 노웅래 무죄에 대통령 사과 (56) | `7사` | **정치 1st** |
| 8 | 교육교부금 55년 만 개편 (53) | (one row with 5) | — |

**What changed**

1. **`7사` 노웅래 went from 1st to 7th.** It has the most outlets, but no editorial or column and no follow-up. This separates a one-shot item that every outlet copied from a topic that drew controversy.
2. **The 국힘 당협 item went from 5th in 정치 to 1st overall.** The two editorials (the full 20 points) were decisive. The old order looked only at 3 outlets and put it lower.
3. **Scoops came back.** 「[단독] 제주청 112 단독 종결 금지」 was the fourth link of the 제주 follow-up row in the earlier version, with no scoop mark. Now it gets `B=10` and the row itself ranks 2nd in 사회.
4. **The "1 outlet" 40-row problem is gone.** In the earlier issue table, 40 of 61 rows were tied at `1사`. Now the scores of 1-outlet rows spread from 15.0 to 30.0 (`D` and `N` separate them).

**The remaining weakness**: among rows of 1 outlet and 1 piece, `D` (body length) is the only separator. 「알프스 낙석 경고」 (25) > 「테슬라 중국 300만대 리콜」 (18) is an example. Body length is only a proxy for depth, not for importance. No improvement is implemented yet.

### 1-5. Selection rule for 「오늘 세 줄」

1. Sort all issue rows by score, descending.
2. Take **at most 1 per category**, 3 from the top.
3. On a tie, the larger `O` wins; if still tied, the larger `L`.
4. If the 3rd score is **under 40, leave that line empty** and change the heading from "오늘 세 줄" to "오늘 두 줄" or "오늘 한 줄". Do not fill it by force.
5. Each line has the form `**title** — one line`, within 70 characters. **Do not print the score.**

Result on 2026-08-21: 국힘 당협 (정치) / 삼성전자 (경제) / 노동 네 곳 (사회). 4th place 제주 was dropped as a 사회 duplicate.
(The internal scores were 68, 67, 64 and 61, but they are not kept in the output.)

---

## 2. Table structure

### 2-1. Issue table

```
| 이슈 | 한 줄 | 링크 |
|---|---|---|
```

| Column | Content | Cap |
|---|---|---|
| 이슈 | issue name + tags. Tags in fixed order: `` `3사` `` `` `사설2` `` `` `단독` `` `` `6사9건` `` | 40 characters |
| 한 줄 | factual summary. When there are several strands, separate with ` / ` | **90 characters** |
| 링크 | `[outlet][id]`, space-separated | 8 links |

**Row cap**: **300 characters** per raw markdown row (counted with references, URLs excluded). When over, shorten 「한 줄」 first.
**Row-count cap**: **8 rows** per category. The overflow is folded into one `> **그 밖**` line under the table.
**Cutoff**: a score under 20 goes to 「그 밖」 even if within the 8 rows. If 「그 밖」 is empty, do not write that line.
**No opinion-only rows**: a cluster made only of editorials and columns goes in the tone table, not the issue table. Exception: if the straight news on that topic is not in the sample and the score is 50 or more, put one row in the issue table as well, with the link pointing to the editorial (「교육교부금」 on 2026-08-21).

### 2-2. Tone table

Fixed order of a dispute block:

```
**쟁점 N · {사안}** — **축**: {판단 축} · **대상**: {정부|여당|야당|기업|사법부|노조|…}

| 매체 | 유형 | 입장 | 핵심 논거 | 링크 |

> ⚔️ **충돌점** …
> 🤝 **합의점** …
> 🕳️ **공백** …
> 🚫 **반론 없음** …
```

- **The four blocks are written for every dispute, with no exception.** When nothing applies, fill in `없음` or `해당 없음`. Never drop a block.
- A category with 0 editorials and 0 columns keeps the four blocks too, and `🕳️ 공백` states that fact.
- `유형` is `**사설**` (bold) / `칼럼` / `해설` / `스트레이트`. **Only editorials are bold**, which separates the levels visually.
- **Never mix editorials and straight news in one table.** When a topic needs both, split into two tables and change the second table's column name from `입장` to **`프레임`**. Put one line above the table saying 「매체 공식 입장이 아니다」 (this is not the outlet's official position).
- The `핵심 논거` cell is capped at **120 characters**. Quoted original text inside quotation marks is **at most 40 characters in total** per editorial (an editorial is 1,000 to 1,500 characters, so a summary easily becomes a substitute for the original).
- An outlet not in the sample is **always** named in one line right under the table: `> ⊘ **표본 내 미포착** 조선·매경·한경`. Do not leave it blank. When only the editorial is missing: `⊘ **{분야} 사설 미포착**`.

### 2-3. Tone scale

Print it in the document **every time** (a briefing must be readable on its own).

| Icon | Meaning |
|---|---|
| 🟢 | 적극 찬성 (strongly in favour) |
| 🟡 | 조건부 — 방향엔 동의, 조건·보완 요구 (agrees with the direction, asks for conditions or additions) |
| 🟠 | 우려 — 방향 자체에 회의적이나 전면 반대는 아님 (doubtful about the direction itself, but not fully opposed) |
| 🔴 | 비판 — 정면 반대 (openly opposed) |
| ⚪ | 유보 — 판단을 내리지 않음 (no judgment made) |

**Duty to state axis and target.** A dispute block with no `축` and `대상` in its head is not rendered.
Icons are comparable **only within the same axis**. This sentence goes into the briefing header as fixed wording.

### 2-4. Upcoming-schedule table

```
| 날짜 | 분야 | 일정 | 출처 |
```

- **Window**: base date D+0 to D+14. Anything later is folded into one `> **그 이후**` line.
- **Order**: date ascending. Within a day, category order (정치, 경제, 사회, 국제, 문화).
- **Imminent mark**: within D+7, put 🔴 before the date.
- **Row cap**: 16 rows. When over, move the schedule items attached to the lowest-scoring issues to 「그 이후」 first.
- **Source required**: link to the article that reported that date. It may be an article not in the issue table.
- **Schedule cell cap**: 40 characters.
- **Relative expressions**: base date = the target date (`target_date`). `오늘`=D+0, `내일`=D+1, `모레`=D+2, `이달 말`=the last day of that month, `다음 달 N일`/`내달 N일`=day N of next month, `내년 N월`=month N of `target_date.year+1`, `내년 N월 M일`=an absolute date. If only a weekday appears (`이번 주 금요일`), compute it within the week of the base date.
  - **Caution**: even if an article writes "내년 3월 17일", when the target date is in 2026 it is **17 March 2027**. If an absolute year is written beside it in the source (`2027년 3월 17일`), that wins.
- **Expressions that cannot be converted are not printed.** `조만간`, `연내`, `수개월 내`, `향후` and the like are excluded.
- **Past dates are excluded.** A date earlier than the base date is background and is not printed.

---

## 3. Link rules

### 3-1. Labels

| Case | Label |
|---|---|
| Default | outlet abbreviation — `조선` `중앙` `동아` `한겨레` `경향` `매경` `한경` |
| 2 or more pieces from the same outlet in one row | `outlet·keyword` — e.g. `중앙·현대차` `중앙·삼성` `경향·단독` `경향·2차가해` |

- **`[보기]` is forbidden.** A label with no information cannot be scanned.
- **No ad-hoc parentheses.** Do not use forms like `[중앙(현대차)]` or `[한겨레(취재)]`. The one separator is the middle dot `·`.
- The keyword is **2 to 4 characters**. Priority: (1) the first cluster `keywords` entry that appears in that article's title, (2) the first proper noun of the title, (3) the kind of article, such as `단독`/`사설`/`칼럼`.

### 3-2. Reference links

The body uses **reference style** only (`[조선][p9]`), and the URLs are collected in a definition block at the end of the document.

- Why: on 2026-08-21 URLs were 43% of all characters in the document (8,257 of 19,117). Inline, a raw markdown row grew to 525 characters, which clashes head-on with the point of a scan-style document. With references the longest row dropped to **200 characters**.
- ID rule `{first letter of category}{serial number}`: `p` (정치) `e` (경제) `s` (사회) `i` (국제) `c` (문화). Numbered in order of appearance within a category.
- The definition block goes under a `<!-- 링크 정의 ... -->` comment, one `[id]: url` per line.

### 3-3. URL hygiene

1. Remove everything from `?utm_*` on (BigKinds tracking parameters).
2. **No home addresses.** It must be an individual article URL.
3. The same URL must not be defined under two IDs (duplicate-definition check).
4. Confirm that every URL returns **HTTP 200**, **sequentially or with concurrency of 3 or less**. 한겨레 returns 403 to parallel requests (6 items got 403 at 12 threads, and all were 200 when rechecked sequentially). (The original rule used an ordinary browser UA string; this skill no longer does, because it never fakes a UA.) In the current implementation the HTTP check is replaced by a shape check of the address (collection gate G5), so no newspaper site is opened.

---

## 4. Categories

**5 fixed: 정치 · 경제 · 사회 · 국제 · 문화.** The order is fixed too. Never add or remove one.

When a cluster's category is not among the five:

| Original category | Handling |
|---|---|
| `지역` | 사회 or 정치, depending on the nature of the topic |
| `IT_과학` | industry and companies → 경제, culture and consumer trends → 문화 |
| `미분류` | place in one of the five by title and body |
| `스포츠` | **do not print** |

On a manually moved item, **put `⟳` right there**, and put one line `⟳ 수동 교정: A(원분류→새분류)` under the tone blocks of that category. Do not collect them at the end of the document.

---

## 5. Length caps

Measured as **plain text**: URL strings removed, markdown symbols (`| * # > ` [ ] ( )`) removed, whitespace kept.

| Item | Cap | Measured 2026-08-21 |
|---|---:|---:|
| Plain text | 10,500 characters | 10,472 characters |
| One raw markdown row | 300 characters | 200 characters |
| Issue-table rows / category | 8 | 8·8·8·8·6 |
| Schedule-table rows | 16 | 16 |
| 「한 줄」 cell | 90 characters | — |
| 「핵심 논거」 cell | 120 characters | — |

The baseline was 8,642 characters in the earlier version. The increase breaks down as: new 「다가오는 일정」 table +1,250; tone scale, collection time and coverage header +500; the four-block structure on every dispute +900; issue table compressed from 66 rows to 38 −1,400; **score column and meta section removed −2,300**. **No increase beyond these items is allowed.**

Reading time is **either computed or not written.** Formula: table-row characters ÷ 1,500 + prose characters ÷ 500 (minutes). Do not write a number with no basis.

---

## 6. Trust marks

Do not push them to the end of the document. A scan-style reader does not scroll to the end.

| Mark | Position |
|---|---|
| Collection time (`collected_at`, converted to KST) · publish window of the target | 3rd header line |
| Coverage per outlet (`fetched ÷ totalCount`) | 4th header line |
| Link check result (count / number of 200 replies) | 4th header line |
| `⟳` manual category correction | next to the item + under the tone blocks of that category |
| `⊘ 표본 내 미포착` | one line right under each tone table |
| Outlets with 0 editorials | named on the same line |
| 30-piece cut limit | one ⚠️ line in a category with 0 tone pieces + meta |

**Forbidden expression**: `미보도`. Always `표본 내 미포착`.

---

## 7. Failure modes

| Case | Criterion | Rendering |
|---|---|---|
| **Weekend or holiday** | the target date is Saturday, Sunday or a public holiday **or** `article_count < 120` | A badge in the header: `⚠️ 주말/연휴 — 발행량 {n}건(평일 대비 {n/210:.0%})`. Lower the issue-table row cap from 8 to **5**. If the collection gap between outlets exceeds 2×, add `⚠️ 매체 간 {k}배 격차` to the coverage line |
| **A category with 0 editorials** | that category's `E=0` | Do not delete the tone section. Write the heading as `## ② 논조 — **사설 0 · 칼럼 {C}**`, keep the four blocks, and write 「N개사 전체가 이 분야에 논평을 붙이지 않았습니다」 in `🕳️ 공백`. Also attach the 30-piece cut ⚠️ |
| **0 editorials and 0 columns** | `E=0 and C=0` | Same as above, but omit the table itself and leave only the four blocks (the 2026-08-21 국제 section was this case) |
| **No common agenda of 3 or more outlets** | `max(O) < 3` | Change the 「오늘 세 줄」 heading to **「오늘 눈에 띄는 것」**, apply the 40-point floor of §1-5, and give 1 to 3 lines. Put one header line `⚠️ 오늘은 3개사 이상이 함께 다룬 사안이 없습니다` |
| **A category with 0 articles** | 0 rows in that category | Keep the category heading and add one line `> 표본에 해당 분야 기사가 없습니다. (매체당 30건 컷)`. **Do not break the 5-category structure** |
| **0 schedule items** | no confirmed date within D+14 | Omit the whole schedule section (do not leave an empty table) |
| **Collection gate FAIL** | `normalize.py` check failed | **Do not produce a briefing.** Produce instead a one-screen report with only the failure reason, the collection status per outlet and retry guidance. A briefing built from partial data would destroy the meaning of "표본 내 미포착" |
| **Some outlets failed** | `outlet_stats[*].error != null` | Produce the briefing, but put `⚠️ {매체} 수집 실패` in the header coverage line, and mark that outlet separately (`미수집`) in the `표본 내 미포착` row of every tone table |

---

## 8. Lint of the output

A briefing must pass these before it is released.

1. Reference links: 0 undefined references, 0 unused definitions.
2. URLs: 0 containing `utm_`, 0 duplicates, 0 with path depth under 2 (home addresses).
3. HTTP: every URL 200 (concurrency ≤ 3). In the current implementation this is the shape check of the address.
4. Row width: 0 raw markdown rows over 300 characters.
5. Length: plain text at most 10,500 characters.
6. Structure: 5 categories present, and each has `## ① 이슈` and `## ② 논조`.
7. Tone: every dispute block has `축` and `대상`, has all four blocks ⚔️🤝🕳️🚫, and every tone table has a `⊘` line.
8. Forbidden words: `[보기]`, `미보도`, a `홈페이지` link.
9. Order: the issue table is sorted by internal score descending, and **no score number appears in the table**.
10. Schedule: every date is after the base date, and a source link exists.

---

## 9. Deterministic / non-deterministic boundary

| Deterministic (same input → same output) | Non-deterministic (LLM judgment) |
|---|---|
| Score calculation and sorting, rows per category, cutoff | the 「한 줄」 summary sentence |
| Link labels, ID assignment, the definition block | assignment of tone icons |
| Coverage, the sample table, meta numbers | the wording of a dispute's axis and target |
| Date sorting and 🔴 marks of the schedule table | the content of the four blocks ⚔️🤝🕳️🚫 |
| the **selection** of 「오늘 세 줄」 | the **sentences** of 「오늘 세 줄」 |

For the deterministic items, **run twice and confirm the results are identical.** The non-deterministic items sit inside the table structure, so any wobble shows at once. If a row order or an icon changes for no reason, a deterministic item has been contaminated.

---

## Appendix. What is left out of the output

By the user's decision the following are **not rendered.**

| Item | Handling | Reason |
|---|---|---|
| The priority score number | used only as an internal sort key | what the reader wants is the order, not the value |
| The 「메타」 section at the bottom | deleted | collection time, coverage and link check are already shown in the **2 header lines** |

**But these two must always stay.**

- **The reference-link definition block** (`[p1]: https://…`). It does not show in the rendering, but without it all 179 body-link references break.
- **The one source line** (the BigKinds credit + "원문 전재가 아님", not a reproduction of the original). It is the copyright-handling principle.

The limit notices that used to sit in the meta are **replaced by inline marks in the body.**

- `⊘ 표본 내 미포착 조선·매경·한경`: under each tone block
- `⟳`: next to an item whose category was corrected by hand

**So read the rule 「30-piece cut limit → meta」 in §6 as 「→ one ⚠️ line in each tone block」.**
