---
name: assembly-bill-tracker
description: "Looks up 22nd National Assembly bills by period, committee, keyword or proposer and returns a linked table of bill number, stage and committee, marking each bill as new, progressed or stalled against the previous query. Use for \"이번 주 새로 나온 식품 법안\", \"우리가 보는 법안 어디까지 갔어\". Not for a one-off lookup of a single bill."
---

# Assembly Bill Tracker

Finds 22nd-Assembly bills that match a period, keyword, committee or proposer, and writes a table (bill number, name with link, proposer, committee, stage as the Assembly words it). Each bill is compared with the previous query of this skill and marked 「새로 생김」 / 「진척」 / 「멈춤」 / 「종료」. The user is a non-developer: answer in Korean, plain words, conclusion first.

## Rules that always apply
- Every bill row has a link (`DETAIL_LINK`, else the detail's `LINK_URL`). No link: `url: null` and a warning naming the bill number.
- Stage words (`PASSGUBN`, `PROC_STAGE_CD`) are copied exactly as the tool returns them. Never translate, rank or guess a direction. A value not in `scripts/lib/assembly_stages.py` is shown as is and the script adds a 「미해석」 warning.
- 「진척」 means only "the stage text differs from the previous query". 「종료」 only when `PASSGUBN` is 「처리의안」.
- 「멈춤」 = same stage for at least 60 days (setting `--stall-days`), judged only against a saved previous query. Never count days since the bill was proposed. With no previous query the report says 「멈춤 여부 확인 필요」.
- Keywords: the request wins; else `config/watch.json` (read only); else ask the user. Never invent a keyword list.
- At most 30 detail lookups per run (setting `--detail-cap`). The rest show 「단계 미확인」 with a warning.
- A query that returns as many bills as its `limit` may be cut off. Split its date range and query again (step 4). If it cannot be split, the report says 「일부만」.
- Never open press sites. Never ask for, print or write API keys. A number not seen in a tool result is written as 「확인 필요」.

## Steps
`PY` is `python3` on macOS and `py -3 -X utf8` on Windows. `SKILL_DIR` is this skill's folder as an absolute path. Details of each step: `references/run-steps.md`.

1. Fix the period (ask once if unclear), keywords, committee and proposer. Keywords:
   ```
   PY SKILL_DIR/scripts/abt.py keywords [--keyword WORD ...]
   ```
   Exit 3 prints `ask`: put that question to the user and stop until answered.
2. Make the run folder:
   ```
   PY SKILL_DIR/scripts/abt.py run-new --today YYYY-MM-DD
   ```
   It prints `run_dir`.
3. Query `assembly_bills` (limit 300) for each keyword and each proposer kind (`proposer_kind` 「의원」, 「위원장」, 「정부」). Save every result as a file under `run_dir/raw/` and list the queries in `run_dir/work/queries.json`. Tool quirks: `references/assembly-tools.md`. File formats: `references/run-steps.md`.
4. Check for cut-off queries:
   ```
   PY SKILL_DIR/scripts/abt.py merge --run RUN_DIR
   ```
   Exit 3 with `need_split`: get the halves with `abt.py ranges --from D --to D`, query each half, add them to `queries.json` (`parent` = the cut-off query, whose `split` becomes true), and run `merge` again. Repeat until it exits 0 or only `unsplittable` is left.
5. See which bills need a stage lookup:
   ```
   PY SKILL_DIR/scripts/abt.py targets --run RUN_DIR --today YYYY-MM-DD
   ```
   Call `assembly_bill_detail` for each bill in `fetch` and save the result as `run_dir/raw/detail-<BILL_NO>.json`.
6. Build:
   ```
   PY SKILL_DIR/scripts/abt.py build --run RUN_DIR --from D --to D --today D --keyword WORD [--committee TEXT] [--proposer TEXT] [--stall-days N] [--detail-cap N] [--transcribed]
   ```
   Add `--transcribed` when any result file was typed by hand instead of saved from the tool's own file. The command prints `report`, `envelope`, `status`, `rows` and `warnings`.
7. Verify: `PY SKILL_DIR/scripts/abt.py check ENVELOPE_FILE` must print `"valid": true`.
8. Tell the user: the conclusion in one or two lines (counts of 「새로 생김」 / 「진척」 / 「멈춤」), the report path, and every warning in plain words.

## Reading the output
- Exit code 0 clean, 3 means the report was written and there are warnings to read (not a failure), 1 means nothing could be read, 2 means wrong flags or an unusable run folder.
- 「일부만」: a query hit the cap; the table is not the full list. Say so.
- 「단계 미확인」: no stage was looked up; no change label is given. These rows come last in the table. When there are many, tell the user the lookup cap (30) can be raised with `--detail-cap`, or the period or committee narrowed, and ask which they want.
- To use another output root (for tests), put `env GR_HOME=FOLDER` in front of every `PY` command.

## Files
- Report: `reports/assembly-bill-tracker/<period>[_keywords].md`; data: `data/assembly-bill-tracker/<same>.json`; previous-query record: `state/assembly-bill-tracker/bills.json`; run record: `runs/assembly-bill-tracker/<date>-<n>/`. An earlier report with the same name moves to `history/assembly-bill-tracker/`.
- The output root follows `GR_HOME`, else the work folder `output/`.
- The record in `state/` is shared by every query of this skill, so a query with other keywords marks older bills it never saw before as 「처음 조회」, not 「새로 생김」.

## References
- `references/assembly-tools.md`: read before calling `assembly_bills` or `assembly_bill_detail`.
- `references/run-steps.md`: read before writing `queries.json` or when a step's output is unclear.
- `references/observed-stage-values.md`: read when a 「미해석」 warning appears, and add the new value with the bill number it was seen on.
