"""
Bridge to upstream mac-cleanup-py's own modules.

Upstream ships ~40 cleanup modules. Most are superseded here by something broader — its
``xcode`` module does DerivedData and Archives, ours also does DeviceSupport, ModuleCache,
SwiftPM and Previews — but a dozen cover tools nobody here wrote a module for.

Rather than copy them, the upstream functions are called directly. They populate the same
collector (``_Collector`` is a Borg, so every instance shares one execute list), and the
patched ``Path`` routes their deletions through the same policy, quarantine and reporting
as everything else. So they inherit all the safety work for free.

Keeping this list explicit rather than "everything upstream has" means a new upstream
module cannot start deleting things after a ``git merge upstream/main`` without someone
looking at it first.
"""

from __future__ import annotations

from typing import Dict, Tuple

from mc.registry import Context, Risk, cleanup_module

#: Upstream modules worth running, mapped to (risk tier, description).
#:
#: Everything absent from this list is deliberately omitted — either superseded by a
#: module in this package, or for a tool that is not installed here.
ADOPTED: Dict[str, Tuple[str, str]] = {
    "dropbox": (Risk.STANDARD, "Dropbox cache"),
    "google_drive": (Risk.STANDARD, "Google Drive File Stream content cache"),
    "composer": (Risk.STANDARD, "PHP Composer cache"),
    "conan": (Risk.STANDARD, "Conan C/C++ package cache"),
    "nuget_cache": (Risk.AGGRESSIVE, ".NET NuGet package cache"),
    "docker": (Risk.AGGRESSIVE, "Docker images, containers and build cache"),
    "lunarclient": (Risk.STANDARD, "Lunar Client logs and caches"),
    "cacher": (Risk.SAFE, "Cacher logs"),
    "kite": (Risk.SAFE, "Kite logs"),
    "wget_logs": (Risk.SAFE, "wget logs and HSTS database"),
}

#: Upstream modules deliberately *not* run, with the reason. Distinct from SUPERSEDED:
#: nothing here replaces them, we have decided against them.
DECLINED = {
    # Deletes .ipa archives under ~/Music, which is soft-protected user territory. The
    # directory is a leftover from pre-Catalina iTunes and the archives are the only
    # local copy of any app version no longer on the App Store.
    "ios_apps": "targets ~/Music; archives may be irreplaceable",
}

#: Recorded for the benefit of anyone reading this later, and asserted in the tests: if
#: upstream adds a module, it appears in neither list and the test flags it.
SUPERSEDED = frozenset(
    {
        "trash",
        "system_caches",
        "system_log",
        "jetbrains",
        "adobe",
        "chrome",
        "chromium_caches",
        "arc",  # covered by our chromium_browsers, which also handles secondary profiles
        "xcode",
        "xcode_simulators",
        "android",
        "gradle",
        "brew",
        "gem",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "pod",
        "go",
        "microsoft_teams",
        "poetry",
        "java_cache",
        "dns_cache",
        "inactive_memory",
        "telegram",
        "obsidian_caches",
        "ea_caches",
        "ios_backups",
        "steam",
        "minecraft",
        "pyenv",
    }
)


def _register(module_name: str, risk: str, description: str) -> None:
    """Wrap one upstream module function as a MacCleaner module."""

    @cleanup_module(
        name=f"upstream_{module_name}",
        risk=risk,
        title=description,
        tags=("upstream",),
    )
    def _adapter(ctx: Context, _name: str = module_name, _description: str = description) -> None:
        from mac_cleanup import default_modules

        function = getattr(default_modules, _name, None)

        if function is None:
            # Upstream removed or renamed it; not an error, just nothing to do.
            return ctx.skip(f"upstream module '{_name}' no longer exists")

        # Upstream modules open their own collector context and do their own existence
        # checks, so they are called directly. Anything they queue is tagged with this
        # module's name by the surrounding collect() call.
        function()

    _adapter.__name__ = f"upstream_{module_name}"


for _name, (_risk, _description) in ADOPTED.items():
    _register(_name, _risk, _description)
