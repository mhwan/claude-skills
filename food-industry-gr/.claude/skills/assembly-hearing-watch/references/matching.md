# Attaching a link to a schedule row

Read this when a schedule row has no link or a link looks doubtful.

Schedule rows have no URL. `build` attaches a meeting-list link only when all four match:

1. Same date.
2. Same committee name (compared after Unicode NFC normalization).
3. Same session number: the `제N회` in the schedule line equals the one in the meeting title.
4. Same meeting number: the `제N차` in both equals.

Rules:
- Any difference, or more than one candidate, gives `url: null` and a warning that contains the row id. The warning says which case it was.
- A meeting without `제N차` (audit meetings) never matches.
- A link is never borrowed from another row, another day or another committee.
- When there is no meeting list at all, every schedule row has `url: null` and a warning.
- Agency or committee names are copied as the tool gave them. Do not shorten or correct them.

Row ids:
- `schedule-<date>-<hhmm>-<hash>`, `speech-<conf_id>-<n>`, `clips-<date>-<hash>`, `audit-<doc_id>`.
- The `speech-<key>-<n>` stem is how `check` counts items per meeting.
