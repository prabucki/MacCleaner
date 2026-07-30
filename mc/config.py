"""
Optional user configuration at ``~/.maccleaner/config.toml``.

Risk tiers already act as profiles — ``--profile standard`` runs everything at or below
the standard tier — so this file is not for defining profiles. It is for making your
usual flags stick, which matters most for the scheduled run, where there is no command
line to read.

Everything is optional; the file need not exist. Command-line flags always win.

Example::

    profile = "aggressive"
    skip = ["kext_cache", "spotlight_index"]
    retention_days = 14
    os_updates = false
    min_free_gb = 10
    notify = true
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from mc.util import MC_HOME

__all__ = ["UserConfig", "load_config", "CONFIG_PATH"]

CONFIG_PATH = MC_HOME / "config.toml"


@dataclass
class UserConfig:
    """Defaults read from the config file."""

    profile: str = "aggressive"
    skip: List[str] = field(default_factory=list)
    only: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    retention_days: int = 7
    os_updates: bool = False
    min_free_gb: int = 5
    notify: bool = True
    quarantine: bool = True
    snapshot: bool = False

    @property
    def min_free_bytes(self) -> int:
        return self.min_free_gb * 1024**3


def load_config(path: Path = CONFIG_PATH) -> UserConfig:
    """
    Read the config file, ignoring anything malformed.

    A broken config must not stop a scheduled cleanup — unknown keys and bad types are
    dropped and the defaults stand.
    """

    config = UserConfig()

    if not path.is_file():
        return config

    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return config

    try:
        with path.open("rb") as handle:
            data: Dict[str, Any] = tomllib.load(handle)
    except (OSError, ValueError):
        return config

    for key, current in vars(config).items():
        if key not in data:
            continue

        value = data[key]

        # Only accept a value whose type matches the default's.
        if isinstance(current, bool) and isinstance(value, bool):
            setattr(config, key, value)
        elif isinstance(current, int) and not isinstance(current, bool) and isinstance(value, int):
            setattr(config, key, value)
        elif isinstance(current, list) and isinstance(value, list):
            setattr(config, key, [str(item) for item in value])
        elif isinstance(current, str) and isinstance(value, str):
            setattr(config, key, value)

    return config
