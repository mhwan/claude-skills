# Summary rules

A summary says what the article reports, in Korean, in one or two sentences of about 100 characters (hard limit 200). It is written from `work/articles.json`, which holds the article text.

## File format
`work/summaries.json`:
```
{"summaries": [{"id": "<article id>", "summary": "…", "quote": "…"}]}
```
- One entry per article in `work/articles.json`; the id must match. A missing entry, a duplicate or an unknown id fails the check.
- `quote` is optional. Leave it out when no phrase is worth showing.

## What to write
- Facts of the article only: who did what, what the number or decision is, when. No opinion, no forecast, no advice, and no link to other events that the article does not state.
- A figure, date or amount goes in exactly as the text writes it (`1,200원` may be written `1200원`). Never round, convert or add a unit the text does not use.
- Name a person or company only as the text does. Do not give a title that is not in the text.
- Use an English word or abbreviation only if it is in the article text.
- Say who said it when the article reports a claim: write 「~라고 밝혔다」 style, not as a plain fact.
- A quote is a phrase copied word for word from the text, at most 40 characters, with no edits. It appears in the report with the outlet name and the link of the article.
- Do not copy more than 40 characters in a row from the text into the summary: write it in your own words.

## What the script checks
`verify_summaries.py` checks, for each summary: every number (comma ignored) is in the text; every Latin-letter word is in the text; every 2–4 character name in front of a title word (대표이사, 대표, 회장, 사장, 위원장, 장관, 의원, 국장, 청장, 총리, 대통령, 교수, 이사) is in the text; the quote is at most 40 characters and is in the text word for word; no run of more than 40 characters is copied; the length is 1–200 characters.

It cannot check meaning. A wrong verb, a swapped subject or a claim with no number, English word, name or quote passes. So after the check, read each summary once against its text for these: who did it, what direction (up or down, approved or rejected), and whether a claim is stated as fact when the article only reports it.

## When the check fails
`work/verified.json` lists the problem per article (`number:`, `latin:`, `person:`, `quote:`, `copy:`, `length:`, `missing:`, `unknown:`, `duplicate:`). Fix that summary from the text, not by editing the text or the script. If a number cannot be supported by the text, drop the number from the summary.

## Articles without a text
The text could not be fetched (`failed` in the bodies file or an empty body). Do not summarize from the title or from memory. The article stays in the list with its link and no summary, and a warning names it.
