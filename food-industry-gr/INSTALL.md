# 설치 안내 (Claude가 읽고 진행하는 문서)

> 이 문서는 사용자의 Claude가 따라 하는 절차다. 사용자는 개발자가 아니므로 한 단계씩, 쉬운 말로 안내한다.
> Windows 기준 문서다. 명령은 PowerShell 도구로 실행한다.
> 「미확인」 표시는 아직 직접 확인하지 못한 부분이다. 그 단계에서 막히면 추측으로 우회하지 말고 사용자에게 상황을 그대로 알린다.

## 원칙
- **API 키 값은 절대 보지 않는다.** 묻지 않고, 출력하지 않고, 파일이나 명령에 적지 않는다. 키가 들어 있는지만 확인한다(3단계).
- 프로그램 설치는 무엇을 설치하는지 목록으로 보여 주고 승인을 받은 뒤 실행한다.
- `.mcp.json` 에 키를 적거나 `env` 항목을 추가하지 않는다. 각 MCP는 앱이 받은 Windows 사용자 환경 변수를 그대로 물려받는다.
- 명령은 도구 호출 하나에 하나씩 실행한다(`&&`, `||`, `;` 로 잇지 않는다).
- **회사 PC 제한으로 막히면 멈춘다.** 관리자 권한 요구, "조직에서 관리" 같은 정책 차단, 인증서·프록시 오류가 나면 우회 방법을 찾지 말고, 오류 문구를 그대로 보여 주며 회사 IT 담당자에게 보여 달라고 안내한다.
- 명령을 실행할 때 Claude 앱이 "허용할까요?" 카드를 띄우면, 무엇을 하는 명령인지 한 줄로 말하고 사용자에게 **허용**을 눌러 달라고 한다.

## 중간에 앱을 다시 열었을 때 (이어서 하기)
설치 중 앱을 두 번 다시 열게 된다(1단계 끝, 3단계 끝). 다시 연 뒤 사용자는 같은 폴더에서 **"INSTALL.md 이어서 해줘"** 라고 말하면 된다. 그러면 Claude는:
1. 1단계 "설치 확인 명령"과 3단계 키 확인 스크립트를 다시 돌려 어디까지 됐는지 본다.
2. 이미 된 단계는 건너뛰고, 안 된 첫 단계부터 이어서 안내한다.

## 1. 필요한 프로그램 설치

이 폴더를 쓰려면 아래 4개가 필요하고, Git은 있으면 업데이트가 편하다. 이미 있는 것은 건너뛰고, 없는 것만 설치한다. 표의 명령은 PowerShell 문법이므로 PowerShell 도구로 실행한다.

| 프로그램 | 쓰는 곳 | 설치 확인 명령 | 설치 명령 |
|---|---|---|---|
| Node.js (LTS) | MCP 4개(law·dart·kordoc·assembly) 실행 | `node --version` → 22.13 이상 | `winget install -e --id OpenJS.NodeJS.LTS --source winget` |
| Python 3.12 | 스킬 8개 실행 | `py -3 --version` → 3.9 이상 | `winget install -e --id Python.Python.3.12 --source winget` |
| Google Chrome | 국회 MCP가 국회 사이트를 열 때 | 아래 3개 중 하나가 `True`: `Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"`, `Test-Path "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"`, `Test-Path "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"` | `winget install -e --id Google.Chrome --source winget` |
| Poppler (pdftotext) | 국회 검토보고서·국감 보고서 PDF 본문 읽기 | `pdftotext -v` | `winget install -e --id oschwartz10612.Poppler --source winget` |
| Git for Windows (선택) | 업데이트(`git pull`) | `git --version` | `winget install -e --id Git.Git --source winget` |

### 진행 순서
1. `winget --version` 으로 설치 도구가 있는지 본다. 없으면 사용자에게 Microsoft Store에서 **앱 설치 관리자(App Installer)** 를 설치해 달라고 안내하고 기다린다.
2. 표의 "설치 확인 명령"을 하나씩 실행해 없는 프로그램을 추린다.
3. 없는 프로그램 목록을 사용자에게 보여 주고 승인을 받는다.
4. 승인받은 것만 "설치 명령"으로 하나씩 설치한다. 설치 중 Windows가 "변경을 허용할까요?" 창을 띄우면 사용자에게 **예**를 눌러 달라고 한다.
   - winget이 약관 동의를 물으면(`Do you agree to all the source agreements terms?` 등) 사용자에게 그대로 알리고, 사용자가 동의하면 그 명령 끝에 `--accept-source-agreements --accept-package-agreements` 를 붙여 다시 실행한다.
