"""
Tests for the Full Disk Access probe.

The per-user TCC database does not exist on every macOS release. Probing only that path
made the check report "not granted" forever, however the grant was set — so a missing
file must be skipped, and only a permission error may count as a denial.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mc import preflight


def _fake_open(outcomes: dict[str, BaseException | None]):
    real_open = Path.open

    def fake(self: Path, *args, **kwargs):
        for suffix, outcome in outcomes.items():
            if str(self).startswith(suffix):
                if outcome is not None:
                    raise outcome
                return real_open(Path(__file__), "rb")
        raise AssertionError(f"unexpected probe {self}")

    return fake


USER = str(Path.home())
SYSTEM = "/Library/Application Support/com.apple.TCC"


@pytest.mark.parametrize(
    ("user", "system", "expected"),
    [
        (None, None, True),
        (FileNotFoundError(), None, True),
        (FileNotFoundError(), PermissionError(), False),
        (PermissionError(), None, False),
        (FileNotFoundError(), FileNotFoundError(), False),
    ],
)
def test_full_disk_access_probe(monkeypatch, user, system, expected) -> None:
    monkeypatch.setattr(Path, "open", _fake_open({USER: user, SYSTEM: system}))

    assert preflight.has_full_disk_access() is expected
