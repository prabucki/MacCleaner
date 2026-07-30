"""
Non-path collector units.

Upstream ships ``Path`` (delete a path) and ``Command`` (run a shell string). Two more
kinds of work are needed, and both slot into the existing collector because
``BaseModule`` is not final:

* :class:`Action` — call a Python function. Used for root-helper verbs, app quitting,
  and anything whose logic does not fit in a shell one-liner.
* :class:`SafeCommand` — like ``Command`` but with a per-call timeout and stdout captured
  into the run report instead of discarded.
"""

from __future__ import annotations

from typing import Callable, Optional

from mac_cleanup.core_modules import BaseModule, get_current_module

from mc.util import run

__all__ = ["Action", "SafeCommand"]


class Action(BaseModule):
    """
    Collector unit wrapping a Python callable.

    :param func: Callable invoked at execution time. Its return value, when an int, is
        treated as bytes reclaimed and added to the module's total.
    :param description: Shown in error messages and the report.
    """

    def __init__(self, func: Callable[[], object], description: str = ""):
        self.__func = func
        self.description = description or getattr(func, "__name__", "action")
        self.owner: str = get_current_module()

    def _execute(self) -> Optional[object]:  # type: ignore[override]
        if not BaseModule._execute(self):
            return None

        return self.__func()


class SafeCommand(BaseModule):
    """
    Run an external command with a timeout, recording failures rather than swallowing them.

    Upstream's ``Command`` sends stderr to ``/dev/null`` by default and cannot time out,
    so a failing or hanging step is invisible on an unattended run.

    :param argv: Argument list, or a string to run through the shell.
    :param timeout: Seconds before the process group is killed.
    :param ignore_exit: When True a non-zero exit is not recorded as an error — correct
        for tools like ``brew cleanup`` that exit non-zero on entirely normal conditions.
    :param report: Optional report to record failures against.
    """

    def __init__(
        self,
        argv,
        *,
        timeout: int = 300,
        ignore_exit: bool = True,
        description: str = "",
        report=None,
    ):
        self.argv = argv
        self.timeout = timeout
        self.ignore_exit = ignore_exit
        self.description = description or (argv if isinstance(argv, str) else " ".join(argv))
        self.owner: str = get_current_module()
        self._report = report

    def _execute(self) -> Optional[str]:  # type: ignore[override]
        if not BaseModule._execute(self):
            return None

        result = run(self.argv, timeout=self.timeout)

        if result.returncode != 0 and not self.ignore_exit and self._report is not None:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            self._report.module(self.owner).errors.append(
                f"{self.description}: exit {result.returncode}"
                + (f" - {detail[-1][:200]}" if detail else "")
            )

        return result.stdout
