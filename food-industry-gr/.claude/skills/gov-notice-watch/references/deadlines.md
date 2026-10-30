# Deadlines and states

Read when: explaining the 「의견 마감일」 column, a 「접수 종료」 or 「확인 필요」 mark, why a notice is or is not listed, or how the kind (입법예고 / 행정예고) is decided.

## The comment-period states

The mark is set against the report's **기준일** (`--today`, default: the day of the run). It is not the day the report is read.

| Cell in the report | Rule | Example |
|---|---|---|
| `YYYY-MM-DD ⏰` | 접수 중: the deadline is on or after 기준일 | `2026-11-14 ⏰` |
| `YYYY-MM-DD (접수 종료)` | 종료: the deadline is before 기준일 (the notice overlapped the period, but the comment window has already closed) | `2026-09-30 (접수 종료)` |
| `확인 필요` | No deadline could be read | `확인 필요` |

A report is a snapshot. The same report run a day later can change ⏰ to 접수 종료.

## Which notices are listed

- A notice is listed when its comment period **overlaps** the requested period. Both ends count (inclusive). A notice with no end date counts as open-ended.
- A notice whose period cannot be read at all is **dropped**, and the report says so under 「확인할 것」 with its key (for example `lm-…: 의견 기간(…)을 읽지 못해 뺌 — 확인 필요`). Look at the source page yourself.
- A notice with a start date but no deadline is **kept**, marked 「확인 필요」, with a warning.

## Where each deadline comes from

| Source | Deadline comes from |
|---|---|
| 식약처 board | The first deadline phrase in the detail page that is on or after the posting date (for example `2026년 11월 14일(토)까지`). Phrases are only kept as a short evidence fragment. |
| 공정위 board | The period cell on the list row. Exactly two valid dates in order; otherwise the row is dropped. |
| 국민참여입법센터 | The period text (`YYYY. M. D. ~ YYYY. M. D.`); its end date is the deadline. |
| Same notice on two sources | Matched by department + normalised title. The board deadline wins. If the board has none, the centre's period fills it. If the two periods differ, the board stays and a warning is written. |

## Kind (구분)

| Kind | How it is decided |
|---|---|
| 보도자료 | Every korea.kr and ministry press release (press table, not the notice table) |
| 입법예고 | Title words (`notices.notice_kind`), else 식약처 detail page citing 행정절차법 제41조, else the default for 국민참여입법센터 |
| 행정예고 | Title words, else 식약처 detail page citing 행정절차법 제46조, else the default for 공정위 |
| 재행정예고 | Title words only |
| 확인 필요 | None of the above |

## Sorting

- Press releases: by the agency order in the config, then newest first.
- Notices: earliest deadline first; notices with no deadline at the bottom.

There is no separate 「마감 임박」 label. Look at the date in the 의견 마감일 column. The script does not set a day threshold.

## Food filter (applies to both tables)

Done before listing, by title and agency:

- A title with an include word (식품, 축산, 원산지, 급식, …) → kept.
- A title with an exclude word (의약품, 화장품, …) and no include word → 「식품과 무관한 단어가 있음」, dropped.
- No food word at all → kept only if the agency is not a `no_match_other` agency. For 보건복지부 it is dropped and counted under 「확인할 것」.
- `--agency` names skip the filter. `--food-only` keeps only items with a food word.

## Warnings to expect

- `<key>: 의견 마감일을 찾지 못함 — 확인 필요 (reason)`: a notice with no deadline.
- `식약처 <seq>: 본문을 읽지 못해 의견 마감일은 확인 필요 (…)`: a detail page failed.
- `같은 제목의 입법센터 자료 … 의견 기간(…)이 다른 기간이라 보드 기준으로 둠`: two sources disagree.
