---
name: food-industry-watch
description: "Builds a Korean food-industry government-relations (대관) report for any period by orchestrating the collector skills (news, ministry press releases and notices, National Assembly bills and schedule, food-safety records) and then judging relevance, carrying issues forward (new / progressed / dormant) and writing summaries, with every row linked to its source and every sentence checked against the source text. While BigKinds is down it takes the Naver fallback news as title/link rows only (body unverified). Keeps each run and builds period reports from the runs. Use when the user asks for (1) the food-industry or 대관 trend report — e.g. \"식품업계 동향\", \"식품 대관 동향\", \"대관 현황판\", \"업계 동향 정리\", \"food industry watch\", (2) a period — \"최근 2주\", \"최근 N주\", \"최근 N개월\", \"3개월 종합\", \"분기 종합\", \"이번 달\", \"기간 지정\" (dates), or catching up since the last report, (3) changes to a previous report of this skill (corrections, format changes), or (4) first-time setup, environment check, or moving data from the old folder. Not for a single source on its own — use the collector skill (korean-news-search, gov-notice-watch, assembly-bill-tracker, assembly-hearing-watch, food-safety-records)."
---

# Food Industry Watch

Produce a Korean report on the food industry's regulatory and political environment for the period the user asks for. This skill **does not collect** news, notices, bills, schedules or safety records itself. The collector skills do, and each leaves a `gr.envelope/1` result in `data/<skill>/`; `fiw.py collect` only reads those results. Each run is kept (`runs/food-industry-watch/{run_id}/`: collection, relevance, summary, verification) and carries the issues forward; the user reads **period reports** (`reports/food-industry-watch/{from}~{to}.md`) built from the runs. Scripts do bookkeeping, counting, rendering and all checks; Claude reads, judges relevance and writes summaries under the evidence rules. **Reports are written in Korean** (section titles and table headers come from the `format` part of `config/food-industry-watch.json`); these instructions are in English.

Never assemble or edit a report or a state file by hand: every change goes through the commands below and their gates.

## What the user does

Confirm the period once (first run, or after a long gap), open a few 「확인할 곳」 links at the end, and say what to fix. Claude does everything else.

## Running the scripts

- `PY` = `python3` on macOS; on Windows the first of `py -3`, `python3`, `python` that works (`references/first-run.md` §1). Always call `PY -X utf8 "<skill>/scripts/fiw.py" <command>` on Windows, `PY "<skill>/scripts/fiw.py" <command>` on macOS, with the absolute path of this skill's folder as `<skill>`. One command per tool call (no `&&`, `||`, `;`).
- Exit codes: `0` ok · `1` FAIL (fix and rerun; never publish) · `2` wrong command or input (fix the command) · `3` warning or a user decision is needed (read the output, tell or ask the user). Unknown or abbreviated flags are rejected — use the exact names from `--help`.
- Everything is stored under one output folder: `GR_HOME` if set, otherwise `output/` in the working folder. Layout: `data/` (collector results, read only here), `runs/food-industry-watch/`, `state/food-industry-watch/`, `reports/food-industry-watch/`, `config/` (`watch.json` shared by all skills, `food-industry-watch.json` for this skill). A copy of this skill kept outside the working folder needs `GR_HOME` set (`env GR_HOME=<folder> PY …` for another folder). Never edit these files by hand.
- Add `--json` for machine-readable output.

## Workflow

Two parts: **update the runs** (steps 1–12) until they reach the end of the asked period, then **build the period report** (step 13). For a period request (「최근 2주 정리해줘」), say the dates back first, then try step 13; if it stops with "raws end at", run steps 1–12 for the missing days and step 13 again. Steps 5–13 have their exit codes and briefs in `references/run-steps.md`; read the matching section before each of them.

1. **Environment** — `doctor`. On the first run on a machine, or when it does not exit `0`, follow `references/first-run.md`. Do not collect anything while `doctor` exits `1`.
2. **Old data** — only if `~/.claude/food-industry-watch/` exists and `state/food-industry-watch/` is empty: `import-legacy` (a dry run that copies nothing), show the user what it would copy, and run `import-legacy --apply` only after a yes. It copies, never deletes (`references/later-runs.md` § Moving from the old folder).
3. **Period** — `period`.
   - Exit `0`: the period to run is printed.
   - Exit `3` with `first_run_quarter`: no data yet. If the skill's `seed/` is wanted, import it first (`references/later-runs.md` § Sharing a starting point). Otherwise ask the user, in Korean, whether to cover the proposed quarter as three monthly runs (show the three date ranges from the output). On yes: `period --accept-quarter`. On a different wish, use `run new --from … --to …` with the dates they give.
   - Exit `3` with `gap_over_quarter`: the last covered day is more than a quarter ago. Ask: "N개월 공백이다. 전부 따라잡을까, 최근 분기만 볼까?" — then `period --catch-up all` or `period --catch-up recent-quarter`.
