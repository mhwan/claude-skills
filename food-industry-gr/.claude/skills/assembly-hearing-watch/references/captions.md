# Live captions and recording

Read this when the user mentions live broadcast, captions, or recording.

## Marking
- Every caption-based row carries `「자막 기준·확인 필요」` at the start of its title. `build` adds it; `check` fails without it.
- The report repeats the notice once above the speech list.
- Captions have recognition errors (names, numbers, bill titles). Never state a figure or a name from captions as fact.

## Recording
- Start a recording (`assembly_live_captions_record`) only when the user says "녹화해줘" or equivalent. Never start one on your own.
- Before starting, tell the user in one line what will be recorded and where it will be saved, then wait for the go-ahead.
- Captions exist only for the time after the recording started. Say so when the user asks about earlier parts of a meeting.
- Tests and examples use synthetic caption files only. Never start a real recording to test this skill.

## Summary file for captions
An entry in the summaries file for captions needs `basis: "caption"`, `committee`, `date`, `time` and `url` (the broadcast link). Without `url` the row has no link and a warning.
