from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


APP_NAME = "Novel Agent Studio"
APP_FOLDER = "NovelAgentStudioV2"
VERSION = "2.2.6"
HOST = "127.0.0.1"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="NovelAgentStudio")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--gui-smoke-test-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    return parser.parse_args()


def program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle).resolve() if isinstance(bundle, str) else program_dir()


def resolve_data_dir(override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    root = program_dir()
    if (root / "portable.flag").is_file():
        return root / "data"
    if not getattr(sys, "frozen", False):
        return root / "backend" / "data-v2"
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return local / APP_FOLDER / "data"


def configure_environment(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    database = data_dir / "studio-v2.db"
    os.environ["NAS_APP_VERSION"] = VERSION
    os.environ["NAS_ENV"] = "production"
    os.environ["NAS_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    os.environ["NAS_LOG_DIR"] = str(logs)
    os.environ["NAS_ALLOWED_HOSTS"] = "127.0.0.1,localhost"
    os.environ["NAS_CORS_ORIGINS"] = ""
    os.environ["NAS_FRONTEND_DIST"] = str(resource_dir() / "frontend-dist")
    if not getattr(sys, "frozen", False):
        backend = program_dir() / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))


def choose_port(requested: int) -> int:
    if requested:
        if not 1 <= requested <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间")
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def start_server(port: int) -> tuple[Any, threading.Thread]:
    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,
        server_header=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="nas-local-service", daemon=True)
    thread.start()
    return server, thread


