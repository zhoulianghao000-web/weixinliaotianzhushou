"""把侧边栏窗口贴到微信窗口右侧，并做成"不抢焦点"的浮窗。

三个关键点：
1. WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW：点击侧边栏不会把键盘焦点从微信抢走，
   否则 wxauto 的 SendKeys / Ctrl+V 会打到侧边栏上
2. HWND_TOPMOST + SWP_NOACTIVATE：置顶但不激活
3. wxauto 每次操作都会把微信强制置顶并盖住侧边栏，所以循环里持续把自己抬回 TOPMOST
"""
from __future__ import annotations

import threading
import time
import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui

DEFAULT_TARGET_CLASS = 'WeChatMainWndForPC'
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def find_target_hwnd(target_class: str = '', target_title: str = '') -> int:
    """找贴边跟随的目标 IM 窗口：先按类名精确匹配，再按窗口标题匹配。

    target_class / target_title 由 config.TARGET_* 传入，
    这样换目标 IM（微信 3.9 / 4.x / QQ NT）只改环境变量，不改代码。
    """
    if target_class:
        h = win32gui.FindWindow(target_class, None)
        if h:
            return h
    if target_title:
        return find_hwnd_by_title(target_title)
    return 0


def find_hwnd_by_title(title: str) -> int:
    """先精确匹配，失败再按标题包含匹配（不同壳的窗口类名不一致）。"""
    hwnd = win32gui.FindWindow(None, title)
    if hwnd:
        return hwnd
    found = 0

    def _enum(h, _):
        nonlocal found
        if not found and title.lower() in (win32gui.GetWindowText(h) or '').lower():
            found = h

    win32gui.EnumWindows(_enum, None)
    return found


def make_no_activate(hwnd: int, on: bool = True) -> None:
    """加（默认）/ 去 WS_EX_NOACTIVATE，并统一加 WS_EX_TOOLWINDOW、去掉任务栏按钮。

    微信 wxauto 模式必须 on：点击侧边栏不能把焦点从微信输入框抢走，否则 SendKeys 打到自己身上。
    QQ 半自动模式必须 off：用户要在侧边栏里填"补充要求"、改生成的回复，
    带 NOACTIVATE 的窗口拿不到键盘焦点，输入框会点不进去（实测现象）。
    """
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if on:
        style |= win32con.WS_EX_NOACTIVATE
    else:
        style &= ~win32con.WS_EX_NOACTIVATE
    style |= win32con.WS_EX_TOOLWINDOW
    style &= ~win32con.WS_EX_APPWINDOW
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
    win32gui.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE
        | win32con.SWP_FRAMECHANGED,
    )


def clamp_to_screen(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    vx = win32api.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = win32api.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = win32api.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = win32api.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if x + w > vx + vw:
        x = max(vx, vx + vw - w)
    if x < vx:
        x = vx
    if y + h > vy + vh:
        y = max(vy, vy + vh - h)
    if y < vy:
        y = vy
    return x, y


def visible_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return the painted frame bounds, excluding Windows' invisible resize border."""
    try:
        rect = wintypes.RECT()
        # DWMWA_EXTENDED_FRAME_BOUNDS = 9. GetWindowRect includes invisible
        # DWM resize margins, which look like a gap when two windows are docked.
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if result == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return win32gui.GetWindowRect(hwnd)


class Docking:
    """后台线程：每 120ms 跟随一次微信窗口。"""

    def __init__(
        self,
        sidebar_title: str,
        width: int = 380,
        target_class: str = DEFAULT_TARGET_CLASS,
        target_title: str = '',
        no_activate: bool = True,
    ) -> None:
        self.sidebar_title = sidebar_title
        self.width = width
        self.target_class = target_class
        self.target_title = target_title
        self.no_activate = no_activate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sidebar_hwnd: int = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        last_z = 0
        while not self._stop.is_set():
            try:
                wx_hwnd = find_target_hwnd(self.target_class, self.target_title)
                if not wx_hwnd:
                    if self.sidebar_hwnd:
                        win32gui.ShowWindow(self.sidebar_hwnd, win32con.SW_HIDE)
                    time.sleep(0.5)
                    continue

                if not self.sidebar_hwnd or not win32gui.IsWindow(self.sidebar_hwnd):
                    self.sidebar_hwnd = find_hwnd_by_title(self.sidebar_title)
                    if not self.sidebar_hwnd:
                        time.sleep(0.3)
                        continue
                    make_no_activate(self.sidebar_hwnd, self.no_activate)

                left, top, right, bottom = visible_window_rect(wx_hwnd)
                w, h = right - left, bottom - top
                if w <= 0 or h <= 0 or win32gui.IsIconic(wx_hwnd):
                    win32gui.ShowWindow(self.sidebar_hwnd, win32con.SW_HIDE)
                    time.sleep(0.3)
                    continue

                x, y = clamp_to_screen(right, top, self.width, h)
                win32gui.ShowWindow(self.sidebar_hwnd, win32con.SW_SHOWNOACTIVATE)

                # 跟随位置/尺寸
                win32gui.SetWindowPos(
                    self.sidebar_hwnd,
                    win32con.HWND_TOPMOST,
                    x,
                    y,
                    self.width,
                    h,
                    win32con.SWP_NOACTIVATE | win32con.SWP_NOOWNERZORDER,
                )
                # 微信被 wxauto 强制置顶后会把侧边栏盖住，这里持续补一次 Z 序
                fg = win32gui.GetForegroundWindow()
                if fg != last_z:
                    last_z = fg
                    win32gui.SetWindowPos(
                        self.sidebar_hwnd,
                        win32con.HWND_TOPMOST,
                        0,
                        0,
                        0,
                        0,
                        win32con.SWP_NOMOVE
                        | win32con.SWP_NOSIZE
                        | win32con.SWP_NOACTIVATE,
                    )
                self.last_error = None
            except Exception as e:  # 不因一次异常中断贴边
                self.last_error = f'{type(e).__name__}: {e}'
            time.sleep(0.12)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
