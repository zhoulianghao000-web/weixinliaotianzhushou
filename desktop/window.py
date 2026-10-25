"""侧边栏主窗口（PRD §6 全局窗口约束）。

窗口 flags：无边框 / Tool / 置顶 / **不抢焦点**。
未贴边时默认停靠在**屏幕右侧垂直居中**，可用鼠标按住空白处拖动（无边框窗口必须自己实现拖动）。
贴边开启后位置与 Z 序交由后端 services/docker.py 接管，前端不调用 move/resize。
所有网络请求走子线程，结果通过 Signal 回主线程（CLAUDE.md §7.4）。
WS 用 PySide6 自带 QWebSocket，不引入新依赖。
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from api_client import ApiClient, ApiWorker
from views.connect_view import ConnectView
from views.main_view import MainView
from views.quick_view import QuickView
from views.product_view import ProductView
from views.settings_view import SettingsView
import theme

VIEW_CONNECT = 'connect'
VIEW_MAIN = 'main'
VIEW_PRODUCTS = 'products'

# PRD §5 错误码 → 用户可读文案（不出现堆栈）
ERR_TEXT = {
    1000: '目标 IM 不可用，请启动所选的 QQ 或微信后重试',
    1001: '读取消息失败：确认所选窗口已打开且可见',
    2001: '请先选择一个会话',
    3001: '大模型调用失败',
    5000: '操作失败',
}


class SidebarWindow(QWidget):
    def __init__(self, width: int = 380, height: int = 760):
        super().__init__()
        self.setObjectName('sidebarWindow')
        self.setWindowTitle('WeChat Sidebar')
        self.setFixedWidth(width)
        self.resize(width, height)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.client = ApiClient()
        self._workers: list[ApiWorker] = []
        self._last_status: dict = {}
        self.current: str | None = None
        self.active_product_id: int | None = None
        self.active_product_name = ''
        self._applied_theme_mode = 'qq'
        self._placed = False  # 首次显示时定一次默认位置，之后不抢位置
        self._drag_pos = None
        self._accept_focus = False  # 当前窗口 flags 是否允许拿到键盘焦点

        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        # ---- V0 ----
        self.connect_view = ConnectView()
        self.connect_view.retry_clicked.connect(lambda: self.refresh_status(auto=False))
        self.connect_view.continue_clicked.connect(self.show_main)
        self.connect_view.close_clicked.connect(self._quit_app)
        self.connect_view.platform_changed.connect(self.on_platform_changed)
        self.stack.addWidget(self.connect_view)

        # ---- V1 ----
        self.main_view = MainView()
        self.main_view.refresh_sessions_clicked.connect(self.load_sessions)
        self.main_view.session_selected.connect(self.on_session_selected)
        self.main_view.generate_clicked.connect(self.on_generate)
        self.main_view.fill_clicked.connect(self.on_fill)
        self.main_view.send_clicked.connect(self.on_send)
        self.main_view.dock_toggled.connect(self.on_dock_toggle)
        self.main_view.listen_toggled.connect(self.on_listen_toggle)
        self.main_view.close_clicked.connect(self._quit_app)
        self.main_view.settings_clicked.connect(self.open_settings)
        self.main_view.quick_clicked.connect(self.open_quick)
        self.main_view.products_clicked.connect(self.open_products)
        self.main_view.platform_changed.connect(self.on_platform_changed)
        self.stack.addWidget(self.main_view)

        # ---- V2 设置 ----
        self.settings_view = SettingsView()
        self.settings_view.back_clicked.connect(self.show_main)
        self.settings_view.save_clicked.connect(self.on_save_settings)
        self.stack.addWidget(self.settings_view)

        # ---- V3 话术库 ----
        self.quick_view = QuickView()
        self.quick_view.back_clicked.connect(self.show_main)
        self.quick_view.insert_requested.connect(self.on_insert_quick)
        self.quick_view.add_requested.connect(self.on_add_quick)
        self.quick_view.delete_requested.connect(self.on_delete_quick)
        self.stack.addWidget(self.quick_view)

        # ---- V4 产品库 ----
        self.product_view = ProductView()
        self.product_view.back_clicked.connect(self.show_main)
        self.product_view.add_requested.connect(self.on_add_product)
        self.product_view.update_requested.connect(self.on_update_product)
        self.product_view.delete_product_requested.connect(self.on_delete_product)
        self.product_view.product_selected.connect(self.load_product_documents)
        self.product_view.use_product_requested.connect(self.on_use_product)
        self.product_view.clear_product_requested.connect(self.on_clear_product)
        self.product_view.import_requested.connect(self.on_import_product_folder)
        self.product_view.import_paths_requested.connect(self.on_import_product_paths)
        self.product_view.delete_document_requested.connect(self.on_delete_product_document)
        self.stack.addWidget(self.product_view)

        # 停留在 V0 时每 3 秒轮询一次 status（PRD §3 V0）
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(lambda: self.refresh_status(auto=True))

        # ---- WS（监听开启时才连接）----
        self._ws: QWebSocket | None = None
        self._ws_attempts = 0
        self._ws_timer = QTimer(self)
        self._ws_timer.setInterval(5000)
        self._ws_timer.timeout.connect(self._ws_retry)
        # Debounce incoming bubbles so a burst of messages produces one draft.
        self._auto_reply_pending = False
        self._reply_generation_busy = False
        self._auto_reply_timer = QTimer(self)
        self._auto_reply_timer.setSingleShot(True)
        self._auto_reply_timer.setInterval(650)
        self._auto_reply_timer.timeout.connect(self._generate_auto_reply)

        self.refresh_status(auto=False)

    # ---------------- 视图切换 ----------------
    def show_main(self) -> None:
        self._timer.stop()
        self.stack.setCurrentWidget(self.main_view)
        self._set_accept_focus(self._last_status.get('mode') in ('qq', 'wechat4'))
        if self._last_status:
            self.main_view.set_status(self._last_status)
        self.load_sessions()

    def _quit_app(self) -> None:
        """✕ 关闭按钮：退出整个应用。exe 形态下内嵌后端同进程，一起退出。"""
        self._ws_close()
        QApplication.instance().quit()

    # ---------------- 默认位置与拖动 ----------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._apply_rounded_corners()
        if not self._placed:
            self._placed = True
            g = QGuiApplication.primaryScreen().availableGeometry()
            # 屏幕右侧垂直居中：不贴边也不挡在屏幕中间
            self.move(g.right() - self.width() - 24, g.top() + (g.height() - self.height()) // 2)

    def _apply_rounded_corners(self) -> None:
        """Ask Windows 11 DWM to round this frameless sidebar's outer corners."""
        try:
            corner_preference = ctypes.c_int(2)  # DWMWCP_ROUND
            hwnd = ctypes.c_void_p(int(self.winId()))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                33,  # DWMWA_WINDOW_CORNER_PREFERENCE
                ctypes.byref(corner_preference),
                ctypes.sizeof(corner_preference),
            )
        except Exception:
            # Older Windows builds ignore this cosmetic hint; app behavior is unchanged.
            pass

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def _set_accept_focus(self, on: bool) -> None:
        """按 Adapter 决定是否允许侧边栏拿到键盘焦点。

        - wxauto（微信 3.9）：**必须不抢焦点**，否则点击侧边栏会让微信输入框失焦、SendKeys 打空
        - QQ 半自动：**必须能拿焦点**，否则"补充要求"填不进去、生成的回复改不了
          （适配器自己会激活 QQ 窗口粘贴，不依赖"QQ 一直持焦点"）
        """
        if self._accept_focus == on:
            return
        self._accept_focus = on
        flags = self.windowFlags()
        flags = (flags & ~Qt.WindowDoesNotAcceptFocus) if on else (flags | Qt.WindowDoesNotAcceptFocus)
        pos = self.pos()
        self.setWindowFlags(flags)  # 改 flags 后必须重新 show，位置会被重置
        self.show()
        self.move(pos)

    # ---------------- 通用：子线程跑请求 ----------------
    def _spawn(self, fn, slot) -> None:
        w = ApiWorker(fn, self)
        w.result.connect(slot)
        w.finished.connect(lambda: self._drop(w))
        self._workers.append(w)
        w.start()

    def _drop(self, w: ApiWorker) -> None:
        if w in self._workers:
            self._workers.remove(w)

    def _fail(self, res: dict) -> str:
        code = res.get('code')
        text = ERR_TEXT.get(code, res.get('message') or '未知错误')
        return f'{text}（{code}）' if code else text

    # ---------------- status ----------------
    def refresh_status(self, *, auto: bool = False) -> None:
        """auto=True 为定时轮询：保留现有错误文案，避免每 3 秒闪回"正在连接"。"""
        if not auto:
            self.connect_view.show_loading()
        self._timer.start()
        self._spawn(lambda: self.client.status(), self._on_status)

    def _on_status(self, res: dict) -> None:
        if not res.get('ok'):
            if self.stack.currentWidget() is self.main_view:
                self.main_view.toast(self._fail(res), 'error')
            else:
                self.connect_view.show_error(self._fail(res))
            return
        data = res['data'] or {}
        previous_mode = self._last_status.get('mode')
        self._last_status = data
        if previous_mode and previous_mode != data.get('mode'):
            # Messages from different IMs must never be shown as one thread.
            self.current = None
            self.main_view.set_messages([])
        theme_mode = 'wechat' if data.get('mode') in ('wechat', 'wechat4', 'wxauto') else 'qq'
        if theme_mode != self._applied_theme_mode:
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(theme.stylesheet(theme_mode))
            self._applied_theme_mode = theme_mode
            self.main_view._palette = theme.palette(theme_mode)
            self.product_view.set_mode(theme_mode)
            self.connect_view.set_mode(theme_mode)
            self.settings_view.set_mode(theme_mode)
            self.quick_view.set_mode(theme_mode)
        # Keep settings focusable while status polling runs. Flipping the native
        # no-activate window flag during a combo popup steals focus and closes it.
        settings_open = self.stack.currentWidget() is self.settings_view
        self._set_accept_focus(settings_open or data.get('mode') in ('qq', 'wechat4'))
        self.connect_view.show_status(data)
        if self.stack.currentWidget() is self.main_view:
            self.main_view.set_status(data)
        # QQ / WeChat 4.x can show locally cached messages without a running
        # client, so enter the main view in these modes even while offline.
        if (
            self.stack.currentWidget() is self.connect_view
            and (
                data.get('mode') in ('wechat4', 'qq')
                or (data.get('mode') == 'wxauto' and data.get('wechat_running'))
            )
        ):
            self.show_main()

    # ---------------- sessions ----------------
    def load_sessions(self) -> None:
        self.main_view.show_sessions_loading()
        self._spawn(lambda: self.client.sessions(), self._on_sessions)

    def _on_sessions(self, res: dict) -> None:
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            self.main_view.set_sessions([])
            return
        sessions = (res['data'] or {}).get('sessions') or []
        self.main_view.set_sessions(sessions)
        # Keep the backend's current chat only when it belongs to this
        # adapter's session list. On adapter changes an old QQ target may
        # otherwise shadow WeChat's single "微信当前窗口" OCR target.
        session_names = {s.get('name') for s in sessions if s.get('name')}
        who = self._last_status.get('current')
        if who not in session_names:
            who = sessions[0].get('name') if sessions else None
        if not who and self._last_status.get('mode') == 'qq':
            # QQ cannot expose its chat list. Keep a stable internal target
            # without presenting a fake “QQ 当前窗口” session row.
            who = 'QQ 当前窗口'
        elif not who and self._last_status.get('mode') == 'wechat4':
            who = '微信当前窗口'
        if who:
            self.main_view.select_session(who)
            self.load_messages(who)

    def on_session_selected(self, who: str) -> None:
        if not who or who == self.current:
            return
        self.load_messages(who)

    # ---------------- messages ----------------
    def load_messages(self, who: str) -> None:
        self.current = who
        self.main_view.show_messages_loading()
        self._spawn(lambda: self.client.messages(who=who, limit=60), self._on_messages)

    def _on_messages(self, res: dict) -> None:
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            self.main_view.show_messages_empty()
            return
        data = res['data'] or {}
        msgs = data.get('messages') or []
        if not msgs and self._last_status.get('mode') == 'qq':
            self.main_view.show_messages_empty('未识别到左侧白色来信气泡；请保持 QQ 聊天窗口可见后刷新')
            return
        if not msgs and self._last_status.get('mode') == 'wechat4':
            self.main_view.show_messages_empty('未识别到左侧浅色气泡；请保持微信聊天窗口可见后刷新')
            return
        self.main_view.set_messages(msgs)

    # ---------------- 生成建议回复（PRD §3.6 C）----------------
    def on_generate(self) -> None:
        who = self.current
        if not who:
            self.main_view.toast(ERR_TEXT[2001], 'error')
            return
        if self._reply_generation_busy:
            self._auto_reply_pending = True
            return
        self._reply_generation_busy = True
        self.main_view.set_generating(True)
        instruction = self.main_view.instruction()
        self._spawn(lambda: self.client.reply(who, instruction, product_id=self.active_product_id), self._on_reply)

    def _on_reply(self, res: dict) -> None:
        self._reply_generation_busy = False
        self.main_view.set_generating(False)
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            self._start_pending_auto_reply()
            return
        d = res['data'] or {}
        self.main_view.set_reply(
            d.get('reply') or '', d.get('source') or '', d.get('product_sources') or [],
            product_context_used=d.get('product_context_used', True),
        )
        if d.get('degraded'):
            self.main_view.toast('模型失败，已用规则兜底（仍可发送）', 'warn')
        self._start_pending_auto_reply()

    def _queue_auto_reply(self) -> None:
        if self._reply_generation_busy:
            self._auto_reply_pending = True
            return
        self._auto_reply_timer.start()

    def _generate_auto_reply(self) -> None:
        if not self.main_view.is_listening() or not self.current:
            return
        if self._reply_generation_busy:
            self._auto_reply_pending = True
            return
        self.on_generate()

    def _start_pending_auto_reply(self) -> None:
        if self._auto_reply_pending and self.main_view.is_listening():
            self._auto_reply_pending = False
            self._auto_reply_timer.start()

    # ---------------- 填入 / 发送（PRD §3.6 D / E）----------------
    def on_fill(self) -> None:
        who, text = self._ready_to_send()
        if not (who and text):
            return
        self.main_view.set_sending(True)
        platform = '微信' if self._last_status.get('mode') in ('wechat4', 'wxauto') else 'QQ'
        self._spawn(lambda: self.client.fill(who, text), lambda r: self._on_sent(r, f'已填入{platform}输入框', refresh=False))

    def on_send(self) -> None:
        who, text = self._ready_to_send()
        if not (who and text):
            return
        self.main_view.set_sending(True)
        self._spawn(lambda: self.client.send(who, text), lambda r: self._on_sent(r, '已发送', refresh=True))

    def _ready_to_send(self) -> tuple[str | None, str]:
        text = self.main_view.reply_text()
        if not text:
            return None, ''
        who = self.current
        if not who:
            self.main_view.toast(ERR_TEXT[2001], 'error')
            return None, text
        return who, text

    def _on_sent(self, res: dict, verb: str, refresh: bool) -> None:
        self.main_view.set_sending(False)
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            return
        d = res['data'] or {}
        if d.get('duplicated'):
            self.main_view.toast('已发送（忽略重复点击）', 'warn')
        elif verb != '已发送':
            self.main_view.toast(verb, 'ok')
        if refresh:
            QTimer.singleShot(800, self._reload_current)

    def _reload_current(self) -> None:
        if self.current:
            self.load_messages(self.current)

    # ---------------- 贴边 / 监听开关（PRD §3.6 F / G）----------------
    def on_dock_toggle(self, on: bool) -> None:
        self._spawn(lambda: self.client.dock(on, 380), lambda r: self._on_dock(r, on))

    def _on_dock(self, res: dict, on: bool) -> None:
        if not res.get('ok'):
            self.main_view.set_docking(not on)  # 回滚按钮态
            self.main_view.toast(self._fail(res), 'error')
            return
        d = res['data'] or {}
        self.main_view.set_docking(bool(d.get('docking')))
        if d.get('error'):
            self.main_view.toast(f'贴边异常：{d["error"]}', 'warn')
        else:
            self.main_view.toast('贴边已开启' if d.get('docking') else '贴边已关闭', 'ok')

    def on_listen_toggle(self, on: bool) -> None:
        self._spawn(lambda: self.client.listen(on), lambda r: self._on_listen(r, on))

    def _on_listen(self, res: dict, on: bool) -> None:
        if not res.get('ok'):
            self.main_view.set_listen(not on)  # 回滚按钮态
            self.main_view.toast(self._fail(res), 'error')
            return
        listen = bool((res['data'] or {}).get('listen'))
        self.main_view.set_listen(listen)
        if listen:
            self.main_view.toast('实时监听已开启', 'ok')
            self._ws_connect()
        else:
            self.main_view.toast('实时监听已关闭', 'ok')
            self._ws_close()

    # ---------------- V2 设置页（CLAUDE.md §4.11）----------------
    def open_settings(self) -> None:
        self._spawn(lambda: self.client.get_settings(), self._on_settings_loaded)

    def _on_settings_loaded(self, res: dict) -> None:
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            return
        self.settings_view.set_settings(res['data'] or {})
        # Keep dropdown popups and editable fields alive while settings are open,
        # including when the previous adapter was the no-activate legacy mode.
        self._set_accept_focus(True)
        self.stack.setCurrentWidget(self.settings_view)

    def on_save_settings(self) -> None:
        vals = self.settings_view.values()
        self.settings_view.set_saving(True)
        self._spawn(lambda: self.client.put_settings(**vals), self._on_settings_saved)

    def _on_settings_saved(self, res: dict) -> None:
        self.settings_view.set_saving(False)
        if not res.get('ok'):
            self.settings_view.toast(self._fail(res), 'error')
            return
        d = res['data'] or {}
        # 宽度即时生效
        w = int(d.get('width') or 380)
        if w != self.width():
            self.setFixedWidth(w)
            self.resize(w, self.height())
        if d.get('adapter_changed'):
            # 适配器热切换：后端已惰性重建并关闭监听，UI 同步
            self.main_view.set_listen(False)
            self._ws_close()
            self.settings_view.toast('已保存；适配器已切换、监听已关闭', 'warn')
            self.current = None
            self._spawn(lambda: self.client.status(), self._after_adapter_change)
        else:
            self.settings_view.toast('已保存（即时生效）', 'ok')

    def on_platform_changed(self, adapter: str) -> None:
        """Switch QQ / WeChat directly from the main header selector."""
        if not adapter or adapter == self._last_status.get('selected_adapter'):
            return
        self.main_view.cmbPlatform.setEnabled(False)
        self.connect_view.cmbPlatform.setEnabled(False)
        self._spawn(lambda: self.client.put_settings(adapter=adapter), self._on_platform_change_saved)

    def _on_platform_change_saved(self, res: dict) -> None:
        self.main_view.cmbPlatform.setEnabled(True)
        self.connect_view.cmbPlatform.setEnabled(True)
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            if self._last_status:
                self.main_view.set_status(self._last_status)
                self.connect_view.show_status(self._last_status)
            return
        self.current = None
        self._spawn(lambda: self.client.status(), self._after_adapter_change)

    def _after_adapter_change(self, res: dict) -> None:
        self._on_status(res)
        if res.get('ok'):
            self.load_sessions()
            self.main_view.toast('已切换平台；实时监听已关闭', 'ok')
        self.refresh_status(auto=False)  # 顶部状态条 / 焦点策略跟随新适配器

    # ---------------- V3 话术库页（CLAUDE.md §4.12）----------------
    def open_quick(self) -> None:
        self._spawn(lambda: self.client.quick_replies(), self._on_quick_loaded)

    def _on_quick_loaded(self, res: dict) -> None:
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            return
        self.quick_view.set_items((res['data'] or {}).get('items') or [])
        self.stack.setCurrentWidget(self.quick_view)

    def on_insert_quick(self, text: str) -> None:
        """插入话术：追加到回复框（已有内容则换行），并跳回主页面。"""
        cur = self.main_view.reply_text()
        self.main_view.set_reply((cur + '\n' + text) if cur else text, '')
        self.stack.setCurrentWidget(self.main_view)
        self.main_view.toast('已插入话术', 'ok')

    def on_add_quick(self, tag: str, content: str) -> None:
        self._spawn(lambda: self.client.add_quick_reply(tag, content), self._on_quick_added)

    def _on_quick_added(self, res: dict) -> None:
        if not res.get('ok'):
            self.quick_view.toast(self._fail(res), 'error')
            return
        self.quick_view.set_items((res['data'] or {}).get('items') or [])
        self.quick_view.clear_add_form()
        self.quick_view.toast('已添加', 'ok')

    def on_delete_quick(self, qid: int) -> None:
        self._spawn(lambda: self.client.delete_quick_reply(qid), self._on_quick_deleted)

    def _on_quick_deleted(self, res: dict) -> None:
        if not res.get('ok'):
            self.quick_view.toast(self._fail(res), 'error')
            return
        self.quick_view.set_items((res['data'] or {}).get('items') or [])
        self.quick_view.toast('已删除', 'ok')

    # ---------------- V4 产品库页 ----------------
    def open_products(self) -> None:
        self._spawn(lambda: self.client.products(), self._on_products_loaded)

    def _on_products_loaded(self, res: dict) -> None:
        if not res.get('ok'):
            self.main_view.toast(self._fail(res), 'error')
            return
        self.product_view.set_products((res.get('data') or {}).get('items') or [])
        self.product_view.set_active_product(self.active_product_id, self.active_product_name)
        self.stack.setCurrentWidget(self.product_view)

    def _reload_products(self, selected_id: int | None = None, *, toast: str = '') -> None:
        def done(res: dict) -> None:
            if not res.get('ok'):
                self.product_view.toast(self._fail(res), 'error')
                return
            self.product_view.set_products((res.get('data') or {}).get('items') or [])
            if selected_id:
                for i in range(self.product_view.listProducts.count()):
                    item = self.product_view.listProducts.item(i)
                    if item.data(Qt.UserRole) == selected_id:
                        self.product_view.listProducts.setCurrentItem(item)
                        break
            if toast:
                self.product_view.toast(toast)
        self._spawn(lambda: self.client.products(), done)

    def load_product_documents(self, product_id: int) -> None:
        self.product_view._active_product_id = product_id
        self._spawn(lambda: self.client.product_documents(product_id), self._on_product_documents)

    def _on_product_documents(self, res: dict) -> None:
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        self.product_view.set_documents((res.get('data') or {}).get('items') or [])

    def on_add_product(self, name: str, category: str, summary: str) -> None:
        self._spawn(lambda: self.client.add_product(name, category, summary), self._on_product_added)

    def _on_product_added(self, res: dict) -> None:
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        data = res.get('data') or {}
        self.product_view.clear_form()
        self._reload_products((data.get('item') or {}).get('id'), toast='已新增产品')

    def on_update_product(self, product_id: int, name: str, category: str, summary: str) -> None:
        self._spawn(lambda: self.client.update_product(product_id, name, category, summary),
                    lambda r: self._on_product_mutation(r, product_id, '已保存修改'))

    def _on_product_mutation(self, res: dict, product_id: int, message: str) -> None:
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        self._reload_products(product_id, toast=message)

    def on_delete_product(self, product_id: int) -> None:
        product = self.product_view.selected_product() or {}
        answer = QMessageBox.question(
            self, '删除产品',
            f'删除“{product.get("name") or "该产品"}”及本机产品库中的原件副本和索引？原始导入文件不受影响。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._spawn(lambda: self.client.delete_product(product_id),
                    lambda r: self._on_product_deleted(r, product_id))

    def _on_product_deleted(self, res: dict, product_id: int) -> None:
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        if self.active_product_id == product_id:
            self.on_clear_product()
        self._reload_products(toast='已删除产品及其资料副本')

    def on_use_product(self, product_id: int, name: str) -> None:
        self.active_product_id, self.active_product_name = product_id, name
        self.main_view.set_selected_product(product_id, name)
        self.stack.setCurrentWidget(self.main_view)
        self.main_view.toast(f'回复将检索“{name}”资料', 'ok')

    def on_clear_product(self) -> None:
        self.active_product_id, self.active_product_name = None, ''
        self.main_view.set_selected_product(None)
        self.product_view.set_active_product(None)
        self.main_view.toast('已关闭产品资料检索', 'ok')

    def on_import_product_folder(self, product_id: int) -> None:
        folder = QFileDialog.getExistingDirectory(self, '选择产品资料文件夹')
        if not folder:
            return
        if not product_id:
            self.product_view.set_importing(True)
            self._spawn(lambda: self.client.add_product(Path(folder).name or '导入资料'),
                        lambda r: self._on_product_created_for_import(r, [folder]))
            return
        self.product_view.set_importing(True)
        self._spawn(lambda: self.client.import_product_folder(product_id, folder),
                    lambda r: self._on_product_imported(r, product_id))

    @staticmethod
    def _suggest_product_name(paths: list[str]) -> str:
        entries = [Path(path) for path in paths]
        if len(entries) == 1:
            entry = entries[0]
            return entry.name if entry.is_dir() else entry.stem
        parents = {str(entry.parent) for entry in entries}
        if len(parents) == 1:
            return entries[0].parent.name or '导入资料'
        return '导入资料'

    def on_import_product_paths(self, product_id: int, paths: list[str]) -> None:
        paths = [str(path) for path in paths if path and Path(path).exists()]
        if not paths:
            self.product_view.toast('没有找到可导入的文件或文件夹', 'warn')
            return
        self.product_view.set_importing(True)
        if not product_id:
            name = self._suggest_product_name(paths)
            self._spawn(lambda: self.client.add_product(name),
                        lambda r: self._on_product_created_for_import(r, paths))
            return
        self._start_product_import(product_id, paths)

    def _on_product_created_for_import(self, res: dict, paths: list[str]) -> None:
        if not res.get('ok'):
            self.product_view.set_importing(False)
            self.product_view.toast(self._fail(res), 'error')
            return
        product_id = int((res.get('data') or {}).get('item', {}).get('id') or 0)
        if not product_id:
            self.product_view.set_importing(False)
            self.product_view.toast('产品已创建，但未取得产品编号', 'error')
            return
        self._reload_products(product_id)
        self._start_product_import(product_id, paths)

    def _start_product_import(self, product_id: int, paths: list[str]) -> None:
        def import_all():
            combined = {
                'product_id': product_id,
                'items': [],
                'imported_count': 0,
                'duplicate_count': 0,
                'error_count': 0,
            }
            for path in paths:
                try:
                    result = self.client.import_product_folder(product_id, path)
                    for key in ('items', 'imported_count', 'duplicate_count', 'error_count'):
                        if key == 'items':
                            combined[key].extend(result.get(key) or [])
                        else:
                            combined[key] += int(result.get(key) or 0)
                except Exception as exc:
                    combined['error_count'] += 1
                    combined['items'].append({
                        'name': Path(path).name,
                        'status': 'error',
                        'detail': str(exc)[:1000],
                    })
            return combined

        self._spawn(import_all, lambda r: self._on_product_imported(r, product_id))

    def _on_product_imported(self, res: dict, product_id: int) -> None:
        self.product_view.set_importing(False)
        data = res.get('data') or {}
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        items = data.get('items') or []
        summary = f'导入完成：{data.get("imported_count", 0)} 个，重复 {data.get("duplicate_count", 0)} 个，失败 {data.get("error_count", 0)} 个'
        details = '\n'.join(f'{item.get("name", "文件")}: {item.get("detail", item.get("status", "完成"))}' for item in items)
        self.product_view.toast(summary, 'warn' if data.get('error_count') else 'ok')
        self.load_product_documents(product_id)
        self._reload_products(product_id)
        if details and data.get('error_count'):
            QMessageBox.information(self, '导入结果', summary + '\n\n' + details[:5000])

    def on_delete_product_document(self, product_id: int, document_id: int) -> None:
        self.product_view.set_removing(True)
        self._spawn(lambda: self.client.delete_product_document(product_id, document_id),
                    lambda r: self._on_product_document_deleted(r, product_id))

    def _on_product_document_deleted(self, res: dict, product_id: int) -> None:
        self.product_view.set_removing(False)
        if not res.get('ok'):
            self.product_view.toast(self._fail(res), 'error')
            return
        self.load_product_documents(product_id)
        self._reload_products(product_id, toast='已移除资料')

    # ---------------- WS 推送（PRD §3.6 G）----------------
    def _ws_url(self) -> QUrl:
        port = self.client.base.rsplit(':', 1)[-1]
        return QUrl(f'ws://127.0.0.1:{port}/ws/messages')

    def _ws_connect(self) -> None:
        self._ws_close()
        self._ws_attempts = 0
        self._ws = QWebSocket()
        self._ws.textMessageReceived.connect(self._on_ws_text)
        self._ws.disconnected.connect(self._ws_disconnected)
        self._ws.open(self._ws_url())

    def _ws_disconnected(self) -> None:
        # 自动重连：5 秒间隔，最多 3 次（PRD §3.6 G）
        if self.main_view.is_listening() and self._ws_attempts < 3:
            self._ws_attempts += 1
            self._ws_timer.start()

    def _ws_retry(self) -> None:
        self._ws_timer.stop()
        if self.main_view.is_listening() and self._ws is not None:
            self._ws.open(self._ws_url())

    def _ws_close(self) -> None:
        self._ws_timer.stop()
        if self._ws is not None:
            try:
                self._ws.textMessageReceived.disconnect(self._on_ws_text)
                self._ws.disconnected.disconnect(self._ws_disconnected)
            except Exception:
                pass
            self._ws.close()
            self._ws.deleteLater()
            self._ws = None

    def _on_ws_text(self, text: str) -> None:
        try:
            payload = json.loads(text)
        except Exception:
            return
        if payload.get('type') != 'new':
            return
        data = payload.get('data') or {}
        for chat, msgs in data.items():
            is_current = chat == self.current
            if self._last_status.get('mode') == 'wechat4':
                is_current = is_current or chat == '微信当前窗口'
            if is_current:
                self.main_view.append_messages(msgs)
                if any(m.get('type') == 'friend' and m.get('content') for m in msgs):
                    self._queue_auto_reply()
            else:
                self.main_view.mark_new(chat)
