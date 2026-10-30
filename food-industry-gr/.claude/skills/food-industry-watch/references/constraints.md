# Constraints and limits

Boundaries this skill keeps, and limits the report must state about itself.

## Contents
- [What the skill never does](#what-the-skill-never-does)
- [robots.txt — per host](#robotstxt--per-host)
- [Copyright handling](#copyright-handling)
- [Sources and attribution](#sources-and-attribution)
- [Known limits](#known-limits)

---

## What the skill never does

- **Collect directly from news outlet sites.** Many outlets disallow AI crawlers in robots.txt. News comes only through BigKinds (Korea Press Foundation). The user obtained the foundation's permission for this use; record only that fact, do not re-open the question.
- **Change or blank the User-Agent.** Requests use the default Python UA. `lib/net.fetch` rejects a `User-Agent` header. Disguising the UA is the same as ignoring robots.txt (and it does not help: an outlet once answered `Mozilla/5.0` with 403 and an honest UA with 200).
- **Retry after a 4xx by changing the request shape.** Stop at the first 4xx and report it.
- **Use RSS feeds.**
- **Run on a schedule or collect in bulk.** Runs are started by the user. Each run reads only the span since the previous run; the first run covers one quarter in three monthly runs.
- **Open a host whose robots.txt disallows the path.** Links to such hosts are kept as links for people to click and are exempt from link checking.
- **Store full article text permanently.** See below.

## robots.txt — per host

Check robots.txt **for each host (sub-domain) separately** before the first request to it in a run. A rule on `www.example.go.kr` says nothing about `openapi.example.go.kr`: the food safety portal's site host allows the list pages while its API host is `Disallow: /`, and that difference was missed once. `lib/net.RobotsCache` caches one result per host per run; `assets/defaults/link_policy.json` lists hosts that are never requested, hosts kept as click-only links, and hosts whose robots.txt was read in the browser because script requests get an error.

When a milestone or a fix adds a new host, check and record its robots.txt first.

## Copyright handling

- Article bodies are intermediate data for writing and checking the summary only. The report never reprints them.
- The report carries the skill's own summary, the title, a link to the original, the date, and the outlet.
- Only text inside quotation marks is verbatim, and quoted text is at most 40 characters per issue in total.
- Never reproduce an article so fully that visiting the original becomes unnecessary.
- **Do not keep article bodies.** Once a report passes every gate and is published, `prune` removes bodies and excerpts; only titles, links and metadata stay. After pruning, a summary cannot be rewritten from the stored data — collect again instead (re-checking the gate without bodies is impossible, and an unchecked rewrite is not allowed).
- Primary sources with unclear reuse terms (the food and drug ministry's pages say "All rights reserved"; the health ministry's terms were not confirmed) are reported with title, date and link only.
- The first-run data shared with other users (runs, state and config) contains no body or excerpt fields; `seed export` refuses otherwise.

## Sources and attribution

The renderer adds the source credits automatically: BigKinds (Korea Press Foundation), the National Assembly open data portal (its terms require attribution), the National Participation Legislation Center, and the government briefing site (KOGL type 1).

## Known limits

State these in the report (the renderer does it) or keep them in mind:

- The food safety portal's administrative-action and nonconformity lists are the portal's public lists: no industry filter, and counts can differ from its Open API (which the skill may not call). The renderer adds a fixed footnote saying so.
- The evidence gate cannot catch a claim invented purely in prose (no numbers, quotes, names or judgment words), or an evidence fragment quoted out of context. These are covered by the human review sample.
- Ministry press releases alone miss events (a competition-authority raid in the reference period had no press release); the news axis covers them.
