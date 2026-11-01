"""fiw.py render — render the run's work/report.md from the run files (M5, addendum §5)."""
from __future__ import annotations

from lib import accum, cli, dates, from_envelope, gates, layout, schema

from ._render_model import ISSUE_SECTIONS, SOURCE_LABELS, Run, top

TODO_NOTE = '> 🧭 표시는 편집 판단이다 — 묶음에서 계산한 값(N사M건 태그, 이번 보도 기간)과 쟁점을 잇는 문장.'
RETRO_NOTE = '> 소급 실행 — 현재 열린 예고·일정은 최신 보고서에'
NO_KEY_NOTE = '> 키 없음 — 국회 동향은 뉴스로만'
FAILED_LINE = '수집 실패 — 위 ⚠ 참고'
PRESS_NOTE = '> 제목은 정책브리핑 원제목 그대로다. 「식품 관련」은 제목 키워드로 거른 뒤 Claude 가 분류했다(🧭).'
MARKET_NOTE = '> 대관과 직접 닿는 건은 위 업계 표에 있다. 이 표는 맥락용이다.'
OPEN_SOURCE_PARTS = (
    ('lawmaking:입법의견 접수기간', '국민참여입법센터 「입법의견 접수기간」 칸'),
    ('ftc:기간', '공정위 게시판 「기간」 칸'),
    ('mfds:body_regex', '식약처 게시판 본문의 「…까지」 문장'),
)
HYGIENE_ROWS = (
    ('recall', '회수·판매중지',
     'https://www.foodsafetykorea.go.kr/portal/fooddanger/suspension.do?menu_no=4408&menu_grp=MENU_NEW02'),
    ('admin', '행정처분(식품안전나라 공개 목록)',
     'https://www.foodsafetykorea.go.kr/portal/fooddanger/administMeasureList.do?menu_no=4411&menu_grp=MENU_NEW02'),
    ('unfit', '검사부적합(국내, 공개 목록)',
     'https://www.foodsafetykorea.go.kr/portal/fooddanger/testUnfitDom.do?menu_no=4409&menu_grp=MENU_NEW02'),
)
HYGIENE_KIND_LABEL = {'recall': ('회수', '회수'), 'admin': ('행정처분', '처분'), 'unfit': ('검사부적합', '부적합')}
HYGIENE_NOTES = ('> 행정처분·부적합은 식품안전나라 누리집 공개 목록 기준 — 업종 구분 없음, Open API 집계와 건수가 다를 수 있음.',
                 '> 개인 농가 등 마스킹된 실명은 싣지 않는다.')
CREDIT = ('출처: 한국언론진흥재단 빅카인즈 · 열린국회정보(국회사무처) · 국민참여입법센터(법제처) · '
          '정책브리핑(공공누리 제1유형) · 식품안전나라(식품의약품안전처)')


def register(sub):
    p = sub.add_parser('render', help='render the run\'s work/report.md from the run files')
    p.add_argument('--run', required=True, metavar='ID', help='run id, YYYY-MM-DD-N')
    p.add_argument('--json', action='store_true', help='print the result JSON')
    p.set_defaults(func=main)


# ---------------------------------------------------------------- document builder

