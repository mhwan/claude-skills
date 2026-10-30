"""fiw.py doctor — environment and first-run checks."""
from __future__ import annotations

import argparse
import json
import locale
import os
import platform
import sys
import tempfile
from pathlib import Path

from lib import cli, dates, layout, schema

DEFAULT_MARKER_LEN = 160000
# export.js chunk sizes; a chunk is the slice plus ~170,100 characters of header and padding, so a size is
# used only when doctor saw a marker at least that long come back intact (M8 0-A a).
CHUNK_STEPS = (150000, 500000, 1000000)
CHUNK_OVERHEAD = 170100
REFERENCES = ('first-run.md', 'constraints.md', 'korean-text.md', 'contracts.md')
DEFAULTS = ('watch.json', 'food-industry-watch.json', 'link_policy.json')
FIX_FORMAT = ('the app did not save the tool result in the expected format '
              '(a JSON list of {type, text} objects) — see references/first-run.md step 3')


def register(sub):
    p = sub.add_parser('doctor', help='check the environment and the first-run setup')
    p.add_argument('--tool-result', metavar='FILE',
                   help='saved large tool result file to check (references/first-run.md step 3)')
    p.add_argument('--marker-len', metavar='N', type=_positive_int, default=DEFAULT_MARKER_LEN,
                   help=f'number of A characters in the marker (default {DEFAULT_MARKER_LEN})')
    p.add_argument('--json', action='store_true', help='print fiw.doctor/1 JSON')
    p.set_defaults(func=main)


def _positive_int(s):
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not an integer: {s!r}') from None
    if n < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return n


def _result(check_id, status, detail, fix=None):
    return {'id': check_id, 'status': status, 'detail': detail, 'fix': fix}


def check_python_version():
    v = platform.python_version()
    if sys.version_info >= (3, 9):
        return _result('python_version', 'ok', v)
    return _result('python_version', 'fail', f'{v} is older than 3.9',
                   'install Python 3.9 or newer; on Windows use python.org or '
                   '`winget install Python.Python.3.12`, not the Microsoft Store alias')


def check_python_executable():
    exe = sys.executable or '(unknown)'
    if os.name == 'nt' and '\\windowsapps\\' in exe.lower():
        return _result('python_executable', 'warn', f'{exe} (Microsoft Store Python)',
                       'Store Python works, but its AppData redirection can hide files; prefer '
                       'python.org or `winget install Python.Python.3.12`')
    return _result('python_executable', 'ok', exe)


def check_platform():
    return _result('platform', 'ok', platform.platform())


def check_shell():
    parts = [f'{k}={os.environ[k]}' for k in ('SHELL', 'MSYSTEM', 'ComSpec') if os.environ.get(k)]
    detail = ', '.join(parts) or 'no SHELL / MSYSTEM / ComSpec set'
    fix = None
    if os.name == 'nt':
        fix = ('quoting differs by shell: in bash (Git Bash) wrap arguments in single quotes; '
               'in PowerShell use single quotes too, and escape $ and backticks')
    return _result('shell', 'ok', detail, fix)


def check_stdio_encoding():
    enc = sys.stdout.encoding or ''
    # locale.getencoding() (3.11+) is the warning-free equivalent of getpreferredencoding(False):
    # the latter raises EncodingWarning under -X warn_default_encoding in UTF-8 mode.
    getenc = getattr(locale, 'getencoding', None)
    preferred = getenc() if getenc else locale.getpreferredencoding(False)
    detail = f'stdout={enc or "(none)"}, preferred={preferred}, utf8_mode={sys.flags.utf8_mode}'
    if enc.lower().replace('-', '').replace('_', '') == 'utf8':
        return _result('stdio_encoding', 'ok', detail)
    return _result('stdio_encoding', 'warn', detail,
                   'set PYTHONUTF8=1 (or PYTHONIOENCODING=utf-8) before running fiw.py')


