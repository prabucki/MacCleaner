"""Small shared helpers used across the ``mc`` package."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

__all__ = [
    "MC_HOME",
    "terminate_group",
    "iso_stamp",
    "human",
    "path_size",
    "run",
    "which",
    "app_is_running",
    "quit_app",
    "expand_globs",
    "same_volume",
]


def _mc_home() -> Path:
    """Root of all MacCleaner state (logs, quarantine, run metadata)."""

    override = os.environ.get("MACCLEANER_STATE")
    if override:
        return Path(override).expanduser()

    return Path(os.environ.get("MACCLEANER_HOME", Path.home())).expanduser() / ".maccleaner"


MC_HOME = _mc_home()


def iso_stamp() -> str:
    """Filesystem-safe UTC timestamp used to name runs and quarantine batches."""

    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def human(size_bytes: float) -> str:
    """Render a byte count for humans. Mirrors upstream ``bytes_to_human`` formatting."""

    if size_bytes <= 0:
        return "0 B"

    units = ("B", "KB", "MB", "GB", "TB", "PB")
    index = 0
    value = float(size_bytes)

    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1

    return f"{value:.2f} {units[index]}"


def path_size(target: Path) -> int:
    """
    Total size of a file or directory tree, in bytes.

    Symlinks are measured but never followed, so a link into a protected tree cannot
    inflate the figure or trigger a traversal outside the target.
    """

    try:
        stat = target.lstat()
    except (OSError, ValueError):
        return 0

    if not target.is_dir() or target.is_symlink():
        return stat.st_size

    total = 0
    for root, dirs, files in os.walk(target, followlinks=False):
        # Do not descend into mount points; a stray external volume would be counted whole.
        dirs[:] = [d for d in dirs if not os.path.ismount(os.path.join(root, d))]
        for name in files + dirs:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue

    return total


def terminate_group(process: subprocess.Popen, *, grace: int = 5) -> None:
    """
    Stop a child and everything it spawned, politely first.

    Children run in their own process group (see :func:`run`), so a plain ``kill`` would
    leave grandchildren orphaned. SIGTERM gives the tool a chance to clean up — important
    when it is a package manager mid-operation — before SIGKILL.
    """

    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):
        return

    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, signal_number)
        except (ProcessLookupError, PermissionError):
            return

        try:
            process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run(
    command: Sequence[str] | str,
    *,
    timeout: int = 300,
    check: bool = False,
    env: Optional[dict] = None,
    cwd: Optional[Path] = None,
    stream: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a command with a hard timeout, killing the whole process group on expiry.

    Upstream's ``mac_cleanup.utils.cmd`` has no timeout at all; an unattended run that
    hits a hung ``brew`` or a network stall would block forever. Everything in ``mc``
    goes through here instead.

    :param command: Argument list, or a string to be run through the shell.
    :param timeout: Seconds before the process group is killed.
    :param check: Raise :class:`subprocess.CalledProcessError` on non-zero exit.
    :param stream: Let the child write straight to this process's stdout/stderr instead
        of capturing. Use for long interactive steps so the user can see progress; the
        returned stdout is empty.
    :return: The completed process, with stdout/stderr captured as text (unless ``stream``).
    """

    shell = isinstance(command, str)

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)

    process = subprocess.Popen(
        command,
        shell=shell,
        stdout=None if stream else subprocess.PIPE,
        stderr=None if stream else subprocess.PIPE,
        text=True,
        errors="replace",
        env=merged_env,
        cwd=str(cwd) if cwd else None,
        start_new_session=True,  # own process group, so the kill below reaches children
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_group(process)
        stdout, stderr = process.communicate()
        stderr = (stderr or "") + f"\n[mc] timed out after {timeout}s and was killed"
    except KeyboardInterrupt:
        # start_new_session puts the child in its own process group, so the terminal's
        # Ctrl-C never reaches it. Without this the interrupt kills mc and leaves the
        # child running detached, still writing to the terminal — which reads as "Ctrl-C
        # does nothing". Forward it explicitly, then let the interrupt propagate.
        terminate_group(process)
        try:
            process.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):  # pragma: no cover - already dead
            pass
        raise

    completed = subprocess.CompletedProcess(
        args=command, returncode=process.returncode, stdout=stdout or "", stderr=stderr or ""
    )

    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout, completed.stderr)

    return completed


def which(binary: str) -> Optional[str]:
    """Locate an executable, also searching the Homebrew prefixes that launchd contexts miss."""

    found = shutil.which(binary)
    if found:
        return found

    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".cargo/bin"), str(Path.home() / ".local/bin")):
        candidate = Path(prefix) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def app_is_running(process_name: str) -> bool:
    """
    True when a process matching ``process_name`` is alive.

    Used by the running-app guard: cleaning a live app's cache directory out from under
    it is the fastest way to corrupt its state.
    """

    result = run(["/usr/bin/pgrep", "-x", process_name], timeout=10)
    return result.returncode == 0 and bool(result.stdout.strip())


def quit_app(process_name: str, *, force_after: int = 10) -> bool:
    """
    Ask an app to quit, escalating to SIGKILL if it ignores the request.

    :param process_name: Exact process name as reported by ``pgrep -x``.
    :param force_after: Seconds to wait for a graceful quit before SIGKILL.
    :return: True if the process is gone afterwards.
    """

    if not app_is_running(process_name):
        return True

    run(["/usr/bin/osascript", "-e", f'tell application "{process_name}" to quit'], timeout=15)

    deadline = force_after
    while deadline > 0 and app_is_running(process_name):
        run(["/bin/sleep", "1"], timeout=5)
        deadline -= 1

    if app_is_running(process_name):
        run(["/usr/bin/pkill", "-9", "-x", process_name], timeout=10)

    return not app_is_running(process_name)


def expand_globs(pattern: str) -> Iterable[Path]:
    """
    Expand a glob pattern to existing paths, without following symlinked directories.

    ``Path.glob`` needs a base; this accepts absolute patterns directly.
    """

    from glob import glob

    for match in glob(os.path.expanduser(pattern)):
        yield Path(match)


def same_volume(first: Path, second: Path) -> bool:
    """
    True when both paths live on the same filesystem.

    Quarantine relies on this: a same-volume ``mv`` is an instant metadata operation,
    while a cross-volume move is a full copy and is not worth doing for cache data.
    """

    def device(path: Path) -> Optional[int]:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            return probe.stat().st_dev
        except OSError:
            return None

    first_device, second_device = device(first), device(second)

    return first_device is not None and first_device == second_device
