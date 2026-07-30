"""
Policy tests.

This is the file that matters most. Every other component defers to ``mc.policy`` for
"may I delete this", and the failure mode of a wrong answer is destroyed user data.
"""

from __future__ import annotations

import os

import pytest

from mc import policy


@pytest.fixture(autouse=True)
def fixed_home(monkeypatch):
    """Pin the policy's idea of home so tests do not depend on who runs them."""

    monkeypatch.setenv("MACCLEANER_HOME", "/Users/testuser")
    yield


# --------------------------------------------------------------------------------------
# Downloads — hard-protected by explicit instruction
# --------------------------------------------------------------------------------------


DOWNLOADS_PATHS = [
    "~/Downloads",
    "~/Downloads/",
    "~/Downloads/installer.dmg",
    "~/Downloads/*",
    "~/Downloads/**",
    "~/Downloads/nested/deep/file.txt",
    "~/Downloads/.DS_Store",
    "/Users/testuser/Downloads",
    "/Users/testuser/Downloads/thing.pkg",
    "~/Library/../Downloads/x",  # normalises back into Downloads
    "~/Downloads/../Downloads/x",
]


@pytest.mark.parametrize("path", DOWNLOADS_PATHS)
@pytest.mark.parametrize("privileged", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_downloads_is_never_deletable(path, privileged, override):
    """
    ~/Downloads must be unreachable under every combination of flags.

    Hard protection rather than soft: there is deliberately no override that reaches it,
    and mc-root enforces the same rule independently.
    """

    decision = policy.check(path, privileged=privileged, override=override)

    assert not decision.allowed, f"{path} was allowed (privileged={privileged}, override={override})"
    assert "Downloads" in (decision.reason or "")


def test_downloads_rejected_by_privileged_allowlist():
    """The root helper's own check must refuse Downloads too, not just the front end."""

    assert policy.is_privileged_allowed("~/Downloads/anything") is not None


def test_real_home_is_protected_even_when_policy_home_is_wrong(monkeypatch, tmp_path):
    """
    Regression: a wrong MACCLEANER_HOME must not expose the real user's data.

    This is defence in depth against a bug that actually happened during development.
    ``mac_cleanup.core_modules.Path`` expanded ``~`` with pathlib (real ``$HOME``) while
    the policy expanded it against its own configured home. With the two disagreeing,
    ``~/Downloads/*`` became a real path that the policy — looking at a different home —
    did not recognise as protected, and the real Downloads directory was staged.

    Two things now prevent it: Path routes through :func:`policy.expand`, and hard
    protection is evaluated against every plausible home, which is what this asserts.
    """

    import os

    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))
    real_home = os.path.normpath(os.path.expanduser("~"))

    for relative in ("Downloads", "Downloads/file.pdf", ".ssh/id_rsa", "Library/Keychains/login.keychain-db"):
        candidate = os.path.join(real_home, relative)
        decision = policy.check(candidate, privileged=True, override=True)
        assert not decision.allowed, f"{candidate} was reachable under a mismatched home"


def test_path_module_and_policy_agree_on_home(monkeypatch, tmp_path):
    """The stored path and the checked path must be the same string."""

    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))

    from mac_cleanup.core_modules import Path as PathModule
    from mac_cleanup.core_modules import set_current_module

    set_current_module("test")
    module = PathModule("~/Library/Caches/thing")

    assert module.get_path.as_posix() == policy.expand("~/Library/Caches/thing")
    assert str(tmp_path) in module.get_path.as_posix()


# --------------------------------------------------------------------------------------
# Structural directories
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "/Users", "/Library", "/System", "/usr", "/etc", "/var", "/private/var", "/Applications", "/Volumes",
     "~", "~/Library", "~/Library/Caches", "~/Library/Application Support", "~/Library/Containers"],
)
def test_structural_directories_are_never_targets(path):
    """A bug producing an empty glob suffix must not turn a rule into 'delete /'."""

    assert not policy.check(path, privileged=True, override=True).allowed


