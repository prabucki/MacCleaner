"""
The update phase.

``topgrade`` does most of the work — it already knows how to drive Homebrew, npm, pnpm,
yarn, cargo, rustup, gem, pipx, uv, go, VS Code, Cursor, Antigravity, Android Studio,
Sparkle-based apps, tldr and oh-my-zsh. Rather than reimplement any of that, MacCleaner
points topgrade at its own tuned config (``config/topgrade.toml``) and then fills the
handful of gaps topgrade leaves.

Everything here is best-effort: a failed update never aborts the cleanup that follows.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from mac_cleanup.console import console

from mc.privileged import Privileged
from mc.report import RunReport
from mc.util import run, which

__all__ = ["run_updates", "TOPGRADE_CONFIG"]

#: Shipped config, resolved relative to the repo so it works from a checkout or an install.
TOPGRADE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "topgrade.toml"


def run_updates(
    *,
    report: RunReport,
    privileged: Privileged,
    dry_run: bool = False,
    verbose: bool = False,
    os_updates: bool = False,
    timeout: int = 3600,
) -> None:
    """
    Update everything updatable.

    :param os_updates: Install pending macOS system updates. Off by default because
        ``softwareupdate -ia`` can trigger a reboot without warning, which an unattended
        weekly run has no business doing.
    """

    result = report.module("update", "safe")

    if dry_run:
        result.status = "skipped"
        result.reason = "dry run"
        return

    console.print("[info]Update phase[/info]")

    _topgrade(report, privileged, verbose=verbose, timeout=timeout)
    _homebrew_extras(report, privileged)
    _oh_my_zsh(report)
    _uv_and_pipx(report)
    _nvm(report)

    if os_updates:
        _os_updates(report, privileged)

    result.status = "ok"


# --------------------------------------------------------------------------------------
# topgrade
# --------------------------------------------------------------------------------------


def _topgrade(report: RunReport, privileged: Privileged, *, verbose: bool, timeout: int) -> None:
    result = report.module("update", "safe")
    binary = which("topgrade")

    if binary is None:
        report.warn("topgrade is not installed; most updates will be skipped. `brew install topgrade`")
        return

    argv: List[str] = [binary, "--yes", "--no-ask-retry", "--no-self-update", "--notify-end", "never"]

    if TOPGRADE_CONFIG.is_file():
        argv += ["--config", str(TOPGRADE_CONFIG)]
    else:
        report.warn(f"shipped topgrade config missing at {TOPGRADE_CONFIG}; using your own")

    if verbose:
        argv.append("--show-skipped")

    console.print("  running topgrade...")

    # SUDO_ASKPASS lets the cask/pkg steps escalate without a TTY prompt. Without it
    # those steps fail fast rather than hanging, which is the behaviour we want.
    completed = run(argv, timeout=timeout, env=privileged.askpass_env)

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        result.errors.append(f"topgrade exited {completed.returncode}" + (f": {tail[-1][:200]}" if tail else ""))
        console.print(f"  [warning]topgrade exited {completed.returncode}[/warning] (continuing)")
    else:
        console.print("  [success]topgrade finished[/success]")


# --------------------------------------------------------------------------------------
# Gap fillers
# --------------------------------------------------------------------------------------


def _homebrew_extras(report: RunReport, privileged: Privileged) -> None:
    """
    Homebrew housekeeping topgrade does not do.

    ``brew cleanup -s`` plus removing the download cache is what the original shell
    script did; ``autoremove`` and ``tap --repair`` are worth having alongside.
    """

    brew = which("brew")
    if brew is None:
        return

    result = report.module("update", "safe")

    for argv, description, step_timeout in (
        ([brew, "autoremove"], "brew autoremove", 600),
        ([brew, "tap", "--repair"], "brew tap --repair", 300),
        ([brew, "cleanup", "-s", "--prune=all"], "brew cleanup", 900),
    ):
        completed = run(argv, timeout=step_timeout, env=privileged.askpass_env)
        if completed.returncode != 0:
            # Homebrew exits non-zero for entirely ordinary reasons (nothing to remove,
            # a tap that is not a git repo). Record it, do not treat it as a failure.
            tail = (completed.stderr or "").strip().splitlines()
            if tail:
                result.errors.append(f"{description}: {tail[-1][:200]}")


def _oh_my_zsh(report: RunReport) -> None:
    """
    Upgrade oh-my-zsh.

    topgrade's ``shell`` step covers this, but only when ``$ZSH`` is exported into the
    environment — which it is not under launchd. Running the upgrade script directly is
    more reliable than hoping the environment is right.
    """

    upgrade_script = Path.home() / ".oh-my-zsh" / "tools" / "upgrade.sh"

    if not upgrade_script.is_file():
        return

    completed = run(
        ["/bin/sh", str(upgrade_script)],
        timeout=300,
        env={"ZSH": str(Path.home() / ".oh-my-zsh"), "ZSH_CACHE_DIR": str(Path.home() / ".oh-my-zsh" / "cache")},
    )

    if completed.returncode not in (0, 80):  # 80 = "already up to date" in some versions
        report.module("update", "safe").errors.append(f"oh-my-zsh upgrade exited {completed.returncode}")


def _uv_and_pipx(report: RunReport) -> None:
    """Upgrade uv-managed and pipx-managed tools. topgrade covers both, but not reliably
    when they were installed under a different Python than the one on PATH."""

    result = report.module("update", "safe")

    uv = which("uv")
    if uv is not None:
        completed = run([uv, "tool", "upgrade", "--all"], timeout=900)
        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()
            if tail:
                result.errors.append(f"uv tool upgrade: {tail[-1][:200]}")

    pipx = which("pipx")
    if pipx is not None:
        completed = run([pipx, "upgrade-all"], timeout=900)
        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()
            if tail:
                result.errors.append(f"pipx upgrade-all: {tail[-1][:200]}")


def _nvm(report: RunReport) -> None:
    """
    Upgrade Node to the latest release through nvm, carrying global packages over.

    Replaces ``source/update_nvm.sh`` from the old mac-scripts repo. topgrade's ``node``
    step upgrades npm itself but never installs a new Node runtime, so without this the
    Node version drifts indefinitely.

    nvm is a shell function, not a binary, so it has to be driven through an interactive
    bash that sources ``nvm.sh`` first.
    """

    result = report.module("update", "safe")

    nvm_script = None
    for candidate in (
        Path.home() / ".nvm/nvm.sh",
        Path("/opt/homebrew/opt/nvm/nvm.sh"),
        Path("/usr/local/opt/nvm/nvm.sh"),
    ):
        if candidate.is_file():
            nvm_script = candidate
            break

    if nvm_script is None:
        return

    # Mirrors update_nvm.sh: install latest, migrate global packages off the old
    # version, drop it, clear the cache, repoint the default alias, ensure corepack.
    script = f"""
        set -e
        export NVM_DIR="${{NVM_DIR:-$HOME/.nvm}}"
        . "{nvm_script}"

        current=$(nvm current)
        latest=$(nvm version-remote --lts)

        if [ "$current" = "$latest" ]; then
            echo "node already at $current"
            exit 0
        fi

        echo "updating node $current -> $latest"
        nvm install "$latest"

        if [ "$current" != "system" ] && [ "$current" != "none" ]; then
            nvm reinstall-packages "$current" || true
            nvm uninstall "$current" || true
        fi

        nvm cache clear
        nvm alias default "$latest"
        nvm use default

        command -v corepack >/dev/null 2>&1 || npm install -g corepack
    """

    completed = run(["/bin/bash", "-lc", script], timeout=1800)

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        result.errors.append("nvm update: " + (tail[-1][:200] if tail else f"exit {completed.returncode}"))
    else:
        summary = (completed.stdout or "").strip().splitlines()
        if summary:
            console.print(f"  {summary[-1]}")


def _os_updates(report: RunReport, privileged: Privileged) -> None:
    """
    Install pending macOS updates through the root helper.

    Opt-in only. This can reboot the machine.
    """

    result = report.module("update", "safe")

    if not privileged.available:
        result.errors.append(f"macOS updates need root: {privileged.unavailable_reason}")
        return

    console.print("  [warning]installing macOS system updates (this may reboot)[/warning]")

    response = privileged.software_update()
    if not response.ok:
        result.errors.append(f"softwareupdate: {response.error}")
