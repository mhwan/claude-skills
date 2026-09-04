#!/usr/bin/env python3
"""근거 게이트 회귀 테스트 — 정상은 통과시키고 심은 오류는 잡는가

격리 원칙 1 — **실제 날짜의 작업 파일을 건드리지 않는다.**
초기 버전이 `{date}.summary.json` 을 덮어쓰고 마지막에 지워서
실제 브리핑의 요약본을 날렸다. 그래서 전용 날짜(TEST_DATE)로 복제해 쓴다.

격리 원칙 2 — **실제 수집 데이터에 의존하지 않는다.**
그 다음 버전은 `work/2026-08-21.todo.json` 을 빌려 썼다. 그 파일은 `prune.py` 가
지우는 대상이라 한 번 정리하면 테스트가 영구히 안 돌았고, 본문 전문을 영구
보관하지 않는다는 원칙과도 충돌했다. 지금은 합성 픽스처를 쓴다.
"""
import json
import os
import subprocess
import sys

W = os.path.expanduser('~/.claude/news-briefs/work')
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')   # 설치 경로 하드코딩 금지
TEST_DATE = '1999-01-01'         # 테스트 전용. 실제 데이터와 절대 겹치지 않는다
FIXTURE = os.path.join(HERE, 'fixtures', 'evidence-gate.todo.json')

fx = json.load(open(FIXTURE, encoding='utf-8'))
# 테스트 대상 이슈 하나만 남긴다. 다른 이슈가 남아 있으면 '누락' 위반이 대량으로
# 잡혀서 정작 검사하려는 항목이 묻힌다.
e1 = fx['issue']
pol_eds = fx['opinions']         # 논조 블록 검사용 — 정치 사설 3건

os.makedirs(W, exist_ok=True)


def make_todo(with_opinions):
    """이슈 케이스와 논조 케이스는 다른 todo 를 쓴다.

    사설이 든 todo 로 이슈 전용 케이스를 돌리면 '사설 있는데 쟁점 없음' 검사가
    걸려서 정상 케이스까지 FAIL 이 난다. 검사 대상을 격리한다.
    """
    return {
        'target_date': TEST_DATE,
        'lead': [], 'schedule': [], 'meta': fx['meta'],
        'categories': {
            cat: {'issues': [e1] if cat == '경제' else [],
                  'rest_count': 0, 'rest_titles': [],
                  'opinions': pol_eds if (with_opinions and cat == '정치') else []}
            for cat in ['정치', '경제', '사회', '국제', '문화']},
    }

test_todo = f'{W}/{TEST_DATE}.todo.json'
test_summ = f'{W}/{TEST_DATE}.summary.json'
json.dump(make_todo(False), open(test_todo, 'w', encoding='utf-8'), ensure_ascii=False)

outlets = sorted({a['outlet'] for a in e1['articles']})

CASES = [
    ('정상 — 원문 그대로',
     {'name': '삼성전자 110조 주주환원',
      'oneline': '삼성전자가 올해 최대 110조원 주주환원을 의결했다. 3분기 30조원 현금배당.'},
     'PASS'),
    ('🔴 숫자 변조 — 110조를 150조로',
     {'name': '삼성전자 150조 주주환원',
      'oneline': '삼성전자가 올해 최대 150조원 주주환원을 의결했다.'},
     'FAIL'),
    ('🔴 지어낸 날짜',
     {'name': '삼성전자 주주환원',
      'oneline': '삼성전자가 9월 15일 이사회에서 주주환원을 의결했다.'},
     'FAIL'),
    ('🔴 지어낸 인용',
     {'name': '삼성전자 주주환원',
      'oneline': '한지훈 회장은 "주주 여러분께 보답하겠습니다"라고 말했다.'},
     'FAIL'),
    ('🔴 매체 귀속 오류 — 매경은 이 이슈에 없다',
     {'name': '삼성전자 주주환원',
      'oneline': '매경이 단독으로 보도한 삼성전자 주주환원 계획.'},
     'FAIL'),
    ('🔴 조사 붙은 매체명 — 「매경은」',
     {'name': '삼성전자 주주환원',
      'oneline': '매경은 이 사안을 1면에 실었다.'},
     'FAIL'),
    ('🔴 한 줄 요약 빈칸',
     {'name': '삼성전자 주주환원', 'oneline': ''},
     'FAIL'),
    ('정상 — 「중앙은행」은 중앙일보가 아니다',
     {'name': '삼성전자 주주환원',
      'oneline': '중앙은행 금리 결정과 무관하게 110조원 규모로 의결됐다.'},
     'PASS'),
    ('정상 — 집계 표현(6사9건)은 검사 대상 아님',
     {'name': '삼성전자 주주환원',
      'oneline': '6사 9건이 다룬 사안. 최대 110조원 규모.'},
     'PASS'),
]

