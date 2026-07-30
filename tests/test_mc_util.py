"""
Tests for subprocess handling.

Children are started with ``start_new_session=True`` so a timeout can kill the whole
process tree. That detaches them from the terminal's foreground process group, which
means Ctrl-C does *not* reach them — an interrupt would kill mc and leave the child
running, still writing to the terminal. These pin the handling that makes an interrupt
actually stop things.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

import pytest

from mc.util import run, terminate_group


def _interrupt_self_after(delay: float) -> None:
    """Deliver SIGINT to this process, as the terminal does on Ctrl-C."""

    def fire():
        time.sleep(delay)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=fire, daemon=True).start()


def test_interrupt_kills_the_child_and_propagates(tmp_path):
    """
    Ctrl-C must stop the child, not just mc.

    The child here explicitly ignores SIGINT (`trap '' INT`), matching a tool that
    installs its own handler, so this also proves the escalation to SIGKILL works.
    """

    marker = tmp_path / "still-running"
    marker.write_text("x")

    _interrupt_self_after(1.0)

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run(["/bin/sh", "-c", f"trap '' INT; sleep 30; rm -f {marker}"], timeout=60)

    assert time.monotonic() - started < 15, "the interrupt should not wait for the timeout"

    # If the child had survived it would still be sleeping, then delete the marker.
    time.sleep(1.5)
    assert marker.exists(), "the child kept running after the interrupt"


def test_timeout_kills_the_process_group():
    started = time.monotonic()
    result = run(["/bin/sh", "-c", "sleep 30"], timeout=2)

    assert time.monotonic() - started < 12
    assert "timed out" in result.stderr


def test_terminate_group_is_safe_on_an_already_dead_process():
    process = subprocess.Popen(["/bin/sh", "-c", "true"], start_new_session=True)
    process.wait()

    terminate_group(process)  # must not raise


def test_normal_command_still_captures_output():
    result = run(["/bin/echo", "hello"], timeout=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_stream_mode_does_not_capture(capfd):
    """Streaming hands stdio to the child so long steps show progress live."""

    result = run(["/bin/echo", "streamed"], timeout=10, stream=True)

    assert result.returncode == 0
    assert result.stdout == "", "stream mode should not capture"
    assert "streamed" in capfd.readouterr().out


def test_env_additions_reach_the_child():
    result = run(["/bin/sh", "-c", "echo $MC_TEST_VAR"], timeout=10, env={"MC_TEST_VAR": "present"})

    assert result.stdout.strip() == "present"
