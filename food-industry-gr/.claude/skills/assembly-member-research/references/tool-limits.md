# Assembly tool limits and quirks

Read this before calling any assembly tool for this skill.

## Contents
- Search (`assembly_search`)
- Meeting list (`assembly_committee_meetings`)
- Minutes (`assembly_meeting_speeches`)
- Bills (`assembly_bills`)
- Member list (`assembly_members`)
- Large results and hand-copied results

## Search (`assembly_search`)
- Call with `remote: "only"` and a `remote_page` number (1, 2, ...). Each page holds 10 meetings. Save every page to its own file.
- The result is `remote.meetings[]`; each meeting has `conf_id`, `title`, `date` and `matches[]` (`speaker`, `speaker_id`, `snippet`).
- Without `remote: "only"` the tool may answer from its local index (a `local` block with `hits[]` and no `remote`). The scripts cannot read that; call again with `remote: "only"`.
- A name search with the speaker filter finds only meetings where the name text appears (the `speaker` filter is not applied remotely, so it also matches other people saying the name). A meeting where the member spoke without being named can be missing; the build warns about it for a named request without a topic.
- `remote_detail` is at most 10. The `matches[]` list of a meeting is capped (for example 5 listed of 7 matched), so a member whose only hit is not listed can be missed. State this when the count matters.
- A speaker is a National Assembly member only when `speaker_id` is all digits. A minister or witness has the title text as `speaker_id`; those are never rows.
- The `from` / `to` filter is applied inside the 10 meetings of one page, not across all pages. A narrow window can return nothing even when later pages have hits. The report says this ("한 쪽(10건) 안에서만").
- `has_more: true` means a next page exists. The report must say how many pages were seen and that the next page is "확인 필요". Fetch another page only when the user asks.
- Titles containing "제21대" and meetings outside the period are dropped by the script and counted in a warning.
- Search hit marks `【】` in snippets are search marks, not the speaker's words. Quotes are never taken from snippets.

## Meeting list (`assembly_committee_meetings`)
- One query covers at most 62 days (`from`, `to`). Run `plan` and query each segment it prints.
- Returned fields used here: `confer_num`, `conf_id`, `title`, `committee`, `date`, `minutes_url`.
- It is the only place that maps `conf_id` (from search) to `confer_num` (for minutes) and gives the minutes link. A `conf_id` is not a `confer_num`; passing one as the other fails.
- Minutes appear about one week after a meeting. A newer meeting has no minutes yet: say so, do not guess.

## Minutes (`assembly_meeting_speeches`)
- Input is `confer_num`. Output is `members[]` with `name`, `party`, `district`, `committee`, `turn_count`, `truncated`, `transcript`.
- Open at most 5 meetings per request (`--open-max`). Pick the ones `candidates` marks `<- open`. Pass `max_chars_per_member` of at least 500 (default 3000); a larger value keeps fewer turns cut.
- Passing `members` returns only those members and adds the ministers' replies. Use it when the user named a member.
- `transcript` turns are separated by blank lines and start with `[안건N]`. Lines starting with `↳` are other people's replies; they are not counted for the member.
- `…(N자 생략)` marks omitted text. A quote never spans it, and a count from a `truncated` or unopened meeting is shown as "최소 N".

## Bills (`assembly_bills`)
- Query once per member with `proposer` (name followed by 의원, for example `홍길동의원`) and leave `proposer_kind` at its default. The value `대표발의` is not valid.
- `total` counts all bills of that query without the period, while `returned` is what came back. When `returned` is below `total` the script warns "N건 중 M건".
- A bill is listed under a member only when `RST_PROPOSER` (the lead proposer) is that member.
- The bill link is `DETAIL_LINK`.

## Member list (`assembly_members`)
- Used only to fill party and committee. The tool also returns e-mail, phone, homepage and office; the script copies none of them into any file, and neither must you in an answer.
- Two members with the same name make the party "확인 필요".

## Large results and hand-copied results
- A result of about 96k characters or more is saved by the tool as a file under `tool-results/`. Pass that path to the flag. Do not retype it.
- If a result has to be copied by hand into a file, put the file under `runs/assembly-member-research/<run>/raw/`, run `build` with `--transcribed`, and re-query a sample to compare before trusting it.
- "Not seen" means not seen in the pages and meetings that were read. It never means the member did not speak.
