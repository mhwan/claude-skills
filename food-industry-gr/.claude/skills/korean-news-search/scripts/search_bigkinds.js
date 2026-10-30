/*
 * BigKinds collector. Runs inside a browser tab that has https://www.bigkinds.or.kr/v2/news/index.do open
 * (the site only answers real browsers), and under node for tests with a fake network.
 *
 * Conditions in, lists and article texts out:
 *   collectList(job, io)    job = {query, start, end, provider_codes}; one search, the period is halved
 *                           when the site reports more articles than one answer can hold.
 *   collectBodies(ids, io)  article texts for a list of news ids.
 *   encodeChunks(kind, obj, size)  gzip + base64 text cut into chunks the agent can carry out of the tab;
 *                           scripts/ingest.py puts them back together.
 *
 * In the browser the file only defines window.__kns:
 *   startList(job) / startBodies(ids)   start work, returns at once
 *   status()                            {phase: idle | running | done | error, requests, items, ...}
 *   result(kind)                        the finished result ('list' or 'bodies')
 *   export(kind, size)                  prepare chunks, resolves to {b64_len, count}
 *   chunk(i)                            one chunk of text
 *
 * Politeness: at most 2 requests in flight, 150 ms between requests, one request per period,
 * a 60 s timeout, and no retry after an HTTP error.
 */
