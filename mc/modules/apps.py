"""
Per-application cleanup.

Most Electron apps are handled generically by :mod:`mc.modules.electron`. This file is
for apps that need something specific: a process quit first, a non-standard cache
location, or a rule that came from the original MasterCleanScript.
"""

from __future__ import annotations

from mc.registry import Context, Risk, cleanup_module


@cleanup_module(
    name="microsoft_teams",
    risk=Risk.STANDARD,
    title="Microsoft Teams",
    requires_any=(
        "~/Library/Application Support/Microsoft/Teams",
        "~/Library/Group Containers/UBF8T346G9.com.microsoft.teams",
    ),
    tags=("apps",),
)
def microsoft_teams(ctx: Context) -> None:
    """
    Teams caches. Quits Teams first, as the original script did with ``pkill -x Teams``.

    Covers both classic Teams and the newer container-based version.
    """

    with ctx.step("Clearing Microsoft Teams caches") as step:
        step.quit_app("Teams", "MSTeams")
        step.path(
            "~/Library/Application Support/Microsoft/Teams/Cache/*",
            "~/Library/Application Support/Microsoft/Teams/Code Cache/*",
            "~/Library/Application Support/Microsoft/Teams/GPUCache/*",
            "~/Library/Application Support/Microsoft/Teams/blob_storage/*",
            "~/Library/Application Support/Microsoft/Teams/tmp/*",
            "~/Library/Application Support/Microsoft/Teams/*logs*.txt",
            "~/Library/Application Support/Microsoft/Teams/watchdog/*",
            "~/Library/Group Containers/UBF8T346G9.com.microsoft.teams/Library/Caches/*",
            "~/Library/Containers/com.microsoft.teams2/Data/Library/Caches/*",
        )


@cleanup_module(
    name="communication_apps",
    risk=Risk.STANDARD,
    title="Chat and communication apps",
    requires_any=(
        "~/Library/Application Support/Ferdium",
        "~/Library/Application Support/Slack",
        "~/Library/Application Support/discord",
        "~/Library/Group Containers/*.ru.keepcoder.Telegram",
    ),
    tags=("apps",),
)
def communication_apps(ctx: Context) -> None:
    """
    Ferdium, Slack, Discord and Telegram.

    Ferdium is 13 GB on this machine — the biggest single directory under Application
    Support. Its per-service partitions are globbed rather than hardcoded by UUID the way
    the original script did (those UUIDs had gone stale).
    """

    with ctx.step("Clearing Ferdium service caches") as step:
        step.path(
            "~/Library/Application Support/Ferdium/Partitions/*/Cache/*",
            "~/Library/Application Support/Ferdium/Partitions/*/Code Cache/*",
            "~/Library/Application Support/Ferdium/Partitions/*/GPUCache/*",
            "~/Library/Application Support/Ferdium/Partitions/*/Service Worker/CacheStorage/*",
            "~/Library/Application Support/Ferdium/Cache/*",
            "~/Library/Application Support/Ferdium/logs/*",
        )

    with ctx.step("Clearing Slack and Discord caches") as step:
        step.path(
            "~/Library/Application Support/Slack/Cache/*",
            "~/Library/Application Support/Slack/Code Cache/*",
            "~/Library/Application Support/Slack/GPUCache/*",
            "~/Library/Application Support/Slack/Service Worker/CacheStorage/*",
            "~/Library/Application Support/Slack/logs/*",
            "~/Library/Application Support/discord/Cache/*",
            "~/Library/Application Support/discord/Code Cache/*",
            "~/Library/Application Support/discord/GPUCache/*",
        )


@cleanup_module(
    name="telegram",
    risk=Risk.AGGRESSIVE,
    title="Telegram cache",
    requires_any=("~/Library/Group Containers/*.ru.keepcoder.Telegram",),
    tags=("apps",),
)
def telegram(ctx: Context) -> None:
    """
    Telegram's media cache.

    Aggressive tier: this is downloaded media, so it re-downloads on demand, but on a
    slow connection that is noticeable. Telegram is quit first because it holds the
    database open.
    """

    with ctx.step("Clearing Telegram cache") as step:
        step.quit_app("Telegram")
        step.path("~/Library/Group Containers/*.ru.keepcoder.Telegram/*/account-*/postbox/media/*")


@cleanup_module(
    name="spotify",
    risk=Risk.STANDARD,
    title="Spotify cache",
    requires=("~/Library/Application Support/Spotify",),
    tags=("apps",),
)
def spotify(ctx: Context) -> None:
    """Offline audio cache. Re-downloads on demand; downloaded playlists are re-synced."""

    with ctx.step("Clearing Spotify cache") as step:
        step.path(
            "~/Library/Application Support/Spotify/PersistentCache/Storage/*",
            "~/Library/Caches/com.spotify.client/*",
        )


@cleanup_module(
    name="setapp_apps",
    risk=Risk.STANDARD,
    title="Setapp app caches",
    requires_any=("~/Library/Application Support/Setapp", "/Applications/Setapp"),
    tags=("apps",),
)
def setapp_apps(ctx: Context) -> None:
    """
    Caches for Setapp and the apps installed through it.

    CleanShot's media store is included: it keeps every screenshot and recording you have
    ever taken until you clear it, which the original script did explicitly.
    """

    with ctx.step("Clearing Setapp caches") as step:
        step.path(
            "~/Library/Application Support/Setapp/Cache/*",
            "~/Library/Caches/com.setapp.DesktopClient/*",
            "~/Library/Application Support/iStat Menus*/Cache/*",
        )

    with ctx.step("Clearing CleanShot media") as step:
        # Only the app's own cache and the "recently captured" store; the export
        # destination you configured is somewhere else and is not touched.
        step.path(
            "~/Library/Application Support/CleanShot/media/*",
            "~/Library/Caches/pl.maketheweb.cleanshotx/*",
        )


