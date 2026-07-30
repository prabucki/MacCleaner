"""
Pre-run environment checks.

An unattended cleaner should refuse to start rather than half-finish. These checks run
before any module declares work, and each either aborts the run, disables a capability,
or records a warning that ends up in the report.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from mc.privileged import ASKPASS_PATH, HELPER_PATH, Privileged
from mc.util import human, run

__all__ = ["Preflight", "PreflightResult"]

#: Refuse to run below this much free space unless overridden — a cleaner that fills the
#: disk with quarantine while trying to free it is worse than no cleaner.
DEFAULT_MIN_FREE_BYTES = 5 * 1024**3


@dataclass
class PreflightResult:
    """Outcome of the pre-run checks."""

    ok: bool = True
    abort_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    free_bytes: int = 0
    privileged_ok: bool = False
    askpass_ok: bool = False
    full_disk_access: bool = False
    on_ac_power: bool = True
    snapshot_taken: bool = False


class Preflight:
    """
    Runs the checks.

    :param privileged: Root-helper client to probe.
    :param require_ac: Abort when running on battery — for scheduled runs.
    :param min_free: Abort below this many free bytes.
    :param take_snapshot: Request an APFS local snapshot before cleaning.
    """

    def __init__(
        self,
        *,
        privileged: Privileged,
        require_ac: bool = False,
        min_free: int = DEFAULT_MIN_FREE_BYTES,
        take_snapshot: bool = False,
    ):
        self.privileged = privileged
        self.require_ac = require_ac
        self.min_free = min_free
        self.take_snapshot = take_snapshot

    def run_checks(self) -> PreflightResult:
        result = PreflightResult()

        result.free_bytes = free_space()
        if result.free_bytes < self.min_free:
            result.ok = False
            result.abort_reason = (
                f"only {human(result.free_bytes)} free, below the {human(self.min_free)} floor. "
                "Free some space manually first, or lower --min-free."
            )
            return result

        result.on_ac_power = on_ac_power()
        if self.require_ac and not result.on_ac_power:
            result.ok = False
            result.abort_reason = "running on battery and --on-ac-only was requested"
            return result

        # Privilege availability is a capability check, not a hard failure: an
        # unprivileged run still cleans everything inside the user's home.
        result.privileged_ok = self.privileged.available
        if not result.privileged_ok:
            if HELPER_PATH.is_file():
                result.warnings.append(
                    f"Root helper installed but unusable ({self.privileged.unavailable_reason}); "
                    "system-level modules will be skipped."
                )
            else:
                result.warnings.append(
                    "Root helper not installed - run ./install.sh to enable system-level cleaning. "
                    "Continuing with user-level modules only."
                )

        result.askpass_ok = ASKPASS_PATH.is_file() and self.privileged.askpass_available
        if not result.askpass_ok and result.privileged_ok:
            result.warnings.append(
                "No Keychain sudo credential; third-party tools that call sudo themselves "
                "(cask pkg installers, softwareupdate) will be skipped."
            )

        result.full_disk_access = has_full_disk_access()
        if not result.full_disk_access:
            result.warnings.append(
                "Full Disk Access is not granted to this process. Container, Mail and Safari "
                "caches will appear empty. See README 'Full Disk Access'."
            )

        if self.take_snapshot and result.privileged_ok:
            snapshot = self.privileged.snapshot_create()
            result.snapshot_taken = snapshot.ok
            if not snapshot.ok:
                result.warnings.append(f"Could not take a pre-run APFS snapshot: {snapshot.error}")

        return result


# --------------------------------------------------------------------------------------
# Individual probes
# --------------------------------------------------------------------------------------


def free_space(path: str = "/") -> int:
    """Free bytes on the volume containing ``path``, as the user would see it in Finder."""

    usage = shutil.disk_usage(path)
    return usage.free


def on_ac_power() -> bool:
    """
    True when the Mac is on mains power (or is a desktop).

    ``pmset -g batt`` reports the power source; desktops report 'AC Power' with no battery.
    """

    result = run(["/usr/bin/pmset", "-g", "batt"], timeout=15)

    if result.returncode != 0:
        return True  # cannot tell; do not block the run on it

    return "AC Power" in result.stdout


def has_full_disk_access() -> bool:
    """
    Probe whether this process has Full Disk Access.

    Reading the user's TCC database is the conventional test: it is protected by TCC
    itself, so a successful read means the grant is in place. Without it, the
    container/Mail/Safari modules silently see empty directories, which is far worse than
    being told up front.
    """

    probe = Path.home() / "Library/Application Support/com.apple.TCC/TCC.db"

    try:
        with probe.open("rb") as handle:
            handle.read(16)
        return True
    except (PermissionError, OSError):
        return False


def running_under_launchd() -> bool:
    """True when invoked by launchd rather than from a terminal."""

    return not os.isatty(0) and os.environ.get("XPC_SERVICE_NAME", "0") != "0"
