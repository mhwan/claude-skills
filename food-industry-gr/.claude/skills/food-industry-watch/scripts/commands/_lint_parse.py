"""Read report.md back into blocks for gate R: headings, tables, notes, lists, details, references."""
from __future__ import annotations

import re

HEADING = re.compile(r'^(#{1,6}) (.*)$')
REF_USE = re.compile(r'\[([^\[\]\n]*)\]\[([A-Za-z]+\d+)\]')
REF_DEF = re.compile(r'^\[([A-Za-z]+\d+)\]: (\S+)\s*$')
DETAILS_HEAD = re.compile(r'^<details><summary>그 밖 (\d+)건</summary>$')
DORMANT_HEAD = re.compile(r'^<details><summary>💤 휴면 (\d+)건</summary>$')
SEPARATOR_CELL = re.compile(r'^:?-{3,}:?$')
CELL_SPLIT = re.compile(r'(?<!\\)\|')


def raw_blocks(text) -> list:
    """[(kind, lines)]: headings stand alone; <details> … </details> is one block; else blank-separated."""
    out, cur, in_details = [], [], False

    def flush():
        if cur:
            out.append(list(cur))
            cur.clear()

    for line in text.split('\n'):
        if in_details:
            cur.append(line)
            if line.strip() == '</details>':
                flush()
                in_details = False
            continue
        if HEADING.match(line):
            flush()
            out.append([line])
            continue
        if not line.strip():
            flush()
            continue
        if line.startswith('<details>') and not cur:
            cur.append(line)
            in_details = True
            continue
        cur.append(line)
    flush()
    return [(classify(b), b) for b in out]


def classify(lines) -> str:
    first = lines[0]
    if HEADING.match(first):
        return 'heading'
    if first.startswith('<details>'):
        return 'details'
    if first.strip() == '---':
        return 'rule'
    if all(ln.startswith('|') for ln in lines):
        return 'table'
    if all(ln.startswith('>') for ln in lines):
        return 'notes'
    if all(ln.startswith('- ') for ln in lines):
        return 'list'
    if all(REF_DEF.match(ln) for ln in lines):
        return 'defs'
    return 'para'


def split_row(line) -> list:
    s = line.strip()
    parts = CELL_SPLIT.split(s)
    if parts and parts[0].strip() == '':
        parts = parts[1:]
    if parts and parts[-1].strip() == '':
        parts = parts[:-1]
    return [p.strip() for p in parts]


def table(lines):
    """(header cells, separator ok, [(raw line, cells)])."""
    header = split_row(lines[0]) if lines else []
    sep_ok = len(lines) > 1 and all(SEPARATOR_CELL.match(c) for c in split_row(lines[1])) \
        and len(split_row(lines[1])) == len(header)
    rows = [(ln, split_row(ln)) for ln in lines[2 if sep_ok else 1:]]
    return header, sep_ok, rows


def details(lines, head=DETAILS_HEAD):
    """(declared N or None, item lines)."""
    m = head.match(lines[0].strip()) if lines else None
    items = [ln for ln in lines[1:] if ln.startswith('- ')]
    return (int(m.group(1)) if m else None), items


def definitions(blocks) -> list:
    out = []
    for kind, lines in blocks:
        if kind == 'defs':
            out += [REF_DEF.match(ln).groups() for ln in lines]
    return out
