# 식품업계 대관(GR) 작업 폴더

## 사용자
- 비개발자다. 한국어로, 전문용어 없이, 결론부터 짧게 답한다.

## 설치·환경 문제
설치·처음 설정·업데이트 요청, MCP 도구가 안 보이거나 failed, 키 오류, 프로그램이 없다는 오류가 나면 [INSTALL.md](INSTALL.md) 를 읽고 그대로 따른다.

## 도구
- MCP: `law` 법령·판례, `dart` 기업 공시, `assembly` 국회(법안·회의록·표결·일정), `kordoc` HWP·PDF 등 문서 읽기·작성. 문서는 사용자에게 이 폴더에 넣어 달라고 한다.
- 스킬(`.claude/skills/`) 8개: `food-industry-watch`(대관 동향 종합), `daily-news-brief`(조간 브리핑), `korean-news-search`(뉴스 검색), `gov-notice-watch`(부처 보도자료·입법예고), `assembly-bill-tracker`(법안 추적), `assembly-hearing-watch`(국회 일정·회의·국감), `assembly-member-research`(의원 발언·발의), `food-safety-records`(회수·행정처분).
- 신문 기사는 `daily-news-brief`·`korean-news-search`·`food-industry-watch` 스킬로만 모은다.

## 규칙
- API 키 값을 묻거나 출력하거나 파일·명령에 적지 않는다. 들어 있는지만 INSTALL.md 3단계 스크립트로 확인한다. `.mcp.json` 에 키나 `env` 를 적지 않는다.
- 언론사 사이트를 직접 열지 않는다. 그 밖의 사이트는 호스트별 robots.txt 를 먼저 확인한다. User-Agent 를 바꾸지 않고, 4xx 가 나면 우회하지 말고 멈춘다. RSS 는 쓰지 않는다.
- 기사 본문을 옮기거나 저장하지 않는다. 인용은 기사 1건당 40자 이내로 하고 원문 링크를 붙인다.
- 출처 없는 숫자·날짜·인명·법안명을 쓰지 않는다. 조문·공시 링크·의안번호를 붙이고, 확인 못 한 것은 "확인 필요"로 표시한다. 법령·판례를 인용한 결과물은 `law` 의 `legal_analysis`(`mode=verify_citations`)로 검증한다.
- 스킬 명령의 `PY` 는 Windows 에서 `py -3 -X utf8`(빼면 한국어 Windows 에서 출력 오류), macOS 에서 `python3`. 스킬 폴더는 절대경로로 쓰고 `.claude/skills/` 파일은 고치지 않는다. 스킬이 실패하면 우회하지 말고 오류를 그대로 보여준다.
- 명령은 도구 호출 하나에 하나씩 실행한다(`&&`, `||`, `;` 로 잇지 않는다).
- 스킬 결과물은 이 폴더 `output/` 에 저장된다(스킬이 만든다). 그 밖의 결과물도 `output/` 에 둔다(없으면 만든다).
- 파일 삭제, 프로그램 설치, 외부로 보내는 작업은 먼저 사용자에게 묻는다.
