"""
Tests for the launchd plist loader behind broken_login_items.

launchd and plutil accept a ``--`` inside an XML comment; plistlib does not, and raises
ExpatError — which is not a ValueError, so a single such file aborted the whole scan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mc.modules.leftovers import _load_launchd_plist

BODY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- {comment} -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.agent</string>
</dict>
</plist>
"""


def test_strict_xml_loads(tmp_path: Path) -> None:
    path = tmp_path / "ok.plist"
    path.write_text(BODY.format(comment="plain comment"))

    assert _load_launchd_plist(path)["Label"] == "com.example.agent"


def test_double_dash_in_comment_falls_back_to_plutil(tmp_path: Path) -> None:
    path = tmp_path / "dash.plist"
    path.write_text(BODY.format(comment="runs `tool --flag`"))

    assert _load_launchd_plist(path)["Label"] == "com.example.agent"


def test_garbage_still_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.plist"
    path.write_text("<plist><dict><key>unterminated")

    with pytest.raises(Exception):
        _load_launchd_plist(path)
