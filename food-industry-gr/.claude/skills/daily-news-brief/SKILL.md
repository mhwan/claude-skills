---
name: daily-news-brief
description: "Builds a morning briefing of seven Korean dailies (조선·중앙·동아·한겨레·경향·매경·한경) for one date: collects the articles from BigKinds, writes an issue table per category and an editorial tone comparison, and checks every summary sentence against the source text. Use for \"조간 브리핑\", \"오늘 뉴스 정리\", \"신문 요약\", \"뉴스 브리핑\", \"논조 비교\", \"사설 비교\", for the press coverage of a given date, for how the papers differ in tone, or to redo or fix a briefing this skill made. Not for a keyword news search (use korean-news-search) and not for a food-industry trend report (use food-industry-watch)."
---

# Daily news brief

Collects the articles of seven dailies from BigKinds and builds a briefing: **an issue table per category plus an editorial tone comparison**.

Output: `reports/daily-news-brief/{date}.md` under the output folder (see "Running the scripts").
Working files: `runs/daily-news-brief/{date}-N/` with `raw/`, `work/` and `run.json`. The article-body files are deleted once the report exists.

## Two principles

**Stop silent failure.** A briefing built from 2 of the 7 papers still looks fine.
Collection therefore has 7 gates; if any one trips, never call the result normal.

**Stop invented summaries.** A tidy table with live links passes every format check.
So the summary lives in its own file, and each number, date, quote and outlet attribution in it is checked against the source text.
A briefing that fails this gate is not released. (The same split lets the summary be redone alone.)

## Pipeline

```
collect.js    collect in the browser, export as chunks
ingest.py     reassemble + check the chunks -> runs/.../{date}-N/raw/collected.json (new run folder)
normalize.py  normalize + 7 collection gates -> raw/{date}.json (collected.json is removed)
cluster.py    clusters, categories, opinion pieces, schedule -> work/{date}.clusters.json
render.py     prepare -> work/{date}.todo.json
(the LLM writes the summary) -> work/{date}.summary.json
verify.py     evidence gate: compare with the source text
render.py     build -> reports/daily-news-brief/{date}.md (common 5-line header on top)
verify.py     --lint format check
prune.py      delete the body files, move the 14-day stale-check summary to state/
```

## Running the scripts

- `PY` = this PC's Python 3. On macOS `python3`. On Windows the first of `py -3`, `python3`, `python` whose `--version` prints a version (no version means a Microsoft Store alias, not Python).
- `<skill>` = absolute path of this skill folder. Do not `cd`; call `PY "<skill>/scripts/X.py"`.
- One command per tool call (no `&&`, `||`, `;`).
- The output folder is `GR_HOME` if set, else `output/` in the work folder. A copy kept in the personal folder (`~/.claude/skills/`) is outside every work folder, so there `GR_HOME` is required. For another output folder (a test run, say) put `env GR_HOME=folder` before every `PY` command.
- Several run folders for one date: the latest is used. Pick another with `--run 2026-08-21-1`.

## Steps

### 1. Collect

Open `https://www.bigkinds.or.kr/v2/news/index.do` in the built-in browser (`preview_start`), inject all of `scripts/collect.js`, then run:

```js
window.__collect.run("YYYY-MM-DD")
```

BigKinds is the only site opened (never a newspaper site). Check its `robots.txt` first; on a 4xx, stop and do not work around it.
Poll `window.__collect.status().phase` until it is `done` (2 to 4 minutes for 7 papers).
If `status().stopped` has a reason, the collector stopped itself (a 403 or 429 was received, or the 450-request cap was hit). Report it as it is and do not retry.

### 2. Retrieve

Downloads are blocked in the built-in browser, so export the result in 60,000-character chunks, let each land on disk as a tool-result file, and rebuild with `ingest.py --expect-len <b64_len> files...`.
**Read `references/collection.md` for the exact procedure**, the BigKinds API traps and the collector's politeness limits.

### 3. Run the pipeline

```bash
PY "<skill>/scripts/normalize.py" "<run printed by ingest.py>/raw/collected.json"
PY "<skill>/scripts/cluster.py" YYYY-MM-DD
PY "<skill>/scripts/render.py" prepare YYYY-MM-DD
```

If `normalize.py` reports FAIL, stop and find the cause. A half-collected day gets no briefing.

### 4. Write the summary (the LLM's part)

Read `work/{date}.todo.json` and write `work/{date}.summary.json`. The work order holds the article bodies and full opinion pieces of every issue; find evidence only there.
**Read `references/summary-schema.md` for the schema and writing rules** (length caps, allowed `stance` values, the lead `ref` duty, how to judge tone, common failures).

### 5. Verify and build

```bash
PY "<skill>/scripts/render.py" build YYYY-MM-DD
PY "<skill>/scripts/verify.py" YYYY-MM-DD --lint
```

**Both must PASS.** On FAIL, fix what is flagged and repeat steps 4 and 5. Never bypass a gate or lower its bar.
`render.py` writes all of the markdown; **never edit it by hand**. Fix `summary.json` and run `build` again.

### 6. Clean up

Only after both checks PASS, because a cleaned run cannot be rendered again.

```bash
PY "<skill>/scripts/prune.py" YYYY-MM-DD
```

The article bodies are discarded here as a matter of principle, not disk space.
The retrieval chunks from step 2 sit outside the pipeline and must go too. The app made them, so **show the user the list first and delete only after they answer "예"**.
Read `references/cleanup.md` for what prune deletes and keeps, its options and the chunk list command.

## References

| File | Read when |
|---|---|
| `references/collection.md` | running steps 1 and 2 (required) |
| `references/summary-schema.md` | writing the summary in step 4 (required) |
| `references/verification.md` | a gate returns FAIL, or before changing a gate, a script or the tests |
| `references/cleanup.md` | running step 6, or changing `prune.py` |
| `references/constraints.md` | adding sources, considering automation, or writing the limits into a briefing |
| `references/korean-text.md` | **before changing any regex or pattern in the pipeline** |
| `references/rendering-rules.md` | you need the report layout rules: table structure, scores, links, length caps |

## Never do (reasons in `references/constraints.md`)

- Collect the blocked outlets (`중앙`, `한겨레`, `경향`, `매경`) directly.
- Fake the User-Agent.
- Reproduce article bodies in any output.
- Keep article bodies permanently; `prune.py` discards them once the briefing exists.
- Attach a scheduled bulk collection.
