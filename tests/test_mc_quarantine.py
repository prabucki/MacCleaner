"""
Quarantine tests.

Quarantine is the undo button for everything above the ``safe`` tier, so the round trip
has to be exact: what went in must come back to the same absolute path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mc import quarantine
from mc.quarantine import Entry, QuarantineBatch


@pytest.fixture()
def batch_root(tmp_path):
    root = tmp_path / "quarantine"
    root.mkdir()
    return root


@pytest.fixture()
def victim(tmp_path):
    """A directory tree standing in for something being cleaned."""

    target = tmp_path / "workspace" / "Cache"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x" * 1024)
    (target / "nested").mkdir()
    (target / "nested" / "b.bin").write_bytes(b"y" * 2048)
    return target


def test_stage_moves_and_records(batch_root, victim):
    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        size = batch.stage(victim, module="test")

    assert size >= 3072
    assert not victim.exists(), "the original must be gone after staging"
    assert len(batch) == 1

    staged = Path(batch.entries[0].staged)
    assert staged.exists()
    assert (staged / "nested" / "b.bin").read_bytes() == b"y" * 2048


def test_manifest_is_written_incrementally(batch_root, victim):
    """
    A batch interrupted mid-run must still be restorable, so the manifest is appended to
    as each path is staged rather than written at the end.
    """

    batch = QuarantineBatch("2026-01-01T00-00-00", root=batch_root)
    batch.stage(victim, module="test")

    # Read it back without closing the batch — simulating a crash right here.
    lines = batch.manifest_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["original"] == str(victim)
    assert record["module"] == "test"

    batch.close()


def test_restore_returns_everything_exactly(batch_root, victim):
    original_path = victim
    original_bytes = (victim / "a.bin").read_bytes()

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    assert not original_path.exists()

    restored, skipped, errors = quarantine.restore("2026-01-01T00-00-00", root=batch_root)

    assert (restored, skipped, errors) == (1, 0, [])
    assert original_path.exists()
    assert (original_path / "a.bin").read_bytes() == original_bytes
    assert (original_path / "nested" / "b.bin").exists()


def test_restore_does_not_clobber_newer_data(batch_root, victim):
    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    # Something recreated the directory since the clean.
    victim.mkdir(parents=True)
    (victim / "new.bin").write_bytes(b"newer")

    restored, skipped, errors = quarantine.restore("2026-01-01T00-00-00", root=batch_root)

    assert restored == 0
    assert skipped == 1
    assert (victim / "new.bin").read_bytes() == b"newer", "existing data must win"


def test_same_named_directories_from_different_apps_do_not_collide(batch_root, tmp_path):
    """
    Every app has a directory called "Cache". Preserving the full original path as the
    staged subtree is what keeps them apart.
    """

    first = tmp_path / "AppOne" / "Cache"
    second = tmp_path / "AppTwo" / "Cache"

    for path, payload in ((first, b"one"), (second, b"two")):
        path.mkdir(parents=True)
        (path / "data").write_bytes(payload)

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(first, module="test")
        batch.stage(second, module="test")

    quarantine.restore("2026-01-01T00-00-00", root=batch_root)

    assert (first / "data").read_bytes() == b"one"
    assert (second / "data").read_bytes() == b"two"


def test_protected_paths_cannot_be_staged(batch_root, monkeypatch, tmp_path):
    """
    The staging layer re-checks policy itself.

    A module that builds a path dynamically must not be able to reach a protected
    location just because its own check was written wrong.
    """

    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "keep.dmg").write_bytes(b"important")

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        with pytest.raises(PermissionError, match="protected"):
            batch.stage(downloads / "keep.dmg", module="rogue")

    assert (downloads / "keep.dmg").exists()


def test_purge_respects_retention(batch_root, victim):
    import os
    import time

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    # Fresh batch by name (dated 2026-01-01 is in the past relative to nothing here —
    # purge_expired parses the directory name, so use an obviously old and a new one).
    reclaimed = quarantine.purge_expired(retention_days=36500, root=batch_root)
    assert reclaimed == 0, "a batch inside the retention window must survive"

    reclaimed = quarantine.purge_expired(retention_days=0, root=batch_root)
    assert reclaimed > 0
    assert not (batch_root / "2026-01-01T00-00-00").exists()


def test_batch_creates_nothing_until_something_is_staged(batch_root):
    """A dry run, or a run that finds nothing, must leave no directories behind."""

    batch = QuarantineBatch("2026-01-01T00-00-00", root=batch_root)

    assert not batch.root.exists()
    assert len(batch) == 0

    batch.close()
    assert not batch.root.exists()


def test_torn_manifest_line_does_not_break_restore(batch_root, victim):
    """A hard kill can leave a partial final line; the rest must still be readable."""

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    with batch.manifest_path.open("a", encoding="utf-8") as handle:
        handle.write('{"original": "/truncated')

    restored, _, _ = quarantine.restore("2026-01-01T00-00-00", root=batch_root)
    assert restored == 1


def test_fully_restored_batch_disappears(batch_root, victim):
    """
    A drained batch must not linger in --list-quarantine.

    Restoring leaves the empty parent directories that mirrored the original paths, so
    "is anything under payload/" is not the right question — only files count.
    """

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    quarantine.restore("2026-01-01T00-00-00", root=batch_root)

    assert not (batch_root / "2026-01-01T00-00-00").exists()
    assert list(quarantine.list_batches(root=batch_root)) == []


def test_listed_size_is_measured_not_summed(batch_root, victim):
    """
    Reported size must come from disk, not the manifest.

    The manifest lists everything ever staged; after a partial restore, summing it would
    promise a reclaim that is no longer there.
    """

    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    # Remove the payload behind the manifest's back.
    import shutil as _shutil

    _shutil.rmtree(batch_root / "2026-01-01T00-00-00" / "payload")

    (_, _, size, count) = next(iter(quarantine.list_batches(root=batch_root)))

    assert size == 0
    assert count == 0


def test_list_batches_reports_sizes(batch_root, victim):
    with QuarantineBatch("2026-01-01T00-00-00", root=batch_root) as batch:
        batch.stage(victim, module="test")

    entries = list(quarantine.list_batches(root=batch_root))

    assert len(entries) == 1
    stamp, age_days, total, count = entries[0]
    assert stamp == "2026-01-01T00-00-00"
    assert total >= 3072
    assert count == 1
    assert age_days > 0
