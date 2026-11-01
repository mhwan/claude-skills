#!/usr/bin/env python3
"""Write the Markdown report and the gr.envelope/1 data file for a verified run.

Usage: render_report.py --run RUN_DIR

Needs work/rows.json, work/articles.json and a passing work/verified.json that was made from the current
work/summaries.json. Refuses (exit 1, nothing written) otherwise, and also when the search could not be read.
An earlier report with the same name is moved to history/ as .vN before the new one is put in place.
Exit code: 0 clean, 3 written but with warnings, 1 nothing written.
"""
import hashlib
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from lib import dates, envelope, paths  # noqa: E402
from run_search import KNOWN_PROVIDER_CODES  # noqa: E402
from lib.naver_news import STATUS_LABELS  # noqa: E402

SOURCE = 'bigkinds'
NAVER_SOURCE = 'naver'
NAVER_NOTICE = '네이버 대체판 — 본문 미확인'
NAVER_CREDIT = '출처: 네이버 뉴스 검색 API'
EVIDENCE_SHOWN = 80
TITLE_LABEL = '뉴스 검색 결과'
CHECKED_WHAT = '숫자·날짜·영문·직함 앞 이름·따옴표 구절'


def squash(text):
    return ' '.join(str(text or '').split())


def topic_of(query):
    """A file-name topic made from the search query (the name rules reject some characters outright)."""
    text = unicodedata.normalize('NFC', query or '')
    text = ''.join(' ' if (ch in paths.FORBIDDEN_TOPIC_CHARS or ord(ch) < 32) else ch for ch in text)
    tokens = [t.lstrip('-') for t in text.split()]
    topic = '-'.join(t for t in tokens if t).strip('-.')
    topic = topic[:paths.TOPIC_MAX].rstrip('-.')
    return topic or None


def md_url(url):
    return url.replace('(', '%28').replace(')', '%29').replace('<', '%3C').replace('>', '%3E')


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_ready(run, rows_doc):
    """None when the run may be rendered, else the reason it may not."""
    verified_path = run / 'work' / 'verified.json'
    if not verified_path.is_file():
        return 'the summaries were not verified: run verify_summaries.py first'
    verified = common.read_json(verified_path)
    if verified.get('ok') is not True:
        return 'the verification did not pass: fix the summaries and verify again'
    summaries_path = run / 'work' / 'summaries.json'
    current = sha256_of(summaries_path) if summaries_path.is_file() else None
    if verified.get('summaries_sha256') != current:
        return 'summaries.json changed after it was verified: verify again'
    articles = common.read_json(run / 'work' / 'articles.json').get('articles') or []
    verified_ids = set(r.get('id') for r in verified.get('results') or [])
    if verified_ids != set(a['id'] for a in articles):
        return 'the verified articles are not the current articles: run normalize and verify again'
    return None


def load_summaries(run, listed_ids):
    path = run / 'work' / 'summaries.json'
    if not path.is_file():
        return {}
    out = {}
    for entry in common.read_json(path).get('summaries') or []:
        if entry.get('id') in listed_ids:
            out[entry['id']] = (squash(entry.get('summary')), squash(entry.get('quote')) or None)
    return out


def outlet_names(job):
    if job.get('post_filter'):
        return list(job['post_filter'])
    by_code = dict((code, name) for name, code in KNOWN_PROVIDER_CODES.items())
    return [by_code.get(c, c) for c in job.get('provider_codes') or []]


def overview(rows):
    lines = []
    counts = Counter(r['outlet'] or '(매체 확인 필요)' for r in rows)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lines.append('- 매체별 건수: ' + ' · '.join('%s %d' % kv for kv in ordered))
    days = sorted(r['date'] for r in rows)
    lines.append('- 기사 날짜: %s ~ %s' % (days[0], days[-1]))
    return lines


def found_line(matched, n_sum):
    if matched == 0:
        return '- 찾은 기사 **0건**'
    if n_sum == matched:
        return '- 찾은 기사 **%d건**, %d건 모두 요약했습니다' % (matched, matched)
    return ('- 찾은 기사 **%d건**, %d건 중 %d건만 요약했습니다. 나머지는 제목과 링크만 있습니다. '
            '이어서 보려면 "나머지도 요약해줘"라고 말씀해 주세요.' % (matched, matched, n_sum))


