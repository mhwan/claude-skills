# Portal notes

How the three lists are read, what the fields mean, and what usually goes wrong.

## The three lists
| kind | portal name | date shown in the table |
|---|---|---|
| `recall` | 회수·판매중지 | 등록일 (registration date) |
| `admin` | 행정처분 | 처분일(처분확정일자) (decision date of the action) |
| `unfit` | 검사 부적합 | 등록일 |

The dates differ in meaning, so the report names the basis of each. Do not compare an action's decision date with a recall's registration date as if they were the same event.

All three come from the portal's own list pages on `www.foodsafetykorea.go.kr`, newest first. The script stops paging at the first row older than the period and never goes past 30 pages. It does not use the portal's Open API host, which forbids robots.

## Recall grade (회수등급)
The grade is not in the list. It is read from each recall's detail page (the row link). Values seen: `1등급`, `2등급`, `3등급`. If the detail page cannot be read or has no grade text, the row says "등급 확인 필요" and one warning counts them. Do not infer a grade from the reason.

## Administrative action types (처분)
The `action` column repeats the portal's action name as written (for example a business suspension with a number of days, a fine, a correction order). Do not group or rank them. The legal basis column of the portal is not carried over; the detail link has it.

## Fields that are cut or cleaned
- Administrative-action reasons are cut at 200 characters with `…`; the link has the full text.
- Inspection reasons can contain line-break tags; they are replaced by spaces. HTML entities are unescaped.
- An administrative action has no product field in the list. The table shows "확인 필요" there.
- Individuals appear with a masked name. Keep it masked.
- Officials' names, staff IDs, phone numbers and representatives' names exist in the raw answers. The script never copies them.

## When something fails
| symptom | meaning | what to do |
|---|---|---|
| `failed`, error mentions robots | the portal's robots.txt (or its being unreadable) blocked the request | stop; report it; do not retry or work around |
| `failed`, error says HTTP 4xx | the portal refused | stop; report the status; do not retry in a loop |
| `failed`, error says HTTP 5xx or no response | the portal is down or slow | the other kinds were still read; a later retry is fine |
| `unread` | an answer arrived in a different shape (maintenance page, changed format) or page 1 held no rows at all | show what was read; tell the user the portal's format may have changed; do not fill the gap from memory |
| `ok` with an error text | a later page failed or the 30-page limit was reached | the table starts with the newest rows only; say "일부만 — 확인 필요" |
| `empty` | the list was read and nothing falls in the period | say it was not seen in this query |

After a 4xx or a robots block the run makes no further requests, so kinds not yet asked are `unread` with the reason "not requested".

## Tracked companies
Matching uses the company name and the product text against `config/watch.json`. Exclusion words in an entry keep look-alike names unmarked. A malformed entry is skipped and counted in a warning. The ★ is added only when the table is drawn; the data file keeps the raw company text.
