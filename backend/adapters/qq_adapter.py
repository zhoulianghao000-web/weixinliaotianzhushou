"""QQ NT adapter: local screenshot OCR for incoming bubbles, clipboard for fill/send."""
from __future__ import annotations

import hashlib
import re
import time
from difflib import SequenceMatcher

import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab

from .base import BaseAdapter

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56  # 用虚拟键码而非 ord('V')，避免依赖键盘布局
KEYEVENTF_KEYUP = 0x0002
_OCR = None
_TIME_RE = re.compile(r'^\d{1,2}\s*[:：]\s*\d{2}$')


def _ocr_engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _incoming_bubble(pixel: tuple[int, int, int]) -> bool:
    """QQ incoming bubbles in the user's reference are white/light neutral."""
    r, g, b = pixel
    return min(r, g, b) >= 250 and max(r, g, b) - min(r, g, b) <= 8


# ---------------- 剪贴板 ----------------
def get_clipboard_text() -> str:
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ''
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ''
    return ''


def set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


# ---------------- 窗口与键盘 ----------------
def find_hwnd(target_class: str = '', target_title: str = '') -> int:
    """先按类名精确匹配，再按标题包含匹配。"""
    if target_class:
        h = win32gui.FindWindow(target_class, None)
        if h:
            return h
    if not target_title:
        return 0
    h = win32gui.FindWindow(None, target_title)
    if h:
        return h
    found = 0

    def _enum(hwnd, _):
        nonlocal found
        if not found and target_title.lower() in (win32gui.GetWindowText(hwnd) or '').lower():
            found = hwnd

    win32gui.EnumWindows(_enum, None)
    return found


def _tap(vk: int, up: bool = False) -> None:
    win32api.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def _ctrl_combo(vk: int) -> None:
    _tap(VK_CONTROL)
    _tap(vk)
    time.sleep(0.03)
    _tap(vk, up=True)
    _tap(VK_CONTROL, up=True)


