"""
``mc`` — the headless entry point.

Drives upstream's collector directly rather than going through ``mac_cleanup.main``,
which clears the console, opens an interactive module picker on first run and blocks on
a confirmation prompt. None of that survives contact with launchd.

Run order is deliberate:

1. Preflight (space, power, privilege, Full Disk Access).
2. **Purge expired quarantine** — this is where space from the *previous* run returns,
   and it happens first so the current run has room to work.
3. Update phase (topgrade and friends), unless ``--no-update``.
4. Declaration: every selected module states what it wants to clean.
5. Dry-run accounting.
6. Execution.
7. Report, notification.
"""

from __future__ import annotations

import os
import sys

# Upstream parses sys.argv at import time; stop it before importing anything from it.
os.environ.setdefault("MAC_CLEANUP_NO_ARGPARSE", "1")

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import List, Optional  # noqa: E402

import mac_cleanup  # noqa: E402,F401  (import for side-effect ordering)
from mac_cleanup.console import console, print_panel  # noqa: E402
from mac_cleanup.core import _Collector, ProxyCollector  # noqa: E402
from mac_cleanup.core_modules import Path as UpstreamPath  # noqa: E402
from mac_cleanup.core_modules import set_current_module, set_runtime  # noqa: E402
from mac_cleanup.progress import ProgressBar  # noqa: E402

from mc import policy, quarantine  # noqa: E402
from mc.policy import Risk  # noqa: E402
from mc.preflight import DEFAULT_MIN_FREE_BYTES, Preflight, free_space  # noqa: E402
from mc.privileged import Privileged  # noqa: E402
from mc.quarantine import QuarantineBatch  # noqa: E402
from mc.registry import REGISTRY, collect, select  # noqa: E402
from mc.report import RunReport  # noqa: E402
from mc.runtime import Runtime  # noqa: E402
from mc.util import human  # noqa: E402

__all__ = ["main", "build_parser"]


