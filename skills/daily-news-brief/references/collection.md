# 수집과 회수

빅카인즈에서 7개사 기사를 받아 로컬 파일로 가져오는 절차. `SKILL.md` 1~2단계의 상세다.

## 목차
- [왜 브라우저인가](#왜-브라우저인가)
- [1. 수집기 주입과 실행](#1-수집기-주입과-실행)
- [2. 회수 — gzip + 분할](#2-회수--gzip--분할)
- [빅카인즈 API 메모](#빅카인즈-api-메모)
- [수집기가 하는 일](#수집기가-하는-일)

---

## 왜 브라우저인가

빅카인즈 WAF 가 비브라우저 클라이언트를 400 으로 거른다 — 정직한 UA 를 쓴 curl 도 거부된다.
**Claude 내장 브라우저는 UA 에 Claude 를 명시하고도 통과하므로 UA 위장 없이 접근하는 유일한 경로다.**

실제 Chrome 은 200 을 받지만 UA 가 Claude 를 밝히지 않으므로 쓰지 않는다.
헤드리스가 필요하면 빅카인즈 공식 OPEN API 를 신청하는 것이 정도다.

## 1. 수집기 주입과 실행

```
mcp__Claude_Browser__preview_start   url: https://www.bigkinds.or.kr/v2/news/index.do
```

`scripts/collect.js` **전체를 읽어** `javascript_tool` 로 평가한다. 그러면 `window.__collect` 가 생긴다.

```js
window.__collect.run("YYYY-MM-DD")
```

`window.__collect.status()` 를 폴링한다. `phase` 가 `done` 이 될 때까지 7개사 약 60~90초.

```js
JSON.stringify({p: window.__collect.status().phase, n: window.__collect.status().articleCount})
```

> `javascript_tool` 은 30초에 타임아웃된다. `setTimeout` 으로 25초 이상 기다리는 폴링은 실패한다.
> 짧은 상태 조회를 반복하는 편이 안전하다.

## 2. 회수 — gzip + 분할

내장 브라우저는 Blob 다운로드가 **작동하지 않는다**(`~/Downloads` 에 떨어지지 않음).
대신 **툴 출력이 토큰 상한을 넘으면 자동으로 디스크에 저장되는 것**을 이용한다.
컨텍스트를 거치지 않으므로 600KB 를 옮겨도 토큰을 쓰지 않는다.

### 압축

```js
(async () => {
  const raw = JSON.stringify(window.__collect.result());
  const buf = new Uint8Array(await new Response(
    new Blob([raw]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
  let bin = ''; const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
  window.__b64 = btoa(bin);
  return {raw: raw.length, b64: window.__b64.length, chunks: Math.ceil(window.__b64.length / 160000)};
})()
```

### 분할 내보내기

160,000자씩 순서대로. 각 호출이 상한을 넘겨 `tool-results/*.txt` 로 저장된다.

```js
window.__b64.slice(0, 160000)
window.__b64.slice(160000, 320000)
window.__b64.slice(320000, 480000)
```

**마지막 조각이 짧으면 상한을 못 넘겨 컨텍스트로 들어온다.** 패딩해서 강제로 넘긴다.

```js
"PAD".repeat(40000) + "|" + window.__b64.slice(480000)
```

### 재조립

저장 파일 형태: `[{"type":"text","text":"\"<base64>\"\n\n(captured at origin …)"}]`

```python
import json, base64, gzip, re
D = '<tool-results 경로>/'

def load(f):
    t = json.load(open(D + f, encoding='utf-8'))[0]['text']
    t = re.sub(r'\n\n\(captured at origin.*?\)\s*$', '', t, flags=re.S).strip()
    return json.loads(t)          # 바깥 따옴표를 벗긴다

b64 = ''.join(load(f) for f in CHUNK_FILES[:-1])
b64 += load(CHUNK_FILES[-1]).split('|', 1)[1]      # 패딩 제거
data = gzip.decompress(base64.b64decode(b64)).decode('utf-8')
```

길이를 압축 단계에서 받은 `b64` 값과 대조해 확인한 뒤 파일로 저장한다.

### 청크는 남는다 — 브리핑이 끝나면 지운다

`tool-results/*.txt` 는 세션 트랜스크립트 저장소에 그대로 쌓인다.
**1회 수집당 2~3MB**, 내용은 본문 전문의 gzip 이다. 파이프라인 밖이라 잊기 쉬운데
본문 영구 보관 금지가 여기에도 걸린다. 실측으로 두 세션에 3.2MB 가 방치돼 있었다.

```bash
python3 prune.py --sweep --chunks ~/.claude/projects/{프로젝트}/{세션id}/tool-results
```

파일명이 아니라 **내용**으로 판별한다(base64 한 덩어리인지). 같은 도구의 다른
출력이나 에이전트 트랜스크립트 덤프는 건드리지 않는다.

> 지우면 그 세션을 되살릴 때 해당 툴 결과를 못 읽는다. 끝난 수집 건에만 쓸 것.

## 빅카인즈 API 메모

브라우저 세션에서 확인한 내부 API. 로그인 불요.

| 엔드포인트 | 용도 |
|---|---|
| `POST /api/news/search.do` | 기사 검색 |
| `GET /news/detailView.do?docId=` | 본문 전문 + 개체명 + `TMS_SIMILARITY` |

### 실측으로 알아낸 것

- **`startNo` 는 오프셋이 아니라 페이지 번호다.** `startNo:101` 은 0건을 준다.
  오프셋처럼 쓰면 조용히 빈 결과를 받는다.
- **`resultNumber` 를 총 건수 이상으로 주면 전량이 1회에 온다.** 동아 277, 중앙 227, 매경 432 확인.
- **응답 순서가 `resultNumber` 값에 따라 달라진다.** `resultNumber:100` 일 때 동아 1페이지는
  23:07 부터 시작하는데, 사설은 23:25~23:30 으로 더 최신인데도 그 안에 없었다.
  → 서버 순서를 믿지 말고 `NEWS_ID` 의 타임스탬프로 직접 정렬할 것.
- **`editorialIs: true` 는 사설만 정확히 돌려준다.** 2026-08-21 전수 검사 20건, 오탐 0.
  칼럼은 안 잡힌다.
- **오피니언 카테고리는 없다.** 분류 필터에 사설 플래그 외에 의견 기사 구분이 없다.

## 수집기가 하는 일

`collect.js` 는 매체마다 세 갈래로 받는다.

| 갈래 | 상한 | 이유 |
|---|---|---|
| 사설 (`editorialIs:true`) | **없음** | 하루 3건 안팎. 논조 비교가 요건이라 누락이 곧 요건 미달 |
| 칼럼 (목록에서 골라냄) | **없음** | 전용 필터가 없다. 목록은 이미 전량을 받았으므로 추가 요청 없이 골라낸다 |
| 일반 | 30건 | 발행시각 내림차순 정렬 후 절삭. 리포트에 반드시 명시 |

사설·칼럼을 상한 밖에 두지 않으면 매체별로 통째 누락된다.
실측: 사설이 8→20건, 칼럼이 16→40건으로 늘었다.
