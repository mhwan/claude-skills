#!/usr/bin/env python3
"""Delete the article text of a finished run (raw/ and work/), keeping the report, the data file and history.

Usage: purge_raw.py --run RUN_DIR [--abandon]

Only a folder directly under <root>/runs/korean-news-search/ is accepted. Without --abandon the purge is
refused until run.json records a report, so the text is never lost before the report exists.
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

PURGE_DIRS = ('raw', 'work')


def count_files(folder):
    total = 0
    for _, _, names in os.walk(str(folder)):
        total += len(names)
    return total


def remove_dir(folder):
    """Delete a folder and return how many files went with it (a symbolic link is removed, not followed)."""
    if folder.is_symlink():
        folder.unlink()
        return 0
    if not folder.is_dir():
        return 0
    n = count_files(folder)
    shutil.rmtree(str(folder))
    return n


def main(argv=None):
    common.setup_output()
    parser = common.make_parser('Delete raw/ and work/ of a finished run.')
    parser.add_argument('--run', required=True)
    parser.add_argument('--abandon', action='store_true',
                        help='delete even though no report was made (the search is given up)')
    args = parser.parse_args(argv)
    try:
        run = common.resolve_run(args.run)
    except common.UsageError as e:
        return common.fail('usage error: %s' % e, 2)
    meta = common.load_meta(run)
    if not args.abandon and not meta.get('report') and not meta.get('purged'):
        return common.fail('refused: no report was made from this run yet, so the article text is kept. '
                           'Render the report first, or use --abandon to give the search up.', 1)
    deleted = 0
    for name in PURGE_DIRS:
        deleted += remove_dir(run / name)
    remaining = sum(count_files(run / name) for name in PURGE_DIRS if (run / name).is_dir())
    common.record_step(run, 'purge', purged=True)
    common.emit({'deleted_files': deleted, 'remaining': remaining})
    return 0 if remaining == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
