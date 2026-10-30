/*
 * food-industry-watch — BigKinds collector (m3-spec §1.1). Evaluate on a page of
 * https://www.bigkinds.or.kr (e.g. /v2/news/index.do). robots.txt allows / for `*`.
 *
 *   window.__fiw.bigkinds_list.start(cfg)    cfg from `fiw.py browser-config --job bigkinds_list`
 *   window.__fiw.bigkinds_bodies.start(cfg)  cfg from `fiw.py browser-config --job bigkinds_bodies`
 *   .status() -> {phase, done, total, errors}   .result() -> object for export.js
 *
 * Measured 2026-10-26: `startNo` is a page number; pages of 2,000 do not overlap; 2,000 is not a
 * server cap. Requests use the browser's own headers. No retry after an HTTP error.
 */
(function () {
  const F = (window.__fiw = window.__fiw || {});
  const GAP_MS = 150;
  const WORKERS = 2;
  const MAX_PAGES = 20;
  const TIMEOUT_MS = 60000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function strip(s) {
    return String(s || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
      .replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
  }

  function bodyText(html) {
    return String(html || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '')
      .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'").replace(/&amp;/g, '&')
      .replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  function isoDate(d) {
    const s = String(d || '');
    return /^\d{8}$/.test(s) ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) : s;
  }

  function payload(query, from, to, page, size) {
    return {
      indexName: 'news', searchKey: query, searchKeys: [{}], byLine: '',
      searchFilterType: '1', searchScopeType: '1', searchSortType: 'date', sortMethod: 'date',
      mainTodayPersonYn: '', startDate: from, endDate: to, newsIds: [], categoryCodes: [],
      providerCodes: [], incidentCodes: [], networkNodeType: '', topicOrigin: '', dateCodes: [],
      editorialIs: false, startNo: page, resultNumber: size, isTmUsable: false, isNotTmUsable: false,
    };
  }

  // A request with no answer must end the job, not hang it (seen 2026-10-26: no response for minutes).
  async function httpJson(url, init) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    let r;
    try {
      r = await fetch(url, Object.assign({ credentials: 'same-origin', signal: ac.signal }, init));
      if (r.ok) return await r.json();
    } catch (e) {
      throw new Error(e && e.name === 'AbortError' ? 'timeout after ' + TIMEOUT_MS / 1000 + ' s'
        : String((e && e.message) || e).slice(0, 200));
    } finally {
      clearTimeout(timer);
    }
    const e = new Error('HTTP ' + r.status);
    e.status = r.status;
    throw e;
  }

  function search(query, from, to, page, size) {
    return httpJson('/api/news/search.do', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json;charset=UTF-8' },
      body: JSON.stringify(payload(query, from, to, page, size)),
    });
  }

  function errText(e) {
    return e && e.status ? 'HTTP ' + e.status : String((e && e.message) || e).slice(0, 200);
  }

  async function pool(tasks, fn) {
    let next = 0;
    const run = async () => {
      while (next < tasks.length) {
        const t = tasks[next++];
        await fn(t);
        await sleep(GAP_MS);
      }
    };
    await Promise.all(Array.from({ length: Math.min(WORKERS, tasks.length) }, run));
  }

  function makeJob(name, runner) {
    const st = { phase: 'idle', done: 0, total: 0, errors: [], out: null };
    return {
      start(cfg) {
        if (st.phase === 'running') return 'already running';
        st.phase = 'running'; st.done = 0; st.total = 0; st.errors = []; st.out = null;
        runner(cfg, st).then((out) => { st.out = out; st.phase = 'done'; })
          .catch((e) => { st.errors.push({ detail: errText(e) }); st.phase = 'failed'; });
        return 'started';
      },
      status() {
        return { phase: st.phase, done: st.done, total: st.total, errors: st.errors.slice(0, 10) };
      },
      result() {
        return st.out || { job: name, phase: st.phase, errors: st.errors };
      },
    };
  }

  // ------------------------------------------------------------------ list
  async function runList(cfg, st) {
    const from = cfg.from, to = cfg.to, size = cfg.page_size || 2000;
    const out = { job: 'bigkinds_list', period: { from: from, to: to }, probe: null,
                  requests: [], items: [], errors: st.errors };
    try {
      const p = await search(cfg.probe_query, from, from, 1, 1);
      out.probe = { rows: Number(p.totalCount) || 0, ok: (Number(p.totalCount) || 0) >= 1 };
    } catch (e) {
      out.probe = { rows: 0, ok: false };
      st.errors.push({ query: cfg.probe_query, detail: 'probe: ' + errText(e) });
      return out;
    }
    const byId = new Map();
    const queries = cfg.queries || [];
    st.total = queries.length;
    await pool(queries, async (q) => {
      const req = { target: '검색어 「' + q.query + '」', query: q.query, total: null,
                    received: 0, pages: 0, status: 'ok' };
      const seen = new Set();
      try {
        for (let page = 1; page <= MAX_PAGES; page++) {
          const r = await search(q.query, from, to, page, size);
          req.pages = page;
          if (req.total === null) req.total = Number(r.totalCount) || 0;
          const rows = r.resultList || [];
          for (const a of rows) {
            if (!a || !a.NEWS_ID || seen.has(a.NEWS_ID)) continue;
            seen.add(a.NEWS_ID);
            let it = byId.get(a.NEWS_ID);
            if (!it) {
              it = { id: String(a.NEWS_ID), title: strip(a.TITLE), date: isoDate(a.DATE),
                     outlet: strip(a.PROVIDER), url: String(a.PROVIDER_LINK_PAGE || ''),
                     excerpt: strip(a.CONTENT).slice(0, 200), queries: [] };
              byId.set(a.NEWS_ID, it);
            }
            if (it.queries.indexOf(q.query) < 0) it.queries.push(q.query);
          }
          req.received = seen.size;
          if (req.received >= req.total || rows.length === 0) break;
          await sleep(GAP_MS);
        }
        if (req.received < req.total) req.status = 'partial';
      } catch (e) {
        req.status = req.received > 0 ? 'partial' : 'failed';
        st.errors.push({ query: q.query, detail: errText(e) });
      }
      out.requests.push(req);
      st.done++;
    });
    const order = new Map(queries.map((q, i) => [q.query, i]));
    out.requests.sort((a, b) => order.get(a.query) - order.get(b.query));
    out.items = Array.from(byId.values());
    return out;
  }

  // ------------------------------------------------------------------ bodies
  async function runBodies(cfg, st) {
    const ids = cfg.ids || [];
    const out = { job: 'bigkinds_bodies', articles: [], errors: [] };
    st.total = ids.length;
    await pool(ids, async (id) => {
      try {
        const q = new URLSearchParams({ docId: id, returnCnt: '1', sectionDiv: '1000' });
        const r = await httpJson('/news/detailView.do?' + q.toString(), { method: 'GET' });
        const d = (r && r.detail) || {};
        const body = bodyText(d.CONTENT);
        if (!body) {
          out.errors.push({ id: id, status: null, detail: 'empty body' });
        } else {
          out.articles.push({ id: String(id), title: strip(d.TITLE), outlet: strip(d.PROVIDER),
                              date: isoDate(d.DATE), url: String(d.PROVIDER_LINK_PAGE || ''),
                              body: body, chars: Array.from(body).length });
        }
      } catch (e) {
        out.errors.push({ id: id, status: (e && e.status) || null, detail: errText(e) });
      }
      st.done++;
    });
    st.errors.push.apply(st.errors, out.errors.slice(0, 10));
    return out;
  }

  F.bigkinds_list = makeJob('bigkinds_list', runList);
  F.bigkinds_bodies = makeJob('bigkinds_bodies', runBodies);
  return 'bigkinds loaded';
})();
