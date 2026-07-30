"""
Arrow-key selector for the review gate.

A tree of modules with their biggest locations underneath, navigated with the arrow keys,
toggled with space, confirmed with enter. Everything starts selected, so doing nothing and
pressing enter runs exactly what the plan said.

This reads single keypresses, which means putting the terminal into raw mode. That is the
same state a SIGKILLed topgrade once left behind and stranded the whole prompt, so the
restore here is in a ``finally`` that runs on every exit path, including an exception —
plus a belt-and-braces :func:`mc.util.restore_terminal` call after it.

Falls back to the typed-number prompt in :mod:`mc.review` when there is no usable
terminal, so nothing here can block a scheduled run.
"""

from __future__ import annotations

import os
import select
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mc.breakdown import _elide, _group_by_ancestor, _leaf_summary
from mc.util import human, restore_terminal

__all__ = ["run_selector", "build_tree", "Node", "tui_available"]

#: Locations shown per module before the rest collapse into one "+ N more" row.
LOCATIONS_PER_MODULE = 5

#: Modules contributing less than this start collapsed — still selectable, just quiet.
COLLAPSE_UNDER = 100 * 1024 * 1024


@dataclass
class Node:
    """One row in the tree."""

    kind: str  # "module" | "location" | "more"
    label: str
    size: int
    module: str
    checked: bool = True
    expanded: bool = True
    depth: int = 0
    #: Directory this row stands for ("location"), or the set it covers ("more").
    prefixes: List[str] = field(default_factory=list)
    children: List["Node"] = field(default_factory=list)

    @property
    def is_parent(self) -> bool:
        return bool(self.children)

    @property
    def state(self) -> str:
        """``on`` / ``off`` / ``part`` for a parent whose children disagree."""

        if not self.children:
            return "on" if self.checked else "off"
        if not self.checked:
            return "off"

        checked = [child.checked for child in self.children]
        if all(checked):
            return "on"
        if not any(checked):
            return "off"
        return "part"

    @property
    def selected_size(self) -> int:
        """Bytes this row contributes with the current selection."""

        if not self.checked:
            return 0
        if not self.children:
            return self.size
        return sum(child.selected_size for child in self.children)


def build_tree(details: Dict[Tuple[str, str], List[Tuple[Path, int]]]) -> List[Node]:
    """
    Turn estimate detail into the module/location tree.

    Locations come from the same roll-up the printed breakdown uses, so the selector shows
    the directories you already read about rather than a different decomposition.
    """

    per_module: Dict[str, List[Tuple[Path, int]]] = {}
    for (module, _rule), entries in details.items():
        per_module.setdefault(module, []).extend(entries)

    nodes: List[Node] = []

    for module in sorted(per_module, key=lambda m: -sum(s for _p, s in per_module[m])):
        entries = per_module[module]
        total = sum(s for _p, s in entries)

        groups = _group_by_ancestor(entries, max_groups=40)
        head, tail = groups[:LOCATIONS_PER_MODULE], groups[LOCATIONS_PER_MODULE:]

        children: List[Node] = [
            Node(
                kind="location",
                label=f"{_elide(directory)}/",
                size=size,
                module=module,
                depth=1,
                prefixes=[directory],
                # Shown under the row, same as the printed breakdown.
                children=[],
                expanded=False,
            )
            for directory, size, _count, _members in head
        ]

        # Keep the leaf summary for display without making it a selectable row.
        for child, (directory, _size, _count, members) in zip(children, head):
            child.label = f"{_elide(directory)}/"
            child.expanded = False
            child.prefixes = [directory]
            child.__dict__["leaves"] = _leaf_summary(directory, members, limit=2)

        if tail:
            children.append(
                Node(
                    kind="more",
                    label=f"+ {len(tail)} more locations",
                    size=sum(s for _d, s, _c, _m in tail),
                    module=module,
                    depth=1,
                    prefixes=[d for d, _s, _c, _m in tail],
                )
            )

        nodes.append(
            Node(
                kind="module",
                label=module,
                size=total,
                module=module,
                depth=0,
                children=children,
                expanded=total >= COLLAPSE_UNDER,
            )
        )

    return nodes


def visible_rows(nodes: Sequence[Node]) -> List[Node]:
    """Flatten the tree, honouring collapsed parents."""

    rows: List[Node] = []
    for node in nodes:
        rows.append(node)
        if node.expanded:
            rows.extend(node.children)
    return rows


def toggle(node: Node, nodes: Sequence[Node]) -> None:
    """
    Flip a row.

    A module carries its locations with it. Flipping a location on turns its module back
    on too, otherwise the click would appear to do nothing.
    """

    if node.kind == "module":
        node.checked = not node.checked
        for child in node.children:
            child.checked = node.checked
        return

    node.checked = not node.checked

    parent = next((n for n in nodes if n.module == node.module and n.kind == "module"), None)
    if parent and node.checked:
        parent.checked = True


def set_all(nodes: Sequence[Node], checked: bool) -> None:
    for node in nodes:
        node.checked = checked
        for child in node.children:
            child.checked = checked


def to_selection(nodes: Sequence[Node]):
    """Convert the tree into a :class:`mc.review.Selection`."""

    from mc.review import Selection

    selection = Selection()

    for node in nodes:
        if not node.checked:
            selection.excluded_modules.add(node.module)
            continue
        for child in node.children:
            if not child.checked:
                selection.excluded_prefixes.update(child.prefixes)

    return selection


# --------------------------------------------------------------------------------------
# Terminal plumbing
# --------------------------------------------------------------------------------------


