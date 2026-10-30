# Summaries file and report rules

Read this before writing the summaries file or answering questions about the report layout.

## Summaries file
JSON written by the assistant, passed with `--summaries`:

```json
{"meetings": [
  {"conf_id": "N054700", "basis": "minutes",
   "items": [{"summary": "원산지 표시 강화 질의가 이어짐", "quote": "짧은 인용"}]}
]}
```

- `basis` is `minutes` (from `assembly_meeting_speeches`) or `caption`.
- Up to 5 items per meeting; extra items are dropped with a warning. Keep each summary to 1-2 sentences, paraphrased.
- `quote` is optional and at most 40 characters. A longer quote is removed and a warning names the row.
- A `minutes` entry takes committee, date and link from the meeting list entry with the same `conf_id`.
- Do not copy long passages of the minutes. The report links to the minutes instead.

## Data rows
- Kinds: `schedule`, `speech`, `audit_report`. Bases: `schedule`, `minutes`, `caption`, `clips`, `report`.
- Extra fields per row: `basis`, `kind`, `committee`, `time`. The speech summary text lives in `title`.
- Every row has a link or `url: null` with a warning that contains the row id.

## Report
Sections in order: schedule table, speech summaries, audit correction/handling requests, items to check.
Do not interpret bill stage names. A section with no hits says "not seen on this page, check next page".
