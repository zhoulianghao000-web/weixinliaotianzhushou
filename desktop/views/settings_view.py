"""V2.settings — 设置页（PRD §3 V2，实现决策 grill 2026-10-21）。

生效策略（全部即时）：
- Key / BaseURL / 模型名：后端每次生成前从 DB 读 → 保存即生效
- 窗口宽度：保存后 UI 立即 setFixedWidth，贴边线程同步改
- 适配器：热切换（后端惰性重建 + 自动关监听，在途请求跑完不报错）
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QStyleFactory,
)

from theme import BRAND, ERROR, LINE, PANEL, SUB, TEXT, WARN, palette

ADAPTER_TIPS = {
    'qq': 'QQ：本地截图 OCR 读取左侧白色来信气泡，剪贴板填入',
    'wechat4': '微信 4.x：本地截图 OCR 读取白色气泡，剪贴板填入',
    'wxauto': '微信 3.9.11.17（旧版 wxauto 兼容）',
    'mock': '演示数据，不碰任何 IM 窗口',
    'auto': '按当前前台窗口自动选择 QQ 或微信 4.x',
}


class SettingsView(QWidget):
    back_clicked = Signal()
    save_clicked = Signal()  # window.py 收集表单后调 API

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('settingsView')
        self._palette = palette('qq')

        self.btnBack = QToolButton()
        self.btnBack.setText('←')
        self.btnBack.setToolTip('返回')
        self.btnBack.clicked.connect(self.back_clicked.emit)

        self.lblTitle = QLabel('设置')
        self.lblTitle.setStyleSheet(f'font-size: 14px; font-weight: 600; color: {TEXT};')

        top = QVBoxLayout()
        head = self._row(self.btnBack, self.lblTitle)
        top.addLayout(head)

        # ---- 表单 ----
        self.inputApiKey = QLineEdit()
        self.inputApiKey.setEchoMode(QLineEdit.Password)
        self.inputApiKey.setPlaceholderText('sk-…（留空 = 未配置，走规则兜底）')

        self.inputBaseUrl = QLineEdit()
        self.inputBaseUrl.setPlaceholderText('https://api.deepseek.com/v1')

        self.inputModel = QLineEdit()
        self.inputModel.setPlaceholderText('deepseek-chat')

        self.spinWidth = QSpinBox()
        self.spinWidth.setRange(300, 600)
        self.spinWidth.setSingleStep(10)
        self.spinWidth.setSuffix(' px')

        self.cmbAdapter = QComboBox()
        self.cmbAdapter.setObjectName('cmbAdapter')
        self.cmbAdapter.setStyle(QStyleFactory.create('Fusion'))
        display_names = {
            'auto': '自动选择',
            'qq': 'QQ',
            'wechat4': '微信 4.x（本地 OCR）',
            'wxauto': '微信 3.9（旧版）',
            'mock': '演示模式',
        }
        for name, tip in ADAPTER_TIPS.items():
            self.cmbAdapter.addItem(display_names.get(name, name), name)
            self.cmbAdapter.setItemData(self.cmbAdapter.count() - 1, tip, 1)  # Qt.ToolTipRole
        self.cmbAdapter.currentIndexChanged.connect(lambda _index: self._adapter_tip(self.cmbAdapter.currentData()))

        self.lblAdapterTip = QLabel('')
        self.lblAdapterTip.setWordWrap(True)
        self.lblAdapterTip.setStyleSheet(f'color: {SUB}; font-size: 11px;')

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        form.addRow('API Key', self.inputApiKey)
        form.addRow('Base URL', self.inputBaseUrl)
        form.addRow('模型名', self.inputModel)
        form.addRow('窗口宽度', self.spinWidth)
        form.addRow('适配器', self.cmbAdapter)
        form.addRow('', self.lblAdapterTip)

        self.btnSave = QPushButton('保存')
        self.btnSave.setProperty('primary', True)
        self.btnSave.clicked.connect(self.save_clicked.emit)

        self.lblToast = QLabel('')
        self.lblToast.setWordWrap(True)
        self.lblToast.hide()

        top.addLayout(form)
        top.addWidget(self.btnSave)
        top.addWidget(self.lblToast)
        top.addStretch(1)
        top.setContentsMargins(14, 12, 14, 12)
        top.setSpacing(10)
        self.setLayout(top)

    # ---------------- 渲染 ----------------
    def set_mode(self, mode: str) -> None:
        self._palette = palette(mode)
        self.lblTitle.setStyleSheet(f"font-size:15px;font-weight:600;color:{self._palette['text']};")
        self.lblAdapterTip.setStyleSheet(f"color:{self._palette['sub']};font-size:11px;")

    def _row(self, *widgets) -> object:
        from PySide6.QtWidgets import QHBoxLayout

        lay = QHBoxLayout()
        for w in widgets:
            lay.addWidget(w)
        lay.addStretch(1)
        return lay

    def _adapter_tip(self, name: str) -> None:
        self.lblAdapterTip.setText(ADAPTER_TIPS.get(name, ''))

    def set_settings(self, data: dict) -> None:
        self.inputApiKey.setText(data.get('api_key') or '')
        self.inputBaseUrl.setText(data.get('base_url') or '')
        self.inputModel.setText(data.get('model') or '')
        self.spinWidth.setValue(int(data.get('width') or 380))
        idx = self.cmbAdapter.findData(data.get('adapter') or 'auto')
        self.cmbAdapter.setCurrentIndex(max(0, idx))
        self._adapter_tip(self.cmbAdapter.currentData())

    def values(self) -> dict:
        return {
            'api_key': self.inputApiKey.text().strip(),
            'base_url': self.inputBaseUrl.text().strip(),
            'model': self.inputModel.text().strip(),
            'width': self.spinWidth.value(),
            'adapter': self.cmbAdapter.currentData(),
        }

    def set_saving(self, busy: bool) -> None:
        self.btnSave.setEnabled(not busy)
        self.btnSave.setText('保存中…' if busy else '保存')

    def toast(self, message: str, level: str = 'ok') -> None:
        color = {
            'ok': self._palette['brand'], 'warn': self._palette['warn'],
            'error': self._palette['error'],
        }.get(level, self._palette['text'])
        self.lblToast.setStyleSheet(
            f"background:{self._palette['panel']};color:{color};border:1px solid {self._palette['line']};"
            'border-radius:10px;padding:7px 9px;font-size:11px;'
        )
        self.lblToast.setText(message)
        self.lblToast.show()
