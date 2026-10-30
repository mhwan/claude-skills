# Later runs, corrections and format changes

What changes after the first run, how to change a published report, and how to bring in data from elsewhere. `PY "<skill>/scripts/fiw.py"` is written as `fiw`. `<run folder>` is `runs/food-industry-watch/<id>/` inside the output folder.

## Contents

- Accumulation
- Correction of one line (D33)
- Many fixes after publish
- Format change (D59)
- Issue identity
- Moving from the old folder
- Sharing a starting point (D55)

## Accumulation

- The next `period` starts at the last covered day. That day is re-read; articles already reported are dropped as `seen:`.
- In the relevance step, `clusters_brief.md` starts with the earlier issues. Set `issue_link` to that id when a cluster continues one.
- The report then marks 🆕 / ▶, lists earlier issues without new articles, and folds issues dormant for `dormancy_weeks` (default 4) covered weeks under 💤.
- In the summary step keep every `prior.corrections` change: the user made it.

## Correction of one line (D33)

Use when the user says a report line is wrong.

1. Write `<run folder>/work/corrections/<n>.json` (`fiw.correction/1`): the user's words, the issue key or id, the field path, the new value with refs, and exact evidence quotes (the writing-rules reference).
2. Run `fiw correct --run <id> --file <it>`.
   - `3` = bodies needed: run `bigkinds_bodies`, `ingest`, `correct` again. A flow candidate not in this run's export is found by its exact URL in earlier runs' exports.
   - `1` = the change fails the evidence gate or 「근거 확인 불가」: tell the user it was not applied and why. Never weaken the evidence.
   - `0`: show the diff, then rerun with `--apply`.
3. `--apply` rewrites the run's `work/report.md` in place and keeps the correction in the issue's state; a title change becomes a user `rename` lock.
4. Prune again, then rebuild every period report that covers the run (`fiw report --from … --to …`, same dates). The user's next check reads the rebuilt one.

## Many fixes after publish

Use when the check list turns up wrong lines in several places.

1. Rewrite each in its part (same rules), then `parts check`, `parts merge`, `verify` until `0`.
2. Run `fiw correct --run <id> --reviewed --request "<the user's words>"`. It refuses a summary without a passing `verify` of that exact file. It prints each changed issue's `- old` / `+ new` sentences and the changed report lines.
3. On `--apply` it rewrites the run's `work/report.md` and keeps each changed issue's sentences as a correction, so the next run's prior starts from the fixed text. Then rebuild the period reports that cover the run.

## Format change (D59)

Use when the user wants a section removed, moved or renamed, columns changed, rows capped, or dormancy weeks changed.

1. Write the patch: `{"sections": [{"id": …, "enabled" / "order" / "title" / "columns" / "max_rows": …}], "dormancy_weeks": …}`.
2. Run `fiw format apply <patch>`. It re-renders the latest run to `work/report.preview.md` and lints it.
3. Show the user the printed changes; on their yes rerun with `--apply`.
4. Rebuild the period reports the user reads (`fiw report --from … --to …`, same dates) so they take the new format.

`fiw format undo --apply` restores the previous format. Changes to sentence limits apply to summaries written afterwards. The format lives in the `format` part of `config/food-industry-watch.json`; `fiw config` shows where it differs from the shipped default and `fiw config --take-default KEY` adopts the default for one key.

## Issue identity

On the user's word only. Each command previews; add `--apply` to run it.

- `fiw state merge A B`
- `fiw state split A --articles ID,ID`
- `fiw state lock A --type no_merge_with|exclude_pattern|rename|pin_section --value V`

`fiw state history` lists snapshots and `fiw state undo --to REV --apply` restores one. `fiw state check` must exit `0`.

## Moving from the old folder

Before the collector skills existed, this skill kept everything in `~/.claude/food-industry-watch/` (or `FIW_HOME`). `fiw import-legacy` brings that data into the output folder. It only copies: the old folder is never changed or deleted.

1. `fiw import-legacy` (add `--from <folder>` for another old folder) prints the plan and writes nothing: how many files of each kind (reports, state, runs, config) would be written and how many are already identical. Show it to the user.
2. On their yes: `fiw import-legacy --apply`. Old `raw/<id>/` folders become runs, the old history becomes `state/food-industry-watch/snapshots.json`, reports go to `reports/food-industry-watch/`, and the old `watch.json` is split into the shared `config/watch.json` and this skill's `config/food-industry-watch.json`. After copying it reads every file back and compares; a difference is a failure.
3. The old keys for sources that now belong to the collector skills (`assembly`, `korea_kr`, `lawmaking`) are dropped from the copied config; the output says so. Run `fiw doctor` and `fiw state check` afterwards; both should exit `0`.
4. Tell the user the old folder is still there and can be removed by them once they are satisfied. Do not delete it.

It refuses to run over a state folder that already holds data of its own; ask the user before doing anything about that.

## Sharing a starting point (D55)

- `fiw seed export --out <empty folder> --apply` copies the runs, state and config in the layout of the output folder. It never copies article text, hidden files or this machine's app check, and it refuses while any run is not pruned (`fiw prune --run <id> --apply` first).
- On another PC, run `fiw seed import --from <folder> --apply` into an empty output folder, then `doctor`. Period reports are not in a seed: build the ones wanted with `fiw report`.
- This skill ships the developer's first quarter (2026-07-27~2026-10-26) in `seed/` as three runs, in the **old** folder layout (`raw/`, `state/`, `config/`). `seed import` does not read that layout (it would find almost nothing). On a first run with an empty output folder, offer `fiw import-legacy --from <this skill>/seed` (a dry run), and on the user's yes `--apply`, before `period`. The bundled `seed/` stays where it is.
