# Cleanup (step 6)

What `prune.py` deletes and keeps after a briefing is built, its options, and how to remove the retrieval chunks. Run it only after the evidence check and the `--lint` check both PASS.

## What prune deletes

Per run folder, unconditionally:

- `raw/*.json` (full article bodies) and `work/{date}.todo.json` (a copy of the bodies)
- `work/{date}.clusters.json` (nothing reads it after `prepare`)
- `work/{date}.summary.json` (its sentences are already in the report; if it stayed, the structure check would FAIL with "the report copied text from work/")

The number of deleted body files (raw and todo) goes into the last header line of the report. That line changes from "작업용 본문 N개 파일은 … 지울 예정입니다" to "… 지웠습니다". No other byte of the report changes.

Full article bodies are intermediate data only, and one source's `llms.txt` explicitly refuses permanent storage of full text. Pruning follows that rule; it is not about disk space.

## What prune keeps

- `reports/daily-news-brief/{date}.md` (the deliverable)
- `work/{date}.verify.json` and `run.json` of the run folder
- `state/daily-news-brief/{date}.json`: titles, links and metadata only. It is written before anything is deleted and is what the stale gate reads for the previous 7 days. The fields kept are an allowlist (outlet, outlet code, news id, title, url, published date and time, editorial flag, body length); every other field is dropped, so a field the collector adds later is not kept by accident.

Running it again on an already cleaned run does nothing.

## After pruning

The summary cannot be fixed and rebuilt any more. To change a cleaned briefing, collect again. This is deliberate: a briefing must not go out without verification.

## Options

```bash
PY "<skill>/scripts/prune.py" YYYY-MM-DD
PY "<skill>/scripts/prune.py" --sweep
```

| Option | Meaning |
|---|---|
| `--dry-run` | Only list what would be deleted. Use it first. |
| `--run 2026-08-21-1` | Clean a specific run folder of one date (only together with one date). Without it, the latest run folder of the date is used. |
| `--sweep` | Clean every run folder that has a report, and also delete the body files and `state/` summaries that are outside the retention window. |
| `--retain-days N` | Retention window for `--sweep`. The default is 14: the 7-day stale window plus 7 days of margin for backfill runs. |
| `--force` | Clean even when the report file does not exist. Use it only when you mean to give up on that run. |
| `--chunks DIR` | Also delete the retrieval chunks in DIR (see below). |

## Retrieval chunks

The chunk files from step 2 pile up in the session's `tool-results` folder, outside the pipeline. They hold the gzip of the full bodies, so the rule against permanent storage applies to them too.

List them first:

```bash
PY "<skill>/scripts/prune.py" --sweep --dry-run --chunks "<home>/.claude/projects/{project}/{session id}/tool-results"
```

Then show that list to the user and delete only after they answer "예": run the same command again without `--dry-run`. The app made these files, so the user decides.

Files are recognised by their content (the `KNSCHUNK|collect|` header, or the old single base64 blob), not by name. Other outputs of the same tool and agent transcript dumps are never touched. Deleting a chunk means that session can no longer read that tool result when it is resumed, so do it only for a finished collection.
