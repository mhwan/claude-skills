# food-industry-gr

식품업계 대관(GR) 업무용 Claude 작업 폴더입니다. Claude 데스크톱 앱의 Code 탭에서 이 폴더를 열면 법령·공시·국회·문서 도구와 대관 스킬 8개가 준비된 상태로 Claude를 쓸 수 있습니다.

## 들어 있는 도구

| 도구 | 하는 일 | 필요한 키 |
|---|---|---|
| 법령 (korean-law-mcp) | 법령·조문·판례·자치법규 조회 | 국가법령정보 OC |
| 공시 (korean-dart-mcp) | 기업 공시 조회 | OpenDART 인증키 |
| 문서 (kordoc) | HWP·HWPX·PDF 읽기, 표 추출 | 없음 |
| 국회 ([korean-assembly-mcp](https://github.com/mhwan/korean-assembly-mcp)) | 의안·회의록·표결·일정·생중계 자막 조회 | 열린국회정보 인증키 |

## 들어 있는 스킬 (8개)

| 스킬 | 이렇게 말하면 |
|---|---|
| 대관 동향 (`food-industry-watch`) | "식품업계 동향 정리해줘", "최근 2주 대관 현황" — 아래 수집 스킬들을 묶어 기간별 보고서로 정리 |
| 조간 브리핑 (`daily-news-brief`) | "오늘 조간 브리핑", "사설 논조 비교" — 일간지 7개사 이슈 표 |
| 뉴스 검색 (`korean-news-search`) | "설탕 담합 기사 찾아줘" — 기사 목록·요약·원문 링크 |
| 부처 보도자료·입법예고 (`gov-notice-watch`) | "식약처 이번 주 보도자료", "의견 접수 중인 입법예고" |
| 법안 추적 (`assembly-bill-tracker`) | "이번 주 새로 나온 식품 법안" — 신규·진행·정체 표시 |
| 국회 일정·회의 (`assembly-hearing-watch`) | "이번 주 상임위 일정", "국정감사 시정·처리요구" |
| 의원 조사 (`assembly-member-research`) | "설탕세 관련해서 누가 발언했어" |
| 회수·행정처분 (`food-safety-records`) | "최근 회수·판매중지 식품", "이번 달 행정처분" |

모든 결과물은 이 폴더 안 `output/` 에 저장됩니다(공유되지 않음). 대관 동향 스킬에는 개발자가 모아 둔 첫 3개월(2026-07-27~2026-10-26) 수집 자료가 `seed/` 로 들어 있어, 처음 실행할 때 가져오면 처음부터 다시 조사하지 않아도 됩니다. 기사 본문은 들어 있지 않고 제목·링크만 있습니다.

## 필요한 프로그램

아래 4개가 필요하고, Git은 선택입니다. 없는 것은 Claude가 목록을 보여 주고 승인을 받은 뒤 설치합니다(Windows 설치 도구 winget 사용).

| 프로그램 | 쓰는 곳 |
|---|---|
| Node.js (LTS, 22.13 이상) | 위 도구 4개 실행 |
| Python 3 (3.9 이상) | 스킬 8개 실행 |
| Google Chrome | 국회 도구 |
| Poppler (pdftotext) | 국회 검토보고서 PDF 본문 읽기 |
| Git for Windows (선택) | 업데이트(`git pull`) |

## 시작하기

1. [Claude 데스크톱 앱](https://claude.ai/download)을 설치하고 로그인합니다.
2. 이 저장소 전체를 ZIP으로 받습니다. GitHub 페이지의 **Code → Download ZIP** 으로 받아 압축을 풉니다. (GitHub는 폴더 하나만 따로 받는 기능이 없습니다.)
3. 압축 푼 곳 안의 **`food-industry-gr` 폴더**를 앱의 **Code** 탭에서 엽니다. 저장소 맨 위 폴더가 아니라 이 안의 폴더입니다.
4. Claude에게 **"INSTALL.md 보고 설치해줘"** 라고 말합니다. Claude가 필요한 프로그램과 키 설정을 한 단계씩 안내합니다.

API 키는 Claude에게 보여주지 말고, 안내에 따라 직접 넣으세요(Windows 환경 변수 창).

Windows 앱에서의 실제 동작은 아직 직접 확인하지 못한 부분이 있습니다(INSTALL.md의 「미확인」 표시). 막히면 Claude가 상황을 그대로 알려 줍니다.
