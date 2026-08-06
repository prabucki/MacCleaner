"""
Browser caches.

Caches only. Cookies, history, saved passwords and profiles are deliberately out of
scope here — clearing those logs you out of everything and loses your session state,
which is not what "free up disk space" should mean. The opt-in :mod:`mc.modules.privacy`
module handles that separately, and is off by default.

Firefox alone is 8.5 GB of Application Support plus 1 GB of Caches on this machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from mc.registry import Context, Risk, cleanup_module


@cleanup_module(
    name="safari",
    risk=Risk.STANDARD,
    title="Safari caches",
    requires_any=("~/Library/Caches/com.apple.Safari", "~/Library/Containers/com.apple.Safari"),
    tags=("browser", "apps"),
)
def safari(ctx: Context) -> None:
    """Safari's cache and favicon store. Needs Full Disk Access to see the container."""

    with ctx.step("Clearing Safari caches") as step:
        step.path(
            "~/Library/Caches/com.apple.Safari/*",
            "~/Library/Containers/com.apple.Safari/Data/Library/Caches/*",
            "~/Library/Caches/com.apple.WebKit.WebContent/*",
            "~/Library/Caches/com.apple.WebKit.Networking/*",
        )
        # ~/Library/Safari is soft-protected because it also holds bookmarks, reading
        # list and history. Only the two icon caches are taken, by name.
        step.path(
            "~/Library/Safari/Favicon Cache/*",
            "~/Library/Safari/Touch Icons Cache/*",
            override="icon caches only; bookmarks and history are not touched",
        )


@cleanup_module(
    name="chromium_browsers",
    risk=Risk.STANDARD,
    title="Chrome and Chromium caches",
    requires_any=(
        "~/Library/Application Support/Google/Chrome",
        "~/Library/Application Support/Chromium",
        "~/Library/Application Support/BraveSoftware",
        "~/Library/Application Support/Arc",
        "~/Library/Application Support/Microsoft Edge",
    ),
    tags=("browser", "apps"),
)
def chromium_browsers(ctx: Context) -> None:
    """
    Chrome-family caches across every profile.

    The ``*`` in the profile position covers Default, Profile 1, Profile 2 and so on, so
    this does not silently miss secondary profiles the way a Default-only rule would.
    """

    roots = (
        "~/Library/Application Support/Google/Chrome",
        "~/Library/Application Support/Google/Chrome Canary",
        "~/Library/Application Support/Chromium",
        "~/Library/Application Support/BraveSoftware/Brave-Browser",
        "~/Library/Application Support/Microsoft Edge",
        "~/Library/Application Support/Arc",
        "~/Library/Application Support/Vivaldi",
    )

    for root in roots:
        if not ctx.exists(root):
            continue

        name = root.rsplit("/", 1)[-1]

        with ctx.step(f"Clearing {name} caches") as step:
            step.path(
                f"{root}/*/Cache/*",
                f"{root}/*/Code Cache/*",
                f"{root}/*/GPUCache/*",
                f"{root}/*/Service Worker/CacheStorage/*",
                f"{root}/*/Service Worker/ScriptCache/*",
                f"{root}/*/Application Cache/*",
                f"{root}/*/File System/*",
                f"{root}/*/Storage/ext/*/def/GPUCache/*",
                f"{root}/ShaderCache/*",
                f"{root}/GrShaderCache/*",
                f"{root}/component_crx_cache/*",
                f"{root}/extensions_crx_cache/*",
                f"{root}/Crashpad/completed/*",
                f"{root}/*/Extension State/*.log",
            )

    with ctx.step("Clearing Chrome-family HTTP caches") as step:
        step.path(
            "~/Library/Caches/Google/Chrome/*",
            "~/Library/Caches/com.google.Chrome/*",
            "~/Library/Caches/BraveSoftware/*",
            "~/Library/Caches/company.thebrowser.Browser/*",
        )


@cleanup_module(
    name="firefox",
    risk=Risk.STANDARD,
    title="Firefox caches",
    requires_any=("~/Library/Application Support/Firefox", "~/Library/Caches/Firefox"),
    tags=("browser", "apps"),
)
def firefox(ctx: Context) -> None:
    """
    Firefox caches across all profiles, including Tor Browser.

    ``storage/default`` is left alone — that is site data (IndexedDB, service worker
    storage), not cache.
    """

    with ctx.step("Clearing Firefox caches") as step:
        step.path(
            "~/Library/Caches/Firefox/Profiles/*/cache2/*",
            "~/Library/Caches/Firefox/Profiles/*/startupCache/*",
            "~/Library/Caches/Firefox/Profiles/*/thumbnails/*",
            "~/Library/Application Support/Firefox/Profiles/*/startupCache/*",
            "~/Library/Application Support/Firefox/Profiles/*/shader-cache/*",
            "~/Library/Application Support/Firefox/Profiles/*/crashes/*",
            "~/Library/Application Support/Firefox/Crash Reports/*",
            "~/Library/Application Support/Firefox/Pending Pings/*",
        )

    if ctx.exists("~/Library/Application Support/TorBrowser-Data"):
        with ctx.step("Clearing Tor Browser caches") as step:
            step.path(
                "~/Library/Application Support/TorBrowser-Data/Browser/Caches/*",
                "~/Library/Application Support/TorBrowser-Data/Browser/*/cache2/*",
            )