5. 다 설치했으면 Claude 데스크톱 앱을 **완전히 종료했다가 다시 열어 달라고** 한다(작업 표시줄 트레이 아이콘까지 종료). 앱은 시작할 때만 새 프로그램을 찾는다. 다시 연 뒤 "INSTALL.md 이어서 해줘" 라고 말하라고 미리 알려 둔다.
   - 2·3단계(키 발급·입력)를 바로 이어서 할 수 있으면, 여기서 다시 열지 말고 3단계 끝에서 한 번만 다시 열어도 된다.
6. 다시 연 뒤 "설치 확인 명령"을 모두 다시 실행해 다 되는지 확인한다.

### 막힐 때
- **Python**: 이 폴더의 스킬은 `py -3` 으로 실행한다. 설치 후에도 `py -3 --version` 이 안 되면 「미확인」(winget 설치가 `py` 실행기를 넣는지 Windows에서 아직 못 봤다) — 사용자에게 https://www.python.org/downloads/ 에서 설치 프로그램을 받아 기본값으로 설치해 달라고 한다. 이미 있던 `python` 명령이 아무 버전도 안 찍고 끝나거나 Microsoft Store를 열면 그것은 Store 바로가기일 뿐 Python이 아니므로 쓰지 않는다.
- **Poppler**: 설치 후에도 `pdftotext -v` 가 안 되면 「미확인」(PATH 등록 여부를 Windows에서 아직 못 봤다). `Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter pdftotext.exe` 로 파일 위치를 찾아 사용자에게 그 전체 경로를 알려 준다. 사용자가 3단계 환경 변수 창에서 이름 `ASSEMBLY_PDFTOTEXT`, 값에 그 경로를 넣는다(키와 같이 넣으면 앱을 한 번만 다시 열면 된다). 그래도 안 되면 멈추고 알린다. Poppler가 없어도 국회 MCP의 다른 기능은 모두 돈다.
- **Git**: 설치가 안 되면 건너뛴다. Claude는 PowerShell로 동작하므로 진행에는 문제없고, 업데이트는 ZIP으로 한다.

## 2. API 키 발급 (사이트는 Claude가 열고, 입력은 사용자가)

| 환경 변수 이름 | 사이트 | 쓰는 곳 | 발급까지 |
|---|---|---|---|
| `LAW_OC` | 국가법령정보 공동활용 | 법령 MCP | 신청 후 승인 1~2일 |
| `DART_API_KEY` | OpenDART | 공시 MCP (키가 없으면 시작하지 않음) | 개인회원은 즉시 |
| `ASSEMBLY_API_KEY` | 열린국회정보 | 국회 MCP | 로그인 후 버튼 한 번 |

진행 방법:
- 승인을 기다려야 하는 법령부터 신청하게 한다. 이미 받은 키는 건너뛴다.
- 사이트마다 아래 페이지를 사용자의 평소 브라우저(Chrome·Edge 등)로 연다: `Start-Process "<주소>"`. 이때 "허용할까요?" 카드가 뜨면 **허용**을 눌러 달라고 한다. Claude 내장 브라우저는 쓰지 않는다. 창이 안 뜨면 주소를 채팅에 적어 직접 열게 한다.
- Claude는 그 화면을 볼 수 없다. 아래 순서대로 무엇을 누르고 쓰는지 한 단계씩 말로 안내하고, 사용자가 지금 보이는 화면을 말해 주면 그에 맞춰 다음을 안내한다. 화면이 안내와 다르면 추측하지 말고 무엇이 보이는지 묻는다.
- 가입, 본인인증, 비밀번호, 약관 동의, 신청서 입력·제출은 **사용자가 직접** 한다.
- 키가 화면에 나오면 복사만 해 두라고 한 뒤 3단계로 넘어간다. 키를 채팅에 붙여 넣지 말라고 알린다.

### 법령 `LAW_OC`
1. https://open.law.go.kr/LSO/login.do 를 연다. **[사용자 가입]** 으로 회원가입 후 로그인.
2. https://open.law.go.kr/LSO/openApi/cuAskList.do (상단 OPEN API → 활용신청)를 연다. 신청서를 쓰고 제출한다. 칸(안내 이미지 기준, 실제 화면과 다를 수 있음): 기업명·신청자명·전화번호, 시스템/홈페이지명·소개, 도메인("도메인 없음" 선택 가능)·서버 IP, 활용 목적, 구축기간.
   신청서의 OC(인증값) 칸은 사용자가 직접 정한다. 이 값이 그대로 `LAW_OC` 가 된다. 비밀번호와 다른 영문·숫자로 정하고 따로 적어 두라고 안내한다. Claude는 무엇으로 정했는지 묻지 않는다.
3. 심의 후 승인된다(1~2일). 승인 전에 OC로 법령 MCP를 부르면 어떻게 되는지는 「미확인」. 막히면 이용문의 044-200-6797.

