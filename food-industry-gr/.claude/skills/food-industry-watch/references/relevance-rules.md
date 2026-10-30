# Relevance rules — writing `work/relevance_brief.json`

Read before judging clusters and items. The reader works on a food company's government-affairs (대관) team and needs what government, the National Assembly and the industry did that can change rules, prices, enforcement or the company's standing. **Missing a relevant issue is worse than including noise** — when in doubt, `other` (it stays visible under 「그 밖」), never `noise`.

## Contents
- [Inputs](#inputs)
- [Cluster verdicts](#cluster-verdicts)
- [Sections](#sections)
- [Linking to existing issues](#linking-to-existing-issues)
- [Primary-source items](#primary-source-items)
- [Writing the file](#writing-the-file)
- [Self-check before the gate](#self-check-before-the-gate)

---

## Inputs

- `work/clusters_brief.md` — every cluster that needs a decision, one line each (cid, outlets and articles, span, representative title, tracked entities), largest coverage first. Read all of it; do not stop at the top. `work/clusters_view.md` shows the same clusters with up to five titles — open a cid there when one title cannot settle it (a vague or mixed headline). Clusters in neither file (one outlet, no signal word or own company in any title) are treated as `noise` automatically; you may still add an entry for one (by its cid in `work/clusters.json`) when you know it matters.
- `work/items_view.md` — every press release, notice and bill that needs an entry: all `undecided` items and the keyword `include` items of `korea_kr`, the notice sources and `assembly_bills` (confirm or overturn them). `other` items need no entry. Verdicts and rules are in `work/prefilter.json`.
- Earlier issues — the top of `clusters_brief.md` lists every issue of `state/food-industry-watch/issues.json` (id, section, status, last article, title; 🔒 = the user set a lock — read it in that file before linking that id).

## Cluster verdicts

| verdict | Use for |
|---|---|
| `issue` | Policy, regulation, enforcement, prices, collusion/fines, food-safety incidents, Assembly activity (bills, hearings, lawmakers' data releases), industry statements on these, the own company directly involved, and market trends worth a market topic |
| `other` | Related but minor or unclear; coverage worth listing but not a table row; overflow beyond a section's row cap |
| `noise` | Clearly unrelated: new products, promotions, events, local festivals, sports sponsorship, drug/medical news pulled in by an agency name, homonyms (「대상」 as "target") |

Specific rules:
- **D62** — Issues raised by agencies not on the tracked list (financial regulator, consumer agency, IP office, …) stay `issue` when a tracked company appears in the articles.
- **D16** — Food-service (외식) news is out of scope **unless** a tracked company is mentioned alongside (e.g. a franchise price increase reported together with manufacturers' increases).
- **D38** — A food-safety record (`foodsafety`) naming a tracked company is always shown in the hygiene section's 「추적 회사 해당」 line. When news clusters report the same case, make that cluster an `industry` issue.
- Collusion investigations, fines and court steps on food raw materials are `gov` issues even when only companies are named.
- **Own company** is `watch.json` `own_company` only. Other companies of the same group (롯데마트, 롯데칠성음료, …) are tracked or other companies, not the own company.
- **Own company vs promotion** — own-company news on rules, prices, safety, labour, courts or the company's standing is an `industry` issue. When the own company appears only in a launch, promotion, ad or event (빼빼로데이 판촉), it is `other` in `industry`, not `issue` and not `noise` (the reader still sees own-company coverage). Other companies' promotions stay `noise`.
- **Market `issue` vs `other`** — a `market` cluster is an `issue` when it fits a `market_topic` and carries numbers or two or more outlets, or is a tracked company's own results, investment or exports; a passing mention, a single-outlet listing or a column is `other`.
- A cluster mixing two different events: judge by its representative; mention the mix in `reason` (the reviewer checks `--cluster-members`).

## Sections

`issue` and `other` both need a section (`other` is listed under that section's 「그 밖」); `noise` takes `null`. `gov` (government and regulators) · `assembly` (the 국회 ① table — lawmakers, committees, hearings) · `industry` (companies, associations, incidents, own company) · `market` (business context; set `market_topic` to one of `수출·해외`, `저당·제로`, `원가·환율·가격`, `투자·설비`, `실적·주가`, `인사·지배구조`). An item that touches government relations directly (a price hike under government pressure, a fine) goes to `industry` or `gov`, not `market`.

## Linking to existing issues

`issue_link` = an existing issue id when the cluster continues it (same event, same actors, a new development), else `new`. A later stage of the same case (an investigation, then a hearing, a fine, a suit or a court step) links to the case's issue. Compare every cluster with the earlier-issue list, not only its `prior_issue_candidates` (those need a tracked name in the issue title and miss issues like trade negotiations). A dormant issue can be linked; it becomes active again. Never link against a user lock (`no_merge_with`, `exclude_pattern`, `pin_section`) — the gate fails it. A `rename` lock does not block linking; it only fixes the title the summary must use. (Locks: the contracts reference, § State, `fiw.state.issues/1`.)

### Duplicate clusters of one run

When several clusters of this run report the same new event (one company split into c0011, c0012, c0017), pick the best-covered one as the lead (`issue`, `issue_link: new`) and give each other one `issue` with `issue_link: "new:<lead cid>"` in the same section. They become one row whose 「N사 M건」 counts every joined cluster; the gate fails a join to a cid that is not such a lead, to a `market` cluster, or across sections. Duplicate `market` clusters need no join: each one `issue` with the same `market_topic` merges into that topic.

`other` vs noise for a duplicate: a cluster that reports the same event is joined, not `other` or noise — otherwise its outlets drop out of the count. Noise (duplicate) only for a cluster with nothing of the event a reader needs (a photo caption, a repost of one article); `other` is for a different, minor event of the section (listed under 「그 밖」).

## Primary-source items

- Every `korea_kr` press release kept as `include` needs a short Korean `category` label, reused consistently within a department (e.g. `기준·제도`, `회수`, `점검·단속`, `수출·국제`, `소비자 안내`, `물가`, `방역`, `담합`, `가맹`, `표시·광고`). Releases on medicine, medical devices, pensions or welfare unrelated to food are `other`.
- Notices (입법·행정예고) and bills: `include` when the instrument governs food, agriculture/livestock/fisheries products, distribution, labelling, franchising/fair trade affecting food companies, or food safety; otherwise `other`. An agriculture, fisheries or other ministry's instrument counts only when it changes something a food maker deals with — raw-material supply, prices, import quotas or tariffs, origin or quality labelling, standards, distribution; farm-only matters (farmer subsidies, rural housing, land, machinery, a single local designation) are `other`.
- `reason` is one short Korean sentence a reviewer can check.

## Writing the file

Write `<run folder>/work/relevance_brief.json` (`runs/food-industry-watch/<id>/` in the output folder) with a top-level `"schema": "fiw.relevance_brief/1"` (see the contracts reference) and compile it with `relevance --run <id> --compile <absolute path of the brief>` (a relative path is read from the current folder, not the run folder); it expands to `work/relevance.json` and runs the gate. Full entries are needed only for `issue` / `other` clusters and `include` items; the rest go in groups that share one reason:

- `clusters` — `{cid, verdict: issue|other, section, market_topic?, issue_link?, reason}` (`issue_link` defaults to `new` for an issue; `new:<lead cid>` joins a duplicate).
- `noise` — `[{reason, cids}]`, e.g. one group for duplicate reports of an issue already in a table, one for unrelated news.
- `noise_rest` — `{reason, last_cid, count}`: every reviewed cluster not listed above is noise. `last_cid` must be the last line of `clusters_brief.md` and `count` the number of clusters it covers, otherwise the command refuses (exit 2) — it proves the list was read to the end.
- `items` — `{source, id, category, reason}` for each item kept as `include` (`category` is required for `korea_kr`; other sources may give `null`); `items_other` — `[{source, reason, ids}]`; `items_rest` — `[{source, reason, count}]`: every item of that source in `items_view.md` not listed above is `other` (count checked the same way). `noise` and `items_other` are required keys and may be `[]`.

A cluster or item listed twice is kept twice, and the gate reports it. Writing `work/relevance.json` directly and running `relevance --check` remains possible.

Rows per table are capped (`max_rows` in the `format` part of `config/food-industry-watch.json`; overflow shows under 「그 밖」). Two `issue` clusters with `issue_link: new` become two rows even when they report the same event — join them (§ Duplicate clusters of one run).

## Self-check before the gate

- Every reviewed cluster id has exactly one entry.
- Every `issue` and `other` has a section; every `market` entry has a topic; `issue_link` is `new`, `new:<lead cid>` or an existing id for `issue`, `null` otherwise.
- You did not mark anything `noise` that a 대관 reader might need. Run `fiw.py relevance --run <id> --compile <absolute path of the brief>` (or `--check` for a hand-written `relevance.json`) and fix every finding.
