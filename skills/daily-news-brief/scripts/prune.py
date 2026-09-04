#!/usr/bin/env python3
"""브리핑 생성 후 중간 데이터 정리 — 명시적 호출 전용

  prune.py {date}          그 날짜의 중간 데이터를 정리
  prune.py --sweep         브리핑이 있는 모든 날짜를 정리 + 오래된 raw 삭제

옵션
  --dry-run                무엇을 지울지만 보여준다
  --retain-days N          raw 보관 일수 (기본 14 = 스테일 창 7일 + 소급 여유 7일)
  --force                  브리핑 .md 가 없어도 정리
  --chunks DIR             수집 회수 청크(base64 .txt)도 삭제. 아래 경고 참조

왜 필요한가
  이 파이프라인은 본문 전문을 raw/ 와 work/*.todo.json 에 두 벌 보관한다.
  프로젝트 원칙은 "본문 전문은 중간 데이터로만 쓴다"이고 매경 llms.txt 는
  전문 영구 저장을 명시적으로 거부한다. 정리는 용량 문제가 아니라 원칙 준수다.

무엇을 남기나
  news-briefs/{date}.md            산출물. 절대 건드리지 않는다
  work/{date}.summary.json         우리가 쓴 요약. 무엇을 주장했는지의 기록
  raw/{date}.json                  제목·링크·메타만. 본문·개체명·유사도 벡터 제거
                                   → 직전 7일 스테일 게이트(G4)가 제목을 읽는다

무엇을 지우나
  clusters/{date}.json             prepare 이후 읽는 곳이 없다
  work/{date}.todo.json            본문 전문 사본. 재렌더용인데 verify 를 못 돌리므로
                                   검증 없는 재렌더를 남기느니 지운다

재렌더가 필요하면
  정리 후에는 요약만 고쳐 다시 뽑을 수 없다. 수집부터 다시 돌린다.
  검증 없이 브리핑을 내보내지 않기 위한 의도적 선택이다.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

BASE = os.path.expanduser('~/.claude/news-briefs')
RAW_DIR = os.path.join(BASE, 'raw')
CLUSTER_DIR = os.path.join(BASE, 'clusters')
WORK_DIR = os.path.join(BASE, 'work')

# normalize.py 의 HISTORY_WINDOW_DAYS 는 7. 소급 실행(며칠 전 날짜로 브리핑)을
# 감안해 두 배를 남긴다. 정리된 raw 는 하루 100KB 안쪽이라 여유를 둬도 싸다.
DEFAULT_RETAIN_DAYS = 14

# G4 가 읽는 것 + 헤드라인을 원문으로 되짚을 수 있는 최소한. **허용 목록**이다 —
# 여기 없는 필드는 전부 지워진다. 금지 목록으로 뒀다가 수집기가 새 필드를 추가하면
# 조용히 살아남기 때문이다. 실측으로 버려지는 것: body·subtitle(원문 텍스트),
# ne_*·similarity·sentiment(빅카인즈 파생), category·byline(그날 안에 다 쓴다).
KEEP_FIELDS = ('outlet', 'outlet_code', 'news_id', 'title', 'url',
               'published_date', 'published_ts', 'is_editorial', 'body_len')

DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.md$')
# 회수 청크의 내용물: base64 한 덩어리. 앞에 "PAD…|" 패딩이 붙기도 한다.
CHUNK_RE = re.compile(r'^(?:PAD)*\|?[A-Za-z0-9+/]{20000,}={0,2}$')


def human(n):
    return f'{n / 1024:,.0f} KB' if n < 1024 * 1024 else f'{n / 1048576:.1f} MB'


class Plan:
    """무엇을 할지 모아뒀다가 한 번에 실행한다. --dry-run 이 같은 경로를 탄다."""

    def __init__(self, dry):
        self.dry = dry
        self.acts = []       # (동작, 경로, 절감바이트, 비고)

    def strip_raw(self, path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.acts.append(('건너뜀', path, 0, f'읽기 실패: {e}'))
            return
        if data.get('pruned'):
            self.acts.append(('이미 정리됨', path, 0, ''))
            return

        before = os.path.getsize(path)
        arts = data.get('articles', [])
        # 실제로 사라지는 것을 그대로 적는다. 금지 목록을 따로 두면 수집기가 새 필드를
        # 추가했을 때 조용히 지워지고 기록에는 안 남는다.
        dropped = sorted({k for a in arts for k in a} - set(KEEP_FIELDS))
        data['articles'] = [{k: a[k] for k in KEEP_FIELDS if k in a} for a in arts]
        data['pruned'] = True
        data['pruned_at'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        data['pruned_fields'] = dropped

        body = json.dumps(data, ensure_ascii=False, indent=1)
        after = len(body.encode('utf-8'))
        self.acts.append(('본문 제거', path, before - after,
                          f'{len(arts)}건 · {before // 1024}KB → {after // 1024}KB'))
        if not self.dry:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(body)
            os.replace(tmp, path)      # 중간에 죽어도 원본이 반쪽이 되지 않는다

    def delete(self, path, note=''):
        if not os.path.exists(path):
            return
        n = os.path.getsize(path)
        self.acts.append(('삭제', path, n, note))
        if not self.dry:
            os.remove(path)

    def freed(self):
        return sum(a[2] for a in self.acts)

    def report(self):
        if not self.acts:
            print('정리할 것이 없다.')
            return
        head = '## 정리 계획 (--dry-run — 실제로 지우지 않았다)' if self.dry else '## 정리 결과'
        print(head + '\n')
        print('| 동작 | 파일 | 절감 | 비고 |')
        print('|---|---|--:|---|')
        for act, path, n, note in self.acts:
            print(f'| {act} | `{os.path.relpath(path, BASE)}` | '
                  f'{human(n) if n else "—"} | {note} |')
        verb = '절감 예정' if self.dry else '절감'
        print(f'\n**총 {verb}: {human(self.freed())}**')


def prune_date(plan, date, force=False):
    brief = os.path.join(BASE, f'{date}.md')
    if not os.path.exists(brief) and not force:
        print(f'브리핑이 없다: {brief}\n'
              f'정리는 브리핑 생성·검증이 끝난 뒤에 한다. '
              f'중간 데이터를 지우면 다시 만들 수 없다.\n'
              f'그래도 지우려면 --force.', file=sys.stderr)
        return False

    raw = os.path.join(RAW_DIR, f'{date}.json')
    if os.path.exists(raw):
        plan.strip_raw(raw)
    plan.delete(os.path.join(CLUSTER_DIR, f'{date}.json'), 'prepare 이후 안 읽음')
    plan.delete(os.path.join(WORK_DIR, f'{date}.todo.json'), '본문 전문 사본')
    return True


def sweep_old_raw(plan, retain_days):
    """보관 창을 벗어난 raw 는 통째로 지운다. 안 그러면 정리본이 무한히 쌓인다."""
    if not os.path.isdir(RAW_DIR):
        return
    cutoff = (datetime.now() - timedelta(days=retain_days)).strftime('%Y-%m-%d')
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.endswith('.json'):
            continue
        d = name[:-5]
        if len(d) == 10 and d < cutoff:
            plan.delete(os.path.join(RAW_DIR, name), f'{retain_days}일 창 밖')


def sweep_chunks(plan, d):
    """회수 청크 삭제.

    경고: 이 파일들은 Claude Code 세션 트랜스크립트가 참조한다. 지우면 그 세션을
    되살릴 때 해당 툴 결과를 못 읽는다. 끝난 수집 건에 한해 쓸 것.
    """
    if not os.path.isdir(d):
        print(f'청크 디렉터리가 없다: {d}', file=sys.stderr)
        return
    for name in sorted(os.listdir(d)):
        if not name.endswith('.txt'):
            continue
        p = os.path.join(d, name)
        if os.path.getsize(p) < 100_000:
            continue                      # 청크는 160,000자. 작은 건 다른 툴 출력이다
        # 내용까지 확인한다. 파일명만 보면 같은 도구의 다른 출력을 지운다.
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


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    # 모르는 플래그는 거부한다. 예전에는 조용히 무시했는데, `--dryrun` 을 치면
    # 사용자는 예행 연습이라고 믿는 채로 실제 삭제가 돌았다. 파괴적 도구에서
    # '아마 이런 뜻이겠지'는 허용하지 않는다.
    FLAGS = {'--dry-run', '--force', '--sweep'}
    ARG_FLAGS = {'--retain-days', '--chunks'}

    dry = force = sweep = False
    retain = DEFAULT_RETAIN_DAYS
    chunks_dir = None
    dates = []

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
                chunks_dir = os.path.expanduser(argv[i])
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', a):
            dates.append(a)
        else:
            known = ' '.join(sorted(FLAGS | ARG_FLAGS))
            print(f'알 수 없는 인자: {a}\n  아는 것: {known} · 날짜(YYYY-MM-DD)',
                  file=sys.stderr)
            return 2
        i += 1

    if sweep and dates:
        print('--sweep 은 브리핑이 있는 모든 날짜를 대상으로 한다. '
              '날짜와 같이 쓸 수 없다.', file=sys.stderr)
        return 2

    if sweep:
        dates = sorted(m.group(1) for m in
                       (DATE_RE.match(n) for n in os.listdir(BASE)) if m)
        if not dates:
            print(f'브리핑이 하나도 없다: {BASE}', file=sys.stderr)
    elif not dates:
        print('날짜를 주거나 --sweep 을 쓸 것.', file=sys.stderr)
        return 2

    plan = Plan(dry)
    for d in dates:
        if not prune_date(plan, d, force=force) and not sweep:
            return 1
    if sweep:
        sweep_old_raw(plan, retain)
    if chunks_dir:
        sweep_chunks(plan, chunks_dir)

    plan.report()
    return 0


if __name__ == '__main__':
    sys.exit(main())