def build_parser(defaults=None) -> argparse.ArgumentParser:
    """
    Build the argument parser.

    :param defaults: A :class:`mc.config.UserConfig` supplying defaults from
        ``~/.maccleaner/config.toml``. Explicit flags still win over it.
    """

    from mc.config import UserConfig

    cfg = defaults or UserConfig()

    parser = argparse.ArgumentParser(
        prog="mc",
        description="MacCleaner - comprehensive headless macOS cleanup, update and maintenance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  mc --dry-run                      see what an aggressive run would clean\n"
            "  mc                                clean at the default profile\n"
            "  mc --profile safe                 caches only, nothing needing root\n"
            "  mc --only xcode,homebrew          run two modules\n"
            "  mc --skip dev,browsers            run everything except those tags\n"
            "  mc --restore 2026-07-30T09-30-00  undo a quarantined run\n"
        ),
    )

    what = parser.add_argument_group("what to run")
    what.add_argument(
        "--profile",
        default=cfg.profile,
        choices=list(policy.RISK_ORDER),
        help=f"highest risk tier to execute (default: {cfg.profile})",
    )
    what.add_argument(
        "--only", default=",".join(cfg.only), help="comma-separated module names; ignores the profile filter"
    )
    what.add_argument(
        "--skip", default=",".join(cfg.skip), help="comma-separated module names or tags to exclude"
    )
    what.add_argument(
        "--tags", default=",".join(cfg.tags), help="comma-separated tags; run only modules carrying one of them"
    )
    what.add_argument("--no-update", action="store_true", help="skip the update phase (topgrade, brew, etc.)")
    what.add_argument("--update-only", action="store_true", help="run the update phase and nothing else")
    what.add_argument(
        "--os-updates",
        action="store_true",
        default=cfg.os_updates,
        help="also install pending macOS system updates (off by default: this can reboot the Mac)",
    )

    how = parser.add_argument_group("how to run")
    how.add_argument("-n", "--dry-run", action="store_true", help="measure everything, delete nothing")
    how.add_argument(
        "--no-quarantine", action="store_true", help="delete immediately instead of staging (no undo)"
    )
    how.add_argument(
        "--retention-days",
        type=int,
        default=cfg.retention_days,
        help=f"days before a quarantine batch is purged (default: {cfg.retention_days})",
    )
    how.add_argument("--no-privileged", action="store_true", help="never escalate; user-level cleaning only")
    how.add_argument(
        "--snapshot",
        action="store_true",
        default=cfg.snapshot,
        help="take an APFS local snapshot before cleaning",
    )
    how.add_argument("--on-ac-only", action="store_true", help="abort if running on battery")
    how.add_argument(
        "--min-free",
        type=int,
        default=cfg.min_free_bytes,
        help="abort if free space is below this many bytes",
    )
    how.add_argument("--yes", "-y", action="store_true", help="assume yes (default when not on a terminal)")
    how.add_argument(
        "--review",
        action="store_true",
        help="show the plan and let you deselect parts before deleting (default on a terminal)",
    )
    how.add_argument(
        "--no-review", action="store_true", help="skip the interactive review even on a terminal"
    )

    out = parser.add_argument_group("output")
    out.add_argument("-v", "--verbose", action="store_true", help="list every path as it is processed")
    out.add_argument(
        "--breakdown",
        action="store_true",
        help="grouped view of what would be deleted, folders rolled up (implies --dry-run)",
    )
    out.add_argument(
        "--breakdown-all",
        action="store_true",
        help="like --breakdown but lists every path instead of collapsing",
    )
    out.add_argument("--json", dest="json_path", default="", help="write the run report to this path as well")
    out.add_argument("--no-notify", action="store_true", help="suppress the macOS notification")

    manage = parser.add_argument_group("quarantine and introspection")
    manage.add_argument("--list-modules", action="store_true", help="list all modules with tier and tags, then exit")
    manage.add_argument("--list-quarantine", action="store_true", help="list quarantine batches, then exit")
    manage.add_argument("--restore", metavar="STAMP", default="", help="restore a quarantine batch, then exit")
    manage.add_argument(
        "--purge-quarantine", action="store_true", help="purge expired quarantine batches, then exit"
    )
    manage.add_argument("--explain-policy", action="store_true", help="print the path policy, then exit")
    manage.add_argument("--doctor", action="store_true", help="check the install and exit")

    project = parser.add_argument_group("project cleaning")
    project.add_argument(
        "--project-clean",
        metavar="DIR",
        default="",
        help="remove build artefacts (node_modules, Pods, target, build) from one project, then exit",
    )
    project.add_argument(
        "--deep",
        action="store_true",
        help="with --project-clean, also run the package managers' own cache-clean commands",
    )

    return parser


def _csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_modules() -> None:
    """Import every module file so their decorators register them."""

    import importlib
    import pkgutil

    from mc import modules as modules_package

    for info in pkgutil.iter_modules(modules_package.__path__):
        importlib.import_module(f"{modules_package.__name__}.{info.name}")


# --------------------------------------------------------------------------------------
# Sub-commands that exit early
# --------------------------------------------------------------------------------------


def _cmd_list_modules() -> int:
    from rich.table import Table

    table = Table(show_header=True, header_style="info", box=None)
    table.add_column("Module", style="bold")
    table.add_column("Tier")
    table.add_column("Tags")
    table.add_column("Available")

    colour = {Risk.SAFE: "green", Risk.STANDARD: "cyan", Risk.AGGRESSIVE: "yellow", Risk.NUCLEAR: "red"}

    for module in REGISTRY.values():
        reason = module.guard()
        table.add_row(
            module.name,
            f"[{colour.get(module.risk, 'white')}]{module.risk}[/]",
            ",".join(module.tags),
            "[success]yes[/success]" if reason is None else f"[warning]no[/warning] ({reason})",
        )

    console.print(table)
    console.print(f"\n{len(REGISTRY)} modules registered.")

    return 0


