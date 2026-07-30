"""
Per-project build artefact cleaner — ``mc --project-clean <dir>``.

Replaces ``others/clean_react_native.sh`` from the old mac-scripts repo, generalised
beyond React Native to whatever the project turns out to be.

Kept out of the module system on purpose. Everything in :mod:`mc.modules` is
machine-scoped and safe to run unattended on a schedule; deleting ``node_modules`` and
``ios/Pods`` is neither. It is an explicit, interactive, one-directory-at-a-time
operation, so it gets its own entry point.

Differences from the original script, all deliberate:

* It detects the project type instead of assuming React Native, and skips what does not
  apply.
* It never runs ``xcrun simctl erase all``. That wipes *every* simulator on the machine —
  all installed apps, all login state, for every project — as a side effect of cleaning
  one checkout.
* It refuses to run anywhere that is not recognisably a project root, so a mistyped path
  cannot turn into a recursive delete of your home directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from mac_cleanup.console import console

from mc import policy
from mc.util import human, path_size, run, which

__all__ = ["clean_project"]


@dataclass
class Artefact:
    """One removable build artefact."""

    relative_path: str
    description: str


#: Marker file -> artefacts to remove. A project can match several at once.
PROJECT_TYPES = {
    "package.json": [
        Artefact("node_modules", "npm/yarn/pnpm dependencies"),
        Artefact(".expo", "Expo build cache"),
        Artefact("coverage", "test coverage output"),
        Artefact(".nyc_output", "nyc coverage output"),
        Artefact("dist", "build output"),
        Artefact("build", "build output"),
        Artefact(".next", "Next.js build cache"),
        Artefact(".turbo", "Turborepo cache"),
        Artefact(".parcel-cache", "Parcel cache"),
        Artefact(".vite", "Vite cache"),
        Artefact("node_modules/.cache", "bundler caches"),
    ],
    "android/gradlew": [
        Artefact("android/app/build", "Android app build output"),
        Artefact("android/build", "Android build output"),
        Artefact("android/.gradle", "project Gradle state"),
        Artefact("android/.cxx", "native build output"),
    ],
    "ios/Podfile": [
        Artefact("ios/Pods", "CocoaPods dependencies"),
        Artefact("ios/build", "Xcode build output"),
        Artefact("ios/DerivedData", "project-local derived data"),
    ],
    "Cargo.toml": [Artefact("target", "Cargo build output")],
    "pubspec.yaml": [Artefact("build", "Flutter build output"), Artefact(".dart_tool", "Dart tool cache")],
    "go.mod": [Artefact("bin", "compiled binaries")],
    "pom.xml": [Artefact("target", "Maven build output")],
    "build.gradle": [Artefact("build", "Gradle build output"), Artefact(".gradle", "project Gradle state")],
    "Package.swift": [Artefact(".build", "SwiftPM build output")],
    "pyproject.toml": [
        Artefact(".venv", "virtualenv"),
        Artefact(".pytest_cache", "pytest cache"),
        Artefact(".mypy_cache", "mypy cache"),
        Artefact(".ruff_cache", "ruff cache"),
        Artefact("__pycache__", "bytecode cache"),
    ],
}

#: Any of these makes a directory a plausible project root.
PROJECT_MARKERS = tuple(marker.split("/")[0] for marker in PROJECT_TYPES) + (".git",)


def _is_project_root(directory: Path) -> bool:
    """Guard against being pointed at a home directory or a volume root."""

    return any((directory / marker).exists() for marker in PROJECT_MARKERS)


def clean_project(target: str, *, dry_run: bool = False, deep: bool = False) -> int:
    """
    Remove build artefacts from a single project directory.

    :param target: Project root.
    :param dry_run: Report what would go, delete nothing.
    :param deep: Also run the package managers' own cache-clean commands, which affect
        the whole machine rather than just this project.
    :return: Process exit code.
    """

    directory = Path(target).expanduser().resolve()

    if not directory.is_dir():
        console.print(f"[danger]{directory} is not a directory[/danger]")
        return 1

    blocked = policy.is_protected(str(directory))
    if blocked is not None:
        console.print(f"[danger]Refusing to clean {directory}:[/danger] {blocked}")
        return 1

    if not _is_project_root(directory):
        console.print(
            f"[danger]{directory} does not look like a project root[/danger] "
            f"(no {', '.join(PROJECT_MARKERS[:5])}, ...). Refusing to run."
        )
        return 1

    console.print(f"[info]Cleaning project[/info] {directory}")

    # -- work out what applies ---------------------------------------------------------
    applicable: List[Artefact] = []
    detected: List[str] = []

    for marker, artefacts in PROJECT_TYPES.items():
        if not (directory / marker).exists():
            continue
        detected.append(marker)
        applicable.extend(artefacts)

    console.print(f"  detected: {', '.join(detected)}")

    # -- measure and remove ------------------------------------------------------------
    total = 0
    removed = 0

    for artefact in applicable:
        path = directory / artefact.relative_path

        # resolve() above means the project root is symlink-free; confirm each artefact
        # still lands inside it, so a symlinked node_modules cannot redirect the delete.
        if not path.exists() or directory not in path.resolve().parents:
            continue

        size = path_size(path)
        total += size

        console.print(f"  {'would remove' if dry_run else 'removing'} {human(size):>10}  "
                      f"{artefact.relative_path}  [dim]{artefact.description}[/dim]")

        if not dry_run:
            import shutil

            try:
                shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
                removed += 1
            except OSError as exc:
                console.print(f"  [danger]failed:[/danger] {artefact.relative_path}: {exc}")

    # Python bytecode caches are scattered rather than in one place.
    if (directory / "pyproject.toml").exists() or (directory / "setup.py").exists():
        for pycache in directory.rglob("__pycache__"):
            if ".venv" in pycache.parts or "node_modules" in pycache.parts:
                continue
            total += path_size(pycache)
            if not dry_run:
                import shutil

                shutil.rmtree(pycache, ignore_errors=True)

    # -- machine-wide extras -----------------------------------------------------------
    if deep and not dry_run:
        _deep_clean(directory)

    if dry_run:
        console.print(f"\n[info]Would reclaim approximately {human(total)}[/info]")
    else:
        console.print(f"\n[success]Reclaimed {human(total)}[/success] from {removed} artefact(s)")
        _print_next_steps(detected)

    return 0


def _deep_clean(directory: Path) -> None:
    """
    Run the package managers' own clean commands.

    Separate from the artefact removal above because these affect every project on the
    machine, not just this one.
    """

    steps: List[tuple] = []

    if (directory / "package.json").exists():
        if (directory / "yarn.lock").exists() and which("yarn"):
            steps.append((["yarn", "cache", "clean"], "yarn cache"))
        if (directory / "pnpm-lock.yaml").exists() and which("pnpm"):
            steps.append((["pnpm", "store", "prune"], "pnpm store"))

    if (directory / "android/gradlew").exists():
        steps.append((["./gradlew", "clean"], "gradle clean"))

    if (directory / "ios/Podfile").exists() and which("pod"):
        steps.append((["pod", "cache", "clean", "--all"], "CocoaPods cache"))

    if which("watchman"):
        steps.append((["watchman", "watch-del", str(directory)], "watchman watch"))

    for argv, description in steps:
        console.print(f"  running {description}...")
        cwd = directory / "android" if argv[0] == "./gradlew" else directory
        result = run(argv, timeout=900, cwd=cwd)
        if result.returncode != 0:
            console.print(f"  [warning]{description} exited {result.returncode}[/warning] (continuing)")


def _print_next_steps(detected: List[str]) -> None:
    """Tell the user how to get the project working again."""

    steps: List[str] = []

    if "package.json" in detected:
        steps.append("npm install   (or yarn / pnpm install)")
    if "ios/Podfile" in detected:
        steps.append("cd ios && pod install")
    if "Cargo.toml" in detected:
        steps.append("cargo build")
    if "pyproject.toml" in detected:
        steps.append("uv sync   (or poetry install)")

    if steps:
        console.print("\n[info]To restore the project:[/info]")
        for step in steps:
            console.print(f"  {step}")
