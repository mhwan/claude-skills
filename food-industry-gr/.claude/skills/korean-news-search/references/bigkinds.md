# BigKinds collection

## Contents
- Why a browser
- Procedure (list, texts, export)
- Request and answer fields
- Limits and politeness
- Not verified

## Why a browser
BigKinds answers real browsers only; plain HTTP clients are blocked by its firewall. So the collector (`scripts/search_bigkinds.js`) runs inside a tab of the built-in browser that has `https://www.bigkinds.or.kr/v2/news/index.do` open, and uses the page's own `jQuery.ajax`. Read `https://www.bigkinds.or.kr/robots.txt` there first (see SKILL.md rules).

## Procedure
Use the built-in browser tools only. `javascript_tool` stops after about 30 seconds, so start the work, then poll with short calls.

1. Navigate to `https://www.bigkinds.or.kr/v2/news/index.do`. If a block or login page shows, stop and tell the user.
2. Read `collector` (from the `prepare` output) with the Read tool and run its whole text once with `javascript_tool`. It defines `window.__kns`. Running it again in the same tab is harmless.
3. Start the list with the `work/job.json` content as `JOB`:
   `window.__kns.startList({query:"…", start:"YYYY-MM-DD", end:"YYYY-MM-DD", provider_codes:["…"]})`
   Only these four fields are used.
4. Poll `window.__kns.status()` every few seconds. `phase` is `running`, `done` or `error`; `requests` and `items` show progress. On `error` or a result `status` of `failed`, stop and report it; do not start again more than once.
5. Export: `await window.__kns.export('list', 60000)` returns `{b64_len, count}`. Write down `b64_len`. Then read each chunk with `window.__kns.chunk(i)` for `i` from 0 to `count - 1`, one call per chunk. Each chunk is longer than the inline limit of the tool result, so the app saves it as a file and tells you its path. Collect the paths. The size must be about 60000: seen on the real site, a chunk of 20000 characters came back inline (no file), and `ingest.py` cannot read an inline answer.
6. Run `ingest.py --kind list --expect-len B64_LEN` with all the chunk file paths. It checks the number of chunks, the length and the compression, and refuses on any mismatch.
7. For the article texts, after `normalize.py` printed `to_summarize`: `window.__kns.startBodies([ids])`, poll, `export('bodies', 60000)`, read the chunks, ingest with `--kind bodies`. A second batch merges into the first.

Do not paste the whole list or any article text into a command or a message. Everything goes through the chunk files.

## Request and answer fields
List request: POST `/api/news/search.do`, JSON body with exactly these keys: `indexName` ("news"), `searchKey`, `searchKeys` ([{}]), `byLine`, `searchFilterType` ("1"), `searchScopeType` ("1"), `searchSortType` ("date"), `sortMethod` ("date"), `mainTodayPersonYn`, `startDate`, `endDate` (YYYY-MM-DD), `newsIds`, `categoryCodes`, `providerCodes`, `incidentCodes`, `networkNodeType`, `topicOrigin`, `dateCodes`, `editorialIs` (false), `startNo` (a page number, 1), `resultNumber` (up to 2000), `isTmUsable`, `isNotTmUsable`. `buildPayload` in the collector builds it; do not hand-write it.

List answer: `totalCount` and `resultList[]` with `NEWS_ID` (`<provider code>.<YYYYMMDDhhmmssSSS>`), `TITLE`, `DATE` (YYYYMMDD), `PROVIDER` (outlet name), `PROVIDER_LINK_PAGE` (original link, may be empty), `CONTENT` (not used).

Text request: GET `/news/detailView.do` with `docId` (the NEWS_ID), `returnCnt` 1, `sectionDiv` 1000. The text is `detail.CONTENT`; the collector turns `<br/>` into line breaks, removes tags and blanks.

Order: the id carries the publishing time, so rows are sorted newest first by it, then by id.

## Limits and politeness
- One answer holds at most 2,000 articles. Above that the collector halves the period, up to 3 levels deep; a single day above 2,000 is cut and the result says `truncated`.
- At most 2 requests in flight, 150 ms between requests, 60 s timeout, a hard cap of 40 list requests. No retry after an HTTP error. After a 403 or 429 on a text, the remaining texts are not fetched.
- The list is capped at 200 rows per report and 30 summaries per run (`scripts/common.py`, `CAPS`).

## Seen on the real site (2026-10-29, built-in browser, no login)
- The collector ran in a real tab. It was pasted without its header comment, and the no-break space in the `cleanBody` pattern may have become a plain space; not exercised otherwise.
- Query syntax, period 2026-10-26~2026-10-29, base query "설탕" = 69 articles: `설탕 담합` (space) = 6 (AND); `설탕 -담합` = 63 (63 + 6 = 69, so `-` is NOT); `"설탕 담합"` (quotes) = 1, a subset of the AND answer; `설탕|담합` and `설탕 | 담합` = 0, so `|` is NOT OR; `설탕 OR 담합` and `설탕,담합` = 184 (69 + 121 − 6, so OR). One query and one period only.
- Provider codes and names: the 7 known codes matched the returned `PROVIDER` names exactly. Filtering by 조선(01100801)+중앙(01100901) gave the same 5 rows as filtering the whole list by name.
- Whole period against halves: 10-26~10-29 gave 69; 10-26~27 gave 45 and 10-28~29 gave 24; the id sets are identical, no overlap. Done by hand: the collector's own splitting (answer above 2,000) did not run.
- 3 of 69 rows had no original link (`PROVIDER_LINK_PAGE` empty); the report must mark such rows.
- Texts: 9 of 9 fetched, 0 failed. `status()` showed `requests` 0 and `done` 9 for the texts, so read `done`, not `requests`.
- BigKinds matches the query in the article text, so many results mention the word only in passing.

## Not verified
- Query syntax for other queries and periods; whether `OR` and `,` differ in some cases.
- Provider codes other than the known 7 are only read from ids, never used as a filter.
- The collector's halving of the period above 2,000 articles, and the 40-request cap, on the real site.
- Behaviour on a real Windows app.
