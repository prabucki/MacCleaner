"""
Path safety policy — the single source of truth for what may and may not be deleted.

This module is deliberately **stdlib-only and Python 3.9 compatible**. It is imported
both by the main package (running under Homebrew Python) and by the root helper
``mc-root`` (running under ``/usr/bin/python3``, which is 3.9.6 on macOS 26 and is the
only interpreter on this system that is not writable by the unprivileged user).

Adding a third-party import here, or 3.10+ syntax, breaks the root helper.

Three questions are answered here:

* :func:`is_protected` — may this path be touched *at all*? (hard deny)
* :func:`is_soft_protected` — may this path be touched only with an explicit override?
* :func:`is_privileged_allowed` — may ``mc-root`` delete this path as root?

The privileged allowlist is re-evaluated *inside* ``mc-root`` rather than trusted from
the caller, so a compromised user-side process cannot talk the root helper into
deleting something outside the allowlist.
"""

from __future__ import annotations

import fnmatch
import os
import os.path
from typing import List, Optional, Tuple

__all__ = [
    "Risk",
    "RISK_ORDER",
    "Decision",
    "expand",
    "normalize",
    "is_protected",
    "is_soft_protected",
    "is_privileged_allowed",
    "check",
]


# --------------------------------------------------------------------------------------
# Risk tiers
# --------------------------------------------------------------------------------------


class Risk(object):
    """Risk tiers. Plain constants rather than :class:`enum.Enum` to keep TOML round-trips trivial."""

    SAFE = "safe"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"
    NUCLEAR = "nuclear"


#: Ordered least → most destructive. A profile runs every tier at or below its own level.
RISK_ORDER = (Risk.SAFE, Risk.STANDARD, Risk.AGGRESSIVE, Risk.NUCLEAR)


def risk_at_or_below(profile: str) -> Tuple[str, ...]:
    """Return every risk tier a given profile is permitted to execute."""

    if profile not in RISK_ORDER:
        raise ValueError("unknown profile %r (expected one of %s)" % (profile, ", ".join(RISK_ORDER)))

    return RISK_ORDER[: RISK_ORDER.index(profile) + 1]


# --------------------------------------------------------------------------------------
# Home resolution
# --------------------------------------------------------------------------------------


def _home() -> str:
    """
    Resolve the *user's* home directory.

    ``mc-root`` runs as root under sudo, where ``~`` would expand to ``/var/root``. The
    real target user is passed through ``SUDO_USER``/``MACCLEANER_HOME`` so the policy
    evaluates against the same paths on both sides of the privilege boundary.
    """

    override = os.environ.get("MACCLEANER_HOME")
    if override:
        return os.path.normpath(override)

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        return os.path.normpath(os.path.expanduser("~" + sudo_user))

    return os.path.normpath(os.path.expanduser("~"))


def _all_homes() -> Tuple[str, ...]:
    """
    Every directory that could plausibly be "the user's home" on this machine.

    Hard-protected patterns are matched against all of them, not just the one
    :func:`_home` picked. This is deliberate defence in depth: if ``MACCLEANER_HOME`` is
    ever wrong — misconfigured, spoofed, or left set by a test harness — the real user's
    Downloads, SSH keys and Keychains must still be untouchable. Without this, a single
    wrong environment variable disables every protection at once.
    """

    homes = [_home()]

    # The process's actual home, whatever the policy was told.
    real = os.path.normpath(os.path.expanduser("~"))
    if real not in homes:
        homes.append(real)

    # Under sudo, the invoking user's home rather than root's.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd

            sudo_home = os.path.normpath(pwd.getpwnam(sudo_user).pw_dir)
            if sudo_home not in homes:
                homes.append(sudo_home)
        except (ImportError, KeyError):
            pass

    return tuple(homes)


def expand(path: str) -> str:
    """Expand ``~`` and ``$VAR`` against the *target user's* home, without resolving symlinks."""

    home = _home()

    if path == "~":
        return home
    if path.startswith("~/"):
        path = home + path[1:]

    return os.path.expandvars(path)


