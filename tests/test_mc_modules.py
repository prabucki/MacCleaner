"""
Module registry tests.

These check the shape of every registered module rather than its specific paths: that it
declares work without raising, that its risk tier is real, and — most importantly — that
no module asks for anything the policy would refuse. A module whose paths are all denied
is a module that silently does nothing, which is the failure mode hardest to notice.
"""

from __future__ import annotations

import pytest

from mac_cleanup.core import ProxyCollector, _Collector
from mc import policy
from mc.policy import Risk
from mc.privileged import Privileged
from mc.registry import REGISTRY, collect, select
from mc.report import RunReport


@pytest.fixture(autouse=True)
def loaded_modules():
    """Import every module file so the registry is populated."""

    from mc.cli import _load_modules

    _load_modules()
    yield


@pytest.fixture()
def clean_collector():
    """
    A collector with an empty execute list.

    ``_Collector`` is a Borg — every instance shares state — so it has to be reset
    between tests or declarations accumulate across them.
    """

    collector = _Collector()
    collector._execute_list.clear()  # noqa: SLF001
    yield collector
    collector._execute_list.clear()  # noqa: SLF001


def test_registry_is_populated():
    assert len(REGISTRY) > 50, "expected the full module set to register"


def test_every_module_has_a_valid_risk_tier():
    for name, module in REGISTRY.items():
        assert module.risk in policy.RISK_ORDER, f"{name} has risk {module.risk!r}"


def test_module_names_are_unique_and_sane():
    for name in REGISTRY:
        assert name.islower(), f"{name} should be lowercase"
        assert " " not in name, f"{name} should not contain spaces"


def test_all_modules_declare_without_raising(clean_collector):
    """
    Declaration must never throw.

    ``collect`` catches exceptions so one bad module cannot abort an unattended run, but
    a module that always fails is still a bug — this surfaces it.
    """

    report = RunReport()
    collect(
        list(REGISTRY.values()),
        collector=ProxyCollector(),
        report=report,
        privileged=Privileged(enabled=False),
        profile=Risk.NUCLEAR,
    )

    failures = {m.name: m.reason for m in report.failed}
    assert not failures, f"modules raised during declaration: {failures}"


def test_no_module_requests_a_protected_path(clean_collector):
    """
    Every declared path must survive the policy.

    A rule that the policy always refuses is dead code that looks like coverage. It also
    catches the reverse mistake: a module reaching somewhere it should not.
    """

    from mac_cleanup.core_modules import Path as PathModule

    report = RunReport()
    collect(
        list(REGISTRY.values()),
        collector=ProxyCollector(),
        report=report,
        privileged=Privileged(enabled=False),
        profile=Risk.NUCLEAR,
    )

    refused = []

    for unit in clean_collector._execute_list:  # noqa: SLF001
        for module in unit.modules:
            # Measurement-only paths never delete, so the deletion policy does not
            # apply to them — a module is allowed to report the size of something it
            # must not touch.
            if not isinstance(module, PathModule) or module.is_dry_run_only:
                continue

            decision = policy.check(
                module.get_path.as_posix(),
                privileged=module.is_privileged,
                override=module.has_override,
            )

            if not decision.allowed:
                refused.append((getattr(module, "owner", "?"), module.get_path.as_posix(), decision.reason))

    assert not refused, "modules declared paths the policy refuses:\n" + "\n".join(
        f"  {owner}: {path}\n    {reason}" for owner, path, reason in refused[:20]
    )


def test_no_module_targets_downloads(clean_collector):
    """An explicit, targeted assertion rather than relying on the general check above."""

    from mac_cleanup.core_modules import Path as PathModule

    report = RunReport()
    collect(
        list(REGISTRY.values()),
        collector=ProxyCollector(),
        report=report,
        privileged=Privileged(enabled=False),
        profile=Risk.NUCLEAR,
    )

    offenders = [
        (getattr(m, "owner", "?"), m.get_path.as_posix())
        for unit in clean_collector._execute_list  # noqa: SLF001
        for m in unit.modules
        if isinstance(m, PathModule) and "/Downloads" in m.get_path.as_posix()
    ]

    assert not offenders, f"modules referencing Downloads: {offenders}"


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------


def test_profile_filters_by_risk_tier():
    safe = select(profile=Risk.SAFE)
    nuclear = select(profile=Risk.NUCLEAR)

    assert len(safe) < len(nuclear)
    assert all(m.risk == Risk.SAFE for m in safe)
    assert any(m.risk == Risk.NUCLEAR for m in nuclear)


def test_nuclear_modules_are_excluded_from_the_default_profile():
    """
    The default profile is aggressive. Anything nuclear must require asking for it.
    """

    selected = {m.name for m in select(profile=Risk.AGGRESSIVE)}
    nuclear = {name for name, m in REGISTRY.items() if m.risk == Risk.NUCLEAR}

    assert nuclear, "expected some modules to be classified nuclear"
    assert not (selected & nuclear)


def test_only_bypasses_the_tier_filter():
    """--only should run a single nuclear module without changing profile."""

    selected = select(profile=Risk.SAFE, only=["kext_cache"])

    assert [m.name for m in selected] == ["kext_cache"]


def test_skip_accepts_names_and_tags():
    by_name = {m.name for m in select(profile=Risk.NUCLEAR, skip=["trash"])}
    assert "trash" not in by_name

    by_tag = {m.name for m in select(profile=Risk.NUCLEAR, skip=["browser"])}
    assert "firefox" not in by_tag
    assert "safari" not in by_tag


def test_tags_restrict_selection():
    selected = select(profile=Risk.NUCLEAR, tags=["browser"])

    assert selected
    assert all("browser" in m.tags for m in selected)


