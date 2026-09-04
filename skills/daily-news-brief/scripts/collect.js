/**
 * daily-news-brief — 빅카인즈 수집기 (브라우저 구동)
 *
 * 실행 위치: https://www.bigkinds.or.kr/v2/news/index.do 가 로드된 탭
 * 실행 방법: 이 파일 전체를 javascript_tool 로 평가한 뒤,
 *            window.__collect.status() 를 폴링하고 window.__collect.result() 로 회수한다.
 *
 * 왜 브라우저인가: 빅카인즈 WAF 가 비브라우저 클라이언트(정직한 UA 포함)를 400 으로 거른다.
 * Claude 내장 브라우저는 UA 에 Claude 를 명시하고도 통과하므로, UA 위장 없이 접근하는
 * 유일한 정직한 경로다. 헤드리스가 필요하면 빅카인즈 공식 OPEN API 를 신청할 것.
 */
(function () {
  const PROVIDERS = {
    '조선일보': '01100801',
    '중앙일보': '01100901',
    '동아일보': '01100401',
    '한겨레': '01101001',
    '경향신문': '01100101',
    '매일경제': '02100101',
    '한국경제': '02100601',
  };

  // 본문까지 받아올 매체당 상한. 초과분은 수집하지 않고 리포트에 명시한다(무언의 절삭 금지).
  // 사설은 이 상한의 적용을 받지 않는다 — 아래 EDITORIAL 주석 참조.
  const MAX_BODIES_PER_OUTLET = 30;
  const CONCURRENCY = 3;
  const DELAY_MS = 120; // 서버 부하 배려

  // 목록 조회 크기. 실측: resultNumber 를 총 건수 이상으로 주면 전량이 1회에 온다
  // (동아 277, 중앙 227, 매경 432 확인). startNo 는 오프셋이 아니라 '페이지 번호'라서
  // startNo:101 은 0건을 준다 — 페이지네이션으로 쓰지 말 것.
  const LIST_FETCH_SIZE = 2000;

  const state = {
    phase: 'idle',
    targetDate: null,
    outlets: {},     // 매체명 -> { totalCount, fetched, capped, error }
    articles: [],
    errors: [],
    startedAt: null,
    finishedAt: null,
  };
  window.__collect = {
    status: () => ({
      phase: state.phase,
      outlets: state.outlets,
      articleCount: state.articles.length,
      errors: state.errors.slice(0, 10),
    }),
    result: () => state,
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function searchPayload(codes, date, startNo, size, editorialIs) {
    return {
      indexName: 'news', searchKey: '', searchKeys: [{}], byLine: '',
      searchFilterType: '1', searchScopeType: '1',
      searchSortType: 'date', sortMethod: 'date', mainTodayPersonYn: '',
      startDate: date, endDate: date,
      newsIds: [], categoryCodes: [], providerCodes: codes,
      incidentCodes: [], networkNodeType: '', topicOrigin: '', dateCodes: [],
      editorialIs: !!editorialIs, startNo: startNo, resultNumber: size,
      isTmUsable: false, isNotTmUsable: false,
    };
  }

  // --- 칼럼 판별 (수집 단계) ------------------------------------------
  // 사설은 editorialIs:true 로 완전히 확보된다. 칼럼은 그런 필터가 없다
  // (빅카인즈 분류 체계에 오피니언 항목이 없음을 확인했다).
  //
  // 그런데 목록 요청은 이미 전량을 돌려준다. 그 전량에서 칼럼을 골라
  // 상한 밖으로 빼면 목록 요청 추가 없이 본문 몇 건만 더 받으면 된다.
  // 이렇게 하지 않으면 칼럼이 최신 30건 컷에 밀려 매체별로 통째로 빠진다
  // (실측 2026-08-21: 칼럼이 조선·동아에서만 잡히고 경향·매경·한경 0건).
  // ⚠️ 이 규칙은 cluster.py 의 classify_opinion() 과 **의미가 같아야 한다.**
  // 여기서 정하는 것은 '상한 밖에서 수집할 대상'이고 저기서 정하는 것은 '분류'다.
  // 어긋나면 수집됐는데 분류 안 되거나(칼럼이 일반 기사로 표시), 분류 대상인데
  // 애초에 수집이 안 되는(칼럼 통째 유실) 기사가 생긴다.
  //
  // 앵커(^)를 쓰지 않는다 — 매체마다 대괄호 위치가 다르다.
  //   조선   「[만물상] …」                                    앞
  //   한겨레 「'수저 자랑' 시대의 아픔 [박권일의 다이내믹 도넛]」  뒤
  //   동아   「잃고 얻은 것[이준식의 한시 한수]〈382〉」          중간
  const OPINION_URL_RE = /\/(opinion|editorial|column)(\/|$)/i;
  const COLUMN_TITLE_RE = new RegExp(
    '\\[[^\\]]*(' +
    '칼럼|시론|논단|기고|발언대|만물상|데스크|窓|창' +
    '|기자의\\s*시각|취재파일|취재수첩|기자수첩|현장에서|아침을\\s*열며|세상읽기' +
    ')[^\\]]*\\]');
  const NAMED_COLUMN_RE = /\[\s*[가-힣]{2,4}의\s+[^\]]{2,}\]/;
  // 시간 표현은 필자명이 아니다. 이게 없으면 「[오늘의 채널A]…」 같은
  // 편성 안내가 '○○의 △△' 패턴에 걸려 칼럼으로 오분류된다(실측).
  const TIME_WORD_RE = /\[\s*(오늘|내일|어제|이번주|금주|이달|금월|올해)의\s/;
  // 말머리가 대괄호라도 이건 칼럼이 아니다.
  //
  // 경계에 \b 를 쓰면 안 된다 — JS 정규식의 \b 는 [A-Za-z0-9_] 기준이라
  // 한글 뒤에서는 경계로 인정되지 않는다. 실측: 「[오늘의 운세/8월 21일]」이
  // '오늘의 운세\b' 를 통과해 칼럼으로 잡혔다. (파이썬 re 는 한글을 단어문자로
  // 보므로 cluster.py 쪽 \b 는 정상 동작한다 — 두 언어가 다르다.)
  const NOT_COLUMN_RE = new RegExp(
    '^\\s*\\[\\s*(속보|단독|긴급|종합|포토|포토뉴스|화보|부고|부음|인사|동정|알림'
    + '|사진|영상|카드뉴스|모집|공고|오늘의\\s*운세|운세|내일\\s*날씨|날씨|부동산\\s*캘린더)'
    + '(?=[\\s/\\]|·,.\\-–—:：]|$)');

  function looksLikeColumn(item) {
    const title = item.TITLE || '';
    if (NOT_COLUMN_RE.test(title)) return false;
    if (TIME_WORD_RE.test(title)) return false;
    if (COLUMN_TITLE_RE.test(title) || NAMED_COLUMN_RE.test(title)) return true;
    const url = item.PROVIDER_LINK_PAGE || '';
    return OPINION_URL_RE.test(url.replace(/^https?:\/\/[^/]+/, ''));
  }

  // NEWS_ID 뒤쪽이 'YYYYMMDDhhmmssSSS' 다. DATE 필드는 날짜까지만 주므로
  // 시각 정렬에는 이쪽을 쓴다. 정렬을 서버 응답 순서에 맡기면 안 된다 —
  // 실측상 resultNumber 값에 따라 순서가 달라졌다(사설 3건이 앞 100건에서 빠짐).
  function tsOf(item) {
    return String(item && item.NEWS_ID || '').split('.')[1] || '';
  }
  function byNewestFirst(a, b) {
    const x = tsOf(a), y = tsOf(b);
    return x < y ? 1 : x > y ? -1 : 0;
  }

  function post(url, body) {
    return jQuery.ajax({
      url, type: 'POST',
      contentType: 'application/json;charset=UTF-8',
      data: JSON.stringify(body),
    });
  }

  function detailView(docId) {
    return jQuery.ajax({
      url: '/news/detailView.do', type: 'GET',
      data: { docId: docId, returnCnt: 1, sectionDiv: 1000 },
    });
  }

  // <br/> 등 마크업 제거 → 순수 본문 텍스트
  function clean(html) {
    if (!html) return '';
    return String(html)
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  // 빅카인즈 TMS_NE_* 는 개체명을 개행으로 구분해 내려주는 경우가 많다.
  // 개행을 빼먹으면 '추\n이\n추미애' 가 한 덩어리로 남는다(실측 확인).
  function splitNE(v) {
    if (!v) return [];
    return String(v).split(/[\n\r,;|]/).map((s) => s.trim()).filter((s) => s.length > 1);
  }

  async function collectOutlet(name, code, date) {
    const rec = {
      totalCount: 0, fetched: 0, capped: false, error: null,
      editorialCount: 0, editorialFetched: 0,
      columnCount: 0, columnFetched: 0, generalFetched: 0,
    };
    state.outlets[name] = rec;
    try {
      // 1) EDITORIAL — 사설 전용 조회. 상한을 적용하지 않는다.
      //
      // 왜 별도 조회인가: 본 목록만으로는 사설이 구조적으로 누락됐다. 2026-08-21 실측에서
      // 7개사 사설 20건 중 12건(중앙·동아·매경 전량)이 본 목록에 없었다.
      // editorialIs:true 는 사설만 정확히 돌려준다(20건 전수 검사, 오탐 0).
      // 하루 3건 안팎이라 상한을 둘 이유가 없고, 논조 비교가 브리핑 요건이므로
      // 사설 누락은 곧 요건 미달이다.
      const edRes = await post('/api/news/search.do',
        searchPayload([code], date, 1, 200, true));
      const edList = (edRes.resultList || []).slice().sort(byNewestFirst);
      rec.editorialCount = edRes.totalCount || edList.length;

      // 2) 일반 목록: 전량을 받아 발행시각 내림차순으로 직접 정렬한 뒤 상한을 적용한다.
      //    서버 응답 순서를 믿고 앞에서 자르면 '최신 30건'이 아니게 된다(실측 확인).
      const head = await post('/api/news/search.do',
        searchPayload([code], date, 1, LIST_FETCH_SIZE, false));
      rec.totalCount = head.totalCount || 0;
      const edIds = new Set(edList.map((a) => a.NEWS_ID));
      const pool = (head.resultList || [])
        .filter((a) => !edIds.has(a.NEWS_ID))   // 사설과 중복 수집 방지
        .sort(byNewestFirst);

      // 2-b) 칼럼도 상한 밖으로 뺀다. 목록은 이미 전량을 받았으므로
      //      추가 목록 요청 없이 본문 몇 건만 더 받으면 된다.
      const columns = pool.filter(looksLikeColumn);
      const colIds = new Set(columns.map((a) => a.NEWS_ID));
      rec.columnCount = columns.length;

      let list = pool.filter((a) => !colIds.has(a.NEWS_ID));
      const beforeCap = list.length;
      if (list.length > MAX_BODIES_PER_OUTLET) {
        list = list.slice(0, MAX_BODIES_PER_OUTLET);
      }
      rec.capped = beforeCap > list.length;

      // 3) 본문: 사설 전량 + 칼럼 전량 + 일반 상한분. 제한 동시성으로 detailView
      // kind 는 통계용 라벨. editorial 플래그와 구분한다 —
      // 칼럼은 사설이 아니므로 is_editorial 은 false 지만, 상한 밖에서 온 것이라
      // '일반 30건' 집계에 섞이면 리포트가 거짓말을 한다.
      const targets = edList.map((x) => ({ item: x, editorial: true, kind: 'editorial' }))
        .concat(columns.map((x) => ({ item: x, editorial: false, kind: 'column' })))
        .concat(list.map((x) => ({ item: x, editorial: false, kind: 'general' })));
      let idx = 0;
      const workers = Array.from({ length: CONCURRENCY }, async () => {
        while (idx < targets.length) {
          const { item, editorial, kind } = targets[idx++];
          try {
            const r = await detailView(item.NEWS_ID);
            const d = (r && r.detail) || {};
            const body = clean(d.CONTENT);
            state.articles.push({
              outlet: name,
              outlet_code: code,
              news_id: item.NEWS_ID,
              title: item.TITLE || d.TITLE || '',
              subtitle: d.SUB_TITLE || '',
              body: body,
              body_len: body.length,
              published_date: item.DATE || d.DATE || '',
              published_ts: (item.NEWS_ID || '').split('.')[1] || '',
              byline: d.BYLINE || item.BYLINE || '',
              category: d.CATEGORY || item.CATEGORY || '',
              url: d.PROVIDER_LINK_PAGE || item.PROVIDER_LINK_PAGE || '',
              ne_person: splitNE(d.TMS_NE_PERSON),
              ne_org: splitNE(d.TMS_NE_ORGANIZATION),
              ne_location: splitNE(d.TMS_NE_LOCATION),
              sentiment: d.TMS_SENTIMENT_CLASS || '',
              similarity: d.TMS_SIMILARITY || '',
              // 빅카인즈가 사설로 색인한 기사. 칼럼은 여기 안 잡히므로
              // cluster.py 의 classify_opinion() 이 계속 필요하다.
              is_editorial: editorial,
            });
            rec.fetched++;
            if (kind === 'editorial') rec.editorialFetched++;
            else if (kind === 'column') rec.columnFetched++;
            else rec.generalFetched++;
          } catch (e) {
            state.errors.push({ outlet: name, news_id: item.NEWS_ID, err: String(e && e.status || e) });
          }
          await sleep(DELAY_MS);
        }
      });
      await Promise.all(workers);
    } catch (e) {
      rec.error = String((e && e.status) || e);
      state.errors.push({ outlet: name, err: rec.error });
    }
  }

  window.__collect.run = async function (targetDate) {
    state.phase = 'running';
    state.targetDate = targetDate;
    state.startedAt = new Date().toISOString();
    state.articles = [];
    state.errors = [];
    state.outlets = {};
    for (const [name, code] of Object.entries(PROVIDERS)) {
      await collectOutlet(name, code, targetDate);
    }
    state.finishedAt = new Date().toISOString();
    state.phase = 'done';
    return state.articles.length;
  };

  return 'collector loaded — call window.__collect.run("YYYY-MM-DD")';
})();
