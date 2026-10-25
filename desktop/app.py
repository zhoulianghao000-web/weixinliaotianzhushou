"""入口：`python desktop/app.py`，打包后即为单个 exe 的入口。

打包形态下**后端内嵌在本进程里**（uvicorn 跑在守护线程），双击 exe 即可用，
不需要另外开终端跑 `server.py`。若本机已有后端在跑（端口已占用），就直接复用它。
"""
from __future__ import annotations

import socket
import sys
import threading
import os
import json
import urllib.error
import urllib.request
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = DESKTOP_DIR.parent / 'backend'
sys.path.insert(0, str(DESKTOP_DIR))
# 静态 import，保证 PyInstaller 能把整个后端依赖树打进包
sys.path.insert(0, str(BACKEND_DIR))

from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
from window import SidebarWindow  # noqa: E402

FROZEN = bool(getattr(sys, 'frozen', False))


def root_dir() -> Path:
    """打包后根目录 = exe 所在目录（.env / data / logs 都在它旁边）。"""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return DESKTOP_DIR.parent


def _redirect_stdio() -> None:
    """windowed 打包没有控制台，把输出落盘，否则排错时两眼一抹黑。"""
    if not FROZEN:
        return
    log_dir = root_dir() / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    f = open(log_dir / 'app.log', 'a', encoding='utf-8')
    sys.stdout = sys.stderr = f


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def backend_compatible(host: str, port: int, timeout: float = 0.6) -> bool:
    """Do not reuse a stale backend just because something listens on the port."""
    api_host = '127.0.0.1' if host in {'0.0.0.0', '::'} else host
    try:
        def get_json(path: str) -> dict:
            with urllib.request.urlopen(f'http://{api_host}:{port}{path}', timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))

        status = get_json('/api/status')
        products = get_json('/api/products')
        status_data = status.get('data') or {}
        product_data = products.get('data') or {}
        return (
            status.get('code') == 0
            and 'selected_adapter' in status_data
            and 'client_running' in status_data
            and products.get('code') == 0
            and isinstance(product_data.get('items'), list)
        )
    except Exception:
        return False


def start_embedded_backend() -> bool:
    """端口空闲才起内嵌后端；已有人在跑就复用，避免抢端口。返回是否由本进程启动。"""
    try:
        import config
    except Exception:
        return False
    preferred_port = config.PORT
    if port_open(config.HOST, preferred_port):
        if backend_compatible(config.HOST, preferred_port):
            api_host = '127.0.0.1' if config.HOST in {'0.0.0.0', '::'} else config.HOST
            os.environ['SIDEBAR_API'] = f'http://{api_host}:{preferred_port}'
            print(f'[app] 复用兼容后端 {api_host}:{preferred_port}')
            return False
        # A previous app version may still own the default port. Keep it intact
        # and start this package's embedded backend on the next free local port.
        config.PORT = next(
            (candidate for candidate in range(preferred_port + 1, preferred_port + 41)
             if not port_open(config.HOST, candidate)),
            0,
        )
        if not config.PORT:
            print('[app] 默认端口被旧服务占用，且备用端口均不可用')
            return False
        print(f'[app] 默认端口有不兼容服务，改用 {config.PORT}')

    api_host = '127.0.0.1' if config.HOST in {'0.0.0.0', '::'} else config.HOST
    os.environ['SIDEBAR_PORT'] = str(config.PORT)
    os.environ['SIDEBAR_API'] = f'http://{api_host}:{config.PORT}'

    def run() -> None:
        try:
            import uvicorn
            from server import app as fastapi_app

            uvicorn.run(fastapi_app, host=config.HOST, port=config.PORT, log_level='warning')
        except Exception as e:  # 后端挂了也不能拖死 UI
            print(f'[app] 内嵌后端启动失败: {type(e).__name__}: {e}')

    threading.Thread(target=run, daemon=True).start()
    print(f'[app] 内嵌后端启动中 {config.HOST}:{config.PORT}')
    return True


def main() -> None:
    _redirect_stdio()
    started = start_embedded_backend()
    if started:
        # 等后端接口就绪，否则首屏会闪一下"后端未启动"。
        for _ in range(60):
            try:
                import config

                if backend_compatible(config.HOST, config.PORT, timeout=0.3):
                    break
            except Exception:
                break
            import time

            time.sleep(0.1)

    app = QApplication(sys.argv)
    # Auxiliary dialogs (for example, confirmation/error dialogs) must not
    # terminate the floating sidebar when they are dismissed. The sidebar's
    # own close button calls QApplication.quit() explicitly.
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.QSS)

    win = SidebarWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
