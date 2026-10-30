# Run steps in detail

## Run folder
`run-new` creates `runs/assembly-bill-tracker/<date>-<n>/` with `raw/`, `work/` and `run.json`.

## work/queries.json
```
{"queries": [
  {"id": "q1", "proposer_kind": "의원", "from": "2026-10-01", "to": "2026-10-29", "limit": 300,
   "name": "식품", "file": "raw/q1.json", "depth": 0, "split": false}
]}
```
- `id`: unique. `file`: path relative to the run folder, or absolute (a `tool-results/` path).
- `error`: instead of `file`, when the tool call failed; the text goes into the report as the reason.
- `parent`, `split`, `depth`: for halves of a cut-off query. The cut-off query keeps `"split": true`; each half has `"parent": "<its id>"`, `"depth"` one higher, and exactly the range `abt.py ranges` printed, the same `name` and `proposer_kind`.

A result file counts as readable only if it is JSON with a `bills` list and, when `returned` is present, `returned` equals the list length. Otherwise the query is `unread`.

## Cut-off handling
`merge` prints `need_split` (not yet split), `unsplittable` (a single day that is still cut off), `missing_children` (a half without a result) and `unread`. The report says 「일부만」 for every one of these that is left at `build` time.

## Detail files
`raw/detail-<BILL_NO>.json` holds the unchanged `assembly_bill_detail` result. A bill in `fetch` without a readable file becomes 「단계 미확인」 with a warning.

## Change labels
| Label | Meaning |
|---|---|
| 새로 생김 | not in the previous record and proposed on or after the previous query's date |
| 진척 | stage text differs from the previous record (shows old → new) |
| 멈춤 | same stage text, unchanged for at least `--stall-days` days |
| 변화 없음 | same stage, shorter than that |
| 종료 | `PASSGUBN` is 처리의안 (not looked up again once recorded) |
| 처음 조회 | no previous record, or the bill is older than the previous query and was never recorded |
| 단계 미확인 | stage not looked up or not readable |

The previous record (`state/assembly-bill-tracker/bills.json`) is written after each `build` unless `--no-save-state` is given. A failed run (`status` unread) does not write it; a partial run keeps the old query date.
