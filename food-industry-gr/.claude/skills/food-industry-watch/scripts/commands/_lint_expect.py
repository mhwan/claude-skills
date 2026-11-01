"""What report.md must contain, re-derived from the run files for gate R (independent of render).

Links are written inline as `[label](url)`; lint resolves the report's reference links to the same
form before comparing, so key numbering is checked separately.

Blocks: ('para', text) · ('notes', [lines]) · ('list', [lines]) · ('details', [item lines]) ·
('table', columns, rows). A section is a list of (sub-heading or None, [blocks]).
"""
from __future__ import annotations

import re
from datetime import date

from lib import accum, dates, entities, from_envelope, gates, layout, notices, runview, schema

WARN_SOURCES = (('bigkinds', '빅카인즈'), ('korea_kr', '정책브리핑'), ('lawmaking_web', '국민참여입법센터'),
                ('mfds_notice', '식약처 예고 게시판'), ('ftc_notice', '공정위 예고 게시판'),
                ('assembly_bills', '열린국회정보 법안'), ('assembly_schedule', '열린국회정보 위원회 일정'),
                ('foodsafety', '식품안전나라'))
EDIT_NOTE = '> 🧭 표시는 편집 판단이다 — 묶음에서 계산한 값(N사M건 태그, 이번 보도 기간)과 쟁점을 잇는 문장.'
RETRO = '> 소급 실행 — 현재 열린 예고·일정은 최신 보고서에'
NO_KEY = '> 키 없음 — 국회 동향은 뉴스로만'
UNREAD = '수집 실패 — 위 ⚠ 참고'
CREDIT = ('출처: 한국언론진흥재단 빅카인즈 · 열린국회정보(국회사무처) · 국민참여입법센터(법제처) · '
          '정책브리핑(공공누리 제1유형) · 식품안전나라(식품의약품안전처)')
FS_LIST = 'https://www.foodsafetykorea.go.kr/portal/fooddanger/'
FS_ROWS = {'recall': ('회수·판매중지', FS_LIST + 'suspension.do?menu_no=4408&menu_grp=MENU_NEW02'),
           'admin': ('행정처분(식품안전나라 공개 목록)', FS_LIST + 'administMeasureList.do?menu_no=4411&menu_grp=MENU_NEW02'),
           'unfit': ('검사부적합(국내, 공개 목록)', FS_LIST + 'testUnfitDom.do?menu_no=4409&menu_grp=MENU_NEW02')}
ISSUE_SECTION_OF = {'gov': 'gov', 'assembly_issues': 'assembly', 'industry': 'industry'}
GOOD = ('ok', 'partial')


def lk(label, url):
    return f'[{label}]({url})' if url else label


def esc(text):
    return re.sub(r'\r\n|\r|\n', ' ', str(text)).replace('|', '\\|')


def iso_day(s) -> date:
    return dates.parse_date(s)


def ranked(values, limit):
    """Most frequent non-null values (ties: first seen), as 'value n' strings."""
    seen = []
    n = {}
    for v in values:
        if v is None:
            continue
        if v not in n:
            seen.append(v)
            n[v] = 0
        n[v] += 1
    best = sorted(range(len(seen)), key=lambda i: (-n[seen[i]], i))[:limit]
    return [f'{seen[i]} {n[seen[i]]}' for i in best]


def first_outlet(articles, url):
    return next(a['outlet'] for a in articles if a['url'] == url)


def say(entries, basis=None) -> str:
    """Rendered sentence list: 🧭 for edit, a parenthesised unverified marker for unverified, basis(e) before the final period."""
    return ' '.join(('🧭 ' if e['kind'] == 'edit' else '') + dates.with_basis(e['text'], basis(e) if basis else '')
                    + (' (미확인)' if e.get('unverified') else '') for e in entries or [])


