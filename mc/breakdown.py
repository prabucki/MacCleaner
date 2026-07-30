"""
Rolled-up view of what a run would delete.

A flat list is unreadable at 35+ GB — the Electron sweeper alone produces 60-odd
directories, each added as its own rule. Grouping by rule therefore collapses nothing;
what actually helps is grouping by *location*.

So: gather every path a module would remove, then group them by a common ancestor,
walking that ancestor up the tree until the number of groups is small enough to read.
The result is "6.99 GB under Ferdium/Partitions/, 24 items" rather than 24 lines of
near-identical UUID paths.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from mc.util import human

__all__ = ["render_breakdown"]

#: Aim for at most this many lines per module before rolling up further.
MAX_GROUPS = 8

#: List paths individually when a module has no more than this many.
LIST_BELOW = 6

#: Modules under this total get one shared summary line at the end.
MODULE_FLOOR = 10 * 1024 * 1024

#: Longest path rendered before the middle is elided.
MAX_PATH = 68

#: Home-relative directories holding one subdirectory per application. Roll-up keeps the
#: app name for these, so each app gets its own line.
_PER_APP_CONTAINERS = (
    "Library/Application Support",
    "Library/Containers",
    "Library/Group Containers",
    "Library/Caches",
    "Library/HTTPStorages",
    "Library/WebKit",
    "Library/Logs",
    "Library/Developer",
)


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path.startswith(home) else path


def _elide(path: str) -> str:
    """Shorten a long path from the middle, keeping both ends legible."""

    short = _tilde(path)
    if len(short) <= MAX_PATH:
        return short

    head, tail = short[: MAX_PATH // 2 - 2], short[-(MAX_PATH // 2 - 1) :]
    return f"{head}…{tail}"


def _floor(path_str: str) -> str:
    """
    Shallowest ancestor worth rolling up to.

    Without a floor the walk keeps going until everything fits in one bucket, and you
    get "4.22 GB under /" — technically true, useless to read. Inside the home directory
    we keep two components (`~/Library/Caches`, `~/.npm`); outside it, three
    (`/private/var/db`).
    """

    home = os.path.expanduser("~")

    if path_str.startswith(home + os.sep):
        rest = [p for p in path_str[len(home) :].split(os.sep) if p]

        # These hold one subdirectory per application, so rolling up to the container
        # itself would merge Ferdium, Claude and Notion into one meaningless line.
        # Keep the app name.
        for container in _PER_APP_CONTAINERS:
            parts = container.split("/")
            if rest[: len(parts)] == parts:
                return os.path.join(home, *rest[: len(parts) + 1])

        return os.path.join(home, *rest[:2]) if rest else home

    parts = [p for p in path_str.split(os.sep) if p]
    return os.sep + os.sep.join(parts[:3])


def _leaf_summary(directory: str, paths: Iterable[Path], limit: int = 3) -> str:
    """
    Name what is actually removed under a rolled-up directory.

    Without this the report shows `~/Library/Application Support/Syncthing/` and reads as
    "delete Syncthing", when the rule only removes a log file inside it. Showing the leaf
    names makes the difference obvious at a glance.
    """

    names: List[str] = []
    for path in paths:
        try:
            relative = os.path.relpath(str(path), directory)
        except ValueError:  # pragma: no cover - different volumes
            relative = path.name
        # Keep the last two components at most: "Service Worker/CacheStorage".
        parts = relative.split(os.sep)
        label = os.sep.join(parts[-2:]) if len(parts) > 1 else parts[0]
        if label not in names:
            names.append(label)
        if len(names) > limit:
            break

    shown = names[:limit]
    more = "" if len(names) <= limit else ", …"

    return ", ".join(shown) + more


def _group_by_ancestor(
    entries: Sequence[Tuple[Path, int]], max_groups: int = MAX_GROUPS
) -> List[Tuple[str, int, int, List[Path]]]:
    """
    Bucket paths under a shared ancestor directory.

    Starts at each path's immediate parent and walks up a level at a time until the
    bucket count fits, so deeply-nested near-duplicates (per-service cache dirs,
    per-profile browser dirs) fold into one line. A key is never shortened past its
    :func:`_floor`, so the result always names somewhere meaningful.

    :return: ``[(directory, total_bytes, item_count, member_paths)]``, largest first.
    """

    keys = [str(path.parent) for path, _size in entries]
    floors = [_floor(k) for k in keys]

    while True:
        buckets: Dict[str, List[Tuple[Path, int]]] = defaultdict(list)
        for key, (path, size) in zip(keys, entries):
            buckets[key].append((path, size))

        if len(buckets) <= max_groups:
            break

        shortened = [
            os.path.dirname(k) if len(k) > len(floor) else k for k, floor in zip(keys, floors)
        ]

        if shortened == keys:
            break  # everything is at its floor; more groups beats a meaningless one

        keys = shortened

    rolled = [
        (directory, sum(s for _p, s in members), len(members), [p for p, _s in members])
        for directory, members in buckets.items()
    ]

    return sorted(rolled, key=lambda r: -r[1])


def render_breakdown(
    console,
    details: Dict[Tuple[str, str], List[Tuple[Path, int]]],
    *,
    total: int,
    show_all: bool = False,
) -> None:
    """
    Print the grouped view.

    :param details: ``{(module, rule): [(path, size), ...]}`` collected during estimation.
    :param total: Overall estimated bytes.
    :param show_all: List every path instead of rolling up.
    """

    per_module: Dict[str, List[Tuple[Path, int]]] = defaultdict(list)
    for (module, _rule), entries in details.items():
        per_module[module].extend(entries)

    totals = {m: sum(s for _p, s in e) for m, e in per_module.items()}

    console.print()
    console.print(f"[info]What would be deleted[/info] — [bold]{human(total)}[/bold] total")
    console.print(
        "[dim]Rolled up by location. The path is the CONTAINER; the line under it names\n"
        "what is actually removed from inside it.[/dim]\n"
    )

    trivial: List[Tuple[str, int]] = []

    for module in sorted(per_module, key=lambda m: -totals[m]):
        entries = sorted(per_module[module], key=lambda e: -e[1])
        module_total = totals[module]

        if module_total < MODULE_FLOOR and not show_all:
            trivial.append((module, module_total))
            continue

        console.print(f"[bold]{module}[/bold]  [success]{human(module_total)}[/success]", highlight=False)

        if show_all or len(entries) <= LIST_BELOW:
            for path, size in entries:
                console.print(
                    f"    {human(size):>10}  {'':>5}  {_elide(str(path))}", no_wrap=True, highlight=False
                )
        else:
            groups = _group_by_ancestor(entries)

            # Count goes before the path: a long path gets elided, and a truncated
            # "426 i" reads as noise.
            for directory, size, count, members in groups[:MAX_GROUPS]:
                tally = f"{count}x" if count > 1 else ""
                console.print(
                    f"    {human(size):>10}  [dim]{tally:>5}[/dim]  {_elide(directory)}/",
                    no_wrap=True,
                    highlight=False,
                )
                # The directory above is only the container. Name what goes from inside
                # it, so a rolled-up line cannot read as "delete the whole app".
                console.print(
                    f"    {'':>10}  {'':>5}  [dim]\u2514 {_leaf_summary(directory, members)}[/dim]",
                    no_wrap=True,
                    highlight=False,
                )

            rest = groups[MAX_GROUPS:]
            if rest:
                tally = f"{sum(c for _d, _s, c, _m in rest)}x"
                console.print(
                    f"    {human(sum(s for _d, s, _c, _m in rest)):>10}  "
                    f"[dim]{tally:>5}[/dim]  [dim]+ {len(rest)} more locations[/dim]",
                    no_wrap=True,
                    highlight=False,
                )

        console.print()

    if trivial:
        names = ", ".join(f"{n} ({human(s)})" for n, s in sorted(trivial, key=lambda t: -t[1]))
        console.print(
            f"[dim]Plus {len(trivial)} modules under {human(MODULE_FLOOR)} each, "
            f"{human(sum(s for _n, s in trivial))} combined: {names}[/dim]\n"
        )
