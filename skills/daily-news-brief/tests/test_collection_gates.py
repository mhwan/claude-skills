#!/usr/bin/env python3
"""S1-3 게이트 회귀 검증 — G4 4시나리오 + G2 주말 하한

실제 normalize.py 를 임시 RAW_DIR 로 돌려서 게이트 문구·판정을 확인한다.
네트워크(G5)는 스텁으로 대체한다.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import contextlib

# importlib 로 normalize.py 를 불러오면 scripts/__pycache__ 가 남는다.
# 테스트가 스킬 폴더에 쓰레기를 만들지 않게 한다.
sys.dont_write_bytecode = True

# 설치 경로가 아니라 이 파일 기준으로 찾는다. 하드코딩하면 저장소를 클론해서
# 돌려도 클론이 아닌 설치본을 검사하게 된다.
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'scripts', 'normalize.py')
OUTLETS = ['조선일보', '중앙일보', '동아일보', '한겨레', '경향신문', '매일경제', '한국경제']


def load_module(raw_dir):
    spec = importlib.util.spec_from_file_location('nz', SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.RAW_DIR = raw_dir
    # 네트워크 스텁. 이름이 틀리면 조용히 실제 네트워크를 친다 —
    # link_ok 는 link_status 로 바뀌었는데 스텁이 옛 이름을 잡고 있었다.
    assert hasattr(m, 'link_status'), 'normalize.link_status 가 없다 — 스텁 이름 확인'
    m.link_status = lambda url: 'ok'
    return m


def make_state(date, titles, fetched=None):
    arts = [{'outlet': OUTLETS[i % 7], 'title': t, 'body_len': 900,
             'published_date': date.replace('-', ''),
             'url': f'https://example.com/{i}'} for i, t in enumerate(titles)]
    n = fetched if fetched is not None else max(1, len(titles) // 7)
    return {
        'targetDate': date,
        'finishedAt': f'{date}T13:00:00+09:00',
        'articles': arts,
        'outlets': {o: {'fetched': n, 'totalCount': n * 4, 'capped': False} for o in OUTLETS},
    }


def write_raw(raw_dir, date, titles):
    """이력 파일을 직접 심는다"""
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, f'{date}.json'), 'w', encoding='utf-8') as f:
        json.dump({'target_date': date,
                   'articles': [{'title': t} for t in titles]}, f, ensure_ascii=False)


def run(raw_dir, state):
    m = load_module(raw_dir)
    tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
    json.dump(state, tmp, ensure_ascii=False)
    tmp.close()
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ['normalize.py', tmp.name]
    try:
        with contextlib.redirect_stdout(buf):
            m.main()
    finally:
        sys.argv = old
        os.unlink(tmp.name)
    return buf.getvalue()


def gate_row(report, name):
    for line in report.splitlines():
        if line.startswith(f'| {name} |'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            return cells[1], cells[2]
    return None, None


SET_A = [f'헤드라인 A{i}' for i in range(20)]
SET_B = [f'헤드라인 B{i}' for i in range(20)]

results = []

# --- 시나리오 1: 이력 0일 -> WARN, 조용히 PASS 하면 안 됨 -------------
d = tempfile.mkdtemp()
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S1 이력 0일', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 2: 이력 1일 · 불일치 -> PASS -----------------------------
d = tempfile.mkdtemp()
write_raw(d, '2026-08-20', SET_B)
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S2 이력 1일·불일치', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 3: 이력 2일 · 전부 동일 -> FAIL --------------------------
d = tempfile.mkdtemp()
write_raw(d, '2026-08-20', SET_A)
write_raw(d, '2026-08-19', SET_A)
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S3 이력 2일·전부동일', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 3b: 이력 1일만 동일 -> WARN (FAIL 아님) ------------------
d = tempfile.mkdtemp()
write_raw(d, '2026-08-20', SET_A)
write_raw(d, '2026-08-19', SET_B)
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S3b 1일만 동일', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 3c: 90% 중복(완전 동일 아님) -> WARN ---------------------
d = tempfile.mkdtemp()
write_raw(d, '2026-08-20', SET_A[:19] + ['헤드라인 다른것'])
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S3c 95% 중복', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 4: 구 코드가 꺼졌던 지점 — 4일 공백 ----------------------
# days=3 이던 시절엔 hist 가 비어 "최초 실행"으로 조용히 PASS 했다.
d = tempfile.mkdtemp()
write_raw(d, '2026-08-17', SET_A)   # 4일 전
write_raw(d, '2026-08-16', SET_A)   # 5일 전
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S4 4~5일 전 동일(연휴)', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- 시나리오 5: 이력 파일 손상 -> 읽기 실패 노출 ----------------------
d = tempfile.mkdtemp()
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, '2026-08-20.json'), 'w') as f:
    f.write('{ 깨진 JSON')
r = run(d, make_state('2026-08-21', SET_A))
results.append(('S5 이력 파일 손상', *gate_row(r, '스테일 탐지')))
shutil.rmtree(d)

# --- G2: 평일 vs 주말 하한 --------------------------------------------
g2 = []
for date, label in [('2026-08-21', '금'), ('2026-08-22', '토'), ('2026-08-23', '일')]:
    d = tempfile.mkdtemp()
    r = run(d, make_state(date, SET_A, fetched=4))   # 4건 = 평일 미달, 주말 충족
    g2.append((f'{label}요일 4건', *gate_row(r, '매체별 최소 건수')))
    shutil.rmtree(d)

print('## G4 스테일 탐지\n')
print('| 시나리오 | 판정 | 문구 |')
print('|---|---|---|')
for n, s, det in results:
    print(f'| {n} | {s} | {det} |')

print('\n## G2 매체별 최소 건수\n')
print('| 시나리오 | 판정 | 문구 |')
print('|---|---|---|')
for n, s, det in g2:
    print(f'| {n} | {s} | {det} |')

# --- 기대값 대조 -------------------------------------------------------
expect = {
    'S1 이력 0일': 'WARN', 'S2 이력 1일·불일치': 'PASS',
    'S3 이력 2일·전부동일': 'FAIL', 'S3b 1일만 동일': 'WARN',
    'S3c 95% 중복': 'WARN', 'S4 4~5일 전 동일(연휴)': 'FAIL',
    'S5 이력 파일 손상': 'WARN',
}
print('\n## 기대값 대조\n')
bad = 0
for n, s, _ in results:
    got = re.sub(r'[^A-Z]', '', s)
    ok = got == expect[n]
    bad += 0 if ok else 1
    print(f'{"✅" if ok else "❌"} {n}: 기대 {expect[n]} / 실제 {got}')
g2ok = ('WARN' in g2[0][1]) and ('PASS' in g2[1][1]) and ('PASS' in g2[2][1])
print(f'{"✅" if g2ok else "❌"} G2: 금요일 4건 WARN / 토·일 4건 PASS')
print(f'\n불일치 {bad + (0 if g2ok else 1)}건')
