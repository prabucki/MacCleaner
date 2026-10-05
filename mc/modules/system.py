"""
Trash, snapshots and system maintenance.

The maintenance half is the OnyX equivalent: rebuild the databases that go stale,
flush caches that need an explicit poke.

Retired on macOS 27 (Golden Gate), 2026-09-24: periodic_scripts (/usr/sbin/periodic
and /etc/periodic are gone, and the module reported ok while running nothing) and
kext_cache (the system volume is sealed read-only, so `touch /System/Library/Extensions`
fails and kextcache -u / has nothing to rebuild; kmutil owns kext collections).
"""

from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path

from mc.registry import Context, Risk, cleanup_module
from mc.util import MC_HOME, human, run


@cleanup_module(name="trash", risk=Risk.SAFE, title="Trash", tags=("core",))
def trash(ctx: Context) -> None:
    """Empty the Trash on the boot volume and every mounted volume."""

    with ctx.step("Emptying the Trash") as step:
        step.path("~/.Trash/*")
        step.path("/Volumes/*/.Trashes/*", privileged=True)


@cleanup_module(
    name="local_snapshots",
    risk=Risk.AGGRESSIVE,
    title="Time Machine local snapshots",
    tags=("system", "privileged"),
)
def local_snapshots(ctx: Context) -> None:
    """
    Delete Time Machine local snapshots.

    A classic cause of "I deleted 50 GB and free space did not move": snapshots pin the
    old blocks and are invisible as files. macOS thins them automatically under disk
    pressure, but only under pressure.

    The most recent snapshot is kept — it is the one you would actually want if
    something went wrong today.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    snapshots = ctx.privileged.list_snapshots()

    if len(snapshots) <= 1:
        return ctx.skip(f"{len(snapshots)} snapshot(s); nothing worth removing")

    # Sorted names are chronological: com.apple.TimeMachine.<date>-<time>.local
    stale = sorted(snapshots)[:-1]

    with ctx.step(f"Deleting {len(stale)} stale local snapshot(s)") as step:
        for name in stale:
            step.root("delete_snapshot", name)


@cleanup_module(
    name="memory_purge",
    risk=Risk.STANDARD,
    title="Purge inactive memory",
    tags=("system", "maintenance", "privileged"),
)
def memory_purge(ctx: Context) -> None:
    """Force inactive memory back to the free pool. Harmless; briefly slows things down."""

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Purging inactive memory") as step:
        step.root("purge_memory")


@cleanup_module(
    name="dns_cache",
    risk=Risk.SAFE,
    title="DNS cache",
    tags=("system", "maintenance", "privileged"),
)
def dns_cache(ctx: Context) -> None:
    """Flush the DNS resolver cache and restart mDNSResponder."""

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Flushing DNS cache") as step:
        step.root("flush_dns")


@cleanup_module(
    name="launch_services",
    risk=Risk.AGGRESSIVE,
    title="Launch Services database",
    tags=("system", "maintenance", "privileged"),
)
def launch_services(ctx: Context) -> None:
    """
    Rebuild the Launch Services database.

    Fixes duplicate entries in 'Open With' and wrong default apps. Rebuilding it takes a
    minute and the Dock/Finder may flicker.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Rebuilding Launch Services database") as step:
        step.root("rebuild_launch_services")


#: Apple Intelligence model sets to reset, UAF asset-set name -> the asset type the
#: service must report for it. A mismatch means Apple remapped the set, and resetting
#: by name could then hit something else, so the set is refused. Xcode's code completion
#: set (com.apple.MobileAsset.UAF.FM.CodeLM) is deliberately left out: it is a tool in
#: use here, not a dormant feature.
AI_MODEL_SETS = {
    "com.apple.modelcatalog": "com.apple.MobileAsset.UAF.FM.GenerativeModels",
    "com.apple.MobileAsset.UAF.FM.Visual": "com.apple.MobileAsset.UAF.FM.Visual",
    "com.apple.MobileAsset.UAF.Photos.SpatialPhotosRelive": "com.apple.MobileAsset.UAF.Photos.SpatialPhotosRelive",
    "com.apple.MobileAsset.UAF.Photos.MagicCleanup": "com.apple.MobileAsset.UAF.Photos.MagicCleanup",
}