### 공시 `DART_API_KEY`
1. https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do (인증키 신청)를 연다.
2. 사용자가 직접: 이용약관·개인정보 수집이용 동의(필수) → 사용자 구분 **개인** → 이메일(중복확인)·비밀번호 → API 사용환경·사용용도 → 등록. 확인 URL은 선택이라 비워도 된다.
3. 개인회원은 신청 완료 즉시 발급되고 결과가 메일로 온다. 키는 https://opendart.fss.or.kr/mng/userApiKeyListView.do (인증키 관리)에서 본다. 한 사람이 키를 2개 이상 받을 수 없다.

### 국회 `ASSEMBLY_API_KEY`
1. https://member.assembly.go.kr/member/join/joinSelectPage.do 를 연다. **일반회원** → 약관 동의 → 본인인증 → 정보 입력으로 국회 통합회원 가입.
2. https://open.assembly.go.kr/portal/openapi/openApiActKeyIssPage.do (Open API → 인증키 발급)를 연다. 로그인 창이 나오면 방금 만든 계정으로 로그인. 발급 화면에서 무엇을 묻는지는 「미확인」(로그인해야 보인다).
3. 키는 https://open.assembly.go.kr/portal/openapi/openApiActKeyPage.do (인증키 발급내역)에서 본다.

## 3. 키 넣기 (사용자가 직접)

1. 사용자에게 환경 변수 창을 열어 달라고 한다: 작업 표시줄 **시작** 버튼 → 검색창에 `환경 변수` 입력 → **계정의 환경 변수 편집** 클릭.
   - 검색에 안 나오면, 사용자 동의를 받고 Claude가 연다: `rundll32.exe "sysdm.cpl,EditEnvironmentVariables"` (따옴표를 빼면 PowerShell에서 창이 안 열린다).
