"""
End-to-end tests for the deletion runtime, against a synthetic home directory.

The point of these is to prove the safety layer holds when it is driven the way the real
program drives it, rather than only unit-testing the policy in isolation. Every test runs
against ``tmp_path``; nothing here touches the real filesystem.
"""

from __future__ import annotations

import os

import pytest

from mc.privileged import Privileged
from mc.quarantine import QuarantineBatch
from mc.report import RunReport
from mc.runtime import Runtime


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """
    A miniature home directory, complete with things that must survive.

    The decoys matter more than the targets: a cleaner that removes caches is easy, one
    that reliably leaves the 11 GB of LLM weights next door alone is the actual problem.

    Every test in this module addresses files through :func:`sandbox`, which builds
    absolute paths under here. Nothing uses a ``~`` string, because ``~`` is resolved at
    runtime and a resolution bug is exactly what let an earlier version of this file
    operate on the real home directory.
    """

    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))

    # Abort before creating anything if the sandbox is not actually isolated. Without
    # this the tests below happily delete whatever the patterns resolve to.
    real_home = os.path.realpath(os.path.expanduser("~"))
    assert os.path.realpath(tmp_path) != real_home, "sandbox resolved to the real home"

    from mc import policy

    resolved = os.path.realpath(policy.expand("~"))
    assert resolved == os.path.realpath(tmp_path), (
        f"policy resolves ~ to {resolved}, not the sandbox {tmp_path}. "
        "Refusing to run destructive tests against an unknown directory."
    )

    # Check the *component under test*, not just the policy. The original incident was
    # precisely a disagreement between the two: mac_cleanup's Path expanded ~ with
    # pathlib (real HOME) while the policy used MACCLEANER_HOME, so a policy-only check
    # like the one above would have passed while Path pointed at the real home.
    from mac_cleanup.core_modules import Path as _UpstreamPath
    from mac_cleanup.core_modules import set_current_module as _set_module

    _set_module("sandbox-check")
    probe = _UpstreamPath("~/probe-sandbox-isolation").get_path.as_posix()
    assert probe.startswith(str(tmp_path)), (
        f"mac_cleanup.Path resolves ~ to {probe}, outside the sandbox {tmp_path}. "
        "This is the exact disagreement that destroyed real files once; refusing to run."
    )

    layout = {
        # Legitimate targets
        "Library/Caches/SomeApp/blob.bin": b"cache" * 200,
        "Library/Caches/Other/data.bin": b"cache" * 100,
        "Library/Logs/app.log": b"log" * 100,
        ".Trash/old-thing": b"trash" * 50,
        # Must survive: hard-protected
        "Downloads/installer.dmg": b"IMPORTANT",
        "Downloads/nested/thing.pkg": b"IMPORTANT",
        ".ssh/id_ed25519": b"PRIVATE KEY",
        "Library/Application Support/Jan/models/llama.gguf": b"WEIGHTS",
        "Library/Application Support/SomeApp/model.safetensors": b"WEIGHTS",
        "Parallels/Win11.pvm/disk.hdd": b"VM",
        # An archive of a VM, not a VM bundle. This decoy exists because the real thing
        # was destroyed: `**/*.pvm` did not match `Windows 11.pvm.zip`.
        "Parallels/Windows 11 (copy-do not run).pvm.zip": b"VM ARCHIVE",
        "code/repo/.git/config": b"GIT",
        # Must survive: soft-protected
        "Documents/report.docx": b"DOC",
    }

    for relative, payload in layout.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    return tmp_path


@pytest.fixture()
def sandbox(fake_home):
    """
    Build an absolute path inside the sandbox.

    Tests call ``sandbox("Library/Caches/*")`` rather than writing ``"~/Library/Caches/*"``.
    The resulting pattern names the sandbox explicitly, so it cannot follow a
    mis-resolved ``~`` out into the real filesystem.
    """

    def build(relative: str) -> str:
        return os.path.join(str(fake_home), relative)

    return build


@pytest.fixture()
def runtime_factory(fake_home, tmp_path):
    """Build a Runtime wired to a quarantine batch under the fake home."""

    def build(*, dry_run: bool = False, quarantine: bool = True):
        report = RunReport()
        batch = (
            QuarantineBatch("2026-01-01T00-00-00", root=tmp_path / "quarantine") if quarantine else None
        )
        runtime = Runtime(
            privileged=Privileged(enabled=False), batch=batch, report=report, dry_run=dry_run
        )
        return runtime, report, batch

    return build


def _path_module(pattern: str, **kwargs):
    """Construct an upstream Path module the way the DSL does."""

    from mac_cleanup.core_modules import Path, set_current_module

    set_current_module("test")
    module = Path(pattern)

    if kwargs.get("privileged"):
        module.privileged()
    if "quarantine" in kwargs:
        module.quarantined(kwargs["quarantine"])
    if kwargs.get("override"):
        module.override_protection(kwargs["override"])

    return module


# --------------------------------------------------------------------------------------
# The protected decoys
# --------------------------------------------------------------------------------------


#: (label, sandbox-relative pattern, file that must survive)
PROTECTED_TARGETS = [
    ("Downloads", "Downloads/*", "Downloads/installer.dmg"),
    ("Downloads recursive", "Downloads/nested/*", "Downloads/nested/thing.pkg"),
    ("ssh key", ".ssh/*", ".ssh/id_ed25519"),
    ("Jan model weights", "Library/Application Support/Jan/models/*",
     "Library/Application Support/Jan/models/llama.gguf"),
    ("safetensors anywhere", "Library/Application Support/SomeApp/*",
     "Library/Application Support/SomeApp/model.safetensors"),
    ("Parallels VM", "Parallels/*", "Parallels/Win11.pvm/disk.hdd"),
    ("Parallels VM archive", "Parallels/*", "Parallels/Windows 11 (copy-do not run).pvm.zip"),
    ("git repository", "code/repo/.git", "code/repo/.git/config"),
]


