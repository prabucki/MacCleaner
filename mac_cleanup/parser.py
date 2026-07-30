"""Console argument parser configuration."""

import os
from argparse import ArgumentParser, RawTextHelpFormatter
from typing import final

import attr

from mac_cleanup.__version__ import __version__


@final
@attr.s(slots=True)
class Args:
    dry_run: bool = attr.ib(default=False)
    update: bool = attr.ib(default=False)
    configure: bool = attr.ib(default=False)
    custom_path: bool = attr.ib(default=False)
    force: bool = attr.ib(default=False)
    verbose: bool = attr.ib(default=False)


parser = ArgumentParser(
    description=f"""\
    Python cleanup script for macOS
    Version: {__version__}
    https://github.com/mac-cleanup/mac-cleanup-py\
    """,
    formatter_class=RawTextHelpFormatter,
)

parser.add_argument("-n", "--dry-run", help="Run without deleting stuff", action="store_true")

parser.add_argument("-u", "--update", help="Update Homebrew on cleanup", action="store_true")

parser.add_argument("-c", "--configure", help="Open module configuration screen", action="store_true")

parser.add_argument("-p", "--custom-path", help="Specify path for custom modules", action="store_true")

parser.add_argument("-f", "--force", help="Accept all warnings", action="store_true")

parser.add_argument("-v", "--verbose", help="Print folders to be deleted", action="store_true")

args = Args()

# MacCleaner patch: `mc` drives the collector directly and owns its own argument parser,
# so importing this package must not consume sys.argv. parse_known_args (rather than
# parse_args) also keeps upstream usable as a library without it exiting on stray flags.
if os.environ.get("MAC_CLEANUP_NO_ARGPARSE"):
    pass
else:
    parser.parse_known_args(namespace=args)

# args.dry_run = True  # debug
# args.configure = True  # debug
# args.custom_path = True  # debug
# args.force = True  # debug
# args.verbose = True # debug
