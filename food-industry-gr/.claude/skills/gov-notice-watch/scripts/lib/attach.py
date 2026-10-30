"""Attachments of korea.kr press releases: find the one document link of a page, download it, check it.

Only the first document link of a press page is taken. The link shape is not verified against the live
site yet (see the session report); a page without a matching link is recorded as no_link, never guessed.
Downloads go through the vendored net.Session, so robots.txt, pacing and the no-User-Agent rule apply.
Text extraction is not done here: the agent reads each saved file with the kordoc tool and writes the text.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from urllib.parse import urljoin, urlsplit

import net

MAX_BYTES = 10 * 1024 * 1024          # checked after the body is received (net reads the whole body)
OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'    # HWP 5.x is an OLE2 compound file
_HREF_RE = re.compile(r'''href\s*=\s*["']([^"']+)["']''', re.I)
_DOC_EXT_RE = re.compile(r'\.(hwpx?|pdf)$', re.I)


def file_links(doc, page_url, hosts):
    """The first document link of a page as {'url', 'name'}, or None.

    A link counts when it is https on an allowed host and its path is a document name (.hwp, .hwpx, .pdf)
    or looks like a file download address. Page order decides, so the first match is the attachment.
    """
    for raw in _HREF_RE.findall(doc or ''):
        url = urljoin(page_url, html.unescape(raw))
        parts = urlsplit(url)
        if parts.scheme != 'https' or (parts.hostname or '') not in hosts:
            continue
        path = parts.path.lower()
        query = parts.query.lower()
        if _DOC_EXT_RE.search(path) or 'download' in path or 'filedown' in path or 'fileid' in query:
            return {'url': url, 'name': parts.path.rsplit('/', 1)[-1] or 'attachment'}
    return None


def sniff(body):
    """'pdf', 'hwp', 'hwpx' by the first bytes of the file, or None for anything else."""
    if body.startswith(b'%PDF'):
        return 'pdf'
    if body.startswith(OLE_MAGIC):
        return 'hwp'
    if body.startswith(b'PK\x03\x04') and _is_hwpx(body):
        return 'hwpx'
    return None


def _is_hwpx(body):
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            return any(name.startswith('Contents/') for name in zf.namelist())
    except zipfile.BadZipFile:
        return False


def _result(status, reason=None, file=None, size=None):
    return {'attach': status, 'reason': reason, 'file': file, 'bytes': size}


def download(session, candidates, folder, hosts, max_bytes=MAX_BYTES):
    """Fetch each candidate ({key: {'url', 'name'}}) into folder as <key>.<kind>. Returns {key: result}.

    A robots refusal, an offline setting, or any HTTP error other than 200 stops every later request to
    that host in this run; the rows are recorded and nothing is retried.
    """
    results, stopped = {}, set()
    for key, cand in candidates.items():
        url = cand['url']
        host = urlsplit(url).hostname or ''
        if host not in hosts or urlsplit(url).scheme != 'https':
            results[key] = _result('refused', '허용되지 않은 주소')
            continue
        if host in stopped:
            results[key] = _result('refused', '앞의 요청이 막혀 이 사이트는 더 읽지 않음')
            continue
        try:
            r = session.get(url)
        except net.Blocked:
            stopped.add(host)
            results[key] = _result('unread', 'robots.txt 가 허용하지 않음')
            continue
        except net.OfflineError:
            stopped.add(host)
            results[key] = _result('unread', '오프라인 설정이라 요청하지 않음')
            continue
        if r.status != 200:
            stopped.add(host)
            results[key] = _result('failed', 'HTTP %s' % r.status + (' (%s)' % r.error if r.error else ''))
            continue
        final_host = urlsplit(r.url).hostname or ''
        if final_host not in hosts:
            results[key] = _result('refused', '리다이렉트 뒤 주소가 허용 목록 밖: %s' % final_host)
            continue
        if len(r.body) > max_bytes:
            results[key] = _result('refused', '%d바이트 — 10MB 를 넘음' % len(r.body))
            continue
        kind = sniff(r.body)
        if kind is None:
            results[key] = _result('refused', 'HWP·HWPX·PDF 파일이 아님')
            continue
        name = '%s.%s' % (key, kind)
        (folder / name).write_bytes(r.body)
        results[key] = _result('ok', None, name, len(r.body))
    return results
