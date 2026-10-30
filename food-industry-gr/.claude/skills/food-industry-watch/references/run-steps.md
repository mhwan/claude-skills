# Run steps in detail

What to do at steps 5–13 of SKILL.md, including every exit code. `PY "<skill>/scripts/fiw.py"` is written as `fiw`. `<run folder>` is `runs/food-industry-watch/<id>/` inside the output folder (`GR_HOME`, else `output/`); `fiw run show --run <id>` prints it.

## Contents

- Collector skills and collect (steps 5–6)
- Up to the judgement (step 7)
- Relevance (step 8)
- Work order (step 9)
- Summary in parts (step 10)
- Report (step 11)
- Check list and prune (step 12)
- Period report (step 13)

## Collector skills and collect

**Step 5.** Run the five collector skills for the run's dates, each by its own instructions: `korean-news-search`, `gov-notice-watch`, `assembly-bill-tracker`, `assembly-hearing-watch`, `food-safety-records`. Each leaves a `gr.envelope/1` file in `data/<skill>/` named `YYYY-MM-DD~YYYY-MM-DD_<words>.json`. Use the run's `from`/`to` as the period (the schedule skill looks four weeks ahead on its own). A result that already overlaps the period is reused, so look in `data/<skill>/` first and run only the skills with a gap. Tell the user in one line which collectors you are running.

**Step 6.** `fiw collect --run <id> --source all` makes no web request. It reads the envelopes in `data/`, keeps the rows of the run's period and writes `<run folder>/raw/collect/<source>.json`; the run's manifest records each request as finished or failed.

- `0`: every source was read and complete.
- `3`: a source has no result for the period, or its result is partial or failed. The line says which (`<source>: not collected — no <skill> result in data/ overlaps A~B`). Run that collector skill, then `collect --source <source>` again; or go on and let the report show ⚠ and 「확인 필요」 for it. A collector that itself reported `no_key` or `failed` is not retried here.
- `1` / `2`: read the message and fix the command or the file it names.

## Up to the judgement

`fiw pipeline --run <id> --until judge` runs `check` → `prefilter` → `cluster` and prints one line. Each stage's full output is in `<run folder>/work/logs/<stage>.txt`.

