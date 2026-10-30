---
name: food-safety-records
description: "Lists food-safety records from the Korean food-safety portal (식품안전나라) for a period as a linked table: recalls and sales stops with their grade, administrative actions, and failed inspections, with tracked companies marked ★. Use for \"최근 회수·판매중지 식품\", \"이번 달 행정처분 목록\", \"검사 부적합 나온 것 정리\", \"우리 거래처 관련 회수 있었어?\". Not for judging how dangerous a product is, and not for press coverage or ministry press releases."
---

# food-safety-records

Reads the three public record lists of the food-safety portal for one period and writes a report where every row links to the portal's detail page. The user is a non-developer: answer in Korean, plain words, conclusion first.

## Rules that always apply
- Every row has a portal link. A row never leaves the table without one.
- Anything not confirmed is written as "확인 필요": an unread recall grade, a missing product name, a list that could not be read. An empty table means "not seen in this query", never "there were none".
- No harm assessment, no advice to consumers, no guess about cause or scope. The table repeats what the portal lists.
- Masked individuals (for example `정*구`) stay as given and get the note "(개인, 이름 가림)". Never try to restore a name.
- Company names are not shortened or merged. ★ appears only for companies in `config/watch.json`; without that file there is no ★ and the report says so.
- Never request the portal's Open API host. Never open press sites, use RSS, or change the User-Agent. The script checks `robots.txt` before every request; on any 4xx or a robots block it stops everything and you report it. Never work around it.
- No API key is needed. Never ask for or print one. No browser is used.

## Steps
1. Fix the period from the request. With no period the script uses the last 14 days ending today. Ask once only if the request is unclear.
2. Run:
   ```
   PY SKILL_DIR/scripts/records.py run [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--topic TEXT]
   ```
   `PY` is `python3` on macOS and `py -3 -X utf8` on Windows. `SKILL_DIR` is this skill's folder as an absolute path. The run takes a few minutes at most: it reads list pages one request per half second, then one detail page per recall for its grade.
3. Verify:
   ```
   PY SKILL_DIR/scripts/records.py check --data DATA_FILE --report REPORT_FILE
   ```
   It must print `problems: 0`.
4. Tell the user: the conclusion in one or two lines (how many rows per kind, how many are ★), the report path, and every warning in plain words. Write each unconfirmed thing with the words "확인 필요" in the answer itself. When the status is `partial`, say in the first lines that the result is only partly read ("일부만 읽음") and which list is missing.

## Reading the output
- The run prints `status`, `rows`, the count per kind, `requests`, `report`, `data`, `run` and the warnings. Exit code 0 clean, 3 read the warnings, 1 nothing could be read (no report is written, an earlier report stays), 2 wrong flags.
- Source statuses: `ok` rows found; `empty` the list was read and has nothing in the period; `unread` an answer came but not in the expected shape; `failed` the request did not succeed or was blocked. `partial` means some kinds were read and some not. A source with `ok` and an error text was read only in part.
- Rows carry `kind` (`recall`, `admin`, `unfit`), `company`, `product`, `reason`, `grade` (recalls only) and `action` (administrative actions only). The row `id` is the kind plus the portal's own number.
- If a kind is `unread` or `failed`, say so, show what was read, and read `references/portal.md` before suggesting a retry.

## Files
- Report: `reports/food-safety-records/<period>[_topic].md`; data: `data/food-safety-records/<same>.json`; run record: `runs/food-safety-records/<date>-<n>/` with the request log. An earlier report with the same name moves to `history/food-safety-records/`.
- Tracked companies: `config/watch.json`, key `companies`, each `{name, aliases?, requires_any?, exclude_compounds?}`.
- The output root follows `GR_HOME`, else the work folder `output/`.

## References
- `references/portal.md`: read when a kind is `unread` or `failed`, when a field looks wrong, or before explaining recall grades and action types.
- `references/limits.md`: read when the user asks for something outside the three lists, a longer history, a harm judgment, or a different period basis.
