"""Windows 开机自启 + 单实例检查 + 快捷方式创建。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import config as cfg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------------
# 开机自启
# ---------------------------------------------------------------------------

def _launch_command() -> str:
    """开机时应该执行的命令。"""
    if getattr(sys, "frozen", False):        # 已打包成 exe
        return f'"{sys.executable}" {cfg.AUTOSTART_ARGS}'
    # 源码运行：用 pythonw.exe 静默启动，不留黑窗口
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else py
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{exe}" "{main}" {cfg.AUTOSTART_ARGS}'


def is_autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, cfg.AUTOSTART_REG_NAME)
            return bool(v)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(enabled: bool) -> bool:
    """写 / 删 HKCU\\...\\Run 里的启动项。成功返回 True。"""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, cfg.AUTOSTART_REG_NAME, 0, winreg.REG_SZ,
                                  _launch_command())
            else:
                try:
                    winreg.DeleteValue(k, cfg.AUTOSTART_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 单实例：用 Windows 命名互斥体，第二次启动就会把已开的窗口叫到前面
# ---------------------------------------------------------------------------

class SingleInstance:
    def __init__(self, name: str = "MemoReminder_SingleInstance"):
        self.name = name
        self.handle = None
        self.is_first = True

    def acquire(self) -> bool:
        if sys.platform != "win32":
            self.is_first = True
            return True
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, False, self.name)
            last_error = kernel32.GetLastError()
            self.handle = mutex
            if last_error == 183:      # ERROR_ALREADY_EXISTS
                self.is_first = False
                return False
            self.is_first = True
            return True
        except Exception:
            self.is_first = True
            return True

    def bring_existing_to_front(self) -> None:
        """找到已运行的窗口并激活它。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, cfg.APP_NAME)
            if hwnd:
                user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def release(self) -> None:
        try:
            if self.handle and sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.handle)
                self.handle = None
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 快捷方式（打包后用，需要 pywin32 或 Windows Script Host）
# ---------------------------------------------------------------------------

def _desktop_dir() -> Path:
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)  # CSIDL_DESKTOP
        return Path(buf.value)
    except Exception:
        return Path.home() / "Desktop"


def _start_menu_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_shortcut(target: str, link_path: Path, arguments: str = "",
                    workdir: str = "", icon: str = "",
                    description: str = cfg.APP_NAME) -> bool:
    """用 PowerShell 的 WScript.Shell 建 .lnk（不依赖额外 Python 包）。"""
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "$w = New-Object -ComObject WScript.Shell; "
            f"$s = $w.CreateShortcut('{link_path}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.Arguments = '{arguments}'; "
            f"$s.WorkingDirectory = '{workdir or str(Path(target).parent)}'; "
            f"$s.IconLocation = '{icon or target}'; "
            f"$s.Description = '{description}'; "
            "$s.Save()"
        )
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        return r.returncode == 0 and link_path.exists()
    except Exception:
        return False


def install_shortcuts() -> dict:
    """给打包好的 exe 建桌面 + 开始菜单快捷方式。返回结果字典。"""
    if not getattr(sys, "frozen", False):
        return {"ok": False, "reason": "源码运行模式不建快捷方式"}
    exe = sys.executable
    icon = str(cfg.ICON_PATH if cfg.ICON_PATH.exists() else exe)
    results = {}
    desktop = _desktop_dir() / f"{cfg.APP_NAME}.lnk"
    results["desktop"] = create_shortcut(exe, desktop, "", str(Path(exe).parent), icon)
    start = _start_menu_dir() / cfg.APP_NAME / f"{cfg.APP_NAME}.lnk"
    results["start_menu"] = create_shortcut(exe, start, "", str(Path(exe).parent), icon)
    results["ok"] = all(v for k, v in results.items() if isinstance(v, bool))
    return results


def remove_shortcuts() -> None:
    try:
        (_desktop_dir() / f"{cfg.APP_NAME}.lnk").unlink(missing_ok=True)
        (_start_menu_dir() / cfg.APP_NAME / f"{cfg.APP_NAME}.lnk").unlink(missing_ok=True)
    except Exception:
        pass
