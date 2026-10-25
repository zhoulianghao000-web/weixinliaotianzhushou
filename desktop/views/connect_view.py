"""V0.connect — 连接状态页（PRD §3 V0）。

三态必须齐全：
- 加载中：灰色圆点 + "正在连接后端…"
- 错误：红点 + 可读文案（绝不显示堆栈）
- 正常：绿/黄点 + 后端返回的 detail
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget, QStyleFactory

from theme import BRAND, ERROR, LINE, PANEL, SUB, TEXT, WARN, palette

HINT_TEXT = (
    '选择 QQ、微信或自动识别。QQ 与微信 4.x 使用本地 OCR 读取当前可见聊天区；\n'
    '生成结果先放入回复框，填入或发送由你确认。请保持目标聊天窗口可见。'
)


class ConnectView(QWidget):
    """用途：后端未就绪 / 微信未运行 / 降级 mock 时的兜底页。"""

    retry_clicked = Signal()
    continue_clicked = Signal()
    refresh_requested = Signal()
    close_clicked = Signal()
    platform_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('connectView')
        self._palette = palette('qq')

        self.lblIcon = QLabel('●')
        self.lblIcon.setObjectName('lblIcon')
        self.lblIcon.setStyleSheet(f'color: {SUB}; font-size: 20px;')

        self.lblTitle = QLabel('正在连接后端…')
        self.lblTitle.setObjectName('lblTitle')
        self.lblTitle.setStyleSheet(f'font-size: 15px; font-weight: 600; color: {TEXT};')

        self.cmbPlatform = QComboBox()
        self.cmbPlatform.setObjectName('cmbPlatform')
        self.cmbPlatform.setStyle(QStyleFactory.create('Fusion'))
        self.cmbPlatform.addItem('自动', 'auto')
        self.cmbPlatform.addItem('QQ', 'qq')
        self.cmbPlatform.addItem('微信', 'wechat4')
        self.cmbPlatform.setToolTip('选择要连接的聊天平台')
        self.cmbPlatform.currentIndexChanged.connect(
            lambda _index: self.platform_changed.emit(self.cmbPlatform.currentData())
        )

        self.btnClose = QToolButton()
        self.btnClose.setObjectName('btnClose')
        self.btnClose.setText('✕')
        self.btnClose.setToolTip('关闭侧边栏（退出应用）')
        self.btnClose.clicked.connect(self.close_clicked.emit)

        head = QHBoxLayout()
        head.addWidget(self.lblIcon)
        head.addSpacing(6)
        head.addWidget(self.lblTitle)
        head.addWidget(self.cmbPlatform)
        head.addStretch(1)
        head.addWidget(self.btnClose)

        self.lblDetail = QLabel('正在请求 /api/status')
        self.lblDetail.setObjectName('lblDetail')
        self.lblDetail.setStyleSheet(f'color: {SUB};')
        self.lblDetail.setWordWrap(True)

        self.lblHint = QLabel(HINT_TEXT)
        self.lblHint.setObjectName('lblHint')
        self.lblHint.setStyleSheet(
            f'color: {SUB}; background: {PANEL}; border: 1px solid {LINE}; '
            f'border-radius: 10px; padding: 9px 10px; font-size: 11px;'
        )
        self.lblHint.setWordWrap(True)

        self.btnRetry = QPushButton('重新连接')
        self.btnRetry.setObjectName('btnRetry')
        self.btnRetry.setProperty('primary', True)
        self.btnRetry.clicked.connect(self.retry_clicked.emit)

        self.btnContinue = QPushButton('仍以演示模式进入')
        self.btnContinue.setObjectName('btnContinue')
        self.btnContinue.clicked.connect(self.continue_clicked.emit)

        btns = QHBoxLayout()
        btns.addWidget(self.btnRetry)
        btns.addWidget(self.btnContinue)
        btns.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        root.addLayout(head)
        root.addWidget(self.lblDetail)
        root.addWidget(self.lblHint)
        root.addLayout(btns)
        root.addStretch(1)

    # ---------------- 三态渲染 ----------------
    def set_mode(self, mode: str) -> None:
        self._palette = palette(mode)
        self.lblTitle.setStyleSheet(f"font-size:15px;font-weight:600;color:{self._palette['text']};")
        self.lblDetail.setStyleSheet(f"color:{self._palette['sub']};")
        self.lblHint.setStyleSheet(
            f"color:{self._palette['sub']};background:{self._palette['panel']}; "
            f"border:1px solid {self._palette['line']};border-radius:10px;padding:9px 10px;font-size:11px;"
        )

    def show_loading(self) -> None:
        self.lblIcon.setStyleSheet(f"color:{self._palette['sub']};font-size:20px;")
        self.lblTitle.setText('正在连接后端…')
        self.lblDetail.setText('正在请求 /api/status')
        self.btnRetry.setEnabled(False)
        self.btnContinue.hide()

    def show_error(self, message: str) -> None:
        """网络异常 / 信封异常：给可读文案，不显示堆栈。"""
        self.lblIcon.setStyleSheet(f"color:{self._palette['error']};font-size:20px;")
        self.lblTitle.setText('后端未启动')
        self.lblDetail.setText(message)
        self.btnRetry.setEnabled(True)
        self.btnContinue.hide()

    def show_status(self, data: dict) -> None:
        """GET /api/status 成功：按 PRD §3 V0 状态映射表渲染。"""
        mode = data.get('mode')
        index = self.cmbPlatform.findData(data.get('selected_adapter') or mode)
        if index >= 0 and index != self.cmbPlatform.currentIndex():
            self.cmbPlatform.blockSignals(True)
            self.cmbPlatform.setCurrentIndex(index)
            self.cmbPlatform.blockSignals(False)
        nickname = data.get('nickname') or 'QQ 侧边栏'
        running = bool(data.get('wechat_running'))

        if mode == 'qq' and running:
            self.lblIcon.setStyleSheet(f"color:{self._palette['brand']};font-size:20px;")
            self.lblTitle.setText('QQ 已就绪（本地 OCR）')
            self.btnContinue.hide()
        elif mode == 'qq':
            self.lblIcon.setStyleSheet(f"color:{self._palette['warn']};font-size:20px;")
            self.lblTitle.setText('QQ 未运行')
            self.btnContinue.show()
        elif mode == 'wechat4' and data.get('client_found') and not running:
            self.lblIcon.setStyleSheet(f"color:{self._palette['warn']};font-size:20px;")
            self.lblTitle.setText('微信窗口已最小化')
            self.btnContinue.show()
        elif mode in ('wxauto', 'wechat4') and running:
            self.lblIcon.setStyleSheet(f"color:{self._palette['brand']};font-size:20px;")
            self.lblTitle.setText(f'已连接 {nickname}' if mode == 'wxauto' else '微信 4.x 已就绪（本地 OCR）')
            self.btnContinue.hide()
        elif mode in ('wxauto', 'wechat4'):
            self.lblIcon.setStyleSheet(f"color:{self._palette['warn']};font-size:20px;")
            self.lblTitle.setText('微信未运行')
            self.btnContinue.show()
        else:
            self.lblIcon.setStyleSheet(f"color:{self._palette['warn']};font-size:20px;")
            self.lblTitle.setText('演示数据')
            self.btnContinue.show()

        self.lblDetail.setText(data.get('detail') or '')
        self.btnRetry.setEnabled(True)
