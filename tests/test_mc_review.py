"""
Tests for the interactive review gate.

The property that matters most is the boring one: it must never prompt when there is no
terminal. A scheduled run that blocks on input does not fail loudly — it hangs until the
launchd timeout, having cleaned nothing, which is the worst of both outcomes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from rich.console import Console

from mc.review import Selection, review_selection

HOME = os.path.expanduser("~")


class ScriptedConsole(Console):
    """
    A console that discards output and answers prompts from a queue.

    The review loop reads with the builtin ``input()`` rather than ``Console.input`` — a
    stray Live display repaints over anything rendered through the console — so the queue
    is installed by patching ``builtins.input``, which keeps the real code path under test.
    """

    def __init__(self, answers):
        # Same theme as the real console: the renderer uses [info]/[success]/[danger]
        # markup, and a bare Console raises MissingStyle on all of them.
        from rich.theme import Theme

        super().__init__(
            file=open(os.devnull, "w"),
            force_terminal=False,
            width=100,
            theme=Theme(
                {"info": "cyan", "warning": "magenta", "danger": "bold red", "success": "bold green"}
            ),
        )
        self._answers = list(answers)
        self.asked = 0

    def install(self, monkeypatch):
        """Point builtins.input at this console's answer queue."""

        def fake_input(*_a, **_k):
            self.asked += 1
            if not self._answers:
                return "go"
            answer = self._answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr("builtins.input", fake_input)
        return self


def _details():
    """Two modules with distinct locations, shaped like a real estimate."""

    return {
        ("electron_apps", "rule-a"): [
            (Path(HOME) / "Library/Application Support/Ferdium/Cache/x", 900),
            (Path(HOME) / "Library/Application Support/Claude/Cache/x", 100),
        ],
        ("browsers", "rule-b"): [
            (Path(HOME) / "Library/Caches/Firefox/cache2/x", 500),
        ],
    }


# --------------------------------------------------------------------------------------
# Non-interactive safety
# --------------------------------------------------------------------------------------


def test_is_interactive_false_without_a_terminal(monkeypatch):
    """launchd gives no tty; the gate must know that."""

    from mc import review

    monkeypatch.setattr("sys.stdin", open(os.devnull))
    assert review.is_interactive() is False


def test_is_interactive_survives_closed_streams(monkeypatch):
    class Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    from mc import review

    monkeypatch.setattr("sys.stdin", Closed())
    assert review.is_interactive() is False


# --------------------------------------------------------------------------------------
# Selection semantics
# --------------------------------------------------------------------------------------


def test_accepting_immediately_excludes_nothing(monkeypatch):
    console = ScriptedConsole(["go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.is_empty
    assert not selection.cancelled


def test_toggling_a_module_excludes_it(monkeypatch):
    # electron_apps is listed first (1000 bytes vs 500).
    console = ScriptedConsole(["1", "go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.excluded_modules == {"electron_apps"}


def test_toggling_twice_restores_it(monkeypatch):
    console = ScriptedConsole(["1", "1", "go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.is_empty


def test_none_then_all_round_trips(monkeypatch):
    console = ScriptedConsole(["none", "all", "go"]).install(monkeypatch)

    assert review_selection(console, _details(), total=1500).is_empty


def test_none_excludes_every_module(monkeypatch):
    console = ScriptedConsole(["none", "go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.excluded_modules == {"electron_apps", "browsers"}


def test_cancelling_reports_cancelled(monkeypatch):
    console = ScriptedConsole(["q"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.cancelled


def test_ctrl_c_cancels_rather_than_proceeding(monkeypatch):
    console = ScriptedConsole([KeyboardInterrupt()]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.cancelled, "an interrupt must never be read as consent"


def test_eof_cancels_rather_than_proceeding(monkeypatch):
    """Ctrl-D at the prompt must not be read as approval either."""

    console = ScriptedConsole([EOFError()]).install(monkeypatch)

    assert review_selection(console, _details(), total=1500).cancelled


def test_garbage_input_is_ignored_not_obeyed(monkeypatch):
    console = ScriptedConsole(["banana", "99", "-1", "go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.is_empty


def test_drilling_in_excludes_one_location(monkeypatch):
    # d1 -> locations of electron_apps; 1 toggles the largest (Ferdium); back; go
    console = ScriptedConsole(["d1", "1", "back", "go"]).install(monkeypatch)

    selection = review_selection(console, _details(), total=1500)

    assert selection.excluded_modules == set()
    assert any("Ferdium" in p for p in selection.excluded_prefixes)


# --------------------------------------------------------------------------------------
# Prefix matching
# --------------------------------------------------------------------------------------


def test_excludes_path_matches_children_not_siblings(monkeypatch):
    selection = Selection(excluded_prefixes={f"{HOME}/Library/Application Support/Ferdium"})

    assert selection.excludes_path(f"{HOME}/Library/Application Support/Ferdium")
    assert selection.excludes_path(f"{HOME}/Library/Application Support/Ferdium/Cache/x")
    # A sibling whose name merely starts with the same characters must not match.
    assert not selection.excludes_path(f"{HOME}/Library/Application Support/FerdiumOther/x")
    assert not selection.excludes_path(f"{HOME}/Library/Application Support/Claude/Cache")


# --------------------------------------------------------------------------------------
# The runtime honours it
# --------------------------------------------------------------------------------------


def test_runtime_skips_deselected_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))

    from mac_cleanup.core_modules import Path as UpstreamPath
    from mac_cleanup.core_modules import set_current_module

    from mc.privileged import Privileged
    from mc.report import RunReport
    from mc.runtime import Runtime

    target = tmp_path / "Library/Caches/App/blob"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * 1024)

    runtime = Runtime(
        privileged=Privileged(enabled=False),
        batch=None,
        report=RunReport(),
        selection=Selection(excluded_modules={"doomed"}),
    )

    set_current_module("doomed")
    runtime.delete_path(UpstreamPath(str(tmp_path / "Library/Caches/App/*")))

    assert target.exists(), "a deselected module must not delete anything"


def test_runtime_skips_deselected_locations(tmp_path, monkeypatch):
    monkeypatch.setenv("MACCLEANER_HOME", str(tmp_path))

    from mac_cleanup.core_modules import Path as UpstreamPath
    from mac_cleanup.core_modules import set_current_module

    from mc.privileged import Privileged
    from mc.report import RunReport
    from mc.runtime import Runtime

    keep = tmp_path / "Library/Caches/Keep/blob"
    drop = tmp_path / "Library/Caches/Drop/blob"
    for path in (keep, drop):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * 1024)

    runtime = Runtime(
        privileged=Privileged(enabled=False),
        batch=None,
        report=RunReport(),
        selection=Selection(excluded_prefixes={str(tmp_path / "Library/Caches/Keep")}),
    )

    set_current_module("caches")
    runtime.delete_path(UpstreamPath(str(tmp_path / "Library/Caches/*")))

    assert keep.exists(), "the deselected location must survive"
    assert not drop.exists(), "everything else should still be cleaned"