2. 사용자에게 안내: 위쪽 "사용자 변수" 칸에서 **새로 만들기** → 변수 이름에 표의 이름(예: `LAW_OC`), 값에 발급받은 키 → 확인. 키마다 반복한다. 1단계에서 `ASSEMBLY_PDFTOTEXT` 를 넣기로 했다면 여기서 같이 넣는다. 다 넣었으면 맨 아래 **확인**.
3. Claude 데스크톱 앱을 완전히 종료했다가 다시 연다(앱은 시작할 때만 환경 변수를 읽는다). 다시 연 뒤 "INSTALL.md 이어서 해줘" 라고 말하라고 미리 알려 둔다.
4. 다시 연 뒤, 값은 보지 않고 **들어 있는지만** 확인한다. 이 폴더에서 실행:
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-keys.ps1
   ```
   키마다 `user`(Windows에 저장됐는지)와 `app`(지금 앱이 읽었는지)이 `True`/`False` 로 나온다.
   - 둘 다 `True`: 정상.
   - `user=True`, `app=False`: 앱을 완전히 종료하지 않고 다시 연 것이다. 트레이 아이콘까지 종료하고 다시 열게 한다.
   - `user=False`: 2번을 다시 안내한다(이름 철자, "시스템 변수" 칸이 아닌 "사용자 변수" 칸인지 확인).
   - 스크립트 실행이 회사 정책으로 막히면 원칙대로 멈추고 IT 담당자에게 보여 달라고 한다.

## 4. 폴더 신뢰와 MCP 승인

- 이 폴더를 처음 열면 폴더를 신뢰할지, `.mcp.json` 의 서버(law, dart, kordoc, assembly)를 쓸지 묻는다. 모두 허용하도록 안내한다. 화면 모양은 「미확인」. 이 질문은 1단계보다 먼저 나올 수도 있다.
- 폴더를 신뢰해야 `.claude/settings.json` 의 설정(서버 자동 승인, 권한)이 적용된다.
- Node.js를 설치하기 전이나 키를 넣기 전에는 서버가 "failed"로 보이는 것이 정상이다. 1~3단계를 마치고 앱을 다시 연 뒤에 판단한다.
- 처음 시작할 때 npx가 각 MCP를 내려받느라 수십 초 걸릴 수 있다. DART는 첫 실행 때 회사 목록을 추가로 받는다.

## 5. 도구 동작 확인

각 서버를 한 번씩 가볍게 호출하고, 결과를 사용자에게 한 줄씩 알려준다.

| 서버 | 확인 방법 |
|---|---|
| law | `search_law` 로 "식품위생법" 검색 → 법령이 나오면 성공. 인증 오류면 `LAW_OC` 확인 |
| dart | `get_company` 로 "롯데웰푸드" 조회 → 회사 개황이 나오면 성공. 서버가 안 보이면 `DART_API_KEY` 확인 |
| assembly | `assembly_bills` 로 법안명 "식품위생법", 최근 3건 검색 → 법안이 나오면 성공. 키 오류면 `ASSEMBLY_API_KEY`, 브라우저 오류면 Chrome 확인 |
| kordoc | 도구 목록이 보이면 성공(문서 파일이 생기면 그때 실제로 써 본다) |

- 국회 MCP의 `assembly_live_captions_record`(생중계 자막 녹화)는 백그라운드 프로그램을 오래 띄우므로 매번 사용자에게 묻도록 설정돼 있다.

## 6. 스킬 확인

이 폴더의 `.claude/skills/` 에 스킬 8개가 들어 있다(목록은 README.md). `<스킬 폴더>` 는 이 폴더 안 `.claude/skills/<스킬 이름>` 의 절대경로다.

| 스킬 | 확인 방법 |
|---|---|
| 대관 동향 `food-industry-watch` | 그 스킬의 `references/first-run.md` 를 처음부터 따른다. `py -3 -X utf8 "<스킬 폴더>/scripts/fiw.py" doctor` 는 처음에는 `3`(아직 할 확인이 남음)으로 끝나는 것이 정상이고, first-run.md 4절까지 마치면 `0` 으로 끝나야 한다. `-X utf8` 을 빼면 한국어 Windows에서 출력 오류(`UnicodeEncodeError`)로 멈춘다 |
| 나머지 7개 | 따로 설치할 것이 없다. 처음 쓸 때 해당 스킬이 환경을 점검하고, 막히면 오류를 그대로 보여 준다 |

- 뉴스를 모으는 스킬(조간 브리핑·뉴스 검색·대관 동향)은 Claude 데스크톱 앱의 **내장 브라우저**로 빅카인즈를 연다. Windows에서 내장 브라우저로 빅카인즈가 열리는지는 「미확인」 — 첫 실행 때 확인하고, 안 되면 멈추고 알린다. 처음 여는 사이트마다 허용 카드가 뜰 수 있다.
- 국회 자료는 국회 MCP(`ASSEMBLY_API_KEY`)로 가져온다. 키를 별도 입력창에 넣을 필요가 없다.
- **처음 쓰는 사람의 시간 단축**: 대관 동향 스킬은 개발자가 모아 둔 첫 3개월(2026-07-27~2026-10-26) 수집 자료를 `seed/` 폴더에 담아 둔다. 처음 실행할 때 Claude가 이 자료를 가져올지 먼저 묻는다. 가져오면 처음부터 다시 조사하지 않아도 된다. 기사 본문은 들어 있지 않고 제목·링크만 있다.
- 결과물 위치: 모든 스킬이 이 폴더 안 `output/` 에 저장한다(`output/reports/<스킬 이름>/`). 이 폴더는 공유되지 않는다(`.gitignore`).

## 문제가 생기면

- **서버가 "failed"로 뜸 (1~3단계를 다 마치고 앱을 다시 연 뒤에도)** — 「미확인」 Windows에서는 `npx` 를 `cmd /c` 로 감싸야 할 수 있다(korean-dart-mcp README의 Claude Desktop 안내). 공유 파일인 `.mcp.json` 은 고치지 않는다. 대신 사용자 승인을 받고, 이 폴더에서 같은 이름의 서버를 이 폴더 전용(local) 설정으로 `cmd /c` 형태로 등록한다. 공식 문서상 local 설정이 공유 `.mcp.json` 보다 우선한다(Code 탭에서는 「미확인」). 예:
  ```
  claude mcp add --scope local law -- cmd /c npx -y korean-law-mcp@4.15.14
  ```
  `claude` 명령이 없으면 여기서 멈추고 사용자에게 알린다.
- **키를 바꿨는데 반영이 안 됨** — 앱을 완전히 종료했다가 다시 연다.
- **회사망에서 법령 조회가 인증서 오류로 실패** — 원칙대로 먼저 IT 담당자에게 오류를 보여 준다. 회사가 허용하면 사용자 환경 변수 `LAW_API_PROTOCOL` 을 `http` 로 넣는 방법이 있다(korean-law-mcp README). 보안상 마지막 수단이다.

## 업데이트

- Git으로 받은 폴더면 `git pull`. ZIP으로 받았으면 새 ZIP을 받아 `food-industry-gr` 폴더만 덮어쓴다. 스킬 결과물은 이 폴더 안 `output/` 에 있으므로 덮어쓰기 전에 `output/` 폴더를 따로 옮겨 두었다가 다시 넣는다.
- 업데이트 뒤에는 1단계의 "설치 확인 명령"을 다시 돌려 새로 필요한 프로그램이 없는지 본다.
