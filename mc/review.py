"""
Interactive review gate.

Shown before a manual run so you can deselect anything you would rather keep. Never
shown when stdin is not a terminal, so a scheduled run can never block waiting for
input — that is the single most important property here.

Two levels of granularity:

* whole modules — "don't touch the browsers this time"
* individual locations within a module — "clean Code, leave Claude alone"

The result is a :class:`Selection` the runtime consults before deleting anything.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from mc.breakdown import _elide, _group_by_ancestor, _leaf_summary
from mc.util import human, restore_terminal

__all__ = ["Selection", "review_selection", "is_interactive"]


@dataclass
class Selection:
    """What the user chose to exclude."""

    excluded_modules: Set[str] = field(default_factory=set)
    excluded_prefixes: Set[str] = field(default_factory=set)
    cancelled: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.excluded_modules and not self.excluded_prefixes

    def excludes_path(self, path: str) -> bool:
        """True when ``path`` sits under a deselected location."""

        return any(path == p or path.startswith(p.rstrip("/") + os.sep) for p in self.excluded_prefixes)


def is_interactive() -> bool:
    """
    Whether it is safe to prompt.

    Requires a terminal on both stdin and stdout. launchd gives neither, so a scheduled
    run skips the gate rather than hanging forever with nobody to answer.
    """

    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # pragma: no cover - closed streams
        return False


def _ask(console, prompt: str) -> str:
    """
    Read a line, bypassing rich's console input.

    ``Console.input`` renders the prompt through the console, which is exactly what a
    stray Live display repaints over. Writing the prompt and reading with the builtin
    keeps the two concerns apart.
    """

    console.print(prompt, end="")

    try:
        return input().strip().lower()
    finally:
        # An interrupt here leaves the terminal however the tty driver left it.
        restore_terminal()


def _module_rows(details: Dict[Tuple[str, str], List[Tuple[Path, int]]]):
    """Collapse the estimate detail into ``{module: [(path, size), ...]}``."""

    per_module: Dict[str, List[Tuple[Path, int]]] = {}
    for (module, _rule), entries in details.items():
        per_module.setdefault(module, []).extend(entries)

    return per_module


def _print_modules(console, rows, selection: Selection, totals) -> List[str]:
    """Render the module table. Returns the index-ordered module names."""

    from rich.table import Table

    order = sorted(rows, key=lambda m: -totals[m])

    table = Table(show_header=True, header_style="info", box=None, pad_edge=False)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("Module", style="bold", no_wrap=True)
    table.add_column("Size", justify="right", no_wrap=True)
    table.add_column("Locations", justify="right", no_wrap=True)

    for index, module in enumerate(order, start=1):
        entries = rows[module]
        excluded = module in selection.excluded_modules
        partial = not excluded and any(selection.excludes_path(str(p)) for p, _s in entries)

        if excluded:
            mark, style = "[danger]skip[/danger]", "dim"
        elif partial:
            mark, style = "[warning]part[/warning]", ""
        else:
            mark, style = "[success]  on[/success]", ""

        kept = sum(s for p, s in entries if not selection.excludes_path(str(p)) and not excluded)

        table.add_row(
            str(index),
            mark,
            f"[{style}]{module}[/{style}]" if style else module,
            human(kept) if kept else "[dim]—[/dim]",
            str(len({str(p.parent) for p, _s in entries})),
        )

    console.print(table)

    return order


def _drill(console, module: str, entries, selection: Selection) -> None:
    """Toggle individual locations within one module."""

    groups = _group_by_ancestor(entries, max_groups=20)

    while True:
        console.print(f"\n[info]Locations in[/info] [bold]{module}[/bold]")
        for index, (directory, size, count, members) in enumerate(groups, start=1):
            off = selection.excludes_path(directory)
            mark = "[danger]skip[/danger]" if off else "[success]  on[/success]"
            console.print(
                f"  {index:>3}  {mark}  {human(size):>10}  {_elide(directory)}/", no_wrap=True, highlight=False
            )
            console.print(
                f"                          [dim]└ {_leaf_summary(directory, members)}[/dim]",
                no_wrap=True,
                highlight=False,
            )

        console.print("\n[dim]numbers to toggle · [/dim]back[dim] to return[/dim]")
        raw = _ask(console, "locations> ")

        if raw in ("back", "b", "", "ok"):
            return

        for token in raw.replace(",", " ").split():
            if not token.isdigit() or not 1 <= int(token) <= len(groups):
                console.print(f"[warning]ignored:[/warning] {token}")
                continue
            directory = groups[int(token) - 1][0]
            if directory in selection.excluded_prefixes:
                selection.excluded_prefixes.discard(directory)
            else:
                selection.excluded_prefixes.add(directory)


def review_selection(console, details, *, total: int) -> Selection:
    """
    Show the plan and let the user deselect parts of it.

    :param details: ``{(module, rule): [(path, size)]}`` from the estimate pass.
    :param total: Estimated bytes, before any deselection.
    :return: The :class:`Selection` to apply. ``cancelled`` is set if the user backed out.
    """

    selection = Selection()
    rows = _module_rows(details)

    if not rows:
        return selection

    # Anything that ran before this may have left the terminal in raw mode — a killed
    # child that was reading single keypresses is enough. In that state the prompt below
    # accepts no input and Ctrl-C echoes as text instead of interrupting, which strands
    # the user with no way out. Repair it before asking a question.
    restore_terminal()

    # A progress bar's Live display repaints over the prompt and swallows the echo. It
    # should already be stopped, but stopping it again is free and a stuck prompt is not.
    try:
        from mac_cleanup.progress import ProgressBar

        ProgressBar.current_progress.stop()
    except Exception:  # noqa: BLE001 - never let cosmetics block the gate
        pass

    # Preferred path: arrow keys, space to toggle, enter to run. Falls back to the typed
    # prompt below when the terminal cannot support it, so nothing is ever unusable.
    from mc.tui import run_selector, tui_available

    if tui_available():
        try:
            return run_selector(console, details, total=total)
        except Exception as exc:  # noqa: BLE001 - any TUI failure falls back, never blocks
            console.print(f"[warning]Selector unavailable ({exc}); using the text prompt.[/warning]")
            restore_terminal()

    totals = {m: sum(s for _p, s in e) for m, e in rows.items()}

    while True:
        console.print()
        remaining = sum(
            size
            for module, entries in rows.items()
            if module not in selection.excluded_modules
            for path, size in entries
            if not selection.excludes_path(str(path))
        )

        console.print(f"[info]Review before deleting[/info] — [bold]{human(remaining)}[/bold] selected"
                      + (f" [dim]of {human(total)}[/dim]" if remaining != total else ""))

        order = _print_modules(console, rows, selection, totals)

        console.print(
            "\n[dim]numbers[/dim] toggle a module · [dim]d <n>[/dim] pick locations inside one · "
            "[dim]all[/dim] / [dim]none[/dim] · [dim]go[/dim] to run · [dim]q[/dim] to cancel"
        )

        try:
            raw = _ask(console, "review> ")
        except (EOFError, KeyboardInterrupt):
            selection.cancelled = True
            return selection

        if raw in ("q", "quit", "cancel"):
            selection.cancelled = True
            return selection

        if raw in ("go", "ok", "y", "yes", "run", ""):
            return selection

        if raw == "all":
            selection.excluded_modules.clear()
            selection.excluded_prefixes.clear()
            continue

        if raw == "none":
            selection.excluded_modules = set(rows)
            continue

        if raw.startswith("d"):
            token = raw[1:].strip()
            if token.isdigit() and 1 <= int(token) <= len(order):
                module = order[int(token) - 1]
                _drill(console, module, rows[module], selection)
            else:
                console.print("[warning]usage:[/warning] d <module number>")
            continue

        for token in raw.replace(",", " ").split():
            if not token.isdigit() or not 1 <= int(token) <= len(order):
                console.print(f"[warning]ignored:[/warning] {token}")
                continue
            module = order[int(token) - 1]
            if module in selection.excluded_modules:
                selection.excluded_modules.discard(module)
            else:
                selection.excluded_modules.add(module)
