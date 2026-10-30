#!/usr/bin/env python3
"""Check every summary against the article text it was written from.

Usage: verify_summaries.py --run RUN_DIR

Reads  work/articles.json (the texts) and work/summaries.json ({"summaries": [{"id", "summary", "quote"}]})
Writes work/verified.json; prints {"ok", "checked", "failed": [ids]}; exit 0 when every summary passes.

What is checked, mechanically:
  number  every number (digits, commas ignored) appears in the article text
  latin   every Latin-letter word appears in the article text
  person  every name written before a title word (CEO, chairman, minister ... in Korean) appears in the article text
  quote   the quote is at most 40 characters and appears word for word in the article text
  copy    the summary does not copy a run of more than 40 characters from the article text
  length  the summary is not empty and not longer than 200 characters
A claim without a number, a Latin word, a name or a quote cannot be checked this way; a person must read it.
"""
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

KINDS = ['number', 'latin', 'person', 'quote', 'copy', 'length']
MAX_QUOTE = 40
MAX_SUMMARY = 200
COPY_WINDOW = MAX_QUOTE + 1

_NUMBER = re.compile(r'\d[\d,]*\d|\d')
_LATIN = re.compile(r'[A-Za-z][A-Za-z0-9]*')
_TITLES = '대표이사|대표|회장|사장|위원장|장관|의원|국장|청장|총리|대통령|교수|이사'
_PERSON = re.compile(r'(?<![가-힣])([가-힣]{2,4})\s*(?:%s)(?![적단회팀])' % _TITLES)


def squash(text):
    return ' '.join(str(text or '').split())


def numbers_of(text):
    return set(m.group(0).replace(',', '') for m in _NUMBER.finditer(text))


def check_one(summary, quote, body):
    """The list of problems found in one summary (empty when it passes)."""
    problems = []
    summary = squash(summary)
    body_text = squash(body)
    if not summary:
        return ['length: the summary is empty']
    if len(summary) > MAX_SUMMARY:
        problems.append('length: the summary is too long (%d characters, the limit is %d)' % (len(summary), MAX_SUMMARY))
    body_numbers = numbers_of(body_text)
    for m in _NUMBER.finditer(summary):
        value = m.group(0).replace(',', '')
        if value not in body_numbers:
            problems.append('number: %s is not in the article text' % value)
    body_words = set(w.lower() for w in _LATIN.findall(body_text))
    for word in _LATIN.findall(summary):
        if word.lower() not in body_words:
            problems.append('latin: %s is not in the article text' % word)
    for m in _PERSON.finditer(summary):
        if m.group(1) not in body_text:
            problems.append('person: %s is not in the article text' % m.group(1))
    if quote:
        quote = squash(quote)
        if len(quote) > MAX_QUOTE:
            problems.append('quote: %d characters is over the limit of %d' % (len(quote), MAX_QUOTE))
        if quote not in body_text:
            problems.append('quote: not found word for word in the article text')
    for i in range(0, max(len(summary) - COPY_WINDOW + 1, 0)):
        if summary[i:i + COPY_WINDOW] in body_text:
            problems.append('copy: the summary copies more than %d characters from the article text' % MAX_QUOTE)
            break
    return problems


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_result(run, doc):
    common.write_json(run / 'work' / 'verified.json', doc)
    common.record_step(run, 'verify')


def verify(run):
    articles_path = run / 'work' / 'articles.json'
    summaries_path = run / 'work' / 'summaries.json'
    if not articles_path.is_file():
        return None, 'work/articles.json is missing: run normalize.py first'
    articles = common.read_json(articles_path).get('articles') or []
    summaries_sha = sha256_of(summaries_path) if summaries_path.is_file() else None
    base = {'ok': False, 'checked': 0, 'kinds': KINDS, 'summaries_sha256': summaries_sha, 'results': []}
    if not summaries_path.is_file():
        if articles:
            write_result(run, base)
            return None, 'work/summaries.json is missing: %d article(s) still need a summary' % len(articles)
        base['ok'] = True
        write_result(run, base)
        return base, None
    try:
        given = common.read_json(summaries_path).get('summaries')
    except (ValueError, AttributeError):
        given = None
    if not isinstance(given, list):
        write_result(run, base)
        return None, 'work/summaries.json is not {"summaries": [...]}'
    by_id = {}
    results = []
    extra = []
    for entry in given:
        key = entry.get('id') if isinstance(entry, dict) else None
        if key in by_id:
            extra.append({'id': key, 'ok': False, 'problems': ['duplicate: two summaries for the same article']})
        elif key is not None:
            by_id[key] = entry
    known = set()
    for art in articles:
        aid = art['id']
        known.add(aid)
        entry = by_id.get(aid)
        if entry is None:
            problems = ['missing: no summary was written for this article']
        else:
            problems = check_one(entry.get('summary'), entry.get('quote'), art.get('text'))
        results.append({'id': aid, 'ok': not problems, 'problems': problems})
    for key in by_id:
        if key not in known:
            results.append({'id': key, 'ok': False,
                            'problems': ['unknown: this id is not among the articles to summarize']})
    results.extend(extra)
    doc = dict(base)
    doc['results'] = results
    doc['checked'] = len(articles)
    doc['ok'] = all(r['ok'] for r in results)
    write_result(run, doc)
    return doc, None


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Check summaries against the article texts.')
    parser.add_argument('--run', required=True)
    args = parser.parse_args(argv)
    try:
        run = common.resolve_run(args.run)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    doc, reason = verify(run)
    if doc is None:
        return common.fail('cannot verify: %s' % reason, 1)
    common.emit({'ok': doc['ok'], 'checked': doc['checked'], 'failed': [r['id'] for r in doc['results'] if not r['ok']]})
    return 0 if doc['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