4. **Run** — `run new` creates the run for the period from step 3 (or `--from/--to`). `run show --run <id>` prints its manifest. Every later command takes `--run <id>`.
5. **Collector skills** — for the run's dates, run the five collector skills, each with its own instructions, so that each leaves its result in `data/<skill>/`: `korean-news-search` (BigKinds news), `gov-notice-watch` (press releases and notices), `assembly-bill-tracker` (bills), `assembly-hearing-watch` (committee schedule), `food-safety-records` (recalls, actions, failed inspections). Which skill feeds which source: `references/sources.md`. Run the ones whose result does not yet cover the period; a result that already overlaps it is reused.
6. **Collect** — `collect --run <id> --source all`. It makes no web request: it reads the results in `data/`, keeps the rows of the run's period, and records every request as finished or failed. Exit `3` means a source is missing or failed — read which, and run that collector skill again, or carry on and let the report say 「확인 필요」. When the news came from the 「네이버 대체판」 of `korean-news-search` (BigKinds under maintenance), `collect` prints `fallback: Naver substitute …` and exits `3`: tell the user, carry on, and plan to collect the period again once BigKinds is back (`references/sources.md` § The Naver substitute).
7. **Up to the judgement** — `pipeline --run <id> --until judge`.
8. **Relevance** — one subagent judges all clusters (`references/relevance-rules.md`).
9. **Work order** — `prepare`, fetching news bodies until it stops asking (`references/browser-collection.md`). Naver substitute rows are never asked for; an exit `3` with only 「뉴스(네이버 대체판) 확인 필요」 lines needs no fetch.
10. **Summary, in parts** — `parts split`, one subagent per part (`references/writing-rules.md`), then write `work/summary_head.json` yourself and `parts merge --run <id> --head work/summary_head.json` (the path is relative to the run folder).
11. **Report** — `pipeline --run <id> --from summary`, through `publish`. Fix dead links as the reference says.
12. **Check list** — `review --run <id> --risk`, shown as 「확인할 곳」. Then, once the user has no more questions, `prune --run <id> --chunks "<tool-results folder>" --apply`; it must print `본문 0건`.
13. **Period report** — `report --from A --to B` for the asked period (「최근 2주」, 「3개월」 or dates; mapping in `references/run-steps.md`); it asks once for 「세 줄」 written from its brief. Never rewrite a run's sentences for it.

For the next run, a wrong line (corrected in the run, then the period report rebuilt), a format change or issue merges, read `references/later-runs.md`.

## Rules that always apply

- Never state anything unverified as fact. Mark it 「미확인」 or leave it out. A report with one invented detail is worse than a shorter report.
- A collector that failed is reported as failed (⚠), never filled silently from another source. The only substitute allowed is the labelled 「네이버 대체판」 news while BigKinds is under maintenance: its rows appear by title and link only, marked 「확인 필요」, never as evidence for a summary, a quote or new / progressed / dormant, and the report names it in the header, a ⚠ note and the credit (the scripts and gates enforce this).
- This skill makes no web request of its own except fetching BigKinds article bodies for summaries in the built-in browser (`references/browser-collection.md`). Check robots.txt per host first; never change the User-Agent; on a 4xx, stop and report instead of retrying differently. See `references/constraints.md`.
- Never handle key values: do not print, write, or pass any API key in commands or chat.
- Do not keep article bodies beyond a run; respect the quoting limits in `references/constraints.md`. Never print article bodies into the conversation through a shell command.

## References

| File | Read when |
|---|---|
| `references/first-run.md` | First use on a machine, any `doctor` problem |
| `references/run-steps.md` | Steps 5–13: exit codes, subagent briefs, dead links, prune, period report |
| `references/later-runs.md` | Accumulation, corrections (one line or reviewed), format changes, issue identity, seed, moving from the old folder |
| `references/sources.md` | Which collector skill feeds which source, and how `collect` reads it |
| `references/constraints.md` | Before any change to collection, linking or what the report reprints |
| `references/contracts.md` | Reading or writing any JSON file of a run, state or config; the period report rules; what may be deleted |
| `references/relevance-rules.md` | Writing `work/relevance_brief.json` and compiling it (step 8) |
| `references/writing-rules.md` | Writing a summary (step 10) and every correction file — every time |
| `references/browser-collection.md` | Fetching BigKinds article bodies in the built-in browser (step 9) |
| `references/review-guide.md` | Checking a report against its sources (a reviewer who did not write it) |
| `references/korean-text.md` | Writing or changing any regex or keyword match over Korean text |
