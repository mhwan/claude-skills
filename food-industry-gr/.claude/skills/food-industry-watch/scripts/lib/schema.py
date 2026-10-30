"""Declarative JSON contracts, validation, and atomic JSON I/O.

Spec language:
  scalar names   'str' 'int' 'num' 'bool' 'null' 'date' 'datetime' 'run_id' 'any'
  Enum(*values)  List(spec)  Map(spec)  Or(*specs)  Str(max_len)
  dict           strict object: key -> spec, a key ending in '?' is optional
  Obj(dict, check=fn)  object with an extra cross-field check fn(obj, path) -> [errors]
Any object whose spec has both 'from' and 'to' as 'date' also requires from <= to.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from . import dates


class SchemaError(Exception):
    def __init__(self, message, errors=()):
        super().__init__(message)
        self.errors = list(errors)


class Enum:
    def __init__(self, *values):
        self.values = values


class List:
    def __init__(self, item):
        self.item = item


class Map:
    def __init__(self, value):
        self.value = value


class Or:
    def __init__(self, *options):
        self.options = options


class Str:
    def __init__(self, max_len):
        self.max_len = max_len


class Obj:
    def __init__(self, fields, check=None):
        self.fields = fields
        self.check = check


_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3}|\.\d{6})?[+-]\d{2}:\d{2}$')
_RUN_ID_RE = re.compile(r'^\d{4}-\d{2}-\d{2}-[1-9]\d*$')


def _is_date(v) -> bool:
    try:
        dates.parse_date(v)
        return True
    except dates.DateError:
        return False


def _is_datetime(v) -> bool:
    if not isinstance(v, str) or not _DATETIME_RE.match(v):
        return False
    try:
        datetime.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_run_id(v) -> bool:
    return isinstance(v, str) and bool(_RUN_ID_RE.match(v)) and _is_date(v[:10])


_SCALARS = {
    'str': lambda v: isinstance(v, str),
    'int': lambda v: isinstance(v, int) and not isinstance(v, bool),
    'num': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    'bool': lambda v: isinstance(v, bool),
    'null': lambda v: v is None,
    'date': _is_date,
    'datetime': _is_datetime,
    'run_id': _is_run_id,
    'any': lambda v: True,
}


def _type_name(v) -> str:
    return 'null' if v is None else type(v).__name__


def _describe(spec) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, Enum):
        return 'enum(' + ' '.join(str(x) for x in spec.values) + ')'
    if isinstance(spec, List):
        return 'list'
    if isinstance(spec, Map):
        return 'map'
    if isinstance(spec, Str):
        return f'str(max {spec.max_len})'
    if isinstance(spec, Or):
        return ' or '.join(_describe(o) for o in spec.options)
    return 'object'


def _check(value, spec, path, errors):
    if isinstance(spec, str):
        if not _SCALARS[spec](value):
            errors.append(f'{path}: expected {spec}, got {_type_name(value)} {value!r:.60}')
    elif isinstance(spec, Str):
        if not isinstance(value, str):
            errors.append(f'{path}: expected str, got {_type_name(value)}')
        elif len(value) > spec.max_len:
            errors.append(f'{path}: longer than {spec.max_len} characters ({len(value)})')
    elif isinstance(spec, Enum):
        if not any(value == x and type(value) is type(x) for x in spec.values):
            errors.append(f'{path}: {value!r:.60} not in {_describe(spec)}')
    elif isinstance(spec, List):
        if not isinstance(value, list):
            errors.append(f'{path}: expected list, got {_type_name(value)}')
        else:
            for i, item in enumerate(value):
                _check(item, spec.item, f'{path}[{i}]', errors)
    elif isinstance(spec, Map):
        if not isinstance(value, dict):
            errors.append(f'{path}: expected map, got {_type_name(value)}')
        else:
            for k, v in value.items():
                _check(v, spec.value, f'{path}.{k}', errors)
    elif isinstance(spec, Or):
        _check_or(value, spec, path, errors)
    elif isinstance(spec, (dict, Obj)):
        _check_obj(value, spec if isinstance(spec, Obj) else Obj(spec), path, errors)
    else:
        raise TypeError(f'bad spec at {path}: {spec!r}')


def _check_or(value, spec, path, errors):
    attempts = []
    for option in spec.options:
        errs = []
        _check(value, option, path, errs)
        if not errs:
            return
        attempts.append((option, errs))
    # Report the detailed errors when only one option could apply to a non-null value.
    real = [a for a in attempts if a[0] != 'null']
    if value is not None and len(real) == 1:
        errors.extend(real[0][1])
    else:
        errors.append(f'{path}: expected {_describe(spec)}, got {_type_name(value)} {value!r:.60}')


def _check_obj(value, spec, path, errors):
    if not isinstance(value, dict):
        errors.append(f'{path}: expected object, got {_type_name(value)}')
        return
    names = set()
    for key, sub in spec.fields.items():
        optional = key.endswith('?')
        name = key[:-1] if optional else key
        names.add(name)
        if name not in value:
            if not optional:
                errors.append(f'{path}: missing key {name!r}')
            continue
        _check(value[name], sub, f'{path}.{name}', errors)
    for name in value:
        if name not in names:
            errors.append(f'{path}: unknown key {name!r}')
    if spec.fields.get('from') == 'date' and spec.fields.get('to') == 'date':
        frm, to = value.get('from'), value.get('to')
        if _is_date(frm) and _is_date(to) and frm > to:
            errors.append(f'{path}: from {frm} is after to {to}')
    if spec.check is not None:
        errors.extend(spec.check(value, path))


# ---------------------------------------------------------------- contracts

PERIOD = {'from': 'date', 'to': 'date'}
SECTION4 = Enum('gov', 'assembly', 'industry', 'market')


def _notice_check(item, path):
    if item.get('open_parse') != 'ok':
        return []
    errs = [f'{path}.{k}: required when open_parse is ok'
            for k in ('open_from', 'open_to', 'open_source', 'open_evidence') if item.get(k) is None]
    frm, to = item.get('open_from'), item.get('open_to')
    if not errs and _is_date(frm) and _is_date(to) and frm > to:
        errs.append(f'{path}: open_from {frm} is after open_to {to}')
    return errs


NOTICE_ITEM = Obj({
    'id': 'str', 'dept': 'str', 'kind': Enum('입법예고', '행정예고', '재행정예고'),
    'law_type': Or('str', 'null'), 'title': 'str', 'posted': 'date',
    'open_from': Or('date', 'null'), 'open_to': Or('date', 'null'),
    'open_parse': Enum('ok', 'failed'),
    'open_source': Or(Enum('lawmaking:입법의견 접수기간', 'ftc:기간', 'mfds:body_regex', 'quote'), 'null'),
    'open_evidence': Or(Str(80), 'null'),
    'url': 'str', 'dup_key': 'str', 'family_key': Or('str', 'null'),
}, check=_notice_check)

COLLECT_ITEMS = {
    'bigkinds': {'id': 'str', 'title': 'str', 'date': 'date', 'outlet': 'str', 'url': 'str',
                 'excerpt': Str(200), 'queries': List('str')},
    'korea_kr': {'id': 'str', 'dept': 'str', 'title': 'str', 'date': 'date', 'url': 'str'},
    'lawmaking_web': NOTICE_ITEM,
    'mfds_notice': NOTICE_ITEM,
    'ftc_notice': NOTICE_ITEM,
    'mafra_notice': NOTICE_ITEM,
    'assembly_bills': {'bill_id': 'str', 'name': 'str', 'proposer': 'str', 'date': 'date',
                       'committee': Or('str', 'null'), 'stage': Or('str', 'null'), 'url': 'str'},
    'assembly_schedule': {'date': 'date', 'time': Or('str', 'null'), 'committee': 'str',
                          'meeting': 'str', 'url': 'str'},
    'foodsafety': {'kind': Enum('recall', 'admin', 'unfit'), 'seq': 'str', 'company': 'str',
                   'product': Or('str', 'null'), 'food_type': Or('str', 'null'),
                   'reason': Or('str', 'null'), 'grade': Or('str', 'null'),
                   'action': Or('str', 'null'), 'law': Or('str', 'null'), 'date': 'date',
                   'date_field': Enum('hmpgpblict_prcsdtm', 'dsps_dcsndt', 'cret_dtm'),
                   'url': 'str', 'personal': 'bool', 'tracked_company': Or('str', 'null')},
}


def _collect_check(obj, path):
    src = obj.get('source')
    errs = []
    if 'basis' in obj and src != 'foodsafety':
        errs.append(f'{path}.basis: only allowed for source foodsafety')
    item_spec = COLLECT_ITEMS.get(src) if isinstance(src, str) else None
    items = obj.get('items')
    if item_spec is not None and isinstance(items, list):
        for i, item in enumerate(items):
            _check(item, item_spec, f'{path}.items[{i}]', errs)
    return errs


ARTICLE = {'ref': 'str', 'id': 'str', 'title': 'str', 'outlet': 'str', 'date': 'date',
           'url': 'str', 'body': 'str', 'title_body_conflicts': List('str'),
           # chars: len(body); src_chars: the length the browser job read (null when it sent none)
           'body_check?': {'chars': 'int', 'src_chars': Or('int', 'null'), 'ends_sentence': 'bool'}}
SENTENCE = {'text': 'str', 'kind': Enum('fact', 'edit'), 'refs': List('str'),
            'evidence?': List({'ref': 'str', 'quote': 'str'}), 'links?': List('str'),
            'unverified?': 'bool'}
FIELD = {'text': 'str', 'refs': List('str')}
LWF = {'direct': 'bool', 'sentences?': List(SENTENCE), 'ref?': Or('str', 'null'),
       'not_shown_reason?': Or('str', 'null')}
ENTITY = {'name': 'str', 'aliases?': List('str'), 'requires_any?': List('str'),
          'exclude_compounds?': List('str')}


def _unique(items, key, path, label):
    errs, seen = [], set()
    if not isinstance(items, list):
        return errs
    for i, item in enumerate(items):
        if isinstance(item, dict) and key in item:
            v = item[key]
            if v in seen:
                errs.append(f'{path}[{i}].{key}: duplicate {label} {v!r}')
            seen.add(v)
    return errs


def _format_check(obj, path):
    sections = obj.get('sections')
    return (_unique(sections, 'id', f'{path}.sections', 'section id')
            + _unique(sections, 'order', f'{path}.sections', 'section order'))


def _link_policy_check(obj, path):
    hosts = obj.get('hosts')
    errs = _unique(hosts, 'host', f'{path}.hosts', 'host')
    for i, h in enumerate(hosts if isinstance(hosts, list) else []):
        if not isinstance(h, dict):
            continue
        is_seed = h.get('mode') == 'robots_seed'
        if is_seed and 'robots_seed' not in h:
            errs.append(f'{path}.hosts[{i}]: mode robots_seed requires robots_seed')
        if not is_seed and 'robots_seed' in h:
            errs.append(f'{path}.hosts[{i}]: robots_seed is only allowed with mode robots_seed')
    return errs


def _contract(name, fields, check=None):
    return Obj(dict({'schema': 'str'}, **fields), check=check)


REGISTRY = {}


def _register(name, fields, check=None):
    REGISTRY[name] = _contract(name, fields, check)


_register('fiw.collect/1', {
    'source': Enum(*COLLECT_ITEMS),
    'run_id': 'run_id', 'period': PERIOD,
    'inclusion': Enum('article_date', 'posted', 'notice_open_overlap', 'upcoming_4w', 'record_date'),
    'method': Enum('script', 'browser', 'api'),
    'status': Enum('ok', 'partial', 'failed', 'no_key'),
    'probe': Or('null', {'rows': 'int', 'ok': 'bool'}),
    'requests': List({'target': 'str', 'query?': Or('str', 'null'), 'total': Or('int', 'null'),
                      'received': 'int', 'pages': 'int', 'status': Enum('ok', 'partial', 'failed')}),
    'basis?': Map('str'),
    'error': Or('str', 'null'),
    'items': List('any'),
}, check=_collect_check)

_register('fiw.clusters/1', {
    'rule_stage': {'input_articles': 'int', 'dropped_by_rule': 'int', 'clusters': 'int'},
    'clusters': List({'cid': 'str', 'article_ids': List('str'), 'core_ids': List('str'),
                      'rep_id': 'str', 'from': 'date', 'to': 'date', 'entities': List('str'),
                      'prior_issue_candidates': List('str'), 'auto?': Or('str', 'null')}),
    'dropped': List({'article_id': 'str', 'rule': 'str'}),
})

_register('fiw.relevance/1', {
    'clusters': List({'cid': 'str', 'verdict': Enum('issue', 'other', 'noise'),
                      'section': Or(SECTION4, 'null'), 'market_topic': Or('str', 'null'),
                      'issue_link': Or('str', 'null'), 'reason': 'str'}),
    'items': List({'source': 'str', 'id': 'str', 'verdict': Enum('include', 'other'),
                   'category': Or('str', 'null'), 'reason': 'str'}),
})

# Compact authoring form of fiw.relevance/1 (`relevance --compile`): full entries only for issue / other
# clusters and included items; noise clusters and other items in groups sharing one reason.
_register('fiw.relevance_brief/1', {
    'clusters': List({'cid': 'str', 'verdict': Enum('issue', 'other'), 'section': SECTION4,
                      'market_topic?': Or('str', 'null'), 'issue_link?': Or('str', 'null'), 'reason': 'str'}),
    'noise': List({'reason': 'str', 'cids': List('str')}),
    # Every reviewed cluster not listed above is noise; last_cid / count prove the list was read to the end.
    'noise_rest?': {'reason': 'str', 'last_cid': 'str', 'count': 'int'},
    'items': List({'source': 'str', 'id': 'str', 'category': Or('str', 'null'), 'reason': 'str'}),
    'items_other': List({'source': 'str', 'reason': 'str', 'ids': List('str')}),
    # Per source: every item that needs an entry (prefilter undecided, or include of a keyword source)
    # and is not listed above is other; count proves the source's list was read.
    'items_rest?': List({'source': 'str', 'reason': 'str', 'count': 'int'}),
})

_register('fiw.todo/1', {
    'issues': List({'key': 'str', 'section': SECTION4, 'articles': List(ARTICLE),
                    'coverage?': {'outlets': 'int', 'articles': 'int'},
                    'flow_candidates': List({'ref': 'str', 'title': 'str', 'date': 'date',
                                             'url': 'str', 'excerpt': 'str', 'outlet?': 'str'}),
                    'prior': Or('null', {'title': 'str', 'last_sentences': List('str'),
                                         'corrections?': List('str')}),
                    'locks': {'rename': Or('str', 'null'), 'pin_section': Or('str', 'null')}}),
    'excluded_no_link': List({'title': 'str', 'outlets': List('str')}),
    'excluded_no_body?': List({'title': 'str', 'outlets': List('str')}),
    'market': List({'topic': 'str', 'articles': List(ARTICLE)}),
    'lwf_mentions': Map('bool'),
})

THREE_LINE = {'issue': 'str', 'head': FIELD, 'sentences': List(SENTENCE)}
SUMMARY_ISSUE = {'key': 'str', 'title': FIELD, 'actors?': List(FIELD),
                 'sentences': List(SENTENCE),
                 'flow_name?': {'text': 'str', 'kind': Enum('edit'), 'links': List('str')},
                 'flow?': List({'ref': 'str', 'text': 'str', 'mode': Enum('title', 'summary')}),
                 'lwf?': LWF}
SUMMARY_NOTICE = {'id': 'str', 'label': Or('str', 'null')}
SUMMARY_MARKET = {'topic': 'str', 'sentences': List(SENTENCE), 'lwf?': LWF}

_register('fiw.summary/1', {
    'three_lines': List(THREE_LINE),
    'issues': List(SUMMARY_ISSUE),
    'notices': List(SUMMARY_NOTICE),
    'market': List(SUMMARY_MARKET),
})

# ---------------------------------------------------------------- the summary written in parts (M8 0-C k, l)
_register('fiw.parts/1', {
    'todo_hash': 'str', 'max_chars': 'int',
    'parts': List({'part': 'str', 'issues': List('str'), 'market': List('str'), 'refs': List('str'),
                   'chars': 'int'}),
})

# per body: 1-3 facts, each with a verbatim quote of that body, or unused with the reason; last_ref and count
# prove the writer read the part to its end
_register('fiw.facts/1', {
    'part': 'str',
    'articles': List({'ref': 'str', 'facts?': List({'text': 'str', 'quote': 'str'}), 'unused?': 'str'}),
    'last_ref': 'str', 'count': 'int',
})

_register('fiw.part_summary/1', {
    'part': 'str', 'issues': List(SUMMARY_ISSUE), 'market': List(SUMMARY_MARKET),
})

_register('fiw.summary_head/1', {
    'three_lines': List(THREE_LINE), 'notices': List(SUMMARY_NOTICE),
})

_register('fiw.gate/1', {
    'gate': 'str', 'exit': Enum(0, 1, 2, 3), 'checked': 'int',
    'findings': List({'where': 'str', 'kind': 'str', 'detail': 'str'}),
    'classified': Map('int'), 'skipped': List('str'), 'input_hash': Or('str', 'null'),
})

_register('fiw.dead_links/1', {       # work/dead_links.json — every URL linkcheck saw die, never shrinks
    'links': List({'url': 'str', 'status': 'int', 'key': 'str', 'first_seen': 'datetime'}),
})

_register('fiw.prefilter/1', {
    'items': List({'source': 'str', 'id': 'str', 'verdict': Enum('include', 'other', 'undecided'),
                   'rule': 'str'}),
})

_register('fiw.bodies/1', {
    'run_id': 'run_id',
    'articles': List({'id': 'str', 'title': 'str', 'outlet': 'str', 'date': 'date', 'url': 'str',
                      'body': 'str', 'chars?': 'int'}),
    'errors': List({'id': 'str', 'status': Or('int', 'null'), 'detail': 'str'}),
})

_register('fiw.body_requests/1', {'ids': List('str')})

_register('fiw.manifest/1', {
    'run_id': 'run_id', 'created_at': 'datetime', 'today': 'date', 'period': PERIOD,
    'period_source': Enum('plan', 'incremental', 'explicit'), 'plan_segment': Or('int', 'null'),
    'stages': Map({'status': Enum('pending', 'ok', 'failed', 'warn'), 'exit': Or('int', 'null'),
                   'input_hash': Or('str', 'null'), 'started_at?': Or('datetime', 'null'),
                   'at': Or('datetime', 'null')}),
    'sources': Map({'status': Enum('ok', 'partial', 'failed', 'no_key'),
                    'read_from': Or('date', 'null'), 'read_to': Or('date', 'null')}),
    'period_report?': {'raws': List('run_id'), 'first_seen': Map('date'), 'retro': 'bool',
                       'uncollected': Or(PERIOD, 'null')},
})

_register('fiw.plan/1', {
    'created_at': 'datetime',
    'reason': Enum('first_run', 'catch_up_all', 'catch_up_recent_quarter'),
    'segments': List({'index': 'int', 'from': 'date', 'to': 'date',
                      'status': Enum('pending', 'running', 'done'), 'run_id': Or('run_id', 'null')}),
})

_register('fiw.state.runs/1', {
    'runs': List({'run_id': 'run_id', 'created_at': 'datetime', 'period': PERIOD,
                  'period_source': Enum('plan', 'incremental', 'explicit'),
                  'report': Or('str', 'null'), 'gates': Map('int'),
                  # summary issue key -> state id, written by publish (new:c0003 -> I-0007)
                  'issue_ids?': Map('str')}),
})

_register('fiw.state.coverage/1', {'sources': Map(List(PERIOD))})

_register('fiw.state.doctor/1', {
    'app_check': Or('null', {'at': 'datetime', 'elements': 'int', 'matched_index': 'int',
                             'file_name': 'str', 'marker_len?': 'int', 'chunk_chars?': 'int'}),
})

_register('fiw.state.issues/1', {
    'issues': List({
        'id': 'str', 'title': 'str', 'section': SECTION4, 'status': Enum('active', 'dormant'),
        'first_seen': 'date', 'last_article': Or('date', 'null'), 'aliases': List('str'),
        'flow': List({'date': 'date', 'text': 'str', 'url': 'str', 'article_id': 'str',
                      'verified_at': 'datetime', 'run_id': 'run_id', 'outlet?': 'str'}),
        'last_sentences': List({'text': 'str', 'article_ids': List('str'), 'urls': List('str'),
                                'verified_at': 'datetime', 'run_id': 'run_id'}),
        'locks': List({'type': Enum('no_merge_with', 'exclude_pattern', 'rename', 'pin_section'),
                       'value': 'str', 'by': Enum('user'), 'at': 'datetime'}),
        'corrections': List({'rev': 'int', 'request': 'str', 'diff': 'str', 'gate': 'str'}),
        'last_run?': 'run_id',
    }),
})

_register('fiw.state.notices/1', {
    'items': List(Obj(dict(NOTICE_ITEM.fields, first_run='run_id'), check=_notice_check)),
})

_register('fiw.state.hygiene/1', {
    'periods': List({'run_id': 'run_id', 'period': PERIOD,
                     'counts': {'recall': 'int', 'admin': 'int', 'unfit': 'int'},
                     'grades': Map('int'), 'basis': Map('str')}),
})

_register('fiw.state.seen_ids/1', {'run_id': 'run_id', 'sources': Map(List('str'))})

_register('fiw.history/1', {
    'revs': List({'rev': 'int', 'at': 'datetime', 'command': 'str', 'run_id': Or('run_id', 'null'),
                  'files': Map('str')}),
})

_register('fiw.correction/1', {
    'request': 'str', 'issue': 'str', 'field': 'str', 'new': 'any',
    'refs': List('str'), 'evidence': List({'ref': 'str', 'quote': 'str'}),
})

_register('fiw.doctor/1', {
    'checks': List({'id': 'str', 'status': Enum('ok', 'warn', 'fail', 'not_run'),
                    'detail': 'str', 'fix': Or('str', 'null')}),
    'exit': Enum(0, 1, 3),
})

_register('fiw.period/1', {
    'status': Enum('ready', 'needs_decision'), 'today': 'date',
    'period': Or(PERIOD, 'null'), 'period_source': Or(Enum('plan', 'incremental'), 'null'),
    'plan_segment': Or('int', 'null'),
    'decision': Or('null', {'kind': Enum('first_run_quarter', 'gap_over_quarter'),
                            'options': List('str'), 'proposals': Map(List(PERIOD))}),
})

_register('fiw.watch/1', {
    'provisional': List('str'), 'own_company': 'str',
    'companies': List(ENTITY), 'associations': List(ENTITY),
    'agencies': List(ENTITY), 'committees': List(ENTITY),
    'bigkinds': {'providers': Enum('all'), 'probe_query': 'str',
                 'queries': List({'query': 'str',
                                  'sections': List(Enum('gov', 'assembly_issues', 'industry', 'market'))}),
                 'open_questions': List('str')},
    'stage1': {'exclude': List('str'), 'include_signals': List('str')},
    'food_related': {'include': List('str'), 'exclude': List('str'),
                     'no_match_other?': List('str')},
})

_register('fiw.format/1', {
    'sentence_limit': {'min_sentences': 'int', 'max_sentences': 'int',
                       'min_chars': 'int', 'max_chars': 'int'},
    'max_articles_per_issue': 'int', 'dormancy_weeks': 'int',
    'sections': List({'id': 'str', 'enabled': 'bool', 'order': 'int', 'heading_level': Enum(1, 2),
                      'title': 'str', 'columns': List('str'), 'max_rows': Or('int', 'null')}),
}, check=_format_check)

_register('fiw.link_policy/1', {
    'hosts': List({'host': 'str', 'mode': Enum('never_request', 'no_check', 'robots_seed'),
                   'reason': 'str',
                   'robots_seed?': {'checked': 'date', 'via': Enum('browser', 'script'),
                                    'status': 'int', 'lines': List('str')}}),
}, check=_link_policy_check)


# ---------------------------------------------------------------- API

def validate(obj, name) -> list:
    """Error strings (with JSON paths) for obj against contract name; [] when valid."""
    if name not in REGISTRY:
        raise KeyError(f'unknown schema {name!r}')
    if not isinstance(obj, dict):
        return [f'$: expected object, got {_type_name(obj)}']
    errors = []
    if obj.get('schema') != name:
        errors.append(f'$.schema: expected {name!r}, got {obj.get("schema")!r}')
    _check(obj, REGISTRY[name], '$', errors)
    return errors


def check(obj, name):
    errors = validate(obj, name)
    if errors:
        raise SchemaError(f'{name}: {len(errors)} schema error(s)', errors)


def load(path, name):
    path = Path(path)
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        raise SchemaError(f'{name}: file not found: {path}') from None
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise SchemaError(f'{name}: invalid JSON in {path}: {e}') from None
    got = obj.get('schema') if isinstance(obj, dict) else None
    if got != name:
        raise SchemaError(f'{path}: schema is {got!r}, expected {name!r}')
    errors = validate(obj, name)
    if errors:
        raise SchemaError(f'{path}: {len(errors)} schema error(s) for {name}', errors)
    return obj


def atomic_write(path, text):
    """Write text via a temp file in the same folder and os.replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump(path, obj, name):
    """Validate, then write JSON atomically (nothing is written when invalid)."""
    check(obj, name)
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
