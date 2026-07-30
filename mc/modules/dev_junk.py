"""
Filesystem junk and React Native / Metro caches.

This file exists to absorb the last of ``~/Drive/Macbook/mac-scripts``, whose
``cleanup_DS_Store.sh``, the ``/tmp`` half of ``cleanup_cache.sh``, and the global
portions of ``clean_react_native.sh`` had no equivalent here.

Both of the originals were blunter than they needed to be:

* ``find "$HOME" -name .DS_Store -delete`` swept the entire home directory including
  Downloads. Downloads is hard-protected here, and the scan skips heavy or irrelevant
  trees rather than walking 74 GB of Application Support.
* ``sudo rm -rf /tmp/*`` deletes sockets and lock files belonging to *running*
  processes. This version only removes entries that have not been touched for days.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List

from mc.registry import Context, Risk, cleanup_module

#: Trees not worth walking for .DS_Store: they either cannot contain them, are protected
#: anyway, or are large enough that scanning costs more than the bytes reclaimed.
DS_STORE_PRUNE = {
    ".Trash",
    "Downloads",  # hard-protected; not even scanned
    "Library",
    # Credential stores. Hard-protected, so a .DS_Store found here would be refused
    # anyway; skipping the walk keeps the run report free of pointless denials.
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".nvm",
    ".cargo",
    ".rustup",
    "go",
    ".gradle",
    ".m2",
    "Parallels",
    "Applications",
}

#: Entries in the temp directories must be older than this to be removed. Anything newer
#: may belong to a process that is still running.
TEMP_AGE_DAYS = 3


@cleanup_module(
    name="ds_store",
    risk=Risk.STANDARD,
    title=".DS_Store files",
    tags=("junk", "core"),
)
def ds_store(ctx: Context) -> None:
    """
    Remove ``.DS_Store`` files from the home directory.

    Individually tiny, collectively thousands of files. Finder recreates them for folders
    you actually browse, so this is a genuine no-loss cleanup — it only resets custom
    icon positions and per-folder view settings.
    """

    home = Path.home()
    found: List[Path] = []

    for root, dirs, files in os.walk(home, topdown=True, followlinks=False):
        # Prune in place so os.walk never descends into them.
        dirs[:] = [
            d
            for d in dirs
            if d not in DS_STORE_PRUNE
            and not d.endswith((".app", ".photoslibrary", ".pvm", ".sparsebundle", ".fcpbundle"))
            and not os.path.ismount(os.path.join(root, d))
        ]

        if ".DS_Store" in files:
            found.append(Path(root) / ".DS_Store")

    if not found:
        return ctx.skip("no .DS_Store files found")

    with ctx.step(f"Removing {len(found)} .DS_Store files") as step:
        for path in found:
            # ~/Documents, ~/Desktop and ~/Pictures are soft-protected, and most
            # .DS_Store files live in exactly those places. The override is scoped to
            # individual files literally named .DS_Store — never a glob — so it cannot
            # widen to anything else. Downloads is hard-protected and is not even walked.
            step.path(str(path), override="Finder view metadata, recreated on demand")


@cleanup_module(
    name="temp_files",
    risk=Risk.AGGRESSIVE,
    title="Stale temporary files",
    tags=("junk", "system", "privileged"),
)
def temp_files(ctx: Context) -> None:
    """
    Clear ``/tmp`` and ``/var/tmp`` of entries older than a few days.

    The original ``sudo rm -rf /tmp/*`` was a real hazard: ``/tmp`` holds active unix
    sockets, PID files and lock files, and deleting them under a running process causes
    failures that are very hard to trace back to a cleanup script. The age filter means
    anything currently in use is left alone.
    """

    cutoff = time.time() - (TEMP_AGE_DAYS * 86400)
    stale: List[Path] = []

    for directory in ("/private/tmp", "/private/var/tmp"):
        root = Path(directory)
        if not root.is_dir():
            continue

        try:
            entries = list(root.iterdir())
        except PermissionError:
            continue

        for entry in entries:
            # Never touch macOS's own scaffolding in /tmp.
            if entry.name.startswith((".keystone", "com.apple.")) or entry.name in ("KSOutOfProcessFetcher",):
                continue

            try:
                if entry.lstat().st_mtime < cutoff:
                    stale.append(entry)
            except OSError:
                continue

    if not stale:
        return ctx.skip(f"nothing in /tmp older than {TEMP_AGE_DAYS} days")

    with ctx.step(f"Removing {len(stale)} stale temporary item(s)") as step:
        for entry in stale:
            step.path(str(entry), privileged=True)


@cleanup_module(
    name="react_native",
    risk=Risk.STANDARD,
    title="React Native and Metro caches",
    requires_any=("~/.metro", "~/.rncache", "~/.flipper", "~/Library/Caches/org.reactjs.native.packager"),
    tags=("dev", "node"),
)
def react_native(ctx: Context) -> None:
    """
    Global React Native tooling caches.

    The machine-wide half of ``clean_react_native.sh``. Per-project artifacts
    (``node_modules``, ``ios/Pods``, Gradle build output) are handled by
    ``mc --project-clean <dir>``, which is the right scope for them — a global cleaner
    has no business guessing which of your checkouts you want rebuilt.
    """

    with ctx.step("Clearing React Native caches") as step:
        step.path(
            "~/.metro/*",
            "~/.rncache/*",
            "~/.flipper/*",
            "~/Library/Caches/org.reactjs.native.packager/*",
            "~/Library/Caches/com.facebook.ReactNativeBuild/*",
            "/private/tmp/metro-*",
            "/private/tmp/react-*",
            "/private/tmp/haste-map-*",
        )

    if ctx.which("watchman"):
        with ctx.step("Resetting watchman watches") as step:
            # Watchman keeps state for directories that may no longer exist, and its
            # state directory grows without bound.
            step.command([ctx.which("watchman"), "watch-del-all"], timeout=120)
            step.command([ctx.which("watchman"), "shutdown-server"], timeout=60)
            step.path("/private/var/usr/local/var/run/watchman/*-state/*")