#: Where throwaway profiles are looked for. Deliberately a short list of conventional
#: project roots rather than a walk of ``$HOME``: these directories are scratch space
#: inside working trees, so a full-home scan would cost far more than it finds.
TEST_PROFILE_ROOTS = (
    "~/Drive/Projects",
    "~/Projects",
    "~/Developer",
    "~/dev",
    "~/src",
    "~/code",
)

#: How far below each root to look. A harness keeps its scratch near its own scripts,
#: so `<root>/<repo>/<area>/<harness>/.work/profile` is about as deep as it gets.
TEST_PROFILE_MAX_DEPTH = 6

#: A candidate must carry these to be treated as a Firefox profile. ``times.json`` is
#: written at profile creation and ``prefs.js`` at first shutdown; requiring one of them
#: alongside a cache directory is what separates a real profile from any directory that
#: merely happens to be called ``profile``.
PROFILE_MARKERS = ("times.json", "prefs.js")
PROFILE_CACHE_DIRS = ("cache2", "startupCache", "storage", "extensions")


def _looks_like_firefox_profile(path: Path) -> bool:
    """
    Is this really a Firefox profile directory?

    The check exists because this module is the only one here that deletes inside the
    user's own working trees rather than inside ``~/Library``. A name match alone is not
    evidence — plenty of projects have a directory called ``profile`` that means
    something entirely different — so require the structure Firefox actually writes.
    """

    try:
        names = {entry.name for entry in path.iterdir()}
    except OSError:
        return False

    return any(m in names for m in PROFILE_MARKERS) and any(d in names for d in PROFILE_CACHE_DIRS)


@cleanup_module(
    name="browser_test_profiles",
    risk=Risk.STANDARD,
    title="Throwaway browser profiles from test harnesses",
    tags=("browser", "dev"),
)
def browser_test_profiles(ctx: Context) -> None:
    """
    Scratch Firefox profiles created by automation, in project trees rather than
    ``~/Library``.

    A harness that drives a real browser has to give it a real profile, and a real
    profile fills up like one: extensions, ``cache2``, ``startupCache``, Widevine, the
    Safebrowsing lists, IndexedDB. The copy found while writing this module was 312 MB
    from a single ``userChrome.css`` screenshotting run — and because the harness
    correctly gitignores its own scratch directory, nothing in the repository, the
    working tree, or ``git status`` ever mentioned it again.

    That is the whole reason for this module: the :mod:`firefox` module above cleans
    profiles where Firefox itself puts them, and these are precisely the ones it cannot
    see. They are also the ones nobody is watching, since they were never meant to
    outlive the test run that made them.

    The harness recreates the profile on its next run, so this is a no-loss cleanup in
    the same sense as a cache — but only the profile is taken. Sibling scratch such as
    captured screenshots or logs is left alone; it is small, and it is plausibly output
    somebody still wants to look at.
    """

    found: List[Path] = []

    for root_spec in TEST_PROFILE_ROOTS:
        root = Path(root_spec).expanduser()
        if not root.is_dir():
            continue

        root_depth = len(root.parts)

        for current, dirs, _files in os.walk(root, topdown=True, followlinks=False):
            here = Path(current)

            # Depth-bound the walk, and never descend into the heavy trees that make an
            # unbounded project-tree scan unusable.
            if len(here.parts) - root_depth >= TEST_PROFILE_MAX_DEPTH:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "vendor", "Pods", "target")]

            # ".work/profile" is the ffprobe convention; "*mozprofile*" is what
            # geckodriver and the selenium bindings name theirs.
            for d in list(dirs):
                candidate = here / d
                is_scratch_name = (d == "profile" and here.name == ".work") or "mozprofile" in d
                if is_scratch_name and _looks_like_firefox_profile(candidate):
                    found.append(candidate)
                    dirs.remove(d)  # nothing further inside it is interesting

    # geckodriver's own temporary profiles, which it leaves behind whenever a driver
    # process is killed rather than closed.
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))
    if tmp.is_dir():
        try:
            found.extend(
                p for p in tmp.glob("rust_mozprofile*") if p.is_dir() and _looks_like_firefox_profile(p)
            )
        except OSError:
            pass

    if not found:
        return ctx.skip("no throwaway browser profiles found")

    with ctx.step(f"Removing {len(found)} throwaway browser profile(s)") as step:
        for path in found:
            step.path(str(path))
