"""
Tests for the arrow-key selector.

Key decoding and the tree model are pure; they get real tests here. The interactive loop
itself is exercised end to end through a pty in the project's manual checks, because
driving termios from pytest is more fragile than the thing it would be testing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mc.tui import Node, build_tree, set_all, to_selection, toggle, visible_rows

HOME = os.path.expanduser("~")


def _details():
    return {
        ("electron_apps", "a"): [
            (Path(HOME) / f"Library/Application Support/Ferdium/Partitions/s{i}/Cache", 200 * 1024**2)
            for i in range(8)
        ],
        ("user_caches", "b"): [(Path(HOME) / ".cache/uv", 4 * 1024**3)],
        ("tiny", "c"): [(Path(HOME) / "Library/Caches/x/y", 1024)],
    }


def test_tree_is_ordered_biggest_first():
    nodes = build_tree(_details())

    assert [n.label for n in nodes] == ["user_caches", "electron_apps", "tiny"]


def test_everything_starts_selected():
    nodes = build_tree(_details())

    assert all(n.checked for n in nodes)
    assert all(c.checked for n in nodes for c in n.children)


def test_big_modules_start_expanded_small_ones_collapsed():
    nodes = build_tree(_details())
    by_name = {n.label: n for n in nodes}

    assert by_name["electron_apps"].expanded
    assert not by_name["tiny"].expanded, "a 1 KB module should not take up screen space"


def test_locations_are_capped_with_a_rollup_row():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")

    assert len(electron.children) == 6, "5 locations plus one '+ N more' row"
    assert electron.children[-1].kind == "more"
    assert len(electron.children[-1].prefixes) == 3


def test_collapsed_children_are_not_visible():
    nodes = build_tree(_details())
    rows = visible_rows(nodes)

    assert not any(r.module == "tiny" and r.kind == "location" for r in rows)


def test_toggling_a_module_carries_its_locations():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")

    toggle(electron, nodes)

    assert electron.state == "off"
    assert not any(c.checked for c in electron.children)
    assert electron.selected_size == 0


def test_toggling_one_location_makes_the_module_partial():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")
    full = electron.selected_size

    toggle(electron.children[0], nodes)

    assert electron.state == "part"
    assert 0 < electron.selected_size < full


def test_toggling_a_location_back_on_revives_its_module():
    """Otherwise re-selecting a location under a disabled module appears to do nothing."""

    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")

    toggle(electron, nodes)  # whole module off
    toggle(electron.children[0], nodes)  # one location back on

    assert electron.checked
    assert electron.state == "part"


def test_selection_excludes_unchecked_modules():
    nodes = build_tree(_details())
    toggle(next(n for n in nodes if n.label == "tiny"), nodes)

    assert to_selection(nodes).excluded_modules == {"tiny"}


def test_selection_excludes_unchecked_locations():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")
    toggle(electron.children[0], nodes)

    selection = to_selection(nodes)

    assert selection.excluded_modules == set()
    assert selection.excluded_prefixes == set(electron.children[0].prefixes)


def test_rollup_row_excludes_everything_it_covers():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")
    more = electron.children[-1]

    toggle(more, nodes)

    assert to_selection(nodes).excluded_prefixes == set(more.prefixes)


def test_select_none_then_all_round_trips():
    nodes = build_tree(_details())

    set_all(nodes, False)
    assert to_selection(nodes).excluded_modules == {"electron_apps", "user_caches", "tiny"}

    set_all(nodes, True)
    assert to_selection(nodes).is_empty


def test_selected_size_tracks_toggles():
    nodes = build_tree(_details())
    total = sum(n.selected_size for n in nodes)

    toggle(next(n for n in nodes if n.label == "user_caches"), nodes)

    assert sum(n.selected_size for n in nodes) == total - 4 * 1024**3


# --------------------------------------------------------------------------------------
# Key decoding
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sent,expected",
    [
        (b"\x1b[A", "up"),
        (b"\x1b[B", "down"),
        (b"\x1b[C", "right"),
        (b"\x1b[D", "left"),
        (b" ", "space"),
        (b"\r", "enter"),
        (b"\n", "enter"),
        (b"q", "cancel"),
        (b"\x03", "cancel"),  # Ctrl-C arrives as a byte in cbreak mode, not a signal
        (b"\x04", "cancel"),
        (b"j", "down"),
        (b"k", "up"),
        (b"a", "all"),
        (b"n", "none"),
    ],
)
def test_key_decoding(sent, expected):
    from mc.tui import _read_key

    # A pipe, not a pty: a pty in canonical mode buffers until a newline, so reading a
    # bare escape sequence from one blocks forever. _read_key only needs a readable fd.
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, sent)
        assert _read_key(read_fd) == expected
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_ctrl_c_is_decoded_as_cancel_not_ignored():
    """
    In cbreak mode Ctrl-C is delivered as \\x03 rather than raising KeyboardInterrupt.

    If it were not decoded the selector would swallow it, which is exactly the "I can't
    even cancel with ctrl c" failure this whole area already produced once.
    """

    from mc.tui import _read_key

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x03")
        assert _read_key(read_fd) == "cancel"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_bare_escape_is_not_mistaken_for_a_sequence():
    from mc.tui import _read_key

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x1b")
        assert _read_key(read_fd) == "escape"
    finally:
        os.close(read_fd)
        os.close(write_fd)


# --------------------------------------------------------------------------------------
# Label fitting
# --------------------------------------------------------------------------------------


def test_fit_keeps_the_tail_where_paths_differ():
    """
    Five Ferdium partitions differ only in a UUID near the end.

    Middle-elision rendered them as identical visible text, which is what made the list
    unreadable. Cutting from the front keeps what distinguishes them.
    """

    from mc.tui import _fit

    a = "service-74b0d2c6-1139-47d1-b3d2-bd122d7aedf2/Cache/"
    b = "service-868feca5-52a4-437f-9422-e3f74783fb38/Cache/"

    assert _fit(a, 30) != _fit(b, 30), "trimmed labels must still differ"
    assert _fit(a, 30).endswith("Cache/")
    assert len(_fit(a, 30)) == 30


def test_fit_leaves_short_text_alone():
    from mc.tui import _fit

    assert _fit("~/.npm/_npx/", 40) == "~/.npm/_npx/"


def test_shared_root_is_hoisted_off_siblings():
    from mc.tui import _relative_label, _shared_root

    base = f"{HOME}/Library/Application Support/Ferdium/Partitions"
    dirs = [f"{base}/service-{n}/Cache" for n in ("aaa", "bbb", "ccc")]

    root = _shared_root(dirs)

    assert root == base
    assert _relative_label(dirs[0], root) == "service-aaa/Cache/"


def test_shared_root_ignored_when_too_short_to_help():
    from mc.tui import _shared_root

    assert _shared_root(["/tmp/a", "/tmp/b"]) == ""


def test_module_row_carries_the_root_its_children_are_relative_to():
    nodes = build_tree(_details())
    electron = next(n for n in nodes if n.label == "electron_apps")

    assert electron.root.endswith("Ferdium/Partitions")
    assert not electron.children[0].label.startswith("/"), "children should be relative"
