# Summary rules

Read when: writing `raw/summaries.json`, when `summarize` refuses a summary, or when explaining why a row shows `요약 확인 필요`.

## What a summary is for

The 「요약」 cell tells the reader what a press release or notice is about, in one or two sentences, without opening it. It is written from the page text of that row only.

## Which rows get one

- The first 30 press releases and the first 30 notices get a summary. Each table has its own cap. Among notices, the ones still open for comment come first. The cap is `SUMMARY_CAP` in `scripts/govnotice.py`.
- A row past its table's 30th shows `요약은 앞 30건만`. That is correct, not an error.
- A row needs source text. The source is `raw/attach/text/<id>.txt` (the text of its HWP or PDF attachment) when that file exists; otherwise `raw/body/<id>.txt` (the page text, written by build). The summary records which one it used (`body` in `raw/attach-log.json`: `attach` or `page`).
- Build writes `raw/body/<id>.txt` for each row in those slots that has page text. Rows from `국민참여입법센터` have none, so they show `요약 확인 필요` and the reader must check them on the source page.
- A press release with a downloaded attachment whose text was not saved is refused (see the table), even when its page text exists. Read the attachment first, or leave the row out.

## The file

`raw/summaries.json` is one object keyed by row id. Each value has `summary` (text) and `terms` (a list of names that must appear in the page text).

```json
{
  "kr-1001": {"summary": "가공식품 표시 항목을 늘리는 개선 방안이 나왔다. 의견은 2026년 11월 9일까지 받는다.", "terms": ["가공식품"]}
}
```

The keys must be row ids from the data file (`kr-`, `mfds-`, `ftc-`, `lm-` prefixes). An id that is not in the data file is refused.

## The checks

`summarize` refuses a summary when any of these is true. The refused row keeps `요약 확인 필요`, and the reason is printed.

| Reason printed | Cause |
| --- | --- |
| `형식이 맞지 않음 (summary 글자 필요)` | `summary` is missing or not text |
| `요약이 비어 있음` | `summary` is blank |
| `요약이 N자 (최대 200자)` | longer than 200 characters after spaces are normalised |
| `숫자 X 가 원문에 없음` | a number in the summary is not in the page text (commas and spaces are ignored when comparing) |
| `인용 「…」 가 N자 (최대 40자)` | a quoted phrase is longer than 40 characters |
| `인용 「…」 가 원문에 없음` | a phrase in 「」 is not in the page text, word for word |
| `고유명사 X 가 원문에 없음` | a name in `terms` is not in the page text |
| `원문이 없음` | no source text for this row (neither `raw/attach/text/<id>.txt` nor `raw/body/<id>.txt`) |
| `원문이 없어 확인 못 함` | a rerun after cleanup, for a row with no summary yet (see below) |
| `첨부는 받았으나 글로 추출하지 않음` | the row has a downloaded file in `raw/attach/` but no `raw/attach/text/<id>.txt` |
| `첨부 글 추출 결과가 비어 있음` | `raw/attach/text/<id>.txt` exists but is blank |
| `첨부 원문이 있어 제목 기준 요약은 쓸 수 없음` | the summary contains `(제목 기준)` and attachment text exists for the row |
| `앞 30건 밖` | the row is past its table's first 30 |
| `데이터에 없는 행` | the id is not a row of the data file |

Accepted summaries are written with spaces normalised. Each quoted phrase is 40 characters or fewer; the script refuses a longer one.

## Writing a summary

- Use the page text only. Do not add background, dates or figures that the page does not state.
- Put the subject first: what the notice or release proposes, orders or reports. Then the one date or deadline that matters to a reader, if the page states it.
- Quote 40 characters or fewer, and only when the exact wording matters.
- When the page text is unclear, incomplete or not about the row, leave the row out of `raw/summaries.json`. It then stays `요약 확인 필요`. A missing summary is better than a guessed one.

## Title-only summaries

- A summary that rests on the title alone is written with `(제목 기준)` in its text, without 「」. The report shows it as it was written.
- Without attachment text, it is accepted, since the page text is the only source.
- With attachment text for the row, it is refused. Write the summary from the attachment text instead.

## After `summarize`

- The report is re-rendered from the data file, so the summaries show in both tables.
- `raw/body/` and `raw/attach/` are deleted, and only after the report is written. `raw/summaries.json`, `raw/deadlines.json`, `raw/filtered.json` and `raw/attach-log.json` remain.
- `raw/attach-log.json` holds, for each press release, the attachment result (`attach`: `ok`, `refused`, `failed` or `unread`, with the `reason`), which source the summary used (`body`: `attach` or `page`), and the summary result (`summary`: `accepted` or `refused`, with `summary_reason`). Build also writes the `no_link` and `listed` states there.
- A second run is safe. If `raw/body/` is already gone, the command prints `이미 정리됨`, keeps the summaries accepted earlier, and refuses a new summary for a row with no source text as `원문이 없어 확인 못 함`. Attachments are never downloaded again into the same run: `attach` refuses a run whose log already has download results.
- Exit code 3 means some summaries were refused. It is not a failure of the whole run.