# --------------------------------------------------------------------------------------
# Coverage of the scripts this tool replaces
# --------------------------------------------------------------------------------------


def test_upstream_modules_are_triaged():
    """
    Every upstream module must be either adopted or explicitly marked superseded.

    Without this, a module added by `git merge upstream/main` would sit unnoticed in
    neither list — neither running nor consciously excluded.
    """

    from inspect import getmembers, isfunction

    from mac_cleanup import default_modules

    from mc.modules.upstream import ADOPTED, DECLINED, SUPERSEDED

    available = {name for name, _ in getmembers(default_modules, isfunction)}
    triaged = set(ADOPTED) | SUPERSEDED | set(DECLINED)

    untriaged = available - triaged
    assert not untriaged, (
        f"upstream modules not classified: {sorted(untriaged)}. "
        "Add each to ADOPTED, SUPERSEDED or DECLINED in mc/modules/upstream.py."
    )


@pytest.mark.parametrize(
    "capability,module_name",
    [
        # From MasterCleanScript.sh
        ("user caches", "user_caches"),
        ("system caches", "system_caches"),
        ("system logs", "system_logs"),
        ("container caches", "container_caches"),
        ("font caches", "font_cache"),
        ("launch services rebuild", "launch_services"),
        ("dns flush", "dns_cache"),
        ("periodic scripts", "periodic_scripts"),
        ("inactive memory purge", "memory_purge"),
        ("xcode derived data", "xcode"),
        ("xcode simulators", "xcode_simulators"),
        ("android sdk keep-latest", "android_sdk"),
        ("homebrew cleanup", "homebrew"),
        ("npm/nvm", "node"),
        ("gradle", "android_caches"),
        ("teams", "microsoft_teams"),
        ("adobe", "adobe_caches"),
        ("trash", "trash"),
        # From mac-scripts
        ("ds_store sweep", "ds_store"),
        ("tmp cleanup", "temp_files"),
        ("react native caches", "react_native"),
        # CleanMyMac / OnyX equivalents
        ("orphaned app leftovers", "app_leftovers"),
        ("unified log", "unified_log"),
        ("local snapshots", "local_snapshots"),
        ("electron sweeper", "electron_apps"),
        ("disk verification", "disk_health"),
    ],
)
def test_replaced_script_capabilities_are_covered(capability, module_name):
    """Each capability of the scripts this replaces maps to a registered module."""

    assert module_name in REGISTRY, f"{capability} has no module ({module_name})"


# ---------------------------------------------------------------------------
# browser_test_profiles
#
# This is the only module that deletes inside the user's own working trees rather
# than inside ~/Library, so what it declines to match matters as much as what it
# matches. A directory called "profile" is not evidence of anything by itself.
# ---------------------------------------------------------------------------


def _make_ff_profile(root):
    """A directory with the structure Firefox actually writes."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "times.json").write_text("{}")
    (root / "cache2").mkdir(exist_ok=True)
    return root


def test_browser_test_profiles_finds_harness_scratch(tmp_path, monkeypatch):
    """A .work/profile carrying Firefox markers is picked up."""

    from mc.modules import browsers

    target = _make_ff_profile(tmp_path / "repo" / "firefox" / "testing" / ".work" / "profile")
    monkeypatch.setattr(browsers, "TEST_PROFILE_ROOTS", (str(tmp_path),))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "nonexistent-tmp"))

    found = []
    ctx = _RecordingContext(found)
    browsers.browser_test_profiles(ctx)

    assert str(target) in found


def test_browser_test_profiles_ignores_lookalikes(tmp_path, monkeypatch):
    """
    A directory named "profile" with no Firefox structure is left alone.

    This is the case that would lose someone's work: "profile" is an ordinary name for
    an ordinary source directory.
    """

    from mc.modules import browsers

    decoy = tmp_path / "repo" / "src" / ".work" / "profile"
    decoy.mkdir(parents=True)
    (decoy / "index.ts").write_text("export const profile = {}\n")

    monkeypatch.setattr(browsers, "TEST_PROFILE_ROOTS", (str(tmp_path),))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "nonexistent-tmp"))

    found = []
    ctx = _RecordingContext(found)
    browsers.browser_test_profiles(ctx)

    assert found == []
    assert (decoy / "index.ts").exists()


class _RecordingContext:
    """Minimal Context stand-in that records declared paths instead of deleting."""

    def __init__(self, sink):
        self._sink = sink
        self.skipped = None

    def skip(self, reason):
        self.skipped = reason
        return None

    def step(self, _message):
        sink = self._sink

        class _Step:
            def path(self, *patterns, **_kwargs):
                sink.extend(patterns)
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        return _Step()


def test_export_paths_writes_module_and_rule(tmp_path, monkeypatch):
    """
    --export-paths emits machine-readable TSV and deletes nothing.

    MyMacSetup derives its Time Machine exclusions from this, on the reasoning that
    anything safe to DELETE is certainly safe to omit from a backup. The contract
    that matters to that consumer: absolute paths, never truncated for display, and
    a dry run regardless of the other flags.
    """

    from mc.cli import main

    out = tmp_path / "rules.tsv"
    rc = main(["--export-paths", str(out), "--only", "user_caches", "--no-update", "--no-notify"])

    assert rc == 0
    assert out.exists()

    lines = [line for line in out.read_text().splitlines() if line]
    for line in lines:
        module, _, rule = line.partition("\t")
        assert module, f"no module attributed: {line!r}"
        assert rule.startswith("/"), f"rule is not absolute: {rule!r}"
        assert "…" not in rule, f"rule was truncated for display: {rule!r}"