def build_markdown(job, rows_doc, rows, summaries, checked, warnings):
    start, end, query = job['start'], job['end'], job['query']
    period = '%s~%s' % (start, end)
    matched, listed = rows_doc['matched'], len(rows)
    n_sum = sum(1 for r in rows if r['id'] in summaries)
    out = ['# %s — %s (%s)' % (TITLE_LABEL, query, period), '']
    out.append('- 스킬: %s %s · 만든 시각: %s' % (common.SKILL, common.VERSION, dates.now_iso()))
    names = outlet_names(job)
    out.append('- 요청: 검색어 "%s", 기간 %s%s' % (query, period, ', 매체 ' + ', '.join(names) if names else ''))
    out.append('- 자료원: 빅카인즈 — 정상 (검색 %d회)' % rows_doc['requests'])
    if checked:
        out.append('- 요약 검사: 요약 %d건을 원문과 대조했고(검사 대상: %s), %d건 모두 통과했습니다.'
                   % (checked, CHECKED_WHAT, checked))
    else:
        out.append('- 요약 검사: 요약한 기사가 없어 검사할 것이 없습니다.')
    out.append('- 기사 본문은 이 보고서에 옮기지 않았습니다. 작업용 본문 파일은 보고서를 만든 뒤 지웁니다.')
    out += ['', '## 한눈에', '', found_line(matched, n_sum)]
    if matched > listed:
        out.append('- 목록에는 최신 %d건만 실었습니다(한 보고서에 최대 %d건). 기간이나 검색어를 좁히면 나머지를 볼 수 있습니다.'
                   % (listed, common.CAPS['list']))
    if rows_doc.get('incomplete'):
        out.append('- 검색 결과가 %d건이라 한 번에 가져올 수 있는 한도를 넘어 일부만 수집했습니다. '
                   '기간을 나누어 다시 찾으면 빠짐없이 볼 수 있습니다.' % rows_doc['total'])
    if rows:
        out += overview(rows)
    else:
        out.append('- 이 기간에 검색어에 맞는 기사가 없습니다.')
    out += ['', '## 기사 목록 (최신순)', '']
    for i, row in enumerate(rows, 1):
        title = squash(row['title'])
        parts = [row['date']]
        if row['outlet']:
            parts.append(row['outlet'])
        parts.append('[원문 보기](%s)' % md_url(row['url']) if row['url'] else '링크 없음')
        out += ['### %d. %s' % (i, title), '', '- ' + ' · '.join(parts)]
        if row['id'] in summaries:
            summary, quote = summaries[row['id']]
            out.append('- 요약: %s' % summary)
            if quote:
                out.append('- 인용: "%s"%s' % (quote, ' (%s)' % row['outlet'] if row['outlet'] else ''))
        out.append('')
    out.append('---')
    out.append('확인 필요: 요약에서 기계로 검사한 것은 %s뿐입니다. 그 밖의 문장은 원문 링크에서 확인해 주세요.' % CHECKED_WHAT)
    if warnings:
        out.append('')
        out.append('경고:')
        out += ['- %s' % squash(w) for w in warnings]
    return '\n'.join(out) + '\n'


