# Writing rules — `work/summary.json`

Read every time before writing the summary. Write in Korean, plain report style (「~했다」). The evidence gate (`fiw.py verify`) enforces most rules mechanically; the rest are checked by the human review. A sentence the gate rejects is rewritten, never argued with — **do not loosen a claim's evidence to get it through; change the claim.**

## Contents
- [What you write and what you never write](#what-you-write-and-what-you-never-write)
- [Refs and evidence](#refs-and-evidence)
- [Rules](#rules)
- [Fact vs edit sentences](#fact-vs-edit-sentences)
- [Flow nodes](#flow-nodes)
- [Earlier issues and corrections](#earlier-issues-and-corrections)
- [Own company](#own-company)
- [Summary head](#summary-head)
- [Writing a part](#writing-a-part)
- [Checklist](#checklist)

---

## What you write and what you never write

Write: issue titles, three-line heads and sentences, issue sentences (3–5 sentences and 200–400 characters per issue, all its sentences joined; E10, limits in `config/format.json`; market topics have no such limit), actors, flow name and flow node texts, press grouping labels, market topic sentences, own-company sentences.

Never write (the renderer computes them): `N사M건` tags, company tags, 「이번 보도」 dates, header numbers, remaining days, the open-comment list, hygiene counts, footnotes, link labels.

## Refs and evidence

- Every field carries `refs` — the todo refs (`c0012:1`) of **its own issue** whose body or title contains what the field says. A ref from another issue is a structure error.
- `evidence[]` = `{ref, quote}` where `quote` is copied **exactly** from that ref's body or title (whitespace may differ), at most 80 characters, and contains the risky expression it supports. (The `facts.json` quotes of a part are a different thing: at least 12 characters, no upper limit.)
- A sentence may use only what its refs' bodies or titles say. Words of a body beyond the part's `facts.json` are allowed when they are in that body, but the part's sentences should come from its facts.
- A todo issue's `flow_candidates` refs (`I-0070:f1`) belong to that issue: any of its fields may cite them, checked against the candidate's title and excerpt only. Use them mainly for `flow` nodes; a body ref (`c0012:1`) is better for a sentence.

## Rules

1. **Numbers** exactly as in the source body. No rounding, unit conversion or arithmetic (not 「10년 2.2조」 when the body says 「2조1,911억원」). Counts like 「61곳」「937건」 are checked too.
2. **Dates** only as written in the sources.
3. **Quotes** — anything in `"…"`, `“…”`, `'…'`, `‘…’`, `「…」` must be verbatim. At most 40 quoted characters per issue in total.
4. **Speech** — when anyone (a person, an agency or a company) 「주장했다·밝혔다·말했다·요구했다·지적했다·강조했다」, either quote their words directly or make the article the subject: 「기사는 …라고 전했다」 (E13 checks every such subject). Do not attribute an unattributed article sentence (「…라는 지적이다」) to a person. The same holds for every verb that reports what someone said or thinks (「촉구했다·주문했다·분석했다·판단했다·전망했다·설명했다」 …): a named speaker needs that speaker in the ref doing it; the gate checks only the six verbs above, the review checks the rest.
5. **Limiting phrases and modifiers** — 「같은 기간」「최근 N년」「올해」「지난해」「N월부터」 (also 「N일부터」「N년부터」)「이후」「한 차례」「처음」「모두」「가장」「최대」 need an evidence quote containing the phrase. If the source uses a different span (「최근 5년간」), use the source's words.
6. **Pairs** — a sentence with two or more numbers and two or more companies/agencies needs one evidence quote per company–number pair (prevents swapping 「삼양사 4,354억」 and 「CJ제일제당 3,730억」). One quote may cover several pairs when it holds each company next to its number.
7. **Judgment words** — 「이어지」「연속」「잇따」「계기」「때문」「최대」「최초」「처음」「가장」「마쳤」「마무리」「완료」「공동」 in a fact sentence need an evidence quote with the same word; otherwise the sentence is an edit sentence (below) or must go. 「함께」 needs the word in the sentence's refs (a joint act the source does not state — 「같은 취지의 소송」 is not 「함께 낸 소송」). Keep the scope the source gives the word: a completion limited to one stage or product says that stage (「1단계 서류 요건 준비를 마쳤다」), and 「처음」「최대」 that a party said keeps the speaker (「기사는 검찰이 …을 처음이라고 밝혔다고 전했다」).
8. **Institutions and people** — name only agencies, companies, committees and lawmakers that appear in the refs. No background knowledge (not 「농식품부 산하 낙농진흥회」 when the article does not say so). A person's name counts only as a body writes it: headlines shorten and mistype names (a headline 「윤병준」 over a body that has only 「윤 의원」), so write the body's form (「윤 의원」) or cite a ref whose body names them.
9. **Outlets** — name an outlet only if it is the outlet of a ref.
10. **Title–body conflicts** — if the todo lists a title number absent from the body, use the body's number. Mention the title only to say both sides — 「(제목은 5.6%로 적었으나 본문은 5.9%)」 — or not at all; a bare 「기사 제목은 X다」 tacked on is not a note.
11. **Unknown is null** — leave out what the sources do not say; never fill with an empty string. If an article itself says it is not final (전망·관측), keep its wording and set `unverified: true` (rendered as 「(미확인)」). An outlook the article gives without a speaker (「…할 것으로 전망된다」「…라는 관측이 나온다」) keeps the article as its subject (「기사는 …할 것으로 전망했다」) and `unverified: true`; never give it a speaker the article does not name. An outlook a named speaker gives keeps that speaker as rule 4 says (「업계는 …할 것으로 전망했다」 only when the ref names 업계 doing it), also with `unverified: true`.
12. **Conditions** — keep a source's qualifier that changes what a number or step means: 「일단」「우선」「일부 청구」「감정 뒤 확정」「1단계만」. A claimed amount that the source says is provisional is written as provisional.
13. **Scope in titles and heads** — a shortened line keeps the subject the source limits: if the source says only 「문제의」「해당」「일부」「이 중」 items, the title says so too or leaves that clause out (not 「중국산 줄기상추…국내 수입 이력 없어」 when only the product in the local report had none).
14. **「오는」 dates** — 「오는 19일」 is relative to the article's day and reads as still to come. If that date is on or before the end of the report period, drop 「오는」 and keep the date as the source writes it (「19일부터 시행」 — adding a month the source does not write breaks rule 2); say it took effect or happened only if a source says so.

## Fact vs edit sentences

- `fact` — states what the refs say. Most sentences.
- `edit` (rendered with 🧭) — only grouping, linking issues into a flow, or classification judgments, e.g. 「전분당에 이어 제분 부산물까지 원료 담합 제재가 이어지고 있다」. Needs `links` (the issue keys it connects) and at least two refs; may only use names and numbers present in those issues' refs. Background explanation is not allowed even as an edit sentence.

## Flow nodes

- `mode: "title"` — the node text is the article title exactly, minus leading 말머리 like `[단독]`; the renderer wraps it in 「」. Prefer this mode.
- `mode: "summary"` — your own short text; it must pass rules 1–3, and assertive words 「안 돼」「불가」「금지」 may only appear inside quotes (not 「국내 수입 불가」 for a title 「…수입 안 돼」).
- `flow_name` is an edit judgment: `kind: "edit"` with `links` (for a flow inside one issue, `links` is that issue's key alone).
- A flow is optional: write one when the todo lists flow candidates and they show steps of the same matter; otherwise leave `flow_name` and `flow` out.

## Summary head

This is the head of a run's summary. The 세 줄 of a period report is a different file with its own rule (the contracts reference, § Period report step 6).

`work/summary_head.json` (`fiw.summary_head/1`) is written once, after every part passes `parts check`, by whoever runs the run (not a part subagent). `parts merge --head` puts it into `summary.json`; `verify` checks it like any other field.

- `three_lines` — exactly min(3, todo issues) entries `{issue, head {text, refs}, sentences}`, each a different todo issue key. Prefer the issues with the most outlets, then articles — count them from todo `coverage.outlets` and `coverage.articles` (the report's 「N사 M건」), not from the articles a todo issue carries. Read them from the issue table of `work/parts/index.md` (key, coverage, refs, most outlets first) — not from `work/todo.json`, which holds article bodies. (A period report orders its 세 줄 by impact instead — the contracts reference, § Period report step 6.) `head` is one short line; `head` has no `evidence` field, so `verify` checks it only for numbers, dates, quotes, names and outlets against its refs — the limiting phrases, pairs and judgment words of rules 5–7 are not checked on a head: leave them out of it (「11월 1일부터」 belongs in a sentence with its evidence quote); `sentences` are 1–2 sentences (`{text, kind, refs, evidence}` — evidence as in any sentence) written under these rules from that issue's facts and bodies — they may repeat that issue's sentences. Refs only of that issue.
- `notices` — `[{id, label}]`, `id` exactly the item's `id` in `raw/collect/korea_kr.json` of the run folder (bare, not `korea_kr/…`), for korea_kr press releases whose final verdict is `include`; `label` is a short tag whose pieces (split at spaces, `()·,/`) each appear in the press title (E14), or `null` for the bare title. Give a label when the title is long or buries what changes (the instrument and its subject, e.g. 「식품 표시기준 개정」); keep `null` when the title already says it in a few words. `[]` is allowed. Three or more labelled items of one category render as one grouped row.

## Earlier issues and corrections

For a todo issue with `prior`, write what is new in this period (the earlier sentences are context, not text to repeat). `prior.corrections` lists the user's earlier corrections of this issue (`- old` / `+ new`): never bring back a `-` wording. A `locks.rename` title must be used exactly (the gate checks it). A correction file (`fiw.correction/1`) follows these same rules: its `new` value must pass the evidence gate on the source text, and a date or number not literally in the source is refused even when it is a correct calculation (「지난달 23일」 cannot become 「9월 23일」).

## Own company

`lwf.direct: true` only when a ref's body or title names the own company (`watch.json` `own_company`); the sentences say what that article says about it — no impact interpretation. When the todo marks a ref with `lwf_mentions: true` but you do not show it, set `direct: false` with `not_shown_reason` (e.g. 「사진 설명에만 언급」).

## Writing a part

When the summary is written in parts (`parts split`), work on one part only, in this order:

1. Read `work/parts/NN/bodies.md` with the Read tool, in pieces, to its last line. Never print it through a shell command.
2. Write `facts.json` before any sentence: for each body the 1–3 facts that matter for the report, each `text` in your words and `quote` copied exactly from **that** body (≥ 12 chars, containing every number of `text`); a body with nothing for the report gets `unused` and why. Check a fact against the end of its body too — a later sentence may withdraw, limit or correct it.
3. Write `summary.json` from those facts (a word beyond them only when it is in that ref's body — § Refs and evidence), then `parts check --part NN`. Fix every finding; F4 `섞임` means a quote came from another body — take the fact from its own body or drop it.

## Checklist

- [ ] Every todo issue and market topic has an entry; `three_lines` has min(3, todo issues) entries.
- [ ] Every number, date, quote, name and outlet is in that field's refs.
- [ ] Risky expressions carry exact evidence quotes.
- [ ] Edit sentences carry `links` and ≥ 2 refs.
- [ ] `fiw.py verify` exit 0 — fix the sentence, never the gate.
