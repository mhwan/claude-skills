#!/bin/zsh
# 네이버 검색 MCP 시작 (macOS 전용).
# 데스크톱 앱이 띄운 MCP 는 ~/.zshrc 의 키를 물려받지 못하므로 여기서 읽는다.
# 이 파일에 키를 적지 않는다. 출력을 막는 것은 MCP 통신에 다른 글자가 섞이지 않게 하기 위해서다.
source "$HOME/.zshrc" >/dev/null 2>&1
exec npx -y @isnow890/naver-search-mcp@1.0.54
