# Agencies and settings

Read when: the user names a ministry, asks why a ministry is missing, wants to change the food words, or wants the config file explained.

## Default agencies

| Name (used in the report) | Aliases accepted by `--agency` | korea.kr code | 국민참여입법센터 code |
|---|---|---|---|
| 식품의약품안전처 | 식약처 | A00027 | 1471000 |
| 농림축산식품부 | 농식품부 | A00008 | 1543000 |
| 보건복지부 | 복지부 | A00012 | 1352000 |
| 공정거래위원회 | 공정위 | C00001 | 1130000 |

Source: `assets/defaults/gov-notice-watch.json`. The codes were copied from the food-industry-watch config (`watch.json`).

Sources per agency:

- 식품의약품안전처 → korea.kr + 식약처 board + 국민참여입법센터
- 농림축산식품부 → korea.kr + 국민참여입법센터
- 보건복지부 → korea.kr + 국민참여입법센터
- 공정거래위원회 → korea.kr + 공정위 board + 국민참여입법센터

## Food words

Defined under `food_related` in the config:

- `include`: 식품, 식료품, 음료, 주류, 축산, 농산물, 수산물, 원산지, 급식, 건강기능식품, 가공, 식중독, 위생, 가맹, 유통, 담합
- `exclude`: 의약품, 의약외품, 의료기기, 의료제품, 화장품, 마약
- `no_match_other`: 보건복지부. An item of this agency with no food word is dropped by default.

Matching is by substring of the title, after the agency names are removed from the title. (The names 식품의약품안전처, 농림축산식품부 and 농림축산식품해양수산위원회 contain 식품, so they must not count as food words.)

## The config file

Optional. Path: `<output folder>/config/gov-notice-watch.json`. The output folder is the `--home` path (see SKILL.md).

```json
{
  "schema": "gr.gov-notice-watch/1",
  "agencies": [
    {"name": "식품의약품안전처", "aliases": ["식약처"], "korea_kr": "A00027", "lawmaking": "1471000"}
  ],
  "food_related": {
    "include": ["식품", "원산지"],
    "exclude": ["의약품"],
    "no_match_other": ["보건복지부"]
  }
}
```

Rules:

- `schema` must be exactly `gr.gov-notice-watch/1`, or the script stops with a usage error (exit 2).
- `agencies` and `food_related` each **replace** the defaults entirely when present. There is no merge. To add one word, copy the whole list.
- Other keys are ignored.

## Naming an agency the user asks for

1. If the name or an alias is in the table above, use it: `--agency 식약처` works.
2. If it is not in the table, a config entry is needed with a korea.kr code and a 국민참여입법센터 code. Those codes are not known for other ministries. **Do not guess codes.** Ask the user to provide them or leave the agency out and say so.
3. Only 식품의약품안전처 and 공정거래위원회 have their own board readers. Any other agency in the config is read through korea.kr and 국민참여입법센터 only, so no board line appears under 「조회 상태」 for it.
4. Unknown names given to `--agency` stop the run with exit 2 and the list of valid names.

## `--agency` and the food filter

- `--agency` is repeatable. Names or aliases both work.
- When `--agency` is given, the food filter is **off** for the run. The report shows everything from those ministries, which is what a named request usually wants.
- `--food-only` (without `--agency`) keeps only items with a food word, from all agencies.
- With neither flag the default applies (see the food words above).

## Changing the period and the topic

- `--from` / `--to` in `YYYY-MM-DD`. Without them: Monday of this week through today. An end date after today or a start after the end is a usage error.
- `--topic` adds a topic word to the report name and the 「주제」 line. It does not filter the rows.
