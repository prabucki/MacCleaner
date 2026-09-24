"""The end-of-run summary line must not make a drop in free space read as a total."""

from __future__ import annotations

from mc.report import RunReport

GB = 1024**3


def _report(before: int, after: int) -> RunReport:
    report = RunReport()
    report.free_before = before
    report.free_after = after
    return report


def test_drop_in_free_space_is_signed() -> None:
    assert "free space -2.00 GB (8.00 GB free)" in _report(10 * GB, 8 * GB).summary_text()


def test_gain_in_free_space_is_signed() -> None:
    assert "free space +2.00 GB (12.00 GB free)" in _report(10 * GB, 12 * GB).summary_text()
