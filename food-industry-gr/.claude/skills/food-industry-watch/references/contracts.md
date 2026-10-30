# Data contracts

Every JSON file the skill reads or writes carries `"schema": "<family>.<name>/<version>"` (`fiw.` for this skill's own files, `gr.` for the files it shares with the collector skills). `scripts/lib/schema.py` holds the authoritative definitions (field types, enums, strict keys); this file explains what the fields mean and who writes them. A reader that finds another version exits `2`. Objects are strict — an unknown key is an error, so add fields by changing the contract, not by writing extra keys.

## Contents
- [Data folder](#data-folder)
- [Period report](#period-report)
- [Exit codes](#exit-codes)
- [Run files](#run-files)
- [Collect envelope](#collect-envelope)
- [Clusters, relevance, todo](#clusters-relevance-todo)
- [Summary](#summary)
- [Summary in parts](#summary-in-parts)
- [Gate result](#gate-result)
- [State](#state)
- [Config](#config)

---

## Data folder

The output folder is `GR_HOME` if set, otherwise `output/` in the working folder. This skill owns the `food-industry-watch` part of each top folder and reads the collector skills' `data/` folders:

```
data/<skill>/                       gr.envelope/1 results of the collector skills — read here, never written
config/   watch.json                shared by all skills (gr.watch/1): companies, agencies, keywords
          food-industry-watch.json  this skill's settings (fiw.settings/1): provisional, bigkinds, stage1, format
state/food-industry-watch/
          plan.json · runs.json · coverage.json · doctor.json · issues.json · notices.json · hygiene.json · seen_ids.json
          snapshots.json            fiw.history/1 — the text of state/ before every publish, correct and state
                                    merge/split/lock/undo, with rev, time, command and run. Keep it: a run with a
                                    later published run is re-rendered from the snapshot the first later publish
                                    took (`correct` exits 1 when that snapshot is gone)
          config-base.json          the shipped defaults the two config files were last reconciled with
          format-history.json       earlier report formats (newest restored by `format undo`)
runs/food-industry-watch/{run_id}/  one collection run (the 「원본」): collection, relevance, summary, verification
  run.json                          fiw.manifest/1
  raw/collect/{source}.json         fiw.collect/1, made by `collect` from data/
  work/                             clusters · prefilter · relevance · todo · summary · dead_links · parts/ ·
                                    corrections/{n}.json (fiw.correction/1) · logs/ · gates/{gate}.json
    report.md                       the run's own report; publish records it in runs.json, nothing is copied.
                                    A correction rewrites it in place (no …_r2 copies)
    build/{from}~{to}/              the period report's scratch folder, laid out like a run folder (work/ inside it
                                    holds its files and gates) — rebuilt on every `fiw report`
reports/food-industry-watch/{from}~{to}.md   period reports (`fiw report`), the only files the user reads
```

Older text and some messages call a run folder a "raw" (「원본」); it is the same thing. `run_id` = `{run date}-{n}` (`2026-10-26-1`); `n` grows within a day, a run folder is never reused. The two config files are copied from `assets/defaults/` on first use; `fiw doctor`/any command reconciles them against newer defaults without overwriting a value the user changed (`fiw config` shows differences, `--take-default KEY` adopts one default). Hosts the scripts never request or keep click-only, and the robots.txt seeds, are in `assets/defaults/link_policy.json` (`fiw.link_policy/1`, shipped, not copied).

**What may go after publish.** Ask the user first, then move (never `rm`) to a `.trash/<run_id>/` folder inside the output folder, only after `publish` and `prune` exited `0`: `work/body_requests.json`, `clusters_brief.md`, `clusters_view.md`, `items_view.md`, `relevance_brief.json`, `summary_head.json` and `logs/` (empty after `prune`, which deletes its stage logs). They are views or briefs that their step writes again, and no gate hashes them (gates hash `relevance.json`, `summary.json`, `todo.json`, `report.md`, `raw/collect/bigkinds.json`). Everything else in a run folder and in `state/food-industry-watch/` stays: period reports, `correct` and `state check` read it. The collector skills' `data/` folders are never touched.

**Seed** (`fiw seed export`) = the run folders, the state folder and the two config files, laid out as under the output folder, without hidden files and this machine's app check. Period reports are not part of it; the receiving PC builds them with `fiw report`. Export and import refuse a seed with article text in any JSON field or with a run that `prune` has not emptied. A seed in the pre-collector folder layout (`raw/`, `state/`, `config/` at the top, like the bundled `seed/`) is read with `fiw import-legacy --from <folder>`, not `seed import`.

## Period report

`fiw report --from A --to B` builds `reports/food-industry-watch/A~B.md` from the runs (the 「원본」). The runs are never rewritten. `<build>` below is `<run folder of the newest run>/work/build/A~B/`.

1. **Runs.** Every published run whose period overlaps A~B, oldest first. A starting A before the earliest run is not an error: the report prints 「미수집 — A~(earliest−1): 원본 없음」 and nothing about those days. Otherwise it stops with one of these:

   | Exit | Message | Do |
   |---|---|---|
   | 3 | `no raw yet — collect first` | SKILL.md steps 1–11, then `report` again |
   | 3 | `the raws end at X — collect X+1~B first` | (`period` proposes X~B: it reads the boundary day X again and `seen_ids` drops the repeats — follow `period`) `doctor`, `period`, `run new` (or `run new --from X+1 --to B` when B is before today), steps 4–11, `report` again |
   | 3 | `no raw covers A~B` | the period lies before every run: collect it as a run, or ask the user for other dates |
   | 3 | `gap F~T between raws — collect it first` | `run new --from F --to T`, steps 4–11 |
   | 3 | `<run>: not published` / `<run>: verify (or relevance) gate is not a pass` | finish that run (its pipeline through `publish`) |
   | 3 | `write 세 줄 from <brief> …` | step 6 below |
   | 1 | lint or period findings | a 세 줄 finding: fix the file; anything else is a run problem — fix the run (`correct`), never the period report |
   | 1 | `dead links found — … rerun the same command` | rerun the same command; the links are recorded and left out |
   | 3 | linkcheck could not check some links | the report **is** written; show it with the linkcheck line |
   | 0 | `report: <path of reports/food-industry-watch/A~B.md>` | show it |
2. **Articles.** One article id counts once across runs. Refs are renumbered for the period (`{cid}:{n}` again, unique within it).
3. **Sentences.** A `fact` sentence is kept only when every ref's article date is inside A~B. An `edit` (🧭) sentence is kept only when every issue in its `links` still has a kept sentence (repeated until nothing changes). Kept sentences are exact copies, in run order; duplicates by text are dropped. Relative dates stay as written (「지난 8일」, 「31일 확인했다」 — rule 2 forbids adding a month). In every period report the renderer puts the article days of the sentence's refs before its final period (「…고시했다(10-08 보도).」) on every sentence with a day but no month (「23일」, 「지난달 23일」 — the article day tells the reader which month), and lint fails a line that still has such a day without that basis (dated title lines `- MM-DD …` and day-count cells are exempt).
4. **Title.** The newest run's title whose refs all lie inside A~B; otherwise the newest title with its outside refs removed (counted `title_refs_trimmed`). An issue with no title ref inside the period is a FAIL.
5. **No kept sentence.** The issue leaves the report; its clusters become `other` and show under 그 밖. A market topic likewise.
6. **세 줄.** The first run writes `<build>/work/three_lines_brief.json` (`want` = min(3, issues), issues sorted by impact — outlets + 3 for a sanction (a government (`gov`) issue whose title names an authority's own action against companies: 현장조사·적발·심의 개시·고발·송치·압수수색·기소 (not 조건부 기소유예)·시정명령·영업정지 처분·과징금 … 부과/처분 (not 부과율); a word that only names the subject — 「담합 손배소」, 「과징금 예시로 … 거론」, 「영업정지 … 추진」 — does not count, nor does an Assembly issue: its 「적발 1건」 is a lawmaker's figure, not an action of the period) + 2 for the own company (a direct 롯데웰푸드 issue), then articles, flow candidates and the newest day; take the first `want` — each with its kept sentences) and exits `3`. Claude writes a JSON list of `want` entries `[{issue, head {text, refs}, sentences}]`, one per issue: `sentences` are exact copies (`text`, `kind`, `refs`) of that issue's kept sentences — one or two read best, no `evidence` field (the copies keep the evidence their run checked); `head` uses only facts in them (every number, date, name and quote), its refs the union of the refs of every copied sentence that holds something the head says (a number found only in one sentence → that sentence's refs must be among them). Words of the issue title may be used when the head's refs include a ref of the title: the head is checked against the kept title, sentences, actors and flow nodes of its refs. In the brief, a run's `new:{cid}` key appears under the issue id `publish` gave it (`new:c0005` → `I-0077`) and refs carry the period's cluster ids (`c0005:1` in the run may be `c4414:1`): use only the keys and refs printed in the brief. Save it as `<build>/work/three_lines.json` and rerun with `--three-lines` that file. The head has no `evidence` field: it is checked for numbers, dates, quotes and names against the kept summary text of its refs (bodies are pruned by then), and the limiting and judgment words of the writing-rules reference, rules 5–7, are not checked there — leave them out of the head unless a copied sentence has the same word. No relative date (「지난 8일」, 「31일」, 「지난달 23일」) in a head, and `report` fails a head day without its month. A date in a head must be written as the kept text writes it with its month (「10월 8일」 is checked as written): when the kept sentences say only 「지난 8일」 or 「지난달 23일」, leave the day out of the head — never add a month or work one out from the article date. This 세 줄 is not the run's `summary_head.json` (the writing-rules reference, § Summary head), which carries its own evidence.
7. **Status**, relative to A, from `issues.json` `first_seen`: 🆕 first reported inside the period; ▶ reported before A and inside A~B — the cell reads 「첫 보도 MM-DD · 이번 기간 MM-DD~MM-DD」; issues reported only before A go under 「기간 전 쟁점 — 이번 기간 새 기사 없음」 or 💤.
8. **Gates**, in `<build>/work/gates/`: `lint`, `linkcheck`, and `period` (`fiw.gate/1`), which holds the evidence checks of the merged summary and the 세 줄 heads plus the period checks: a ref outside A~B, an article id listed twice, a 세 줄 sentence that is not a kept one, a 「미수집」 line missing or out of place. When lint and period exit `0` and linkcheck finds no dead link (exit `0` or `3`), the report is written to `reports/food-industry-watch/A~B.md` and `report` returns linkcheck's exit; `classified` of `period` counts what was left out and why (`dropped` in the output): `collect_duplicate_item` (a primary-source item already taken from an earlier run), `article_in_two_raws`, `cluster_outside_period`, `sentence_ref_outside_period`, `sentence_dead_source`, `sentence_e17_at_period_end`, `edit_link_lost`, `issue_no_sentence_in_period`, `title_refs_trimmed`, `market_topic_no_sentence`. They are bookkeeping, not findings: do not show them to the user unless asked. 💤 counts BigKinds-covered days up to B only.

Corrections go to the run (`correct --run <id>`); then rebuild the period report.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every check actually ran; no violation. Classified results (link blocked by 403, robots-exempt) are counted, not hidden |
| 1 | FAIL — do not publish |
| 2 | Usage or input error: unknown flag, bad date, wrong schema version, missing file |
| 3 | WARN or a user decision is needed — publish with the warning shown, or ask the user |

Skipping a check is never exit 0. Evidence, judgment, state and format gates treat a skipped check as `1`; collection and link gates as `3`.

## Run files

- `fiw.manifest/1` — `period` (inclusive), `period_source` (`plan` segment, `incremental` since last coverage, or `explicit` dates), `plan_segment`, `stages` (per stage: status, exit, input hash, start and end time — `started_at` is absent in older runs), `sources` (per source: status and the dates actually read). Commands write it under `.manifest.lock` and copy only their own entries over the file on disk, so a background `collect` and `ingest` can run at once.
- `fiw.plan/1` — monthly segments of a multi-month span: the first run's quarter (D53) or a catch-up after a long gap. A segment is `pending` → `running` (run created) → `done` (report published).
- `fiw.period/1` — output of `fiw.py period`: `ready` with a period, or `needs_decision` with the options to put to the user.

## Collect envelope

`<run folder>/raw/collect/{source}.json`, `fiw.collect/1`. One file per source, written by `collect` from the collector skills' `gr.envelope/1` files in `data/<skill>/` (`lib/from_envelope.py`; the sources reference has the mapping and the conversion rules). Nothing else writes it.

- `inclusion` says how items are matched to the period, because dates mean different things per source: `article_date` / `posted` / `record_date` = date inside the period; `notice_open_overlap` = comment period overlaps `[from, ∞)` or posted inside the period; `upcoming_4w` = run date ≤ date ≤ run date + 4 weeks.
- `status`: `ok`, `partial` (some envelopes failed, or days of the period no envelope covers), `failed`, `no_key`. There is no "skipped".
- `probe`: always `null` — the envelope's own `ok` / `empty` status tells "read nothing" apart from "nothing to report".
- `requests[]`: one per envelope source (not per page the collector read; `pages` carries that count). `status` is `ok` (the envelope was `ok` or `empty`) or `failed` (`unread`, `failed`, `no_key`); `total` / `received` are the envelope's counts. Days of the period that no `ok` envelope covers add one failed request, source `partial`. A `received` below `total` is a truncation, except for food-safety records. No request carries a key-bearing URL.
- `basis` (food safety only): which date field each kind was counted by.
- `items[]`: fields depend on `source` (see `schema.py`). Notice items carry `open_from`/`open_to` with `open_parse`, the extraction source and the exact source fragment (`open_evidence`). Food safety items keep an allow-list of fields only; officials' names, phone numbers and representatives' names are never carried over from the envelope.

## Clusters, relevance, todo

- `fiw.clusters/1` (script) — rule-stage counts, clusters with `core_ids` (articles close to the representative; tags and date spans are computed from core only), and every rule-dropped article with its rule. `auto` is `null` for clusters that need Claude's review (they are in `work/clusters_brief.md`, one line each, and `work/clusters_view.md`, up to five titles each) and a reason string for clusters judged `noise` automatically. `prefilter` also writes `work/items_view.md`: one line per item that needs a relevance entry.
- `fiw.prefilter/1` (script) — keyword verdicts for press releases, notices and bills (`include` / `other` / `undecided`, with the rule).
- `fiw.relevance/1` (Claude) — a verdict for every reviewed cluster, and for every prefilter item that is `undecided` or an `include` of press releases, notices or bills (press `include` needs a `category`). `issue_link` picks an existing issue id, `new`, or `new:<cid>` — joining a new issue of the same run: `<cid>` must be an `issue` of this run with `issue_link: new`, outside `market`, in the same section; its articles count toward that issue (one todo issue `new:<cid>`, one row). Gate J (`relevance --check`) records the file's sha256; later steps refuse a stale or failing record.
- `fiw.relevance_brief/1` (Claude) — compact authoring form compiled by `relevance --compile FILE` into `fiw.relevance/1` (then gate J runs): `clusters` (issue / other entries only; `issue_link` defaults to `new` for an issue), `noise` groups `{reason, cids}`, optional `noise_rest {reason, last_cid, count}` (all other reviewed clusters; `last_cid` must be the last reviewed cid and `count` match, else exit 2), `items` (include entries), `items_other` groups `{source, reason, ids}`, optional `items_rest [{source, reason, count}]` (all other items of the source that need an entry). Duplicates are kept so the gate reports them.
- `fiw.body_requests/1` (script, prepare step) — `work/body_requests.json`: the BigKinds NEWS_IDs whose bodies the browser job `bigkinds_bodies` fetches.
- `fiw.bodies/1` (`ingest`) — `work/bodies.json`: fetched bodies `{id, title, outlet, date, url, body, chars?}` (`chars` = the length the page showed, when the collector could read it) merged by id, and `errors[]` `{id, status, detail}`. Working data only; removed by prune.
- `fiw.correction/1` (Claude) — one correction of a published run: `request` (the user's words), `issue` (summary key, state id, `market:{topic}` or `three_lines:{n}`), `field` (path inside that entry, e.g. `sentences[2]`, `title`, `lwf.sentences[0]`; index = length appends, `new: null` deletes), `new` (the value as in summary.json, with refs), `refs` (≤ 10, each used in `new`), `evidence` (`{ref, quote}` exact from the source, ≥ 1 unless deleting). `correct` re-requests a pruned flow candidate by its BigKinds id, taken from this run's export or, failing that, from earlier runs' exports (newest first, exact URL match — no URL normalising).
- `fiw.todo/1` (script) — the work order for writing. Article bodies exist only here. `ref` = `{cid}:{n}`, unique within a run; a summary field may cite only refs of its own issue. `excluded_no_link` lists issues dropped because every source article lacks a link; `excluded_no_body` those whose source bodies could not be fetched (both are printed in the report). `lwf_mentions` = whether the script found the own company in each article body. Each issue has `coverage {outlets, articles}`: the outlets and articles of the core of all its clusters — the same count as the report's 「N사 M건」. Each article has `body_check {chars, src_chars, ends_sentence}`: prepare leaves out a body that looks cut (shorter than `src_chars`, or ending mid-sentence where that is reliable) and asks for it again; verify's E15 fails a todo body that still looks cut or whose length changed.
- `fiw.dead_links/1` (`linkcheck`) — `work/dead_links.json`: `links[] {url, status, key, first_seen}`, every URL `linkcheck` saw answer 404/410 (`key` = its reference key in that report). It only grows (union with the existing file, nothing is removed), so a link stays out after a later report no longer defines it. `render` leaves these URLs out of every link it draws and prints `render: N dead link(s) left out (linkcheck)`; it exits `1` (nothing written, the row named) when an issue row or a market row would be left with no link. `verify` E16: a field, sentence, flow node or `lwf` ref that cites a source whose URL is in the file. No file means none of this applies. (`verify`, `parts check` and `correct` also take `manifest.period.to` for E17: a 「오는 [M월] D일」 that, counted from the latest date among the text's refs, falls on or before it.)

## Summary

`fiw.summary/1`, written by Claude following the writing-rules reference. Every narrative field carries refs.

- `fact` sentence: refs ≥ 1; all evidence checks apply.
- `edit` sentence (shown with 🧭): only grouping, flow linking or classification judgments; `links` (the issues it connects) required, refs ≥ 2, checked against the union of the linked issues' refs; it may not introduce names or numbers absent from those refs.
- `evidence[]`: exact fragments (whitespace-normalised, ≤ 80 chars) for risky expressions — limiting phrases, modifiers, speech verbs, multi-number/multi-entity sentences.
- `unverified: true`: rendered with 「(미확인)」; the gates still run.
- Claude never writes computed values: `N사M건` tags, company tags, date spans, header counts, remaining days, the open-comment list, hygiene counts, footnotes. The renderer computes them.

## Summary in parts

`parts split` writes `work/parts/`: `parts.json` (`fiw.parts/1`: `todo_hash`, `max_chars`, `parts[] {part, issues, market, refs, chars}`), `index.md` (a part table, then an issue table — key, todo `coverage` as 「N사 M건」, refs; most outlets, then articles first; no bodies), and per part `NN/todo.json` (`fiw.todo/1`, that part's issues and topics only) and `NN/bodies.md` (one `### ref — title (outlet, date)` header per body, one sentence per line, no line over 500 chars). The writer adds per part:

- `NN/facts.json` (`fiw.facts/1`) — `articles[] {ref, facts?: [{text, quote}], unused?}` for every body of the part in any order, `last_ref` (the part's last ref) and `count` (its number of bodies). Each body has 1–3 facts or `unused` with the reason, not both.
- `NN/summary.json` (`fiw.part_summary/1`) — `issues` and `market` entries exactly as in `fiw.summary/1`, for this part's keys and topics only (no `three_lines` or `notices`: those are `summary_head.json`).

Both files start with `"schema"` and `"part"` (`"01"`, the folder name).

| field | type | rule |
|---|---|---|
| `issues[].key` | str | a todo issue key of the part (`I-0050`, `new:c0021`) |
| `issues[].title` | `{text, refs}` | one line; `locks.rename` exactly when set |
| `issues[].actors?` | `[{text, refs}]` | agencies, companies, lawmakers named in the refs |
| `issues[].sentences` | `[sentence]` | `{text, kind: fact\|edit, refs, evidence?: [{ref, quote}], links?, unverified?}`; `edit` needs `links` and ≥ 2 refs |
| `issues[].flow_name?` | `{text, kind: "edit", links}` | only with `flow` |
| `issues[].flow?` | `[{ref, text, mode: title\|summary}]` | the writing-rules reference, § Flow nodes |
| `issues[].lwf?` | `{direct, sentences?, ref?, not_shown_reason?}` | the writing-rules reference, § Own company |
| `market[].topic` | str | a todo market topic of the part |
| `market[].sentences`, `market[].lwf?` | as above | |

```json
{"schema": "fiw.facts/1", "part": "01", "last_ref": "c0002:2", "count": 2,
 "articles": [{"ref": "c0002:1", "facts": [{"text": "청소 중 근로자 2명이 쓰러져 1명이 숨졌다", "quote": "<12자 이상, 이 본문 그대로>"}]},
              {"ref": "c0002:2", "unused": "같은 사고 사진 기사"}]}
```
```json
{"schema": "fiw.part_summary/1", "part": "01",
 "issues": [{"key": "new:c0002", "title": {"text": "…", "refs": ["c0002:1"]},
             "actors": [{"text": "매일유업", "refs": ["c0002:1"]}],
             "sentences": [{"text": "…", "kind": "fact", "refs": ["c0002:1"],
                            "evidence": [{"ref": "c0002:1", "quote": "…"}]}],
             "lwf": {"direct": false}}],
 "market": []}
```

`prune` deletes every `NN/bodies.md`, empties the bodies and excerpts of every `NN/todo.json` and the `quote` of every fact (the fact `text` stays); `parts.json`, `index.md`, `summary.json` and `check.json` stay.

`parts check --run <id> --part NN` writes `NN/check.json` (`fiw.gate/1`; `input_hash` covers `todo_hash` and the part's three files): F0 todo changed since split · F1 `count` / `last_ref` wrong · F2 a body missing, repeated or not of the part · F3 neither or both of facts / unused, or not 1–3 facts · F4 a quote not in its own body (`섞임` names the body it came from, else `근거 없음`) · F5 a quote under 12 chars · F6 summary entries ≠ the part's, or citing a body marked unused · F7 a number in a fact's text that its quote lacks — then every evidence check of the part's entries and E15. `parts merge --run <id> --head work/summary_head.json` (`fiw.summary_head/1`: `three_lines`, `notices`) writes `work/summary.json` in todo order only when every part's check exited `0` on its current files.

## Gate result

`gates/{gate}.json`, `fiw.gate/1`: `exit`, `checked` (how many fields or items were examined), `findings[]` (`where`, `kind`, `detail`), `classified` counts, `skipped[]`, and the hash of the input it judged (relevance → `relevance.json`, verify → `summary.json`, lint and linkcheck → `report.md`). Rendering refuses a summary whose current hash has no passing evidence-gate record; publishing refuses unless check, relevance, verify, lint and linkcheck all passed on the current files. `verify` on a published run whose todo bodies and excerpts were all pruned exits `2` and writes neither the gate nor the manifest stage; such a run is changed only through `correct`.

## State

- `fiw.state.runs/1` — every run with its period, `period_source`, report path (`<run folder>/work/report.md`) and gate summary; after publish also `issue_ids` (summary key → state id).
- `fiw.state.coverage/1` — per source, the periods actually read. Failed sources are not recorded, so the next run reads them again. The next incremental period starts at the earliest "last covered date" across sources (inclusive); duplicates are removed by id.
- `fiw.state.doctor/1` — the app check result (file name only, never a full path), the longest marker verified intact (`marker_len`) and the export chunk size that follows from it (`chunk_chars`: 150,000 / 500,000 / 1,000,000 — the largest whose chunk plus ~170,100 characters of header and padding fits in `marker_len`). Older records lack both and mean 150,000.
- `fiw.state.issues/1` — issues with stable ids `I-0001` (merged ids stay as `aliases`), flow nodes (article id, URL, outlet, verification time, run) and last sentences with their article ids, user locks, corrections, `last_run`. `locks[]` = `{type, value, by: "user", at}`, set only by the user: `no_merge_with` (value: an issue id — never link a cluster that shares an article with that issue's flow), `exclude_pattern` (value: text — never link a cluster whose representative title contains it), `rename` (value: the title the summary must use; todo `locks.rename`), `pin_section` (value: a section — the issue stays there and only clusters of that section link; todo `locks.pin_section`). The latest lock of a type wins. `status` is `dormant` when the issue was not updated in the latest published run and BigKinds-covered days after its last article ≥ `dormancy_weeks × 7` (uncovered days do not count).
- `fiw.state.notices/1` — included notices still open (`open_to` ≥ the run date), carried into later ⏰ lines until their deadline; `first_run` is the run that found them.
- `fiw.state.hygiene/1` — per run: recall / admin / unfit counts, recall grades, date basis (the public lists drop old records).
- `fiw.state.seen_ids/1` — the item ids of the latest published run per source; the next run's `cluster` drops those BigKinds articles (`seen:{run}`) because the boundary day is read again.
- `fiw.history/1` — `snapshots.json`: the earlier text of the state files, one entry per snapshot (`rev`, time, command, run).
- `fiw.py state check` (S gate) validates all of these and recomputes dormancy; publish restores the snapshot when it fails.

## Config

- `gr.watch/1` — `config/watch.json`, shared by all skills: tracked companies (with `requires_any` for names that are ordinary words), associations, agencies, committees, BigKinds queries with the sections each query feeds (a failed query marks those sections), press-release and legislation department codes, rule-stage words, food-relevance keywords.- `fiw.settings/1` — `config/food-industry-watch.json`, this skill's own settings: `provisional` (parts still to be confirmed by measurement), `bigkinds`, `stage1` and `format`. The old `watch.json` keys for sources that now belong to the collector skills (`assembly`, `korea_kr`, `lawmaking`) are gone.
- `fiw.watch/1` and `fiw.format/1` — not files: the views `load_watch()` and `load_format()` build in memory from the two files above, so the rest of the code reads one watch object and one format object. `format` holds sections (id, enabled, order, heading level, title, columns, row cap), sentence limits, articles per issue and dormancy weeks; `{own_company}` in a column title is replaced with `own_company` of the shared file.
- `fiw.link_policy/1` — `assets/defaults/link_policy.json`, read from there (not copied to the output folder): hosts never requested, hosts kept as click-only links, and robots.txt seeds for hosts that refuse scripted robots requests.