def _cmd_list_quarantine() -> int:
    batches = list(quarantine.list_batches())

    if not batches:
        console.print("Quarantine is empty.")
        return 0

    from rich.table import Table

    table = Table(show_header=True, header_style="info", box=None)
    table.add_column("Batch")
    table.add_column("Age", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Entries", justify="right")

    for stamp, age_days, size, count in batches:
        expiring = age_days >= quarantine.DEFAULT_RETENTION_DAYS
        table.add_row(
            stamp,
            f"[{'danger' if expiring else 'info'}]{age_days:.1f}d[/]",
            human(size),
            str(count),
        )

    console.print(table)
    console.print(f"\n{quarantine.summary_line()}")
    console.print("Restore with: [info]mc --restore <batch>[/info]")

    return 0


def _cmd_restore(stamp: str, privileged: Privileged) -> int:
    try:
        restored, skipped, errors = quarantine.restore(stamp, privileged_client=privileged)
    except FileNotFoundError as exc:
        console.print(f"[danger]{exc}[/danger]")
        return 1

    console.print(f"Restored [success]{restored}[/success] path(s) from {stamp}.")

    if skipped:
        console.print(f"[warning]{skipped} skipped[/warning] - something already exists at the original location.")
    for error in errors:
        console.print(f"[danger]![/danger] {error}")

    return 0 if not errors else 1


def _cmd_doctor(privileged: Privileged) -> int:
    from mc.preflight import has_full_disk_access, on_ac_power
    from mc.privileged import ASKPASS_PATH, HELPER_PATH

    def line(label: str, ok: bool, detail: str = "") -> None:
        mark = "[success]OK[/success]" if ok else "[danger]NO[/danger]"
        console.print(f"  {mark}  {label}" + (f"  [dim]{detail}[/dim]" if detail else ""))

    console.print("[info]MacCleaner install check[/info]")
    line("root helper installed", HELPER_PATH.is_file(), str(HELPER_PATH))
    line("passwordless sudo to helper", privileged.available, privileged.unavailable_reason or "")
    line("askpass helper installed", ASKPASS_PATH.is_file(), str(ASKPASS_PATH))
    line("keychain credential readable", privileged.askpass_available)
    line("full disk access", has_full_disk_access())
    line("on AC power", on_ac_power())
    line("free space above floor", free_space() > DEFAULT_MIN_FREE_BYTES, human(free_space()) + " free")

    console.print(f"\n  {len(REGISTRY)} modules registered, "
                  f"{sum(1 for m in REGISTRY.values() if m.guard() is None)} available on this machine.")
    console.print(f"  {quarantine.summary_line()}")

    return 0


# --------------------------------------------------------------------------------------
# Main run
# --------------------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    from mc.config import load_config

    config = load_config()
    parser = build_parser(config)
    args = parser.parse_args(argv)

    if args.breakdown or args.breakdown_all:
        args.dry_run = True

    if not config.notify:
        os.environ["MACCLEANER_NO_NOTIFY"] = "1"
    if not config.quarantine:
        args.no_quarantine = True

    if args.no_notify:
        os.environ["MACCLEANER_NO_NOTIFY"] = "1"

    # Upstream's per-module prompts have no place in an unattended run.
    mac_cleanup.args.force = True
    mac_cleanup.args.verbose = args.verbose

    if args.project_clean:
        from mc.project import clean_project

        return clean_project(args.project_clean, dry_run=args.dry_run, deep=args.deep)

    _load_modules()

    # Left enabled during a dry run so system-level modules can still be measured. Every
    # mutating call is gated behind Runtime.dry_run and the execution phase, neither of
    # which a dry run reaches, so the only verb that actually fires is `self-check`.
    privileged = Privileged(enabled=not args.no_privileged)

    if args.list_modules:
        return _cmd_list_modules()
    if args.explain_policy:
        console.print("\n".join(policy.describe()))
        return 0
    if args.list_quarantine:
        return _cmd_list_quarantine()
    if args.restore:
        return _cmd_restore(args.restore, privileged)
    if args.doctor:
        return _cmd_doctor(privileged)
    if args.purge_quarantine:
        reclaimed = quarantine.purge_expired(retention_days=args.retention_days, privileged_client=privileged)
        console.print(f"Purged [success]{human(reclaimed)}[/success] of expired quarantine.")
        return 0

    return _run(args, privileged)


def _run(args, privileged: Privileged) -> int:
    report = RunReport(
        profile=args.profile, dry_run=args.dry_run, quarantine=not args.no_quarantine
    )

    console.print(
        f"[info]MacCleaner[/info] profile=[bold]{args.profile}[/bold] "
        f"{'[warning]dry run[/warning]' if args.dry_run else ''}"
    )

    # -- 1. preflight ------------------------------------------------------------------
    preflight = Preflight(
        privileged=privileged,
        require_ac=args.on_ac_only,
        min_free=args.min_free,
        take_snapshot=args.snapshot and not args.dry_run,
    )
    checks = preflight.run_checks()

    for warning in checks.warnings:
        report.warn(warning)
        console.print(f"[warning]![/warning] {warning}")

    if not checks.ok:
        console.print(f"[danger]Aborting:[/danger] {checks.abort_reason}")
        return 1

    report.free_before = checks.free_bytes

    # -- 2. purge expired quarantine (space from previous runs comes back here) --------
    if not args.dry_run:
        report.quarantine_purged = quarantine.purge_expired(
            retention_days=args.retention_days, privileged_client=privileged
        )
        if report.quarantine_purged:
            console.print(
                f"Purged [success]{human(report.quarantine_purged)}[/success] of expired quarantine."
            )

    # -- 3. update phase ---------------------------------------------------------------
    if not args.no_update:
        from mc.update import run_updates

        run_updates(
            report=report,
            privileged=privileged,
            dry_run=args.dry_run,
            verbose=args.verbose,
            os_updates=args.os_updates,
        )

    if args.update_only:
        report.free_after = free_space()
        _finish(report, args)
        return 0

    # -- 4. declaration ----------------------------------------------------------------
    batch: Optional[QuarantineBatch] = None
    if not args.no_quarantine and not args.dry_run and args.profile != Risk.SAFE:
        batch = QuarantineBatch()

    runtime = Runtime(privileged=privileged, batch=batch, report=report, dry_run=args.dry_run)
    set_runtime(runtime)

    collector = _Collector()
    proxy = ProxyCollector()

    chosen = select(
        profile=args.profile, only=_csv(args.only), skip=_csv(args.skip), tags=_csv(args.tags)
    )

    if not chosen:
        console.print("[warning]No modules selected.[/warning]")
        set_runtime(None)
        return 1

    declared = collect(
        chosen, collector=proxy, report=report, privileged=privileged, profile=args.profile
    )

    console.print(
        f"{len(declared)} module(s) declared work, "
        f"{len(report.skipped)} skipped, {len(report.failed)} failed to declare."
    )

    # -- 5. accounting -----------------------------------------------------------------
    from mc.review import is_interactive as _interactive

    want_detail = (
        args.breakdown
        or args.breakdown_all
        or args.review
        or (not args.dry_run and _interactive() and not args.no_review and not args.yes)
    )
    details: dict = {} if want_detail else None

    estimated = _estimate(collector, runtime, verbose=args.verbose, details=details)
    report.estimated_bytes = estimated

    if want_detail:
        from mc.breakdown import render_breakdown

        render_breakdown(console, details, total=estimated, show_all=args.breakdown_all)

    if args.dry_run:
        print_panel(
            text=f"Approximately [success]{human(estimated)}[/success] would be cleaned",
            title="[info]Dry run",
        )
        report.free_after = report.free_before
        set_runtime(None)
        _finish(report, args)
        return 0

    # -- 6. review gate ----------------------------------------------------------------
    from mc.review import is_interactive, review_selection

    selection = None
    wants_review = args.review or (is_interactive() and not args.no_review and not args.yes)

    if wants_review:
        if details is None:
            details = {}
            _estimate(collector, runtime, verbose=False, details=details)

        selection = review_selection(console, details, total=estimated)

        if selection.cancelled:
            console.print("[warning]Cancelled — nothing was deleted.[/warning]")
            set_runtime(None)
            if batch is not None:
                batch.close()
            return 1

        if not selection.is_empty:
            runtime.selection = selection
            for name in sorted(selection.excluded_modules):
                report.module(name).status = "skipped"
                report.module(name).reason = "deselected at review"
            report.warn(
                f"{len(selection.excluded_modules)} module(s) and "
                f"{len(selection.excluded_prefixes)} location(s) deselected at review"
            )

    # -- 7. execution ------------------------------------------------------------------
    try:
        _execute(collector, report)
    finally:
        set_runtime(None)
        if batch is not None:
            batch.close()

    report.free_after = free_space()

    # -- 8. report ---------------------------------------------------------------------
    _finish(report, args, batch=batch)

    return 0


def _estimate(collector: _Collector, runtime: Runtime, *, verbose: bool, details=None) -> int:
    """
    Measure everything the collector holds, honouring policy.

    Uses the runtime's estimator rather than upstream's ``_extract_paths`` so that paths
    the policy will refuse are reported as zero instead of inflating the figure.
    """

    modules = [module for unit in collector._execute_list for module in unit.modules]  # noqa: SLF001
    path_modules = [module for module in modules if isinstance(module, UpstreamPath)]

    total = 0
    seen: set = set()  # shared across modules so overlapping rules are counted once

    for module in ProgressBar.wrap_iter(path_modules, description="Measuring", total=len(path_modules)):
        owner = getattr(module, "owner", "unknown")
        rule = module.get_path.as_posix()
        collected: list = [] if details is not None else None

        size = runtime.estimate(module, seen, collected)
        total += size

        if details is not None and collected:
            details.setdefault((owner, rule), []).extend(collected)

        # Attribute to the owning module so the dry-run table is per-module rather than
        # one aggregate number.
        runtime.report.module(getattr(module, "owner", "unknown")).bytes_estimated += size

        if verbose and size:
            console.print(f"  {human(size):>12}  {module.get_path.as_posix()}", no_wrap=True, highlight=False)

    return total


def _execute(collector: _Collector, report: RunReport) -> None:
    """Run every collected unit."""

    for unit in collector._execute_list:  # noqa: SLF001
        for module in ProgressBar.wrap_iter(unit.modules, description=unit.message, total=len(unit.modules)):
            owner = getattr(module, "owner", "unknown")
            set_current_module(owner)
            try:
                module._execute()  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001 - one bad unit must not abort the run
                report.module(owner).errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                set_current_module("upstream")


def _finish(report: RunReport, args, *, batch: Optional[QuarantineBatch] = None) -> None:
    """Render, persist and announce the result."""

    console.print()
    report.render(console)

    if batch is not None and len(batch):
        console.print(
            f"\n[info]{len(batch)} path(s) staged to quarantine[/info] as [bold]{batch.stamp}[/bold] "
            f"({human(batch.total_bytes)}). Space returns when it is purged in "
            f"{args.retention_days} days; undo with [info]mc --restore {batch.stamp}[/info]."
        )

    written = report.write()

    if args.json_path:
        import json

        Path(args.json_path).expanduser().write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
        )

    print_panel(text=report.summary_text(), title="[info]MacCleaner")
    console.print(f"[dim]Report: {written}[/dim]")

    report.notify()


def entrypoint() -> None:  # pragma: no cover - console_scripts target
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[warning]Interrupted.[/warning]")
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    entrypoint()
