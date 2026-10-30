---
name: assembly-hearing-watch
description: "Builds a linked report of National Assembly committee schedules, meeting speech summaries and audit (국정감사) correction/handling requests for a period. Use for \"이번 주 상임위 일정\", \"농해수위 회의 발언 정리\", \"국정감사 시정·처리요구 찾아줘\", \"국감 대응 자료\". Not for bill status (use assembly-bill-tracker) and not for press coverage."
---

# assembly-hearing-watch

Collects committee schedules, minutes-based speech summaries and audit correction/handling requests for one period, and writes a report where every row has a source link. The user is a non-developer: answer in Korean, plain words, conclusion first.

## Rules that always apply
- Every row has a link. If none can be found, the row has `url: null` and a warning naming the row id.
- Anything not confirmed is written as "확인 필요". Never invent numbers, dates, names or bill titles.
- Caption-based text always carries `「자막 기준·확인 필요」`. This holds in the report and in your own answer: put the mark on every caption-based line, not only on a heading above it.
- At most 5 items per meeting, 1-2 sentences each, paraphrased. A quote is at most 40 characters and has its source link.
- Do not interpret bill stage names. "Not seen" means not seen on this page, check the next page.
- Never start a live-caption recording unless the user says "녹화해줘". See `references/captions.md`.
- Never open press sites. Never print or ask for API keys.

## Steps
1. Fix the period from the request (ask once if it is unclear). Run:
   ```
   PY SKILL_DIR/scripts/hearing.py plan --from YYYY-MM-DD --to YYYY-MM-DD
   ```
   `PY` is `python3` on macOS and `py -3 -X utf8` on Windows. `SKILL_DIR` is this skill's folder as an absolute path. The command prints segments of at most 62 days.
2. Fetch with the assembly tools and save each result to a file (tool results above ~96k characters are already saved under `tool-results/`; use that path). Limits and quirks: `references/tool-limits.md`.
   - `assembly_committee_meetings`: once per segment.
   - `assembly_schedule`: whole period.
   - `assembly_audit_reports`: when the user asks about "국정감사".
   - `assembly_speech_clips`: only for video pointers.
   - `assembly_live_captions`: only if a recording exists.
3. For meetings with published minutes, read them with `assembly_meeting_speeches` and write the summaries file. Format and limits: `references/summaries-file.md`.
4. Build:
   ```
   PY SKILL_DIR/scripts/hearing.py build --from D --to D --meetings F --schedule F --audit F --summaries F [--clips F] [--captions F] [--topic TEXT]
   ```
   Repeat `--meetings` for each segment. The command prints `status`, `rows`, `report`, `data`, `run` and the warnings.
5. Verify:
   ```
   PY SKILL_DIR/scripts/hearing.py check --data DATA_FILE --report REPORT_FILE
   ```
   It must print `problems: 0`.
6. Tell the user: the conclusion in one or two lines, the report path, and every warning in plain words (links not attached, partial period, items cut). Write each thing you could not confirm with the words "확인 필요" in the answer itself.

## Reading the output
- Exit code 0 clean, 3 means read the warnings, 1 means nothing could be read, 2 means wrong flags.
- A warning with "일부만" means some segments of the period were not queried. Query the rest or tell the user the report is partial.
- A schedule row without a link is normal when the match rules in `references/matching.md` do not pass. Do not attach a guessed link.

## Files
- Report: `reports/assembly-hearing-watch/<period>[_topic].md`; data: `data/assembly-hearing-watch/<same>.json`; run record: `runs/assembly-hearing-watch/<date>-<n>/`. An earlier report with the same name moves to `history/assembly-hearing-watch/`.
- The output root follows `GR_HOME`, else the work folder `output/`.

## References
- `references/tool-limits.md`: read before calling any assembly tool.
- `references/matching.md`: read when a schedule row has no link.
- `references/captions.md`: read when live broadcast, captions or recording come up.
- `references/summaries-file.md`: read before writing the summaries file.
