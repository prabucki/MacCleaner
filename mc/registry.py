"""
Module registry and the authoring DSL.

A cleanup module is a function that declares work; it does not perform it. Declaration
happens for every selected module first, then everything runs. That two-phase split is
what makes an accurate dry run possible.

Writing one looks like this::

    @cleanup_module(name="xcode", risk=Risk.AGGRESSIVE, requires_any=["/Applications/Xcode.app"])
    def xcode(ctx: Context) -> None:
        with ctx.step("Clearing Xcode derived data and archives") as step:
            step.path("~/Library/Developer/Xcode/DerivedData/*")
            step.path("~/Library/Developer/Xcode/Archives/*")
            step.command(["xcrun", "simctl", "delete", "unavailable"], timeout=600)

Guard clauses (``requires``, ``requires_any``, ``requires_binary``) are evaluated before
the function is called, so modules do not each re-implement "is this app installed".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path as Path_
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from mac_cleanup.core_modules import Command, Path, set_current_module

from mc import policy
from mc.actions import Action, SafeCommand
from mc.policy import Risk
from mc.util import app_is_running, quit_app, which

__all__ = ["cleanup_module", "Context", "Step", "REGISTRY", "CleanupModule", "Risk", "collect"]


@dataclass
class CleanupModule:
    """A registered cleanup module."""

    name: str
    func: Callable[["Context"], None]
    risk: str = Risk.STANDARD
    title: str = ""
    #: Every path must exist for the module to run.
    requires: Sequence[str] = field(default_factory=tuple)
    #: At least one path must exist for the module to run.
    requires_any: Sequence[str] = field(default_factory=tuple)
    #: Every binary must be on PATH for the module to run.
    requires_binary: Sequence[str] = field(default_factory=tuple)
    #: Tags for --only / --skip selection, e.g. "dev", "browser", "system".
    tags: Sequence[str] = field(default_factory=tuple)
    #: True for modules that only measure and report, never delete.
    read_only: bool = False

    def guard(self) -> Optional[str]:
        """Reason the module cannot run here, or None if it can."""

        for binary in self.requires_binary:
            if which(binary) is None:
                return f"{binary} not installed"

        for required in self.requires:
            if not _exists(required):
                return f"{required} not present"

        if self.requires_any and not any(_exists(candidate) for candidate in self.requires_any):
            return "none of the expected paths are present"

        return None


def _exists(pattern: str) -> bool:
    """Existence check that understands globs."""

    expanded = policy.expand(pattern)

    if any(ch in expanded for ch in "*?["):
        from glob import glob

        return bool(glob(expanded))

    return Path_(expanded).exists()


#: All registered modules, in registration order.
REGISTRY: Dict[str, CleanupModule] = {}


def cleanup_module(
    *,
    name: str,
    risk: str = Risk.STANDARD,
    title: str = "",
    requires: Sequence[str] = (),
    requires_any: Sequence[str] = (),
    requires_binary: Sequence[str] = (),
    tags: Sequence[str] = (),
    read_only: bool = False,
) -> Callable:
    """Register a cleanup module. See the module docstring for the shape of the function."""

    def decorator(func: Callable[["Context"], None]) -> Callable[["Context"], None]:
        if name in REGISTRY:
            raise ValueError(f"duplicate cleanup module name: {name}")

        REGISTRY[name] = CleanupModule(
            name=name,
            func=func,
            risk=risk,
            title=title or name.replace("_", " ").title(),
            requires=tuple(requires),
            requires_any=tuple(requires_any),
            requires_binary=tuple(requires_binary),
            tags=tuple(tags),
            read_only=read_only,
        )

        return func

    return decorator


# --------------------------------------------------------------------------------------
# Authoring DSL
# --------------------------------------------------------------------------------------


class Step:
    """
    One unit of work inside a module — a progress-bar line and the items under it.

    Obtained from :meth:`Context.step`; not constructed directly.
    """

    def __init__(self, context: "Context", unit):
        self._context = context
        self._unit = unit

    # -- paths -------------------------------------------------------------------------

    def path(
        self,
        *patterns: str,
        privileged: bool = False,
        quarantine: Optional[bool] = None,
        override: str = "",
        dry_run_only: bool = False,
    ) -> "Step":
        """
        Queue one or more paths for removal.

        :param patterns: Paths or globs. ``~`` is expanded against the real user's home.
        :param privileged: Route through the root helper (needed outside the user's tree).
        :param quarantine: Force staging on/off. Unset follows the run default.
        :param override: Non-empty to pierce *soft* protection; the string is the audit
            reason and is recorded in the report.
        :param dry_run_only: Measure but never delete — for paths worth reporting whose
            removal is not safe to automate.
        """

        for pattern in patterns:
            module = Path(pattern)

            if privileged:
                module.privileged()
            if quarantine is not None:
                module.quarantined(quarantine)
            if override:
                module.override_protection(override)
            if dry_run_only:
                module.dry_run_only()

            self._unit.add(module)

        return self

    def root_path(self, *patterns: str, **kwargs) -> "Step":
        """Shorthand for :meth:`path` with ``privileged=True``."""

        return self.path(*patterns, privileged=True, **kwargs)

    def measure(self, *patterns: str) -> "Step":
        """Report the size of these paths without ever deleting them."""

        return self.path(*patterns, dry_run_only=True)

    # -- commands and actions ----------------------------------------------------------

    def command(self, argv, *, timeout: int = 300, ignore_exit: bool = True, description: str = "") -> "Step":
        """
        Run an external command.

        Prefer an argv list over a string: it skips the shell entirely, so paths with
        spaces and apostrophes cannot break out.
        """

        self._unit.add(
            SafeCommand(
                argv, timeout=timeout, ignore_exit=ignore_exit, description=description, report=self._context.report
            )
        )

        return self

    def shell(self, command: str) -> "Step":
        """Run a shell string through upstream's ``Command``. Use only when a pipeline is needed."""

        self._unit.add(Command(command))

        return self

    def action(self, func: Callable[[], object], description: str = "") -> "Step":
        """Run a Python callable at execution time."""

        self._unit.add(Action(func, description=description))

        return self

    def root(self, verb: str, *args: str) -> "Step":
        """
        Invoke a root-helper maintenance verb.

        :param verb: Method name on :class:`mc.privileged.Privileged`, e.g. ``periodic``.
        """

        privileged = self._context.privileged
        handler = getattr(privileged, verb, None)

        if handler is None:
            raise AttributeError(f"no privileged verb named {verb!r}")

        def invoke():
            if not privileged.available:
                self._context.report.module(self._context.name).errors.append(
                    f"{verb}: {privileged.unavailable_reason}"
                )
                return None
            result = handler(*args)
            if not result.ok:
                self._context.report.module(self._context.name).errors.append(f"{verb}: {result.error}")
            return None

        return self.action(invoke, description=f"root:{verb}")

    def quit_app(self, *process_names: str) -> "Step":
        """
        Quit apps before cleaning their data.

        Deleting a running app's cache out from under it is a reliable way to corrupt its
        state, so any module that touches a live app's files should call this first.
        """

        for process_name in process_names:
            self._unit.add(Action(lambda name=process_name: quit_app(name), description=f"quit {process_name}"))

        return self


class Context:
    """
    What a cleanup module is handed.

    Exposes :meth:`step` for declaring work, plus a few environment queries so modules do
    not each re-derive "is this installed" or "is this running".
    """

    def __init__(self, *, name: str, collector, report, privileged, profile: str):
        self.name = name
        self.profile = profile
        self.report = report
        self.privileged = privileged
        self._collector = collector
        self._skip_reason: Optional[str] = None

    # -- declaring work ----------------------------------------------------------------

    def step(self, message: str):
        """
        Context manager declaring one unit of work.

        ``message`` is what appears next to the progress bar while the unit runs.
        """

        context = self

        class _StepContext:
            def __enter__(self) -> Step:
                self._proxy = context._collector
                self._unit = self._proxy.__enter__()
                self._unit.message(message)
                return Step(context, self._unit)

            def __exit__(self, *exc):
                return self._proxy.__exit__(*exc)

        return _StepContext()

    def skip(self, reason: str) -> None:
        """Record that this module deliberately did nothing."""

        self._skip_reason = reason

    @property
    def skip_reason(self) -> Optional[str]:
        return self._skip_reason

    # -- environment queries -----------------------------------------------------------

    @staticmethod
    def exists(pattern: str) -> bool:
        """True if the path (or glob) exists."""

        return _exists(pattern)

    @staticmethod
    def which(binary: str) -> Optional[str]:
        """Absolute path to a binary, searching Homebrew prefixes launchd would miss."""

        return which(binary)

    @staticmethod
    def is_running(process_name: str) -> bool:
        """True if a process with this exact name is alive."""

        return app_is_running(process_name)

    @staticmethod
    def glob(pattern: str) -> List[Path_]:
        """Existing paths matching a glob."""

        from glob import glob as _glob

        return [Path_(match) for match in _glob(policy.expand(pattern))]

    @property
    def at_least_aggressive(self) -> bool:
        """True when the active profile is ``aggressive`` or higher."""

        return self.profile in (Risk.AGGRESSIVE, Risk.NUCLEAR)


# --------------------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------------------


def collect(
    modules: Iterable[CleanupModule], *, collector, report, privileged, profile: str
) -> List[CleanupModule]:
    """
    Run the declaration phase for each module.

    Nothing is deleted here — modules only populate the collector. Guard failures and
    exceptions are recorded and the run continues; one broken module must not take down
    an unattended cleanup.

    :return: The modules that actually declared work.
    """

    declared: List[CleanupModule] = []

    for module in modules:
        result = report.module(module.name, module.risk)

        reason = module.guard()
        if reason is not None:
            result.status = "skipped"
            result.reason = reason
            continue

        context = Context(
            name=module.name, collector=collector, report=report, privileged=privileged, profile=profile
        )

        set_current_module(module.name)
        started = time.monotonic()

        try:
            module.func(context)
        except Exception as exc:  # noqa: BLE001 - a bad module must not abort the run
            result.status = "failed"
            result.reason = f"{type(exc).__name__}: {exc}"
            continue
        finally:
            set_current_module("upstream")
            result.duration_seconds += time.monotonic() - started

        if context.skip_reason:
            result.status = "skipped"
            result.reason = context.skip_reason
            continue

        result.status = "ok"
        declared.append(module)

    return declared


def select(
    *, profile: str, only: Sequence[str] = (), skip: Sequence[str] = (), tags: Sequence[str] = ()
) -> List[CleanupModule]:
    """
    Choose which modules to run.

    :param profile: Highest risk tier permitted; modules above it are excluded.
    :param only: If non-empty, restrict to these module names (bypasses the tier filter,
        so a single nuclear module can be run deliberately without changing profile).
    :param skip: Module names or tags to exclude.
    :param tags: If non-empty, restrict to modules carrying at least one of these tags.
    """

    permitted = set(policy.risk_at_or_below(profile))
    chosen: List[CleanupModule] = []

    for module in REGISTRY.values():
        if only:
            if module.name in only:
                chosen.append(module)
            continue

        if module.risk not in permitted:
            continue
        if module.name in skip or any(tag in skip for tag in module.tags):
            continue
        if tags and not any(tag in tags for tag in module.tags):
            continue

        chosen.append(module)

    return chosen