#: Firmlinks/symlinks macOS resolves implicitly. Without canonicalising these, "/var/log"
#: and "/private/var/log" would be treated as different paths and only one of them would
#: hit the allowlist.
_CANONICAL_PREFIXES = (("/var/", "/private/var/"), ("/tmp/", "/private/tmp/"), ("/etc/", "/private/etc/"))


def normalize(path: str) -> str:
    """
    Expand and lexically normalise a path, collapsing ``..`` and ``.`` segments.

    Lexical (``normpath``) rather than physical (``realpath``) normalisation on purpose:
    the input may contain glob metacharacters that do not exist on disk yet. Symlink
    escape is handled separately, at deletion time, once globs have been expanded to
    concrete paths.
    """

    expanded = expand(path)

    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)

    normalised = os.path.normpath(expanded)

    # normpath leaves a leading "//" alone on POSIX; collapse it.
    while normalised.startswith("//"):
        normalised = normalised[1:]

    for alias, canonical in _CANONICAL_PREFIXES:
        if normalised == alias.rstrip("/"):
            return canonical.rstrip("/")
        if normalised.startswith(alias):
            return canonical + normalised[len(alias) :]

    return normalised


def _matches_any(path: str, patterns, *, all_homes: bool = False) -> Optional[str]:
    """
    Return the first pattern in ``patterns`` matching ``path``, or None.

    A pattern beginning with ``**/`` is an *anywhere* pattern — it matches at any depth,
    including directly under ``/``. Such patterns must not go through :func:`normalize`,
    which would resolve the leading ``**`` against the current working directory.

    :param all_homes: Expand ``~`` patterns against every plausible home rather than only
        the configured one. Used for hard protection — see :func:`_all_homes`.
    """

    homes = _all_homes() if all_homes else (None,)

    for pattern in patterns:
        if pattern.startswith("**/"):
            tail = pattern[3:]
            if fnmatch.fnmatchcase(path, "*/" + tail) or fnmatch.fnmatchcase(path, "/" + tail):
                return pattern
            continue

        for home in homes:
            if home is not None and pattern.startswith("~"):
                concrete = os.path.normpath(home + pattern[1:]) if pattern != "~" else home
            else:
                concrete = normalize(pattern)

            if path == concrete or fnmatch.fnmatchcase(path, concrete):
                return pattern

    return None


# --------------------------------------------------------------------------------------
# Hard protection — never deletable, by anyone, with any flag
# --------------------------------------------------------------------------------------

#: Directories that must continue to exist. Their *contents* may be cleanable (subject to
#: the allowlists below) but the directory itself is never a valid deletion target. This
#: is what stops a bug that produces an empty suffix from turning "~/Library/Caches/*"
#: into "/".
_NEVER_THE_TARGET = (
    "/",
    "/Applications",
    "/Library",
    "/System",
    "/Users",
    "/Volumes",
    "/bin",
    "/etc",
    "/opt",
    "/private",
    "/private/var",
    "/sbin",
    "/tmp",
    "/usr",
    "/var",
    "~",
    "~/Library",
    "~/Library/Application Support",
    "~/Library/Caches",
    "~/Library/Containers",
    "~/Library/Group Containers",
)

