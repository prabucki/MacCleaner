"""
Client for the root helper.

All privilege escalation funnels through :class:`Privileged`. It talks to ``mc-root``
over ``sudo -n`` (non-interactive — if the NOPASSWD rule is missing we want an immediate
failure, never a hidden password prompt on a headless run), and exposes
:attr:`Privileged.askpass_env` for the third-party tools that insist on calling sudo
themselves.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from mc.util import run

__all__ = ["Privileged", "PrivilegedResult", "INSTALL_DIR", "HELPER_PATH", "ASKPASS_PATH"]

INSTALL_DIR = Path("/usr/local/libexec/maccleaner")
HELPER_PATH = INSTALL_DIR / "mc-root"
ASKPASS_PATH = INSTALL_DIR / "mc-askpass"


@dataclass
class PrivilegedResult:
    """Parsed response from a helper verb."""

    ok: bool
    payload: Dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def bytes_freed(self) -> int:
        return int(self.payload.get("bytes", 0))

    @property
    def denied(self) -> List[Dict]:
        return list(self.payload.get("denied", []))

    @property
    def errors(self) -> List[Dict]:
        return list(self.payload.get("errors", []))


class Privileged:
    """
    Gateway to root operations.

    :param enabled: Set False to make every call a no-op — used by ``--dry-run`` and by
        ``--no-privileged``, so a run can be exercised end to end without touching
        anything that needs root.
    """

    #: Verbs that can take a long time and need a longer client-side timeout than the
    #: helper's own internal one.
    _SLOW_VERBS = {"softwareupdate": 3700, "kextcache-rebuild": 960, "periodic": 960, "log-erase": 660}

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self._availability: Optional[bool] = None
        self._reason: Optional[str] = None if enabled else "privileged operations disabled (--no-privileged)"

    # -- availability ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """
        True when the helper is installed and usable without a password.

        Probed once with ``self-check`` and cached; a headless run must not discover
        halfway through that it cannot escalate.
        """

        if not self.enabled:
            return False

        if self._availability is None:
            self._availability, self._reason = self._probe()

        return self._availability

    @property
    def unavailable_reason(self) -> str:
        """Why escalation is not possible. Never None when :attr:`available` is False."""

        if self.available:
            return ""

        return self._reason or "root helper unavailable"

    def _probe(self) -> tuple:
        if not HELPER_PATH.is_file():
            return False, f"{HELPER_PATH} is not installed (run ./install.sh)"

        result = run(["/usr/bin/sudo", "-n", str(HELPER_PATH), "self-check"], timeout=20)

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            hint = detail[-1] if detail else f"exit {result.returncode}"
            return False, f"passwordless sudo to mc-root failed: {hint}"

        return True, None

    # -- askpass -----------------------------------------------------------------------

    @property
    def askpass_env(self) -> Dict[str, str]:
        """
        Environment additions that let *other* tools sudo without prompting.

        Returned empty when the askpass helper is not installed, so callers degrade to
        skipping privileged sub-steps rather than hanging on a prompt.
        """

        if not self.enabled or not ASKPASS_PATH.is_file():
            return {}

        return {"SUDO_ASKPASS": str(ASKPASS_PATH), "SUDO_FLAGS": "-A"}

    @property
    def askpass_available(self) -> bool:
        """True when a stored credential actually resolves — verified, not assumed."""

        if not ASKPASS_PATH.is_file():
            return False

        return run([str(ASKPASS_PATH)], timeout=15).returncode == 0

    # -- verb dispatch -----------------------------------------------------------------

    def _call(self, verb: str, *args: str) -> PrivilegedResult:
        if not self.available:
            return PrivilegedResult(ok=False, error=self._reason or "privileged helper unavailable")

        timeout = self._SLOW_VERBS.get(verb, 300)
        result = run(["/usr/bin/sudo", "-n", str(HELPER_PATH), verb, *args], timeout=timeout)

        if result.returncode != 0:
            return PrivilegedResult(ok=False, error=(result.stderr or result.stdout or "").strip())

        try:
            return PrivilegedResult(ok=True, payload=json.loads(result.stdout or "{}"))
        except json.JSONDecodeError:
            return PrivilegedResult(ok=False, error=f"unparseable helper output: {result.stdout[:200]!r}")

    # -- operations --------------------------------------------------------------------

    def rm_paths(self, paths: Sequence[str]) -> PrivilegedResult:
        """Delete allowlisted paths as root. Paths may contain globs."""

        if not paths:
            return PrivilegedResult(ok=True)

        return self._chunked("rm-paths", paths)

    def stage_paths(self, batch_dir: Path, paths: Sequence[str]) -> PrivilegedResult:
        """Move allowlisted paths into a quarantine batch as root."""

        if not paths:
            return PrivilegedResult(ok=True)

        return self._chunked("stage-paths", paths, prefix=(str(batch_dir),), merge_key="staged")

    def _chunked(
        self, verb: str, paths: Sequence[str], *, prefix: Sequence[str] = (), merge_key: str = "removed", size: int = 512
    ) -> PrivilegedResult:
        """
        Split large path lists across several helper invocations.

        A single ``argv`` has an OS-imposed length ceiling and the helper caps argument
        count on its own; chunking keeps both happy while presenting one merged result.
        """

        merged: Dict = {merge_key: [], "bytes": 0, "denied": [], "errors": []}
        ok = True

        for start in range(0, len(paths), size):
            chunk = paths[start : start + size]
            result = self._call(verb, *prefix, *chunk)

            if not result.ok:
                return result

            merged[merge_key].extend(result.payload.get(merge_key, []))
            merged["bytes"] += result.bytes_freed
            merged["denied"].extend(result.denied)
            merged["errors"].extend(result.errors)

        return PrivilegedResult(ok=ok, payload=merged)

    def unstage(self, staged: Path, original: Path) -> bool:
        """Restore one root-owned path out of quarantine."""

        result = self._call("unstage", str(staged), str(original))
        return result.ok and bool(result.payload.get("restored"))

    def purge_quarantine(self, batch_dir: Path) -> PrivilegedResult:
        """Delete a quarantine batch containing root-owned payload."""

        return self._call("purge-quarantine", str(batch_dir))

    # -- maintenance verbs -------------------------------------------------------------

    def purge_memory(self) -> PrivilegedResult:
        return self._call("purge")

    def periodic(self, *which: str) -> PrivilegedResult:
        return self._call("periodic", *(which or ("daily", "weekly", "monthly")))

    def flush_dns(self) -> PrivilegedResult:
        return self._call("flush-dns")

    def erase_unified_log(self) -> PrivilegedResult:
        return self._call("log-erase")

    def rebuild_launch_services(self) -> PrivilegedResult:
        return self._call("lsregister-rebuild")

    def rebuild_kextcache(self) -> PrivilegedResult:
        return self._call("kextcache-rebuild")

    def reset_font_cache(self) -> PrivilegedResult:
        return self._call("font-cache-reset")

    def software_update(self) -> PrivilegedResult:
        return self._call("softwareupdate")

    def snapshot_create(self) -> PrivilegedResult:
        return self._call("snapshot-create")

    def list_snapshots(self) -> List[str]:
        """Names of Time Machine local snapshots on the boot volume."""

        result = self._call("snapshot-list")
        if not result.ok:
            return []

        return [
            line.strip()
            for line in result.payload.get("output", "").splitlines()
            if line.strip().startswith("com.apple.TimeMachine.")
        ]

    def delete_snapshot(self, name: str) -> PrivilegedResult:
        return self._call("snapshot-delete", name)


def sudo_prefix(privileged: Privileged) -> List[str]:  # pragma: no cover - convenience
    """
    ``sudo`` invocation prefix for tools that must run as root but have no helper verb.

    Prefers ``-A`` (askpass) so nothing can block on a TTY prompt.
    """

    if privileged.askpass_env:
        return ["/usr/bin/sudo", "-A"]

    return ["/usr/bin/sudo", "-n"]
