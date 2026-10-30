/*
 * gov-notice-watch — lawmaking-centre notice list reader, run in the browser only.
 * Evaluate on a page of https://opinion.lawmaking.go.kr (robots.txt: Allow /). Script clients get a
 * guide page there, so this list is read only in the browser, with the browser's own requests.
 *
 *   window.__gnw.lawmaking.start(cfg)   cfg from `PY -B scripts/govnotice.py plan --home DIR --from F --to T`
 *                                       (the lawmaking_config line: from, to, window_start, departments)
 *   .status() / .result()               result() is the JSON to save and pass as --lawmaking <file>
 *
 * Rows are listed newest comment-period start first, 20 per page (measured 2026-10-26).
 * Not run live until the coordinator orders it (see references/browser-collection.md).
 */
(function () {
  const F = (window.__gnw = window.__gnw || {});
  const GAP_MS = 200;
  const MAX_PAGES = 15;
  const TIMEOUT_MS = 60000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const ws = (s) => String(s || '').replace(/\s+/g, ' ').trim();

  function addDays(iso, n) {
    const d = new Date(iso + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  // '2026. 10. 21.' -> '2026-10-21' (null when not a date)
  function dotDate(s) {
    const m = /(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})/.exec(s || '');
    if (!m) return null;
    return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
  }

  function cell(tr, label) {
    for (const td of tr.querySelectorAll('td')) {
      if (ws(td.getAttribute('data-th')) === label) return td;
    }
    return null;
  }

  // One list document -> {total, rows} ; throws 'structure' when the table is not the expected one.
  function parse(doc) {
    const ths = Array.from(doc.querySelectorAll('table td[data-th]')).map((td) => ws(td.getAttribute('data-th')));
    const totalM = new RegExp('전체\\s*([\\d,]+)\\s*건').exec(ws(doc.body ? doc.body.textContent : ''));
    const empty = doc.querySelector('table tbody') && !doc.querySelector('table tbody a[href*="/gcom/ogLmPp/"]');
    if (!totalM || (!empty && (ths.indexOf('법령 제명') < 0 || ths.indexOf('입법의견 접수기간') < 0))) {
      const e = new Error('structure');
      e.structure = true;
      throw e;
    }
    const rows = [];
    for (const tr of doc.querySelectorAll('table tbody tr')) {
      const nameTd = cell(tr, '법령 제명');
      const a = nameTd && nameTd.querySelector('a[href*="/gcom/ogLmPp/"]');
      if (!a) continue;
      const idM = /\/gcom\/ogLmPp\/(\d+)/.exec(a.getAttribute('href'));
      const ps = Array.from((cell(tr, '소관부처 (법령종류)') || tr).querySelectorAll('p')).map((p) => ws(p.textContent));
      const period = ws((cell(tr, '입법의견 접수기간') || {}).textContent);
      rows.push({
        id: idM ? idM[1] : '',
        title: ws(a.getAttribute('title') || a.textContent),
        dept: ps[0] || '',
        law_type: (ps[1] || '').replace(/[()]/g, '').trim() || null,
        period_text: period,
        url: 'https://opinion.lawmaking.go.kr/gcom/ogLmPp/' + (idM ? idM[1] : ''),
      });
    }
    return { total: Number(totalM[1].replace(/,/g, '')), rows: rows };
  }

  async function page(code, n) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    let r, text;
    try {
      r = await fetch('/gcom/ogLmPp?cptOfiOrgCd=' + encodeURIComponent(code)
        + '&finishIncludeYn=Y&pageIndex=' + n, { credentials: 'same-origin', signal: ac.signal });
      if (r.ok) text = await r.text();
    } catch (e) {
      throw new Error(e && e.name === 'AbortError' ? 'timeout after ' + TIMEOUT_MS / 1000 + ' s'
        : String((e && e.message) || e).slice(0, 200));
    } finally {
      clearTimeout(timer);
    }
    if (!r.ok) {
      const e = new Error('HTTP ' + r.status);
      e.status = r.status;
      throw e;
    }
    return parse(new DOMParser().parseFromString(text, 'text/html'));
  }

  const st = { phase: 'idle', done: 0, total: 0, errors: [], out: null };

  async function run(cfg) {
    const out = { job: 'lawmaking', period: { from: cfg.from, to: cfg.to || null }, probe: null,
                  requests: [], rows: [], errors: st.errors };
    // Same look-back as scripts/lib/mfds.py and ftc.py (180 days before the period start).
    const windowStart = cfg.window_start || addDays(cfg.from, -180);
    const depts = cfg.departments || [];
    st.total = depts.length;
    for (let di = 0; di < depts.length; di++) {
      const d = depts[di];
      const req = { target: '입법센터 ' + d.name, total: null, received: 0, pages: 0, status: 'ok' };
      try {
        let stopped = false;
        for (let n = 1; n <= MAX_PAGES; n++) {
          const p = await page(d.code, n);
          if (di === 0 && n === 1) out.probe = { rows: p.rows.length, ok: p.rows.length >= 1 };
          if (req.total === null) req.total = p.total;
          req.pages = n;
          req.received += p.rows.length;
          for (const row of p.rows) {
            out.rows.push(row);
            const start = dotDate(row.period_text.split('~')[0]);
            if (start && start < windowStart) stopped = true;
          }
          if (p.rows.length === 0 || req.received >= req.total) stopped = true;
          if (stopped) break;
          await sleep(GAP_MS);
        }
        if (!stopped) req.status = 'partial';
      } catch (e) {
        if (di === 0 && !out.probe) out.probe = { rows: 0, ok: false };
        req.status = req.received > 0 ? 'partial' : 'failed';
        st.errors.push({ target: req.target, detail: e.structure ? 'structure' : (e.status ? 'HTTP ' + e.status : String(e.message || e).slice(0, 200)) });
      }
      out.requests.push(req);
      st.done++;
      await sleep(GAP_MS);
    }
    return out;
  }

  F.lawmaking = {
    start(cfg) {
      if (st.phase === 'running') return 'already running';
      st.phase = 'running'; st.done = 0; st.total = 0; st.errors = []; st.out = null;
      run(cfg || {}).then((out) => { st.out = out; st.phase = 'done'; })
        .catch((e) => { st.errors.push({ detail: String(e && e.message || e).slice(0, 200) }); st.phase = 'failed'; });
      return 'started';
    },
    status() {
      return { phase: st.phase, done: st.done, total: st.total, errors: st.errors.slice(0, 10) };
    },
    result() {
      return st.out || { job: 'lawmaking', phase: st.phase, errors: st.errors };
    },
  };
  return 'lawmaking loaded';
})();
