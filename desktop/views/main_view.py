"""V1.main — 主功能页（PRD §3）。

分区：顶部状态条 / 会话区 / 消息区 / 回复区 / 开关区 / toast。
本文件只负责"渲染与交互约定"，所有数据由 window.py 通过 set_* 方法灌入。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QStyleFactory,
)

from theme import BRAND, BRAND_DARK, ERROR, LINE, PANEL, SUB, TEXT, WARN, palette

TYPE_SYS = ('sys', 'time', 'recall')


class UpwardComboBox(QComboBox):
    """Show the platform picker above its control when it is opened."""

    def showPopup(self) -> None:
        super().showPopup()
        popup = self.view().window()
        top = self.mapToGlobal(QPoint(0, 0)).y()
        popup_height = popup.frameGeometry().height()
        screen = self.screen()
        available = screen.availableGeometry() if screen else None
        popup_y = top - popup_height - 2
        if available is not None and popup_y < available.top():
            popup_y = available.top()
        popup.move(popup.x(), popup_y)


class MainView(QWidget):
    # 交互信号（由 window.py 接线，PRD §3.6）
    generate_clicked = Signal()
    fill_clicked = Signal()
    send_clicked = Signal()
    dock_toggled = Signal(bool)
    listen_toggled = Signal(bool)
    refresh_sessions_clicked = Signal()
    session_selected = Signal(str)
    close_clicked = Signal()
    settings_clicked = Signal()
    quick_clicked = Signal()
    products_clicked = Signal()
    platform_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('mainView')
        self._docking = False
        self._listen = False
        self._palette = palette('qq')
        self._theme_mode = 'qq'
        self._hide_sessions = False
        self._selected_product_id: int | None = None
        self._selected_product_name = ''
        self._current_messages: list[dict] = []
        # setItemWidget 不会持有 Python 引用，必须在实例上保留，否则控件被 GC 掉
        self._bubbles: list[QWidget] = []

        # ---------- 顶部状态条 ----------
        self.cmbPlatform = UpwardComboBox()
        self.cmbPlatform.setObjectName('cmbPlatform')
        self.cmbPlatform.setStyle(QStyleFactory.create('Fusion'))
        self.cmbPlatform.setFixedSize(84, 32)
        self.cmbPlatform.addItem('自动', 'auto')
        self.cmbPlatform.addItem('QQ', 'qq')
        self.cmbPlatform.addItem('微信', 'wechat4')
        self.cmbPlatform.setToolTip('选择当前识别和回复的平台')
        self.cmbPlatform.currentIndexChanged.connect(
            lambda _index: self.platform_changed.emit(self.cmbPlatform.currentData())
        )
        self.btnSettings = QToolButton()
        self.btnSettings.setObjectName('btnSettings')
        self.btnSettings.setText('⚙')
        self.btnSettings.setToolTip('设置：API Key / 模型 / 窗口宽度 / 适配器')
        self.btnSettings.clicked.connect(self.settings_clicked.emit)

        self.btnQuick = QToolButton()
        self.btnQuick.setObjectName('btnQuick')
        self.btnQuick.setText('💬')
        self.btnQuick.setToolTip('话术库：点一条插入回复框')
        self.btnQuick.clicked.connect(self.quick_clicked.emit)

        self.btnProducts = QToolButton()
        self.btnProducts.setObjectName('btnProducts')
        self.btnProducts.setText('▦')
        self.btnProducts.setToolTip('产品库：管理产品资料并用于回复检索')
        self.btnProducts.clicked.connect(self.products_clicked.emit)

        self.btnClose = QToolButton()
        self.btnClose.setObjectName('btnClose')
        self.btnClose.setText('✕')
        self.btnClose.setToolTip('关闭侧边栏（退出应用）')
        self.btnClose.clicked.connect(self.close_clicked.emit)

        top = QHBoxLayout()
        top.addWidget(self.cmbPlatform)
        top.addStretch(1)
        top.addWidget(self.btnQuick)
        top.addWidget(self.btnProducts)
        top.addWidget(self.btnSettings)
        top.addWidget(self.btnClose)

        self.bannerNotice = QLabel('')
        self.bannerNotice.setObjectName('bannerNotice')
        self.bannerNotice.setWordWrap(True)
        self.bannerNotice.setStyleSheet(
            f'background: {PANEL}; color: {WARN}; border: 1px solid {LINE}; '
            f'border-radius: 6px; padding: 6px; font-size: 11px;'
        )
        self.bannerNotice.hide()

        # ---------- 会话区 ----------
        self.lblSessionTitle = QLabel('会话')
        self.lblSessionTitle.setStyleSheet(f'color: {SUB}; font-size: 11px; font-weight: 600;')

        self.btnRefreshSession = QToolButton()
        self.btnRefreshSession.setObjectName('btnRefreshSession')
        self.btnRefreshSession.setText('↻')
        self.btnRefreshSession.clicked.connect(self.refresh_sessions_clicked.emit)

        self.sessionHeader = QWidget()
        head_session = QHBoxLayout(self.sessionHeader)
        head_session.setContentsMargins(0, 0, 0, 0)
        head_session.addWidget(self.lblSessionTitle)
        head_session.addStretch(1)
        head_session.addWidget(self.btnRefreshSession)

        self.listSessions = QListWidget()
        self.listSessions.setObjectName('listSessions')
        self.listSessions.setFixedHeight(116)
        self.listSessions.setStyleSheet(
            f'QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; }}'
            f'QListWidget::item {{ padding: 6px 8px; }}'
            f'QListWidget::item:selected {{ background: {BRAND_DARK}; color: #ffffff; }}'
        )
        self.listSessions.itemClicked.connect(
            lambda item: self.session_selected.emit(item.data(Qt.UserRole) or '')
        )
        self.listSessions.itemSelectionChanged.connect(self._sync_selection_newno)

        self.lblSessionEmpty = QLabel('暂无会话')
        self.lblSessionEmpty.setObjectName('lblSessionEmpty')
        self.lblSessionEmpty.setStyleSheet(f'color: {SUB}; font-size: 12px;')
        self.lblSessionEmpty.setAlignment(Qt.AlignCenter)
        self.lblSessionEmpty.hide()

        # ---------- 消息区 ----------
        self.listMessages = QListWidget()
        self.listMessages.setObjectName('listMessages')
        self.listMessages.setStyleSheet(
            f'QListWidget {{ background: transparent; border: none; }} QListWidget::item {{ border: none; }}'
        )
        self.listMessages.setSpacing(2)

        self.lblMsgEmpty = QLabel('暂无消息')
        self.lblMsgEmpty.setObjectName('lblMsgEmpty')
        self.lblMsgEmpty.setStyleSheet(f'color: {SUB}; font-size: 12px;')
        self.lblMsgEmpty.setAlignment(Qt.AlignCenter)
        self.lblMsgEmpty.setWordWrap(True)

        self.lblMsgLoading = QLabel('加载中…')
        self.lblMsgLoading.setObjectName('lblMsgLoading')
        self.lblMsgLoading.setStyleSheet(f'color: {SUB}; font-size: 12px;')
        self.lblMsgLoading.setAlignment(Qt.AlignCenter)
        self.messageStack = QStackedWidget()
        self.messageStack.setObjectName('messageStack')
        self.messageStack.addWidget(self.listMessages)
        self.messageStack.addWidget(self.lblMsgEmpty)
        self.messageStack.addWidget(self.lblMsgLoading)
        self.messageStack.setCurrentWidget(self.lblMsgEmpty)

        # ---------- 回复区 ----------
        self.inputInstruction = QLineEdit()
        self.inputInstruction.setObjectName('inputInstruction')
        self.inputInstruction.setPlaceholderText('补充要求（可留空）：如 委婉拒绝')

        self.btnGenerate = QPushButton('生成回复')
        self.btnGenerate.setObjectName('btnGenerate')
        self.btnGenerate.setProperty('primary', True)
        self.btnGenerate.clicked.connect(self.generate_clicked.emit)

        row_inst = QHBoxLayout()
        row_inst.addWidget(self.inputInstruction)
        row_inst.addWidget(self.btnGenerate)

        self.editReply = QTextEdit()
        self.editReply.setObjectName('editReply')
        self.editReply.setPlaceholderText('点【生成回复】，结果会在这里，可手动改')
        self.editReply.setFixedHeight(90)
        self.editReply.textChanged.connect(self._sync_send_enabled)

        self.lblSource = QLabel('')
        self.lblSource.setObjectName('lblSource')
        self.lblSource.setStyleSheet(f'color: {SUB}; font-size: 11px;')
        self.lblProductContext = QLabel('')
        self.lblProductContext.setStyleSheet(f'color: {SUB}; font-size: 10px;')

        self.btnFill = QPushButton('填入')
        self.btnFill.setObjectName('btnFill')
        self.btnFill.clicked.connect(self.fill_clicked.emit)

        self.btnSend = QPushButton('发送')
        self.btnSend.setObjectName('btnSend')
        self.btnSend.setProperty('primary', True)
        self.btnSend.clicked.connect(self.send_clicked.emit)

        row_send = QHBoxLayout()
        row_send.addWidget(self.btnFill)
        row_send.addWidget(self.btnSend)
        row_send.addStretch(1)

        # ---------- 开关区 ----------
        self.btnDock = QPushButton('贴边（关）')
        self.btnDock.setObjectName('btnDock')
        self.btnDock.setCheckable(True)
        self.btnDock.setMinimumWidth(104)
        self.btnDock.setCursor(Qt.PointingHandCursor)
        self.btnDock.clicked.connect(lambda: self.dock_toggled.emit(self.btnDock.isChecked()))

        self.btnListen = QPushButton('实时监听（关）')
        self.btnListen.setObjectName('btnListen')
        self.btnListen.setCheckable(True)
        self.btnListen.setMinimumWidth(126)
        self.btnListen.setCursor(Qt.PointingHandCursor)
        self.btnListen.clicked.connect(lambda: self.listen_toggled.emit(self.btnListen.isChecked()))

        row_sw = QHBoxLayout()
        row_sw.addWidget(self.btnDock)
        row_sw.addWidget(self.btnListen)
        row_sw.addStretch(1)

        self.lblToast = QLabel('')
        self.lblToast.setObjectName('lblToast')
        self.lblToast.setAlignment(Qt.AlignCenter)
        self.lblToast.setWordWrap(True)
        self.lblToast.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)
        root.addLayout(top)
        root.addWidget(self.bannerNotice)
        root.addWidget(self.sessionHeader)
        root.addWidget(self.listSessions)
        root.addWidget(self.lblSessionEmpty)
        root.addWidget(self.messageStack, 1)
        root.addLayout(row_inst)
        root.addWidget(self.editReply)
        root.addWidget(self.lblSource)
        root.addWidget(self.lblProductContext)
        root.addLayout(row_send)
        root.addLayout(row_sw)
        root.addWidget(self.lblToast)

        self._sync_send_enabled()

    # ---------------- 状态条 ----------------
    def set_status(self, data: dict) -> None:
        mode = data.get('mode')
        old_theme_mode = self._theme_mode
        selected_adapter = data.get('selected_adapter') or mode
        index = self.cmbPlatform.findData(selected_adapter)
        if index >= 0 and index != self.cmbPlatform.currentIndex():
            self.cmbPlatform.blockSignals(True)
            self.cmbPlatform.setCurrentIndex(index)
            self.cmbPlatform.blockSignals(False)
        self._palette = palette(mode or 'qq')
        new_theme_mode = 'wechat' if mode in ('wechat', 'wechat4', 'wxauto') else 'qq'
        self._theme_mode = new_theme_mode
        if old_theme_mode != new_theme_mode and self._current_messages:
            self.set_messages(self._current_messages)
        # QQ and WeChat 4.x expose only the currently visible chat, not a real
        # conversation list. Hide the fake session panel for both adapters.
        self._hide_sessions = mode in ('qq', 'wechat4')
        self.sessionHeader.setVisible(not self._hide_sessions)
        self.listSessions.setVisible(not self._hide_sessions)
        self.lblSessionEmpty.setVisible(not self._hide_sessions and self.listSessions.count() == 0)
        self.bannerNotice.setStyleSheet(
            f"background:{self._palette['panel']}; color:{self._palette['warn']}; "
            f"border:1px solid {self._palette['line']}; border-radius:10px; padding:8px 10px; font-size:11px;"
        )
        self.lblSessionTitle.setStyleSheet(f"color:{self._palette['sub']};font-size:11px;")
        for label in (self.lblSessionEmpty, self.lblMsgEmpty, self.lblMsgLoading, self.lblSource, self.lblProductContext):
            label.setStyleSheet(f"color:{self._palette['sub']};font-size:11px;")
        detail = data.get('detail') or ''
        notice = ('' if (mode in ('wxauto', 'wechat4') and data.get('wechat_running')) else detail)
        self.bannerNotice.setText(notice)
        self.bannerNotice.setVisible(bool(notice))

        self._docking = bool(data.get('docking'))
        self._listen = bool(data.get('listen'))
        self.btnDock.setChecked(self._docking)
        self.btnDock.setText(f'贴边（{"开" if self._docking else "关"}）')
        self.btnListen.setChecked(self._listen)
        self.btnListen.setText(f'实时监听（{"开" if self._listen else "关"}）')
        self.listSessions.setStyleSheet(
            f"QListWidget {{ background: {self._palette['panel']}; border: 1px solid {self._palette['line']}; border-radius: 8px; }}"
            f"QListWidget::item {{ padding: 6px 8px; }}"
            f"QListWidget::item:selected {{ background: {self._palette['brand_dark']}; color: #ffffff; }}"
        )

    def set_docking(self, on: bool) -> None:
        self._docking = on
        self.btnDock.setChecked(on)
        self.btnDock.setText(f'贴边（{"开" if on else "关"}）')

    def set_listen(self, on: bool) -> None:
        self._listen = on
        self.btnListen.setChecked(on)
        self.btnListen.setText(f'实时监听（{"开" if on else "关"}）')

    def is_listening(self) -> bool:
        return self._listen

    def mark_new(self, chat: str) -> None:
        """非当前会话来新消息：给会话 chip 加红点（PRD §3.6 G）。"""
        for i in range(self.listSessions.count()):
            item = self.listSessions.item(i)
            if item.data(Qt.UserRole) == chat and '🔴' not in item.text():
                item.setText(f'{item.data(Qt.UserRole)} 🔴')
                return

    # ---------------- 会话区 ----------------
    def show_sessions_loading(self) -> None:
        self.lblSessionEmpty.setText('加载中…')
        self.lblSessionEmpty.show()

    def set_sessions(self, sessions: list) -> None:
        self.listSessions.clear()
        if not sessions:
            self.lblSessionEmpty.setText('暂无会话')
            self.lblSessionEmpty.setVisible(not self._hide_sessions)
            return
        self.lblSessionEmpty.hide()
        for s in sessions:
            name = s.get('name') or '(未知会话)'
            mark = ' 🔴' if s.get('isnew') else ''
            item = QListWidgetItem(name + mark)
            item.setData(Qt.UserRole, s.get('name') or '')
            tip = f'{s.get("time") or ""} {s.get("content") or ""}'.strip()
            item.setToolTip(tip or name)
            self.listSessions.addItem(item)
        self.listSessions.setVisible(not self._hide_sessions)

    def select_session(self, who: str) -> None:
        for i in range(self.listSessions.count()):
            item = self.listSessions.item(i)
            if item.data(Qt.UserRole) == who:
                self.listSessions.setCurrentItem(item)
                return

    # ---------------- 消息区 ----------------
    def show_messages_loading(self) -> None:
        if self._current_messages:
            self.messageStack.setCurrentWidget(self.listMessages)
        else:
            self.messageStack.setCurrentWidget(self.lblMsgLoading)

    def show_messages_empty(self, hint: str | None = None) -> None:
        if self._current_messages:
            self.messageStack.setCurrentWidget(self.listMessages)
            return
        self.lblMsgEmpty.setText(hint or '暂无消息')
        self.messageStack.setCurrentWidget(self.lblMsgEmpty)

    def set_messages(self, messages: list) -> None:
        self._current_messages = list(messages or [])
        self.listMessages.clear()
        self._bubbles.clear()
        if not messages:
            self.show_messages_empty()
            return
        self.messageStack.setCurrentWidget(self.listMessages)
        for m in messages:
            self._add_bubble(m)
        self.listMessages.scrollToBottom()

    def append_messages(self, messages: list) -> None:
        if not messages:
            return
        self._current_messages.extend(messages)
        self.messageStack.setCurrentWidget(self.listMessages)
        for m in messages:
            self._add_bubble(m)
        self.listMessages.scrollToBottom()

    def _add_bubble(self, m: dict) -> None:
        text = m.get('content') or ''
        mtype = m.get('type') or 'friend'
        item = QListWidgetItem(self.listMessages)

        if mtype in TYPE_SYS:
            w = QLabel(text)
            w.setWordWrap(True)
            w.setAlignment(Qt.AlignCenter)
            w.setStyleSheet(
                f"color: {self._palette['sub']}; font-size: 10px; background: {self._palette['brand_soft']}; "
                'border-radius: 9px; padding: 4px 8px;'
            )
            self._bubbles.append(w)
            self.listMessages.addItem(item)
            self.listMessages.setItemWidget(item, w)
            item.setSizeHint(w.sizeHint())
            return

        is_self = mtype == 'self'
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setStyleSheet(
            f"background: {self._palette['bubble_out']}; color: {self._palette['text']}; "
            f"border: 1px solid {self._palette['line']}; border-radius: 13px; padding: 8px 10px; font-size: 14px;"
            if is_self
            else f"background: {self._palette['panel']}; color: {self._palette['text']}; "
                 f"border: 1px solid {self._palette['line']}; border-radius: 13px; padding: 8px 10px; font-size: 14px;"
        )
        bubble.ensurePolished()
        lines = text.splitlines() or [text]
        natural_text_width = max(bubble.fontMetrics().horizontalAdvance(line) for line in lines)
        # Keep a comfortably readable minimum while limiting long messages to
        # the available sidebar width.
        bubble_width = min(280, max(210, natural_text_width + 24))
        bubble.setFixedWidth(bubble_width)
        wrapped_height = max(bubble.sizeHint().height(), bubble.heightForWidth(bubble_width))
        if wrapped_height > 0:
            bubble.setMinimumHeight(wrapped_height)

        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(4, 2, 4, 2)
        if is_self:
            lay.addStretch(1)
            lay.addWidget(bubble)
        else:
            col = QVBoxLayout()
            if m.get('sender'):
                name = QLabel(str(m['sender']))
                name.setStyleSheet(f"color: {self._palette['sub']}; font-size: 11px; background: transparent;")
                col.addWidget(name)
            col.addWidget(bubble)
            lay.addLayout(col)
            lay.addStretch(1)

        self._bubbles.append(container)
        self.listMessages.addItem(item)
        self.listMessages.setItemWidget(item, container)
        item.setSizeHint(container.sizeHint())

    # ---------------- 回复区 ----------------
    def set_reply(self, text: str, source: str = '', product_sources: list[dict] | None = None,
                  product_context_used: bool = True) -> None:
        self.editReply.setPlainText(text)
        if source == 'rule':
            self.lblSource.setText('来源：rule（规则兜底）')
        elif source:
            self.lblSource.setText(f'来源：{source}')
        else:
            self.lblSource.setText('')
        sources = product_sources or []
        if sources:
            labels = []
            for item in sources:
                label = str(item.get('document_name') or '产品资料')
                page = item.get('page')
                if page:
                    label += f' p.{page}'
                labels.append(label)
            unique = list(dict.fromkeys(labels))
            label = '产品依据：' if product_context_used else '检索到（本次兜底回复未采用）：'
            self.lblProductContext.setText(label + '；'.join(unique[:3]))
            self.lblProductContext.setToolTip('\n'.join(unique))
        else:
            self.lblProductContext.setText(
                f'回复检索：{self._selected_product_name} · 本次未命中资料'
                if self._selected_product_id else ''
            )
            self.lblProductContext.setToolTip('')

    def set_selected_product(self, product_id: int | None, name: str = '') -> None:
        self._selected_product_id = product_id
        self._selected_product_name = name
        self.lblProductContext.setText(f'回复检索：{name}' if product_id else '')
        self.lblProductContext.setToolTip('已选择产品资料；生成后会显示命中的文件来源。' if product_id else '')

    def reply_text(self) -> str:
        return self.editReply.toPlainText().strip()

    def instruction(self) -> str:
        return self.inputInstruction.text().strip()

    def set_generating(self, busy: bool) -> None:
        self.btnGenerate.setEnabled(not busy)
        self.btnGenerate.setText('生成中…' if busy else '生成回复')

    def set_sending(self, busy: bool) -> None:
        for b in (self.btnFill, self.btnSend):
            b.setEnabled(not busy)

    def _sync_send_enabled(self) -> None:
        ok = bool(self.reply_text())
        self.btnFill.setEnabled(ok)
        self.btnSend.setEnabled(ok)

    # ---------------- toast（非模态，不抢焦点）----------------
    def toast(self, message: str, level: str = 'info') -> None:
        color = {
            'ok': self._palette['brand'], 'warn': self._palette['warn'], 'error': self._palette['error']
        }.get(level, self._palette['text'])
        self.lblToast.setText(message)
        self.lblToast.setStyleSheet(
            f"background: {self._palette['panel']}; color: {color}; border: 1px solid {self._palette['line']}; "
            f'border-radius: 6px; padding: 6px; font-size: 11px;'
        )
        self.lblToast.show()
        QTimer.singleShot(2600, self.lblToast.hide)

    def _sync_selection_newno(self) -> None:
        """选中会话后去掉红点标记（PRD §3.6 B）。"""
        item = self.listSessions.currentItem()
        if item and item.text().endswith(' 🔴'):
            item.setText(item.text()[:-2].strip())