class QQAdapter(BaseAdapter):
    name = 'qq'
    steals_foreground = True  # 会激活 QQ 窗口

    def __init__(self, target_class: str = '', target_title: str = 'QQ') -> None:
        self.target_class = target_class
        self.target_title = target_title
        self._previous_messages: list[str] | None = None
        self._ocr_error = ''

    # ---------- 窗口 ----------
    def _hwnd(self) -> int:
        return find_hwnd(self.target_class, self.target_title)

    def _activate(self) -> int:
        """把 QQ 窗口拉到前台（粘贴的前提）。返回 hwnd，0 表示没找到。"""
        h = self._hwnd()
        if not h:
            return 0
        if win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(h)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == h:
            return h
        # 兜底：Windows 会拒绝非前台进程直接 SetForegroundWindow，借用 AttachThreadInput
        try:
            cur = win32api.GetCurrentThreadId()
            tgt_tid, _ = win32process.GetWindowThreadProcessId(h)
            win32process.AttachThreadInput(cur, tgt_tid, True)
            win32gui.SetForegroundWindow(h)
            win32process.AttachThreadInput(cur, tgt_tid, False)
        except Exception:
            pass
        return h if win32gui.GetForegroundWindow() == h else h

    # ---------- 状态 ----------
    def status(self) -> dict:
        h = self._hwnd()
        return {
            'ok': True,
            'mode': 'qq',
            'nickname': 'QQ',
            'wechat_version': None,
            'client_running': bool(h),
            'detail': self._ocr_error or (
                f'QQ 本地 OCR 模式（窗口已找到 hwnd={h}）：仅识别聊天区左侧白色来信气泡；请保持 QQ 窗口可见'
                if h else '未找到 QQ 窗口，请打开 QQ 后再操作'
            ),
        }

    # ---------- 会话 ----------
    def sessions(self) -> list[dict]:
        """QQ cannot expose its session list; do not show a fake row as a chat."""
        return []

    # ---------- 消息 ----------
    def messages(self, who: str | None = None, limit: int = 50) -> list[dict]:
        """Read visible incoming QQ bubbles locally; OCR does not activate the window."""
        return self._read_incoming(max(1, min(200, int(limit))))

    def _read_incoming(self, limit: int) -> list[dict]:
        hwnd = self._hwnd()
        if not hwnd:
            self._ocr_error = '未找到 QQ 窗口，请打开 QQ 后重试'
            return []
        if win32gui.IsIconic(hwnd):
            self._ocr_error = 'QQ 窗口已最小化，请还原后重试'
            return []
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width < 500 or height < 420:
                self._ocr_error = 'QQ 窗口尺寸过小，请放大后重试'
                return []
            # Keep a safety margin around the full QQ message viewport. Extend
            # both vertical edges so bubbles near the top or bottom are not
            # clipped when the window layout or DPI scaling changes slightly.
            x1, y1 = int(width * 0.30), int(height * 0.04)
            x2, y2 = width, int(height * 0.79)
            image = ImageGrab.grab(
                bbox=(left + x1, top + y1, left + x2, top + y2), all_screens=True
            ).convert('RGB')
            import numpy as np
            crop_w, crop_h = image.size
            # QQ message text is small at the sidebar's native capture scale.
            # Upscale before recognition so narrow strokes and punctuation are
            # less likely to disappear; coordinates are mapped back below.
            ocr_scale = 2
            ocr_image = image.resize(
                (crop_w * ocr_scale, crop_h * ocr_scale), Image.Resampling.LANCZOS
            )
            bgr = np.ascontiguousarray(np.asarray(ocr_image)[:, :, ::-1])
            detections, _elapsed = _ocr_engine()(bgr)
        except Exception as exc:
            self._ocr_error = f'QQ 本地 OCR 暂不可用：{type(exc).__name__}'
            return []

        self._ocr_error = ''
        candidates = []
        for box, raw_text, score in detections or []:
            text = re.sub(r'\s+', '', str(raw_text or '')).strip()
            if not text or float(score) < 0.48 or _TIME_RE.fullmatch(text):
                continue
            xs = [float(point[0]) / ocr_scale for point in box]
            ys = [float(point[1]) / ocr_scale for point in box]
            x0, x1b = max(0, int(min(xs))), min(crop_w - 1, int(max(xs)))
            y0, y1b = max(0, int(min(ys))), min(crop_h - 1, int(max(ys)))
            edge = max(6, int(crop_h * 0.012))
            # Incoming QQ messages are left aligned. Right aligned self bubbles,
            # centered system notices, clipped rows and the composer are ignored.
            if x0 > crop_w * 0.48 or y0 < edge or y1b > crop_h - edge:
                continue
            if x1b <= x0 or y1b <= y0:
                continue
            sample = image.getpixel((max(0, x0 - 5), max(0, min(crop_h - 1, (y0 + y1b) // 2))))
            if not _incoming_bubble(sample):
                continue
            candidates.append((y0, y1b, x0, text))

        candidates.sort(key=lambda item: (item[0], item[2]))
        groups: list[dict] = []
        for y0, y1b, x0, text in candidates:
            if groups and y0 - groups[-1]['bottom'] <= 30 and abs(x0 - groups[-1]['x']) <= 24:
                groups[-1]['parts'].append(text)
                groups[-1]['bottom'] = max(groups[-1]['bottom'], y1b)
            else:
                groups.append({'bottom': y1b, 'x': x0, 'parts': [text]})
        result = []
        for group in groups[-limit:]:
            content = ''.join(group['parts']).strip()
            if content:
                digest = hashlib.sha1(content.encode('utf-8')).hexdigest()[:14]
                result.append({'type': 'friend', 'sender': '对方', 'content': content, 'id': f'qq-ocr-{digest}'})
        return result

    # ---------- 填入 / 发送 ----------
    def fill(self, who: str, text: str) -> dict:
        if not (text or '').strip():
            return {'ok': False, 'error': 'text 不能为空'}
        h = self._activate()
        if not h:
            return {'ok': False, 'error': '未找到 QQ 窗口，请先打开 QQ'}
        set_clipboard_text(text)
        time.sleep(0.1)
        _ctrl_combo(VK_V)
        return {'ok': True, 'detail': f'已粘贴到 QQ 输入框（hwnd={h}），请人工确认后再回车'}

    def send(self, who: str, text: str) -> dict:
        r = self.fill(who, text)
        if not r.get('ok'):
            return r
        time.sleep(0.15)
        _tap(VK_RETURN)
        time.sleep(0.03)
        _tap(VK_RETURN, up=True)
        return {'ok': True, 'detail': '已粘贴并回车发送（QQ 当前窗口）'}

    def poll_new(self) -> dict[str, list[dict]]:
        """Poll visible QQ chat locally, emitting only messages absent last frame."""
        messages = self._read_incoming(60)
        if not messages:
            return {}
        contents = [message['content'] for message in messages]
        if self._previous_messages is None:
            self._previous_messages = contents
            return {}

        def similar(left: str, right: str) -> bool:
            a, b = re.sub(r'\s+', '', left).lower(), re.sub(r'\s+', '', right).lower()
            if a == b or (min(len(a), len(b)) >= 10 and (a in b or b in a)):
                return True
            return min(len(a), len(b)) >= 12 and SequenceMatcher(None, a, b).ratio() >= 0.9

        def unmatched(source: list[str], against: list[str]) -> list[int]:
            used: set[int] = set()
            out = []
            for index, text in enumerate(source):
                match = next((i for i, old in enumerate(against) if i not in used and similar(text, old)), None)
                if match is None:
                    out.append(index)
                else:
                    used.add(match)
            return out

        new_indices = unmatched(contents, self._previous_messages)
        overlap = len(self._previous_messages) - len(unmatched(self._previous_messages, contents))
        old_count = len(self._previous_messages)
        if old_count and (len(new_indices) > max(2, old_count * 0.5) or overlap < max(1, old_count * 0.25)):
            if not new_indices:
                return {}
            self._previous_messages = contents
            return {}
        self._previous_messages = contents
        fresh = [messages[i] for i in new_indices]
        return {'QQ 当前窗口': fresh} if fresh else {}