#: Where the download block lands once the RemoveMacAI-style profile (kept in MyMacSetup)
#: is installed: a forced DownloadServerBaseURLOverride-<asset type> per set.
MANAGED_MOBILEASSET = "/Library/Managed Preferences/com.apple.MobileAsset.plist"

UAFRESET_SOURCE = Path(__file__).resolve().parent.parent / "native" / "uafreset.m"


def _sip_disabled() -> bool:
    return "status: disabled" in run(["/usr/bin/csrutil", "status"], timeout=30).stdout


def _downloads_blocked() -> bool:
    """True when the profile forces every set's download server to a dead address."""

    try:
        with open(MANAGED_MOBILEASSET, "rb") as handle:
            forced = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return False

    return all(f"DownloadServerBaseURLOverride-{kind}" in forced for kind in AI_MODEL_SETS.values())


def _asset_dir(asset_type: str) -> str:
    return "/System/Library/AssetsV2/" + asset_type.replace(".", "_")


def _disk_usage(paths) -> int:
    existing = [p for p in paths if os.path.isdir(p)]
    if not existing:
        return 0
    result = run(["/usr/bin/du", "-sk", *existing], timeout=120)
    return sum(int(line.split("\t", 1)[0]) * 1024 for line in result.stdout.splitlines() if line.strip())


def _uafreset() -> Path:
    """Build the helper on first use, and again whenever its source changes."""

    binary = MC_HOME / "bin" / "uafreset"
    if binary.exists() and binary.stat().st_mtime >= UAFRESET_SOURCE.stat().st_mtime:
        return binary

    binary.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["/usr/bin/xcrun", "clang", "-fobjc-arc", "-O2", "-framework", "Foundation",
         "-o", str(binary), str(UAFRESET_SOURCE)],
        timeout=120,
        check=True,
    )
    return binary


@cleanup_module(
    name="apple_intelligence_models",
    risk=Risk.AGGRESSIVE,
    title="Apple Intelligence models",
    tags=("system",),
)
def apple_intelligence_models(ctx: Context) -> None:
    """
    Have macOS delete the Apple Intelligence models, ~14 GB, through its asset service.

    Deleting the files cannot work: each model's .AssetData is a read-only cryptex graft,
    so even root gets EROFS (23 errors on 2026-10-05). The asset service that grafted
    them tears them down on a ResetAssetSets call — the mechanism RemoveMacAI uses — via
    the small helper in mc/native/uafreset.m. Measured 2026-10-05: 13.45 GB across 100
    model folders gone in 3.4 s, no root needed.

    Runs only when the models cannot simply come back: SIP off (Apple Intelligence is
    unavailable on macOS 27 then) or the download-block profile installed.
    """

    if not (_sip_disabled() or _downloads_blocked()):
        return ctx.skip("Apple Intelligence is usable and would re-download them; install the download-block profile")

    # The service leaves an empty purpose_auto/ behind, so a set counts only while it
    # still holds model assets; otherwise every run would call the service for nothing.
    present = {
        name: kind for name, kind in AI_MODEL_SETS.items() if any(Path(_asset_dir(kind)).glob("*/*.asset"))
    }
    if not present:
        return ctx.skip("no model sets on disk")

    def reset() -> None:
        result = ctx.report.module(ctx.name)
        helper = _uafreset()

        status = json.loads(run([str(helper), "status", *present], timeout=60, check=True).stdout)
        matched = []
        for name, kind in present.items():
            reported = status.get(name, {}).get("type")
            if reported == kind:
                matched.append(name)
            else:
                result.errors.append(f"{name}: service maps it to {reported!r}, expected {kind!r}; not reset")
        if not matched:
            return

        dirs = [_asset_dir(present[name]) for name in matched]
        before = _disk_usage(dirs)
        reply = run([str(helper), "reset", *matched], timeout=150 * len(matched))
        try:
            outcome = json.loads(reply.stdout)
        except ValueError:
            outcome = {name: (reply.stderr.strip() or f"exit {reply.returncode}") for name in matched}
        for name in matched:
            if outcome.get(name):
                result.errors.append(f"{name}: {outcome[name]}")

        result.bytes_reclaimed += max(0, before - _disk_usage(dirs))

    with ctx.step("Asking macOS to remove the Apple Intelligence models") as step:
        step.action(reset, description="uafreset")


