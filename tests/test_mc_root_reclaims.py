"""Root-helper verbs report an exit code, not bytes; `reclaims` credits what they free.

Before this, `log erase` freed 1.35 GB on 2026-10-29 and the report said 0 B.
"""

from __future__ import annotations

from types import SimpleNamespace

from mc.registry import Context
from mc.report import RunReport


class _Unit:
    def __init__(self):
        self.modules = []

    def message(self, _text):
        pass

    def add(self, module):
        self.modules.append(module)


class _Collector:
    """Stands in for the upstream collector: `with` yields a unit to add work to."""

    def __init__(self):
        self.unit = _Unit()

    def __enter__(self):
        return self.unit

    def __exit__(self, *_exc):
        return False


def test_root_verb_credits_the_space_it_freed(tmp_path):
    store = tmp_path / "diagnostics"
    store.mkdir()
    (store / "big.tracev3").write_bytes(b"x" * 65536)

    def erase():
        (store / "big.tracev3").unlink()
        return SimpleNamespace(ok=True, error=None)

    privileged = SimpleNamespace(available=True, unavailable_reason="", erase_unified_log=erase)
    report = RunReport()
    collector = _Collector()
    ctx = Context(name="unified_log", collector=collector, report=report, privileged=privileged, profile="aggressive")

    with ctx.step("erase") as step:
        step.root("erase_unified_log", reclaims=(str(store),))
    collector.unit.modules[-1]._execute()  # noqa: SLF001 - run the queued Action

    assert report.module("unified_log").bytes_reclaimed >= 65536
