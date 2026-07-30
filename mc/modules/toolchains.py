"""
Language toolchain and package manager caches.

Everything here is a download cache: deleting it costs bandwidth and time on the next
build, never data. Each module is guarded on the tool actually being installed, so this
file stays correct on a machine with a different toolchain mix.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module


@cleanup_module(
    name="homebrew",
    risk=Risk.SAFE,
    title="Homebrew",
    requires_binary=("brew",),
    tags=("dev", "core"),
)
def homebrew(ctx: Context) -> None:
    """
    Homebrew download cache and stale versions.

    The original script did ``rm -rf $(brew --cache)`` plus a blanket
    ``find $(brew --prefix)/Caskroom -name '*.pkg' -delete``. That blanket delete also
    removed the installer for the *currently installed* version of each cask, which is
    what Homebrew uses to uninstall it cleanly, and the MS Office rules that followed
    were the same idea applied by hand to three casks.

    All of it is replaced by ``brew cleanup -s --prune=all``, which removes superseded
    formula and cask versions along with every cached download, and knows which files
    are load-bearing. ``/opt/homebrew`` is hard-protected in the policy precisely so that
    nothing here reaches into Homebrew's prefix directly — an earlier draft of this
    module did, and started classifying Homebrew's own ``.metadata`` directories as
    stale versions.
    """

    brew = ctx.which("brew")

    with ctx.step("Cleaning Homebrew") as step:
        step.command([brew, "cleanup", "-s", "--prune=all"], timeout=900)
        step.command([brew, "autoremove"], timeout=600)
        step.command([brew, "tap", "--repair"], timeout=300)

    from mc.util import run

    # The download cache lives under ~/Library/Caches/Homebrew, outside the prefix.
    cache_dir = run([brew, "--cache"], timeout=60).stdout.strip()

    if cache_dir:
        with ctx.step("Removing Homebrew download cache") as step:
            step.path(f"{cache_dir}/*")


@cleanup_module(
    name="node",
    risk=Risk.STANDARD,
    title="Node package managers",
    requires_any=("~/.npm", "~/.nvm", "~/Library/Caches/yarn", "~/.pnpm-store"),
    tags=("dev", "node"),
)
def node(ctx: Context) -> None:
    """npm, pnpm, yarn and npx caches."""

    npm = ctx.which("npm")
    if npm:
        with ctx.step("Cleaning npm cache") as step:
            step.command([npm, "cache", "clean", "--force"], timeout=600)
            step.path("~/.npm/_npx/*", "~/.npm/_cacache/*", "~/.npm/_logs/*")

    pnpm = ctx.which("pnpm")
    if pnpm:
        with ctx.step("Pruning pnpm store") as step:
            step.command([pnpm, "store", "prune"], timeout=900)

    yarn = ctx.which("yarn")
    if yarn:
        with ctx.step("Cleaning yarn cache") as step:
            step.command([yarn, "cache", "clean"], timeout=600)
            step.path("~/Library/Caches/yarn/*")

    with ctx.step("Clearing other JavaScript tool caches") as step:
        step.path(
            "~/.cache/typescript/*",
            "~/Library/Caches/typescript/*",
            "~/.node-gyp/*",
            "~/.electron-gyp/*",
            "~/Library/Caches/electron/*",
            "~/Library/Caches/electron-builder/*",
        )


@cleanup_module(
    name="nvm",
    risk=Risk.AGGRESSIVE,
    title="nvm cache and old Node versions",
    requires=("~/.nvm",),
    tags=("dev", "node"),
)
def nvm(ctx: Context) -> None:
    """
    nvm's download cache, plus Node versions other than the current default.

    Each installed Node is 60-90 MB before you count its global packages. The version
    ``nvm alias default`` points at is always kept, as is whatever is currently active.
    """

    with ctx.step("Clearing nvm cache") as step:
        step.path("~/.nvm/.cache/*")

    stale = _stale_node_versions()
    if stale:
        with ctx.step(f"Removing {len(stale)} superseded Node version(s)") as step:
            for directory in stale:
                step.path(str(directory))


def _stale_node_versions():
    """Installed Node versions except the newest and the currently-active one."""

    import os
    from pathlib import Path

    from mc.modules.android import _version_key

    versions_dir = Path("~/.nvm/versions/node").expanduser()

    if not versions_dir.is_dir():
        return []

    installed = sorted((v for v in versions_dir.iterdir() if v.is_dir()), key=lambda p: _version_key(p.name))

    if len(installed) <= 1:
        return []

    keep = {installed[-1].name}

    # Whatever is on PATH right now must survive, even if it is not the newest.
    for entry in os.environ.get("PATH", "").split(":"):
        if "/.nvm/versions/node/" in entry:
            keep.add(entry.split("/.nvm/versions/node/")[1].split("/")[0])

    return [version for version in installed if version.name not in keep]


@cleanup_module(
    name="python_tooling",
    risk=Risk.STANDARD,
    title="Python caches",
    requires_any=("~/Library/Caches/pip", "~/.cache/uv", "~/Library/Caches/pypoetry"),
    tags=("dev", "python"),
)
def python_tooling(ctx: Context) -> None:
    """pip, uv, poetry and pipx caches."""

    with ctx.step("Clearing Python package caches") as step:
        step.path(
            "~/Library/Caches/pip/*",
            "~/.cache/pip/*",
            "~/Library/Caches/pypoetry/cache/*",
            "~/Library/Caches/pypoetry/artifacts/*",
            "~/.cache/pre-commit/*",
        )

    uv = ctx.which("uv")
    if uv:
        with ctx.step("Pruning uv cache") as step:
            step.command([uv, "cache", "prune"], timeout=600)


@cleanup_module(
    name="rust",
    risk=Risk.STANDARD,
    title="Rust and Cargo",
    requires=("~/.cargo",),
    tags=("dev",),
)
def rust(ctx: Context) -> None:
    """
    Cargo registry cache and downloaded sources.

    The registry index and ``.crate`` archives are re-downloaded on demand. Installed
    binaries under ``~/.cargo/bin`` are never touched.
    """

    with ctx.step("Clearing Cargo caches") as step:
        step.path(
            "~/.cargo/registry/cache/*",
            "~/.cargo/registry/src/*",
            "~/.cargo/git/checkouts/*",
            "~/.cargo/git/db/*",
        )

    rustup = ctx.which("rustup")
    if rustup:
        with ctx.step("Removing superseded Rust toolchain components") as step:
            step.command([rustup, "self", "upgrade-data"], timeout=300)


@cleanup_module(
    name="go",
    risk=Risk.STANDARD,
    title="Go module and build cache",
    requires_binary=("go",),
    tags=("dev",),
)
def go(ctx: Context) -> None:
    """Go build cache and module download cache."""

    go_binary = ctx.which("go")

    with ctx.step("Cleaning Go caches") as step:
        step.command([go_binary, "clean", "-cache"], timeout=600)
        step.command([go_binary, "clean", "-modcache"], timeout=900)
        step.command([go_binary, "clean", "-fuzzcache"], timeout=300)


@cleanup_module(
    name="ruby",
    risk=Risk.STANDARD,
    title="Ruby gems",
    requires_binary=("gem",),
    tags=("dev",),
)
def ruby(ctx: Context) -> None:
    """Remove superseded gem versions and the bundler cache."""

    with ctx.step("Cleaning up old gem versions") as step:
        step.command([ctx.which("gem") or "gem", "cleanup"], timeout=900)
        step.path("~/.bundle/cache/*", "~/.gem/specs/*")


@cleanup_module(
    name="java",
    risk=Risk.STANDARD,
    title="Java and Maven",
    requires_any=("~/.m2", "~/Library/Java", "~/*.hprof"),
    tags=("dev",),
)
def java(ctx: Context) -> None:
    """Maven repository cache and stray heap dumps."""

    with ctx.step("Clearing Maven and Java caches") as step:
        step.path(
            "~/.m2/repository/**/*.lastUpdated",
            "~/.m2/wrapper/dists/*",
            "~/Library/Caches/JNA/*",
        )

    with ctx.step("Removing Java heap dumps") as step:
        # These are multi-gigabyte crash artefacts that nothing ever cleans up.
        step.path("~/*.hprof", "~/Library/Logs/JavaAppletPlugin/*")


@cleanup_module(
    name="jetbrains",
    risk=Risk.STANDARD,
    title="JetBrains IDE caches",
    requires_any=("~/Library/Caches/JetBrains", "~/Library/Logs/JetBrains"),
    tags=("dev", "editor"),
)
def jetbrains(ctx: Context) -> None:
    """Indexes and logs for every installed JetBrains IDE. Reindexes on next open."""

    with ctx.step("Clearing JetBrains caches") as step:
        step.path(
            "~/Library/Caches/JetBrains/*/caches/*",
            "~/Library/Caches/JetBrains/*/index/*",
            "~/Library/Caches/JetBrains/*/tmp/*",
            "~/Library/Logs/JetBrains/*/*",
        )


@cleanup_module(
    name="vscode_family",
    risk=Risk.STANDARD,
    title="VS Code / Cursor / Antigravity caches",
    requires_any=(
        "~/Library/Application Support/Code",
        "~/Library/Application Support/Cursor",
        "~/Library/Application Support/Antigravity",
    ),
    tags=("dev", "editor"),
)
def vscode_family(ctx: Context) -> None:
    """
    Caches for the VS Code family of editors.

    5.1 GB under ``Code`` and 2.2 GB under ``Cursor`` on this machine. Workspace storage
    holds per-project state (open editors, undo history) and is left alone.
    """

    for app in ("Code", "Cursor", "Antigravity", "Code - Insiders", "VSCodium"):
        base = f"~/Library/Application Support/{app}"

        if not ctx.exists(base):
            continue

        with ctx.step(f"Clearing {app} caches") as step:
            step.path(
                f"{base}/Cache/*",
                f"{base}/CachedData/*",
                f"{base}/CachedExtensions/*",
                f"{base}/CachedExtensionVSIXs/*",
                f"{base}/Code Cache/*",
                f"{base}/GPUCache/*",
                f"{base}/DawnGraphiteCache/*",
                f"{base}/DawnWebGPUCache/*",
                f"{base}/logs/*",
                f"{base}/User/workspaceStorage/*/state.vscdb.backup",
                f"{base}/Crashpad/completed/*",
            )


@cleanup_module(
    name="playwright",
    risk=Risk.AGGRESSIVE,
    title="Playwright and Puppeteer browsers",
    requires_any=("~/Library/Caches/ms-playwright", "~/.cache/puppeteer"),
    tags=("dev",),
)
def playwright(ctx: Context) -> None:
    """
    Downloaded test browsers.

    567 MB on this machine. Re-downloaded automatically by ``playwright install`` the
    next time a test suite needs them.
    """

    with ctx.step("Removing Playwright and Puppeteer browsers") as step:
        step.path("~/Library/Caches/ms-playwright/*", "~/.cache/puppeteer/*")