def build_naver_markdown(job, rows_doc, rows, warnings):
    """The Naver fallback report: titles, links, outlets and dates only, with the limits stated up front."""
    start, end, query = job['start'], job['end'], job['query']
    period = '%s~%s' % (start, end)
    fallback = rows_doc.get('fallback') or {}
    matched, listed = rows_doc['matched'], len(rows)
    evidence = squash(fallback.get('evidence'))
    if len(evidence) > EVIDENCE_SHOWN:
        evidence = evidence[:EVIDENCE_SHOWN] + '…'
    out = ['# %s — %s (%s) · %s' % (TITLE_LABEL, query, period, NAVER_NOTICE), '']
    out.append('> **%s.** 빅카인즈를 쓸 수 없어 네이버 뉴스 검색 API로 기사 제목·링크·매체·날짜만 모았습니다. '
               '본문을 보지 않았으므로 요약이 없습니다. 빅카인즈가 돌아오면 다시 모아야 합니다.' % NAVER_NOTICE)
    out.append('')
    out.append('- 스킬: %s %s · 만든 시각: %s' % (common.SKILL, common.VERSION, dates.now_iso()))
    names = outlet_names(job)
    out.append('- 요청: 검색어 "%s", 기간 %s%s' % (query, period, ', 매체 ' + ', '.join(names) if names else ''))
    out.append('- 자료원: 네이버 뉴스 검색 API (빅카인즈 대체, 사유: %s · 검색 %d회)'
               % (fallback.get('reason_label') or '확인 필요', rows_doc['requests']))
    out.append('- 빅카인즈에서 본 것: %s' % (evidence or '확인 필요'))
    out.append('- 요약: 없음. 본문을 확인하지 않아 기사 내용에 대한 문장을 쓰지 않았습니다.')
    if names:
        out.append('- 매체 고르기: 원문 주소(도메인)로 골랐습니다. 계열사·자회사 주소(예: 경제·스포츠 자매지)는 뺐고, '
                   '주소가 바뀐 기사는 빠졌을 수 있습니다(확인 필요).')
    else:
        out.append('- 매체: 원문 주소로 알아낸 이름입니다. 7개 주요 일간지가 아니면 주소를 그대로 적었습니다.')
    out += ['', '## 검색별 결과', '', '| 네이버 검색어 | 찾은 기사 | 어디까지 봤나 |', '|---|---|---|']
    for q in fallback.get('queries') or []:
        found = q.get('found') or 0
        out.append('| %s | %s | %s |' % (squash(q.get('query')).replace('|', '\\|'),
                                         '%d건' % found if found else '못 찾음',
                                         STATUS_LABELS.get(q.get('status'), '확인 필요')))
    notes = fallback.get('notes') or []
    if notes:
        out += ['', '검색식 바꾼 점 (확인 필요):'] + ['- %s' % squash(n) for n in notes]
    out += ['', '## 한눈에', '']
    out.append('- 찾은 기사 **%d건**' % matched if matched else '- 찾은 기사 **0건** (못 찾음)')
    if matched > listed:
        out.append('- 목록에는 최신 %d건만 실었습니다(한 보고서에 최대 %d건).' % (listed, common.CAPS['list']))
    if rows_doc.get('incomplete'):
        out.append('- 기간 끝까지 닿지 못한 검색이 있어 일부만 모았습니다. 기간을 좁히거나 빅카인즈가 돌아온 뒤 다시 찾아 주세요.')
    if rows:
        out += overview(rows)
    else:
        out.append('- 네이버 검색에서 이 기간에 맞는 기사를 못 찾았습니다. 없다고 단정할 수 없습니다.')
    out += ['', '## 기사 목록 (최신순)', '']
    for i, row in enumerate(rows, 1):
        parts = [row['date']]
        if row['outlet']:
            parts.append(row['outlet'])
        parts.append('[원문 보기](%s)' % md_url(row['url']) if row['url'] else '링크 없음')
        out += ['### %d. %s' % (i, squash(row['title'])), '', '- ' + ' · '.join(parts),
                '- 본문 미확인: 제목만 있습니다.', '']
    out.append('---')
    out.append('다시 모아야 함: 이 보고서는 대체판입니다. 빅카인즈가 돌아오면 같은 검색어·기간으로 다시 찾아 주세요.')
    out.append(NAVER_CREDIT)
    if warnings:
        out += ['', '경고:'] + ['- %s' % squash(w) for w in warnings]
    return '\n'.join(out) + '\n'


def next_history(stem):
    n = 1
    while paths.history_path(common.SKILL, stem, n, skill_dir=common.SKILL_DIR).exists():
        n += 1
    return paths.history_path(common.SKILL, stem, n, skill_dir=common.SKILL_DIR)