# --- 논조 블록 케이스 -------------------------------------------------
# 이 블록들은 architect 검증에서 **전혀 검사되지 않는다**고 지적된 지점이다.
# 4블록(conflict/agree/gap/unopposed)과 issue/axis/target 은 실질 주장을
# 담는데 이전 구현은 rows[].argument 만 봤다.
def tone_case(**over):
    base = {
        'issue': '국민의힘 비주류 징계', 'axis': '당 기강인가 반대파 축출인가',
        'target': '국민의힘 지도부',
        'rows': [{'outlet': o['outlet'], 'kind': 'editorial', 'stance': '비판',
                  'url': o['url'], 'argument': '징계 수위를 문제 삼았다'}
                 for o in pol_eds],
        'conflict': '없음', 'agree': '같은 방향이다',
        'gap': '해당 없음', 'unopposed': '반론이 표본에 없다',
    }
    base.update(over)
    return {'issues': {e1['ref']: {'name': '삼성전자 주주환원',
                                   'oneline': '최대 110조원 주주환원을 의결했다'}},
            'tone': {'정치': [base]}}

TONE_CASES = [
    ('정상 — 논조 블록', tone_case(), 'PASS'),
    ('🔴 4블록에 지어낸 숫자', tone_case(conflict='9999조원 규모라고 보도했다'), 'FAIL'),
    ('🔴 4블록에 지어낸 인용', tone_case(agree='세 매체가 "당을 사유화했다"고 썼다'), 'FAIL'),
    ('🔴 표에 있는 매체를 없다고 서술',
     tone_case(gap=f'{pol_eds[0]["outlet"]}은 이 사안에 사설을 쓰지 않았다'), 'FAIL'),
    ('🔴 매체 수 모순', tone_case(agree='다섯 매체 모두 같은 방향이다'), 'FAIL'),
    ('🔴 축 누락', tone_case(axis=''), 'FAIL'),
]

print(f'이 이슈의 매체: {outlets}\n')
print('| 케이스 | 기대 | 실제 | |')
print('|---|:--:|:--:|:--:|')
fails = 0
for name, iss, expect in CASES:
    json.dump({'issues': {e1['ref']: iss}}, open(test_summ, 'w', encoding='utf-8'),
              ensure_ascii=False)
    r = subprocess.run([sys.executable, f'{SCRIPTS}/verify.py', TEST_DATE],
                       capture_output=True, text=True)
    got = 'FAIL' if r.returncode == 1 else ('PASS' if r.returncode == 0 else 'ERR')
    ok = got == expect
    fails += 0 if ok else 1
    print(f'| {name} | {expect} | {got} | {"✅" if ok else "❌"} |')
    if expect == 'FAIL' or not ok:
        for line in r.stdout.splitlines():
            if line.startswith(f'| {e1["ref"]} '):
                cells = [c.strip() for c in line.strip('|').split('|')]
                print(f'|  ↳ {cells[1]}: {cells[2]} | | | |')

json.dump(make_todo(True), open(test_todo, 'w', encoding='utf-8'), ensure_ascii=False)
print()
print('| 논조 케이스 | 기대 | 실제 | |')
print('|---|:--:|:--:|:--:|')
for name, summ, expect in TONE_CASES:
    json.dump(summ, open(test_summ, 'w', encoding='utf-8'), ensure_ascii=False)
    r = subprocess.run([sys.executable, f'{SCRIPTS}/verify.py', TEST_DATE],
                       capture_output=True, text=True)
    got = 'FAIL' if r.returncode == 1 else ('PASS' if r.returncode == 0 else 'ERR')
    ok = got == expect
    fails += 0 if ok else 1
    print(f'| {name} | {expect} | {got} | {"✅" if ok else "❌"} |')
    if expect == 'FAIL' or not ok:
        for line in r.stdout.splitlines():
            if line.startswith('| 정치/쟁점1'):
                cells = [c.strip() for c in line.strip('|').split('|')]
                print(f'|  ↳ {cells[1]}: {cells[2][:56]} | | | |')
                break

for p in (test_todo, test_summ):
    if os.path.exists(p):
        os.remove(p)
print(f'\n불일치 {fails}건')
sys.exit(1 if fails else 0)