#: Irreplaceable user data and credentials. Hard deny, no override path exists.
_HARD_PROTECTED = (
    # Downloads is off-limits entirely, by explicit instruction. Deliberately hard rather
    # than soft protection: soft protection can be pierced with .override_protection(),
    # and the whole point is that no module can ever reach in here, not even by mistake.
    # mc-root enforces the same rule independently, so this holds for root too.
    "~/Downloads",
    "~/Downloads/**",
    # Credentials and secrets
    "~/.ssh",
    "~/.ssh/**",
    "~/.gnupg",
    "~/.gnupg/**",
    "~/.aws",
    "~/.aws/**",
    "~/.kube",
    "~/.kube/**",
    "~/.docker/config.json",
    "~/.netrc",
    "~/.pgpass",
    "~/Library/Keychains",
    "~/Library/Keychains/**",
    "/Library/Keychains",
    "/Library/Keychains/**",
    # Cloud-synced and irreplaceable containers
    "~/Library/Mobile Documents",
    "~/Library/Mobile Documents/**",
    "~/Library/CloudStorage",
    "~/Library/CloudStorage/**",
    # Messages and Mail stores (the *caches* under these are allowlisted individually)
    "~/Library/Messages",
    "~/Library/Messages/**",
    "~/Library/Mail/V*/[0-9A-F]*",
    # Photo / media / VM libraries, wherever they live
    "**/*.photoslibrary",
    "**/*.photoslibrary/**",
    "**/*.musiclibrary",
    "**/*.musiclibrary/**",
    "**/*.tvlibrary",
    "**/*.aplibrary",
    "**/*.fcpbundle",
    "**/*.pvm",
    "**/*.pvm/**",
    "**/*.utm",
    "**/*.utm/**",
    "**/*.vmwarevm",
    "**/*.vmwarevm/**",
    "**/*.sparsebundle",
    "**/*.sparseimage",
    "**/*.dmg.sparsebundle",
    # Archives OF virtual machines. A `.pvm` bundle is protected above, but
    # `Windows 11.pvm.zip` is a plain file whose name merely ends in `.zip`, and
    # nothing here matched it. That gap cost a real 11.75 GB archive during
    # development, so the suffix wildcard covers .pvm.zip / .pvm.tar.gz / .pvm.7z
    # and anything else someone compresses a VM into.
    "**/*.pvm.*",
    "**/*.utm.*",
    "**/*.vmwarevm.*",
    # The default VM storage directories themselves. Protecting only the bundle
    # extensions leaves everything else in these folders — snapshots, archives,
    # exports, notes — unprotected.
    "~/Parallels",
    "~/Parallels/**",
    "~/Virtual Machines.localized",
    "~/Virtual Machines.localized/**",
    "~/Library/Containers/com.utmapp.UTM/**",
    # Backups
    "**/Backups.backupdb",
    "**/Backups.backupdb/**",
    "**/*.backupbundle",
    # Local LLM weights — Jan.app alone holds ~11 GB here and a generic
    # "Application Support" sweep would happily eat all of it.
    "**/*.gguf",
    "**/*.safetensors",
    "**/*.ckpt",
    "**/*.pth",
    "**/*.onnx",
    "**/*.mlmodelc/**",
    "~/Library/Application Support/Jan",
    "~/Library/Application Support/Jan/**",
    "~/.ollama/models",
    "~/.ollama/models/**",
    "~/.cache/huggingface/hub",
    "~/.cache/huggingface/hub/**",
    "~/.cache/lm-studio/models/**",
    # Sync / backup tooling state — losing these forces a full re-index or re-upload
    "~/Library/Application Support/Syncthing",
    "~/Library/Application Support/Syncthing/**",
    "~/Library/Application Support/Carbon Copy Cloner",
    "~/Library/Application Support/Carbon Copy Cloner/**",
    "~/Library/Application Support/restic",
    "~/Library/Application Support/restic/**",
    # Version control — a stray recursive rule must never eat a repository
    "**/.git",
    "**/.git/**",
    # System integrity
    "/System/**",
    "/usr/bin/**",
    "/usr/lib/**",
    "/usr/sbin/**",
    "/bin/**",
    "/sbin/**",
    "/etc/**",
    "/private/etc/**",
    "/Library/LaunchDaemons/**",
    "/Library/LaunchAgents/**",
    "~/Library/LaunchAgents/**",
    "/opt/homebrew/**",
    "/usr/local/Cellar/**",
    "/nix/**",
)

