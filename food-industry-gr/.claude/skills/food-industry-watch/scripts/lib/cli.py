"""Strict argument parsing, exit codes and the main-function wrapper."""
from __future__ import annotations

import argparse
import json
import sys
import traceback

from . import dates, schema

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_WARN = 0, 1, 2, 3

_STARTED = None


def mark_start():
    """Remember when this command started; manifest stages record it as started_at."""
    global _STARTED
    _STARTED = dates.now_iso()


def started_at():
    return _STARTED or dates.now_iso()


class UsageError(Exception):
    """Bad input from the caller; maps to exit 2."""


class Parser(argparse.ArgumentParser):
    """ArgumentParser that never accepts abbreviated flags and exits 2 on error."""

    def __init__(self, *args, **kwargs):
        kwargs['allow_abbrev'] = False
        super().__init__(*args, **kwargs)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f'{self.prog}: error: {message}\n')


def date_arg(s):
    """argparse type for a strict YYYY-MM-DD date."""
    try:
        return dates.parse_date(s)
    except dates.DateError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass


def run_main(fn) -> int:
    """Run fn() and map exceptions to exit codes."""
    try:
        return int(fn())
    except UsageError as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_USAGE
    except schema.SchemaError as e:
        print(f'error: {e}', file=sys.stderr)
        for line in e.errors:
            print(f'  {line}', file=sys.stderr)
        return EXIT_USAGE
    except Exception:
        traceback.print_exc()
        return EXIT_FAIL


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