@cleanup_module(
    name="misc_apps",
    risk=Risk.STANDARD,
    title="Assorted app caches",
    tags=("apps",),
)
def misc_apps(ctx: Context) -> None:
    """
    The long tail from the original script, plus common equivalents.

    Everything is existence-checked by the deletion layer, so entries for apps that are
    not installed cost nothing.
    """

    with ctx.step("Clearing assorted app caches and logs") as step:
        step.path(
            # From the original MasterCleanScript
            "~/Library/Application Support/Transmit/Logs/*",
            "~/Library/Application Support/BetterTouchTool/.BTTClipboardManager_SUPPORT/_EXTERNAL_DATA/*",
            "~/Library/Application Support/com.nektony.Duplicate-File-Finder-SIII/Removed/*.log",
            # Common equivalents
            "~/Library/Application Support/Notion/Cache/*",
            "~/Library/Application Support/Notion/GPUCache/*",
            "~/Library/Application Support/obsidian/Cache/*",
            "~/Library/Application Support/obsidian/Code Cache/*",
            "~/Library/Application Support/obsidian/GPUCache/*",
            "~/Library/Application Support/GitKraken/Cache/*",
            "~/Library/Application Support/GitKrakenCLI/Cache/*",
            "~/Library/Caches/com.raycast.macos/*",
            "~/Library/Caches/com.brave.Browser.helper/*",
            "~/Library/Caches/com.hnc.Discord.ShipIt/*",
            "~/Library/Caches/com.microsoft.VSCode.ShipIt/*",
            "~/Library/Caches/com.tinyspeck.slackmacgap.ShipIt/*",
            # Sparkle updater leftovers, common to many third-party Mac apps
            "~/Library/Caches/*/org.sparkle-project.Sparkle/*",
        )

    with ctx.step("Clearing game launcher caches") as step:
        step.path(
            "~/Library/Application Support/Steam/appcache/*",
            "~/Library/Application Support/Steam/depotcache/*",
            "~/Library/Application Support/Steam/logs/*",
            "~/Library/Application Support/Steam/steamapps/shadercache/*",
            "~/Library/Application Support/Steam/steamapps/temp/*",
            "~/Library/Application Support/Electronic Arts/EA app/Logs/*",
            "~/Library/Application Support/Electronic Arts/EA app/OfflineCache/*",
            "~/Library/Application Support/minecraft/logs/*",
            "~/Library/Application Support/minecraft/crash-reports/*",
            "~/Library/Application Support/minecraft/webcache*/*",
        )

        if ctx.privileged.available:
            step.root_path("/Users/Shared/Blizzard/Battle.net/Cache/*")


@cleanup_module(
    name="apple_apps",
    risk=Risk.AGGRESSIVE,
    title="Apple app caches",
    tags=("apps",),
)
def apple_apps(ctx: Context) -> None:
    """
    Caches belonging to Apple's own apps.

    Mail's downloaded attachments are the notable one — they accumulate indefinitely and
    are re-downloadable from the server. Mail is quit first so it does not rewrite them.
    """

    with ctx.step("Clearing Mail caches") as step:
        step.path(
            "~/Library/Containers/com.apple.mail/Data/Library/Caches/*",
            "~/Library/Containers/com.apple.mail/Data/Library/Logs/Mail/*",
        )
        # Mail Downloads is inside the soft-protected ~/Library/Mail tree. These are
        # attachments already on the mail server, so removal is recoverable, but the
        # override is recorded in the report either way.
        step.path(
            "~/Library/Containers/com.apple.mail/Data/Library/Mail Downloads/*",
            override="mail attachments are re-downloadable from the server",
        )

    with ctx.step("Clearing Photos and Music caches") as step:
        step.path(
            "~/Library/Caches/com.apple.Photos/*",
            "~/Library/Caches/com.apple.amp.mediasharingd/*",
            "~/Library/Caches/com.apple.Music/*",
            "~/Library/Caches/com.apple.iTunes/*",
        )

        if ctx.privileged.available:
            step.root_path("/Library/Application Support/Apple/Photos/Print Products/*")

    with ctx.step("Clearing on-device intelligence caches") as step:
        # These regenerate; they are behavioural models, not user data.
        step.path(
            "~/Library/Caches/com.apple.parsecd/*",
            "~/Library/Caches/com.apple.iCloudHelper/*",
            "~/Library/Caches/com.apple.Spotlight/*",
        )


@cleanup_module(
    name="ios_backups",
    risk=Risk.AGGRESSIVE,
    title="iOS device backups",
    requires=("~/Library/Application Support/MobileSync/Backup",),
    tags=("apps", "ios"),
)
def ios_backups(ctx: Context) -> None:
    """
    Local iPhone/iPad backups.

    Frequently tens of gigabytes. Reported rather than deleted by default: if iCloud
    backup is not enabled for a device, its local backup may be the only copy. Enable
    deletion with ``--only ios_backups`` once you have checked.
    """

    with ctx.step("Measuring iOS device backups") as step:
        step.measure("~/Library/Application Support/MobileSync/Backup/*")

    with ctx.step("Clearing iOS software update downloads") as step:
        step.path(
            "~/Library/iTunes/iPhone Software Updates/*.ipsw",
            "~/Library/iTunes/iPad Software Updates/*.ipsw",
            "~/Library/Caches/com.apple.iTunes/SoftwareUpdates/*",
        )
