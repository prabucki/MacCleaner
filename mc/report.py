"""Run reporting — a human summary on the console and a machine-readable record on disk."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from mc.util import MC_HOME, human, iso_stamp, run

__all__ = ["RunReport", "ModuleResult", "LOG_DIR"]

LOG_DIR = MC_HOME / "logs"


@dataclass
class ModuleResult:
    """Per-module outcome."""

    name: str
    risk: str
    status: str = "pending"  # pending | ok | skipped | failed
    reason: Optional[str] = None
    bytes_reclaimed: int = 0
    bytes_staged: int = 0
    #: Dry-run measurement. Separate from the two above so a dry run never reports space
    #: as having been freed when nothing was touched.
    bytes_estimated: int = 0
    paths_removed: int = 0
    paths_denied: List[Dict[str, str]] = field(default_factory=list)
    overrides: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def bytes_total(self) -> int:
        """Everything this module accounted for, whether reclaimed, staged or estimated."""

        return self.bytes_reclaimed + self.bytes_staged + self.bytes_estimated


@dataclass
class RunReport:
    """The record of a single cleanup run."""

    stamp: str = field(default_factory=iso_stamp)
    profile: str = "standard"
    dry_run: bool = False
    quarantine: bool = True
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: Optional[str] = None
    free_before: int = 0
    free_after: int = 0
    quarantine_purged: int = 0
    estimated_bytes: int = 0
    modules: Dict[str, ModuleResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    host: Dict[str, str] = field(default_factory=lambda: {"os": platform.mac_ver()[0], "arch": platform.machine()})

    # -- accumulation ------------------------------------------------------------------

    def module(self, name: str, risk: str = "standard") -> ModuleResult:
        """Get (creating if needed) the result record for a module."""

        if name not in self.modules:
            self.modules[name] = ModuleResult(name=name, risk=risk)

        return self.modules[name]

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- derived figures ---------------------------------------------------------------

    @property
    def total_reclaimed(self) -> int:
        """Bytes deleted outright during this run."""

        return sum(m.bytes_reclaimed for m in self.modules.values())

    @property
    def total_staged(self) -> int:
        """Bytes moved into quarantine — space that returns when the batch is purged."""

        return sum(m.bytes_staged for m in self.modules.values())

    @property
    def free_space_delta(self) -> int:
        """Actual measured change in free space, which is the number that matters."""

        return self.free_after - self.free_before

    @property
    def ran(self) -> List[ModuleResult]:
        return [m for m in self.modules.values() if m.status == "ok"]

    @property
    def skipped(self) -> List[ModuleResult]:
        return [m for m in self.modules.values() if m.status == "skipped"]

    @property
    def failed(self) -> List[ModuleResult]:
        return [m for m in self.modules.values() if m.status == "failed"]

    # -- output ------------------------------------------------------------------------

    def to_dict(self) -> Dict:
        payload = asdict(self)
        payload["modules"] = {name: asdict(result) for name, result in self.modules.items()}
        payload["totals"] = {
            "reclaimed": self.total_reclaimed,
            "staged": self.total_staged,
            "free_space_delta": self.free_space_delta,
            "quarantine_purged": self.quarantine_purged,
        }
        return payload

    def write(self, *, log_dir: Path = LOG_DIR) -> Path:
        """Persist the JSON record. Returns the path written."""

        self.finished_at = datetime.now().isoformat(timespec="seconds")
        log_dir.mkdir(parents=True, exist_ok=True)

        destination = log_dir / f"{self.stamp}.json"
        destination.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

        self._prune_old_logs(log_dir)

        return destination

    @staticmethod
    def _prune_old_logs(log_dir: Path, keep: int = 60) -> None:
        """Keep the log directory from becoming the thing that needs cleaning."""

        logs = sorted(log_dir.glob("*.json"))
        for stale in logs[:-keep]:
            try:
                stale.unlink()
            except OSError:
                pass

    def render(self, console) -> None:
        """Print the end-of-run summary."""

        from rich.table import Table

        table = Table(title=None, show_header=True, header_style="info", box=None, pad_edge=False)
        table.add_column("Module", style="bold", no_wrap=True)
        table.add_column("Status", no_wrap=True)

        if self.dry_run:
            table.add_column("Would clean", justify="right", no_wrap=True)
        else:
            table.add_column("Reclaimed", justify="right", no_wrap=True)
            table.add_column("Staged", justify="right", no_wrap=True)

        table.add_column("Note", overflow="fold")

        style_for = {"ok": "success", "skipped": "warning", "failed": "danger", "pending": "warning"}

        for result in sorted(self.modules.values(), key=lambda m: -m.bytes_total):
            if result.status == "ok" and result.bytes_total == 0 and not result.errors:
                continue  # ran, found nothing, nothing to say

            note = result.reason or ""
            if result.errors:
                note = (note + " " if note else "") + f"[danger]{len(result.errors)} error(s)[/danger]"
            if result.paths_denied:
                note = (note + " " if note else "") + f"{len(result.paths_denied)} denied by policy"

            sizes = (
                [human(result.bytes_estimated) if result.bytes_estimated else "-"]
                if self.dry_run
                else [
                    human(result.bytes_reclaimed) if result.bytes_reclaimed else "-",
                    human(result.bytes_staged) if result.bytes_staged else "-",
                ]
            )

            table.add_row(
                result.name,
                f"[{style_for.get(result.status, 'info')}]{result.status}[/]",
                *sizes,
                note,
            )

        console.print(table)

        quiet = [m.name for m in self.modules.values() if m.status == "ok" and m.bytes_total == 0 and not m.errors]
        if quiet:
            console.print(f"[info]{len(quiet)} module(s) ran with nothing to clean:[/info] {', '.join(sorted(quiet))}")

        for warning in self.warnings:
            console.print(f"[warning]![/warning] {warning}")

    def summary_text(self) -> str:
        """One-paragraph plaintext summary, also used as the notification body."""

        if self.dry_run:
            return f"Dry run: approximately {human(self.estimated_bytes)} could be cleaned."

        parts = [f"Reclaimed {human(self.total_reclaimed)}"]

        if self.total_staged:
            parts.append(f"staged {human(self.total_staged)} to quarantine")
        if self.quarantine_purged:
            parts.append(f"purged {human(self.quarantine_purged)} of expired quarantine")

        parts.append(f"free space {'+' if self.free_space_delta >= 0 else ''}{human(abs(self.free_space_delta))}")

        return "; ".join(parts) + "."

    def notify(self) -> None:
        """Post a macOS notification. Best-effort — never fails a run."""

        if os.environ.get("MACCLEANER_NO_NOTIFY"):
            return

        title = "MacCleaner"
        body = self.summary_text().replace('"', "'")

        run(
            ["/usr/bin/osascript", "-e", f'display notification "{body}" with title "{title}"'],
            timeout=15,
        )
