"""
Tests for app_leftovers' "is this still installed" check.

On 2026-10-29 it staged the group containers of three installed apps, and the quarantine
was emptied before anyone noticed. Each case below is one of those names, next to the
bundle IDs _installed_bundle_ids() reported for the machine at the time.
"""

from __future__ import annotations

import pytest

from mc.modules.leftovers import _is_kept

INSTALLED = {
    "com.google.drivefs",
    "com.google",
    "com.mysticalbits.lookaway",
    "com.mysticalbits",
    "com.adobe.Acrobat",
    "com.adobe",
}


@pytest.mark.parametrize(
    "name",
    [
        "EQHXZ8M8AV.group.com.google.drivefs",  # team ID AND group. prefix
        "D35B5Y6JX8.com.mysticalbits.lookaway.shared",  # suffix on a stripped name
        "JQ525L2MZD.com.adobe.CreativeCloudDesktop",  # sibling of an installed vendor app
        "243LU875E5.groups.com.apple.podcasts",
        "group.com.google.drivefs",
        "com.google.drivefs",
    ],
)
def test_installed_apps_are_kept(name: str) -> None:
    assert _is_kept(name, INSTALLED)


@pytest.mark.parametrize(
    "name",
    [
        "D43XN356JM.com.charliemonroe.Permute-3",
        "com.crashlytics.data",
        "ABCDE12345.group.com.example.gone",
    ],
)
def test_uninstalled_apps_are_orphans(name: str) -> None:
    assert not _is_kept(name, INSTALLED)
