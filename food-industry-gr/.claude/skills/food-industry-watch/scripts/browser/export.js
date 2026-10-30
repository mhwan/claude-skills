/*
 * food-industry-watch — move a job result out of the page in chunks (m3-spec §1.4).
 *
 * Evaluate this file once on the page that ran the job, then:
 *   window.__fiw.export('<job>', N)  -> {job, raw_len, b64_len, chunks, size}
 *   window.__fiw.chunk(i)            -> one chunk string, for i = 0 .. chunks-1, one tool call each
 * N is the `chunk:` value `fiw.py browser-script` prints (the size `doctor` verified; 150000 if omitted).
 * Every chunk is longer than the tool's inline limit, so the app saves it to a file; pass those
 * files to `fiw.py ingest --run ID --job JOB --expect-len B64_LEN FILE...`.
 */
(function () {
  const F = (window.__fiw = window.__fiw || {});
  const CHUNK = 150000;
  const PAD = '|PAD|' + 'P'.repeat(170000);

  function toBase64(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  F.export = async function (job, chunkChars) {
    const size = chunkChars === undefined ? CHUNK : chunkChars;
    if (!Number.isInteger(size) || size < 1000) throw new Error('chunk size must be an integer >= 1000: ' + chunkChars);
    const holder = F[job];
    if (!holder || typeof holder.result !== 'function') throw new Error('unknown job: ' + job);
    const st = holder.status();
    // A failed job is exported too, so the failure is recorded in the run instead of lost.
    if (st.phase !== 'done' && st.phase !== 'failed') throw new Error('job ' + job + ' is ' + st.phase);
    const raw = JSON.stringify(holder.result());
    const stream = new Blob([new TextEncoder().encode(raw)]).stream()
      .pipeThrough(new CompressionStream('gzip'));
    const gz = new Uint8Array(await new Response(stream).arrayBuffer());
    const b64 = toBase64(gz);
    window.__fiwExport = { job: job, b64: b64, size: size };
    return { job: job, raw_len: raw.length, b64_len: b64.length, chunks: Math.ceil(b64.length / size), size: size };
  };

  F.chunk = function (i) {
    const ex = window.__fiwExport;
    if (!ex) throw new Error('call window.__fiw.export(job) first');
    const n = Math.ceil(ex.b64.length / ex.size);
    if (!(i >= 0 && i < n)) throw new Error('chunk index out of range 0..' + (n - 1));
    const head = 'FIWCHUNK|' + ex.job + '|' + i + '|' + n + '|' + ex.b64.length + '|';
    // The padding goes after the slice so even a short last chunk exceeds the inline limit.
    return head + ex.b64.slice(i * ex.size, (i + 1) * ex.size) + PAD;
  };

  return 'export loaded';
})();
