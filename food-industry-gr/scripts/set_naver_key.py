#!/usr/bin/env python3
"""네이버 API HUB 키를 터미널에서 입력받아 ~/.zshrc 에 저장한다 (macOS 전용).

입력한 글자는 화면에 나오지 않고, 키 값은 어디에도 출력하지 않는다.
이 파일의 키 줄만 바꾸고 다른 줄은 그대로 둔다.
"""
import getpass
import os
import re
import shlex
import sys
import tempfile

ID_ENV = 'NCP_APIGW_API_KEY_ID'
SECRET_ENV = 'NCP_APIGW_API_KEY'
MARKER = '# 네이버 API HUB 키 (scripts/set_naver_key.py)'


def is_valid(value):
    return bool(value) and all(c.isprintable() and not c.isspace() for c in value)


def update_text(text, values):
    """기존 키 줄을 지우고 맨 아래에 새 키 줄을 붙인 글을 돌려준다."""
    names = '|'.join(re.escape(name) for name in values)
    old_line = re.compile(rf'^\s*(export\s+)?({names})=')
    kept = [
        line for line in text.splitlines(keepends=True)
        if not old_line.match(line) and line.strip() != MARKER
    ]
    if kept and not kept[-1].endswith('\n'):
        kept[-1] += '\n'
    kept.append(MARKER + '\n')
    for name, value in values.items():
        kept.append(f'export {name}={shlex.quote(value)}\n')
    return ''.join(kept)


def save(path, text):
    """중간에 끊겨도 원래 파일이 망가지지 않게 임시 파일을 쓴 뒤 바꿔치기한다."""
    real_path = os.path.realpath(path)  # 바로가기(심볼릭 링크)면 원본을 고친다
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(real_path), prefix='.zshrc-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(temp_path, real_path)  # mkstemp 파일은 본인만 읽을 수 있다
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def main():
    if sys.platform != 'darwin':
        print('이 프로그램은 Mac 전용입니다. Windows 는 INSTALL.md 3단계를 따라 주세요.')
        return 2

    print('키를 붙여 넣고 Enter 를 누르세요. 입력한 글자는 화면에 나오지 않습니다.')
    try:
        client_id = getpass.getpass('Client ID: ').strip()
        client_secret = getpass.getpass('Client Secret: ').strip()
    except (KeyboardInterrupt, EOFError):
        print('\n취소했습니다. 아무것도 바꾸지 않았습니다.')
        return 1

    if not is_valid(client_id) or not is_valid(client_secret):
        print('비어 있거나 공백이 섞인 값이 있습니다. 아무것도 바꾸지 않았습니다.')
        return 1
    if client_id == client_secret:
        print('두 값이 같습니다. 같은 키를 두 번 붙여 넣은 것 같습니다. 아무것도 바꾸지 않았습니다.')
        return 1

    path = os.path.join(os.path.expanduser('~'), '.zshrc')
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    save(path, update_text(text, {ID_ENV: client_id, SECRET_ENV: client_secret}))

    print(f'저장했습니다: {path}')
    print('Claude 앱을 Cmd+Q 로 완전히 종료했다가 다시 열어 주세요.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
