---
name: korean-news-search
description: "Searches Korean news on BigKinds for a query and a period and writes a report with an article list, a short summary per article and an original link. Use for \"설탕 담합 기사 찾아줘\", \"지난달 라면 가격 관련 기사 정리해줘\", \"조선일보 중앙일보에서 식품 안전 기사 찾아줘\". Not for the morning newspaper briefing (use daily-news-brief) and not for a food-industry trend report across many sources (use food-industry-watch)."
---

# Korean News Search

Takes a search query and a period, searches BigKinds (the Korean press archive) and writes a report: article list (newest first), a one- or two-sentence summary per article, and the original link. The user is a non-developer: answer in Korean, plain words, conclusion first.

## Rules that always apply
- Never open a news outlet's own site. Articles come only from BigKinds, through `scripts/search_bigkinds.js` in the built-in browser. Before the first BigKinds request of a session, read `https://www.bigkinds.or.kr/robots.txt` in the browser; if it disallows `/api/` or `/news/`, stop and tell the user. Never change the User-Agent, never use RSS, and on any 4xx answer stop: do not retry or work around it.
- Pass the user's query to BigKinds exactly as given; do not rewrite it. Seen on the real site (2026-10-29, one query, one period): a space is AND, `-word` is NOT, quotes are an exact phrase, `OR` or `,` is OR. `|` is NOT OR there: it gave 0 articles. If the user wants "A or B", ask whether to write `A OR B`; an empty answer to a `|` query is not proof that there are no articles. Details: `references/bigkinds.md`.
- The article text lives only in `runs/korean-news-search/<run>/raw/` and `work/`. It is never copied into the report, the data file, `state/` or `history/`. Delete it right after the report (step 9).
- Summaries are written by you from the article text, never from memory. Every number, date, English word, name in front of a title word, and quoted phrase must be in the text. A quote is at most 40 characters, word for word, with the link. Rules: `references/summary-rules.md`.
- Only these things are checked by the script (`verify_summaries.py`): numbers and dates, English words, names in front of a title word (`대표`, `회장`, `장관` ...), quotes, long copies, length. Any other claim in a summary is not checked. Never tell the user the summaries were "verified" without saying this.
- A search that could not be read is reported as 「읽지 못함」, never as "no articles". Do not make a report for it.
- Never ask for, print or write API keys. A fact not seen in the article text or the list is written as 「확인 필요」.

## Steps
`PY` is `python3` on macOS and `py -3 -X utf8` on Windows. `SKILL_DIR` is this skill's folder as an absolute path. Run one command per call.

1. Fix the request: query, period (start and end date; ask once if unclear), and outlets (default: all). The 7 known outlets are in `references/outlets.md`; others are matched by name after the search.
2. Make the run folder:
   ```
   PY SKILL_DIR/scripts/run_search.py prepare --query "QUERY" --start YYYY-MM-DD --end YYYY-MM-DD [--outlets "조선일보,중앙일보"] [--summary-from N]
   ```
   It prints `run_dir` and `collector`. Exit 2 means the request is wrong (bad date, future end date, empty query): fix it and ask again.
3. Collect the list in the built-in browser (steps and snippets: `references/bigkinds.md`): open the BigKinds news page, load the collector file, run `window.__kns.startList(JOB)` with `JOB` from `run_dir/work/job.json`, poll `status()` until `done`, then export the chunks and save them.
4. Put the chunks back together:
   ```
   PY SKILL_DIR/scripts/ingest.py --run RUN_DIR --kind list --expect-len B64_LEN CHUNK_FILE ...
   ```
   Exit 1 means a chunk is missing or damaged: export again, do not repair by hand.
5. Normalize:
   ```
   PY SKILL_DIR/scripts/normalize.py --run RUN_DIR
   ```
   It prints `to_summarize` (at most 30 ids, newest first), `matched`, `total` and `warnings`. Exit 1 prints why nothing can be reported (the search could not be read): tell the user that, and stop at step 9 with `--abandon`. If `matched` is 0 and exit is 0, skip to step 8.
6. Collect the article texts of `to_summarize` the same way (`startBodies(IDS)`, kind `bodies`), ingest them with `--kind bodies`, and run `normalize.py` again. An article without a text is listed without a summary and named in a warning.
7. Write `run_dir/work/summaries.json` and check it:
   ```
   PY SKILL_DIR/scripts/verify_summaries.py --run RUN_DIR
   ```
   Format and rules: `references/summary-rules.md`. Exit 1 prints `failed` ids; read `work/verified.json`, rewrite only those summaries and run the check again. Never edit the text of an article or loosen a rule to get a pass.
8. Write the report:
   ```
   PY SKILL_DIR/scripts/render_report.py --run RUN_DIR
   ```
   It prints `report` and `data` paths. Exit 0 clean, 3 written with warnings to read (not a failure), 1 nothing written.
9. Delete the article text:
   ```
   PY SKILL_DIR/scripts/purge_raw.py --run RUN_DIR
   ```
   For a run that gave no report add `--abandon`. Then see "Leftover files" below.
10. Tell the user: the conclusion in one or two lines (how many articles were found, how many summarized), the report path, and every warning in plain words.

## Limits and what to say
- One report lists at most 200 articles and summarizes at most 30. When more were found, the report says so; for the rest say 「나머지도 요약해줘」 and run again from step 2 with `--summary-from 30` (then 60 ...), same query and period. A new run collects the list again.
- BigKinds answers at most 2,000 articles per request. The collector halves the period (up to 3 times) when the count is higher. If it still cannot read all, the report says 「일부만 수집」: offer to narrow the period or the query.
- Outlets not in the known 7 are filtered by name after the search, so the count BigKinds reports (`total`) may be larger than the rows.
- To use another output root (tests), put `env GR_HOME=FOLDER` in front of every `PY` command.

## Leftover files
The browser export is saved by the app as files in a `tool-results` folder next to the session; their paths are in the tool results. They hold the compressed article text. After step 9, list them for the user and ask before deleting them; delete only after a yes. Say in the final answer that this was done or still open.

## Files
- Report: `reports/korean-news-search/<period>_<topic>.md`; data: `data/korean-news-search/<same>.json` (shared envelope; extra fields `outlet`, `summary`); run record: `runs/korean-news-search/<date>-<n>/run.json`. An earlier report with the same name moves to `history/korean-news-search/` as `.vN`.
- The output root follows `GR_HOME`, else the work folder `output/`.
- `assets/report-template.md` shows the shape and wording of the report. `render_report.py` writes the real report itself and does not read this file; to change the wording, change both.

## References
- `references/bigkinds.md`: read before step 3. The browser procedure, request and answer fields, limits, what is 「미확인」.
- `references/outlets.md`: read when the user names an outlet.
- `references/summary-rules.md`: read before step 7.
