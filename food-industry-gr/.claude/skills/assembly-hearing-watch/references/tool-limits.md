# Assembly tool limits and quirks

Read this before calling any assembly tool for this skill.

## Contents
- Meeting list
- Schedule
- Speech clips
- Minutes (speech text)
- Live captions
- Audit reports
- Large results

## Meeting list (`assembly_committee_meetings`)
- One query covers at most 62 days. Run `plan` and query each segment it prints; save every result to its own file.
- Minutes appear about one week after a meeting. Audit (국정감사) meetings are listed first and carry no agenda and no "제N차" in the title.
- Returned fields used here: `confer_num`, `conf_id`, `title`, `committee`, `date`, `minutes_url`, `video_url`.

## Schedule (`assembly_schedule`)
- Rows have date, time, kind, committee and a content line. They carry no link.
- The link comes from the meeting list only when date, committee, session number and meeting number all match.
- Future dates are normal here.

## Speech clips (`assembly_speech_clips`)
- Returns counts and minutes per member, never the words spoken. Treat it as a pointer to video, not as a summary source.
- With the `member` option, clip items carry video links. Without it, the speech row has no link and a warning.
- Audit clip titles carry agency names, not committee names. Keep the name exactly as given.

## Minutes (`assembly_meeting_speeches`)
- Input is `confer_num` from the meeting list. Output is the transcript grouped by member; long ones are cut (`truncated=true`).
- Summaries are written from this text only. Never summarize from a title or an agenda line.
- If the minutes are not published yet, say so; do not fall back to captions unless captions exist.

## Live captions (`assembly_live_captions`)
- Captions cover only the time after a recording started. No recording means `recordings: []`, which is source status `empty`.
- Captions are speech-to-text and contain recognition errors. Rows built from them are marked in the report.

## Audit reports (`assembly_audit_reports`)
- Items carry a year only, no date. The row date becomes `YYYY-01-01` and the report says "year only".
- Items link to a PDF or HWP file. The row keeps the PDF link when present.

## Large results
- A result above about 96k characters is saved by the tool as a file under `tool-results/`. Pass that file path to `--meetings` and the other flags. Do not paste the content.
- "Not seen" means not seen on this page of results. It never means "does not exist".