# --------------------------------------------------------------------------------------
# Irreplaceable data
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "~/.ssh/id_ed25519",
        "~/.gnupg/secring.gpg",
        "~/.aws/credentials",
        "~/Library/Keychains/login.keychain-db",
        "~/Library/Mobile Documents/com~apple~CloudDocs/notes.txt",
        "~/Library/CloudStorage/Dropbox/work.pdf",
        "~/Library/Messages/chat.db",
        # Local LLM weights — Jan.app is ~11 GB of these on the target machine
        "~/Library/Application Support/Jan/models/llama.gguf",
        "~/Library/Application Support/SomeApp/model.safetensors",
        "~/anywhere/at/all/weights.gguf",
        "~/.ollama/models/blobs/sha256-abc",
        # VM and media libraries
        "~/Parallels/Windows 11.pvm",
        "~/Pictures/Photos Library.photoslibrary",
        "~/Music/Music Library.musiclibrary",
        "/Volumes/Backup/TimeMachine.sparsebundle",
        # Repositories
        "~/code/project/.git",
        "~/code/project/.git/objects/ab/cdef",
        # Sync and backup tooling
        "~/Library/Application Support/Syncthing/config.xml",
        "~/Library/Application Support/Carbon Copy Cloner/state.db",
    ],
)
def test_irreplaceable_data_is_hard_protected(path):
    assert not policy.check(path, privileged=True, override=True).allowed


# --------------------------------------------------------------------------------------
# Soft protection
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Regression: this exact file (11.75 GB) was destroyed during development.
        # `**/*.pvm` protected VM *bundles*, but an archive of one is a plain file
        # whose name merely ends in `.zip`, and nothing matched it.
        "~/Parallels/Windows 11 (copy-do not run).pvm.zip",
        "~/Backups/win.pvm.tar.gz",
        "~/anywhere/at/all/thing.pvm.7z",
        "~/Documents/exported.utm.zip",
        "~/Virtual Machines.localized/Win11.vmwarevm.zip",
        # The VM storage directories themselves, not just the bundle extensions.
        "~/Parallels",
        "~/Parallels/notes.txt",
        "~/Parallels/snapshots/whatever",
        "~/Virtual Machines.localized/anything",
    ],
)
@pytest.mark.parametrize("privileged", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_virtual_machines_and_their_archives_are_protected(path, privileged, override):
    """VM bundles, VM archives, and the folders they live in are all hard-protected."""

    decision = policy.check(path, privileged=privileged, override=override)

    assert not decision.allowed, f"{path} was deletable (privileged={privileged}, override={override})"


@pytest.mark.parametrize("path", ["~/Documents/report.docx", "~/Desktop/notes.txt", "~/Pictures/holiday.jpg"])
def test_soft_protected_needs_override(path):
    assert not policy.check(path).allowed
    assert policy.check(path, override=True).allowed


# --------------------------------------------------------------------------------------
# Normal cleanup targets
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "~/Library/Caches/*",
        "~/Library/Caches/Firefox/Profiles/abc/cache2/entries",
        "~/.cache/pip/http",
        "~/Library/Logs/DiagnosticReports/foo.crash",
        "~/Library/Developer/Xcode/DerivedData/App-abc",
        "~/Library/Application Support/Ferdium/Partitions/service-1/Cache",
        "~/.Trash/old",
    ],
)
def test_ordinary_cache_paths_are_allowed(path):
    decision = policy.check(path)
    assert decision.allowed, decision.reason


# --------------------------------------------------------------------------------------
# Privileged allowlist
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/Library/Caches/x", "/private/var/log/system.log", "/var/log/system.log",
     "/private/var/folders/ab/cd/C/thing", "/Users/Shared/Adobe/installer", "/private/tmp/old-thing"],
)
def test_privileged_allowlist_accepts_expected(path):
    assert policy.is_privileged_allowed(path) is None, policy.is_privileged_allowed(path)


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/usr/bin/python3", "/System/Library/CoreServices/Finder.app",
     "/opt/homebrew/bin/brew", "/Library/LaunchDaemons/com.thing.plist", "/Applications/Safari.app",
     "~/Downloads/x", "/Users/otheruser/Documents/x"],
)
def test_privileged_allowlist_refuses_everything_else(path):
    assert policy.is_privileged_allowed(path) is not None


