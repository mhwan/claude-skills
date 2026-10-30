/**
 * daily-news-brief — BigKinds collector (driven from the browser)
 *
 * Where to run: a tab with https://www.bigkinds.or.kr/v2/news/index.do loaded
 * How to run:   evaluate this whole file with javascript_tool, then
 *               poll window.__collect.status() and fetch the result with window.__collect.result().
 *
 * Why a browser: the BigKinds WAF rejects non-browser clients (even with an honest UA) with a 400.
 * The Claude built-in browser gets through while stating Claude in its UA, so it is the only
 * honest route that does not disguise the UA. If headless access is needed, apply for the official BigKinds OPEN API.
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

  // Per-outlet cap on how many article bodies are fetched. Anything over the cap is not collected and is stated in the report (no silent truncation).
  // Editorials are not subject to this cap — see the EDITORIAL comment below.
  const MAX_BODIES_PER_OUTLET = 30;

  // Server-politeness values — the same as korean-news-search.
  // 2 concurrent requests, 150ms between requests, 60 seconds per request, no retry after an HTTP error,
  // a full stop on 403/429, and a cap on the number of requests.
  // Request cap: 7 outlets × (list 2 + editorials about 3 + columns about 6 + general 30) ≈ 300 requests is normal.
  // Editorials and columns are outside the cap and can grow from day to day, so 450 leaves some margin.
  // On reaching the cap it does not cut off silently; it is recorded in state.stopped and the error list.
  const CONCURRENCY = 2;
  const DELAY_MS = 150;
  const TIMEOUT_MS = 60000;
  const MAX_REQUESTS = 450;

  // Export (retrieval) chunks — scripts/ingest.py puts them back together. The format is the same as korean-news-search.
  const MAGIC = 'KNSCHUNK';
  const PAD_MARK = '|PAD|';

  // List query size. Observed: giving resultNumber at or above the total count returns everything in one request
  // (confirmed with Dong-A 277, Joongang 227, Maekyung 432). startNo is a 'page number', not an offset, so
  // startNo:101 returns 0 items — do not use it for pagination.
  const LIST_FETCH_SIZE = 2000;

  const state = {
    phase: 'idle',
    targetDate: null,
    outlets: {},     // outlet name -> { totalCount, fetched, capped, error }
    articles: [],
    errors: [],
    requests: 0,     // requests sent (list + body)
    stopped: null,   // the reason, if stopped by 403/429 or the request cap
    startedAt: null,
    finishedAt: null,
  };
  let exported = [];
  window.__collect = {
    status: () => ({
      phase: state.phase,
      outlets: state.outlets,
      articleCount: state.articles.length,
      requests: state.requests,
      stopped: state.stopped,
      errors: state.errors.slice(0, 10),
    }),
    result: () => state,
    // Retrieval: compress the result with gzip + base64 and split it into chunks. Read one chunk at a time with chunk(i).
    export: async (size) => {
      const enc = await encodeChunks('collect', state, size || 60000);
      exported = enc.chunks;
      return { b64_len: enc.b64_len, count: enc.chunks.length };
    },
    chunk: (i) => exported[i],
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

  // --- Column detection (collection step) -----------------------------
  // Editorials are fully obtained with editorialIs:true. Columns have no such filter
  // (we confirmed BigKinds' taxonomy has no opinion category).
  //
  // But the list request already returns everything. If columns are picked out of that full list
  // and moved outside the cap, only a few more bodies need fetching, with no extra list request.
  // Without this, columns get pushed out by the newest-30 cut and vanish outlet by outlet
  // (observed 2026-08-21: columns were found only for Chosun and Dong-A, and 0 for Kyunghyang, Maekyung, Hankyung).
  // ⚠️ This rule must **mean the same thing as** classify_opinion() in cluster.py.
  // What is decided here is 'what to collect outside the cap'; there it is 'classification'.
  // If they drift apart, an article is collected but not classified (a column shown as a regular article), or is
  // meant to be classified but was never collected (columns lost wholesale).
  //
  // No anchor (^) is used — the bracket position differs by outlet.
  //   Chosun    "[Mansangmul] ..."                              front
  //   Hankyoreh "... [Park Gwon-il's Dynamic Donut]"            end
  //   Dong-A    "... [Lee Jun-sik's Hansi Hansu]<382>"          middle
  const OPINION_URL_RE = /\/(opinion|editorial|column)(\/|$)/i;
  const COLUMN_TITLE_RE = new RegExp(
    '\\[[^\\]]*(' +
    '칼럼|시론|논단|기고|발언대|만물상|데스크|窓|창' +
    '|기자의\\s*시각|취재파일|취재수첩|기자수첩|현장에서|아침을\\s*열며|세상읽기' +
    ')[^\\]]*\\]');
  const NAMED_COLUMN_RE = new RegExp('\\[\\s*[가-힣]{2,4}의\\s+[^\\]]{2,}\\]');
  // A time expression is not a writer's name. Without this, a programme notice like "[Today's Channel A]..."
  // is caught by the '○○의 △△' pattern and misclassified as a column (observed).
  const TIME_WORD_RE = new RegExp('\\[\\s*(오늘|내일|어제|이번주|금주|이달|금월|올해)의\\s');
  // Even with a bracketed tag, these are not columns.
  //
  // Do not use \b as the boundary — JS regex \b is based on [A-Za-z0-9_], so
  // it does not count as a boundary after Hangul. Observed: "[Today's fortune/Aug 21]"
  // passed 'the 오늘의 운세 \b' check and was caught as a column. (Python re treats Hangul as word
  // characters, so \b on the cluster.py side works as intended — the two languages differ.)
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

  // The tail of NEWS_ID is 'YYYYMMDDhhmmssSSS'. The DATE field gives only the date,
  // so this is used for time ordering. Do not leave the ordering to the server's response order —
  // observed: the order changed with the resultNumber value (3 editorials dropped out of the first 100).
  function tsOf(item) {
    return String(item && item.NEWS_ID || '').split('.')[1] || '';
  }
  function byNewestFirst(a, b) {
    const x = tsOf(a), y = tsOf(b);
    return x < y ? 1 : x > y ? -1 : 0;
  }

  // Every request goes through guard(): nothing is sent after a stop, and exceeding the cap stops the run.
  // Even after an HTTP error nothing is re-sent (no retry). 403/429 stops everything.
  class StopError extends Error {}

  function guard() {
    if (state.stopped) throw new StopError(state.stopped);
    if (state.requests >= MAX_REQUESTS) {
      state.stopped = '요청 수 상한 ' + MAX_REQUESTS + '건에 닿아 멈춤';
      throw new StopError(state.stopped);
    }
    state.requests++;
  }

  function noteBlock(e) {
    if (e && (e.status === 403 || e.status === 429)) {
      state.stopped = 'HTTP ' + e.status + ' 를 받아 전체 중단';
    }
  }

  function post(url, body) {
    guard();
    return Promise.resolve(jQuery.ajax({
      url, type: 'POST',
      contentType: 'application/json;charset=UTF-8',
      data: JSON.stringify(body),
      timeout: TIMEOUT_MS,
    })).catch((e) => { noteBlock(e); throw e; });
  }

  function detailView(docId) {
    guard();
    return Promise.resolve(jQuery.ajax({
      url: '/news/detailView.do', type: 'GET',
      data: { docId: docId, returnCnt: 1, sectionDiv: 1000 },
      timeout: TIMEOUT_MS,
    })).catch((e) => { noteBlock(e); throw e; });
  }

  // --- Retrieval chunks --------------------------------------------
  // The built-in browser cannot write files. The result is compressed with gzip + base64 and split into chunks,
  // and one chunk is read per tool call. If a chunk exceeds the inline limit, the app saves it to a file.
  // Every chunk is padded to longer than size + 200 (so the last chunk is also saved to a file).
  function toBase64(bytes) {
    const parts = [];
    for (let i = 0; i < bytes.length; i += 0x8000) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
    }
    return btoa(parts.join(''));
  }

  async function gzip(text) {
    const bytes = new TextEncoder().encode(text);
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function encodeChunks(kind, obj, size) {
    const b64 = toBase64(await gzip(JSON.stringify(obj)));
    const count = Math.max(1, Math.ceil(b64.length / size));
    const chunks = [];
    for (let i = 0; i < count; i++) {
      const head = MAGIC + '|' + kind + '|' + i + '|' + count + '|' + b64.length + '|';
      const base = head + b64.slice(i * size, (i + 1) * size);
      const pad = Math.max(size + 200 - base.length - PAD_MARK.length, 1);
      chunks.push(base + PAD_MARK + 'P'.repeat(pad));
    }
    return { b64_len: b64.length, chunks: chunks };
  }

  // Strip markup such as <br/> → plain body text
  function clean(html) {
    if (!html) return '';
    return String(html)
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  // BigKinds TMS_NE_* often delivers entity names separated by newlines.
  // Forgetting the newline leaves '추\n이\n추미애' as one lump (confirmed in practice).
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
      // 1) EDITORIAL — a dedicated query for editorials. The cap is not applied.
      //
      // Why a separate query: the main list alone structurally missed editorials. In the 2026-08-21 check,
      // 12 of the 20 editorials from the 7 outlets (all of Joongang, Dong-A and Maekyung) were not in the main list.
      // editorialIs:true returns exactly the editorials (full check of 20 items, 0 false positives).
      // At about 3 a day there is no reason for a cap, and since the tone comparison is a briefing requirement,
      // a missing editorial means a missed requirement.
      const edRes = await post('/api/news/search.do',
        searchPayload([code], date, 1, 200, true));
      const edList = (edRes.resultList || []).slice().sort(byNewestFirst);
      rec.editorialCount = edRes.totalCount || edList.length;

      // 2) General list: take everything, sort it ourselves by publication time descending, then apply the cap.
      //    Trusting the server's response order and cutting from the front stops it being the 'newest 30' (confirmed in practice).
      const head = await post('/api/news/search.do',
        searchPayload([code], date, 1, LIST_FETCH_SIZE, false));
      rec.totalCount = head.totalCount || 0;
      const edIds = new Set(edList.map((a) => a.NEWS_ID));
      const pool = (head.resultList || [])
        .filter((a) => !edIds.has(a.NEWS_ID))   // avoid collecting the same article as an editorial
        .sort(byNewestFirst);

      // 2-b) Columns are also moved outside the cap. The list is already complete,
      //      so only a few more bodies need fetching, with no extra list request.
      const columns = pool.filter(looksLikeColumn);
      const colIds = new Set(columns.map((a) => a.NEWS_ID));
      rec.columnCount = columns.length;

      let list = pool.filter((a) => !colIds.has(a.NEWS_ID));
      const beforeCap = list.length;
      if (list.length > MAX_BODIES_PER_OUTLET) {
        list = list.slice(0, MAX_BODIES_PER_OUTLET);
      }
      rec.capped = beforeCap > list.length;

      // 3) Bodies: all editorials + all columns + the general ones up to the cap. detailView runs with limited concurrency.
      // kind is a label for statistics, kept distinct from the editorial flag —
      // columns are not editorials, so is_editorial is false, but they come from outside the cap,
      // and mixing them into the 'general 30' tally would make the report lie.
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
              // An article BigKinds indexed as an editorial. Columns are not caught here,
              // so classify_opinion() in cluster.py is still needed.
              is_editorial: editorial,
            });
            rec.fetched++;
            if (kind === 'editorial') rec.editorialFetched++;
            else if (kind === 'column') rec.columnFetched++;
            else rec.generalFetched++;
          } catch (e) {
            if (e instanceof StopError) { idx = targets.length; break; }  // after a stop, the remaining bodies are not fetched
            state.errors.push({ outlet: name, news_id: item.NEWS_ID, err: String(e && e.status || e) });
            if (state.stopped) { idx = targets.length; break; }
          }
          await sleep(DELAY_MS);
        }
      });
      await Promise.all(workers);
    } catch (e) {
      rec.error = e instanceof StopError ? e.message : String((e && e.status) || e);
      state.errors.push({ outlet: name, err: rec.error });
    }
    // If the run stopped while fetching this outlet, record that fact (the normalize.py coverage gate counts this outlet as failed).
    if (state.stopped && !rec.error) rec.error = state.stopped;
  }

  window.__collect.run = async function (targetDate) {
    state.phase = 'running';
    state.targetDate = targetDate;
    state.startedAt = new Date().toISOString();
    state.articles = [];
    state.errors = [];
    state.outlets = {};
    state.requests = 0;
    state.stopped = null;
    for (const [name, code] of Object.entries(PROVIDERS)) {
      if (state.stopped) {
        // Outlets after the stop are not requested; only the reason is recorded (the collection gate catches it as a failure).
        state.outlets[name] = { totalCount: 0, fetched: 0, capped: false, error: state.stopped };
        continue;
      }
      await collectOutlet(name, code, targetDate);
    }
    state.finishedAt = new Date().toISOString();
    state.phase = 'done';
    return state.articles.length;
  };

  return 'collector loaded — call window.__collect.run("YYYY-MM-DD")';
})();
