from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop import launcher  # noqa: E402


class FakeWindow:
    def __init__(self) -> None:
        self.hidden = False
        self.destroyed = False
        self.shown = False

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.shown = True

    def restore(self) -> None:
        self.shown = True

    def destroy(self) -> None:
        self.destroyed = True


def test_random_port_remains_reserved_until_server_uses_socket() -> None:
    listener = launcher.reserve_listener(0)
    port = int(listener.getsockname()[1])
    competitor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            competitor.bind((launcher.HOST, port))
    finally:
        competitor.close()
        listener.close()

    reusable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reusable.bind((launcher.HOST, port))
    finally:
        reusable.close()


def test_requested_port_is_validated_before_binding() -> None:
    with pytest.raises(ValueError):
        launcher.reserve_listener(65_536)


def test_non_loopback_listener_is_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        launcher.reserve_listener(0, "0.0.0.0")


def test_portable_data_stays_beside_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "portable.flag").touch()
    monkeypatch.setattr(launcher, "program_dir", lambda: tmp_path)

    assert launcher.resolve_data_dir(None) == tmp_path / "data"


def test_remembered_tray_close_hides_window(tmp_path: Path) -> None:
    controller = launcher.DesktopController(tmp_path, "http://127.0.0.1:1", None, 0)
    window = FakeWindow()
    controller.window = window
    controller.save_behavior("tray")

    assert controller.on_closing() is False
    assert window.hidden is True
    assert json.loads(controller.settings_path.read_text(encoding="utf-8"))["close_behavior"] == "tray"


def test_remembered_exit_allows_window_to_close(tmp_path: Path) -> None:
    controller = launcher.DesktopController(tmp_path, "http://127.0.0.1:1", None, 0)
    controller.window = FakeWindow()
    controller.save_behavior("exit")

    assert controller.on_closing() is True
    assert controller.force_exit is True
    assert controller.stop_event.is_set()


def test_second_launch_signals_existing_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr("desktop.launcher.os.name", "nt")

    class Kernel32:
        def SetEvent(self, handle: int) -> None:
            calls.append(handle)

    monkeypatch.setattr(
        "desktop.launcher.ctypes.windll",
        type("Windll", (), {"kernel32": Kernel32()})(),
        raising=False,
    )
    launcher.signal_existing_instance(42)

    assert calls == [42]


def test_desktop_url_contains_no_api_token() -> None:
    token = "super-secret-local-token"

    url = launcher.desktop_document_url("http://127.0.0.1:8123")

    assert token not in url
    assert "nas-token" not in url
    assert url == "http://127.0.0.1:8123#nas-desktop=1"


def test_desktop_bridge_releases_token_only_once() -> None:
    bridge = launcher.DesktopApiBridge("one-time-token")

    assert bridge.consume_local_api_token() == "one-time-token"
    assert bridge.consume_local_api_token() == ""