def write_text(path, text):
    tmp = path.with_name(path.name + '.tmp')
    with open(str(tmp), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    os.replace(str(tmp), str(path))


def render(run):
    rows_doc = common.read_json(run / 'work' / 'rows.json')
    job = common.load_job(run)
    status = rows_doc.get('status')
    if status not in ('ok', 'empty'):
        error = rows_doc.get('error') or 'the search could not be read'
        common.emit({'status': status, 'rows': 0})
        return None, 'no report: %s' % error
    reason = check_ready(run, rows_doc)
    if reason:
        return None, reason
    rows = rows_doc['rows']
    naver = rows_doc.get('source') == 'naver'
    # Naver fallback: no article text, so no summary is ever shown (verify already refuses any).
    summaries = {} if naver else load_summaries(run, set(r['id'] for r in rows))
    verified = common.read_json(run / 'work' / 'verified.json')
    warnings = list(rows_doc.get('warnings') or [])
    if rows_doc['matched'] > len(rows):
        warnings.append('검색 결과 %d건 중 최신 %d건만 목록에 넣었습니다.' % (rows_doc['matched'], len(rows)))
    if naver:
        fallback = rows_doc.get('fallback') or {}
        warnings.insert(0, '%s: 빅카인즈를 쓸 수 없어(%s) 네이버 뉴스 검색 API로 목록만 만들었습니다. 요약은 없습니다.'
                        % (NAVER_NOTICE, fallback.get('reason_label') or '사유 확인 필요'))
        warnings.insert(1, '다시 모아야 함: 빅카인즈가 돌아오면 같은 검색어·기간으로 다시 검색해 이 보고서를 바꿔야 합니다.')
        for q in fallback.get('queries') or []:
            if q.get('status') != 'complete':
                warnings.append('네이버 검색 "%s": %s' % (q.get('query'), STATUS_LABELS.get(q.get('status'), '확인 필요')))
        for note in fallback.get('notes') or []:
            warnings.append('검색식 변환: %s' % note)

    period = '%s~%s' % (job['start'], job['end'])
    data_rows = []
    for r in rows:
        item = {'id': r['id'], 'title': r['title'], 'date': r['date'], 'url': r['url'], 'outlet': r['outlet']}
        if r['id'] in summaries:
            item['summary'] = summaries[r['id']][0]
        data_rows.append(item)
    sources = [{'source': SOURCE, 'status': status, 'requests': int(rows_doc.get('requests') or 0)}]
    if naver:
        sources = [{'source': NAVER_SOURCE, 'status': status, 'requests': int(rows_doc.get('requests') or 0),
                    'error': '%s (빅카인즈 대체: %s)' % (NAVER_NOTICE, fallback.get('reason') or 'unknown')}]
    try:
        env = envelope.make_envelope(common.SKILL, period, sources, data_rows, warnings)
    except envelope.EnvelopeError as e:
        return None, 'the data file would not be valid: %s' % e
    if naver:
        text = build_naver_markdown(job, rows_doc, rows, env['warnings'])
    else:
        text = build_markdown(job, rows_doc, rows, summaries, verified.get('checked') or 0, env['warnings'])

    stem = paths.make_name(period=(job['start'], job['end']), topic=topic_of(job['query']))
    report = paths.report_path(common.SKILL, stem, 'md', skill_dir=common.SKILL_DIR)
    data = paths.data_path(common.SKILL, stem, skill_dir=common.SKILL_DIR)
    paths.ensure(report, parent=True)
    paths.ensure(data, parent=True)
    if report.exists():
        old = next_history(stem)
        paths.ensure(old, parent=True)
        shutil.move(str(report), str(old))
    write_text(report, text)
    envelope.write_envelope(data, env)
    common.record_step(run, 'render', report=str(report), data=str(data))
    result = {'report': str(report), 'data': str(data), 'status': env['status'], 'rows': len(env['rows'])}
    if naver:
        result.update({'source': NAVER_SOURCE, 'notice': NAVER_NOTICE, 'recollect_required': True})
    common.emit(result)
    return envelope.exit_code(env), None


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Write the report and the data file of a verified run.')
    parser.add_argument('--run', required=True)
    args = parser.parse_args(argv)
    try:
        run = common.resolve_run(args.run)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    try:
        code, reason = render(run)
    except (paths.InvalidName, paths.RootNotFound, OSError, ValueError, KeyError) as e:
        return common.fail('cannot write the report: %s' % e, 1)
    if code is None:
        return common.fail('refused: %s' % reason, 1)
    return code


if __name__ == '__main__':
    sys.exit(main())
