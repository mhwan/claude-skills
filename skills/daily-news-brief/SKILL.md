---
name: daily-news-brief
description: 국내 일간지 7개사(조선·중앙·동아·한겨레·경향·매경·한경) 조간 브리핑을 생성한다. 빅카인즈에서 수집해 카테고리별 이슈 표와 사설 논조 비교를 만들고, 요약의 사실성을 원문 대조로 검증한다. 다음 경우에 사용: (1) "조간 브리핑", "오늘 뉴스 정리", "신문 요약", "뉴스 브리핑", "논조 비교", "사설 비교" 요청, (2) 특정 날짜의 일간지 보도를 정리해 달라는 요청, (3) 매체별 논조 차이를 알고 싶다는 요청, (4) 이 스킬이 만든 브리핑을 다시 만들거나 고쳐 달라는 요청.
---

# 조간 브리핑

빅카인즈에서 7개사 기사를 수집해 **카테고리별 이슈 표 + 사설 논조 비교** 브리핑을 만든다.

산출물: `~/.claude/news-briefs/{date}.md`

## 두 가지 원칙

**조용한 실패를 막는다.** 7개사 중 2개사만 수집돼도 브리핑은 그럴듯하게 나온다.
수집 단계에 게이트 7종을 두고, 하나라도 걸리면 "정상"이라 말하지 않는다.

**지어낸 요약을 막는다.** 표가 예쁘고 링크가 200이면 형식 검사는 전부 통과한다.
그래서 요약을 별도 파일로 떼어내 숫자·날짜·인용·매체 귀속을 원문에 대조한다.
이 게이트를 통과하지 않은 브리핑은 내보내지 않는다.

## 파이프라인

```
collect.js    브라우저 수집 → 회수
     ↓
normalize.py  정규화 + 수집 게이트 7종 → raw/{date}.json
     ↓
cluster.py    클러스터·카테고리·의견분류·일정추출 → clusters/{date}.json
     ↓
render.py     prepare → work/{date}.todo.json
     ↓
   (LLM 이 요약 작성)  → work/{date}.summary.json
     ↓
verify.py     🔴 근거 게이트 — 원문 대조
     ↓
render.py     build → news-briefs/{date}.md
     ↓
verify.py     --lint 형식 검사
     ↓
prune.py      중간 데이터 정리 (본문 전문 폐기)
```

요약과 렌더를 파일로 나눈 이유: **요약의 사실성을 기계로 검사하려면 검사할 파일이 있어야 한다.**
한 덩어리면 요약만 다시 할 수도 없다.

---

## 실행 절차

### 1. 수집

브라우저를 열고 `scripts/collect.js` 전체를 주입한 뒤 실행한다.

```
mcp__Claude_Browser__preview_start   url: https://www.bigkinds.or.kr/v2/news/index.do
```
```js
window.__collect.run("YYYY-MM-DD")
```

7개사 약 60~90초. `window.__collect.status().phase` 가 `done` 이 될 때까지 폴링한다.

### 2. 회수

내장 브라우저는 다운로드가 막혀 있다. gzip 압축 후 분할해 내보내면
툴 출력 상한을 넘겨 자동으로 디스크에 저장된다 — 컨텍스트를 거치지 않는다.

**절차와 재조립 코드는 `references/collection.md` 를 읽는다.**
빅카인즈 API 의 함정(`startNo` 는 페이지 번호, 응답 순서가 `resultNumber` 에 따라 달라짐)도 거기 있다.

### 3. 파이프라인 실행

```bash
cd ~/.claude/skills/daily-news-brief/scripts
python3 normalize.py <회수한 파일>
python3 cluster.py   YYYY-MM-DD
python3 render.py prepare YYYY-MM-DD
```

`normalize.py` 가 FAIL 을 내면 멈추고 원인을 확인한다. 수집이 반쪽이면 브리핑을 만들지 않는다.

### 4. 요약 작성 — LLM 의 몫

`work/{date}.todo.json` 을 읽고 `work/{date}.summary.json` 을 만든다.
작업지시서에 각 이슈의 기사 본문과 의견 기사 전문이 들어 있다. 근거는 그 안에서만 찾는다.

**스키마와 작성 규칙은 `references/summary-schema.md` 를 읽는다.**
분량 상한, `stance` 값 목록, 리드 `ref` 의무, 논조 판단 지침, 흔한 실패가 거기 있다.

### 5. 검증과 생성

```bash
python3 render.py build YYYY-MM-DD
python3 verify.py YYYY-MM-DD --lint
```

**둘 다 PASS 여야 한다.** FAIL 이면 지적된 항목을 고치고 4~5 를 반복한다.
게이트를 우회하거나 기준을 낮추지 않는다.

게이트가 무엇을 어떻게 검사하는지는 `references/verification.md` 에 있다.

마크다운은 `render.py` 가 전부 만든다. **직접 손대지 않는다** —
고칠 것이 있으면 `summary.json` 을 고치고 `build` 를 다시 돌린다.
이전 산출물이 필요하면 `~/.claude/news-briefs/` 를 본다.

### 6. 정리

**둘 다 PASS 한 뒤에만** 실행한다. 정리하면 재렌더가 불가능하다.

```bash
python3 prune.py YYYY-MM-DD
```

`raw/` 는 제목·링크만 남기고(직전 7일 스테일 게이트가 읽는다),
`clusters/` 와 `work/*.todo.json` 은 지운다. 본문 전문은 이 시점에 폐기된다 —
용량이 아니라 **원칙 준수**다. 실측 6.4MB → 476KB.

`--dry-run` 으로 먼저 확인할 수 있다. `--sweep` 은 브리핑이 있는 모든 날짜를
정리하고 보관 창(기본 14일)을 벗어난 `raw/` 를 지운다.

**2단계 회수 청크도 지운다.** 파이프라인 밖(세션 트랜스크립트 저장소)에 수집 1회당
2~3MB 가 쌓인다. 경로와 주의는 `references/collection.md`.

---

## 참조 문서

| 파일 | 언제 읽나 |
|---|---|
| `references/collection.md` | 수집·회수를 실행할 때 (2단계 필수) |
| `references/summary-schema.md` | 요약을 작성할 때 (4단계 필수) |
| `references/verification.md` | 게이트가 FAIL 을 냈을 때, 게이트를 고칠 때 |
| `references/constraints.md` | 소스를 늘리거나 자동화를 검토할 때, 한계를 브리핑에 쓸 때 |
| `references/korean-text.md` | **파이프라인의 정규식·패턴을 고치기 전에 반드시** |
| `references/rendering-rules.md` | 렌더 규칙 명세 — 표 구조·점수·링크·분량 상한 |

## 반드시 지킬 것

- 차단 매체(중앙·한겨레·경향·매경)를 **직접 수집하지 않는다**
- **UA 를 위장하지 않는다**
- 본문 전문을 산출물에 **전재하지 않는다**
- 본문 전문을 **영구 보관하지 않는다** — 브리핑이 나오면 `prune.py` 로 폐기
- 스케줄 자동 대량 수집을 **붙이지 않는다**

근거는 `references/constraints.md`. 소스를 늘리거나 자동화를 검토하기 전에 읽는다.

## 파이프라인을 고칠 때

한국어 정규식에서 같은 뿌리의 함정에 세 번 빠졌다.
**패턴을 건드리기 전에 `references/korean-text.md` 를 읽는다.**

고친 뒤에는 회귀 테스트 두 개를 모두 돌린다.

```bash
python3 tests/test_collection_gates.py
python3 tests/test_evidence_gate.py
```
