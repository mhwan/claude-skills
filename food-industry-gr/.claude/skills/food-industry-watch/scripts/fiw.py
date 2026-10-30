#!/usr/bin/env python3
"""Food Industry Watch — single entry point.

  fiw.py doctor [--tool-result FILE] [--marker-len N] [--json]
  fiw.py config [--take-default KEY ...] [--json]
  fiw.py period [--today D] [--accept-quarter | --catch-up {all,recent-quarter}] [--json]
  fiw.py run new [--today D] [--from D --to D] [--json]
  fiw.py run show --run ID [--json]
  fiw.py collect --run ID --source {bigkinds,korea_kr,lawmaking_web,mfds_notice,ftc_notice,assembly_bills,
                                    assembly_schedule,foodsafety,all} [--json]
  fiw.py check --run ID [--json]
  fiw.py prefilter --run ID [--json]
  fiw.py browser-script JOB [--json]
  fiw.py browser-config --run ID --job JOB
  fiw.py ingest --run ID --job bigkinds_bodies --expect-len N FILE... [--json]
  fiw.py cluster --run ID [--json]
  fiw.py relevance --check --run ID [--json]
  fiw.py prepare --run ID [--json]
  fiw.py verify --run ID [--json]
  fiw.py render --run ID [--json]
  fiw.py lint --run ID [--json]
  fiw.py linkcheck --run ID [--max N] [--json]
  fiw.py publish --run ID [--json]
  fiw.py report --from D --to D [--three-lines FILE] [--json]
  fiw.py pipeline --run ID (--until judge | --from summary) [--json]
  fiw.py parts --run ID {split [--max-chars N] | check --part NN | merge --head FILE} [--json]
  fiw.py state {check [--run ID] | merge A B | split A --articles ID,ID | lock A --type T --value V |
                undo --to REV | history} [--apply] [--json]
  fiw.py correct --run ID --file FILE [--apply] [--json]
  fiw.py format {apply PATCH [--run ID] | undo} [--apply] [--json]
  fiw.py prune --run ID [--chunks DIR ...] [--apply] [--json]
  fiw.py review --run ID {--all | --risk --n N --seed S | --dropped N --seed S | --other N --seed S |
                          --cluster-members N} [--json]
  fiw.py seed {export --out DIR | import --from DIR} [--apply] [--json]
  fiw.py import-legacy [--from DIR] [--apply] [--json]

Exit codes: 0 ok · 1 fail · 2 usage/input error · 3 warn / user decision needed.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import cli  # noqa: E402
from commands import (browser, check, cluster, collect, config, correct, doctor, format, ingest,  # noqa: E402
                      legacy, linkcheck, lint, parts, period, pipeline, prefilter, prepare, prune, publish, relevance,
                      render, report, review, run, seed, state, verify)


def build_parser():
    parser = cli.Parser(prog='fiw.py', description='Food Industry Watch report pipeline.')
    sub = parser.add_subparsers(dest='command', metavar='COMMAND', required=True)
    for module in (doctor, config, period, run, collect, check, prefilter, browser, ingest,
                   cluster, relevance, prepare, verify, render, lint, linkcheck, publish, report, pipeline, parts,
                   state, correct, format, prune, review, seed, legacy):
        module.register(sub)
    return parser


def main(argv=None) -> int:
    cli.setup_stdio()
    cli.mark_start()
    args = build_parser().parse_args(argv)
    return cli.run_main(lambda: args.func(args))


if __name__ == '__main__':
    sys.exit(main())
