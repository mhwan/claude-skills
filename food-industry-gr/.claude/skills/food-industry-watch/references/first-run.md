# First run and environment check

Read on the first use on a machine, and whenever `fiw.py doctor` does not exit 0. The skill was developed on macOS; **its behaviour inside the Claude desktop app on Windows has not been verified** — these checks exist so that the agent on that machine can find and fix problems itself. After fixing anything, tell the user in one or two lines what was wrong and what was changed.

## Contents
- [1. Find Python](#1-find-python)
- [2. Shell and command form](#2-shell-and-command-form)
- [3. Script environment — doctor](#3-script-environment--doctor)
- [4. App check — large tool results](#4-app-check--large-tool-results)
- [5. Collector results](#5-collector-results)

---

## 1. Find Python

Try in order and use the first that prints a version ≥ 3.9. Call it `PY` below.

| OS | Commands |
|---|---|
| Windows | `py -3 --version` → `python3 --version` → `python --version` <!-- gr-compat: ignore --> |
| macOS / Linux | `python3 --version` <!-- gr-compat: ignore --> |

On Windows, `where.exe python python3` <!-- gr-compat: ignore --> showing only `…\Microsoft\WindowsApps\python.exe` while no version prints means the Microsoft Store alias stub, not Python. If no Python works, ask the user for permission before installing anything; suggest python.org (tick "Add python.exe to PATH") or `winget install Python.Python.3.12`, then open a new terminal.

## 2. Shell and command form

The Bash tool on Windows may run Git Bash or PowerShell; check with `echo $0` / `$PSVersionTable.PSVersion` if unsure. Always pass the absolute path of `scripts/fiw.py` (the skill's base directory is given when the skill loads) and quote it. On Windows add `-X utf8` after the Python command (without it Korean output can fail on a Korean-locale Windows).

| Shell | Form |
|---|---|
| bash / zsh (macOS) | `PY "<skill>/scripts/fiw.py" doctor` |
| Git Bash (Windows) | `PY -X utf8 "<skill>/scripts/fiw.py" doctor` |
| PowerShell | `& PY -X utf8 "<skill>\scripts\fiw.py" doctor` |

Never chain commands with `&&`, `||` or `;` — one command per call.

## 3. Script environment — doctor

Run `PY "<skill>/scripts/fiw.py" doctor`. Exit `0` = all checks ran and passed, `3` = warning or a check still to do, `1` = failure. Each non-ok line prints a `fix:`. Common cases:

| Check | Symptom | Do |
|---|---|---|
| `python_version` | below 3.9 | Install a newer Python (step 1), rerun with it |
| `python_executable` | warn: Store Python | Works; if files written by the skill seem to disappear, switch to a python.org install |
| `stdio_encoding` | warn: not UTF-8 | Output may be garbled in the console; file contents are unaffected (all file I/O is UTF-8). Setting `PYTHONIOENCODING=utf-8` for the command fixes the console |
| `utf8_file_roundtrip` | fail | Report to the user with the detail line; do not run collection |
| `skill_files` | fail | The skill folder is incomplete or a defaults file is invalid — reinstall or re-copy the skill |
| `data_home` | fail | The output folder (`GR_HOME`, else `output/` in the working folder) is not writable or is a file; fix the path or permissions |
| `app_tool_results` | not_run | Do step 4 |

## 4. App check — large tool results

Fetching news bodies for the summaries returns data from the built-in browser in large chunks, and relies on the app saving an oversized tool result to a file. Check that once per machine, before the first body fetch:

1. Make sure the built-in browser pane is visible (Windows `Ctrl+Shift+B`, macOS `Cmd+Shift+B`). If it does not open, ask the user to open it from the app.
2. Open `https://www.korea.kr` in the built-in browser and run, with the browser's JavaScript tool:
   `'시작' + 'A'.repeat(160000) + '끝'`
3. The tool result should say the output was saved to a file under `…/.claude/projects/…/tool-results/…` with format `[{type, text}]`. Pass that path:
   `PY "<skill>/scripts/fiw.py" doctor --tool-result "<path>"`
4. Exit `0` records the check in the output folder (`state/food-industry-watch/`); later `doctor` runs report it as ok.
5. Larger chunks (fewer tool calls per job): run `'시작' + 'A'.repeat(1200000) + '끝'` the same way and pass the new path with `--marker-len 1200000`. Exit `0` records export chunks of 1,000,000 characters (`browser-script` prints them as `chunk:`). If this probe fails, `doctor` keeps the step-4 record and says the chunks stay at 150,000 — fetching still works, only with more calls. Do this step only after step 4 passed.

If the result came back inline or truncated instead of as a file, or `doctor --tool-result` fails, **stop before any body fetch** and tell the user what the tool returned (the notice text, path, and the doctor detail line). Without bodies the summaries cannot be written, so the run stops at `prepare` until the retrieval method is adjusted for this app.

## 5. Collector results

This skill collects nothing itself. The five collector skills (`korean-news-search`, `gov-notice-watch`, `assembly-bill-tracker`, `assembly-hearing-watch`, `food-safety-records`) each need their own tools and keys; their set-up problems (the `assembly` MCP server missing or failed, a missing key) are handled by their own instructions and by `INSTALL.md` of the working folder. When `fiw.py collect` prints "not collected" for a source, the cause is on the collector side: run that skill (the sources reference names which), do not work around it here. The run goes on without that source and the report marks it 「확인 필요」.
