---
name: assembly-member-research
description: "Finds which 22nd National Assembly members spoke about a topic or what a named member said and proposed, and returns a per-member table with party, committee, related speech count, one short quote and the lead-sponsored bills, each linked to the minutes. Use for \"설탕세 관련해서 누가 발언했어\", \"OOO 의원 최근 발언 정리\", \"이 법안 쪽 의원들 누구야\", \"농해수위 의원 중 식품 이야기한 사람\". Not for bill progress tracking (use assembly-bill-tracker), not for committee schedules or meeting summaries (use assembly-hearing-watch), and not for press coverage."
---

# assembly-member-research

Finds who spoke about a topic (or what one member said and proposed) in a period, and writes a per-member table where every speech links to the minutes. The user is a non-developer: answer in Korean, plain words, conclusion first.

## Rules that always apply
- Every row has a link to the minutes. If none can be found the row has `url: null` and a warning naming the row id.
- Anything not confirmed is written as "확인 필요". Never invent names, parties, committees, dates, counts or bill titles.
- A quote is at most 40 characters, copied from the minutes, with its link. Do not paraphrase inside quotation marks.
- Counts are facts about the meetings that were read. Never rank, grade or describe a member's leaning. Details: `references/search-interpretation.md`.
- Use only name, party, committee and district of a member. Never copy an e-mail, phone, homepage or office into any file or answer.
- Do not change the table columns. They are fixed in `assets/report-template.md` and checked by `check`.
- Never open press sites or any website; use the assembly tools only. Never print or ask for API keys.

## Steps
1. Fix the period and the topic words, or the member names, from the request (ask once if unclear). Run:
   ```
   PY SKILL_DIR/scripts/member.py plan --from YYYY-MM-DD --to YYYY-MM-DD --topic "TEXT"
   ```
   `PY` is `python3` on macOS and `py -3 -X utf8` on Windows (not checked on a real Windows app yet). `SKILL_DIR` is this skill's folder as an absolute path. It prints the meeting-list segments (62 days at most) and the topic words.
2. Search. Call `assembly_search` with `remote: "only"` for the topic (page 1, then more only if the user asks), and `assembly_committee_meetings` once per segment. For a name-only request search the member's name with the speaker filter and no topic. Save each result to its own file. Limits and quirks: `references/tool-limits.md`. A period over 62 days takes several segments; read page 1 of each, say what was not read, and offer to continue.
3. Choose meetings to open:
   ```
   PY SKILL_DIR/scripts/member.py candidates --from D --to D --topic "TEXT" --search F [--search F]
   ```
   It prints each meeting's `conf_id`. Look up its `confer_num` in the saved meeting-list file (search that file for the `conf_id`). Open the meetings marked `<- open` (5 at most) with `assembly_meeting_speeches` and save each. If the user named members, pass them as `members`.
4. If the user wants bills, call `assembly_bills` once per member shown; for party and committee call `assembly_members`. Save each result.
5. Build:
   ```
   PY SKILL_DIR/scripts/member.py build --from D --to D --topic "TEXT" --search F --meetings F --speeches F --bills F --members F [--member NAME] [--all]
   ```
   Repeat a flag for each file. The command prints `status`, `rows`, `report`, `data`, `run` and the warnings. To use another output root put `env GR_HOME=ABSOLUTE_PATH` before `PY`. If you had to type a tool result into a file by hand, save it under `<run>/raw/` and add `--transcribed` (see `references/tool-limits.md`).
6. Verify:
   ```
   PY SKILL_DIR/scripts/member.py check --data DATA_FILE --report REPORT_FILE
   ```
   It must print `problems: 0`.
7. Tell the user: the conclusion in one or two lines, the report path, how many search pages and meetings were read, and every warning in plain words. State what was not read with the words "확인 필요" in the answer itself.

## Reading the output
- Exit code 0 clean, 3 means read the warnings, 1 means nothing could be read, 2 means wrong flags.
- `status: empty` means the search worked and found no member speech. `unread` or `failed` means the tool result could not be used; say so, do not report "no speeches".
- "최소 N" in the table means the count is a lower bound (cut or unopened minutes).
- A topic-only request shows 10 members by speech count with the note that it is not a ranking; `--all` shows everyone.
- A member the search listed but whose opened minutes have no speech with the topic word gets no row; one warning names them ("확인 필요", not "did not speak"). A named request gives rows only for the named members.
- Without a topic the count is the member's speech turns in the opened meetings, whatever the subject; the report says so.

## Files
- Report: `reports/assembly-member-research/<period>[_topic].md`; data: `data/assembly-member-research/<same>.json`; run record: `runs/assembly-member-research/<date>-<n>/`. An earlier report with the same name moves to `history/assembly-member-research/`.
- The output root follows `GR_HOME`, else the work folder `output/`.

## References
- `references/tool-limits.md`: read before calling any assembly tool.
- `references/search-interpretation.md`: read before writing the answer or when a count, quote or ordering is questioned.
