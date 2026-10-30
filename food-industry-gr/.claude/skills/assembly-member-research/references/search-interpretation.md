# Reading and stating the results

Read this before writing the answer to the user.

## Contents
- What a number means
- Default list and ordering
- Quotes
- Words that must not appear
- Standard sentences

## What a number means
- "관련 발언 수" is the count of the member's own turns, in the meetings that were opened, whose text contains the topic words. Words split by a line break still match.
- It covers only the meetings that were opened (at most 5) and only the search pages that were seen. It is not the member's total activity.
- A count from a cut or unopened meeting is a lower bound: the table shows "최소 N". The data file holds the whole number.
- A member found only by search, with no minutes opened, gets the count of search hits and the same "최소" mark.

## Default list and ordering
- A topic-only request shows the 10 members with the most related speeches, with the note "기본 정렬: 발언 수 많은 순. 순위나 평가가 아닙니다". `--all` shows everyone.
- A request for named members (`--member`) keeps the requested order. A named member with no data still gets a row, marked "확인 필요".
- The order is only a sort. Never call it a ranking, and never compare members' attitudes.

## Quotes
- At most 3 speeches per member in the data file; the table shows the representative one.
- A quote is at most 40 characters, copied letter for letter from the minutes, with the minutes link next to it. A speech with no clean piece to quote is left out of the list instead of being filled with a guess.
- Never copy a piece that spans `…(N자 생략)`, and never take a quote from a search snippet.

## Words that must not appear
- Rank words such as "1위", evaluation words such as "가장 열심", "최다", "최고" about a person, and leaning words such as "보수적" or "친야".
- A hit word inside a quoted speech is the member's own text and is allowed.
- `check` fails the report when these appear outside quotation marks.

## Standard sentences
- Pages: "검색 결과는 N쪽까지 확인했습니다. 다음 쪽은 확인 필요입니다."
- Date filter: "기간 필터는 검색 결과 한 쪽(10건) 안에서만 적용됩니다."
- Opened meetings: "회의록은 N건을 열어 셌습니다. 나머지는 열지 않았습니다."
- Missing party or link: "확인 필요".