def check_utf8_roundtrip():
    sample = '식품 대관 동향 — 「한글 확인」 ‘인용’ ㈜'
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'roundtrip.txt'
            with open(p, 'w', encoding='utf-8', newline='') as f:
                f.write(sample)
            with open(p, encoding='utf-8') as f:
                back = f.read()
    except OSError as e:
        return _result('utf8_file_roundtrip', 'fail', f'temp file error: {e}',
                       'check that the temp folder is writable')
    if back == sample:
        return _result('utf8_file_roundtrip', 'ok', 'Korean text written and read back unchanged')
    return _result('utf8_file_roundtrip', 'fail', 'Korean text changed after a UTF-8 round trip',
                   'check the file system and Python installation')


def check_skill_files():
    skill = layout.skill_dir()
    problems = []
    required = [skill / 'SKILL.md'] + [skill / 'references' / n for n in REFERENCES]
    required += [layout.defaults_dir() / n for n in DEFAULTS]
    for p in required:
        if not p.is_file():
            problems.append(f'missing {p.relative_to(skill).as_posix()}')
    if not problems:
        checks = (('watch.json + food-industry-watch.json', lambda: layout.load_watch(defaults=True)),
                  ('food-industry-watch.json format', lambda: layout.load_format(defaults=True)),
                  ('link_policy.json', lambda: schema.load(layout.defaults_dir() / 'link_policy.json',
                                                           'fiw.link_policy/1')))
        for label, load in checks:
            try:
                load()
            except (schema.SchemaError, cli.UsageError) as e:
                first = getattr(e, 'errors', None) and e.errors[0] or str(e)
                problems.append(f'invalid assets/defaults/{label}: {first}')
    if problems:
        return _result('skill_files', 'fail', '; '.join(problems),
                       'reinstall the skill folder (files are missing or invalid)')
    return _result('skill_files', 'ok', 'SKILL.md, references, and defaults present and valid')


def _writable_dir(d: Path) -> bool:
    probe = d / f'.fiw-probe-{os.getpid()}'
    try:
        with open(probe, 'w', encoding='utf-8', newline='') as f:
            f.write('probe')
        probe.unlink()
        return True
    except OSError:
        return False


def check_data_home():
    h = layout.output_root()
    fix = 'set GR_HOME to a writable folder, or fix the permissions of the data folder'
    if h.exists():
        if not h.is_dir():
            return _result('data_home', 'fail', f'{h} exists but is not a directory', fix)
        if _writable_dir(h):
            return _result('data_home', 'ok', f'{h} (writable)')
        return _result('data_home', 'fail', f'{h} is not writable', fix)
    anc = h.parent
    while not anc.exists() and anc != anc.parent:
        anc = anc.parent
    if anc.is_dir() and os.access(anc, os.W_OK | os.X_OK):
        return _result('data_home', 'ok', f'{h} (created on first run)')
    return _result('data_home', 'fail', f'{h} cannot be created ({anc} is not a writable folder)', fix)


def _marker(n):
    return '시작' + 'A' * n + '끝'


def chunk_chars_for(marker_len) -> int:
    fits = [t for t in CHUNK_STEPS if t + CHUNK_OVERHEAD <= marker_len]
    return max(fits) if fits else CHUNK_STEPS[0]


def _load_app_check():
    try:
        return schema.load(layout.state_dir() / 'doctor.json', 'fiw.state.doctor/1').get('app_check')
    except (OSError, schema.SchemaError):
        return None


def export_chunk_chars() -> int:
    """The chunk size browser jobs export with: the verified one, or the 150,000 default."""
    app = _load_app_check()
    return (app or {}).get('chunk_chars') or CHUNK_STEPS[0]


def _inspect_tool_result(p, marker_len):
    """(matched, elements, failure result or None)."""
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        return False, 0, _result('app_tool_results', 'fail', f'not JSON: {e}', FIX_FORMAT)
    if not isinstance(data, list) or not data or not all(
            isinstance(el, dict) and 'type' in el and isinstance(el.get('text'), str) for el in data):
        return False, 0, _result('app_tool_results', 'fail', 'not a JSON list of {type, text} objects', FIX_FORMAT)
    marker = _marker(marker_len)
    texts = [el['text'] for el in data]
    hits = [i for i, t in enumerate(texts) if marker in t]
    if hits and hits[0] == 0:
        return True, len(data), None
    if hits:
        return False, len(data), _result(
            'app_tool_results', 'fail',
            f'marker found only in element {hits[0]} of {len(data)}; reassembly reads element 0',
            'see references/first-run.md step 3 (the app split the tool result differently)')
    if any('A' * marker_len in t for t in texts):
        return False, len(data), _result(
            'app_tool_results', 'fail', 'the A run is intact but the Korean characters at its ends changed',
            'the app altered non-ASCII text in saved tool results — see references/first-run.md step 3')
    return False, len(data), _result('app_tool_results', 'fail', 'marker not found (the tool result was truncated)',
                                     'see references/first-run.md step 3 (large tool results are cut off)')


