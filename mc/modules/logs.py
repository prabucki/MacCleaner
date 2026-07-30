"""
Logs, crash reports and diagnostics.

Worth calling out: the original script's ``rm -rf /private/var/log/*`` never actually
reclaimed the big one. Since macOS 10.12 the bulk of logging goes into the *unified log*
store under ``/private/var/db/diagnostics``, which is managed by ``logd`` and reappears
immediately if you delete the files underneath it. ``log erase --all`` is the supported
way to reclaim it, and it is routinely several gigabytes.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module


@cleanup_module(
    name="user_logs",
    risk=Risk.STANDARD,
    title="User logs",
    tags=("logs", "core"),
)
def user_logs(ctx: Context) -> None:
    """Per-user logs and crash reports."""

    with ctx.step("Clearing user logs") as step:
        step.path(
            "~/Library/Logs/*",
            "~/Library/Application Support/CrashReporter/*",
            "~/Library/Logs/DiagnosticReports/*",
            "~/Library/Logs/CoreSimulator/*",
            "~/Library/Logs/JetBrains/*/*.log",
        )

    with ctx.step("Removing crash and hang reports") as step:
        step.path(
            "~/Library/Logs/DiagnosticReports/*.crash",
            "~/Library/Logs/DiagnosticReports/*.diag",
            "~/Library/Logs/DiagnosticReports/*.hang",
            "~/Library/Logs/DiagnosticReports/*.spin",
            "~/Library/Logs/DiagnosticReports/*.ips",
        )


@cleanup_module(
    name="system_logs",
    risk=Risk.AGGRESSIVE,
    title="System logs",
    tags=("logs", "system", "privileged"),
)
def system_logs(ctx: Context) -> None:
    """Root-owned logs under /Library and /var."""

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Clearing system logs") as step:
        step.root_path(
            "/Library/Logs/*",
            "/private/var/log/*",
            "/private/var/log/asl/*.asl",
            "/Library/Logs/DiagnosticReports/*",
            "/Library/Application Support/CrashReporter/*",
        )


@cleanup_module(
    name="unified_log",
    risk=Risk.AGGRESSIVE,
    title="Unified log store",
    tags=("logs", "system", "privileged"),
)
def unified_log(ctx: Context) -> None:
    """
    Erase the unified log store.

    Frequently the largest single item on this list and completely invisible in Finder,
    which is why deleting files under /private/var/log appeared to free nothing.

    Cost: you lose historical `log show` data. If you are actively debugging something,
    skip this module.
    """

    if not ctx.privileged.available:
        return ctx.skip(f"needs root: {ctx.privileged.unavailable_reason}")

    with ctx.step("Erasing the unified log store") as step:
        # Measured before erasing so the report can attribute the reclaim.
        step.measure("/private/var/db/diagnostics", "/private/var/db/uuidtext")
        step.root("erase_unified_log")


@cleanup_module(
    name="sysdiagnose",
    risk=Risk.STANDARD,
    title="Sysdiagnose archives",
    tags=("logs", "system"),
)
def sysdiagnose(ctx: Context) -> None:
    """
    Sysdiagnose bundles.

    Created when you press the diagnostic key combination or when Apple support asks for
    one. Hundreds of megabytes each and almost always forgotten about afterwards.
    """

    with ctx.step("Removing sysdiagnose archives") as step:
        step.path("~/Library/Logs/sysdiagnose_*", "/private/var/tmp/sysdiagnose_*")

        if ctx.privileged.available:
            step.root_path("/private/var/log/sysdiagnose_*")