@pytest.mark.parametrize(
    "label,pattern,survivor", PROTECTED_TARGETS, ids=[t[0] for t in PROTECTED_TARGETS]
)
def test_protected_paths_survive_a_real_run(runtime_factory, sandbox, fake_home, label, pattern, survivor):
    """Point the runtime straight at protected data and confirm it refuses."""

    runtime, report, batch = runtime_factory()

    runtime.delete_path(_path_module(sandbox(pattern)))

    if batch:
        batch.close()

    assert (fake_home / survivor).exists(), f"{label} was deleted"
    assert report.module("test").paths_denied, "the refusal should be recorded, not silent"


def test_downloads_survives_even_with_override_and_privilege(runtime_factory, sandbox, fake_home):
    """The combination that would defeat soft protection must still fail on Downloads."""

    runtime, report, batch = runtime_factory()

    runtime.delete_path(_path_module(sandbox("Downloads/*"), privileged=True, override="I really mean it"))

    if batch:
        batch.close()

    assert (fake_home / "Downloads/installer.dmg").read_bytes() == b"IMPORTANT"


def test_soft_protected_needs_the_override(runtime_factory, sandbox, fake_home):
    runtime, _, batch = runtime_factory()
    runtime.delete_path(_path_module(sandbox("Documents/*")))
    if batch:
        batch.close()

    assert (fake_home / "Documents/report.docx").exists()


# --------------------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------------------


def test_caches_are_staged_to_quarantine(runtime_factory, sandbox, fake_home):
    runtime, report, batch = runtime_factory()

    runtime.delete_path(_path_module(sandbox("Library/Caches/*")))
    batch.close()

    assert not (fake_home / "Library/Caches/SomeApp").exists()
    assert report.module("test").bytes_staged > 0
    assert report.module("test").bytes_reclaimed == 0, "staging is not reclaiming"
    assert len(batch) == 2


def test_no_quarantine_deletes_outright(runtime_factory, sandbox, fake_home):
    runtime, report, _ = runtime_factory(quarantine=False)

    runtime.delete_path(_path_module(sandbox("Library/Caches/*")))

    assert not (fake_home / "Library/Caches/SomeApp").exists()
    assert report.module("test").bytes_reclaimed > 0
    assert report.module("test").bytes_staged == 0


def test_staged_data_can_be_restored(runtime_factory, sandbox, fake_home, tmp_path):
    from mc import quarantine as quarantine_module

    runtime, _, batch = runtime_factory()
    original = (fake_home / "Library/Caches/SomeApp/blob.bin").read_bytes()

    runtime.delete_path(_path_module(sandbox("Library/Caches/*")))
    batch.close()

    restored, skipped, errors = quarantine_module.restore(
        "2026-01-01T00-00-00", root=tmp_path / "quarantine"
    )

    assert errors == []
    assert restored == 2
    assert (fake_home / "Library/Caches/SomeApp/blob.bin").read_bytes() == original


def test_dry_run_touches_nothing(runtime_factory, sandbox, fake_home):
    runtime, report, batch = runtime_factory(dry_run=True)

    module = _path_module(sandbox("Library/Caches/*"))
    runtime.delete_path(module)
    estimated = runtime.estimate(module)

    if batch:
        batch.close()

    assert (fake_home / "Library/Caches/SomeApp/blob.bin").exists()
    assert estimated > 0
    assert report.module("test").bytes_reclaimed == 0
    assert report.module("test").bytes_staged == 0


# --------------------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------------------


def test_estimate_excludes_paths_policy_would_refuse(runtime_factory, sandbox):
    """
    The dry-run number has to match what a real run would do.

    Upstream's estimator counts every path it is handed, including ones its own safety
    check refuses at execution time, which overstates the result.
    """

    runtime, _, _ = runtime_factory(dry_run=True)

    assert runtime.estimate(_path_module(sandbox("Downloads/*"))) == 0
    assert runtime.estimate(_path_module(sandbox(".ssh/*"))) == 0
    assert runtime.estimate(_path_module(sandbox("Library/Caches/*"))) > 0


def test_estimate_deduplicates_overlapping_modules(runtime_factory, sandbox):
    """
    Overlapping rules are normal — the generic Electron sweep and the app-specific rules
    both reach the same directories. Counting them twice inflates the headline figure.
    """

    runtime, _, _ = runtime_factory(dry_run=True)
    seen: set = set()

    first = runtime.estimate(_path_module(sandbox("Library/Caches/*")), seen)
    second = runtime.estimate(_path_module(sandbox("Library/Caches/SomeApp")), seen)

    assert first > 0
    assert second == 0, "the nested path was already counted by the glob above it"


# --------------------------------------------------------------------------------------
# Privilege
# --------------------------------------------------------------------------------------


def test_privileged_paths_are_skipped_without_the_helper(runtime_factory):
    runtime, report, batch = runtime_factory()

    runtime.delete_path(_path_module("/Library/Caches/*", privileged=True))
    if batch:
        batch.close()

    errors = report.module("test").errors
    assert errors and "needs root" in errors[0]


def test_symlinked_parent_is_refused(runtime_factory, sandbox, fake_home):
    """A cache directory containing a link into protected territory must not be followed."""

    cache = fake_home / "Library/Caches/Sneaky"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "escape").symlink_to(fake_home / "Downloads")

    runtime, report, batch = runtime_factory()
    runtime.delete_path(_path_module(sandbox("Library/Caches/Sneaky/escape/*")))
    if batch:
        batch.close()

    assert (fake_home / "Downloads/installer.dmg").exists()
