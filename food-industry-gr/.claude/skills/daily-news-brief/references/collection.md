# Collection and retrieval

How the articles of the seven papers get from BigKinds into local files. Detail for steps 1 and 2 of `SKILL.md`.

## Contents
- [Why a browser](#why-a-browser)
- [1. Inject and run the collector](#1-inject-and-run-the-collector)
- [2. Retrieval: export and ingest.py](#2-retrieval-export-and-ingestpy)
- [The collector's politeness values and stop rules](#the-collectors-politeness-values-and-stop-rules)
- [Why the chunk size is 60,000](#why-the-chunk-size-is-60000)
- [BigKinds API notes](#bigkinds-api-notes)
- [What the collector does](#what-the-collector-does)

---

## Why a browser

The BigKinds WAF rejects non-browser clients with a 400, including curl with an honest User-Agent.
**The built-in Claude browser gets through while naming Claude in its UA, so it is the only way in without faking a UA.**

A real Chrome gets a 200, but its UA does not say it is Claude, so it is not used.
If a headless run is ever needed, the proper route is to apply for the official BigKinds OPEN API.

**BigKinds is the only site opened.** No newspaper site is opened (the link check in `normalize.py` looks only at the shape of the address).
Check the BigKinds `robots.txt` first, and on a 4xx stop instead of working around it.

## 1. Inject and run the collector

```
mcp__Claude_Browser__preview_start   url: https://www.bigkinds.or.kr/v2/news/index.do
```

**Read all of** `scripts/collect.js` and evaluate it with `javascript_tool`. That creates `window.__collect`.

```js
window.__collect.run("YYYY-MM-DD")
```

Poll `window.__collect.status()` until `phase` is `done` (requests go out slower than they used to, so allow 2 to 4 minutes).

```js
JSON.stringify({p: window.__collect.status().phase, n: window.__collect.status().articleCount, r: window.__collect.status().requests, s: window.__collect.status().stopped})
```

> `javascript_tool` times out after 30 seconds, so a poll that waits 25 seconds or more with `setTimeout` fails.
> Repeating short status calls is safer.

If `stopped` is not empty, the collector stopped itself (stop rules below). Tell the user the reason as it is and do not try again.

## 2. Retrieval: export and ingest.py

Blob downloads **do not work** in the built-in browser (nothing lands in `~/Downloads`).
Instead this relies on **a tool output that exceeds the token cap being saved to disk automatically**.
It never passes through the context, so hundreds of KB move without spending tokens.

### Export

```js
window.__collect.export(60000)
```

This packs the result as gzip + base64 and cuts it into 60,000-character chunks. Note the returned `{b64_len, count}`
(`b64_len` is used later as `--expect-len`; `count` is how many times to call `chunk`).

Then call, one at a time, for `i = 0 ... count-1`:

```js
window.__collect.chunk(0)
```

Every chunk is **padded to the same length** (`|PAD|PPP...`), so even the last piece goes over the cap and is saved as a file.
When a call returns a file path, note that path. **If the content comes straight into the context, the size is too small**:
call `export` again with a larger value, not a smaller one (and start the download over).

### Reassemble with ingest.py

Shape of a saved file: `[{"type":"text","text":"\"KNSCHUNK|collect|i|count|b64len|<base64>|PAD|PPP…\"\n\n(captured at origin …)"}]`

```bash
PY "<skill>/scripts/ingest.py" --expect-len <b64_len> <file0> <file1> ...
```

`ingest.py` compares the chunk headers (kind, number, count, length) and the joined length with `--expect-len`, and checks that the
unpacked JSON is a finished collection (`phase: done`, with a date). Only then does it **create a new run folder** and write
`raw/collected.json` there. If anything is off it writes nothing, prints the reason and exits with code 1.
On success it prints one line of JSON, `{run, file, date, articles, requests, stopped}`.

The next step is `normalize.py <run>/raw/collected.json`. It writes the normalized result into the same run folder and
removes `collected.json` (so only one copy of the bodies is left). Given a `raw/collected.json` that sits in a run folder it uses
that folder; given a file from anywhere else it makes a new run folder. `--run 2026-08-21-1` picks a run folder of the same date by hand.

### The chunk files stay: show the list after the briefing, then delete

`tool-results/*.txt` piles up in the session transcript store. Each holds the gzip of the full article bodies.
It is outside the pipeline and easy to forget, but the ban on permanent storage of bodies covers it too.

1. First list only what would be deleted.

```bash
PY "<skill>/scripts/prune.py" --sweep --dry-run --chunks "<home>/.claude/projects/{project}/{session id}/tool-results"
```

2. **Show that list to the user and run it again without `--dry-run` only after they answer "예"** (yes).

A file is recognised by its **content** (the `KNSCHUNK|collect|` header, or one base64 blob in the old format), not by its name.
Other outputs of the same tool and agent transcript dumps are not touched.

> After deleting, that session cannot read those tool results when it is resumed. Use it only for a finished collection.

## The collector's politeness values and stop rules

Fewer requests go to BigKinds, and slowly.

| Value | Size | Meaning |
|---|---|---|
| `CONCURRENCY` | 2 | requests in flight at once (was 3) |
| `DELAY_MS` | 150 | wait between requests (was 120) |
| `TIMEOUT_MS` | 60000 | time limit of one request. Past it, that article is recorded as an error and the run goes on |
| `MAX_REQUESTS` | 450 | cap on requests in one collection |

Requests per outlet: list 2 + editorials about 3 + columns about 6 + 30 general articles (bodies) = about 40; about 300 for 7 papers is normal.
450 is 1.5 times the usual. When the cap is hit the reason goes into `state.stopped` and the remaining outlets are skipped without a request.

One HTTP 403 or 429 **stops everything at once** (`state.stopped = "HTTP 403|429 를 받아 전체 중단"`).
There is no retry: not working around the other side's decision is the rule of this folder. Any other error (500 and the like) puts only
that article in the error list and moves on. An outlet that stopped or had errors is counted as a failure by the coverage gate of `normalize.py`.

## Why the chunk size is 60,000

- Measured for korean-news-search: a 20,000-character chunk did not go over the tool output cap and came into the context as it was. 60,000 characters were saved to a file reliably.
- This skill used 160,000 characters before. The current format pads every chunk with a header and filler to equalize size, so a big value is not needed.
- The smaller the chunk, the more calls, but with 60,000 a day (about 250 to 300 KB of base64) takes only a few. The real size of a day and the number of calls are recorded below from the first real collection.
- So **60,000 characters** it is (a verified value, unchanged). Do not go smaller.

**Measured in the first real collection** (2026-10-28, 7 papers):

| Item | Value |
|---|---|
| Articles collected | 306 (per outlet 44, 40, 49, 44, 39, 41, 49) |
| Requests | 320 (list 14 + body 306), within the cap of 450 |
| Time | about 2 min 18 s, 0 errors, no stop |
| `b64_len` | 760,108 |
| Chunks (`count`) | 13 (60,000 characters + header and filler, 60,501 per chunk) |
| Saved as files | all 13 saved as files (none came into the context) |

- With 160,000 it would have ended in 5 chunks. 60,000 stays anyway: only this value was seen to save to files, and 13 calls cost little.
- A day is about this size (about 760,000 characters), so a chunk count around 13 is normal. Far above 20, first check whether the collection range changed.

## BigKinds API notes

Internal APIs confirmed in a browser session. No login needed.

| Endpoint | Use |
|---|---|
| `POST /api/news/search.do` | article search |
| `GET /news/detailView.do?docId=` | full body + named entities + `TMS_SIMILARITY` |

### Found by measurement

- **`startNo` is a page number, not an offset.** `startNo:101` returns 0 results.
  Used as an offset it quietly gives an empty result.
- **With `resultNumber` at or above the total, everything comes in one call.** Confirmed with 동아 277, 중앙 227, 매경 432.
- **The response order changes with the `resultNumber` value.** With `resultNumber:100`, page 1 of 동아 starts at
  23:07, and the editorials at 23:25 to 23:30, though newer, were not in it.
  -> Do not trust the server order; sort by the timestamp in `NEWS_ID` yourself.
- **`editorialIs: true` returns exactly the editorials.** Full check on 2026-08-21: 20 items, 0 false positives.
  It does not catch columns.
- **There is no opinion category.** Besides the editorial flag, the category filter has no way to tell opinion pieces.

## What the collector does

`collect.js` fetches three ways per outlet.

| Way | Cap | Reason |
|---|---|---|
| Editorials (`editorialIs:true`) | **none** | about 3 a day. Tone comparison is a requirement, so a miss means the requirement is not met |
| Columns (picked out of the list) | **none** | there is no dedicated filter. The list was already fetched in full, so they are picked out with no extra request |
| General | 30 | cut after sorting by publish time, newest first. The report must say so |

Without leaving editorials and columns outside the cap, a whole outlet's pieces go missing.
Measured: editorials went from 8 to 20 and columns from 16 to 40.
