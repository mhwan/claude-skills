# Browser step for 국민참여입법센터

Read when: the report must include 입법예고 from 국민참여입법센터, or `build` says the 국민참여입법센터 file is missing or failed.

## Why a browser step

The 국민참여입법센터 list is only shown to a browser. A script that asks for it gets a guide page instead, so the script never reads it (`lawmaking_in.py` reads only the file this step saves). The browser runs `scripts/browser/lawmaking.js`, which reads the list with the browser's own requests.

## When to run it

- Only when the coordinator (the session that started this work) has said that the browser step may run now. Until then, skip it: `build` without `--lawmaking` still works and marks 국민참여입법센터 as `unread`.
- One browser session at a time. Do not run it while another skill is using the built-in browser.
- Before the first request, check `https://opinion.lawmaking.go.kr/robots.txt`. It must allow `/` (it did when the script was written, 2026-10-26). If it does not, stop and say so.
- Never open a newspaper site in the browser. Never retry a 4xx response: record it and stop.

## Steps

1. Get the browser config. Run the `plan` command (see [SKILL.md](../SKILL.md)) for the same period and agencies. Copy the single line that starts with `lawmaking_config:`; it is a JSON object with `from`, `to`, `window_start` and `departments`.
2. Open the list site in the built-in browser: `https://opinion.lawmaking.go.kr`.
3. Read `scripts/browser/lawmaking.js` (Read tool) and evaluate its whole text in that page (javascript_tool). The page then has `window.__gnw.lawmaking`.
4. Start the collection with the config from step 1:
   `window.__gnw.lawmaking.start(<the JSON object>)` — it returns `started` (or `already running`).
5. Check progress every few seconds with `window.__gnw.lawmaking.status()`. Wait until `phase` is `done` or `failed`. `done` is not the same as clean: read the `errors` list too.
6. Fetch the result as text: `JSON.stringify(window.__gnw.lawmaking.result())`. Save that text as a file with the Write tool at:
   `<output folder>/runs/gov-notice-watch/browser/lawmaking-<from>_<to>.json`
   (`<output folder>` is the absolute output folder, the same path as `--home`; `<from>` / `<to>` are the period dates.)
7. Run `build` with that file: `--lawmaking <that path>`. Use the same `--from`, `--to`, `--agency` and `--topic` as the plan.

## What the result file holds

- `job` = `lawmaking`, `period` = the requested dates.
- `requests`: one entry per department with `target`, `total` (rows the site reports), `received`, `pages`, and `status` (`ok`, `partial` or `failed`).
- `rows`: `id`, `title`, `dept`, `law_type`, `period_text` (for example `2026. 10. 15. ~ 2026. 11. 14.`), `url`.
- `errors`: the list of problems seen. A non-empty list means the source is not clean.

The file holds titles, dates and links only. No notice body is saved.

## How `build` reads it

| What is wrong | Source status | Report line |
|---|---|---|
| File missing | `unread` | 국민참여입법센터 읽지 못함 — 브라우저 결과 없음 |
| File is not valid JSON, or wrong job, or period differs from the request | `failed` | 읽지 못함 — reason |
| Any request not `ok`, or any `errors` entry | `failed` with some rows kept | 일부만 읽음 — 입법센터 수집 중 오류 N건 |
| No rows | `empty` | 자료 없음 |
| Rows, all clean | `ok` | 정상 |

A row whose period cannot be read is dropped and listed under 「확인할 것」.

## Limits and what to say

- The browser step has **not yet been run against the live site** in this skill's tests. Say that when the user is asked to rely on its output, and mark the section 「확인 필요」 until a live run is checked.
- The collector stops early when a row starts before the look-back floor, or after 15 pages (20 rows each) per department. The result then says `partial` for that department only if it was cut off by the page limit.
- If the page layout changes, the collector reports `structure` in `errors`. Say the script needs an update; do not fill the rows by hand.
- The browser step writes nothing into the report on its own. Only `build` does.