#: Deletable, but only when a module explicitly calls ``.override_protection(reason)``.
#: Everything here is user data that has a narrow legitimate cleanup case — for example
#: stale installers in ``~/Downloads`` — but that must never be swept generically.
_SOFT_PROTECTED = (
    "~/Documents",
    "~/Documents/**",
    "~/Desktop",
    "~/Desktop/**",
    # NOTE: ~/Downloads is intentionally *not* here - it is hard-protected above.
    "~/Movies",
    "~/Movies/**",
    "~/Pictures",
    "~/Pictures/**",
    "~/Music",
    "~/Music/**",
    "~/Public",
    "~/Public/**",
    "~/Library/Preferences/**",
    "~/Library/Mail/**",
    "~/Library/Safari/**",
    "~/Library/Application Support/MobileSync/**",
    # Obsidian's vault registry and plugin settings. Scoped to the config files rather
    # than the whole directory, so the Electron cache subdirectories beside them stay
    # cleanable. (Vaults themselves live in user folders and are protected there.)
    "~/Library/Application Support/obsidian/obsidian.json",
    "~/Library/Application Support/obsidian/Preferences",
    "~/.zshrc",
    "~/.zshenv",
    "~/.bashrc",
    "~/.profile",
    "~/.gitconfig",
)


def is_protected(path: str) -> Optional[str]:
    """
    Hard-deny check.

    :param path: Path to test. May contain ``~`` and glob metacharacters.
    :return: Human-readable reason if the path must not be touched, else ``None``.
    """

    target = normalize(path)

    # A bare mount point or top-level directory is never itself a deletion target.
    if _matches_any(target, _NEVER_THE_TARGET, all_homes=True) is not None:
        return "%s is a structural directory and is never a deletion target" % target

    # Refuse anything that would delete the whole of another volume.
    if target.startswith("/Volumes/"):
        remainder = target[len("/Volumes/") :]
        # Only the trash of a mounted volume is fair game.
        if "/" not in remainder or ".Trashes" not in remainder:
            return "%s is on an external or network volume" % target

    # Checked against every plausible home, so a wrong MACCLEANER_HOME cannot expose the
    # real user's data.
    matched = _matches_any(target, _HARD_PROTECTED, all_homes=True)
    if matched is not None:
        return "%s matches protected pattern %s" % (target, matched)

    return None


def is_soft_protected(path: str) -> Optional[str]:
    """
    Override-required check.

    :param path: Path to test.
    :return: Reason string if the path needs an explicit override, else ``None``.
    """

    matched = _matches_any(normalize(path), _SOFT_PROTECTED)
    if matched is not None:
        return "%s matches soft-protected pattern %s (needs override_protection)" % (normalize(path), matched)

    return None


# --------------------------------------------------------------------------------------
# Privileged allowlist — what mc-root will delete as root
# --------------------------------------------------------------------------------------

#: Everything the root helper is willing to remove. Intentionally narrow: each entry
#: exists because a specific module needs it. Hard protection is still applied on top,
#: so an allowlist entry can never resurrect a protected path.
_PRIVILEGED_ALLOW = (
    # System caches
    "/Library/Caches/**",
    "/System/Library/Caches/**",
    "/private/var/folders/*/*/C/**",
    "/private/var/folders/*/*/T/**",
    "/private/var/db/coreduet/**",
    "/private/var/db/BootCache.playlist",
    # Temp directories. The modules that use these apply an age filter first; live
    # sockets and lock files belonging to running processes must survive a cleanup.
    "/private/tmp/**",
    "/private/var/tmp/**",
    "/private/var/vm/sleepimage",
    "/private/var/db/diagnostics/**",
    "/private/var/db/uuidtext/**",
    # System logs and crash reports
    "/Library/Logs/**",
    "/private/var/log/**",
    "/Library/Application Support/CrashReporter/**",
    # Shared installer / vendor leftovers
    "/Users/Shared/Adobe/**",
    "/Users/Shared/AdobeGCInfo/**",
    "/Users/Shared/Blizzard/**",
    "/Library/Updates/**",
    "/Library/Application Support/Adobe/Adobe Desktop Common/*Cache*/**",
    "/Library/Logs/Adobe/**",
    "/Library/Logs/CreativeCloud/**",
    # Illustrator's bundled sample AppleScripts register themselves as launchable
    # applications and clutter Spotlight and Launchpad. Beside the .app bundle, not
    # inside it, so removing them does not break the code signature.
    "/Applications/Adobe Illustrator */Scripting.localized/Sample Scripts.localized/**",
    # Root-owned strays inside the user's own Library
    "~/Library/Caches/**",
    "~/Library/Logs/**",
    "~/Library/Containers/*/Data/Library/Caches/**",
    "~/Library/Application Support/CrashReporter/**",
    "~/.Trash/**",
    "/Volumes/*/.Trashes/**",
)