def wait_for_ready(url: str, thread: threading.Thread, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "服务尚未响应"
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("本地服务在启动阶段意外退出")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise TimeoutError(f"本地服务启动超时：{last_error}")


def smoke_test(url: str) -> None:
    with urllib.request.urlopen(url, timeout=5.0) as response:
        index = response.read()
    if b'<div id="root"></div>' not in index:
        raise RuntimeError("生产前端未正确内置")
    with urllib.request.urlopen(f"{url}/api/release/status", timeout=5.0) as response:
        status = json.loads(response.read().decode("utf-8"))
    if status.get("app_version") != VERSION or not status.get("frontend_bundled"):
        raise RuntimeError("发布状态与桌面包不一致")
    if status.get("database_integrity") != "ok":
        raise RuntimeError("桌面数据库完整性检查失败")


def instance_handles(data_dir: Path) -> tuple[int | None, int | None, bool]:
    if os.name != "nt":
        return None, None, False
    digest = hashlib.sha256(str(data_dir).casefold().encode("utf-8")).hexdigest()[:20]
    kernel32 = ctypes.windll.kernel32
    mutex = int(kernel32.CreateMutexW(None, False, f"Local\\NAS2-{digest}"))
    already_running = int(kernel32.GetLastError()) == ERROR_ALREADY_EXISTS
    reopen_event = int(kernel32.CreateEventW(None, True, False, f"Local\\NAS2-Reopen-{digest}"))
    return mutex, reopen_event, already_running


def close_handle(handle: int | None) -> None:
    if handle and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(handle)


def signal_existing_instance(event: int | None) -> None:
    if event and os.name == "nt":
        ctypes.windll.kernel32.SetEvent(event)


def prompt_close_choice() -> tuple[str, bool]:
    """Return tray, exit, or cancel plus whether the choice should be remembered."""
    if os.name != "nt":
        return "exit", False
    try:
        import clr

        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from System.Drawing import Point, Size
        from System.Windows.Forms import (
            Button,
            CheckBox,
            DialogResult,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Label,
        )

        form = Form()
        form.Text = "关闭 Novel Agent Studio"
        form.ClientSize = Size(470, 188)
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.StartPosition = FormStartPosition.CenterScreen
        form.MaximizeBox = False
        form.MinimizeBox = False
        label = Label()
        label.Text = "创作任务仍可在后台继续。请选择关闭窗口后的操作："
        label.Location = Point(22, 22)
        label.Size = Size(425, 38)
        remember = CheckBox()
        remember.Text = "记住我的选择"
        remember.Location = Point(22, 78)
        remember.Size = Size(180, 26)
        tray_button = Button()
        tray_button.Text = "转入托盘继续"
        tray_button.Location = Point(185, 130)
        tray_button.Size = Size(112, 34)
        tray_button.DialogResult = DialogResult.Yes
        exit_button = Button()
        exit_button.Text = "停止并退出"
        exit_button.Location = Point(305, 130)
        exit_button.Size = Size(95, 34)
        exit_button.DialogResult = DialogResult.No
        cancel_button = Button()
        cancel_button.Text = "取消"
        cancel_button.Location = Point(408, 130)
        cancel_button.Size = Size(54, 34)
        cancel_button.DialogResult = DialogResult.Cancel
        form.Controls.Add(label)
        form.Controls.Add(remember)
        form.Controls.Add(tray_button)
        form.Controls.Add(exit_button)
        form.Controls.Add(cancel_button)
        form.AcceptButton = tray_button
        form.CancelButton = cancel_button
        result = form.ShowDialog()
        choice = "tray" if result == DialogResult.Yes else "exit" if result == DialogResult.No else "cancel"
        return choice, bool(remember.Checked)
    except Exception:
        result = int(
            ctypes.windll.user32.MessageBoxW(
                None,
                "选择“是”转入托盘继续，选择“否”停止并退出。",
                "关闭 Novel Agent Studio",
                0x23,
            )
        )
        return ("tray" if result == 6 else "exit" if result == 7 else "cancel"), False


class DesktopController:
    def __init__(self, data_dir: Path, url: str, reopen_event: int | None, gui_smoke_seconds: float) -> None:
        self.data_dir = data_dir
        self.url = url
        self.reopen_event = reopen_event
        self.gui_smoke_seconds = gui_smoke_seconds
        self.window: Any | None = None
        self.tray: Any | None = None
        self.force_exit = False
        self.stop_event = threading.Event()
        self.settings_path = data_dir / "desktop-settings.json"

    def remembered_behavior(self) -> str:
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8")).get("close_behavior")
            return value if value in {"ask", "tray", "exit"} else "ask"
        except (OSError, ValueError):
            return "ask"

    def save_behavior(self, behavior: str) -> None:
        self.settings_path.write_text(
            json.dumps({"close_behavior": behavior}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reset_close_behavior(self, _icon: Any = None, _item: Any = None) -> None:
        self.save_behavior("ask")

    def open_window(self, _icon: Any = None, _item: Any = None) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()

    def exit_app(self, _icon: Any = None, _item: Any = None) -> None:
        self.force_exit = True
        self.stop_event.set()
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    def on_closing(self) -> bool:
        if self.force_exit:
            return True
        behavior = self.remembered_behavior()
        remember = behavior != "ask"
        if behavior == "ask":
            behavior, remember = prompt_close_choice()
        if behavior == "cancel":
            return False
        if remember:
            self.save_behavior(behavior)
        if behavior == "tray":
            if self.window is not None:
                self.window.hide()
            return False
        self.force_exit = True
        self.stop_event.set()
        if self.tray is not None:
            self.tray.stop()
        return True

    def start_tray(self) -> None:
        if self.gui_smoke_seconds > 0:
            return
        from PIL import Image, ImageDraw
        import pystray

        image = Image.new("RGBA", (64, 64), (28, 34, 36, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=9, fill=(224, 228, 225, 255))
        draw.line((19, 46, 19, 19, 45, 46, 45, 19), fill=(34, 39, 37, 255), width=6)
        menu = pystray.Menu(
            pystray.MenuItem("打开 Novel Agent Studio", self.open_window, default=True),
            pystray.MenuItem("下次关闭时询问", self.reset_close_behavior),
            pystray.MenuItem("退出", self.exit_app),
        )
        self.tray = pystray.Icon("NovelAgentStudio", image, APP_NAME, menu)
        self.tray.run_detached()

    def watch_reopen_event(self) -> None:
        if not self.reopen_event or os.name != "nt":
            return
        kernel32 = ctypes.windll.kernel32
        while not self.stop_event.is_set():
            if int(kernel32.WaitForSingleObject(self.reopen_event, 500)) == WAIT_OBJECT_0:
                kernel32.ResetEvent(self.reopen_event)
                self.open_window()

    def run_gui_smoke(self) -> None:
        if self.gui_smoke_seconds <= 0:
            return
        time.sleep(self.gui_smoke_seconds)
        smoke_test(self.url)
        print(f"{APP_NAME} {VERSION} GUI smoke test passed")
        self.exit_app()

    def after_start(self) -> None:
        self.start_tray()
        threading.Thread(target=self.watch_reopen_event, name="nas-reopen", daemon=True).start()
        if self.gui_smoke_seconds > 0:
            threading.Thread(target=self.run_gui_smoke, name="nas-gui-smoke", daemon=True).start()


def show_error(message: str, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "launch-error.log").write_text(message, encoding="utf-8")
    if os.name == "nt" and not sys.stdout:
        ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} 启动失败", 0x10)
    else:
        print(message, file=sys.stderr)


def run_desktop(url: str, data_dir: Path, reopen_event: int | None, gui_smoke_seconds: float) -> None:
    import webview

    controller = DesktopController(data_dir, url, reopen_event, gui_smoke_seconds)
    window = webview.create_window(
        APP_NAME,
        url=url,
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#151616",
        text_select=True,
        zoomable=False,
    )
    if window is None:
        raise RuntimeError("无法创建桌面窗口")
    controller.window = window
    window.events.closing += controller.on_closing
    webview.start(
        controller.after_start,
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(data_dir / "webview-profile"),
    )
    controller.stop_event.set()
    if controller.tray is not None:
        controller.tray.stop()


def main() -> int:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    configure_environment(data_dir)
    mutex: int | None = None
    reopen_event: int | None = None
    server: Any | None = None
    thread: threading.Thread | None = None
    try:
        if not args.smoke_test and not args.server_only:
            mutex, reopen_event, already_running = instance_handles(data_dir)
            if already_running:
                signal_existing_instance(reopen_event)
                return 0
        port = choose_port(args.port)
        url = f"http://{HOST}:{port}"
        server, thread = start_server(port)
        wait_for_ready(url, thread)
        if args.smoke_test:
            smoke_test(url)
            print(f"{APP_NAME} {VERSION} smoke test passed")
            return 0
        if args.server_only:
            while thread.is_alive():
                time.sleep(0.25)
            return 0
        run_desktop(url, data_dir, reopen_event, args.gui_smoke_test_seconds)
        return 0
    except Exception as exc:
        show_error(f"{type(exc).__name__}: {exc}", data_dir)
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10.0)
        close_handle(reopen_event)
        close_handle(mutex)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
