"""Checks of the evidence gate E (fiw.py verify): E0–E17 and the structure check over a todo + summary pair.

Finding kinds are the check ids. `where` paths follow m4-m5-addendum §4:
three_lines[i]/head · three_lines[i]/sentences[j] · {key}/title · {key}/actors[j] ·
{key}/sentences[j] · {key}/flow_name · {key}/flow[j] · {key}/lwf/sentences[j] ·
market:{topic}/sentences[j] · market:{topic}/lwf/sentences[j] · notices/{id}/label ·
notice/{source}/{id} (E11).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from lib import dates, entities, evidence, korean, runview

# the completion words (finished / wrapped up / completed) (a stage-limited completion written as whole) and the joint words (together / jointly) (a joint act the source does not
# state) were added after the M8 run 1 review. "together" is also an ordinary adverb ("cost and margin must be looked at together"),
# so it only needs to appear in the sentence's sources, not in an evidence quote.
JUDGMENT_WORDS = ('이어지', '이어졌', '연속', '잇따', '계기', '때문', '최대', '최초', '처음', '가장',
                  '마쳤', '마무리', '완료', '공동')
SOURCE_WORDS = ('함께',)
LIMITING_RES = tuple(re.compile(p) for p in (
    r'같은\s*기간', r'최근\s*\d+\s*년', r'올해', r'지난해', r'\d+\s*월\s*부터', r'이후'))
MODIFIER_RES = tuple(re.compile(p) for p in (r'한\s*차례', r'처음', r'모두', r'가장', r'최대'))
SPEECH_VERBS = ('주장했다', '밝혔다', '말했다', '요구했다', '지적했다', '강조했다')
ASSERTIVE_WORDS = ('안 돼', '안돼', '불가', '금지')
# The adjective "typical" form is not the title "representative".
PERSON_RE = re.compile(r'([가-힣]{2,4})\s*(의원|장관|위원장|처장|차관|회장|대표(?!적))')
HANGUL_TOKEN_RE = re.compile(r'[가-힣]{2,}')
NUMBER_RE = re.compile(r'\d+(?:,\d+)*(?:\.\d+)?')       # same tokens as evidence.number_tokens
LABEL_SPLIT_RE = re.compile(r'[\s()·,/]+')
QUOTE_BUDGET = 40
_E11_DATE_RE = re.compile(
    r'(\d{4})-(\d{2})-(\d{2})'
    r'|(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?'
    r'|(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')
PERIOD_SOURCES = ('lawmaking:입법의견 접수기간', 'ftc:기간')
# E17: the "coming [YYYY] [M] D" pattern — an article's "upcoming" date, relative to the article's own date. Not inside
# a word (see the false-match examples in korean-text.md).
UPCOMING_RE = re.compile(r'(?<![가-힣])오는\s*(?:(\d{4})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일')


def _no_space(s) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or ''))


def written_dates(text) -> list:
    """ISO dates written in text (YYYY-MM-DD, YYYY. M. D., YYYY.M.D, Korean year/month/day), in order."""
    out = []
    for m in _E11_DATE_RE.finditer(text or ''):
        g = [x for x in m.groups() if x is not None]
        try:
            out.append(date(int(g[0]), int(g[1]), int(g[2])).isoformat())
        except ValueError:
            continue
    return out


def reparse_notice(item):
    """None when open_evidence re-parses to the recorded period, else the reason."""
    ev = item.get('open_evidence') or ''
    if not ev.strip():
        return 'open_evidence is empty'
    src, frm, to = item.get('open_source'), item.get('open_from'), item.get('open_to')
    found = written_dates(ev)
    until = dates.find_until_date(ev)
    until_iso = until[0].isoformat() if until else None
    period_ok = len(found) >= 2 and found[0] == frm and found[1] == to
    until_ok = until_iso is not None and until_iso == to
    if src == 'mfds:body_regex':
        ok = until_ok
    elif src in PERIOD_SOURCES:
        ok = period_ok
    elif src == 'quote':
        ok = period_ok or until_ok
    else:
        return f'unknown open_source {src!r}'
    if ok:
        return None
    parsed = f'dates {"~".join(found[:2]) or "none"}, 까지 {until_iso or "none"}'
    return f'{src}: open_evidence gives {parsed}; recorded {frm}~{to}'


def upcoming_date(m, base):
    """The date an UPCOMING_RE match means when written on base: a missing year or month is the first one at
    or after base ("coming March 1" written in September is next year). None for an impossible date."""
    y, mo, d = (int(x) if x else None for x in m.groups())
    try:
        if y is not None:
            return date(y, mo or base.month, d)
        if mo is not None:
            out = date(base.year, mo, d)
            return out if out >= base else date(base.year + 1, mo, d)
        out = date(base.year, base.month, d)
        if out >= base:
            return out
        return date(base.year + (base.month == 12), base.month % 12 + 1, d)
    except ValueError:
        return None


def _iso(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _strip_quotes(text) -> str:
    return evidence._QUOTE_RE.sub(' ', text or '')


class Verifier:
    def __init__(self, todo, summary, watch, fmt, envelopes, prefilter, relevance, dead_urls=(), period_to=None):
        self.todo, self.summary, self.watch = todo, summary, watch
        self.dead_urls = set(dead_urls)     # work/dead_links.json: pages the source took down (linkcheck dead)
        self.period_to = _iso(period_to)    # manifest period.to; None skips E17
        self.ref_url = {}                   # ref -> URL of its article or flow candidate
        self.limit = fmt['sentence_limit']
        self.envelopes = envelopes
        self.verdicts = runview.final_verdicts(prefilter, relevance)
        self.own_company = watch['own_company']
        self.lwf_mentions = todo['lwf_mentions']
        self.entities = entities.all_entities(watch)
        self.entity_by_name = {e['name']: e for e in self.entities}
        self.committee_names = {n for e in watch.get('committees') or [] for n in entities.names(e)}
        self.findings = []
        self.checked = 0
        self.notices_checked = 0      # notice items whose comment period E11 re-parsed

        self.refs = {}             # ref -> {title, body, text, outlet, conflicts, date}
        self.issue_refs = {}       # todo issue key -> set of refs (articles + flow candidates)
        self.issue_articles = {}   # todo issue key -> article refs
        for iss in todo['issues']:
            arts = [self._add_article(a) for a in iss['articles']]
            flows = []
            for f in iss['flow_candidates']:
                self.refs[f['ref']] = {'title': f['title'], 'body': f['excerpt'],
                                       'text': f['title'] + '\n' + f['excerpt'],
                                       'outlet': f.get('outlet'), 'conflicts': [], 'date': _iso(f.get('date'))}
                self.ref_url[f['ref']] = f.get('url')
                flows.append(f['ref'])
            self.issue_refs.setdefault(iss['key'], set()).update(arts + flows)
            self.issue_articles.setdefault(iss['key'], []).extend(arts)
        self.renames = {iss['key']: iss['locks']['rename'] for iss in todo['issues'] if iss['locks']['rename']}
        self.topic_refs = {}
        for m in todo['market']:
            arts = [self._add_article(a) for a in m['articles']]
            self.topic_refs.setdefault(m['topic'], set()).update(arts)

        outlets = {r['outlet'] for r in self.refs.values() if r['outlet']}
        bk = envelopes.get('bigkinds')
        if bk is not None:
            outlets |= {it['outlet'] for it in bk['items'] if it.get('outlet')}
        self.outlets = sorted(outlets, key=lambda o: (-len(o), o))

    def _add_article(self, a):
        self.refs[a['ref']] = {'title': a['title'], 'body': a['body'], 'text': a['title'] + '\n' + a['body'],
                               'outlet': a['outlet'], 'conflicts': list(a['title_body_conflicts']),
                               'date': _iso(a.get('date'))}
        self.ref_url[a['ref']] = a.get('url')
        return a['ref']

    # ------------------------------------------------------------------ helpers
    def find(self, where, kind, detail):
        f = {'where': where, 'kind': kind, 'detail': str(detail)}
        if f not in self.findings:
            self.findings.append(f)

    def _valid(self, where, refs, allowed) -> list:
        out = []
        for r in refs:
            if r in allowed:
                out.append(r)
            elif r in self.refs:
                self.find(where, '구조', f'ref {r} belongs to another issue or topic')
            else:
                self.find(where, '구조', f'unknown ref {r}')
        return out

    def dead(self, where, refs):
        """E16: a cited ref whose page linkcheck found deleted — render drops its link, so nothing may rest on it."""
        for r in refs:
            url = self.ref_url.get(r)
            if url and url in self.dead_urls:
                self.find(where, 'E16', f'cites a source whose page was deleted (linkcheck dead): {r}')

    def _links(self, where, links) -> set:
        if not links:
            self.find(where, '구조', 'links is empty')
        out = set()
        for k in links or []:
            if k in self.issue_refs:
                out |= self.issue_refs[k]
            else:
                self.find(where, '구조', f'link {k} is not a todo issue key')
        return out

    def _entity_in(self, text, e) -> bool:
        """e is mentioned in text; an entity with requires_any needs its companion term too."""
        if not entities.mentioned(text, e):
            return False
        return not e.get('requires_any') or korean.match_entity(text, e)

    # ------------------------------------------------------------------ E1–E6, E17
    def text_checks(self, where, text, src_refs, actor=False):
        infos = [self.refs[r] for r in src_refs if r in self.refs]
        sources = [i['text'] for i in infos]
        norm_sources = [evidence.norm_space(s) for s in sources]
        norm_bodies = [evidence.norm_space(i['body']) for i in infos]

        missing = evidence.missing_numbers(text, sources)
        if missing:
            self.find(where, 'E1', ', '.join(missing))
        for d in evidence.missing_dates(text, sources):
            self.find(where, 'E2', d)
        for q in evidence.missing_quotes(text, sources):
            self.find(where, 'E3', f'quote not in sources: {q}')

        ref_outlets = {i['outlet'] for i in infos if i['outlet']}
        work = text
        for o in self.outlets:
            if korean.match_name(work, o):
                if o not in ref_outlets:
                    self.find(where, 'E4', o)
                work = work.replace(o, ' ' * len(o))     # a shorter outlet inside it is not a hit

        conflicts = set()
        for i in infos:
            conflicts.update(i['conflicts'])
        hit = sorted(evidence.number_tokens(text) & conflicts)
        if hit and '제목은' not in text:
            self.find(where, 'E5', ', '.join(hit))
        # Seen 2026-10-26 (M8 run 3 review): "…, the article title is two or more within 5 years" — a note that gets past the rule
        # above without saying the body differs.
        if '제목은' in text and '본문' not in text:
            self.find(where, 'E5', 'title note without 본문 — write 「제목은 X로 적었으나 본문은 Y」, '
                                   'or leave the title out')

        for e in self.entities:
            if self._entity_in(text, e) and not any(
                    n in s for s in norm_sources for n in entities.names(e)):
                self.find(where, 'E6', e['name'])
        tracked_names = {n for e in self.entities for n in entities.names(e)}
        for m in PERSON_RE.finditer(text):
            # "committee name + member": a tracked name before the title is checked by the entity rule above
            # (any of its names counts), not as a person name.
            if m.group(1) in tracked_names:
                continue
            if not any(m.group(1) in s for s in norm_sources):
                self.find(where, 'E6', f'{m.group(1)} {m.group(2)}')
            elif not any(m.group(1) in s for s in norm_bodies):
                # Seen 2026-10-26 (M8 run 3 review): a headline misspelt a member name with two syllables swapped; the body had only surname + title.
                self.find(where, 'E6', f'{m.group(1)} {m.group(2)} is only in a headline — headlines shorten or '
                                       f'mistype names; write the body\'s form (e.g. 「윤 의원」) or cite a ref '
                                       f'whose body names them')
        if actor:
            for tok in HANGUL_TOKEN_RE.findall(text):
                if tok not in self.committee_names and not any(tok in s for s in norm_sources):
                    self.find(where, 'E6', tok)
        self.e17(where, text, infos)

    def e17(self, where, text, infos):
        """A "coming M D" date already past when the report ends: right on the article's day, wrong in the report."""
        # Seen 2026-10-26 (M8 run 3 review): "from the coming October 19" in a 09-29 article in a report up to 10-26.
        if self.period_to is None:
            return
        days = [i['date'] for i in infos if i['date']]
        if not days:
            return
        base = max(days)
        for m in UPCOMING_RE.finditer(text):
            when = upcoming_date(m, base)
            if when is not None and when <= self.period_to:
                self.find(where, 'E17', f'「{m.group(0)}」 = {when.isoformat()}, on or before the period end '
                                        f'{self.period_to.isoformat()} — 「오는」 reads as still to come; drop 「오는」 and '
                                        f'keep the date as the source writes it (writing-rules 14)')

    # ------------------------------------------------------------------ fields
    def field(self, where, fld, own, actor=False):
        self.checked += 1
        if not fld['refs']:
            self.find(where, '구조', 'no refs')
        valid = self._valid(where, fld['refs'], own)
        self.dead(where, valid)
        self.text_checks(where, fld['text'], valid, actor=actor)

    def sentence(self, where, s, own):
        self.checked += 1
        text, refs = s['text'], s['refs']
        if not refs:
            self.find(where, '구조', 'no refs')
        if s['kind'] == 'edit':
            linked = self._links(where, s.get('links'))
            if len(refs) < 2:
                self.find(where, '구조', 'edit sentence needs at least 2 refs')
            valid = self._valid(where, refs, own | linked)
            self.dead(where, valid)
            sources = sorted(set(valid) | linked)
        else:
            sources = self._valid(where, refs, own)
            self.dead(where, sources)
        self.text_checks(where, text, sources)
        quotes = self.evidence_quotes(where, s)
        if s['kind'] == 'fact':
            self.e7(where, text, quotes, sources)
        self.e12(where, text, quotes)
        self.e13(where, text, quotes)

    def evidence_quotes(self, where, s) -> list:
        quotes = []
        for k, ev in enumerate(s.get('evidence') or []):
            ref, quote = ev['ref'], ev['quote']
            if ref not in s['refs']:
                detail = f'evidence[{k}] ref {ref} is not a ref of this sentence'
                self.find(where, '구조', detail)
                self.find(where, 'E0', detail)
            info = self.refs.get(ref)
            ok, reason = evidence.fragment_in(quote, [info['text']]) if info else (False, 'unknown_ref')
            if not ok:
                self.find(where, 'E0', f'evidence[{k}] {reason}: {quote}')
            quotes.append(quote)
        return quotes

    # ------------------------------------------------------------------ E7 E12 E13
    def e7(self, where, text, quotes, sources):
        nq = [evidence.norm_space(q) for q in quotes]
        for w in JUDGMENT_WORDS:
            if w in text and not any(w in q for q in nq):
                self.find(where, 'E7', w)
        ns = nq + [evidence.norm_space(self.refs[r]['text']) for r in sources if r in self.refs]
        for w in SOURCE_WORDS:
            if w in text and not any(w in t for t in ns):
                self.find(where, 'E7', w)

    def e12(self, where, text, quotes):
        t = unicodedata.normalize('NFKC', text)
        if len(evidence.number_tokens(t)) < 2:
            return
        hits = [h for h in entities.mentions(t, self.entities)
                if self._entity_in(t, self.entity_by_name[h[2]])]
        if len({h[2] for h in hits}) < 2:
            return
        nums = [(m.start(), m.group(0).replace(',', '')) for m in NUMBER_RE.finditer(t)]
        qs = [(evidence.norm_space(q), evidence.number_tokens(q)) for q in quotes]
        for i, (start, end, name, written) in enumerate(hits):
            stop = hits[i + 1][0] if i + 1 < len(hits) else len(t)
            num = next((tok for pos, tok in nums if end <= pos < stop), None)
            if num is None:
                continue
            forms = set(entities.names(self.entity_by_name[name])) | {written}
            if not any(num in toks and any(f in q for f in forms) for q, toks in qs):
                self.find(where, 'E12', f'{written} – {num}: no evidence quote with both')

    def e13(self, where, text, quotes):
        nq = [_no_space(q) for q in quotes]
        seen = set()
        for rx in LIMITING_RES + MODIFIER_RES:
            for m in rx.finditer(text):
                key = _no_space(m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                if not any(key in q for q in nq):
                    self.find(where, 'E13', f'{m.group(0)}: no evidence quote contains it')
        if any(v in text for v in SPEECH_VERBS) and not text.lstrip().startswith('기사는') \
                and not evidence.find_quotes(text):
            verb = next(v for v in SPEECH_VERBS if v in text)
            self.find(where, 'E13', f'{verb}: needs a direct quote or the subject 기사는')

    # ------------------------------------------------------------------ E8 E9 E10
    def flow_node(self, where, node, own):
        self.checked += 1
        ref = node['ref']
        if ref not in own:
            self._valid(where, [ref], own)
            return
        self.dead(where, [ref])
        info = self.refs[ref]
        text = node['text']
        if node['mode'] == 'title':
            want = korean.strip_title_prefix(info['title'])
            if evidence.norm_space(text) != evidence.norm_space(want):
                self.find(where, 'E8', f'title mode text differs from the title of {ref}')
            return
        sources = [info['text']]
        missing = evidence.missing_numbers(text, sources)
        if missing:
            self.find(where, 'E1', ', '.join(missing))
        for d in evidence.missing_dates(text, sources):
            self.find(where, 'E2', d)
        for q in evidence.missing_quotes(text, sources):
            self.find(where, 'E3', f'quote not in sources: {q}')
        outside = _strip_quotes(text)
        for w in ASSERTIVE_WORDS:
            if w in outside:
                self.find(where, 'E8', f'assertive {w} outside quotes')

    def lwf_checks(self, base, lwf, own, article_refs):
        """Sentences of lwf (checked as narrative fields) and E9 both ways."""
        if lwf:
            for j, s in enumerate(lwf.get('sentences') or []):
                self.sentence(f'{base}/lwf/sentences[{j}]', s, own)  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
            if lwf.get('ref') is not None:
                self.dead(f'{base}/lwf', self._valid(f'{base}/lwf', [lwf['ref']], own))  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        if lwf and lwf['direct']:
            cands = [lwf['ref']] if lwf.get('ref') else []
            for s in lwf.get('sentences') or []:
                cands.extend(s['refs'])
            if not any(self.lwf_mentions.get(r) for r in cands):
                self.find(f'{base}/lwf', 'E9', f'direct is true but no ref names {self.own_company}')  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
        if any(self.lwf_mentions.get(r) for r in article_refs):
            shown = lwf is not None and (lwf['direct'] or (lwf.get('not_shown_reason') or '').strip())
            if not shown:
                self.find(f'{base}/lwf', 'E9',  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
                          f'a ref names {self.own_company} but direct is false without not_shown_reason')

    def e10(self, where, sentences):
        lim = self.limit
        n = len(sentences)
        chars = len(' '.join(s['text'] for s in sentences))
        if not (lim['min_sentences'] <= n <= lim['max_sentences']
                and lim['min_chars'] <= chars <= lim['max_chars']):
            self.find(where, 'E10', f'{n} sentences, {chars} chars (allowed {lim["min_sentences"]}–'
                                    f'{lim["max_sentences"]} sentences, {lim["min_chars"]}–{lim["max_chars"]} chars)')

    # ------------------------------------------------------------------ run
    def structure(self):
        todo_keys = [i['key'] for i in self.todo['issues']]
        sum_keys = [i['key'] for i in self.summary['issues']]
        self._both_ways(todo_keys, sum_keys, lambda k: k, 'todo issue')
        todo_topics = [m['topic'] for m in self.todo['market']]
        sum_topics = [m['topic'] for m in self.summary['market']]
        self._both_ways(todo_topics, sum_topics, lambda t: f'market:{t}', 'todo market topic')

        lines = self.summary['three_lines']
        want = min(3, len(todo_keys))
        if len(lines) != want:
            self.find('three_lines', '구조', f'{len(lines)} entries, expected {want}')
        seen = set()
        for i, tl in enumerate(lines):
            if tl['issue'] not in todo_keys:
                self.find(f'three_lines[{i}]', '구조', f'issue {tl["issue"]} is not a todo issue key')
            if tl['issue'] in seen:
                self.find(f'three_lines[{i}]', '구조', f'issue {tl["issue"]} repeated')
            seen.add(tl['issue'])

    def _both_ways(self, todo_list, sum_list, where, label):
        seen = set()
        for k in sum_list:
            if k in seen:
                self.find(where(k), '구조', 'duplicate summary entry')
            seen.add(k)
            if k not in todo_list:
                self.find(where(k), '구조', f'not a {label}')
        for k in todo_list:
            if k not in seen:
                self.find(where(k), '구조', f'{label} has no summary entry')

    def run(self):
        self.structure()
        for i, tl in enumerate(self.summary['three_lines']):
            own = self.issue_refs.get(tl['issue'], set())
            self.field(f'three_lines[{i}]/head', tl['head'], own)
            for j, s in enumerate(tl['sentences']):
                self.sentence(f'three_lines[{i}]/sentences[{j}]', s, own)
        for iss in self.summary['issues']:
            self.issue(iss)
        for m in self.summary['market']:
            base = f'market:{m["topic"]}'
            own = self.topic_refs.get(m['topic'], set())
            for j, s in enumerate(m['sentences']):
                self.sentence(f'{base}/sentences[{j}]', s, own)  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
            self.lwf_checks(base, m.get('lwf'), own, sorted(own))
        self.notices()
        self.e11()
        self.e15()

    def e15(self):
        """Every todo body is whole: no cut-looking end, and the length prepare recorded (M8 0-C j)."""
        for a in [a for i in self.todo['issues'] for a in i['articles']] + \
                 [a for m in self.todo['market'] for a in m['articles']]:
            check = a.get('body_check')
            why = evidence.cut_suspect(a['body'], check['src_chars'] if check else None)
            if why is None and check and check['chars'] != len(a['body']):
                why = f'{len(a["body"])} chars, prepare recorded {check["chars"]}'
            if why:
                self.find(a['ref'], 'E15', f'body looks cut: {why}')

    def run_entry(self, kind, name):
        """The checks of one summary entry only — a correction after prune (m6-m7-addendum §E)."""
        if kind == 'issue':
            self.issue(next(i for i in self.summary['issues'] if i['key'] == name))
        elif kind == 'market':
            m = next(x for x in self.summary['market'] if x['topic'] == name)
            base, own = f'market:{name}', self.topic_refs.get(name, set())
            for j, s in enumerate(m['sentences']):
                self.sentence(f'{base}/sentences[{j}]', s, own)  # gr-compat: ignore=pathjoin (a finding location label, not a file path)
            self.lwf_checks(base, m.get('lwf'), own, sorted(own))
        else:
            i = int(name)
            tl = self.summary['three_lines'][i]
            own = self.issue_refs.get(tl['issue'], set())
            self.field(f'three_lines[{i}]/head', tl['head'], own)
            for j, s in enumerate(tl['sentences']):
                self.sentence(f'three_lines[{i}]/sentences[{j}]', s, own)

    def issue(self, iss):
        key = iss['key']
        own = self.issue_refs.get(key, set())
        texts = [iss['title']['text']]
        self.field(f'{key}/title', iss['title'], own)
        rename = self.renames.get(key)
        if rename is not None:
            self.checked += 1
            if iss['title']['text'] != rename:
                self.find(f'{key}/title', 'lock', f'the user fixed this title as 「{rename}」 (rename lock)')
        for j, a in enumerate(iss.get('actors') or []):
            self.field(f'{key}/actors[{j}]', a, own, actor=True)
            texts.append(a['text'])
        for j, s in enumerate(iss['sentences']):
            self.sentence(f'{key}/sentences[{j}]', s, own)
            texts.append(s['text'])
        self.e10(f'{key}/sentences', iss['sentences'])
        fn = iss.get('flow_name')
        if fn is not None:
            self.checked += 1
            where = f'{key}/flow_name'
            linked = self._links(where, fn['links'])
            self.text_checks(where, fn['text'], sorted(own | linked))
            texts.append(fn['text'])
        for j, node in enumerate(iss.get('flow') or []):
            self.flow_node(f'{key}/flow[{j}]', node, own)
            texts.append(node['text'])
        lwf = iss.get('lwf')
        for s in (lwf or {}).get('sentences') or []:
            texts.append(s['text'])
        self.lwf_checks(key, lwf, own, self.issue_articles.get(key, []))
        total = sum(evidence.quote_chars(t) for t in texts)
        if total > QUOTE_BUDGET:
            self.find(key, 'E3', f'{total} quoted characters in the issue (max {QUOTE_BUDGET})')

    def notices(self):
        env = self.envelopes.get('korea_kr')
        items = {it['id']: it for it in env['items']} if env is not None else {}
        for n in self.summary['notices']:
            nid = n['id']
            item = items.get(nid)
            if item is None:
                self.find(f'notices/{nid}', '구조', 'not a korea_kr item of this run')
            else:
                verdict = self.verdicts.get(('korea_kr', nid))
                if verdict != 'include':
                    self.find(f'notices/{nid}', '구조', f'final verdict is {verdict}, not include')
            if n['label'] is None:
                continue
            self.checked += 1
            where = f'notices/{nid}/label'
            pieces = [p for p in LABEL_SPLIT_RE.split(n['label']) if p]
            if not pieces:
                self.find(where, 'E14', 'empty label')
            if item is None:
                continue
            title = evidence.norm_space(item['title'])
            for p in pieces:
                if evidence.norm_space(p) not in title:
                    self.find(where, 'E14', f'{p} is not in the press title')

    def e11(self):
        for src in runview.NOTICE_SOURCES:
            env = self.envelopes.get(src)
            if env is None:
                continue
            for it in env['items']:
                if it.get('open_parse') != 'ok':
                    continue
                self.notices_checked += 1
                reason = reparse_notice(it)
                if reason:
                    self.find(f'notice/{src}/{it["id"]}', 'E11', reason)