class Expected:
    def __init__(self, rd, manifest, fmt=None):
        self.m = manifest
        self.end = manifest['period']['to']
        self.today = manifest['today']
        need = lambda n, c: gates.load_work(rd, n, c)  # noqa: E731
        self.cl = need('clusters', 'fiw.clusters/1')
        self.rel = need('relevance', 'fiw.relevance/1')
        self.pre = need('prefilter', 'fiw.prefilter/1')
        self.todo = need('todo', 'fiw.todo/1')
        self.summ = need('summary', 'fiw.summary/1')
        self.watch = layout.load_watch()
        self.fmt = fmt if fmt is not None else layout.load_format()
        self.runs = accum.load_state('runs.json')
        self.st_issues = accum.issues_list()
        self.st_notices = (accum.load_state('notices.json') or {}).get('items', [])
        self.cov = accum.load_state('coverage.json')
        self.badges = bool(manifest.get('period_report')) or accum.has_earlier_report(self.runs, manifest)
        self.envs = {}
        for src in [s for s, _ in WARN_SOURCES] + ['mafra_notice']:
            self.envs[src] = runview.envelope(rd, src)
        self.final = runview.final_verdicts(self.pre, self.rel)
        self.own = self.watch['own_company']
        self.news = {a['id']: a for a in self.rows_of('bigkinds')}
        self.cidx = {c['cid']: n for n, c in enumerate(self.cl['clusters'])}
        self.cmap = {c['cid']: c for c in self.cl['clusters']}
        self.kinds = {}
        for kind in entities.KINDS:
            for e in self.watch.get(kind) or []:
                self.kinds[e['name']] = kind
        self.ref_info = {}
        for it in self.todo['issues']:
            for f in it['flow_candidates']:
                self.ref_info[f['ref']] = (f['date'], f.get('outlet') or '기사', f['url'], False)
            for a in it['articles']:
                self.ref_info[a['ref']] = (a['date'], a['outlet'], a['url'], True)
        for t in self.todo['market']:
            for a in t['articles']:
                self.ref_info[a['ref']] = (a['date'], a['outlet'], a['url'], True)
        self.is_retro = self._retro()
        self.prev = self._prev()
        self.notice_table = self._notice_rows()

    # ---------------------------------------------------------------- basics
    def basis(self, e):
        m = self.m
        if not m.get('period_report'):
            return ''
        return dates.day_basis(e['text'], [self.ref_info[r][0] for r in e['refs'] if r in self.ref_info], self.end)

    def st(self, src):
        e = self.envs.get(src)
        return e['status'] if e else None

    def rows_of(self, src):
        e = self.envs.get(src)
        return list(e['items']) if e and e['status'] in GOOD else []

    def inc(self, src, iid):
        return self.final.get((src, iid)) == 'include'

    def md(self, iso):
        return dates.short_day(iso, self.end)

    def span(self, a, b):
        return dates.short_span(a, b, self.end)

    def cols(self, sec):
        return [c.replace('{own_company}', self.own) for c in sec['columns']]

    def enabled(self):
        return sorted([s for s in self.fmt['sections'] if s['enabled']], key=lambda s: s['order'])

    def head(self, sec):
        return '#' * sec['heading_level'] + ' ' + sec['title']

    def _retro(self):
        if self.m.get('period_report'):
            return self.m['period_report']['retro']
        seg = self.m['plan_segment']
        p = layout.state_dir() / 'plan.json'
        if seg is None or not p.is_file():
            return False
        return max(s['index'] for s in schema.load(p, 'fiw.plan/1')['segments']) > seg

    def _prev(self):
        p = layout.state_dir() / 'runs.json'
        if self.m.get('period_report') or not p.is_file():
            return None
        runs = schema.load(p, 'fiw.state.runs/1')['runs']
        pos = next((n for n, r in enumerate(runs) if r['run_id'] == self.m['run_id']), None)
        before = runs[:pos] if pos is not None else [r for r in runs if r['created_at'] < self.m['created_at']]
        published = [r for r in before if r['report']]
        return accum.report_link(published[-1]['report']) if published else None

    # ---------------------------------------------------------------- issues
    def cids_of(self, it):
        own = [it['key'][len('new:'):]] if it['key'].startswith('new:') else []
        return own + sorted((c['cid'] for c in self.rel['clusters'] if c['verdict'] == 'issue'
                             and c['section'] != 'market' and c['issue_link'] == it['key']),
                            key=lambda c: self.cidx.get(c, 10 ** 9))

    def core_ids(self, cids):
        ids = {i for c in cids for i in self.cmap.get(c, {}).get('core_ids', [])
           if i in self.news and not from_envelope.is_fallback(i)}
        return sorted(ids, key=lambda i: (self.news[i]['date'], i))

    def stats(self, it):
        core = self.core_ids(self.cids_of(it))
        if not core:
            return 0, 0, None, None
        ds = [self.news[i]['date'] for i in core]
        return len({self.news[i]['outlet'] for i in core}), len(core), min(ds), max(ds)

    def issues_in(self, todo_sec):
        its = [it for it in self.todo['issues'] if it['section'] == todo_sec]

        def order(it):
            n, m, _, _ = self.stats(it)
            return (-n, -m, it['key'])
        return sorted(its, key=order)

    def nodes(self, s):
        return [(nd, self.ref_info[nd['ref']]) for nd in (s.get('flow') or []) if nd['ref'] in self.ref_info]

    def rep_item(self, cid):
        c = self.cmap.get(cid)
        if c is None or c['rep_id'] not in self.news:
            return None
        a = self.news[c['rep_id']]
        t = ' '.join(a['title'].split())
        tail = f' [{a["outlet"]}]({a["url"]})' if a['url'] else f' ({a["outlet"]})'
        return f'- {self.md(a["date"])} {t}{tail}'

    def others(self, sec):
        return sorted((c['cid'] for c in self.rel['clusters'] if c['verdict'] == 'other' and c['section'] == sec),
                      key=lambda c: self.cidx.get(c, 10 ** 9))

    def issue_section(self, sec):
        todo_sec = ISSUE_SECTION_OF[sec['id']]
        allrows = self.issues_in(todo_sec)
        cap = sec['max_rows']
        rows, over = (allrows, []) if cap is None else (allrows[:cap], allrows[cap:])
        by_key = {s['key']: s for s in self.summ['issues']}
        blocks, flow_lines = [], []
        if rows:
            cols = self.cols(sec)
            out = []
            for it in rows:
                s = by_key.get(it['key'], {})
                n, m, a, b = self.stats(it)
                tags = [f'`{n}사{m}건`' if m > n else f'`{n}사`']
                actor_texts = [x['text'] for x in s.get('actors') or []]
                tags += [f'`{x}`' for x in actor_texts]
                ents = []
                for c in self.cids_of(it):
                    for e in self.cmap.get(c, {}).get('entities', []):
                        if self.kinds.get(e) in ('companies', 'associations') and e not in ents and e not in actor_texts:
                            ents.append(e)
                tags += [f'`{e}`' for e in ents[:3]]
                title = (s.get('title') or {}).get('text', it['key'])
                badge = ('🆕 ' if it['key'].startswith('new:') else '▶ ') if self.badges else ''
                nds = self.nodes(s)
                when = f'이번 보도 {self.span(a, b)}' if a else '이번 보도 '
                in_period = accum.period_row(self.m, it['key'])
                if in_period:
                    badge, first = in_period
                    if first and a:
                        when = f'첫 보도 {self.md(first)} · 이번 기간 {self.span(a, b)}'
                if len(nds) >= 2:
                    when += f' · 흐름 **{self.md(min(i[0] for _, i in nds))}~**'
                lwf = s.get('lwf') or {}
                body = ' '.join(x for x in (say(s.get('sentences'), self.basis), say(lwf.get('sentences'), self.basis)) if x)
                cell = {'쟁점': f'{badge}**{title}** ' + ' '.join(tags), '기간': when, '내용': body,
                        self.own: '✅ 직접' if lwf.get('direct') else '—',
                        '링크': ' '.join(lk(first_outlet(it['articles'], u), u) for u in dict.fromkeys(x['url'] for x in it['articles']))}
                out.append([esc(cell.get(c, '')) for c in cols])
                if s.get('flow_name') and len(nds) >= 2:
                    seq = []
                    for nd, (d, outlet, url, _) in nds:
                        txt = f'「{nd["text"]}」' if nd['mode'] == 'title' else nd['text']
                        seq.append(f'{self.md(d)} {txt} {lk(outlet, url)}')
                    flow_lines.append(f'- **{s["flow_name"]["text"]}** — ' + ' → '.join(seq))
            blocks.append(('table', cols, out))
        else:
            blocks.append(('para', '이번 기간 해당 쟁점 없음'))
        extra = [self.rep_item(self.cids_of(it)[0]) for it in over if self.cids_of(it)]
        extra += [self.rep_item(c) for c in self.others(todo_sec)]
        extra = [x for x in extra if x]
        tail = [('details', extra)] if extra else []
        if flow_lines:
            return [(None, blocks), ('### 흐름', [('list', flow_lines)] + tail)]
        return [(None, blocks + tail)]

    # ---------------------------------------------------------------- notices
    @staticmethod
    def _decree(item):
        t = notices.law_type(item['title'])
        if t in ('시행령', '시행규칙'):
            return t
        return {'대통령령': '시행령', '부령': '시행규칙', '총리령': '시행규칙'}.get(item['law_type'] or '')

    def _notice_rows(self):
        found = []
        for n_src, src in enumerate(runview.NOTICE_SOURCES):
            found += [(n_src, it) for it in self.rows_of(src) if self.inc(src, it['id'])]
        return self._rows(found)

    def _carried(self):
        mine = {r['group'] for r in self.notice_table}
        found = [(9, it) for it in self.st_notices if it['open_to'] and it['open_to'] >= self.today
                 and (it['family_key'] if it['family_key'] else it['dup_key']) not in mine]
        return [r for r in self._rows(found) if r['left'] is not None and r['left'] >= 0]

    def _rows(self, found):
        grouped = []
        index = {}
        groups = []
        for n_src, it in found:
            g = it['family_key'] if it['family_key'] else it['dup_key']
            if g not in index:
                index[g] = len(grouped)
                grouped.append([])
                groups.append(g)
            if it['url'] not in [x[2]['url'] for x in grouped[index[g]]]:
                grouped[index[g]].append((n_src, it['id'], it))
        rows = []
        for group, members in zip(groups, grouped):
            pos = {'시행령': 0, '시행규칙': 1}
            members.sort(key=lambda x: (pos.get(self._decree(x[2]), 9), x[0], x[1]))
            its = [x[2] for x in members]
            decrees = [self._decree(i) for i in its]
            fam = len(its) > 1 and {'시행령', '시행규칙'} <= set(decrees)
            lt = []
            for i in its:
                if i['law_type'] and i['law_type'] not in lt:
                    lt.append(i['law_type'])
            lead = its[0]
            left = None if not lead['open_to'] else (iso_day(lead['open_to']) - iso_day(self.today)).days
            base = notices.base_law(lead['title']) + ' 시행령·시행규칙'
            rows.append({'group': group, 'its': its, 'decrees': decrees, 'fam': fam, 'lead': lead, 'left': left,
                         'name': base if fam else lead['title'],
                         'short': base if fam else notices.law_name(lead['title']),
                         'kind': lead['kind'] + ('(' + '·'.join(lt) + ')' if lt else '')})
        opened = [r for r in rows if r['left'] is not None and r['left'] >= 0]
        closed = [r for r in rows if r['left'] is not None and r['left'] < 0]
        unknown = [r for r in rows if r['left'] is None]
        opened.sort(key=lambda r: (r['lead']['open_to'], r['name']))
        closed.sort(key=lambda r: (-iso_day(r['lead']['open_to']).toordinal(), r['name']))
        unknown.sort(key=lambda r: r['name'])
        return opened + closed + unknown

    def open_line(self):
        parts = []
        rows = [r for r in self.notice_table if r['left'] is not None and r['left'] >= 0] + self._carried()
        rows.sort(key=lambda r: (r['lead']['open_to'], r['name']))
        for r in rows:
            day, link = self.md(r['lead']['open_to']), lk('예고', r['its'][0]['url'])
            parts.append(f'{r["short"]} **{day} 마감({r["left"]}일 남음)** {link}' if r['left'] <= 10
                         else f'{r["short"]} {day} 마감 {link}')
        return '> ⏰ **의견 접수 중** — ' + ' · '.join(parts) if parts else None

    def notices_section(self, sec):
        cols = self.cols(sec)
        blocks = []
        if self.notice_table:
            out = []
            for r in self.notice_table:
                lead, d = r['lead'], r['left']
                if r['fam']:
                    links = ' '.join(lk(dc or '예고', i['url']) for i, dc in zip(r['its'], r['decrees']))
                else:
                    links = ' '.join(lk('예고', i['url']) for i in r['its'])
                cell = {'부처': entities.short_name(self.watch, lead['dept'], 'agencies'), '구분': r['kind'],
                        '법령·고시': r['name'],
                        '의견 접수기간': '미확인' if d is None else f'{self.md(lead["open_from"])} ~ {self.md(lead["open_to"])}',
                        '남은 기간': '미확인' if d is None else ('종료' if d < 0 else (f'**{d}일**' if d <= 10 else f'{d}일')),
                        '링크': links}
                out.append([esc(cell.get(c, '')) for c in cols])
            blocks.append(('table', cols, out))
            srcs = {i['open_source'] for r in self.notice_table for i in r['its']}
            names = [('lawmaking:입법의견 접수기간', '국민참여입법센터 「입법의견 접수기간」 칸'),
                     ('ftc:기간', '공정위 게시판 「기간」 칸'), ('mfds:body_regex', '식약처 게시판 본문의 「…까지」 문장')]
            blocks.append(('notes', ['> 의견 접수기간 출처 — ' + ' · '.join(t for s, t in names if s in srcs)
                                     + f'. 「남은 기간」은 {self.md(self.today)} 기준.']))
        else:
            blocks.append(('para', '이번 기간 식품 관련 예고 없음'))
        others, urls = [], set()
        for src in runview.NOTICE_SOURCES:
            for it in self.rows_of(src):
                if self.final.get((src, it['id'])) == 'other' and it['url'] not in urls:
                    urls.add(it['url'])
                    others.append(it)
        others.sort(key=lambda i: (-iso_day(i['posted']).toordinal(), i['title']))
        lines = []
        for it in others:
            per = (f'의견 기간 {self.span(it["open_from"], it["open_to"])}' if it['open_parse'] == 'ok'
                   else '의견 기간 미확인')
            dept = entities.short_name(self.watch, it['dept'], 'agencies')
            lines.append(f'- {" ".join(it["title"].split())} ({dept}, {per}) [예고]({it["url"]})')
        if lines:
            blocks.append(('details', lines))
        return [(None, blocks)]

    # ---------------------------------------------------------------- other sections
    def three_lines(self, sec):
        bullets = []
        for e in self.summ['three_lines']:
            used, links = [], []
            for ref in e['head']['refs'] + [r for s in e['sentences'] for r in s['refs']]:
                info = self.ref_info.get(ref)
                if info and info[3] and ref not in used:
                    used.append(ref)
                    links.append(lk(info[1], info[2]))
            rest = ' '.join(x for x in (say(e['sentences'], self.basis), ' '.join(links)) if x)
            bullets.append(f'- **{e["head"]["text"]}** — {rest}')
        blocks = [('list', bullets)] if bullets else [('para', '이번 기간 해당 쟁점 없음')]
        line = None if self.is_retro else self.open_line()
        if line:
            blocks.append(('notes', [line]))
        return [(None, blocks)]

    def press(self, sec):
        if self.st('korea_kr') not in GOOD:
            return [(None, [('para', UNREAD)])]
        items = self.rows_of('korea_kr')
        depts = self.watch['agencies']
        short = lambda n: entities.short_name(self.watch, n, 'agencies')  # noqa: E731
        intro = []
        for d in depts:
            mine = [i for i in items if i['dept'] == d['name']]
            intro.append(f'**{short(d["name"])}** {len(mine)}건 중 식품 관련 '
                         f'{sum(1 for i in mine if self.inc("korea_kr", i["id"]))}')
        top = [('para', f'기간 {self.span(self.m["period"]["from"], self.end)} · ' + ' · '.join(intro)),
               ('notes', ['> 제목은 정책브리핑 원제목 그대로다. 「식품 관련」은 제목 키워드로 거른 뒤 Claude 가 분류했다(🧭).'])]
        subs = [(None, top)]
        cats = {(r['source'], r['id']): r.get('category') for r in self.rel['items']}
        cat_order = []
        for r in self.rel['items']:
            if r['source'] == 'korea_kr' and r.get('category') and r['category'] not in cat_order:
                cat_order.append(r['category'])
        labels = {n['id']: n['label'] for n in self.summ['notices'] if n['label']}
        cols = self.cols(sec)
        for d in depts:
            mine = [i for i in items if i['dept'] == d['name']]
            inc = [i for i in mine if self.inc('korea_kr', i['id'])]
            blocks = []
            if inc:
                out = []
                present = []
                for i in inc:
                    c = cats.get(('korea_kr', i['id'])) or '미분류'
                    if c not in present:
                        present.append(c)
                present.sort(key=lambda c: cat_order.index(c) if c in cat_order else len(cat_order))
                for c in present:
                    its = sorted((i for i in inc if (cats.get(('korea_kr', i['id'])) or '미분류') == c),
                                 key=lambda i: (-iso_day(i['date']).toordinal(), i['id']))
                    lab = [i for i in its if i['id'] in labels]
                    if len(lab) >= 3:
                        ds = [i['date'] for i in lab]
                        row = {'분류': f'{c} {len(lab)}건', '날짜': self.span(min(ds), max(ds)),
                               '보도자료': ' · '.join(f'{labels[i["id"]]} {lk(self.md(i["date"]), i["url"])}' for i in lab),
                               '링크': '각 날짜 링크'}
                        out.append([esc(row.get(k, '')) for k in cols])
                        its = [i for i in its if i['id'] not in labels]
                    for i in its:
                        row = {'분류': c, '날짜': self.md(i['date']), '보도자료': i['title'], '링크': lk('원문', i['url'])}
                        out.append([esc(row.get(k, '')) for k in cols])
                blocks.append(('table', cols, out))
            else:
                blocks.append(('para', '이번 기간 식품 관련 보도자료 없음'))
            oth = sorted((i for i in mine if self.final.get(('korea_kr', i['id'])) == 'other'),
                         key=lambda i: (-iso_day(i['date']).toordinal(), i['id']))
            if oth:
                blocks.append(('details', [f'- {self.md(i["date"])} {" ".join(i["title"].split())} [원문]({i["url"]})'
                                           for i in oth]))
            subs.append((f'### {d["name"]}', blocks))
        return subs

    def unread(self, src):
        s = self.st(src)
        if s == 'no_key':
            return ('notes', [NO_KEY])
        if s not in GOOD:
            return ('para', UNREAD)
        return None

    def bill_list(self):
        if self.st('assembly_bills') not in GOOD:
            return []
        ok = [b for b in self.rows_of('assembly_bills') if self.inc('assembly_bills', b['bill_id'])
              and (not b['committee'] or entities.committee_alias(self.watch, b['committee']) is not None)]
        return sorted(ok, key=lambda b: (-iso_day(b['date']).toordinal(), b['bill_id']))

    def schedule_list(self):
        if self.st('assembly_schedule') not in GOOD or self.is_retro:
            return []
        return sorted(self.rows_of('assembly_schedule'), key=lambda s: (s['date'], s['time'] or ''))

    def bills(self, sec):
        u = self.unread('assembly_bills')
        if u:
            return [(None, [u])]
        rows = self.bill_list()
        if not rows:
            return [(None, [('para', '기간 내 식품 관련 발의 없음(읽음)')])]
        cols = self.cols(sec)
        out = []
        for b in rows:
            ca = entities.committee_alias(self.watch, b['committee'])
            stage = b['stage'] if b['stage'] else ('회부' if b['committee'] else '접수')
            row = {'발의일': self.md(b['date']), '법안': b['name'], '대표발의': b['proposer'].split('의원', 1)[0].strip(),
                   '소관위': ca or '미배정', '단계': stage, '링크': lk('의안', b['url'])}
            out.append([esc(row.get(k, '')) for k in cols])
        return [(None, [('table', cols, out)])]

    def schedule(self, sec):
        u = self.unread('assembly_schedule')
        if u:
            return [(None, [u])]
        if self.is_retro:
            return [(None, [('notes', [RETRO])])]
        rows = self.schedule_list()
        if not rows:
            return [(None, [('para', '등록된 일정 없음(읽음)')])]
        cols = self.cols(sec)
        out = []
        for s in rows:
            row = {'날짜': self.md(s['date']) + (' ' + s['time'] if s['time'] else ''),
                   '위원회': entities.committee_alias(self.watch, s['committee']) or s['committee'],
                   '회의': s['meeting'], '링크': lk('일정', s['url'])}
            out.append([esc(row.get(k, '')) for k in cols])
        return [(None, [('table', cols, out)])]

    def market(self, sec):
        cols = self.cols(sec)
        by_topic = {x['topic']: x for x in self.summ['market']}
        blocks = []
        if self.todo['market']:
            out = []
            for t in self.todo['market']:
                cids = [c['cid'] for c in self.rel['clusters'] if c['verdict'] == 'issue'
                        and c['section'] == 'market' and c['market_topic'] == t['topic']]
                s = by_topic.get(t['topic'], {})
                lwf = s.get('lwf') or {}
                row = {'주제': f'**{t["topic"]}**', '건수': f'{len(self.core_ids(cids))}건 🧭',
                       '내용': ' '.join(x for x in (say(s.get('sentences'), self.basis),
                                                 say(lwf.get('sentences'), self.basis)) if x),
                       self.own: '✅ 직접' if lwf.get('direct') else '—',
                       '링크': ' '.join(lk(first_outlet(t['articles'], u), u) for u in dict.fromkeys(a['url'] for a in t['articles']))}
                out.append([esc(row.get(k, '')) for k in cols])
            blocks.append(('table', cols, out))
        else:
            blocks.append(('para', '이번 기간 해당 없음'))
        blocks.append(('notes', ['> 대관과 직접 닿는 건은 위 업계 표에 있다. 이 표는 맥락용이다.']))
        extra = [x for x in (self.rep_item(c) for c in self.others('market')) if x]
        if extra:
            blocks.append(('details', extra))
        return [(None, blocks)]

    def hygiene_text(self, kind, its):
        if kind == 'recall':
            g = [i['grade'] for i in its]
            s = f'1등급 {g.count("1등급")} · 2등급 {g.count("2등급")} · 3등급 {g.count("3등급")}'
            other = len([x for x in g if x not in ('1등급', '2등급', '3등급')])
            if other > 0:
                s += f' · 등급 미확인 {other}'
            parts = [s]
            t = ranked([i['food_type'] for i in its], 2)
            if t:
                parts.append('유형 상위: ' + ', '.join(t))
        elif kind == 'admin':
            parts = []
            a = ranked([re.sub(r'(?:\s*\(.*\)|\s*\d+\s*(?:일|개월))+\s*$', '', i['action'])
                        if i['action'] is not None else None
                        for i in its], 4)
            if a:
                parts.append(' · '.join(a))
            law = ranked([i['law'] for i in its], 1)
            if law:
                parts.append('위반 상위: ' + law[0])
        else:
            parts = []
            r = ranked([i['reason'] for i in its], 3)
            if r:
                parts.append('항목 상위: ' + ' · '.join(r))
            t = ranked([i['food_type'] for i in its], 2)
            if t:
                parts.append('품목 상위: ' + ' · '.join(t))
        return '. '.join(parts)

    def hygiene_counts(self):
        rows = self.rows_of('foodsafety')
        return {k: [i for i in rows if i['kind'] == k] for k in ('recall', 'admin', 'unfit')}

    def hygiene(self, sec):
        if self.st('foodsafety') not in GOOD:
            return [(None, [('para', UNREAD)])]
        cols = self.cols(sec)
        by = self.hygiene_counts()
        out = []
        for kind in ('recall', 'admin', 'unfit'):
            name, url = FS_ROWS[kind]
            its = by[kind]
            row = {'구분': name, '건수': str(len(its)),
                   '내역': self.hygiene_text(kind, its) if its else '이번 기간 해당 없음', '링크': lk('목록', url)}
            out.append([esc(row.get(k, '')) for k in cols])
        blocks = [('table', cols, out)]
        ones = sorted((i for i in by['recall'] if i['grade'] == '1등급' and not i['personal']),
                      key=lambda i: (-iso_day(i['date']).toordinal(), i['seq']))[:3]
        if ones:
            blocks.append(('para', '**회수 1등급 주요 건**: ' + ' · '.join(
                (i['product'] or i['company']) + (f'({i["reason"]})' if i['reason'] else '') + ' ' + lk('회수', i['url'])
                for i in ones)))
        order = ('recall', 'admin', 'unfit')
        names = {'recall': ('회수', '회수'), 'admin': ('행정처분', '처분'), 'unfit': ('검사부적합', '부적합')}
        tracked = sorted((i for i in self.rows_of('foodsafety') if i['tracked_company'] and not i['personal']),
                         key=lambda i: (-iso_day(i['date']).toordinal(), order.index(i['kind']), i['seq']))
        foot = ['> 행정처분·부적합은 식품안전나라 누리집 공개 목록 기준 — 업종 구분 없음, Open API 집계와 건수가 다를 수 있음.',
                '> 개인 농가 등 마스킹된 실명은 싣지 않는다.']
        if tracked:
            blocks.append(('para', '**추적 회사 해당**: ' + ' · '.join(
                f'{i["tracked_company"]} {names[i["kind"]][0]} {lk(names[i["kind"]][1], i["url"])}' for i in tracked)))
            blocks.append(('notes', foot))
        else:
            blocks.append(('notes', ['> 추적 회사는 이 기간 세 데이터 모두에 없었다.'] + foot))
        return [(None, blocks)]

    # ---------------------------------------------------------------- top / tail
    def header(self):
        bk = self.rows_of('bigkinds')
        q = len(self.envs['bigkinds']['requests']) if self.st('bigkinds') in GOOD else 0
        kr = self.rows_of('korea_kr')
        h = self.hygiene_counts()
        return (f'{from_envelope.news_header(bk, q, self.fallback())} → '
                f'묶음 {self.cl["rule_stage"]["clusters"]}개 → 대관 쟁점 {len(self.todo["issues"])}개 · '
                f'부처 보도자료 {len(kr)}건(식품 관련 {len([i for i in kr if self.inc("korea_kr", i["id"])])}) · '
                f'입법·행정예고 {len(self.notice_table)}건 · 법안 {len(self.bill_list())}건 · '
                f'위원회 일정 {len(self.schedule_list())}건 · 시장·경영 {len(self.todo["market"])}개 주제 · '
                f'위생 동향(회수 {len(h["recall"])} · 행정처분 {len(h["admin"])} · 부적합 {len(h["unfit"])})')

    def top_notes(self):
        lines = [EDIT_NOTE]
        if self.prev:
            lines.append(f'> 지난 보고서: [{self.prev}]({self.prev})')
        if self.is_retro:
            lines.append(RETRO)
        if accum.uncollected_note(self.m):
            lines.append(accum.uncollected_note(self.m))
        for src, label in WARN_SOURCES:
            e = self.envs.get(src)
            if e is None:
                lines.append(f'> ⚠ 수집 실패 — {label}: 수집 파일 없음')
            elif e['status'] == 'failed':
                lines.append(f'> ⚠ 수집 실패 — {label}: ' + ' '.join((e['error'] or '원인 미기록').split()))
            elif e['status'] == 'no_key':
                lines.append(f'> ⚠ 수집 실패 — {label}: 키 없음')
            elif e['status'] == 'partial':
                bad = [r for r in e['requests'] if r['status'] != 'ok']
                if src == 'bigkinds':
                    if self.fallback():
                        lines.append(from_envelope.fallback_note(self.fallback()))
                        bad = [r for r in bad if r['target'] != from_envelope.FALLBACK_SOURCE]
                    if bad:
                        lines.append('> ⚠ 일부 검색어 실패: ' + ', '.join(r.get('query') or r['target'] for r in bad))
                else:
                    lines.append(f'> ⚠ 일부 수집 실패 — {label}: ' + ', '.join(r['target'] for r in bad))
        return lines

    def tail_notes(self):
        out = []
        for key, word in (('excluded_no_link', '링크 없어 제외'), ('excluded_no_body', '근거 본문 미수신')):
            xs = self.todo.get(key) or []
            if xs:
                out.append(f'> {word} {len(xs)}건: ' + ' · '.join(x['title'] + '(' + '·'.join(x['outlets']) + ')'
                                                               for x in xs))
        for row in self.todo.get('fallback_rows') or []:
            out += [from_envelope.fallback_line(self.md(a['date']), a['title'], lk(a['outlet'] or '기사', a['url']))
                    for a in row['articles']]
        return out

    def fallback(self):
        return (self.envs.get('bigkinds') or {}).get('fallback')

    def accumulated(self):
        """(carried section or None, dormant (N, lines) or None) — addendum §D."""
        carried, dormant = accum.projected(self.m, self.todo, self.runs, self.cov, self.envs.get('bigkinds'),
                                           self.st_issues, self.fmt['dormancy_weeks'])
        sec = None
        if carried:
            out = []
            for i in carried:
                x = f'- {i["title"]} (마지막 기사 {self.md(i.get("last_article") or i["first_seen"])})'
                if i['flow']:
                    nd = sorted(i['flow'], key=lambda n: (n['date'], n['article_id']))[-1]
                    x += ' ' + lk(nd.get('outlet') or '기사', nd['url'])
                out.append(x)
            sec = {'id': 'carried', 'heading': accum.carried_heading(self.m), 'body': [(None, [('list', out)])]}
        dz = None
        if dormant:
            dz = (len(dormant), [f'- {i["title"]} (마지막 기사 {self.md(i.get("last_article") or i["first_seen"])})'
                                 for i in dormant])
        return sec, dz

    def build(self):
        sections = []
        for sec in self.enabled():
            sid = sec['id']
            if sid == 'three_lines':
                body = self.three_lines(sec)
            elif sid in ISSUE_SECTION_OF:
                body = self.issue_section(sec)
            elif sid == 'press':
                body = self.press(sec)
            elif sid == 'notices':
                body = self.notices_section(sec)
            elif sid == 'assembly_bills':
                body = self.bills(sec)
            elif sid == 'assembly_schedule':
                body = self.schedule(sec)
            elif sid == 'market':
                body = self.market(sec)
            elif sid == 'hygiene':
                body = self.hygiene(sec)
            else:
                body = [(None, [])]
            sections.append({'id': sid, 'heading': self.head(sec), 'body': body})
        carried, dormant = self.accumulated()
        if carried:
            sections.append(carried)
        return {'title': f'# 식품업계 대관 현황판 — {self.m["period"]["from"]} ~ {self.end}',
                'header': self.header(), 'notes': self.top_notes(), 'sections': sections,
                'tail': self.tail_notes(), 'dormant': dormant,
                'credit': from_envelope.credit(CREDIT, self.rows_of('bigkinds'), self.fallback())}