@cleanup_module(
    name="sleep_image",
    risk=Risk.NUCLEAR,
    title="Sleep image",
    tags=("system", "privileged"),
)
def sleep_image(ctx: Context) -> None:
    """
    Remove the hibernation image.

    Equal in size to installed RAM. Regenerated on the next hibernate, so this is a
    one-time reclaim that comes back — worth doing on a machine that never hibernates.

    Nuclear tier since 2026-09-24, i.e. off by default. This Mac runs hibernatemode 3,
    which rewrites the image at every sleep, so the weekly run freed nothing: each pass
    staged another 2 GB copy to quarantine, and four same-day runs held 8 GiB of stale
    RAM images for the 7-day retention. Run it with --only if hibernation is ever off.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Removing sleep image") as step:
        step.root_path("/private/var/vm/sleepimage")


@cleanup_module(
    name="disk_health",
    risk=Risk.SAFE,
    title="Disk verification",
    tags=("system", "report"),
    read_only=True,
)
def disk_health(ctx: Context) -> None:
    """
    Verify the boot volume and report. Read-only — never repairs anything.

    OnyX's 'Verify' equivalent. A real repair needs the volume unmounted, which means
    Recovery, which is not something a headless tool should attempt.
    """

    def verify() -> None:
        from mac_cleanup.console import console

        from mc.util import run

        result = run(["/usr/sbin/diskutil", "verifyVolume", "/"], timeout=900)
        report = ctx.report.module("disk_health")

        if "appears to be OK" in result.stdout:
            console.print("  [success]Boot volume verified OK[/success]")
        else:
            tail = [line for line in result.stdout.splitlines() if line.strip()][-3:]
            report.errors.append("verifyVolume: " + " | ".join(tail))
            console.print("  [warning]Boot volume verification reported issues (see report)[/warning]")

    with ctx.step("Verifying the boot volume") as step:
        step.action(verify, description="diskutil verifyVolume /")


@cleanup_module(
    name="spotlight_index",
    risk=Risk.NUCLEAR,
    title="Spotlight index rebuild",
    tags=("system", "maintenance"),
)
def spotlight_index(ctx: Context) -> None:
    """
    Force a full Spotlight reindex.

    Nuclear tier because of the cost, not the risk: reindexing a 900 GB volume pins a
    core for hours and makes search useless until it finishes. Only worth it when
    Spotlight is genuinely broken.
    """

    with ctx.step("Rebuilding the Spotlight index") as step:
        step.command(["/usr/bin/mdutil", "-E", "/"], timeout=300)


@cleanup_module(
    name="finder_restart",
    risk=Risk.AGGRESSIVE,
    title="Restart Finder and Dock",
    tags=("system", "maintenance"),
)
def finder_restart(ctx: Context) -> None:
    """
    Restart the Dock, Finder and menu bar.

    Picks up the cache and database rebuilds above. Cosmetically disruptive — windows
    flicker and Finder windows close — so it sits at aggressive and runs last.
    """

    with ctx.step("Restarting Finder, Dock and menu bar") as step:
        step.command(["/usr/bin/killall", "Dock"], timeout=30)
        step.command(["/usr/bin/killall", "Finder"], timeout=30)
        step.command(["/usr/bin/killall", "SystemUIServer"], timeout=30)