def check_tool_result_file(file_name, marker_len):
    """Inspect a saved tool result; on success also write state/doctor.json."""
    p = Path(file_name)
    if not p.is_file():
        raise cli.UsageError(f'--tool-result file not found: {file_name}')
    matched, elements, failure = _inspect_tool_result(p, marker_len)
    prior = _load_app_check()
    if not matched:
        if prior and marker_len > DEFAULT_MARKER_LEN:
            # A failed large-chunk probe does not undo the earlier pass; exports keep the verified size.
            return _result('app_tool_results', 'ok',
                           f'large-chunk probe failed ({failure["detail"]}) — export chunks stay at '
                           f'{prior.get("chunk_chars") or CHUNK_STEPS[0]}')
        return failure
    verified = max(marker_len, (prior or {}).get('marker_len') or 0)
    chunk = chunk_chars_for(verified)
    layout.ensure_home()
    state = {'schema': 'fiw.state.doctor/1',
             'app_check': {'at': dates.now_iso(), 'elements': elements, 'matched_index': 0, 'file_name': p.name,
                           'marker_len': verified, 'chunk_chars': chunk}}
    schema.dump(layout.state_dir() / 'doctor.json', state, 'fiw.state.doctor/1')
    return _result('app_tool_results', 'ok',
                   f'marker of {marker_len} characters found intact in element 0 of {elements} — '
                   f'export chunks of {chunk}')


def check_tool_result_state():
    p = layout.state_dir() / 'doctor.json'
    fix = 'run references/first-run.md step 3, then `fiw.py doctor --tool-result FILE`'
    if not p.is_file():
        return _result('app_tool_results', 'not_run', 'large tool output check has not been run', fix)
    try:
        state = schema.load(p, 'fiw.state.doctor/1')
    except schema.SchemaError as e:
        return _result('app_tool_results', 'not_run', f'doctor.json (state folder) is unreadable: {e}', fix)
    app = state.get('app_check')
    if not app:
        return _result('app_tool_results', 'not_run', 'large tool output check has not been run', fix)
    return _result('app_tool_results', 'ok', f'checked at {app["at"]} ({app["file_name"]}), '
                   f'export chunks of {app.get("chunk_chars") or CHUNK_STEPS[0]}')


def run_checks(tool_result=None, marker_len=DEFAULT_MARKER_LEN) -> list:
    checks = [check_python_version(), check_python_executable(), check_platform(), check_shell(),
              check_stdio_encoding(), check_utf8_roundtrip(), check_skill_files(),
              check_data_home()]
    if tool_result is not None:
        checks.append(check_tool_result_file(tool_result, marker_len))
    else:
        checks.append(check_tool_result_state())
    return checks


def exit_code(checks) -> int:
    statuses = {c['status'] for c in checks}
    if 'fail' in statuses:
        return cli.EXIT_FAIL
    if statuses & {'warn', 'not_run'}:
        return cli.EXIT_WARN
    return cli.EXIT_OK


def main(args) -> int:
    checks = run_checks(args.tool_result, args.marker_len)
    code = exit_code(checks)
    if args.json:
        out = {'schema': 'fiw.doctor/1', 'checks': checks, 'exit': code}
        schema.check(out, 'fiw.doctor/1')
        cli.print_json(out)
    else:
        for c in checks:
            print(f'[{c["status"]}] {c["id"]} — {c["detail"]}')
            if c['fix']:
                print(f'  fix: {c["fix"]}')
        print(f'doctor: exit {code}')
    return code