- `1` / `2`: open that stage's log and fix.
- `3` from `check`: read `work/logs/check.txt`. Tell the user which sources failed or are partial (the run's report will show ⚠) and which C8 findings were left (comment periods not parsed for non-food items, listed by title only), then go on.

## Relevance

Give the judgement to **one** subagent: all clusters together, never split. Its brief:

1. Read the relevance-rules reference of this skill (path in the SKILL.md table) to the end.
2. Read `work/clusters_brief.md` and `work/items_view.md` to the end. Open a cid in `work/clusters_view.md` only when its line is not enough.
3. Write `<run folder>/work/relevance_brief.json` (absolute path).
4. Run `fiw relevance --run <id> --compile <that absolute path>` until it exits `0`; it writes `work/relevance.json`. A relative path is read from the current folder, not the run folder.
5. Reply with the exit code and counts only.

Read only that reply and the gate. Without a subagent tool, do it yourself.

## Work order

Run `fiw prepare --run <id>`. Exit `3` has two meanings; the printed lines say which:

- "bodies needed: N": run the `bigkinds_bodies` browser job (SKILL.md step 9 names the reference), `ingest`, then `prepare` again. Repeat until it no longer asks.
- 「근거 본문 미수신: …」 or "every issue of section … lost its source bodies": `work/todo.json` is written; tell the user which issues had no readable source and go on.
- Exit `1`: stop.

## Summary in parts

1. `fiw parts split --run <id>` cuts `work/todo.json` into parts of about 20,000 body chars (`work/parts/index.md`).
2. Give each part to its own subagent; they may run at once. Brief: part `NN` of run `<id>`; follow the writing-rules reference (all of it, then § Writing a part); reply with the exit code of `parts check` only.
3. Write `<run folder>/work/summary_head.json` yourself (writing-rules § Summary head): the 세 줄 and the press labels. The parts do not write them.
4. Run `fiw parts merge --run <id> --head work/summary_head.json` (the path is relative to the run folder).

Without a subagent tool, do the parts yourself, one at a time.

## Report

`fiw pipeline --run <id> --from summary` runs `verify` → `render` → `lint` → `linkcheck` → `publish`.

- `1`: open the stopping stage's log.
  - A `verify` finding: rewrite the sentence in its part (never weaken the evidence), then `parts check`, `parts merge`, and run the pipeline again.
  - A `dead` link from `linkcheck` (see below).
- `3` from `linkcheck`: say how many links could not be checked automatically.
- `publish` records the run in `state/food-industry-watch/` (its `report.md` stays in `<run folder>/work/`; nothing goes to `reports/`). Read the 「⚠」 lines (failed or partial collection) at the top of that `report.md` and tell the user, if any.

**Dead links.** `dead` means the source answered 404/410. The page may be gone or only briefly unavailable; either way it is not retried.

1. In the part, put each sentence that cites it on another source of the same issue that states the same fact, or drop the sentence. Never keep the dead source, look for a copy elsewhere, or retry it.
2. `linkcheck` records every dead URL in `work/dead_links.json`. `render` then leaves that link out, and `verify` (E16) rejects any sentence that still cites it.
3. After rewriting the sentences, run the pipeline again from `verify` (`--from summary` starts there).

## Check list and prune

**Check list.** Run `fiw review --run <id> --risk --n 10 --seed <any number>` and show the user all its lines as 「확인할 곳」. The lines are in report order with no mark: every risk field (each 세 줄 line, each `edit` or `evidence` sentence, each sentence with two or more numbers and companies, each own-company sentence), 10 random other fields, then every notice and bill row — so there are usually far more than 10. A flow node that cites a flow candidate (`I-0070:f1`) shows 「기사」 instead of an outlet: the candidate keeps only its date and link. They open the links and say what is wrong; the verdict is theirs. A reviewer who did not write the report follows the review-guide reference.

**Prune.** Run `fiw prune --run <id> --chunks "<tool-results folder>" --apply` once the user has no more questions about this run.

- It deletes the article bodies and excerpts: each summary part's `bodies.md`, its todo copy, the `facts.json` quotes, and the saved export chunks (found by content).
- It must print `본문 0건`.
- `--chunks` takes every folder where browser results were saved: the session's `tool-results` folder (the path shown when a long tool output was saved to a file) and any folder you saved exports to. Without it the chunks stay and prune does not print `본문 0건`.
- Then move the run's leftover briefs to the trash (the contracts reference, § What may go after publish).
- The period report (step 13) needs no bodies, so it may be built before or after prune.
- After prune, `verify` on this published run exits `2` without touching its gate. Change the run only through `correct` (SKILL.md names the reference for corrections).

Never print article bodies into the conversation through a shell command. A long output is saved to a file that prune cannot recognise.

## Period report

The report the user reads. Rules and gates: the contracts reference, § Period report.

1. Turn the user's words into dates and say them back before anything else: 「최근 1주」「최근 1주일」「이번 주」 = the 7 days ending today, 「최근 N주」 = the 7×N days ending today, 「최근 N개월」「3개월 종합」「분기 종합」 = the N (or 3) months ending today, 「이번 달」 = the 1st to today, or the dates they give. Both ends count: on 2026-10-27, 「최근 1주일」 = 2026-10-21~2026-10-27, 「최근 2주」 = 2026-10-14~2026-10-27 and 「최근 3개월」 = 2026-07-28~2026-10-27. `report` alone needs no `doctor`; run `doctor` before any collection.
2. Run `fiw report --from A --to B`.
   - Exit `3`, `1` and the messages are listed in the contracts reference, § Period report. For "the raws end at X — collect X+1~B first": follow `period`, which proposes X~B (one day earlier than the message): it reads the boundary day X again and `seen_ids` drops the articles already read. Then `doctor`, `period`, `run new` (or `run new --from <the day it names> --to B` when B is before today), steps 5–12 for that run, and `report` again.
   - For "write 세 줄": `report` prints the brief's path, `<run folder of the newest run>/work/build/A~B/work/three_lines_brief.json` (issues already sorted by impact — outlets, a government sanction, the own company; take the first `want`). Write `three_lines.json` next to it under the contracts reference, § Period report step 6 (not the writing-rules § Summary head) and rerun with `--three-lines <that file>`.
   - `0`, or `3` after the report was written (links not checkable): show the user `reports/food-industry-watch/A~B.md`, the 「미수집」 line if any, and the linkcheck line.
