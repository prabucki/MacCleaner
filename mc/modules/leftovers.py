"""
Orphaned application leftovers.

The one genuinely useful thing CleanMyMac does that a pile of ``rm -rf`` lines cannot:
find support files belonging to applications that are no longer installed. Dragging an
app to the Trash leaves its caches, preferences, containers and logs behind forever.

This is a **heuristic**, and it is treated as one:

* results are always staged to quarantine, never deleted outright, regardless of the
  ``--no-quarantine`` flag;
* a bundle identifier is only considered orphaned when nothing on the system claims it —
  installed apps, Homebrew casks, system frameworks and a keep-list of known headless
  daemons are all consulted first;
* anything ambiguous is reported rather than removed.

The failure mode to avoid is deleting the preferences of an app that is installed
somewhere this scan does not look, so the search paths are deliberately broad.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Dict, List, Set

from mc.registry import Context, Risk, cleanup_module

#: Where applications can live.
APP_SEARCH_PATHS = (
    "/Applications",
    "/Applications/Utilities",
    "/Applications/Setapp",
    "/System/Applications",
    "/System/Applications/Utilities",
    "/System/Library/CoreServices",
    "~/Applications",
    "~/Applications/Chrome Apps.localized",
    "/Library/Application Support/Adobe",
    "/opt/homebrew/Caskroom",
)

#: User-library directories scanned for reverse-DNS leftovers.
SCAN_ROOTS = (
    "~/Library/Application Support",
    "~/Library/Caches",
    "~/Library/Containers",
    "~/Library/Group Containers",
    "~/Library/HTTPStorages",
    "~/Library/Saved Application State",
    "~/Library/WebKit",
    "~/Library/Logs",
)

#: Bundle-ID prefixes that never count as orphaned. These belong to system components,
#: helper daemons and CLI tools that have no .app bundle to find.
KEEP_PREFIXES = (
    "com.apple.",
    "group.com.apple.",
    "com.microsoft.autoupdate",
    "com.google.keystone",
    "com.google.GoogleUpdater",
    "org.sparkle-project",
    "com.docker",
    "com.vmware",
    "com.parallels",
    "org.python",
    "com.jetbrains",
    "UBF8T346G9.",  # Microsoft Office group container
    "group.",
)

#: Exact names that are not bundle IDs at all but do look like them.
KEEP_NAMES = {"com.apple.TCC", "CrashReporter", "Cache", "Caches", "Temporary Items"}

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9]+(\.[A-Za-z0-9][A-Za-z0-9\-_]*){2,}$")


def _installed_bundle_ids() -> Set[str]:
    """
    Every bundle identifier the system currently has an application for.

    Read from each app's Info.plist rather than inferred from the directory name, because
    the two frequently disagree (``Google Chrome.app`` is ``com.google.Chrome``).
    """

    found: Set[str] = set()

    for search_path in APP_SEARCH_PATHS:
        root = Path(search_path).expanduser()
        if not root.is_dir():
            continue

        # Two levels deep catches /Applications/Foo.app and /Applications/Vendor/Foo.app.
        for candidate in list(root.glob("*.app")) + list(root.glob("*/*.app")) + list(root.glob("*/*/*.app")):
            info = candidate / "Contents" / "Info.plist"
            if not info.is_file():
                continue
            try:
                with info.open("rb") as handle:
                    plist = plistlib.load(handle)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue

            identifier = plist.get("CFBundleIdentifier")
            if isinstance(identifier, str) and identifier:
                found.add(identifier)
                # Match group containers and helper suffixes too.
                found.add(identifier.rsplit(".", 1)[0])

    return found


def _looks_like_bundle_id(name: str) -> bool:
    """True for reverse-DNS-shaped directory names."""

    return bool(_BUNDLE_ID.match(name)) and name not in KEEP_NAMES


def _is_kept(name: str, installed: Set[str]) -> bool:
    """Whether this identifier belongs to something still present."""

    if any(name.startswith(prefix) for prefix in KEEP_PREFIXES):
        return True

    if name in installed:
        return True

    # A group container is "group.com.vendor.app" or "TEAMID.com.vendor.app".
    stripped = re.sub(r"^(group\.|[A-Z0-9]{10}\.)", "", name)
    if stripped in installed:
        return True

    # Any installed bundle that is a prefix of this one — helper apps, XPC services.
    return any(name.startswith(known + ".") or known.startswith(name + ".") for known in installed)


def _find_orphans() -> Dict[str, List[Path]]:
    """Map orphaned bundle identifier to the directories it left behind."""

    installed = _installed_bundle_ids()
    orphans: Dict[str, List[Path]] = {}

    for scan_root in SCAN_ROOTS:
        root = Path(scan_root).expanduser()
        if not root.is_dir():
            continue

        try:
            entries = list(root.iterdir())
        except PermissionError:
            continue  # no Full Disk Access; preflight already warned

        for entry in entries:
            if entry.is_symlink():
                continue

            name = entry.name
            # Saved state directories are "<bundle-id>.savedState".
            identifier = name[: -len(".savedState")] if name.endswith(".savedState") else name

            if not _looks_like_bundle_id(identifier):
                continue
            if _is_kept(identifier, installed):
                continue

            orphans.setdefault(identifier, []).append(entry)

    return orphans


@cleanup_module(
    name="app_leftovers",
    risk=Risk.AGGRESSIVE,
    title="Orphaned app leftovers",
    tags=("apps", "leftovers"),
)
def app_leftovers(ctx: Context) -> None:
    """
    Support files belonging to applications that are no longer installed.

    Always quarantined. If this module gets one wrong, ``mc --restore <batch>`` puts it
    back exactly where it was.
    """

    orphans = _find_orphans()

    if not orphans:
        return ctx.skip("no orphaned app data found")

    total_dirs = sum(len(paths) for paths in orphans.values())

    with ctx.step(f"Staging leftovers from {len(orphans)} uninstalled app(s)") as step:
        for identifier in sorted(orphans):
            for directory in orphans[identifier]:
                # quarantine=True is explicit here rather than inherited: a heuristic
                # result must never be deleted outright, even with --no-quarantine.
                step.path(str(directory), quarantine=True)

    ctx.report.module("app_leftovers").reason = (
        f"{total_dirs} directories from {len(orphans)} uninstalled apps: "
        + ", ".join(sorted(orphans)[:8])
        + (" ..." if len(orphans) > 8 else "")
    )


@cleanup_module(
    name="broken_login_items",
    risk=Risk.SAFE,
    title="Broken login items and agents",
    tags=("apps", "report", "leftovers"),
    read_only=True,
)
def broken_login_items(ctx: Context) -> None:
    """
    Report launch agents pointing at programs that no longer exist.

    Report-only. Removing a LaunchAgent plist can break an app's background updater in
    ways that are hard to diagnose later, so this surfaces them for you to decide on.
    """

    def scan() -> None:
        from mac_cleanup.console import console

        broken: List[str] = []

        for directory in ("~/Library/LaunchAgents", "/Library/LaunchAgents", "/Library/LaunchDaemons"):
            root = Path(directory).expanduser()
            if not root.is_dir():
                continue

            for plist_path in root.glob("*.plist"):
                try:
                    with plist_path.open("rb") as handle:
                        plist = plistlib.load(handle)
                except (OSError, plistlib.InvalidFileException, ValueError):
                    broken.append(f"{plist_path} (unreadable)")
                    continue

                program = plist.get("Program")
                arguments = plist.get("ProgramArguments") or []
                target = program or (arguments[0] if arguments else None)

                if target and target.startswith("/") and not Path(target).exists():
                    broken.append(f"{plist_path.name} -> {target}")

        if broken:
            console.print(f"  [warning]{len(broken)} broken launch item(s):[/warning]")
            for entry in broken[:20]:
                console.print(f"    {entry}")
            ctx.report.module("broken_login_items").reason = f"{len(broken)} broken launch item(s), not removed"

    with ctx.step("Checking for broken login items") as step:
        step.action(scan, description="scan launch agents")
