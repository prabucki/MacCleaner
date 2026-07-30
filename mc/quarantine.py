"""
Quarantine — deletions are staged, not destroyed.

Anything above the ``safe`` tier is *moved* into ``~/.maccleaner/quarantine/<stamp>/``
rather than removed. On the same APFS volume a move is a metadata-only operation, so a
14 GB batch is staged instantly. The batch is deleted for real at the start of the next
run once it is older than the retention window.

The consequence, stated plainly: disk space is not reclaimed at the moment of cleaning.
It comes back when the batch is purged. :func:`purge_expired` runs first in every
cleanup precisely so that the space arrives before the new run needs it.

The manifest is written as JSON Lines, appended as each path is staged, so a batch
interrupted by a crash or a kill is still fully restorable.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

from mc import policy
from mc.util import MC_HOME, human, iso_stamp, path_size, same_volume

__all__ = ["QuarantineBatch", "Entry", "purge_expired", "list_batches", "restore"]

QUARANTINE_ROOT = MC_HOME / "quarantine"

#: Batches older than this are deleted for real at the start of the next run.
DEFAULT_RETENTION_DAYS = 7

_MANIFEST_NAME = "manifest.jsonl"
_PAYLOAD_DIR = "payload"
_STAMP_FORMAT = "%Y-%m-%dT%H-%M-%S"


@dataclass(frozen=True)
class Entry:
    """One staged path."""

    original: str
    staged: str
    size: int
    module: str

    @classmethod
    def from_json(cls, raw: dict) -> "Entry":
        return cls(
            original=raw["original"], staged=raw["staged"], size=int(raw.get("size", 0)), module=raw.get("module", "?")
        )


class QuarantineBatch:
    """
    A single run's worth of staged deletions.

    Created lazily — no directory is made on disk until something is actually staged, so
    a dry run or a clean-nothing run leaves no trace.
    """

    def __init__(self, stamp: Optional[str] = None, *, root: Path = QUARANTINE_ROOT):
        self.stamp = stamp or iso_stamp()
        self.root = root / self.stamp
        self._manifest_handle = None
        self._entries: List[Entry] = []

    # -- lifecycle ---------------------------------------------------------------------

    @property
    def payload_root(self) -> Path:
        return self.root / _PAYLOAD_DIR

    @property
    def manifest_path(self) -> Path:
        return self.root / _MANIFEST_NAME

    def _ensure_open(self) -> None:
        if self._manifest_handle is None:
            self.payload_root.mkdir(parents=True, exist_ok=True)
            self._manifest_handle = self.manifest_path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._manifest_handle is not None:
            self._manifest_handle.close()
            self._manifest_handle = None

    def __enter__(self) -> "QuarantineBatch":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- staging -----------------------------------------------------------------------

    def can_stage(self, target: Path) -> bool:
        """
        Whether ``target`` can be staged rather than deleted outright.

        False for cross-volume paths — staging those would mean a byte-for-byte copy of
        data we are trying to get rid of. Callers fall back to a direct delete.
        """

        return same_volume(target, QUARANTINE_ROOT.parent)

    def staged_location(self, target: Path) -> Path:
        """
        Where ``target`` will live inside the batch.

        The original absolute path is preserved as a subtree under ``payload/`` so a
        restore is an unambiguous reverse mapping and two same-named directories from
        different apps cannot collide.
        """

        return self.payload_root / str(target).lstrip("/")

    def stage(self, target: Path, *, module: str = "?") -> int:
        """
        Move ``target`` into the batch.

        :param target: An existing, concrete (glob-free) path.
        :param module: Name of the module requesting this, recorded in the manifest.
        :return: Bytes staged, or 0 if nothing happened.
        """

        if not target.exists() and not target.is_symlink():
            return 0

        # Policy is re-checked here as a backstop. A module that builds a path
        # dynamically should never be able to reach a protected location just because
        # its own check was written wrong.
        blocked = policy.is_protected(str(target))
        if blocked is not None:
            raise PermissionError(f"refusing to quarantine protected path: {blocked}")

        size = path_size(target)
        destination = self.staged_location(target)

        self._ensure_open()
        destination.parent.mkdir(parents=True, exist_ok=True)

        # A previous batch entry with the same original path would collide; suffix it.
        if destination.exists():
            suffix = 1
            while destination.with_name(f"{destination.name}.{suffix}").exists():
                suffix += 1
            destination = destination.with_name(f"{destination.name}.{suffix}")

        shutil.move(str(target), str(destination))

        entry = Entry(original=str(target), staged=str(destination), size=size, module=module)
        self._entries.append(entry)
        self._manifest_handle.write(json.dumps(entry.__dict__) + "\n")
        self._manifest_handle.flush()

        return size

    def record_external(self, entry: Entry) -> None:
        """
        Record a stage performed by ``mc-root`` on our behalf.

        Root-owned paths cannot be moved by the user-side process, so the helper does the
        move and reports back; the manifest is still owned and written here so that
        restore has one consistent view of the batch.
        """

        self._ensure_open()
        self._entries.append(entry)
        self._manifest_handle.write(json.dumps(entry.__dict__) + "\n")
        self._manifest_handle.flush()

    # -- reporting ---------------------------------------------------------------------

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self._entries)

    @property
    def entries(self) -> List[Entry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------------------
# Batch management
# --------------------------------------------------------------------------------------


def _read_manifest(batch_dir: Path) -> List[Entry]:
    manifest = batch_dir / _MANIFEST_NAME
    if not manifest.is_file():
        return []

    entries: List[Entry] = []
    with manifest.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(Entry.from_json(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue  # a torn final line from a hard kill; the rest is still good

    return entries


def list_batches(*, root: Path = QUARANTINE_ROOT) -> Iterator[tuple]:
    """
    Yield ``(stamp, age_days, total_bytes, entry_count)`` for every batch on disk, oldest first.
    """

    if not root.is_dir():
        return

    for batch_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            created = datetime.strptime(batch_dir.name, _STAMP_FORMAT)
        except ValueError:
            continue

        # Measured on disk, not summed from the manifest. After a restore the manifest
        # still lists everything it ever staged, and reporting that as space held would
        # promise a reclaim that is not there.
        entries = [entry for entry in _read_manifest(batch_dir) if Path(entry.staged).exists()]
        age_days = (datetime.now() - created).total_seconds() / 86400

        yield batch_dir.name, age_days, path_size(batch_dir / _PAYLOAD_DIR), len(entries)


def purge_expired(
    *, retention_days: int = DEFAULT_RETENTION_DAYS, root: Path = QUARANTINE_ROOT, privileged_client=None
) -> int:
    """
    Delete batches older than the retention window. This is where space actually returns.

    :param retention_days: Batches older than this many days are removed.
    :param privileged_client: Optional :class:`mc.privileged.Privileged` used to remove
        root-owned payloads the user cannot unlink.
    :return: Bytes reclaimed.
    """

    if not root.is_dir():
        return 0

    reclaimed = 0
    cutoff = datetime.now() - timedelta(days=retention_days)

    for batch_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            created = datetime.strptime(batch_dir.name, _STAMP_FORMAT)
        except ValueError:
            continue

        if created > cutoff:
            continue

        size = path_size(batch_dir)

        try:
            shutil.rmtree(batch_dir)
        except PermissionError:
            # Root-owned payload (staged from /Library, /var/log, ...).
            if privileged_client is None or not privileged_client.available:
                continue
            privileged_client.purge_quarantine(batch_dir)

        if not batch_dir.exists():
            reclaimed += size

    return reclaimed


def restore(stamp: str, *, root: Path = QUARANTINE_ROOT, privileged_client=None) -> tuple:
    """
    Put a batch back where it came from.

    Entries are replayed in reverse order so that nested paths are restored before their
    parents are re-created, and an entry whose original location is now occupied is
    skipped rather than clobbering newer data.

    :return: ``(restored_count, skipped_count, errors)``
    """

    batch_dir = root / stamp
    if not batch_dir.is_dir():
        raise FileNotFoundError(f"no quarantine batch named {stamp}")

    restored = skipped = 0
    errors: List[str] = []

    for entry in reversed(_read_manifest(batch_dir)):
        source, destination = Path(entry.staged), Path(entry.original)

        if not source.exists() and not source.is_symlink():
            errors.append(f"missing from quarantine: {entry.staged}")
            continue

        if destination.exists() or destination.is_symlink():
            skipped += 1
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            restored += 1
        except PermissionError:
            if privileged_client is not None and privileged_client.available:
                if privileged_client.unstage(source, destination):
                    restored += 1
                    continue
            errors.append(f"permission denied restoring {entry.original}")
        except OSError as exc:
            errors.append(f"{entry.original}: {exc}")

    # A fully drained batch is removed outright rather than left as an empty shell that
    # still shows up in --list-quarantine. The run report in ~/.maccleaner/logs keeps the
    # historical record.
    # Only files count as remaining payload. Restoring leaves the empty parent
    # directories that were created to mirror the original paths, so a plain
    # "is anything here" check would never consider a batch drained.
    payload = batch_dir / _PAYLOAD_DIR
    drained = not payload.is_dir() or not any(
        entry.is_file() or entry.is_symlink() for entry in payload.rglob("*")
    )

    if drained and not errors:
        shutil.rmtree(batch_dir, ignore_errors=True)

    return restored, skipped, errors


def summary_line() -> str:  # pragma: no cover - presentation only
    """One-line description of what is currently sitting in quarantine."""

    batches = list(list_batches())
    if not batches:
        return "Quarantine is empty."

    total = sum(size for _, _, size, _ in batches)
    oldest_age = max(age for _, age, _, _ in batches)

    return (
        f"{len(batches)} batch(es) holding {human(total)}; "
        f"oldest is {oldest_age:.1f} days old "
        f"(purged at {DEFAULT_RETENTION_DAYS} days)"
    )
