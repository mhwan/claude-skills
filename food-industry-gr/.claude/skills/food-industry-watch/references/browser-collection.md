# Browser collection — news bodies

The only thing this skill reads in the built-in browser is the **body of BigKinds articles**, which `prepare` needs to write and check summaries (script clients are refused by the BigKinds firewall; use is permitted by the foundation). The article list itself comes from the `korean-news-search` skill through `data/`, not from here. Everything else (notices, bills, schedule, safety records) is never fetched by this skill.

The job is a JavaScript file evaluated on a BigKinds page; results come back to disk in chunks and `fiw.py ingest` turns them into the run's `work/bodies.json`.

## Contents
- [Procedure](#procedure)
- [Failures](#failures)
- [What is measured and what is not](#what-is-measured-and-what-is-not)

---

## Procedure

The job is `bigkinds_bodies`. It runs when `prepare` exits `3` and writes `work/body_requests.json` (the article ids it still needs); repeat until `prepare` stops asking. Rows of the Naver substitute (ids `naver-…`) are never requested: when `prepare` exits `3` only with 「뉴스(네이버 대체판) 확인 필요」 lines and no `body_requests.json`, there is nothing to fetch — carry on. Never open a Naver or outlet page to get such a body.

0. Once per run, before the job: open `https://www.bigkinds.or.kr/robots.txt` in the browser and check the job's paths are not disallowed (the constraints reference, § robots.txt — per host). If a path is disallowed, skip the job and report it. Hosts the scripts request (`linkcheck`) need no browser check: `lib/net.py` `RobotsCache` reads each `scheme://host`'s robots.txt once per run before the first request there (404/410 = no robots, any other non-200 = refuse), unless `assets/defaults/link_policy.json` gives the host `never_request`/`no_check` (no request) or `robots_seed` (the recorded robots.txt is used); a refused URL is reported, never retried another way.
1. `PY "<skill>/scripts/fiw.py" browser-script bigkinds_bodies` prints `page:` (a page of the site), `script:` and `export:` (absolute paths), and `chunk:` (the chunk size `doctor` verified for this app).
2. Open `page` in the built-in browser. Read the `script` file and evaluate its **whole text unchanged** with the browser JavaScript tool; it returns `bigkinds loaded`.
3. `PY "<skill>/scripts/fiw.py" browser-config --run <id> --job bigkinds_bodies` prints one JSON line. Evaluate `window.__fiw.bigkinds_bodies.start(<that line>)` → `'started'`.
4. Poll with short calls (the tool call limit is about 30–45 s): `await new Promise(r => setTimeout(r, 20000)); window.__fiw.bigkinds_bodies.status()` until `phase` is `done` or `failed`. `done/total` shows progress; `errors` lists the first ten problems.
5. Evaluate the whole `export` file, then `await window.__fiw.export('bigkinds_bodies', <chunk>)` → `{b64_len, chunks}`.
6. For `i = 0 … chunks-1`, one tool call each: `window.__fiw.chunk(i)`. Each result is too large to show and the app saves it to a file (`…/tool-results/….txt`); note every path.
7. `PY "<skill>/scripts/fiw.py" ingest --run <id> --job bigkinds_bodies --expect-len <b64_len> <file> <file> …` (files in any order).

Exit `0` = written and complete; `3` = written with some bodies failed (the articles are left out of the summaries and listed as excluded); `1` = the chunks do not fit together (wrong file, missing chunk, wrong length): redo steps 5–7, do not edit files; `2` = command error.

Bodies are working data only — removed by `prune`, never in a report beyond the quoting limits. Navigating away or reloading loses the page state (`window.__fiw`): evaluate the job script again — and the `export` file before step 5 — in every new tab and after any navigation. Never print `result()` in full into the conversation — it can hold thousands of articles. Bill links (`likms.assembly.go.kr`) are disallowed to robots; they are links for people only and are never opened by the skill.

## Failures

- No retry after any HTTP error; that article is marked failed and the run goes on.
- Each request times out after 60 s. A timeout counts as a failure, not as an empty body.
- When the job ends `failed`, still export and ingest it, so the run records the failure instead of silently missing bodies.
- If BigKinds is slow (single requests taking 20 s or more), do not start the job twice in parallel.

## What is measured and what is not

Measured on macOS, 2026-10-26: a 1.1-million-character tool result is saved to a file intact. Chunks are 150,000 characters unless `doctor` verified a larger marker on this machine (the first-run reference, § 4 step 5), then up to 1,000,000. Not measured: behaviour in the Windows app (`doctor` checks the large-result file before the first body fetch), the cause of the BigKinds slow-down.
