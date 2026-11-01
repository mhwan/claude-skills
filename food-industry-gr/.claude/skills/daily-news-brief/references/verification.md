# Verification gates

The collection gates (`normalize.py`) and the evidence gate and format lint (`verify.py`). Detail for step 5 of `SKILL.md`.

## Contents
- [The 7 collection gates](#the-7-collection-gates)
- [The evidence gate](#the-evidence-gate)
- [Format lint](#format-lint)
- [The Naver fallback (네이버 대체판)](#the-naver-fallback-네이버-대체판)
- [Regression tests](#regression-tests)

---

## The 7 collection gates

`normalize.py` runs them right after collection. Exit code 1 means FAIL (the collection cannot be trusted).
The gate names are what the program prints, so they stay in Korean.

| # | Gate | FAIL condition |
|---|---|---|
| 1 | 커버리지 | successful outlets / 7 < 0.8 |
| 2 | 매체별 최소 건수 | fewer than 5 on a weekday, **3 on a weekend** (WARN) |
| 3 | 신선도 | share of articles from the target day < 0.9 |
| 4 | 스테일 | the headline set is fully identical to **2 or more days** of the previous 7 |
| 5 | 링크 | an invalid link in the sample, one per outlet (WARN) |
| 6 | 본문 확보 | share of **0-character** bodies > 0.1 |
| 7 | 절삭 고지 | never FAIL: when a cap was applied it must be stated (WARN) |

### G4 never switches itself off

With no history to compare, the result is **WARN, not PASS**.
The earlier version used `days=3`, so after a holiday gap of 3 days or more it quietly PASSED with "no history to compare (first run)".
**Right after 설 or 추석 is when a stuck cache is most likely, and that was exactly when it switched off.**

Now the window is 7 days, the number of days compared is always shown in the text,
and "file missing" is told apart from "read failed" (so lost history is not mistaken for a first run).

A duplicate share of 90% or more is also a WARN: not fully identical is still suspicious.

### G6 tells two cases apart

- **0 characters** = detailView failed -> the FAIL criterion
- **more than 0 and fewer than 200 characters** = an article that is short by nature (obituary, photo caption) -> only the count and outlet are shown

Mixing them makes normal data FAIL. Measured: of 24 articles under 200 characters, 20 were
photo captions of a 경향신문 musical competition.

**The remaining blind spot**: error text that is not 0 characters (a short access-restricted notice, say) hides in the short bucket.
It has happened 0 times in the current 496 articles, but it is not closed.

### G5 does not connect to newspaper sites

The earlier version sent a HEAD request to a sample article address to see whether it was alive (first faking `Mozilla/5.0`, later with an honest UA).
This work folder's rule is not to open press sites directly, so the connection was removed.
Now it looks only at the shape of the address: `http`/`https`, a host present and no whitespace gives `ok`, anything else `dead`.
**It does not check that the address really opens.** So this gate goes up to WARN only, and the detail line prints "사이트 접속 없음" (no site connection).

The sample is **one per outlet**, not 3 at random. A whole outlet's links can break
without a random 3 catching it, and that is exactly the accident this gate must catch.

## The evidence gate

`verify.py {date}`. **Half of this skill's reason to exist is here.**

The 10 lint items of the rendering rules (§8) are all format checks.
There is not one fact check. Without this gate,
**a summary that invents things absent from the source passes everything.**

### What it checks

| Type | What it catches |
|---|---|
| Numbers | invented amounts, ratios, counts |
| Dates | invented schedules |
| Quotes | invented statements |
| Attribution | text that names an outlet not in that issue |
| Contradiction | text saying an outlet in the table 「안 썼다」 (did not write it) · 「N 매체 모두」 (all N outlets) not matching the row count |
| Omission | an empty one-line summary · an editorial with no issue point · no axis or subject · no lead `ref` |
| Structure | the `url` of a tone row is not in the opinion-piece list |

### Scope of the check

It covers not only the issue summaries but also the **four tone blocks, the axis, the subject and the lead**.
The first version looked only at `rows[].argument`, so a sentence like 「9999조원 규모라고 보도했다」 in the four blocks
passed (caught in the architect check).

What is compared against is **limited to the evidence of that item**:
- issue summary -> the articles of that issue
- tone argument -> **the one piece that the `url` of that row points to**
- four blocks -> the union of the pieces in the rows of that issue point
- lead -> the issue that its `ref` points to

If the whole day were the comparison pool, a number that happens to appear in an article of another category would pass.
The aim of the gate is not "it is somewhere" but "it is in the evidence for this matter".

### Implementation notes

- **Numbers are compared as token sets.** Searching by substring lets 「150조」 pass by being found inside
  「1500억」 in the body.
- **Counting expressions are excluded.** 「6사 9건」 and 「13건」 are values the summary writer counted,
  so not being in the source is normal. They must be caught even with a particle attached (「13건으로」).
- **A particle after an outlet name is allowed, compounds are not.** Catch 「매경이」 but not 「중앙은행」.
- **A claim of absence needs a subject particle.** In 「조선 사설에 대한 반론이 없다」,
  조선 is the target of the rebuttal, not the subject of the absence.

## Format lint

`verify.py {date} --lint` implements the 10 lint items of the rendering rules (§8).
See the rendering rules for what each item checks.

## The Naver fallback (네이버 대체판)

When BigKinds is down, `collect_naver.py` collects from the Naver news search API instead.
The switch rules come from `korean-news-search` unchanged:
- a block sign (4xx, login, block, captcha) means stop;
- a timeout, maintenance, 5xx or connection failure means switch;
- anything else means stop (확인 필요).

A stop writes nothing: no run folder, no report, only the reason.
Naver gives no body, and the descriptions are never read or stored, so every article has a 0-character body.

**Only the body gate is lowered. The other bars are the same numbers as the normal path.**

| # | Gate | In fallback mode |
|---|---|---|
| 1 | 커버리지 | unchanged (0.8). An outlet with 0 articles counts as failed and shows 「못 찾음」. A FAIL here is shown as a fallback FAIL (「대체판 수집 실패」) and no report is written |
| 2 | 매체별 최소 건수 | unchanged |
| 3 | 신선도 | unchanged |
| 4 | 스테일 | unchanged |
| 5 | 링크 | unchanged (address shape only) |
| 6 | 본문 확보 | WARN 「본문 없음(대체 모드)」. The detail line and the report both say the 90% bar was lowered for this mode only |
| 7 | 절삭 고지 | WARN when a query was truncated or unreached, with the query names |

The collection report adds a per-query table (complete, truncated or unreached) and a per-outlet table.
Past dates reach only the newest ~1,100 articles per query, so 「unreached」 means "could not read", not "none".

### The evidence gate in fallback mode

There is no summary to check, so the gate turns the other way: **any summary sentence is a FAIL.**
`verify.py` walks the whole `summary.json`, if one exists, and flags every non-empty string (「대체 모드 요약 금지」).
`render.py build` runs the same check and refuses to write the report while a FAIL gate or a summary string remains.
The fix is to delete the summary file, not to shorten it.

### The format lint in fallback mode

When the first line carries 「네이버 대체판 — 본문 미확인」, the lint requires 「## 검색별 결과」, 「## 사설」, 「## 기사 목록」 and 「출처: 네이버 뉴스 검색 API」.
It forbids 「자료원: 빅카인즈」 and any `bigkinds.or.kr` address, so a fallback report never claims BigKinds as its source.
The normal report's checks run unchanged when that mark is absent.

## Regression tests

After changing the pipeline run **all** of them. The tests live in the public repository under `tests/daily-news-brief/`
and run inside a temporary `GR_HOME` only, so they never touch the real output folder.

```bash
PY "<repo>/tests/daily-news-brief/run_all.py" --skill-dir "<skill>"
```

The evidence gate test must judge all 10 planted errors FAIL and all 5 normal cases PASS.
The fallback test (`test_naver_fallback.py`) replays saved Naver pages, so it never calls the real API.

### Test isolation: never touch real data, never depend on it

The first version overwrote `{date}.summary.json` and deleted it at the end, **which lost 40 summaries.**
Now it is isolated with a dedicated date, `TEST_DATE='1999-01-01'`.

The next version borrowed `work/2026-08-21.todo.json` as a fixture.
**The first time `prune.py` ran, the test could never run again**, because that was a file prune deletes.
It also clashed with the rule of not keeping full bodies permanently.

Now there is `tests/fixtures/evidence-gate.todo.json`, a synthetic fixture made by hand that holds only the tokens the cases need.
The `_invariants` at the top of the file state the contract with the cases
(매경 must not be in the issue; 110 and 30 must be in the body, 150 and 9월 15일 must not).

The network stub is also checked with an `assert` to be catching the real function name (`link_status`).
Before, it stubbed an old name, `link_ok`, so a test believed to be isolated
was really hitting the network.
