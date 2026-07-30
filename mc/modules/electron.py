"""
Generic Electron and Chromium cache sweeper.

This is the highest-value module on this machine. ``~/Library/Application Support`` is
74 GB, and the Electron apps in it — Ferdium 13 GB, Claude 12 GB, VS Code 5.1 GB,
Cursor 2.2 GB — keep the bulk of that in cache directories with predictable names.

Two things make this safe to do generically rather than app-by-app:

* **A strict allowlist of cache directory names.** Only directories Chromium documents as
  regenerable are removed. ``Local Storage``, ``IndexedDB``, ``Session Storage``,
  ``databases`` and ``Local State`` are excluded — those hold login sessions and app
  data, and CleanMyMac-style tools that wipe them are why people get logged out of
  everything after a "clean".
* **The protect-list still applies.** Jan.app keeps ~11 GB of LLM weights under
  Application Support; the policy hard-blocks that path and every ``*.gguf`` anywhere,
  so a generic sweep cannot reach it.

This also replaces the four hardcoded Ferdi service UUIDs in the original script, which
had already gone stale — the app is Ferdium now and the partitions are different.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from mc.registry import Context, Risk, cleanup_module

#: Directory names Chromium/Electron regenerate on demand. Nothing here holds user data.
CACHE_DIR_NAMES = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "blob_storage",
    "Crashpad/completed",
    "component_crx_cache",
    "extensions_crx_cache",
    "Service Worker/CacheStorage",
    "Service Worker/ScriptCache",
    "Application Cache",
    "CacheStorage",
    "media-cache",
    "Media Cache",
)

#: Never touched, even if an app puts them somewhere unexpected. Listed explicitly so the
#: reasoning is visible rather than implied by omission.
NEVER = ("Local Storage", "IndexedDB", "Session Storage", "databases", "Local State", "Preferences", "Cookies")

#: Apps whose Application Support directory must be skipped wholesale. The policy already
#: hard-blocks these; listing them here avoids even walking the tree.
SKIP_APPS = ("Jan", "Syncthing", "Carbon Copy Cloner", "restic", "MobileSync")


def _cache_dirs_under(root: Path) -> List[Path]:
    """Find allowlisted cache directories directly under an app's data directory."""

    found: List[Path] = []

    for name in CACHE_DIR_NAMES:
        candidate = root / name
        if candidate.is_dir():
            found.append(candidate)

    # Electron apps that host multiple services (Ferdium, Rambox, Station) put one
    # profile per service under Partitions/. This is what the original script's four
    # hardcoded service UUIDs were reaching for.
    partitions = root / "Partitions"
    if partitions.is_dir():
        for partition in partitions.iterdir():
            if partition.is_dir():
                found.extend(_cache_dirs_under(partition))

    return found


@cleanup_module(
    name="electron_apps",
    risk=Risk.STANDARD,
    title="Electron app caches",
    requires=("~/Library/Application Support",),
    tags=("apps", "cache", "core"),
)
def electron_apps(ctx: Context) -> None:
    """Sweep known-regenerable cache directories out of every Electron app."""

    support = Path("~/Library/Application Support").expanduser()
    discovered: List[Path] = []

    for app_dir in sorted(support.iterdir()):
        if not app_dir.is_dir() or app_dir.is_symlink():
            continue
        if app_dir.name in SKIP_APPS:
            continue

        discovered.extend(_cache_dirs_under(app_dir))

    if not discovered:
        return ctx.skip("no Electron cache directories found")

    with ctx.step(f"Clearing {len(discovered)} Electron cache directories") as step:
        for directory in discovered:
            step.path(str(directory))


@cleanup_module(
    name="electron_crash_reports",
    risk=Risk.STANDARD,
    title="Electron crash reports",
    requires=("~/Library/Application Support",),
    tags=("apps", "logs"),
)
def electron_crash_reports(ctx: Context) -> None:
    """Crashpad databases and stray log files left by Electron apps."""

    with ctx.step("Removing Electron crash reports and logs") as step:
        step.path(
            "~/Library/Application Support/*/Crashpad/completed/*",
            "~/Library/Application Support/*/Crashpad/reports/*",
            "~/Library/Application Support/*/logs/*.log",
            "~/Library/Application Support/*/*.log",
            "~/Library/Application Support/*/Partitions/*/Crashpad/completed/*",
        )