def test_var_and_private_var_are_the_same_place():
    """
    /var is a symlink to /private/var. Without canonicalisation the two spell the same
    location differently and only one of them matches the allowlist.
    """

    assert policy.normalize("/var/log/x") == policy.normalize("/private/var/log/x")
    assert policy.normalize("/tmp/x") == "/private/tmp/x"
    assert policy.normalize("/etc/hosts") == "/private/etc/hosts"


# --------------------------------------------------------------------------------------
# Traversal
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "~/Library/Caches/../../.ssh/id_rsa",
        "~/Library/Caches/../../../etc/passwd",
        "/Library/Caches/../../etc/passwd",
        "/Library/Caches/x/../../../../Users/testuser/Downloads",
    ],
)
def test_dot_dot_traversal_is_resolved_before_checking(path):
    """
    ``..`` must be collapsed before matching, or a protected location can be reached
    through an allowed prefix.
    """

    assert not policy.check(path, privileged=True, override=True).allowed


def test_symlinked_parent_directory_is_caught(tmp_path):
    """
    A symlinked *directory component* relocates the deletion and must be refused.

    This is the dangerous shape: `<cache>/sub/victim` where `sub` is a link into a
    protected tree means deleting "victim" actually deletes something in that tree.
    """

    cache = tmp_path / "cache"
    cache.mkdir()

    (cache / "sub").symlink_to("/private/etc")

    assert policy.resolve_escapes(str(cache / "sub" / "passwd")) is not None


def test_plain_symlink_target_is_not_flagged(tmp_path):
    """
    A path that is *itself* a symlink is fine to remove whatever it points at.

    Deletion unlinks the link rather than following it, so refusing these would only
    produce false negatives on the many cache directories that are symlinks.
    """

    link = tmp_path / "link-to-etc"
    link.symlink_to("/private/etc")

    assert policy.resolve_escapes(str(link)) is None


def test_ordinary_path_has_no_escape(tmp_path):
    real = tmp_path / "real"
    real.mkdir()

    assert policy.resolve_escapes(str(real)) is None


# --------------------------------------------------------------------------------------
# Volumes
# --------------------------------------------------------------------------------------


def test_external_volumes_are_protected_except_their_trash():
    assert not policy.check("/Volumes/SD/photos", privileged=True, override=True).allowed
    assert not policy.check("/Volumes/Backups of TomMBP/data", privileged=True, override=True).allowed
    assert policy.is_privileged_allowed("/Volumes/SD/.Trashes/501") is None


# --------------------------------------------------------------------------------------
# Risk tiers
# --------------------------------------------------------------------------------------


def test_risk_tiers_are_cumulative():
    assert policy.risk_at_or_below("safe") == ("safe",)
    assert policy.risk_at_or_below("standard") == ("safe", "standard")
    assert policy.risk_at_or_below("nuclear") == ("safe", "standard", "aggressive", "nuclear")


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        policy.risk_at_or_below("extremely-aggressive")


# --------------------------------------------------------------------------------------
# Python 3.9 compatibility
# --------------------------------------------------------------------------------------


def test_policy_module_is_importable_by_system_python():
    """
    mc-root runs under /usr/bin/python3 (3.9 on macOS 26) because it is the only
    interpreter on the system an unprivileged user cannot replace. If policy.py ever
    grows a 3.10+ construct or a third-party import, root escalation breaks entirely —
    so this compiles it with the real system interpreter.
    """

    import subprocess
    from pathlib import Path

    if not Path("/usr/bin/python3").exists():  # pragma: no cover
        pytest.skip("/usr/bin/python3 not present")

    source = Path(__file__).resolve().parent.parent / "mc" / "policy.py"
    result = subprocess.run(
        ["/usr/bin/python3", "-c", f"import py_compile,sys; py_compile.compile({str(source)!r}, doraise=True)"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
