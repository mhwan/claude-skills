#!/usr/bin/env python3
"""Clean up article-body files after a briefing is built — explicit invocation only.

  prune.py {date}          clean up the (latest) run folder of that date
  prune.py --sweep         clean up every run folder that has a report + delete old ones

Options
  --run DIR                run folder to clean up (use only together with one date)
  --dry-run                only show what would be deleted
  --retain-days N          retention in days (default 14 = 7-day stale window + 7 days of backfill margin)
  --force                  clean up even when the report .md does not exist
  --chunks DIR             also delete collection chunks (tool-results .txt files saved by the app).
                           See the warning below. Show the user the --dry-run list first and
                           delete only after they answer "ye" (yes).

Why this exists
  The pipeline keeps full article bodies in runs/…/raw and runs/…/work/*.todo.json.
  The project rule is "full bodies are intermediate data only", and the Maeil Business
  llms.txt explicitly refuses permanent storage of full text. Pruning is about
  following that rule, not about disk space.

What is deleted (unconditionally)
  runs/…/raw/*.json                  full article bodies
  runs/…/work/{date}.todo.json       copy of the full bodies
  runs/…/work/{date}.clusters.json   nothing reads it after prepare
  runs/…/work/{date}.summary.json    its sentences are already in the report. If kept, the
                                     structure check (check_structure) FAILs with "the report
                                     copied text from work/"
  The number of deleted body files (raw and todo) goes into the last header line of the
  report, worded "jiwotseumnida" (deleted).

What is kept
  reports/…/{date}.md            the deliverable. Only that one header line is touched
  runs/…/work/{date}.verify.json, runs/…/run.json
  state/…/{date}.json            titles, links and meta only (bodies, named entities and
                                 similarity vectors removed)
                                 -> the stale gate (G4) reads titles from the previous 7 days

If a re-render is needed
  After pruning, the summary cannot be fixed and rebuilt. Re-run from collection.
  This is deliberate: a briefing must not go out without verification.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import common
from render import BODY_LINE_PREFIX

# normalize.py's HISTORY_WINDOW_DAYS is 7. Keep double that to allow for backfill runs
# (a briefing for a date several days back). A pruned summary is under 100 KB a day,
# so the margin is cheap.
DEFAULT_RETAIN_DAYS = 14

# What G4 reads + the minimum needed to trace a headline back to its source. This is an
# **allowlist** — every field not listed here is dropped. A denylist would silently keep any
# field the collector adds later. Measured as dropped: body, subtitle (article text),
# ne_*, similarity, sentiment (BigKinds-derived), category, byline (all used within the day).
KEEP_FIELDS = ('outlet', 'outlet_code', 'news_id', 'title', 'url',
               'published_date', 'published_ts', 'is_editorial', 'body_len')

RUN_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})-[1-9]\d*$')
DATE_ARG_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# Content of a collection chunk. The current format, made by collect.js, is
# "KNSCHUNK|collect|<index>|<count>|<length>|" + base64 + "|PAD|PPP…".
# The old format (one base64 blob without a header, sometimes with "PAD…|" padding in front)
# is recognised too.
CHUNK_RE = re.compile(r'^(?:KNSCHUNK\|collect\|\d+\|\d+\|\d+\|[A-Za-z0-9+/=]{1,}(?:\|PAD\|P*)?'
                      r'|(?:PAD)*\|?[A-Za-z0-9+/]{20000,}={0,2})$')
# One chunk is slightly larger than 60,000 characters (the size set when exporting).
# A smaller file is some other tool's output.
CHUNK_MIN_BYTES = 30_000


def human(n):
    return f'{n / 1024:,.0f} KB' if n < 1024 * 1024 else f'{n / 1048576:.1f} MB'


class Plan:
    """Collects what to do and runs it all at once. --dry-run takes the same path."""

    def __init__(self, dry, root):
        self.dry = dry
        self.root = Path(root)
        self.acts = []       # (action, path, bytes saved, note)

    def save_state(self, raw_path, dest):
        """Write a reduced copy of raw (allowlisted fields only) to the state folder. No bodies."""
        try:
            data = common.read_json(raw_path)
        except Exception as e:
            self.acts.append(('건너뜀', raw_path, 0, f'읽기 실패: {e}'))
            return False
        arts = data.get('articles', [])
        # Record exactly what disappears. With a separate denylist, a field the collector adds
        # later would be dropped silently and leave no trace in the record.
        dropped = sorted({k for a in arts for k in a} - set(KEEP_FIELDS))
        data['articles'] = [{k: a[k] for k in KEEP_FIELDS if k in a} for a in arts]
        data['pruned'] = True
        data['pruned_at'] = common.dates.now_iso()
        data['pruned_fields'] = dropped
        self.acts.append(('요약 저장', dest, 0, f'{len(arts)}건 · 허용 필드만'))
        if not self.dry:
            common.write_json(dest, data)      # dying midway never leaves a half-written file
        return True

    def delete(self, path, note=''):
        if not os.path.exists(path):
            return
        n = os.path.getsize(path)
        self.acts.append(('삭제', path, n, note))
        if not self.dry:
            os.remove(path)

    def note(self, act, path, note=''):
        self.acts.append((act, path, 0, note))

    def freed(self):
        return sum(a[2] for a in self.acts)

    def rel(self, path):
        try:
            return os.path.relpath(path, self.root)
        except ValueError:
            return str(path)

    def report(self):
        if not self.acts:
            print('정리할 것이 없다.')
            return
        head = '## 정리 계획 (--dry-run — 실제로 지우지 않았다)' if self.dry else '## 정리 결과'
        print(head + '\n')
        print('| 동작 | 파일 | 절감 | 비고 |')
        print('|---|---|--:|---|')
        for act, path, n, note in self.acts:
            print(f'| {act} | `{self.rel(path)}` | '
                  f'{human(n) if n else "—"} | {note} |')
        verb = '절감 예정' if self.dry else '절감'
        print(f'\n**총 {verb}: {human(self.freed())}**')


def rewrite_body_line(plan, report, n_deleted):
    """Change only the last header line of the report, "jiul yejeong" (to be deleted) -> "jiwotseumnida" (deleted).
    Not a single other byte is touched."""
    if not report.is_file():
        return
    with open(str(report), encoding='utf-8', newline='') as fh:
        text = fh.read()
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if line.startswith(BODY_LINE_PREFIX) and '지울 예정' in line:
            lines[i] = f'{BODY_LINE_PREFIX} 작업용 본문 {n_deleted}개 파일은 보고서를 만든 뒤 지웠습니다.'
            plan.note('머리말 갱신', report, f'본문 {n_deleted}개 삭제를 기록')
            if not plan.dry:
                with open(str(report), 'w', encoding='utf-8', newline='') as fh:
                    fh.write('\n'.join(lines))
            return


def prune_run(plan, date, run, force=False):
    report = common.report_file(date)
    if not report.is_file() and not force:
        print(f'보고서가 없다: {report}\n'
              f'정리는 보고서 생성·검증이 끝난 뒤에 한다. '
              f'본문을 지우면 다시 만들 수 없다.\n'
              f'그래도 지우려면 --force.', file=sys.stderr)
        return False

    raw = run / 'raw' / f'{date}.json'
    if raw.is_file():
        plan.save_state(str(raw), common.state_folder() / f'{date}.json')

    bodies = common.body_files(run, date)
    for p in bodies:
        plan.delete(str(p), '본문 전문')
    plan.delete(str(run / 'work' / f'{date}.clusters.json'), 'prepare 이후 안 읽음')
    plan.delete(str(run / 'work' / f'{date}.summary.json'), '문장이 보고서에 그대로 있음')

    if bodies:
        rewrite_body_line(plan, report, len(bodies))
        if not plan.dry:
            common.write_run_info(run, date, status='pruned',
                                  pruned_at=common.dates.now_iso(), bodies_deleted=len(bodies))
    elif not plan.acts:
        plan.note('이미 정리됨', run, '')
    return True


def sweep_old(plan, retain_days):
    """Delete the bodies of run folders outside the retention window, and old summaries, entirely.
    Otherwise they pile up forever."""
    cutoff = (datetime.now() - timedelta(days=retain_days)).strftime('%Y-%m-%d')
    base = common.runs_base()
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            m = RUN_RE.match(entry.name)
            if m and entry.is_dir() and m.group(1) < cutoff:
                for p in common.body_files(entry, m.group(1)):
                    plan.delete(str(p), f'{retain_days}일 창 밖')
                plan.delete(str(entry / 'work' / f'{m.group(1)}.summary.json'), f'{retain_days}일 창 밖')
    state = common.state_folder()
    if state.is_dir():
        for p in sorted(state.glob('*.json')):
            d = p.name[:-5]
            if len(d) == 10 and d < cutoff:
                plan.delete(str(p), f'{retain_days}일 창 밖')


def sweep_chunks(plan, d):
    """Delete collection chunks.

    Warning: the Claude Code session transcript refers to these files. Once deleted, that
    session can no longer read the matching tool results when it is resumed. Use only
    for collections that are finished.
    """
    if not os.path.isdir(d):
        print(f'청크 디렉터리가 없다: {d}', file=sys.stderr)
        return
    for name in sorted(os.listdir(d)):
        if not name.endswith('.txt'):
            continue
        p = os.path.join(d, name)
        if os.path.getsize(p) < CHUNK_MIN_BYTES:
            continue                      # chunks are around 60,000 characters; smaller is another tool's output
        # Check the content too. Going by file name alone would delete other output of the same tool.
        try:
            with open(p, encoding='utf-8') as f:
                blob = json.load(f)[0]['text']
        except Exception:
            continue
        blob = re.sub(r'\n\n\(captured at origin.*?\)\s*$', '', blob, flags=re.S).strip()
        if blob[:1] == '"' and blob[-1:] == '"':
            blob = blob[1:-1]
        if CHUNK_RE.match(blob):
            plan.delete(p, '회수 청크')


def run(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    run_arg = common.pull_flag(argv, '--run')
    date_flag = common.pull_flag(argv, '--date')

    # Reject unknown flags. They used to be ignored silently, so typing `--dryrun` ran a real
    # deletion while the user believed it was a rehearsal. A destructive tool does not get
    # to guess what was probably meant.
    FLAGS = {'--dry-run', '--force', '--sweep'}
    ARG_FLAGS = {'--retain-days', '--chunks'}

    dry = force = sweep = False
    retain = DEFAULT_RETAIN_DAYS
    chunks_dir = None
    dates = [common.check_date(date_flag)] if date_flag else []

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in FLAGS:
            dry |= a == '--dry-run'
            force |= a == '--force'
            sweep |= a == '--sweep'
        elif a in ARG_FLAGS:
            i += 1
            if i >= len(argv):
                print(f'{a} 에 값이 없다.', file=sys.stderr)
                return 2
            if a == '--retain-days':
                try:
                    retain = int(argv[i])
                except ValueError:
                    print(f'--retain-days 는 정수여야 한다: {argv[i]}', file=sys.stderr)
                    return 2
                if retain < 1:
                    print('--retain-days 는 1 이상이어야 한다.', file=sys.stderr)
                    return 2
            else:
                chunks_dir = argv[i]
        elif DATE_ARG_RE.match(a):
            dates.append(common.check_date(a))
        else:
            known = ' '.join(sorted(FLAGS | ARG_FLAGS | {'--run', '--date'}))
            print(f'알 수 없는 인자: {a}\n  아는 것: {known} · 날짜(YYYY-MM-DD)',
                  file=sys.stderr)
            return 2
        i += 1

    if sweep and (dates or run_arg):
        print('--sweep 은 보고서가 있는 모든 실행 폴더를 대상으로 한다. '
              '날짜·--run 과 같이 쓸 수 없다.', file=sys.stderr)
        return 2
    if len(dates) > 1:
        print('날짜는 하나만 줄 수 있다.', file=sys.stderr)
        return 2

    plan = Plan(dry, common.output_root())

    if sweep:
        base = common.runs_base()
        found = []
        if base.is_dir():
            for entry in sorted(base.iterdir()):
                m = RUN_RE.match(entry.name)
                if m and entry.is_dir() and common.report_file(m.group(1)).is_file():
                    found.append((m.group(1), entry))
        if not found:
            print(f'보고서가 있는 실행 폴더가 하나도 없다: {base}', file=sys.stderr)
        for d, entry in found:
            prune_run(plan, d, entry, force=force)
        sweep_old(plan, retain)
    elif dates:
        d = dates[0]
        entry = common.resolve_run(d, run_arg)
        if not prune_run(plan, d, entry, force=force):
            return 1
    else:
        print('날짜를 주거나 --sweep 을 쓸 것.', file=sys.stderr)
        return 2

    if chunks_dir:
        sweep_chunks(plan, chunks_dir)

    plan.report()
    return 0


def main():
    common.setup_output()
    try:
        return run(sys.argv[1:])
    except common.UsageError as e:
        return common.fail(str(e), 2)


if __name__ == '__main__':
    sys.exit(main())
