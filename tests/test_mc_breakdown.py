"""
Tests for the rolled-up dry-run report.

The grouping is the whole point: a flat list of 42 GB of paths is not something anyone
reads before authorising a delete. These pin the two behaviours that make it readable —
rolling up near-duplicate paths, and refusing to roll up so far that the answer stops
being useful.
"""

from __future__ import annotations

import os
from pathlib import Path

from mc.breakdown import _floor, _group_by_ancestor

HOME = os.path.expanduser("~")


def _entries(*specs):
    """Build (path, size) pairs from ``("relative/path", size)`` specs."""

    return [(Path(HOME) / rel, size) for rel, size in specs]


def test_near_duplicates_roll_into_one_line():
    """
    52 per-service cache directories should become one row, not 52.

    This is the Ferdium/Chromium-profile shape: same structure repeated under
    machine-generated directory names.
    """

    entries = _entries(
        *[(f"Library/Application Support/Ferdium/Partitions/service-{i:02d}/Cache", 1000) for i in range(52)]
    )

    groups = _group_by_ancestor(entries)

    assert len(groups) == 1
    directory, total, count, members = groups[0]
    assert directory.endswith("Ferdium/Partitions")
    assert (total, count) == (52_000, 52)
    assert len(members) == 52, "member paths are needed to name what is removed"


def test_rollup_stops_at_a_meaningful_directory():
    """
    Never roll up to "/" or "~".

    An earlier version walked until everything fit in one bucket and produced
    "4.22 GB under //" — true, and useless.
    """

    entries = _entries(
        ("Library/Caches/A/x", 10), ("Library/Caches/B/x", 10), (".cache/uv/x", 10),
        (".npm/_cacache/x", 10), ("Library/Logs/App/x", 10), ("Library/WebKit/App/x", 10),
        ("Documents/a/x", 10), ("Desktop/b/x", 10), ("Movies/c/x", 10),
    )

    for directory, _total, _count, _members in _group_by_ancestor(entries, max_groups=1):
        assert directory not in ("/", HOME, os.path.dirname(HOME)), f"rolled up to {directory}"
        assert directory.startswith(HOME)


def test_per_app_containers_keep_the_app_name():
    """
    Rolling Ferdium, Claude and Notion into "Application Support/" tells you nothing.

    Directories that hold one subdirectory per app keep that subdirectory.
    """

    entries = _entries(
        ("Library/Application Support/Ferdium/Cache/x", 100),
        ("Library/Application Support/Claude/Cache/x", 100),
        ("Library/Application Support/Notion/Cache/x", 100),
    )

    directories = {d for d, _t, _c, _m in _group_by_ancestor(entries, max_groups=1)}

    assert any(d.endswith("/Ferdium") for d in directories)
    assert any(d.endswith("/Claude") for d in directories)
    assert any(d.endswith("/Notion") for d in directories)


def test_floor_keeps_two_components_inside_home():
    assert _floor(f"{HOME}/.cache/uv/builds") == f"{HOME}/.cache/uv"
    assert _floor(f"{HOME}/.npm/_cacache/index") == f"{HOME}/.npm/_cacache"


def test_floor_keeps_three_components_outside_home():
    assert _floor("/private/var/db/diagnostics/x") == "/private/var/db"
    assert _floor("/Library/Caches/Foo/Bar") == "/Library/Caches/Foo"


def test_totals_are_preserved_by_grouping():
    """Roll-up must not lose or double-count bytes."""

    entries = _entries(
        *[(f"Library/Caches/App{i}/blob", (i + 1) * 1000) for i in range(30)]
    )
    expected = sum(size for _p, size in entries)

    assert sum(total for _d, total, _c, _m in _group_by_ancestor(entries)) == expected
    assert sum(count for _d, _t, count, _m in _group_by_ancestor(entries)) == len(entries)


def test_groups_are_ordered_largest_first():
    entries = _entries(
        ("Library/Caches/Small/x", 10), ("Library/Caches/Big/x", 10_000),
        ("Library/Caches/Medium/x", 500),
    )

    sizes = [total for _d, total, _c, _m in _group_by_ancestor(entries)]

    assert sizes == sorted(sizes, reverse=True)