def cell(text) -> str:
    return ' '.join(str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')).replace('|', '\\|')


class Doc:
    """Blocks joined by one blank line, plus reference-style links numbered per family."""

    def __init__(self, dead=()):
        self.blocks = []
        self.key_of = {}
        self.counts = {}
        self.defs = []
        self.dead = set(dead)               # URLs linkcheck found deleted: never drawn
        self.omitted = set()                # the dead URLs that were asked for and left out
        self.dropped = 0                    # how many times a dead link was asked for (a URL can recur)
        self.failures = []                  # rows that would be left with no link

    def link(self, label, family, url) -> str:
        """The reference link, the bare label for no URL, '' for a dead URL (callers drop the empty part)."""
        if not url:
            return label
        if url in self.dead:
            self.omitted.add(url)
            self.dropped += 1
            return ''
        key = self.key_of.get(url)
        if key is None:
            n = self.counts.get(family, 0) + 1
            self.counts[family] = n
            key = f'{family}{n}'
            self.key_of[url] = key
            self.defs.append(f'[{key}]: {url}')
        return f'[{label}][{key}]'

    def inline(self, label, url) -> str:
        """An inline `[label](url)` link of an 'other items' line; '' for a dead URL."""
        if url in self.dead:
            self.omitted.add(url)
            self.dropped += 1
            return ''
        return f'[{label}]({url})'

    def links(self, items) -> str:
        """Join the non-empty links of one cell."""
        return ' '.join(x for x in items if x)

    def row_links(self, where, items) -> str:
        """A table row's link cell: dead links dropped; a row left with none is a failure naming `where`.

        `items` must be lazy (a generator): the dead links are counted while it is consumed here.
        """
        before = self.dropped
        out = self.links(items)
        if not out and self.dropped > before:
            self.failures.append({'where': where, 'kind': 'dead_link',
                                  'detail': 'every link of this row is a dead link (linkcheck) — nothing left to link'})
        return out

    def add(self, *lines):
        lines = [ln for ln in lines if ln is not None]
        if lines:
            self.blocks.append('\n'.join(lines))

    def table(self, columns, rows):
        lines = ['| ' + ' | '.join(columns) + ' |', '|' + '---|' * len(columns)]
        for r in rows:
            lines.append('| ' + ' | '.join(cell(c) for c in r) + ' |')
        self.add(*lines)

    def details(self, lines):
        if lines:
            self.add(f'<details><summary>그 밖 {len(lines)}건</summary>', '', *lines, '', '</details>')

    def text(self) -> str:
        return '\n\n'.join(self.blocks) + '\n'


def distinct(articles) -> list:
    """Articles with a URL not shown yet in the cell (BigKinds can hold one article twice, under two ids)."""
    out, seen = [], set()
    for a in articles:
        if a['url'] not in seen:
            seen.add(a['url'])
            out.append(a)
    return out


def heading(section) -> str:
    return '#' * section['heading_level'] + ' ' + section['title']


def sentences(items, basis=None) -> str:
    out = []
    for s in items or []:
        t = dates.with_basis(s['text'], basis(s)) if basis else s['text']
        if s['kind'] == 'edit':
            t = '🧭 ' + t
        if s.get('unverified'):
            t += ' (미확인)'
        out.append(t)
    return ' '.join(out)


def content(entry, basis=None) -> str:
    lwf = (entry or {}).get('lwf') or {}
    return ' '.join(x for x in (sentences((entry or {}).get('sentences'), basis),
                                sentences(lwf.get('sentences'), basis)) if x)


def own_cell(entry) -> str:
    return '✅ 직접' if ((entry or {}).get('lwf') or {}).get('direct') else '—'


# ---------------------------------------------------------------- top of the report

def header_line(run, notice_rows, bill_rows, sched_rows):
    bk = run.items('bigkinds')
    env = run.env['bigkinds']
    q = len(env['requests']) if env is not None and env['status'] in ('ok', 'partial') else 0
    kr = run.items('korea_kr')
    f = sum(1 for i in kr if run.included('korea_kr', i['id']))
    hy = {k: len(run.hygiene(k)) for k in ('recall', 'admin', 'unfit')}
    fb = (env or {}).get('fallback')
    return (f'{from_envelope.news_header(bk, q, fb)} → '
            f'묶음 {run.clusters_doc["rule_stage"]["clusters"]}개 → 대관 쟁점 {len(run.todo["issues"])}개 · '
            f'부처 보도자료 {len(kr)}건(식품 관련 {f}) · 입법·행정예고 {len(notice_rows)}건 · '
            f'법안 {len(bill_rows)}건 · 위원회 일정 {len(sched_rows)}건 · '
            f'시장·경영 {len(run.todo["market"])}개 주제 · '
            f'위생 동향(회수 {hy["recall"]} · 행정처분 {hy["admin"]} · 부적합 {hy["unfit"]})')


def notes(run, retro):
    lines = [TODO_NOTE]
    prev = run.previous_report()
    if prev:
        lines.append(f'> 지난 보고서: [{prev}]({prev})')
    if retro:
        lines.append(RETRO_NOTE)
    gap = accum.uncollected_note(run.manifest)
    if gap:
        lines.append(gap)
    for source, label in SOURCE_LABELS:
        env = run.env.get(source)
        if env is None:
            lines.append(f'> ⚠ 수집 실패 — {label}: 수집 파일 없음')
        elif env['status'] == 'failed':
            err = ' '.join((env['error'] or '원인 미기록').split())
            lines.append(f'> ⚠ 수집 실패 — {label}: {err}')
        elif env['status'] == 'no_key':
            lines.append(f'> ⚠ 수집 실패 — {label}: 키 없음')
        elif env['status'] == 'partial':
            bad = [r for r in env['requests'] if r['status'] != 'ok']
            if source == 'bigkinds':
                if env.get('fallback'):
                    lines.append(from_envelope.fallback_note(env['fallback']))
                    bad = [r for r in bad if r['target'] != from_envelope.FALLBACK_SOURCE]
                if bad:
                    lines.append('> ⚠ 일부 검색어 실패: ' + ', '.join(r.get('query') or r['target'] for r in bad))
            else:
                lines.append(f'> ⚠ 일부 수집 실패 — {label}: ' + ', '.join(r['target'] for r in bad))
    return lines


def open_line(run, doc, notice_rows):
    parts = []
    rows = [r for r in notice_rows if r['open']] + run.carried_rows(notice_rows)
    rows.sort(key=lambda r: (r['to'], r['title']))
    for r in rows:
        link = doc.link('예고', 'l', r['items'][0]['url'])
        day = run.short_day(r['to'])
        if r['d'] <= 10:
            parts.append(f'{r["short"]} **{day} 마감({r["d"]}일 남음)** {link}'.rstrip())
        else:
            parts.append(f'{r["short"]} {day} 마감 {link}'.rstrip())
    return ('> ⏰ **의견 접수 중** — ' + ' · '.join(parts)) if parts else None


# ---------------------------------------------------------------- sections

def render_three_lines(run, doc, section, retro, notice_rows):
    doc.add(heading(section))
    bullets = []
    for entry in run.summary['three_lines']:
        refs = list(entry['head']['refs'])
        for s in entry['sentences']:
            refs += s['refs']
        links, seen = [], set()
        for ref in refs:
            info = run.refs.get(ref)
            if info is None or not info['article'] or ref in seen:
                continue
            seen.add(ref)
            links.append(doc.link(info['outlet'], 'n', info['url']))
        tail = ' '.join(x for x in (sentences(entry['sentences'], run.day_basis), doc.links(links)) if x)
        bullets.append(f'- **{entry["head"]["text"]}** — {tail}')
    if bullets:
        doc.add(*bullets)
    else:
        doc.add('이번 기간 해당 쟁점 없음')
    if not retro:
        doc.add(open_line(run, doc, notice_rows))


def rep_line(run, doc, cid):
    a = run.rep(cid)
    if a is None:
        return None
    title = ' '.join(a['title'].split())
    if a['url']:
        link = doc.inline(a['outlet'], a['url'])
        if link:
            return f'- {run.short_day(a["date"])} {title} {link}'
    return f'- {run.short_day(a["date"])} {title} ({a["outlet"]})'


def render_issue_section(run, doc, section):
    todo_section = ISSUE_SECTIONS[section['id']]
    doc.add(heading(section))
    issues = run.section_issues(todo_section)
    cap = section['max_rows']
    shown = issues if cap is None else issues[:cap]
    overflow = [] if cap is None else issues[cap:]
    columns = run.columns(section)
    badges = run.badges()
    if shown:
        rows = []
        for issue in shown:
            s = run.sum_issues.get(issue['key']) or {}
            cids = run.issue_cids(issue)
            n, m, frm, to = run.core_stats(cids)
            nodes = run.flow_nodes(issue['key'])
            actors = [a['text'] for a in s.get('actors') or []]
            ents = [e for e in run.cluster_entities(cids) if e not in actors][:3]
            tag = f'{n}사' + (f'{m}건' if m > n else '')
            badge = ('🆕 ' if issue['key'].startswith('new:') else '▶ ') if badges else ''
            period = f'이번 보도 {run.span(frm, to)}'
            in_period = accum.period_row(run.manifest, issue['key'])
            if in_period:
                badge, first = in_period
                if first:
                    period = f'첫 보도 {run.short_day(first)} · 이번 기간 {run.span(frm, to)}'
            if len(nodes) >= 2:
                period += f' · 흐름 **{run.short_day(min(i["date"] for _, i in nodes))}~**'
            values = {
                '쟁점': badge + ' '.join([f'**{(s.get("title") or {}).get("text", issue["key"])}**', f'`{tag}`']
                                         + [f'`{a}`' for a in actors] + [f'`{e}`' for e in ents]),
                '기간': period,
                '내용': content(s, run.day_basis),
                run.watch['own_company']: own_cell(s),
            }
            cells = []
            for col in columns:
                if col == '링크':
                    cells.append(doc.row_links(issue['key'], (doc.link(a['outlet'], 'n', a['url'])
                                                              for a in distinct(issue['articles']))))
                else:
                    cells.append(values.get(col, ''))
            rows.append(cells)
        doc.table(columns, rows)
        flows = []
        for issue in shown:
            s = run.sum_issues.get(issue['key']) or {}
            nodes = run.flow_nodes(issue['key'])
            if not s.get('flow_name') or len(nodes) < 2:
                continue
            parts = []
            for node, info in nodes:
                link = doc.link(info['outlet'], 't', info['url'])
                day = run.short_day(info['date'])
                text = f'「{node["text"]}」' if node['mode'] == 'title' else node['text']
                parts.append(f'{day} {text} {link}'.rstrip())
            flows.append(f'- **{s["flow_name"]["text"]}** — ' + ' → '.join(parts))
        if flows:
            doc.add('### 흐름')
            doc.add(*flows)
    else:
        doc.add('이번 기간 해당 쟁점 없음')
    lines = [rep_line(run, doc, run.issue_cids(i)[0]) for i in overflow if run.issue_cids(i)]
    lines += [rep_line(run, doc, cid) for cid in run.other_cids(todo_section)]
    doc.details([ln for ln in lines if ln])


def render_press(run, doc, section):
    doc.add(heading(section))
    if run.status('korea_kr') not in ('ok', 'partial'):
        doc.add(FAILED_LINE)
        return
    depts = run.watch['agencies']
    intro = []
    for d in depts:
        items, inc, _ = run.press(d['name'])
        intro.append(f'**{run.short_agency(d["name"])}** {len(items)}건 중 식품 관련 {len(inc)}')
    doc.add(f'기간 {run.span(run.period["from"], run.period["to"])} · ' + ' · '.join(intro))
    doc.add(PRESS_NOTE)
    order = run.category_order()
    columns = run.columns(section)
    for d in depts:
        _, inc, oth = run.press(d['name'])
        doc.add(f'### {d["name"]}')
        if inc:
            by_cat = {}
            for it in inc:
                by_cat.setdefault(run.categories.get(('korea_kr', it['id'])) or '미분류', []).append(it)
            cats = sorted(by_cat, key=lambda c: order.index(c) if c in order else len(order))
            rows = []
            for cat in cats:
                its = sorted(by_cat[cat], key=lambda i: i['id'])
                its.sort(key=lambda i: i['date'], reverse=True)
                labelled = [i for i in its if run.labels.get(i['id'])]
                grouped = len(labelled) >= 3
                if grouped:
                    days = sorted(i['date'] for i in labelled)
                    rows.append({'분류': f'{cat} {len(labelled)}건', '날짜': run.span(days[0], days[-1]),
                                 '보도자료': ('group', labelled), '링크': '각 날짜 링크'})
                for i in its:
                    if grouped and i in labelled:
                        continue
                    rows.append({'분류': cat, '날짜': run.short_day(i['date']), '보도자료': i['title'],
                                 '링크': ('one', i)})
            out = []
            for r in rows:
                cells = []
                for col in columns:
                    v = r.get(col, '')
                    if isinstance(v, tuple) and v[0] == 'group':
                        v = ' · '.join(f'{run.labels[i["id"]]} {doc.link(run.short_day(i["date"]), "k", i["url"])}'.rstrip()
                                       for i in v[1])
                    elif isinstance(v, tuple):
                        v = doc.link('원문', 'k', v[1]['url'])
                    cells.append(v)
                out.append(cells)
            doc.table(columns, out)
        else:
            doc.add('이번 기간 식품 관련 보도자료 없음')
        oth = sorted(oth, key=lambda i: i['id'])
        oth.sort(key=lambda i: i['date'], reverse=True)
        doc.details([f'- {run.short_day(i["date"])} {" ".join(i["title"].split())} {doc.inline("원문", i["url"])}'.rstrip()
                     for i in oth])


def render_notices(run, doc, section, rows):
    doc.add(heading(section))
    if rows:
        out = []
        for r in rows:
            if r['family']:
                links = doc.links([doc.link(lab or '예고', 'l', i['url']) for i, lab in zip(r['items'], r['labels'])])
            else:
                links = doc.links([doc.link('예고', 'l', i['url']) for i in r['items']])
            if r['d'] is None:
                period, left = '미확인', '미확인'
            else:
                period = f'{run.short_day(r["from"])} ~ {run.short_day(r["to"])}'
                left = f'**{r["d"]}일**' if 0 <= r['d'] <= 10 else (f'{r["d"]}일' if r['d'] > 10 else '종료')
            values = {'부처': r['dept'], '구분': r['kind'], '법령·고시': r['title'], '의견 접수기간': period,
                      '남은 기간': left, '링크': links}
            out.append([values.get(c, '') for c in run.columns(section)])
        doc.table(run.columns(section), out)
        present = {i['open_source'] for r in rows for i in r['items']}
        parts = [text for src, text in OPEN_SOURCE_PARTS if src in present]
        doc.add('> 의견 접수기간 출처 — ' + ' · '.join(parts) + f'. 「남은 기간」은 {run.short_day(run.today)} 기준.')
    else:
        doc.add('이번 기간 식품 관련 예고 없음')
    lines = []
    for i in run.other_notices():
        dept = run.short_agency(i['dept'])
        if i['open_parse'] == 'ok':
            when = f'의견 기간 {run.span(i["open_from"], i["open_to"])}'
        else:
            when = '의견 기간 미확인'
        lines.append(f'- {" ".join(i["title"].split())} ({dept}, {when}) {doc.inline("예고", i["url"])}'.rstrip())
    doc.details(lines)


def unread_line(run, source):
    st = run.status(source)
    if st == 'no_key':
        return NO_KEY_NOTE
    if st not in ('ok', 'partial'):
        return FAILED_LINE
    return None


def render_bills(run, doc, section, rows):
    doc.add(heading(section))
    line = unread_line(run, 'assembly_bills')
    if line:
        doc.add(line)
        return
    if not rows:
        doc.add('기간 내 식품 관련 발의 없음(읽음)')
        return
    out = []
    for b in rows:
        alias = run.committee_alias(b['committee'])
        values = {'발의일': run.short_day(b['date']), '법안': b['name'],
                  '대표발의': b['proposer'].split('의원')[0].strip(),
                  '소관위': alias or '미배정',
                  '단계': b['stage'] or ('회부' if b['committee'] else '접수'),
                  '링크': doc.link('의안', 'b', b['url'])}
        out.append([values.get(c, '') for c in run.columns(section)])
    doc.table(run.columns(section), out)


def render_schedule(run, doc, section, rows, retro):
    doc.add(heading(section))
    line = unread_line(run, 'assembly_schedule')
    if line:
        doc.add(line)
        return
    if retro:
        doc.add(RETRO_NOTE)
        return
    if not rows:
        doc.add('등록된 일정 없음(읽음)')
        return
    out = []
    for s in rows:
        day = run.short_day(s['date']) + (f' {s["time"]}' if s['time'] else '')
        values = {'날짜': day, '위원회': run.committee_alias(s['committee']) or s['committee'],
                  '회의': s['meeting'], '링크': doc.link('일정', 's', s['url'])}
        out.append([values.get(c, '') for c in run.columns(section)])
    doc.table(run.columns(section), out)


def render_market(run, doc, section):
    doc.add(heading(section))
    topics = run.todo['market']
    if topics:
        out = []
        for t in topics:
            entry = run.sum_market.get(t['topic']) or {}
            cids = [c['cid'] for c in run.relevance['clusters'] if c['verdict'] == 'issue'
                    and c['section'] == 'market' and c['market_topic'] == t['topic']]
            n = len(run.core(cids))
            values = {'주제': f'**{t["topic"]}**', '건수': f'{n}건 🧭', '내용': content(entry, run.day_basis),
                      run.watch['own_company']: own_cell(entry)}
            cells = []
            for col in run.columns(section):
                if col == '링크':
                    cells.append(doc.row_links(t['topic'], (doc.link(a['outlet'], 'm', a['url'])
                                                            for a in distinct(t['articles']))))
                else:
                    cells.append(values.get(col, ''))
            out.append(cells)
        doc.table(run.columns(section), out)
    else:
        doc.add('이번 기간 해당 없음')
    doc.add(MARKET_NOTE)
    doc.details([ln for ln in (rep_line(run, doc, cid) for cid in run.other_cids('market')) if ln])


def hygiene_detail(run, kind):
    items = run.hygiene(kind)
    parts = []
    if kind == 'recall':
        grades = [i['grade'] for i in items]
        g = ' · '.join(f'{k} {grades.count(k)}' for k in ('1등급', '2등급', '3등급'))
        unknown = sum(1 for x in grades if x not in ('1등급', '2등급', '3등급'))
        if unknown:
            g += f' · 등급 미확인 {unknown}'
        parts.append(g)
        types = top([i['food_type'] for i in items], 2)
        if types:
            parts.append('유형 상위: ' + ', '.join(f'{v} {n}' for v, n in types))
    elif kind == 'admin':
        acts = top([run.action_type(i['action']) for i in items], 4)
        if acts:
            parts.append(' · '.join(f'{v} {n}' for v, n in acts))
        laws = top([i['law'] for i in items], 1)
        if laws:
            parts.append('위반 상위: ' + ' · '.join(f'{v} {n}' for v, n in laws))
    else:
        reasons = top([i['reason'] for i in items], 3)
        if reasons:
            parts.append('항목 상위: ' + ' · '.join(f'{v} {n}' for v, n in reasons))
        types = top([i['food_type'] for i in items], 2)
        if types:
            parts.append('품목 상위: ' + ' · '.join(f'{v} {n}' for v, n in types))
    return '. '.join(parts)


def render_hygiene(run, doc, section):
    doc.add(heading(section))
    if run.status('foodsafety') not in ('ok', 'partial'):
        doc.add(FAILED_LINE)
        return
    out = []
    for kind, name, url in HYGIENE_ROWS:
        items = run.hygiene(kind)
        link = doc.link('목록', 'fl', url)
        detail = hygiene_detail(run, kind) if items else '이번 기간 해당 없음'
        values = {'구분': name, '건수': str(len(items)), '내역': detail, '링크': link}
        out.append([values.get(c, '') for c in run.columns(section)])
    doc.table(run.columns(section), out)
    first = [i for i in run.hygiene('recall') if i['grade'] == '1등급' and not i['personal']]
    first.sort(key=lambda i: i['seq'])
    first.sort(key=lambda i: i['date'], reverse=True)
    if first:
        parts = []
        for i in first[:3]:
            name = i['product'] or i['company']
            if i['reason']:
                name += f'({i["reason"]})'
            parts.append(f'{name} {doc.link("회수", "f", i["url"])}'.rstrip())
        doc.add('**회수 1등급 주요 건**: ' + ' · '.join(parts))
    kinds = ('recall', 'admin', 'unfit')
    tracked = [i for i in run.items('foodsafety') if i['tracked_company'] and not i['personal']]
    tracked.sort(key=lambda i: (kinds.index(i['kind']), i['seq']))
    tracked.sort(key=lambda i: i['date'], reverse=True)
    if tracked:
        parts = []
        for i in tracked:
            kind_label, link_label = HYGIENE_KIND_LABEL[i['kind']]
            parts.append(f'{i["tracked_company"]} {kind_label} {doc.link(link_label, "f", i["url"])}'.rstrip())
        doc.add('**추적 회사 해당**: ' + ' · '.join(parts))
        doc.add(*HYGIENE_NOTES)
    else:
        doc.add('> 추적 회사는 이 기간 세 데이터 모두에 없었다.', *HYGIENE_NOTES)


def render_accumulated(run, doc):
    """The no-new-articles heading + list and the folded 💤 list (addendum §D)."""
    carried, dormant = run.carried_and_dormant()
    if carried:
        lines = []
        for i in carried:
            line = f'- {i["title"]} (마지막 기사 {run.short_day(i.get("last_article") or i["first_seen"])})'
            if i['flow']:
                last = max(i['flow'], key=lambda n: (n['date'], n['article_id']))
                line = f'{line} {doc.link(last.get("outlet") or "기사", "t", last["url"])}'.rstrip()
            lines.append(line)
        doc.add(accum.carried_heading(run.manifest))
        doc.add(*lines)
    if dormant:
        lines = [f'- {i["title"]} (마지막 기사 {run.short_day(i.get("last_article") or i["first_seen"])})'
                 for i in dormant]
        doc.add(f'<details><summary>💤 휴면 {len(dormant)}건</summary>', '', *lines, '', '</details>')


def excluded_lines(run, doc):
    lines = []
    for key, label in (('excluded_no_link', '링크 없어 제외'), ('excluded_no_body', '근거 본문 미수신')):
        entries = run.todo.get(key) or []
        if entries:
            lines.append(f'> {label} {len(entries)}건: ' + ' · '.join(
                f'{e["title"]}({"·".join(e["outlets"])})' for e in entries))
    for row in run.todo.get('fallback_rows') or []:
        for a in row['articles']:
            lines.append(from_envelope.fallback_line(run.short_day(a['date']), a['title'],
                                                     doc.inline(a['outlet'] or '기사', a['url'])))
    return lines


def build(run) -> str:
    retro = run.retro()
    notice_rows = run.notice_rows()
    bill_rows = run.bill_rows() if run.status('assembly_bills') in ('ok', 'partial') else []
    sched_rows = (run.schedule_rows() if run.status('assembly_schedule') in ('ok', 'partial') and not retro
                  else [])
    doc = Doc(run.dead)
    doc.add(f'# 식품업계 대관 현황판 — {run.period["from"]} ~ {run.period["to"]}')
    doc.add(header_line(run, notice_rows, bill_rows, sched_rows))
    doc.add(*notes(run, retro))
    for section in run.sections():
        sid = section['id']
        if sid == 'three_lines':
            render_three_lines(run, doc, section, retro, notice_rows)
        elif sid in ISSUE_SECTIONS:
            render_issue_section(run, doc, section)
        elif sid == 'press':
            render_press(run, doc, section)
        elif sid == 'notices':
            render_notices(run, doc, section, notice_rows)
        elif sid == 'assembly_bills':
            render_bills(run, doc, section, bill_rows)
        elif sid == 'assembly_schedule':
            render_schedule(run, doc, section, sched_rows, retro)
        elif sid == 'market':
            render_market(run, doc, section)
        elif sid == 'hygiene':
            render_hygiene(run, doc, section)
        else:                                   # 'assembly' and any heading-only section
            doc.add(heading(section))
    render_accumulated(run, doc)
    doc.add(*excluded_lines(run, doc))
    doc.add('---', from_envelope.credit(CREDIT, run.items('bigkinds'), (run.env['bigkinds'] or {}).get('fallback')))
    doc.add(*doc.defs)
    run.dead_omitted = len(doc.omitted)
    run.dead_failures = doc.failures
    return doc.text(), len(doc.defs)


def main(args) -> int:
    rd, manifest = gates.load_run(args.run)
    run = Run(rd, manifest)                     # missing work files → exit 2
    findings = []
    for gate, name in (('verify', 'summary'), ('relevance', 'relevance')):
        g = gates.read_gate(rd, gate)
        want = gates.file_hash(rd / 'work' / f'{name}.json')
        if g is None:
            findings.append({'where': f'gates/{gate}.json', 'kind': 'gate', 'detail': f'missing — run {gate} first'})
        elif g['exit'] != 0:
            findings.append({'where': f'gates/{gate}.json', 'kind': 'gate', 'detail': f'exit {g["exit"]}, not 0'})
        elif g['input_hash'] != want:
            findings.append({'where': f'gates/{gate}.json', 'kind': 'gate',
                             'detail': f'input_hash does not match the current {name}.json — run {gate} again'})
    if findings:
        result = {'run': args.run, 'report': None, 'findings': findings}
        if args.json:
            cli.print_json(result)
        else:
            gates.print_findings(findings)
            print('render: refused, nothing written, exit 1')
        return cli.EXIT_FAIL
    text, ndefs = build(run)
    left_out = f'render: {run.dead_omitted} dead link(s) left out (linkcheck)'
    if run.dead_failures:
        if args.json:
            cli.print_json({'run': args.run, 'report': None, 'findings': run.dead_failures})
        else:
            gates.print_findings(run.dead_failures)
            if run.dead_omitted:
                print(left_out)
            print('render: refused, a row would have no link, nothing written, exit 1')
        return cli.EXIT_FAIL
    path = layout.report_file(rd)
    schema.atomic_write(path, text)
    gates.record_stage(rd, manifest, 'render', cli.EXIT_OK, gates.file_hash(rd / 'work' / 'summary.json'))
    result = {'run': args.run, 'report': str(path), 'lines': text.count('\n'), 'references': ndefs,
              'findings': []}
    if run.dead_omitted:
        result['dead_links_left_out'] = run.dead_omitted
    if args.json:
        cli.print_json(result)
    else:
        if run.dead_omitted:
            print(left_out)
        print(f'render: {path} — {text.count(chr(10))} lines, {ndefs} references, exit 0')
    return cli.EXIT_OK
