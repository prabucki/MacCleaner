"""quarantine.totals(): what the end-of-run panel reports as held in quarantine."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from mc import quarantine


def _batch(root: Path, age_days: float, files: dict) -> None:
    stamp = (datetime.now() - timedelta(days=age_days)).strftime(quarantine._STAMP_FORMAT)
    payload = root / stamp / quarantine._PAYLOAD_DIR
    for rel, size in files.items():
        target = payload / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)


def test_empty_root(tmp_path: Path) -> None:
    held = quarantine.totals(root=tmp_path / "missing")

    assert (held.batches, held.total_bytes, held.files) == (0, 0, 0)


def test_counts_files_bytes_and_oldest_across_batches(tmp_path: Path) -> None:
    _batch(tmp_path, 3, {"a/one.bin": 100, "a/two.bin": 200})
    _batch(tmp_path, 1, {"b/three.bin": 300})
    (tmp_path / "not-a-batch").mkdir()

    held = quarantine.totals(root=tmp_path)

    assert held.batches == 2
    assert held.files == 3
    assert held.total_bytes >= 600  # directory entries count too, as in path_size
    assert 2.9 < held.oldest_age_days < 3.1
