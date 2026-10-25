from .base import BaseAdapter
from .mock_adapter import MockAdapter
from .qq_adapter import QQAdapter
from .wechat4_adapter import WeChat4Adapter, _process_image_name

__all__ = ['BaseAdapter', 'MockAdapter', 'QQAdapter', 'WeChat4Adapter']


def create_adapter(prefer: str = 'auto') -> BaseAdapter:
    """Create the explicitly selected adapter, or auto-select the foreground IM."""
    if prefer == 'wechat4':
        return WeChat4Adapter()
    if prefer == 'qq':
        return QQAdapter()
    if prefer == 'auto':
        try:
            import win32gui
            import win32process

            foreground = win32gui.GetForegroundWindow()
            title = (win32gui.GetWindowText(foreground) or '').lower()
            _thread, pid = win32process.GetWindowThreadProcessId(foreground)
            image_name = _process_image_name(pid)
            if image_name == 'weixin.exe' or '微信' in title:
                return WeChat4Adapter()
            if image_name in {'qq.exe', 'qqnt.exe'} or title == 'qq' or title.startswith('qq '):
                return QQAdapter()
            active_im = []

            def visit(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                    return
                try:
                    _thread, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    name = _process_image_name(window_pid)
                    if name == 'weixin.exe':
                        active_im.append('wechat4')
                    elif name in {'qq.exe', 'qqnt.exe'}:
                        active_im.append('qq')
                except Exception:
                    pass

            win32gui.EnumWindows(visit, None)
            if active_im and active_im[0] == 'wechat4':
                return WeChat4Adapter()
            if active_im and active_im[0] == 'qq':
                return QQAdapter()
        except Exception:
            pass
        return QQAdapter()
    if prefer == 'wxauto':
        from .wxauto_adapter import WxautoAdapter

        return WxautoAdapter()
    return MockAdapter()