def tui_available() -> bool:
    """Whether a full-screen selector can run here."""

    if os.environ.get("MACCLEANER_NO_TUI"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False

    try:
        import termios  # noqa: F401

        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


_KEYS = {
    b"\x1b[A": "up",
    b"\x1b[B": "down",
    b"\x1b[C": "right",
    b"\x1b[D": "left",
    b"\x1b[5~": "pgup",
    b"\x1b[6~": "pgdn",
    b"\x1b[H": "home",
    b"\x1b[F": "end",
}


def _read_key(fd: int) -> str:
    """Read one keypress, decoding the escape sequences the arrow keys send."""

    first = os.read(fd, 1)

    if first == b"\x1b":
        # Escape alone, or the start of a sequence. A short wait tells them apart.
        ready, _, _ = select.select([fd], [], [], 0.06)
        if not ready:
            return "escape"
        rest = os.read(fd, 5)
        return _KEYS.get(b"\x1b" + rest, "unknown")

    return {
        b" ": "space",
        b"\r": "enter",
        b"\n": "enter",
        b"\x03": "cancel",  # Ctrl-C: raw mode means we see the byte, not a signal
        b"\x04": "cancel",  # Ctrl-D
        b"q": "cancel",
        b"k": "up",
        b"j": "down",
        b"h": "left",
        b"l": "right",
        b"a": "all",
        b"n": "none",
        b"g": "home",
        b"G": "end",
        b"\t": "down",
    }.get(first, "unknown")


def _render(console, nodes: List[Node], cursor: int, offset: int, height: int, total: int) -> None:
    """Draw the visible slice of the tree."""

    from rich.text import Text

    rows = visible_rows(nodes)
    selected = sum(node.selected_size for node in nodes)

    header = Text()
    header.append("Select what to clean", style="cyan bold")
    header.append(f"  {human(selected)}", style="bold green")
    if selected != total:
        header.append(f" of {human(total)}", style="dim")
    console.print(header)
    console.print(
        Text("↑↓ move · space toggle · →← expand/collapse · a all · n none · enter run · q cancel", style="dim")
    )
    console.print()

    marks = {"on": ("[x]", "green"), "off": ("[ ]", "red"), "part": ("[~]", "yellow")}

    for index in range(offset, min(offset + height, len(rows))):
        node = rows[index]
        mark, colour = marks[node.state]
        here = index == cursor

        line = Text()
        line.append("❯ " if here else "  ", style="cyan bold")
        line.append("  " * node.depth)
        line.append(mark + " ", style=colour)

        if node.kind == "module":
            arrow = "▾ " if node.expanded else "▸ " if node.children else "  "
            line.append(arrow, style="dim")
            line.append(node.label, style="bold" if node.checked else "dim")
        else:
            line.append(node.label, style="" if node.checked else "dim")

        line.append(f"  {human(node.selected_size if node.checked else node.size)}", style="dim")

        leaves = node.__dict__.get("leaves")
        if leaves and node.kind == "location":
            line.append(f"  └ {leaves}", style="dim")

        console.print(line, no_wrap=True, overflow="ellipsis", highlight=False)

    if len(rows) > offset + height:
        console.print(Text(f"  … {len(rows) - offset - height} more rows below", style="dim"))


def run_selector(console, details, *, total: int):
    """
    Show the tree selector and return a :class:`mc.review.Selection`.

    :raises RuntimeError: If the terminal cannot support it; the caller should fall back.
    """

    import termios
    import tty

    if not tui_available():
        raise RuntimeError("no usable terminal for the selector")

    nodes = build_tree(details)
    if not nodes:
        from mc.review import Selection

        return Selection()

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    cursor = offset = 0
    cancelled = False

    try:
        tty.setcbreak(fd)  # cbreak, not full raw: keeps output post-processing sane

        while True:
            rows = visible_rows(nodes)
            cursor = max(0, min(cursor, len(rows) - 1))

            height = max(6, (console.size.height or 24) - 7)
            if cursor < offset:
                offset = cursor
            elif cursor >= offset + height:
                offset = cursor - height + 1

            console.clear()
            _render(console, nodes, cursor, offset, height, total)

            key = _read_key(fd)
            rows = visible_rows(nodes)

            if key == "enter":
                break
            if key in ("cancel", "escape"):
                cancelled = True
                break
            if key == "down":
                cursor += 1
            elif key == "up":
                cursor -= 1
            elif key == "pgdn":
                cursor += height
            elif key == "pgup":
                cursor -= height
            elif key == "home":
                cursor = 0
            elif key == "end":
                cursor = len(rows) - 1
            elif key == "space":
                toggle(rows[cursor], nodes)
            elif key == "right":
                node = rows[cursor]
                if node.children:
                    node.expanded = True
            elif key == "left":
                node = rows[cursor]
                if node.kind == "module" and node.expanded:
                    node.expanded = False
                elif node.kind != "module":
                    # Jump to the parent, so left repeatedly walks out of the tree.
                    parent = next(
                        (i for i, r in enumerate(visible_rows(nodes))
                         if r.kind == "module" and r.module == node.module),
                        cursor,
                    )
                    cursor = parent
            elif key == "all":
                set_all(nodes, True)
            elif key == "none":
                set_all(nodes, False)

            cursor = max(0, min(cursor, len(visible_rows(nodes)) - 1))
    finally:
        # Every exit path, including an exception, must put the terminal back. Getting
        # this wrong is what stranded the prompt before.
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except Exception:  # noqa: BLE001
            pass
        restore_terminal()
        console.show_cursor(True)

    selection = to_selection(nodes)
    selection.cancelled = cancelled

    return selection
