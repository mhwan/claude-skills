# Review guide — checking a report against its sources

For a reviewer who did not write the summary: you get `fiw.py review` output (one line per field, with its links) and nothing else. Do not read the run's work files or the writer's notes.

Open each link in the browser and give every line exactly one verdict:

| Verdict | When |
|---|---|
| 일치 | Every fact in the line is in the linked source(s), with the same meaning. |
| 불일치 | A fact differs from the source: a number, date, name, place, who did what, or what stage something is at. |
| 근거 없음 | A fact is in none of the linked sources (it may be true, but these links do not say it). |
| 중요 사실 누락 | Each fact is right, but the line leaves out a fact of the source without which the reader would misread it — e.g. the measure was withdrawn, only proposed, limited to one product or region, or reversed later in the same article. |
| 의미 왜곡 | The facts are right one by one but the line puts them together into something the source does not say — cause and effect the source does not draw, a party's claim written as fact, a minority view written as the consensus, or hedged wording (검토·추진·전망) written as decided. |

Write each verdict that is not 일치 as `[line] verdict — what the source says (≤ 15 words of your own, no copying)`. A source you cannot open (paywall, 4xx, removed) is `열람 불가` — not a verdict; stop there for that link, do not look for another copy.

The run passes when every line is 일치 (or 열람 불가 with the reason).
