# Constraints and limits

The boundaries this skill keeps, and the limits a briefing must state for itself.

## Contents
- [What this skill does not do](#what-this-skill-does-not-do)
- [Copyright handling](#copyright-handling)
- [Known limits](#known-limits)

---

## What this skill does not do

### Collect blocked outlets directly

중앙, 한겨레, 경향 and 매경 disallow `ClaudeBot` and `anthropic-ai` with `Disallow: /` in robots.txt.
So do 한국일보, 국민일보, 연합뉴스, KBS, JTBC and others. 네이버뉴스 is blocked entirely.

robots.txt is not a technical barrier but a **statement of intent**. A browser could open those pages,
but that is in substance the same as forging the UA string.

BigKinds is different. It is run by the Korea Press Foundation (a public body) under content-supply agreements with
the outlets, so it is an **official distribution channel**, and its robots.txt says `User-agent: * Allow: /` (only GPTBot is blocked).
It is the front door, not a detour.

> When adding a source, **check its robots.txt at that moment.** Policies change.
> Do not trust the list written here.

### Fake the UA

The link check in `normalize.py` also used an honest UA.
Measured, faking was a loss: 매경 returns 403 to `Mozilla/5.0` and 200 to an honest UA.

### Scheduled bulk collection

The 매경 `llms.txt` states what is allowed.

- Allowed: real-time search and RAG for a user's query, minimal summaries, source links
- Forbidden: model training or building datasets, permanent storage of full text, rebuilding an archive,
  combining title, lead and body to reproduce a piece so fully that visiting the original is unnecessary

**A manual call is close to the allowed range; scheduled bulk collection is close to what is refused.**
Reconsider the policy before automating.

## Copyright handling

Mandatory, because the pipeline handles full article bodies.

- Use the full body only as **intermediate data for producing the summary**. Never reproduce it in an output.
- A briefing carries only its own summary, the title, the original link, the publish date and the outlet name.
- Only text inside quotation marks is verbatim. At most 40 characters in total per editorial.
- Do not reproduce a piece in so much detail that it replaces a visit to the original.
- State the source (한국언론진흥재단 BigKinds) at the bottom of the document; the renderer inserts it automatically.
- **Do not keep full bodies permanently.** Once the briefing passes verification, discard them with `prune.py`.

### Why discarding is the rule

The pipeline makes the full bodies in **two copies**: `raw/` and `work/*.todo.json`.
That is exactly the "permanent storage of full text" the 매경 `llms.txt` refuses.
The size (measured 6.4 MB down to 476 KB) is a side matter; the principle is what is violated.

Only titles, links and publication metadata stay in `raw/`, because the 7-day stale gate (G4) reads titles
and the titles are already in the briefing. Bodies, subtitles, named entities, similarity vectors and sentiment are deleted.

The upshot: **the summary alone cannot be fixed and redrawn.** Without `todo.json` the evidence gate cannot run,
and collecting again is judged better than re-rendering without verification.

### A pruned date is refused by the pipeline

`cluster.py` and `render.py prepare` look at `raw['pruned']` and stop with exit code 2.
If they did not, they would **pass silently**: measured, `cluster.py` exited 0 while turning
201 of 226 articles into 「미분류」 (there was no `category`). `render.py` builds a work order whose bodies
are all empty, and then the evidence gate judges every summary sentence as invented.
The cause sits three steps upstream, which makes it hard to find.

## Known limits

What the renderer states automatically so that a briefing discloses its own limits, and what a person must know.

| Limit | How it is shown |
|---|---|
| General articles capped at 30 per outlet | truncation notice in the header, automatic |
| An outlet that did not comment on that issue that day | `⊘ 표본 내 미포착`, automatic (per issue) |
| A category with 0 editorials | `🕳️ 공백`, with editorial and column described separately, automatic |
| Manual category correction | `⟳`, automatic |

What a person must know:

- **경향신문 has 0 columns.** 경향 puts no bracketed tag on columns, and its URL is the flat
  `/article/{id}`, so the current signals cannot identify them. Allow for this asymmetry in a tone comparison.
- **False positives like `[이혼의 세계]`.** A topic-style section name trips the person-column pattern.
  Checking with `byline` was tried, but 12 of 39 are missing, and 3 of the 4 mismatches
  were genuine columns (a reporter using a pen name, an outside writer), so **using it as a condition loses genuine columns.**
  The 「이」 of 「이혼」 is also a family name, so a structural distinction is impossible. This is accepted.
- **Opinion pieces are misfiled by category.** The BigKinds category is used as it is.
  Measured misfile rate 39% (7 of 18). For example, an editorial on the 미래대응기금 is filed under politics.
- **Cluster merging is not promoted.** A rare-keyword guard reached 100% precision, but the sample is thin,
  so nothing is merged automatically. It is only marked in `merge_candidates`.
- **The `sentiment` field is not used.** 6 of 8 editorials are `NEUTRAL`, and the two 🔴 strong-criticism
  cases were exactly `NEUTRAL` and empty. It is the sentiment of the article's vocabulary, not an outlet's tone.
- **What the evidence gate cannot catch.** Inventing a causal link in prose, with no number or quote,
  cannot be verified by machine.