def is_privileged_allowed(path: str) -> Optional[str]:
    """
    Decide whether ``mc-root`` may delete ``path`` as root.

    :param path: Path to test.
    :return: Reason string if the path is *not* allowed, else ``None``.
    """

    protection = is_protected(path)
    if protection is not None:
        return protection

    target = normalize(path)

    if _matches_any(target, _PRIVILEGED_ALLOW) is None:
        return "%s is not on the privileged allowlist" % target

    return None


# --------------------------------------------------------------------------------------
# Combined decision
# --------------------------------------------------------------------------------------


class Decision(object):
    """Outcome of a policy check."""

    __slots__ = ("allowed", "reason", "path")

    def __init__(self, allowed: bool, path: str, reason: Optional[str] = None):
        self.allowed = allowed
        self.path = path
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        verdict = "allow" if self.allowed else "deny"
        return "<Decision %s %s%s>" % (verdict, self.path, "" if self.allowed else ": " + str(self.reason))


def check(path: str, privileged: bool = False, override: bool = False) -> Decision:
    """
    Full policy evaluation for a single path.

    :param path: Candidate path, possibly containing ``~`` or globs.
    :param privileged: True when the deletion will be performed by ``mc-root``.
    :param override: True when the module explicitly opted past soft protection.
    :return: A :class:`Decision`.
    """

    target = normalize(path)

    reason = is_protected(target)
    if reason is not None:
        return Decision(False, target, reason)

    if not override:
        reason = is_soft_protected(target)
        if reason is not None:
            return Decision(False, target, reason)

    if privileged:
        reason = is_privileged_allowed(target)
        if reason is not None:
            return Decision(False, target, reason)

    return Decision(True, target)


def resolve_escapes(concrete_path: str) -> Optional[str]:
    """
    Post-glob symlink-escape check for a path that exists on disk.

    Globs are expanded to concrete paths before deletion; this verifies that a symlinked
    *directory component* does not place the real target outside the policy. Called
    immediately before deletion, by both the runtime and ``mc-root``.

    Only the parent is resolved, not the path itself. Deletion never follows a final
    symlink — it unlinks it — so a link whose target is protected is harmless to remove,
    and resolving it would produce false refusals for the many caches that are symlinks.
    A symlinked *parent*, by contrast, genuinely relocates the deletion.

    :param concrete_path: An existing, glob-free path.
    :return: Reason string if the effective location escapes policy, else ``None``.
    """

    lexical = normalize(concrete_path)

    real_parent = os.path.realpath(os.path.dirname(lexical))
    lexical_parent = os.path.dirname(lexical)

    if real_parent == lexical_parent:
        return None

    effective = os.path.join(real_parent, os.path.basename(lexical))

    for check_fn, label in ((is_protected, "protected"), (is_soft_protected, "soft-protected")):
        reason = check_fn(effective)
        if reason is not None:
            return "%s resolves through a symlink to %s which is %s (%s)" % (
                concrete_path,
                effective,
                label,
                reason,
            )

    return None


def describe() -> List[str]:  # pragma: no cover - used by `mc --explain-policy`
    """Render the active policy as readable lines, for auditing."""

    lines = ["HARD PROTECTED (never deletable):"]
    lines += ["    " + p for p in _HARD_PROTECTED]
    lines += ["", "STRUCTURAL (never a deletion target):"]
    lines += ["    " + p for p in _NEVER_THE_TARGET]
    lines += ["", "SOFT PROTECTED (needs override_protection):"]
    lines += ["    " + p for p in _SOFT_PROTECTED]
    lines += ["", "PRIVILEGED ALLOWLIST (mc-root may delete):"]
    lines += ["    " + p for p in _PRIVILEGED_ALLOW]

    return lines
