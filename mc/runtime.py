"""
The execution runtime — where a ``Path`` module actually turns into a deletion.

Installed into ``mac_cleanup.core_modules`` via ``set_runtime()``. Every deletion in the
whole program funnels through :meth:`Runtime.delete_path`, which is the single place
that decides between:

* refusing (policy said no),
* staging into quarantine,
* deleting directly,
* handing the path to the root helper.

Concrete paths are re-checked against the policy *after* glob expansion. A pattern that
looks harmless can expand to something that is not, and the pattern-level check alone
would miss it.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path as Path_
from typing import List, Optional, Sequence

from mc import policy
from mc.privileged import Privileged
from mc.quarantine import Entry, QuarantineBatch
from mc.report import RunReport
from mc.util import path_size

__all__ = ["Runtime"]


class Runtime:
    """
    Deletion strategy for one run.

    :param privileged: Root-helper client.
    :param batch: Quarantine batch to stage into, or None to always delete directly.
    :param report: Report to accumulate per-module figures into.
    :param dry_run: When True nothing is deleted; sizes are still measured.
    """

    def __init__(
        self,
        *,
        privileged: Privileged,
        batch: Optional[QuarantineBatch],
        report: RunReport,
        dry_run: bool = False,
    ):
        self.privileged = privileged
        self.batch = batch
        self.report = report
        self.dry_run = dry_run

    # -- entry point -------------------------------------------------------------------

    def delete_path(self, path_module) -> None:
        """
        Execute one ``mac_cleanup.core_modules.Path``.

        Called by the patched ``Path._execute``. Returns None; all outcomes are recorded
        on the report rather than returned, because the upstream execution loop discards
        return values anyway.
        """

        pattern = path_module.get_path.as_posix()
        result = self.report.module(getattr(path_module, "owner", "unknown"))

        use_quarantine = path_module.quarantine_preference
        if use_quarantine is None:
            use_quarantine = self.batch is not None

        decision = policy.check(pattern, privileged=path_module.is_privileged, override=path_module.has_override)

        if not decision.allowed:
            result.paths_denied.append({"path": pattern, "reason": decision.reason or "denied"})
            return

        if path_module.has_override:
            result.overrides.append({"path": pattern, "reason": path_module.override_reason})

        if self.dry_run:
            return

        if path_module.is_privileged:
            self._handle_privileged(pattern, result, use_quarantine)
        else:
            self._handle_local(pattern, result, use_quarantine, path_module.has_override)

    # -- unprivileged ------------------------------------------------------------------

    def _handle_local(self, pattern: str, result, use_quarantine: bool, override: bool) -> None:
        """Delete or stage paths the current user owns."""

        for concrete in self._expand(pattern):
            verdict = policy.check(str(concrete), privileged=False, override=override)
            if not verdict.allowed:
                result.paths_denied.append({"path": str(concrete), "reason": verdict.reason or "denied"})
                continue

            escape = policy.resolve_escapes(str(concrete))
            if escape is not None:
                result.paths_denied.append({"path": str(concrete), "reason": escape})
                continue

            if use_quarantine and self.batch is not None and self.batch.can_stage(concrete):
                self._stage_local(concrete, result)
            else:
                self._delete_local(concrete, result)

    def _stage_local(self, concrete: Path_, result) -> None:
        try:
            staged = self.batch.stage(concrete, module=result.name)
            result.bytes_staged += staged
            result.paths_removed += 1
        except PermissionError:
            # Root-owned file inside an otherwise user-owned tree. Retry as root if the
            # policy permits it there; otherwise report rather than fail silently.
            self._retry_privileged(concrete, result, stage=True)
        except (OSError, shutil.Error) as exc:
            result.errors.append(f"{concrete}: {exc}")

    def _delete_local(self, concrete: Path_, result) -> None:
        size = path_size(concrete)

        try:
            if concrete.is_symlink() or not concrete.is_dir():
                concrete.unlink()
            else:
                shutil.rmtree(concrete)
            result.bytes_reclaimed += size
            result.paths_removed += 1
        except PermissionError:
            self._retry_privileged(concrete, result, stage=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return  # vanished between glob and unlink; nothing to report
            result.errors.append(f"{concrete}: {exc}")

    def _retry_privileged(self, concrete: Path_, result, *, stage: bool) -> None:
        """Escalate a permission failure to the root helper, if the allowlist permits."""

        if not self.privileged.available:
            result.errors.append(f"{concrete}: permission denied (root helper unavailable)")
            return

        if policy.is_privileged_allowed(str(concrete)) is not None:
            result.errors.append(f"{concrete}: permission denied and not on the privileged allowlist")
            return

        self._run_privileged([str(concrete)], result, stage=stage)

    # -- privileged --------------------------------------------------------------------

    def _handle_privileged(self, pattern: str, result, use_quarantine: bool) -> None:
        """
        Hand a pattern to the root helper.

        The pattern is passed through unexpanded: the helper does its own glob expansion
        and re-validates each concrete result, so it never has to trust a path list built
        on the unprivileged side.
        """

        if not self.privileged.available:
            result.errors.append(f"{pattern}: needs root but {self.privileged.unavailable_reason}")
            return

        self._run_privileged([pattern], result, stage=use_quarantine and self.batch is not None)

    def _run_privileged(self, patterns: Sequence[str], result, *, stage: bool) -> None:
        if stage and self.batch is not None:
            self.batch._ensure_open()  # materialise the batch dir so the helper can write into it
            response = self.privileged.stage_paths(self.batch.root, patterns)

            if not response.ok:
                result.errors.append(response.error or "root helper failed")
                return

            for staged in response.payload.get("staged", []):
                self.batch.record_external(
                    Entry(
                        original=staged["original"],
                        staged=staged["staged"],
                        size=int(staged.get("size", 0)),
                        module=result.name,
                    )
                )
                result.paths_removed += 1

            result.bytes_staged += response.bytes_freed
        else:
            response = self.privileged.rm_paths(patterns)

            if not response.ok:
                result.errors.append(response.error or "root helper failed")
                return

            result.bytes_reclaimed += response.bytes_freed
            result.paths_removed += len(response.payload.get("removed", []))

        for denial in response.denied:
            result.paths_denied.append(denial)
        for failure in response.errors:
            result.errors.append(f"{failure.get('path', '?')}: {failure.get('reason', 'unknown')}")

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _expand(pattern: str) -> List[Path_]:
        """
        Expand a glob to existing concrete paths.

        Patterns without metacharacters skip the glob machinery, which matters when a
        module adds a few thousand literal paths.
        """

        expanded = policy.expand(pattern)

        if not any(ch in expanded for ch in "*?["):
            candidate = Path_(expanded)
            return [candidate] if candidate.exists() or candidate.is_symlink() else []

        from glob import glob

        return [Path_(match) for match in glob(expanded)]

    # -- size estimation ---------------------------------------------------------------

    def estimate(self, path_module, seen: Optional[set] = None) -> int:
        """
        Bytes this path module would account for, honouring policy.

        Used by the dry-run pass so the estimate reflects what would *actually* be
        deleted rather than what was merely requested. Upstream's own estimator counts
        every path it was handed, including ones its safety check would later refuse.

        :param seen: Shared set of already-counted paths. Several modules legitimately
            target the same directory — the generic Electron sweeper and the Ferdium
            rules both reach Ferdium's partitions — and without this the total is
            inflated by every overlap. Deletion itself is idempotent, so the overlap only
            matters for the estimate.
        """

        pattern = path_module.get_path.as_posix()

        # A dry-run-only path is reported, never deleted — Path._execute returns before
        # reaching the runtime. Measuring is read-only, so the deletion policy does not
        # apply; that is what lets a module report the size of something it must not touch.
        if not path_module.is_dry_run_only:
            decision = policy.check(
                pattern, privileged=path_module.is_privileged, override=path_module.has_override
            )
            if not decision.allowed:
                return 0

        total = 0

        for concrete in self._expand(pattern):
            if seen is not None:
                as_text = str(concrete)

                # Skip if this path, or any directory containing it, was already counted.
                if as_text in seen or any(str(parent) in seen for parent in concrete.parents):
                    continue

                seen.add(as_text)

            total += path_size(concrete)

        return total
