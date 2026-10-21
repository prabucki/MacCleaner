"""
Android SDK, Gradle and Android Studio.

The keep-latest logic is a port of ``clean_keep_latest()`` from the original
MasterCleanScript, with the sharp edges removed. The original:

* ``cd "$TARGET_DIR" || exit 1`` — a missing directory aborted the *entire* script,
  taking every later cleanup step with it;
* parsed ``ls -1d`` output, so a directory name containing a space or newline broke it;
* ran ``rm -rf`` on the result of that parse with no validation.

This version enumerates with :func:`pathlib.Path.iterdir`, sorts with a real version
comparison, and hands the result to the same policy-checked deletion path as everything
else.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Set, Tuple

from mc.registry import Context, Risk, cleanup_module
from mc.util import find_in_projects, run

ANDROID_SDK = "~/Library/Android/sdk"


def _version_key(name: str) -> Tuple:
    """
    Sort key approximating ``sort -V``.

    Handles the three naming schemes that show up in an Android SDK: ``27.0.12077973``
    (NDK), ``android-35`` (platforms), ``android-35-ext15`` and ``29.0.3`` (build-tools).
    Non-numeric fragments sort before numeric ones so ``android-TiramisuPrivacySandbox``
    never wins over ``android-35``.
    """

    import re

    parts: List[Tuple[int, object]] = []

    for fragment in re.split(r"[.\-_]", name):
        if fragment.isdigit():
            parts.append((1, int(fragment)))
        elif fragment:
            parts.append((0, fragment))

    return tuple(parts)


def _all_but_latest(directory: str, keep: Sequence[str] = ()) -> List[Path]:
    """
    Every versioned subdirectory except the highest-versioned one.

    :param keep: Additional directory names to spare regardless of version.
    """

    root = Path(directory).expanduser()

    if not root.is_dir():
        return []

    children = sorted((child for child in root.iterdir() if child.is_dir()), key=lambda p: _version_key(p.name))

    return [child for child in children[:-1] if child.name not in keep]  # keep the newest


def _system_images_in_use() -> Set[str]:
    """
    System image versions that an existing AVD depends on.

    Keep-latest is the right default for everything the build re-downloads on demand,
    but a system image is the one component that does not work that way: Gradle refetches
    a missing NDK or platform without being asked, while an AVD whose image is gone is
    simply broken until someone opens the SDK Manager and puts it back. That asymmetry is
    invisible from the directory tree, so it is read from the AVDs themselves.
    """

    in_use: Set[str] = set()
    avd_root = Path("~/.android/avd").expanduser()

    if not avd_root.is_dir():
        return in_use

    for config in avd_root.glob("*.avd/config.ini"):
        try:
            text = config.read_text(errors="replace")
        except OSError:
            continue

        for line in text.splitlines():
            # "image.sysdir.1=system-images/android-36/google_apis_playstore/arm64-v8a/"
            if not line.startswith("image.sysdir"):
                continue
            _, _, value = line.partition("=")
            parts = [part for part in value.strip().split("/") if part]
            if len(parts) >= 2 and parts[0] == "system-images":
                in_use.add(parts[1])

    return in_use


@cleanup_module(
    name="android_sdk",
    risk=Risk.AGGRESSIVE,
    title="Android SDK (keep latest)",
    requires=(ANDROID_SDK,),
    tags=("dev", "android"),
)
def android_sdk(ctx: Context) -> None:
    """
    Remove superseded NDK, platform and build-tools versions, keeping the newest of each.

    7.6 GB sits under ``~/Library/Android`` on this machine. Note the trade-off: a
    project pinned to an older ``ndkVersion`` or ``compileSdk`` will re-download it on
    next sync. That is a few minutes, not a breakage.

    System images in use by an existing AVD are the exception and are always kept — see
    :func:`_system_images_in_use`.
    """

    targets = {
        "NDK": f"{ANDROID_SDK}/ndk",
        "platform": f"{ANDROID_SDK}/platforms",
        "build-tools": f"{ANDROID_SDK}/build-tools",
        "system image": f"{ANDROID_SDK}/system-images",
    }

    in_use = _system_images_in_use()

    removable = {
        label: _all_but_latest(path, keep=in_use if label == "system image" else ())
        for label, path in targets.items()
    }

    if not any(removable.values()):
        return ctx.skip("only one version of each component installed")

    for label, stale in removable.items():
        if not stale:
            continue

        with ctx.step(f"Removing {len(stale)} superseded Android {label} version(s)") as step:
            for directory in stale:
                step.path(str(directory))

    if in_use:
        ctx.report.module("android_sdk").reason = "kept system image(s) in use by an AVD: " + ", ".join(
            sorted(in_use)
        )


@cleanup_module(
    name="android_caches",
    risk=Risk.STANDARD,
    title="Android and Gradle caches",
    requires_any=("~/.android", "~/.gradle", "~/Library/Logs/Google/AndroidStudio*"),
    tags=("dev", "android"),
)
def android_caches(ctx: Context) -> None:
    """Gradle, AVD and Android Studio caches. All rebuilt on next use."""

    with ctx.step("Clearing Gradle caches") as step:
        # Build caches and downloaded dependencies: re-fetched on the next build.
        step.path(
            "~/.gradle/caches/build-cache-*",
            "~/.gradle/caches/transforms-*",
            "~/.gradle/caches/journal-*",
            "~/.gradle/caches/modules-2/files-2.1/*",
            "~/.gradle/daemon/*",
            "~/.gradle/native/*",
            "~/.gradle/wrapper/dists/*/*/*.zip",
        )

    with ctx.step("Clearing Android tooling caches") as step:
        step.path(
            "~/.android/cache/*",
            "~/.android/build-cache/*",
            "~/.android/avd/*.avd/*.lock",
            "~/Library/Caches/Google/AndroidStudio*/*",
            "~/Library/Logs/Google/AndroidStudio*/*",
        )


@cleanup_module(
    name="gradle_unused_wrappers",
    risk=Risk.STANDARD,
    title="Unused Gradle wrapper versions",
    requires=("~/.gradle/wrapper/dists",),
    tags=("dev", "android"),
)
def gradle_unused_wrappers(ctx: Context) -> None:
    """
    Unpacked Gradle distributions that no project under the project roots asks for.

    ``android_caches`` only removes the downloaded zips; the unpacked distributions stay
    forever, one per version any project ever used. 16 had piled up here (2.8 to 9.2,
    3.3 GB) against one version actually referenced. A wrongly removed version costs a
    single re-download on that project's next build.
    """

    properties = find_in_projects("gradle-wrapper.properties", max_depth=7)

    # Without any project to read, every version would look unused.
    if not properties:
        return ctx.skip("no gradle-wrapper.properties under the project roots")

    if run(["pgrep", "-f", "GradleDaemon"], timeout=10).returncode == 0:
        return ctx.skip("a Gradle daemon is running")

    wanted: Set[str] = set()
    for path in properties:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip().startswith("distributionUrl"):
                # distributionUrl=https\://services.gradle.org/distributions/gradle-8.10.2-all.zip
                wanted.add(line.rsplit("/", 1)[-1].removesuffix(".zip"))

    installed = ctx.glob("~/.gradle/wrapper/dists/gradle-*")
    unused = [d for d in installed if d.name not in wanted]

    if not unused:
        return ctx.skip(f"all {len(installed)} installed version(s) are referenced by a project")

    with ctx.step(f"Removing {len(unused)} unused Gradle version(s)") as step:
        for dist in unused:
            step.path(str(dist))

