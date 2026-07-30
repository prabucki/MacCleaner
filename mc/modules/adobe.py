"""
Adobe Creative Cloud.

6.1 GB of Application Support and 322 MB of Caches on this machine, and the media caches
grow without bound — Premiere and After Effects never prune them on their own.

The original script hardcoded ``/Applications/Adobe Illustrator 2025/...``; those rules
are generalised to a version glob here so they keep working after an upgrade.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module

#: Adobe apps that must not be running when their caches are removed.
ADOBE_PROCESSES = ("Adobe Premiere Pro 2025", "Adobe After Effects 2025", "Adobe Media Encoder 2025")


@cleanup_module(
    name="adobe_media_cache",
    risk=Risk.STANDARD,
    title="Adobe media caches",
    requires_any=(
        "~/Library/Application Support/Adobe/Common/Media Cache Files",
        "~/Library/Application Support/Adobe",
    ),
    tags=("apps", "adobe"),
)
def adobe_media_cache(ctx: Context) -> None:
    """
    Premiere/After Effects media cache, peak files and conformed audio.

    Regenerated on demand when a project is reopened. Reconforming a long timeline takes
    a few minutes, which is the entire cost.
    """

    running = [name for name in ADOBE_PROCESSES if ctx.is_running(name)]
    if running:
        return ctx.skip(f"{', '.join(running)} is running")

    with ctx.step("Clearing Adobe media caches") as step:
        step.path(
            "~/Library/Application Support/Adobe/Common/Media Cache Files/*",
            "~/Library/Application Support/Adobe/Common/Media Cache/*",
            "~/Library/Application Support/Adobe/Common/Peak Files/*",
            "~/Library/Application Support/Adobe/Common/Team Projects Cache/*",
        )

    with ctx.step("Clearing After Effects and Premiere disk caches") as step:
        step.path(
            "~/Library/Caches/Adobe/After Effects/*/*",
            "~/Library/Caches/Adobe/Common/*",
        )
        # A relocated AE disk cache under ~/Documents is deliberately not handled at all.
        # That is user territory, and telling a project folder from a cache folder there
        # needs AE's own preferences, which is more guesswork than a cleaner should do.


@cleanup_module(
    name="adobe_caches",
    risk=Risk.STANDARD,
    title="Adobe caches and logs",
    requires=("~/Library/Application Support/Adobe",),
    tags=("apps", "adobe"),
)
def adobe_caches(ctx: Context) -> None:
    """Camera Raw cache, Creative Cloud logs, and the telemetry store."""

    with ctx.step("Clearing Adobe caches") as step:
        step.path(
            "~/Library/Caches/Adobe/*",
            "~/Library/Caches/Adobe Camera Raw*/*",
            "~/Library/Application Support/Adobe/Camera Raw/Cache/*",
            "~/Library/Application Support/Adobe/Common/AdobePIM.db-journal",
            # 550 MB of telemetry on this machine.
            "~/Library/Application Support/com.adobe.dunamis/*",
            "~/Library/Application Support/Adobe/AAMUpdater/*",
            "~/Library/Application Support/Adobe/OOBE/*.log",
        )

    with ctx.step("Clearing Adobe logs") as step:
        step.path(
            "~/Library/Logs/Adobe/*",
            "~/Library/Logs/CreativeCloud/*",
            "~/Library/Logs/adobegc.log",
            "~/Library/Application Support/Adobe/ARMDC/Application/Logs/*",
        )

        if ctx.privileged.available:
            step.root_path(
                "/Library/Logs/Adobe/*",
                "/Library/Logs/CreativeCloud/*",
                "/Library/Logs/adobegc.log",
            )


@cleanup_module(
    name="adobe_shared",
    risk=Risk.AGGRESSIVE,
    title="Adobe shared installers and tutorials",
    requires_any=("/Users/Shared/Adobe", "/Applications/Adobe Illustrator*"),
    tags=("apps", "adobe", "privileged"),
)
def adobe_shared(ctx: Context) -> None:
    """
    Shipped sample content and installer leftovers.

    Includes the Premiere Pro tutorial project (~166 MB) the original script called out,
    and the Illustrator sample AppleScripts that show up as spurious applications in
    Spotlight and Launchpad. Version numbers are globbed rather than hardcoded.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Removing Adobe shared leftovers") as step:
        step.root_path(
            "/Users/Shared/Adobe/Premiere Pro/*/*",
            "/Users/Shared/Adobe/Installers/*",
            "/Users/Shared/AdobeGCInfo/*",
            "/Library/Logs/CreativeCloud/*",
        )

    # Illustrator's sample scripts register themselves as applications; removing them is
    # cosmetic but was in the original script for good reason.
    sample_scripts = ctx.glob("/Applications/Adobe Illustrator */Scripting.localized/Sample Scripts.localized")

    if sample_scripts:
        with ctx.step("Removing Illustrator sample scripts") as step:
            for base in sample_scripts:
                for name in ("Web Gallery.localized", "Calendar.localized", "Contact Sheet Demo.localized"):
                    step.path(f"{base}/AppleScript.localized/{name}", privileged=True)
