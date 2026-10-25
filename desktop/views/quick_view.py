"""V3.quick_replies — 话术库页（PRD §3 V3，实现决策 grill 2026-10-21）。

范围：查看（按分类筛选）+ 点一条插入回复框 + 新增 + 删除；不做编辑（要改就删了重加）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from theme import BRAND, BRAND_DARK, ERROR, LINE, PANEL, SUB, TEXT, WARN, palette


class QuickView(QWidget):
    back_clicked = Signal()
    insert_requested = Signal(str)  # 把话术文本带回 V1 的回复框

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('quickView')
        self._items: list[dict] = []
        self._palette = palette('qq')

        self.btnBack = QToolButton()
        self.btnBack.setText('←')
        self.btnBack.setToolTip('返回')
        self.btnBack.clicked.connect(self.back_clicked.emit)

        self.lblTitle = QLabel('话术库')
        self.lblTitle.setStyleSheet(f'font-size: 14px; font-weight: 600; color: {TEXT};')

        self.cmbTag = QComboBox()
        self.cmbTag.currentTextChanged.connect(lambda _: self.apply_filter())

        self.btnInsert = QPushButton('插入到回复框')
        self.btnInsert.setProperty('primary', True)
        self.btnInsert.clicked.connect(self._emit_insert)

        head = QHBoxLayout()
        head.addWidget(self.btnBack)
        head.addWidget(self.lblTitle)
        head.addStretch(1)
        head.addWidget(self.cmbTag)

        self.listQuick = QListWidget()
        self.listQuick.itemDoubleClicked.connect(lambda _: self._emit_insert())
        # 话术可能很长：必须换行，否则超出宽度被截断并冒出横向滚动条
        self.listQuick.setWordWrap(True)
        self.listQuick.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.listQuick.setTextElideMode(Qt.ElideNone)
        self.listQuick.setStyleSheet(
            f'QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; }}'
            f'QListWidget::item {{ padding: 8px; }}'
            f'QListWidget::item:selected {{ background: {BRAND_DARK}; color: #ffffff; }}'
        )

        self.btnDelete = QPushButton('删除选中')
        self.btnDelete.clicked.connect(self._emit_delete)

        row_btns = QHBoxLayout()
        row_btns.addWidget(self.btnInsert)
        row_btns.addWidget(self.btnDelete)
        row_btns.addStretch(1)

        # ---- 新增（分两行：内容够宽才好打字）----
        self.inputTag = QLineEdit()
        self.inputTag.setPlaceholderText('分类（可留空 = 常用）')
        self.inputContent = QLineEdit()
        self.inputContent.setPlaceholderText('话术内容')
        self.btnAdd = QPushButton('添加')
        self.btnAdd.clicked.connect(self._emit_add)

        row_tag = QHBoxLayout()
        row_tag.addWidget(self.inputTag, 1)

        row_add = QHBoxLayout()
        row_add.addWidget(self.inputContent, 1)
        row_add.addWidget(self.btnAdd)

        self.lblEmpty = QLabel('暂无话术')
        self.lblEmpty.setStyleSheet(f'color: {SUB}; font-size: 12px;')
        self.lblEmpty.setAlignment(Qt.AlignCenter)
        self.lblEmpty.hide()

        self.lblToast = QLabel('')
        self.lblToast.setWordWrap(True)
        self.lblToast.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)
        root.addLayout(head)
        root.addWidget(self.listQuick, 1)
        root.addWidget(self.lblEmpty)
        root.addLayout(row_btns)
        root.addLayout(row_add)
        root.addLayout(row_tag)
        root.addWidget(self.lblToast)
        self.set_mode('qq')

    # ---------------- 数据渲染 ----------------
    def set_mode(self, mode: str) -> None:
        self._palette = palette(mode)
        self.lblTitle.setStyleSheet(f"font-size:15px;font-weight:600;color:{self._palette['text']};")
        self.lblEmpty.setStyleSheet(f"color:{self._palette['sub']};font-size:12px;")
        self.listQuick.setStyleSheet(
            f"QListWidget{{background:{self._palette['panel']};border:1px solid {self._palette['line']};border-radius:12px;}}"
            'QListWidget::item{padding:9px;border-radius:8px;}'
            f"QListWidget::item:selected{{background:{self._palette['brand_dark']};color:#ffffff;}}"
        )

    def set_items(self, items: list) -> None:
        self._items = items or []
        tags = ['全部'] + sorted({str(it.get('tag') or '常用') for it in self._items})
        cur = self.cmbTag.currentText() or '全部'
        self.cmbTag.blockSignals(True)
        self.cmbTag.clear()
        self.cmbTag.addItems(tags)
        idx = self.cmbTag.findText(cur)
        self.cmbTag.setCurrentIndex(max(0, idx))
        self.cmbTag.blockSignals(False)
        self.apply_filter()

    def apply_filter(self) -> None:
        tag = self.cmbTag.currentText()
        self.listQuick.clear()
        shown = 0
        for it in self._items:
            t = str(it.get('tag') or '常用')
            if tag != '全部' and t != tag:
                continue
            text = (it.get('content') or '').strip()
            item = QListWidgetItem(f'[{t}] {text}')
            item.setData(Qt.UserRole, it.get('id'))
            item.setData(Qt.UserRole + 1, text)
            item.setToolTip(text)
            self.listQuick.addItem(item)
            shown += 1
        self.lblEmpty.setVisible(shown == 0)
        self.lblEmpty.setText('暂无话术（在下面添加一条）' if tag == '全部' else '该分类下暂无话术')

    # ---------------- 交互 ----------------
    def _selected(self) -> QListWidgetItem | None:
        return self.listQuick.currentItem()

    def _emit_insert(self) -> None:
        item = self._selected()
        if item is None:
            self.toast('先选中一条话术', 'warn')
            return
        self.insert_requested.emit(item.data(Qt.UserRole + 1) or '')

    def _emit_delete(self) -> None:
        item = self._selected()
        if item is None:
            self.toast('先选中一条话术', 'warn')
            return
        self.delete_requested.emit(item.data(Qt.UserRole))

    delete_requested = Signal(int)

    def _emit_add(self) -> None:
        content = self.inputContent.text().strip()
        if not content:
            self.toast('话术内容不能为空', 'warn')
            return
        self.add_requested.emit(self.inputTag.text().strip(), content)

    add_requested = Signal(str, str)

    def clear_add_form(self) -> None:
        self.inputContent.clear()
        self.inputTag.clear()

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
