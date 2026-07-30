"""
Trash, snapshots and system maintenance.

The maintenance half is the OnyX equivalent: run the system's own periodic scripts,
rebuild the databases that go stale, flush caches that need an explicit poke.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module
from mc.util import human


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
    name="periodic_scripts",
    risk=Risk.STANDARD,
    title="System maintenance scripts",
    tags=("system", "maintenance", "privileged"),
)
def periodic_scripts(ctx: Context) -> None:
    """
    Run macOS's own daily/weekly/monthly maintenance.

    These rotate logs and rebuild the locate/whatis databases. They are scheduled to run
    overnight, so a Mac that sleeps at night may never run them at all — which is exactly
    the gap OnyX's 'Automation' tab exists to fill.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Running periodic maintenance scripts") as step:
        step.root("periodic", "daily", "weekly", "monthly")


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


@cleanup_module(
    name="kext_cache",
    risk=Risk.NUCLEAR,
    title="Kernel extension cache",
    tags=("system", "maintenance", "privileged"),
)
def kext_cache(ctx: Context) -> None:
    """
    Rebuild the kernel extension cache.

    Nuclear tier, and the honest reason is that this touches the boot path. On a modern
    Apple Silicon Mac with no third-party kexts it is a no-op at best; if it goes wrong
    it goes wrong at boot. The original script ran it unconditionally. Enable it only if
    you actually have third-party kernel extensions misbehaving.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Rebuilding kernel extension cache") as step:
        step.root("rebuild_kextcache")


@cleanup_module(
    name="sleep_image",
    risk=Risk.AGGRESSIVE,
    title="Sleep image",
    tags=("system", "privileged"),
)
def sleep_image(ctx: Context) -> None:
    """
    Remove the hibernation image.

    Equal in size to installed RAM. Regenerated on the next hibernate, so this is a
    one-time reclaim that comes back — worth doing on a machine that never hibernates.
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
