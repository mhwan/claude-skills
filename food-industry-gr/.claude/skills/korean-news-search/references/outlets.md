# Outlets

BigKinds identifies an outlet by a provider code. Codes seen in earlier work of this team (the code is sent to the server, so the search is limited to that outlet):

| Outlet | Provider code |
|---|---|
| 조선일보 | 01100801 |
| 중앙일보 | 01100901 |
| 동아일보 | 01100401 |
| 한겨레 | 01101001 |
| 경향신문 | 01100101 |
| 매일경제 | 02100101 |
| 한국경제 | 02100601 |

These are the same 7 papers as in `daily-news-brief`. The table lives in code as `KNOWN_PROVIDER_CODES` in `scripts/run_search.py`.

Other codes seen in article ids on the real site (2026-10-29), read from the id prefix and the returned name; not used as filters and not in the code table: 국민일보 01100201, 세계일보 01100701, 서울신문 01100611, 문화일보 01100501, SBS 08100301.

## Rules
- Default is all outlets: no outlet in the request means no filter.
- If every outlet the user names is in the table, the codes go to the server.
- If any named outlet is not in the table, the search runs without a code filter and the rows are kept only when the outlet name matches one of the names exactly (after Unicode normalization). The code of an unlisted outlet is 「미확인」: do not guess one.
- The outlet name written in a row is the name BigKinds returns, which may differ from what the user typed. Seen on the real site (2026-10-29): the returned names of the 7 known outlets equal the table's names exactly. If a name filter returns nothing while `total` is large, say so and ask the user to confirm the spelling; do not report it as "no articles".
- Wording: the user says 「조선일보와 한겨레」; pass `--outlets "조선일보,한겨레"`.
