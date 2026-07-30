"""
Browser caches.

Caches only. Cookies, history, saved passwords and profiles are deliberately out of
scope here — clearing those logs you out of everything and loses your session state,
which is not what "free up disk space" should mean. The opt-in :mod:`mc.modules.privacy`
module handles that separately, and is off by default.

Firefox alone is 8.5 GB of Application Support plus 1 GB of Caches on this machine.
"""

from __future__ import annotations

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
