"""WeChat 4.x adapter using local screenshot OCR and clipboard-based fill/send.

Only left-side neutral incoming bubbles from the chat viewport are returned.
The OCR runs locally; this module never sends screenshots to a remote service.
"""
from __future__ import annotations

import hashlib
import re
import time
from difflib import SequenceMatcher

import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab

from .base import BaseAdapter
from .qq_adapter import VK_CONTROL, VK_RETURN, VK_V, _tap, _ctrl_combo, set_clipboard_text

WECHAT_EXE = 'weixin.exe'
_OCR = None
_TIME_RE = re.compile(r'^\d{1,2}\s*[:：]\s*\d{2}$')


def _process_image_name(pid: int) -> str:
    try:
        access = win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
        process = win32api.OpenProcess(access, False, pid)
        try:
            return win32process.GetModuleFileNameEx(process, 0).rsplit('\\', 1)[-1].lower()
        finally:
            win32api.CloseHandle(process)
    except Exception:
        return ''


def find_wechat_hwnd() -> int:
    """Find the main Weixin.exe window, with a title fallback for restricted systems."""
    found = 0
    fallback = 0

    def visit(hwnd, _):
        nonlocal found, fallback
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd) or ''
            normalized_title = title.casefold().strip()
            cls = win32gui.GetClassName(hwnd) or ''
            if _process_image_name(pid) == WECHAT_EXE and win32gui.GetParent(hwnd) == 0:
                # Skip splash/updater windows with no useful client area.
                rect = win32gui.GetWindowRect(hwnd)
                if rect[2] - rect[0] > 300 and rect[3] - rect[1] > 300:
                    found = hwnd
            elif not fallback and (
                    normalized_title.startswith('微信')
                    or normalized_title == 'wechat'
                    or normalized_title.startswith('wechat -')
                    or normalized_title == 'weixin'
            ) \
                    and (cls.startswith('Qt') or 'Chrome_WidgetWin' in cls):
                fallback = hwnd
        except Exception:
            pass

    win32gui.EnumWindows(visit, None)
    return found or fallback


def _ocr_engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR


def _green(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return g > r + 18 and g > b + 12 and g > 115


def _incoming_neutral(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return min(r, g, b) >= 205 and max(r, g, b) - min(r, g, b) <= 15 and max(r, g, b) <= 247


class WeChat4Adapter(BaseAdapter):
    """WeChat 4.x: screen OCR for incoming bubbles, mouse+clipboard for fill."""

    name = 'wechat4'
    steals_foreground = True

    def __init__(self) -> None:
        self._previous_messages: list[str] | None = None
        self._pending_messages: list[dict] = []
        self._ocr_error = ''

    def _hwnd(self) -> int:
        return find_wechat_hwnd()

    def _rect(self):
        hwnd = self._hwnd()
        if not hwnd:
            return 0, None
        if win32gui.IsIconic(hwnd):
            return hwnd, None
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] < 360 or rect[3] - rect[1] < 360:
            return hwnd, None
        return hwnd, rect

    def status(self) -> dict:
        hwnd, rect = self._rect()
        detail = (
            '微信 4.x 本地 OCR 模式：只识别聊天区左侧浅色气泡；请保持微信窗口可见，回复由你确认后填入/发送'
            if rect else ('微信窗口已最小化，请还原窗口' if hwnd else '未找到微信 4.x 窗口，请先打开微信')
        )
        if self._ocr_error:
            detail = self._ocr_error
        return {
            'ok': True,
            'mode': 'wechat4',
            'nickname': '微信',
            'wechat_version': '4.x',
            'client_found': bool(hwnd),
            'client_running': bool(rect),
            'detail': detail,
        }

    def sessions(self) -> list[dict]:
        return [{
            'name': '微信当前窗口',
            'time': time.strftime('%H:%M'),
            'content': ('本地 OCR 读取当前可见聊天区；发送目标为当前微信窗口'
                        if self._hwnd() else '微信未运行；显示本地已保存的聊天记录'),
            'isnew': False,
        }]

    def _read_incoming(self, limit: int) -> list[dict]:
        hwnd, rect = self._rect()
        if not hwnd:
            self._ocr_error = '未找到微信窗口，请打开微信后重试'
            return []
        if not rect:
            self._ocr_error = '微信窗口已最小化，请还原后刷新'
            return []
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        # Ratios follow the user's reference crop: chat starts after the left
        # navigation/list panels and ends before the bottom input/tool area.
        x1, y1 = int(width * 0.34), int(height * 0.10)
        x2, y2 = width, int(height * 0.805)
        try:
            image = ImageGrab.grab(bbox=(left + x1, top + y1, left + x2, top + y2), all_screens=True).convert('RGB')
            import numpy as np

            rgb = np.asarray(image)
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            detections, _elapsed = _ocr_engine()(bgr)
        except Exception as exc:
            self._ocr_error = f'微信本地 OCR 暂不可用：{type(exc).__name__}'
            return []

        self._ocr_error = ''
        crop_w, crop_h = image.size
        candidates = []
        for box, text, score in detections or []:
            text = re.sub(r'\s+', '', str(text or '')).strip()
            if not text or float(score) < 0.48 or _TIME_RE.fullmatch(text):
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            x0, x1b = max(0, int(min(xs))), min(crop_w - 1, int(max(xs)))
            y0, y1b = max(0, int(min(ys))), min(crop_h - 1, int(max(ys)))
            edge_guard = max(6, int(crop_h * 0.012))
            if y0 < edge_guard or y1b > crop_h - edge_guard:
                # Avoid treating a bubble clipped by the viewport boundary as
                # a complete new message when the chat is scrolled mid-bubble.
                continue
            # Incoming WeChat bubbles are left aligned; centered system rows
            # and right-side green self bubbles are excluded geometrically.
            if x0 > crop_w * 0.42 or x1b <= x0 or y1b <= y0:
                continue
            sample_x = max(0, x0 - 5)
            sample_y = min(crop_h - 1, max(0, (y0 + y1b) // 2))
            pixel = image.getpixel((sample_x, sample_y))
            if _green(pixel) or not _incoming_neutral(pixel):
                continue
            candidates.append((y0, y1b, x0, text))

        candidates.sort(key=lambda item: (item[0], item[2]))
        groups: list[dict] = []
        for y0, y1b, x0, text in candidates:
            if groups and y0 - groups[-1]['bottom'] <= 30 and abs(x0 - groups[-1]['x']) <= 24:
                groups[-1]['parts'].append(text)
                groups[-1]['bottom'] = max(groups[-1]['bottom'], y1b)
            else:
                groups.append({'top': y0, 'bottom': y1b, 'x': x0, 'parts': [text]})

        result = []
        for group in groups[-limit:]:
            content = ''.join(group['parts']).strip()
            if not content:
                continue
            digest = hashlib.sha1(content.encode('utf-8')).hexdigest()[:14]
            result.append({
                'type': 'friend',
                'sender': '对方',
                'content': content,
                'id': f'wx4-{digest}',
            })
        return result

    def messages(self, who: str | None = None, limit: int = 60) -> list[dict]:
        return self._read_incoming(max(1, min(200, int(limit))))

    def fill(self, who: str, text: str) -> dict:
        if not (text or '').strip():
            return {'ok': False, 'error': 'text 不能为空'}
        hwnd = self._hwnd()
        if not hwnd:
            return {'ok': False, 'error': '未找到微信窗口，请先打开微信'}
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] < 360 or rect[3] - rect[1] < 360:
            return {'ok': False, 'error': '微信窗口尺寸异常，请还原窗口后重试'}
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() != hwnd:
            try:
                current_thread = win32api.GetCurrentThreadId()
                target_thread, _pid = win32process.GetWindowThreadProcessId(hwnd)
                win32process.AttachThreadInput(current_thread, target_thread, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(current_thread, target_thread, False)
            except Exception:
                pass
        time.sleep(0.05)
        if win32gui.GetForegroundWindow() != hwnd:
            return {'ok': False, 'error': '无法切换到微信窗口，请先在微信内点击聊天区再重试'}
        left, top, right, bottom = rect
        # The chat composer is at the lower part of the main window. Relative
        # coordinates keep the click aligned when the user resizes WeChat.
        win32api.SetCursorPos((left + int((right - left) * 0.68), top + int((bottom - top) * 0.88)))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        set_clipboard_text(text)
        time.sleep(0.08)
        _ctrl_combo(VK_V)
        return {'ok': True, 'detail': '已填入微信输入框，请检查后再发送'}

    def send(self, who: str, text: str) -> dict:
        result = self.fill(who, text)
        if not result.get('ok'):
            return result
        time.sleep(0.12)
        _tap(VK_RETURN)
        time.sleep(0.03)
        _tap(VK_RETURN, up=True)
        return {'ok': True, 'detail': '已发送到微信当前聊天'}

    def poll_new(self) -> dict[str, list[dict]]:
        messages = self._read_incoming(60)
        if not messages:
            # A minimized, covered, or resizing window must not erase the seen
            # baseline; otherwise its old bubbles would look new after restore.
            return {}
        contents = [m['content'] for m in messages]
        if self._previous_messages is None:
            self._previous_messages = contents
            return {}

        def similar(left: str, right: str) -> bool:
            a = re.sub(r'\s+', '', left).lower()
            b = re.sub(r'\s+', '', right).lower()
            if a == b:
                return True
            if min(len(a), len(b)) >= 10 and (a in b or b in a):
                return True
            return min(len(a), len(b)) >= 12 and SequenceMatcher(None, a, b).ratio() >= 0.9

        def unmatched(source: list[str], against: list[str]) -> list[int]:
            used: set[int] = set()
            result: list[int] = []
            for index, text in enumerate(source):
                match = next((i for i, old in enumerate(against) if i not in used and similar(text, old)), None)
                if match is None:
                    result.append(index)
                else:
                    used.add(match)
            return result

        new_indices = unmatched(contents, self._previous_messages)
        overlap = len(self._previous_messages) - len(unmatched(self._previous_messages, contents))
        previous_total = len(self._previous_messages)
        if previous_total and (len(new_indices) > max(2, previous_total * 0.5)
                               or overlap < max(1, previous_total * 0.25)):
            if not new_indices:
                # OCR temporarily returned only a subset of already-seen
                # bubbles; keep the old baseline through resize/move frames.
                return {}
            # A large replacement of visible text usually means the user
            # switched chats or OCR briefly saw a different frame; establish a
            # new baseline instead of generating replies for historical text.
            self._previous_messages = contents
            self._pending_messages = []
            return {}

        candidates = [messages[i] for i in new_indices]
        if not candidates:
            self._previous_messages = contents
            self._pending_messages = []
            return {}
        pending = list(self._pending_messages)
        confirmed: list[int] = []
        for index, candidate in enumerate(candidates):
            match = next((i for i, old in enumerate(pending)
                          if similar(candidate['content'], old['content'])), None)
            if match is not None:
                confirmed.append(index)
                pending.pop(match)
        if not confirmed:
            self._pending_messages = candidates
            return {}

        added = [candidates[i] for i in confirmed]
        self._previous_messages = contents
        self._pending_messages = []
        return {'微信当前窗口': added}
