# Sources — which skill feeds which, and how `collect` reads it

This skill collects nothing itself. Eight sources reach the report; each comes from a collector skill that leaves a `gr.envelope/1` file in `data/<skill>/` (file names are `YYYY-MM-DD~YYYY-MM-DD_<words>.json`). `fiw.py collect` re-shapes those files into the run's `raw/collect/<source>.json` (`fiw.collect/1`), once, with `lib/from_envelope.py`; `SPECS` there is the table below. How each collector reads its site (URLs, markers, paging, robots.txt handling) is in that collector's own skill.

## Contents
- [Which skill feeds which source](#which-skill-feeds-which-source)
- [Conversion rules](#conversion-rules)
- [What the rows hold](#what-the-rows-hold)
- [When a source is missing](#when-a-source-is-missing)

---

## Which skill feeds which source

| Source | Collector skill | Envelope source starts with | Kept when (inclusion) |
|---|---|---|---|
| `bigkinds` (news) | `korean-news-search` | `bigkinds` (or `naver` when it ran its Naver substitute, see below) | `article_date` — inside the period |
| `korea_kr` (press releases) | `gov-notice-watch` | `korea.kr` | `posted` — inside the period |
| `lawmaking_web` (입법센터 notices) | `gov-notice-watch` | `국민참여입법센터` | `notice_open_overlap` |
| `mfds_notice` | `gov-notice-watch` | `식약처` | `notice_open_overlap` |
| `ftc_notice` | `gov-notice-watch` | `공정위` | `notice_open_overlap` |
| `assembly_bills` | `assembly-bill-tracker` | `assembly_bill` | `posted` — proposal date inside the period |
| `assembly_schedule` | `assembly-hearing-watch` | `assembly_schedule` | `upcoming_4w` — from today to 28 days ahead |
| `foodsafety` | `food-safety-records` | `foodsafety` | `record_date` — the kind's date field inside the period |

`mafra_notice` stays in the schema but nothing feeds it: the ministry board gives no comment period outside PDF/HWP attachments. MAFRA legislative notices come in through the 입법센터 source.

`notice_open_overlap`: kept when posted is inside the period, or the comment period is parsed and ends on/after the period start, or the period is not parsed and posted is before the period start (gate C8 then stops the run until a person resolves it — unless the title is non-food by keyword, see below).

## Conversion rules

- A source is read from **every** envelope of its collector skill whose period overlaps what the run needs (the run period; for the schedule, today … today + 28 days). Rows are united by id; a newer file wins.
- Envelope source status `ok` or `empty` is a finished request (`empty` = 0 items, allowed); `unread`, `failed` and `no_key` are failed requests.
- Days of the run period that no `ok` envelope covers become one failed request (source `partial`), so the source never looks complete when it is not.
- No overlapping envelope at all: nothing is written for that source, and `check` and `collect` report it as "not collected".
- Nothing is invented; a value the envelope does not carry stays null. Rows that fit no kind or id are skipped and listed in the `collect` notes.
- `len(requests)` counts envelope sources, not the pages the collector read; `received`/`total` are the envelope's counts.

## What the rows hold

- **News**: id, title, date, outlet, link; `queries` is the search words in the file name. The excerpt is empty — bodies for summaries come from the browser job in the browser-collection reference.
- **Press releases and notices**: id (the collector's prefix removed), department, title, date, link. For notices the comment period is the envelope's `open_to`; `open_from` is the earlier of posted and `open_to`. A notice without `open_to` has `open_parse: failed` (gate C8). Notice `kind` (재행정예고 > 행정예고 > 입법예고), `law_type`, `dup_key` and `family_key` are derived here by `lib/notices.py`; a row that names no notice kind is skipped.
- **C8 and non-food notices**: an unparsed period exits 1 only when the title may be food-related. When `fiw.py prefilter` rules would mark the title `other` (an exclude term and no include term — agency names such as 식품의약품안전처 are blanked first), C8 is a warning (exit 3): the item appears only under 「그 밖」 by title and link, and the report must show it without a period (「의견 기간 미확인」).
- **Bills**: bill id (from the link's `billId`), name, proposer, proposal date, committee, stage (may be empty — the collector's list result has no stage), link.
- **Schedule**: date, time, committee, meeting name, link (may be empty; say 「확인 필요」 rather than presenting a front page as a meeting page). One meeting listed under two ids is one item.
- **Food-safety records** (allow-list only): kind (recall / admin / unfit), seq, company, product, reason, grade, action, date, detail link, `personal` (company masked with `*` or `○` — an individual), `tracked_company` (first `config/watch.json` company matched on company or product). Officials' names, staff IDs, phone numbers and representatives' names are never carried over. For `foodsafety`, `received < total` is normal (the portal's total counts every record ever), so gate C6 relies on the request status instead.

## When a source is missing

`collect` prints `<source>: not collected — no <skill> result in data/ overlaps A~B` and exits `3`. The fix is on the collector side: run that skill for the run's dates (SKILL.md step 5), then `collect` again. If the collector itself reports `no_key` or `failed`, say so in the report (⚠) and do not fill the section from anywhere else — with one labelled exception, the Naver substitute below. Filling a section silently from another source stays forbidden.

### The Naver substitute (BigKinds down)

While BigKinds is under maintenance, `korean-news-search` may write a 「네이버 대체판」 envelope: its only source is `naver` with the error 「네이버 대체판 — 본문 미확인 (빅카인즈 대체: <reason>)」, `warnings[0]` is that notice, `warnings[1]` says 「다시 모아야 함」, rows have no summary and their ids start `naver-`. `collect` takes such an envelope (status `ok`/`empty` only) as news with these limits:

- The `bigkinds` source becomes `partial`, gets one `partial` request for the substitute and a `fallback` block (`reason`, `days`, `recollect_required: true`). Days that a BigKinds envelope covers keep the BigKinds rows; only the rest come from the substitute. Substitute days are not read days: they give no coverage and no dormancy.
- Substitute rows are never body-fetched, summarised, quoted, counted as evidence for new / progressed / dormant, or written to state. `prepare` lists an issue that has only substitute rows as title / link rows (「뉴스(네이버 대체판) 확인 필요」) and does not count it as a lost body.
- The report says so in the header (「뉴스(네이버 대체판) K건 본문 미확인」), in a ⚠ note with the days and 「빅카인즈 복귀 뒤 정식 재수집 필요」, and in the credit (「네이버 뉴스 검색 API(네이버 대체판 — 본문 미확인)」; BigKinds is not credited when it sent no row). Gate E18 fails any summary text built on a substitute row; lint fails a report that drops the note or the credit.
- `publish` records `recollect_required` on the run. Collect the period again once BigKinds is back.
- A `daily-news-brief` envelope is never a news source here, substitute or not.