(function () {
  'use strict';

  var LIST_URL = '/api/news/search.do';
  var BODY_URL = '/news/detailView.do';
  var RESULT_MAX = 2000;
  var MAX_DEPTH = 3;
  var MAX_REQUESTS = 40;
  var WORKERS = 2;
  var GAP_MS = 150;
  var TIMEOUT_MS = 60000;
  var MAGIC = 'KNSCHUNK';
  var PAD_MARK = '|PAD|';
  var DAY_MS = 86400000;

  // ---- pure helpers -------------------------------------------------------------------------

  function buildPayload(job, startNo, resultNumber) {
    return {
      indexName: 'news',
      searchKey: job.query,
      searchKeys: [{}],
      byLine: '',
      searchFilterType: '1',
      searchScopeType: '1',
      searchSortType: 'date',
      sortMethod: 'date',
      mainTodayPersonYn: '',
      startDate: job.start,
      endDate: job.end,
      newsIds: [],
      categoryCodes: [],
      providerCodes: job.provider_codes || [],
      incidentCodes: [],
      networkNodeType: '',
      topicOrigin: '',
      dateCodes: [],
      editorialIs: false,
      startNo: startNo,
      resultNumber: resultNumber,
      isTmUsable: false,
      isNotTmUsable: false
    };
  }

  function decodeEntities(text) {
    return text
      .replace(/&nbsp;/g, ' ')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, '&');
  }

  function stripTags(text) {
    return decodeEntities(String(text === undefined || text === null ? '' : text).replace(/<[^>]*>/g, ''))
      .replace(/\s+/g, ' ')
      .trim();
  }

  function cleanBody(html) {
    var text = String(html === undefined || html === null ? '' : html).replace(/<br\s*\/?>/gi, '\n');
    text = decodeEntities(text.replace(/<[^>]+>/g, ''));
    return text
      .split('\n')
      .map(function (line) { return line.replace(/[ \t ]+/g, ' ').trim(); })
      .filter(function (line) { return line.length > 0; })
      .join('\n');
  }

  function parseList(resp) {
    var list = (resp && resp.resultList) || [];
    var total = Number(resp && resp.totalCount);
    return {
      total: isFinite(total) ? total : list.length,
      items: list.map(function (r) {
        return {
          id: String(r.NEWS_ID),
          title: stripTags(r.TITLE),
          date: String(r.DATE || ''),
          outlet: stripTags(r.PROVIDER),
          url: r.PROVIDER_LINK_PAGE ? String(r.PROVIDER_LINK_PAGE) : null
        };
      })
    };
  }

  function newsTime(id) {
    var parts = String(id).split('.');
    return parts.length > 1 ? parts[1] : '';
  }

  function toUtc(day) {
    var p = day.split('-');
    return Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }

  function fromUtc(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  // Two consecutive halves of a period (the first one is the longer when the length is odd); null for one day.
  function halve(start, end) {
    var days = Math.round((toUtc(end) - toUtc(start)) / DAY_MS) + 1;
    if (days < 2) { return null; }
    var firstLen = Math.ceil(days / 2);
    var mid = toUtc(start) + (firstLen - 1) * DAY_MS;
    return [[start, fromUtc(mid)], [fromUtc(mid + DAY_MS), end]];
  }

  function describe(e) {
    if (e && e.status) { return 'HTTP ' + e.status; }
    return (e && e.message) || String(e);
  }

  // ---- collecting ----------------------------------------------------------------------------

  async function collectList(job, io, onProgress) {
    var out = { status: 'ok', error: null, total: 0, matched: 0, requests: 0, split: 0, truncated: false, items: [] };
    var seen = {};
    var stack = [[job.start, job.end, 0]];
    var firstAnswer = true;
    try {
      while (stack.length) {
        if (out.requests >= MAX_REQUESTS) { out.truncated = true; break; }
        var span = stack.pop();
        if (out.requests > 0) { await io.sleep(GAP_MS); }
        var payload = buildPayload({
          query: job.query, start: span[0], end: span[1], provider_codes: job.provider_codes
        }, 1, RESULT_MAX);
        var resp = await io.post(LIST_URL, payload);
        out.requests += 1;
        var page = parseList(resp);
        if (firstAnswer) { out.total = page.total; firstAnswer = false; }
        var halves = page.total > RESULT_MAX && span[2] < MAX_DEPTH ? halve(span[0], span[1]) : null;
        if (halves) {
          out.split += 1;
          stack.push([halves[1][0], halves[1][1], span[2] + 1]);
          stack.push([halves[0][0], halves[0][1], span[2] + 1]);
        } else {
          if (page.total > page.items.length) { out.truncated = true; }
          page.items.forEach(function (item) {
            if (!seen[item.id]) { seen[item.id] = true; out.items.push(item); }
          });
        }
        if (onProgress) { onProgress({ requests: out.requests, items: out.items.length }); }
      }
    } catch (e) {
      out.status = 'failed';
      out.error = describe(e);
    }
    out.items.sort(function (a, b) {
      var x = newsTime(a.id), y = newsTime(b.id);
      return x < y ? 1 : (x > y ? -1 : 0);
    });
    out.matched = out.items.length;
    if (out.status === 'ok' && out.items.length === 0) {
      if (out.total === 0) {
        out.status = 'empty';
      } else {
        out.status = 'unread';
        out.error = 'the site reported ' + out.total + ' articles but returned none';
      }
    }
    return out;
  }

  async function collectBodies(ids, io, onProgress) {
    var res = { bodies: {}, failed: {} };
    var next = 0;
    var blocked = null;
    async function worker() {
      while (next < ids.length) {
        var id = ids[next];
        next += 1;
        if (blocked) { res.failed[id] = 'stopped after ' + blocked; continue; }
        try {
          var resp = await io.get(BODY_URL, { docId: id, returnCnt: 1, sectionDiv: 1000 });
          var detail = (resp && resp.detail) || {};
          var text = cleanBody(detail.CONTENT);
          if (text) {
            res.bodies[id] = { title: stripTags(detail.TITLE), text: text };
          } else {
            res.failed[id] = 'empty body';
          }
        } catch (e) {
          res.failed[id] = describe(e);
          if (e && (e.status === 403 || e.status === 429)) { blocked = describe(e); }
        }
        if (onProgress) { onProgress({ done: Object.keys(res.bodies).length + Object.keys(res.failed).length }); }
        await io.sleep(GAP_MS);
      }
    }
    var workers = [];
    for (var i = 0; i < WORKERS; i++) { workers.push(worker()); }
    await Promise.all(workers);
    return res;
  }

  // ---- carrying the result out of the tab -----------------------------------------------------

  function toBase64(bytes) {
    var parts = [];
    for (var i = 0; i < bytes.length; i += 0x8000) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
    }
    return btoa(parts.join(''));
  }

  async function gzip(text) {
    var bytes = new TextEncoder().encode(text);
    var stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // Every chunk is padded so that it is longer than size + 100: long answers are saved to a file by the app.
  async function encodeChunks(kind, obj, size) {
    var b64 = toBase64(await gzip(JSON.stringify(obj)));
    var count = Math.max(1, Math.ceil(b64.length / size));
    var chunks = [];
    for (var i = 0; i < count; i++) {
      var head = MAGIC + '|' + kind + '|' + i + '|' + count + '|' + b64.length + '|';
      var base = head + b64.slice(i * size, (i + 1) * size);
      var pad = Math.max(size + 200 - base.length - PAD_MARK.length, 1);
      chunks.push(base + PAD_MARK + new Array(pad + 1).join('P'));
    }
    return { b64_len: b64.length, chunks: chunks };
  }

  // ---- browser wrapper ------------------------------------------------------------------------

  function install(win) {
    var state = { phase: 'idle', kind: null, requests: 0, items: 0, done: 0, error: null, results: {}, chunks: [] };

    function jq() { return typeof jQuery !== 'undefined' ? jQuery : win.jQuery; }

    var io = {
      sleep: function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); },
      post: function (url, payload) {
        return Promise.resolve(jq().ajax({
          url: url, type: 'POST', contentType: 'application/json; charset=utf-8',
          dataType: 'json', data: JSON.stringify(payload), timeout: TIMEOUT_MS
        }));
      },
      get: function (url, params) {
        return Promise.resolve(jq().ajax({ url: url, type: 'GET', data: params, dataType: 'json', timeout: TIMEOUT_MS }));
      }
    };

    function run(kind, work) {
      state.phase = 'running';
      state.kind = kind;
      state.error = null;
      state.requests = 0;
      state.items = 0;
      state.done = 0;
      work(function (p) {
        for (var key in p) { if (Object.prototype.hasOwnProperty.call(p, key)) { state[key] = p[key]; } }
      }).then(function (res) {
        state.results[kind] = res;
        state.phase = 'done';
      }, function (e) {
        state.error = describe(e);
        state.phase = 'error';
      });
    }

    win.__kns = {
      startList: function (job) { run('list', function (progress) { return collectList(job, io, progress); }); },
      startBodies: function (ids) { run('bodies', function (progress) { return collectBodies(ids, io, progress); }); },
      status: function () {
        return { phase: state.phase, kind: state.kind, requests: state.requests, items: state.items,
                 done: state.done, error: state.error };
      },
      result: function (kind) { return state.results[kind || state.kind]; },
      export: async function (kind, size) {
        var res = state.results[kind];
        if (!res) { throw new Error('no result for ' + kind); }
        var enc = await encodeChunks(kind, res, size || 20000);
        state.chunks = enc.chunks;
        return { b64_len: enc.b64_len, count: enc.chunks.length };
      },
      chunk: function (i) { return state.chunks[i]; }
    };
  }

  var api = {
    buildPayload: buildPayload, parseList: parseList, newsTime: newsTime, halve: halve, cleanBody: cleanBody,
    collectList: collectList, collectBodies: collectBodies, encodeChunks: encodeChunks
  };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
  if (typeof window !== 'undefined') { install(window); }
})();
