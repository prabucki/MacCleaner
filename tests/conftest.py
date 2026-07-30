"""
Shared test fixtures, plus a hard guard on the real filesystem.

The guard exists because of a real incident during development: a test pointed the
runtime at ``~/Downloads/*`` expecting it to be refused, but a home-resolution mismatch
meant the pattern expanded against the *real* home while the policy checked a different
one. The whole of Downloads was staged into a quarantine batch. Nothing was lost — the
quarantine design meant it was a move, and the manifest restored it exactly — but no test
run should ever be able to do that.

So: the contents of the directories that must never be touched are recorded before the
session and compared after it. Any change fails the run loudly, whichever test caused it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pytest

#: Real directories no test may modify, under any circumstances.
GUARDED = ("~/Downloads", "~/Documents", "~/Desktop", "~/.ssh")


def _snapshot() -> Dict[str, List[str]]:
    """Top-level listing of each guarded directory."""

    state: Dict[str, List[str]] = {}

    for entry in GUARDED:
        directory = Path(os.path.expanduser(entry))
        if directory.is_dir():
            try:
                state[entry] = sorted(item.name for item in directory.iterdir())
            except PermissionError:
                state[entry] = ["<unreadable>"]

    return state


@pytest.fixture(scope="session", autouse=True)
def guard_real_filesystem():
    """Fail the session if any protected real directory changed while tests ran."""

    before = _snapshot()

    yield

    after = _snapshot()

    for entry, listing in before.items():
        current = after.get(entry, [])

        missing = set(listing) - set(current)
        if missing:
            pytest.fail(
                f"TESTS MODIFIED THE REAL FILESYSTEM: {len(missing)} item(s) disappeared from {entry}: "
                f"{sorted(missing)[:10]}. Check ~/.maccleaner/quarantine and any pytest tmp directory "
                f"for a manifest.jsonl to restore from.",
                pytrace=False,
            )


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """
    Keep every test out of the real ``~/.maccleaner``.

    Without this, a test that writes a report or opens a quarantine batch would litter
    (or purge) the user's actual state directory.
    """

    monkeypatch.setenv("MACCLEANER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MACCLEANER_NO_NOTIFY", "1")
    yield
