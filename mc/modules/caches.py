"""
Cache cleanup — user-level and system-level.

The single biggest reclaim on most machines, and the part of the original
MasterCleanScript that was repeated three times over.

One correction from that script: it hardcoded a per-boot temporary directory
(``/private/var/folders/1v/x2bn002s3cz0c5jc0g7sy9bh0000gn/C``). That path is derived from
the user's UUID and changes; the glob here finds it wherever it is.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module


@cleanup_module(
    name="user_caches",
    risk=Risk.STANDARD,
    title="User caches",
    tags=("cache", "core"),
)
def user_caches(ctx: Context) -> None:
    """``~/Library/Caches`` and the other per-user cache stores."""

    with ctx.step("Clearing user caches") as step:
        step.path(
            "~/Library/Caches/*",
            "~/.cache/*",
            "~/Library/Application Support/Caches/*",
            # Compiled Core ML models. Regenerated on demand; 3.5 GB on this machine.
            "~/Library/Application Support/coreMLCache/*",
        )

    with ctx.step("Clearing per-app HTTP and web caches") as step:
        step.path(
            "~/Library/HTTPStorages/*/*.cache",
            "~/Library/WebKit/*/WebsiteData/ResourceLoadStatistics/*",
            "~/Library/Caches/com.apple.Safari/WebKitCache/*",
            "~/Library/Caches/CloudKit/*",
            "~/Library/Caches/GeoServices/*",
            "~/Library/Caches/com.apple.helpd/*",
            "~/Library/Caches/com.apple.akd/*",
            "~/Library/Caches/com.apple.iCloudHelper/*",
        )


@cleanup_module(
    name="container_caches",
    risk=Risk.STANDARD,
    title="Sandboxed app caches",
    requires=("~/Library/Containers",),
    tags=("cache", "core"),
)
def container_caches(ctx: Context) -> None:
    """
    Caches inside sandboxed app containers.

    This is the loop at the bottom of the original script, expressed as a glob. Requires
    Full Disk Access to see anything; preflight warns when that is missing rather than
    letting the module quietly report zero.
    """

    with ctx.step("Clearing sandboxed app caches") as step:
        step.path(
            "~/Library/Containers/*/Data/Library/Caches/*",
            "~/Library/Containers/*/Data/Library/Logs/*",
            "~/Library/Group Containers/*/Library/Caches/*",
        )


@cleanup_module(
    name="saved_state",
    risk=Risk.AGGRESSIVE,
    title="Saved application state",
    tags=("cache",),
)
def saved_state(ctx: Context) -> None:
    """
    Window/document restore state.

    Safe to remove — apps reopen with default windows instead of restoring the last
    session. Aggressive tier because it is a visible behaviour change, not a risk.
    """

    with ctx.step("Clearing saved application state") as step:
        step.path("~/Library/Saved Application State/*.savedState")


@cleanup_module(
    name="system_caches",
    risk=Risk.AGGRESSIVE,
    title="System caches",
    tags=("cache", "system", "privileged"),
)
def system_caches(ctx: Context) -> None:
    """
    Root-owned caches outside the user's home.

    Everything here goes through the root helper and is on its allowlist.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Clearing system caches") as step:
        step.root_path(
            "/Library/Caches/*",
            "/System/Library/Caches/*",
        )

    with ctx.step("Clearing per-boot temporary caches") as step:
        # The original script hardcoded one machine's UUID here. This finds it anywhere.
        step.root_path(
            "/private/var/folders/*/*/C/*",
            "/private/var/folders/*/*/T/*",
        )

    with ctx.step("Clearing CoreDuet and boot caches") as step:
        step.root_path(
            "/private/var/db/coreduet/*",
            "/private/var/db/BootCache.playlist",
        )


@cleanup_module(
    name="font_cache",
    risk=Risk.AGGRESSIVE,
    title="Font caches",
    tags=("system", "maintenance"),
)
def font_cache(ctx: Context) -> None:
    """
    Rebuild font caches — the OnyX 'Rebuild' equivalent.

    Fixes garbled or missing fonts in apps. Rebuilt lazily, so the only cost is a slower
    first launch afterwards.
    """

    atsutil = (
        "/System/Library/Frameworks/ApplicationServices.framework/Versions/A/Frameworks/"
        "ATS.framework/Versions/A/Support/atsutil"
    )

    with ctx.step("Resetting font caches") as step:
        step.command([atsutil, "databases", "-removeUser"], timeout=120)

        if ctx.privileged.available:
            step.root("reset_font_cache")

        step.command([atsutil, "server", "-shutdown"], timeout=60)
        step.command([atsutil, "server", "-ping"], timeout=60)


@cleanup_module(
    name="quicklook_cache",
    risk=Risk.STANDARD,
    title="QuickLook thumbnail cache",
    tags=("cache", "maintenance"),
)
def quicklook_cache(ctx: Context) -> None:
    """Reset the QuickLook thumbnail cache — fixes blank or wrong Finder previews."""

    with ctx.step("Resetting QuickLook cache") as step:
        step.command(["/usr/bin/qlmanage", "-r", "cache"], timeout=120)
        step.path("~/Library/Caches/com.apple.QuickLook.thumbnailcache/*")
