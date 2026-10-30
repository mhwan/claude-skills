# Sources

Read when: choosing which sites to read, checking why a source is missing from the report, or deciding whether a failed read may be retried.

## The four sources

| Source name in the report | What it is | Address | How it is read |
|---|---|---|---|
| `korea.kr` | 정책브리핑 press releases of each configured agency | `https://www.korea.kr/briefing/pressReleaseList.do` (view: `pressReleaseView.do?newsId=…`) | Script, list pages, one agency at a time |
| `식약처` | 식품의약품안전처 입법·행정예고 board | `https://www.mfds.go.kr/brd/m_209/list.do?page=N` (view: `view.do?seq=…`) | Script, list pages, then one detail page per notice |
| `공정위` | 공정거래위원회 행정예고 board | `https://www.ftc.go.kr/www/selectBbsNttList.do?bordCd=105&key=193` (view: `selectBbsNttView.do?…&nttSn=…`) | Script, list pages (the comment period is on the list row) |
| `국민참여입법센터` | 입법예고 list of the 국민참여입법센터 | `https://opinion.lawmaking.go.kr` | **Browser only.** Scripts get a guide page, so the browser reads it. See the browser step in SKILL.md. |

Only the four agencies in the config are read. `식약처` runs only when 식품의약품안전처 is selected, and `공정위` only when 공정거래위원회 is selected. Other configured agencies are read through `korea.kr` and 국민참여입법센터 only.

## Look-back

- `korea.kr` press releases: only the requested period.
- `식약처`, `공정위`, 국민참여입법센터: 180 days before the period start. A notice posted before the period can still be open for comment during it, so these sources look back further. The food filter and the deadline rules decide what is listed.
- Paging stops once a row is older than that floor, or after 30 list pages.

## Network rules (enforced in code, `scripts/lib/net.py`)

- Every request first checks the site's `robots.txt`. A site that refuses it (non-200, other than 404/410) is not read. The status is then `unread`.
- The default urllib User-Agent is kept. Never set a custom one.
- At most 3 redirects. A 0.5-second pause between requests to the same host.
- `GR_OFFLINE=1` (or the older `FIW_OFFLINE=1`) blocks every real request. Use it for test runs.
- A 4xx or other HTTP error becomes `failed` with the status in the message. **Do not retry it and do not try another address for the same page.** Record it and stop that source.
- Press sites of newspapers are never opened. This skill reads ministry sites only.
- Attachments (step 3b in SKILL.md) use the same rules: `robots.txt` first, the default User-Agent, the 0.5-second pause, and a stop on any refusal or HTTP error for that host. Only `https` links on the four allowed hosts (`www.korea.kr`, `www.mfds.go.kr`, `www.ftc.go.kr`, `opinion.lawmaking.go.kr`) are followed, and a redirect to any other host is refused.
- Attachment size limit: 10MB per file. The size is checked after the file is received.

## What is kept

- Titles, dates, links and the comment period, in the data file and the report.
- Page text (korea.kr, ftc.go.kr and the 식약처 detail pages) is kept only in the run's `raw/body/` folder, for the first 30 press releases and the first 30 notices. `summarize` deletes it after the summaries are checked (the rules are in SKILL.md, step 4).
- For 식약처 only the deadline phrase is kept after the run as well (a fragment of at most 80 characters, in `raw/deadlines.json`).
- Attachments: one HWP or PDF per korea.kr press release, for the first 30 press releases (in `raw/attachments.json`). Each downloaded file is kept in the run's `raw/attach/` folder, with its extracted text in `raw/attach/text/`, only until `summarize` writes the report. Then both folders are deleted. The per-row result stays in `raw/attach-log.json`. The attachment link shape on korea.kr has not been checked against live pages, so a press page with no matching link is recorded as `no_link` and nothing is guessed.

## Status words and exit codes

Each source ends in one status. The report shows them in the `- 조회 상태:` line.

| Status | Shown as | Meaning |
|---|---|---|
| `ok` | 정상 | Read, rows found |
| `empty` | 자료 없음 | Read, no rows in the period |
| `unread` | 읽지 못함 | Not read (robots refused, offline, or the browser file is missing) |
| `failed` | 읽지 못함, or 일부만 읽음 when some rows were read | An error happened: HTTP status, page layout changed, or an unreadable browser result |

The run's exit code (`scripts/govnotice.py`, from `envelope.exit_code`): 0 clean, 1 nothing could be read or the output folder is missing, 2 usage error, 3 partial result or something to look at in 「확인할 것」.

## Limits and what to say

- A source that fails is shown as failed in the report. Never fill its rows from memory.
- If 국민참여입법센터 is not read, say so: the 입법예고 list is then only from the ministry boards and may miss notices.
- The layout of a site can change. A layout change shows as `failed`, with a message that the structure was not recognised. Say that the script needs an update; do not guess the rows.
