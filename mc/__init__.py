"""
MacCleaner — comprehensive headless macOS cleanup, built on a fork of mac-cleanup-py.

The upstream package (``mac_cleanup``) provides the collector, progress bar and a large
set of well-tested cleanup modules. This package adds everything needed to run without
supervision: a path safety policy, quarantine instead of deletion, root escalation
through a verb-limited helper, timeouts, reporting, and the cleanup modules covering
what CleanMyMac and OnyX do.

Nothing here imports ``mac_cleanup`` at package-import time; the CLI sets
``MAC_CLEANUP_NO_ARGPARSE`` first so upstream does not consume ``sys.argv``.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
