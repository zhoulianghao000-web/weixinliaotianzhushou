"""V4 产品库：本地资料管理、目录导入和当前回复产品选择。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QInputDialog,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from theme import BRAND, ERROR, LINE, PANEL, SUB, TEXT, WARN, palette


class DocumentDropList(QListWidget):
    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        paths = [path for path in paths if path]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             '把文件或文件夹拖到这里导入\n也可以点击“导入文件夹”选择')
            painter.end()


class ProductView(QWidget):
    back_clicked = Signal()
    add_requested = Signal(str, str, str)
    update_requested = Signal(int, str, str, str)
    delete_product_requested = Signal(int)
    product_selected = Signal(int)
    use_product_requested = Signal(int, str)
    clear_product_requested = Signal()
    import_requested = Signal(int)
    import_paths_requested = Signal(object, list)
    delete_document_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('productView')
        self._products: list[dict] = []
        self._documents: list[dict] = []
        self._active_product_id: int | None = None
        self._importing = False
        self._removing = False
        self._palette = palette('qq')

        self.btnBack = QToolButton()
        self.btnBack.setText('←')
        self.btnBack.setToolTip('返回')
        self.btnBack.clicked.connect(self.back_clicked.emit)
        self.lblTitle = QLabel('产品库')
        self.lblTitle.setStyleSheet(f'font-size:14px;font-weight:600;color:{TEXT};')
        head = QHBoxLayout()
        head.addWidget(self.btnBack)
        head.addWidget(self.lblTitle)
        head.addStretch(1)

        self.listProducts = QListWidget()
        self.listProducts.setObjectName('listProducts')
        self.listProducts.setFixedHeight(132)
        self.listProducts.setStyleSheet(
            f'QListWidget {{ background:{PANEL}; border:1px solid {LINE}; border-radius:8px; }}'
            'QListWidget::item { padding:6px 8px; }'
            f'QListWidget::item:selected {{ background:{BRAND}; color:#ffffff; }}'
        )
        self.listProducts.itemSelectionChanged.connect(self._on_selection_changed)

        self.inputName = QLineEdit()
        self.inputName.setPlaceholderText('产品名称（必填）')
        self.inputCategory = QLineEdit()
        self.inputCategory.setPlaceholderText('产品分类（可留空）')
        self.inputSummary = QTextEdit()
        self.inputSummary.setPlaceholderText('产品摘要：功能、适用人群、核心信息等；可留空')
        self.inputSummary.setFixedHeight(58)

        form_btns = QHBoxLayout()
        self.btnAdd = QPushButton('新增产品')
        self.btnAdd.clicked.connect(self._add_product)
        self.btnSave = QPushButton('保存修改')
        self.btnSave.clicked.connect(self._save_product)
        self.btnDeleteProduct = QPushButton('删除产品')
        self.btnDeleteProduct.clicked.connect(self._delete_product)
        for button in (self.btnAdd, self.btnSave, self.btnDeleteProduct):
            form_btns.addWidget(button)

        use_row = QHBoxLayout()
        self.btnUse = QPushButton('用于回复')
        self.btnUse.setProperty('primary', True)
        self.btnUse.clicked.connect(self._use_product)
        self.btnClearUse = QPushButton('不使用产品资料')
        self.btnClearUse.clicked.connect(self.clear_product_requested.emit)
        use_row.addWidget(self.btnUse)
        use_row.addWidget(self.btnClearUse)

        doc_head = QHBoxLayout()
        self.lblDocuments = QLabel('产品资料')
        self.lblDocuments.setStyleSheet(f'color:{SUB};font-size:11px;')
        self.btnImport = QPushButton('导入文件夹')
        self.btnImport.setToolTip('复制选中文件夹到产品库 RAW 层，并在本机提取文字')
        self.btnImport.clicked.connect(self._import_folder)
        doc_head.addWidget(self.lblDocuments)
        doc_head.addStretch(1)
        doc_head.addWidget(self.btnImport)

        self.listDocuments = DocumentDropList()
        self.listDocuments.setObjectName('listProductDocuments')
        self.listDocuments.setStyleSheet(
            f'QListWidget {{ background:{PANEL}; border:1px solid {LINE}; border-radius:8px; }}'
            'QListWidget::item { padding:5px 7px; }'
            f'QListWidget::item:selected {{ background:{self._palette["brand_soft"]}; color:{TEXT}; }}'
        )
        self.listDocuments.paths_dropped.connect(self._on_paths_dropped)
        self.listDocuments.itemSelectionChanged.connect(self._on_document_selection)

        self.lblDocumentDetail = QLabel('原始文件保留在本机；纯文本 RAG 使用提取后的内容。')
        self.lblDocumentDetail.setWordWrap(True)
        self.lblDocumentDetail.setStyleSheet(f'color:{SUB};font-size:10px;')

        doc_actions = QHBoxLayout()
        self.btnDeleteDocument = QPushButton('移除选中资料')
        self.btnDeleteDocument.clicked.connect(self._delete_document)
        doc_actions.addWidget(self.btnDeleteDocument)
        doc_actions.addStretch(1)

        self.lblToast = QLabel('')
        self.lblToast.setWordWrap(True)
        self.lblToast.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addLayout(head)
        root.addWidget(self.listProducts)
        root.addWidget(self.inputName)
        root.addWidget(self.inputCategory)
        root.addWidget(self.inputSummary)
        root.addLayout(form_btns)
        root.addLayout(use_row)
        root.addLayout(doc_head)
        root.addWidget(self.listDocuments, 1)
        root.addWidget(self.lblDocumentDetail)
        root.addLayout(doc_actions)
        root.addWidget(self.lblToast)
        self._sync_buttons()
        self.set_mode('qq')

    def set_products(self, products: list[dict]) -> None:
        selected_id = self.selected_product_id()
        self._products = products or []
        self.listProducts.blockSignals(True)
        self.listProducts.clear()
        selected_item = None
        for product in self._products:
            product_id = int(product['id'])
            name = str(product.get('name') or '未命名产品')
            category = str(product.get('category') or '')
            count = int(product.get('document_count') or 0)
            suffix = f' · {category}' if category else ''
            item = QListWidgetItem(f'{name}{suffix}  ({count})')
            item.setData(Qt.UserRole, product_id)
            item.setToolTip(str(product.get('summary') or name))
            self.listProducts.addItem(item)
            if product_id == selected_id or (selected_item is None and not selected_id):
                selected_item = item
        if selected_item:
            self.listProducts.setCurrentItem(selected_item)
        self.listProducts.blockSignals(False)
        self._on_selection_changed()

    def selected_product_id(self) -> int | None:
        item = self.listProducts.currentItem()
        return int(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) is not None else None

    def selected_product(self) -> dict | None:
        product_id = self.selected_product_id()
        return next((item for item in self._products if int(item['id']) == product_id), None)

    def set_documents(self, documents: list[dict]) -> None:
        self._documents = documents or []
        self.listDocuments.clear()
        for document in self._documents:
            status = document.get('status') or 'raw_only'
            chunk_count = int(document.get('chunk_count') or 0)
            marker = {'indexed': '已索引', 'needs_review': '需复核', 'raw_only': '仅原件', 'error': '处理失败'}.get(status, status)
            title = f'[{marker}] {document.get("name") or "未命名文件"}'
            if chunk_count:
                title += f' · {chunk_count}段'
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, int(document['id']))
            item.setData(Qt.UserRole + 1, document.get('error') or '')
            item.setToolTip(document.get('relative_path') or document.get('name') or '')
            self.listDocuments.addItem(item)
        self.lblDocuments.setText(f'产品资料（{len(self._documents)}）')
        self._on_document_selection()

    def set_active_product(self, product_id: int | None, name: str = '') -> None:
        self._active_product_id = product_id
        self.lblTitle.setText(f'产品库 · 当前用于回复：{name}' if product_id else '产品库')

    def set_importing(self, busy: bool) -> None:
        self._importing = busy
        self.btnImport.setEnabled(not busy)
        self.btnImport.setText('正在本机处理…' if busy else '导入文件夹')

    def set_removing(self, busy: bool) -> None:
        self._removing = busy
        self.btnDeleteDocument.setEnabled(not busy and self.listDocuments.currentItem() is not None)
        if busy:
            self.toast('正在从产品库移除…')

    def clear_form(self) -> None:
        self.inputName.clear()
        self.inputCategory.clear()
        self.inputSummary.clear()

    def toast(self, message: str, level: str = 'ok') -> None:
        color = {
            'ok': self._palette['brand'], 'warn': self._palette['warn'], 'error': self._palette['error']
        }.get(level, self._palette['text'])
        self.lblToast.setStyleSheet(
            f"background:{self._palette['panel']};color:{color};border:1px solid {self._palette['line']};"
            'border-radius:6px;padding:5px;font-size:10px;'
        )
        self.lblToast.setText(message)
        self.lblToast.show()

    def set_mode(self, mode: str) -> None:
        self._palette = palette(mode)
        self.lblTitle.setStyleSheet(f"font-size:15px;font-weight:600;color:{self._palette['text']};")
        self.lblDocuments.setStyleSheet(f"color:{self._palette['sub']};font-size:11px;")
        self.listProducts.setStyleSheet(
            f"QListWidget {{ background:{self._palette['panel']}; border:1px solid {self._palette['line']}; border-radius:12px; }}"
            'QListWidget::item { padding:7px 8px; border-radius:8px; }'
            f"QListWidget::item:selected {{ background:{self._palette['brand']}; color:#ffffff; }}"
        )
        self.listDocuments.setStyleSheet(
            f"QListWidget {{ background:{self._palette['panel']}; border:1px solid {self._palette['line']}; border-radius:12px; }}"
            'QListWidget::item { padding:6px 8px; border-radius:8px; }'
            f"QListWidget::item:selected {{ background:{self._palette['brand_soft']}; color:{self._palette['text']}; }}"
        )
        self._on_document_selection()

    def _on_selection_changed(self) -> None:
        product = self.selected_product()
        if product:
            self.inputName.setText(product.get('name') or '')
            self.inputCategory.setText(product.get('category') or '')
            self.inputSummary.setPlainText(product.get('summary') or '')
            self.product_selected.emit(int(product['id']))
        else:
            self.inputName.clear()
            self.inputCategory.clear()
            self.inputSummary.clear()
            self.set_documents([])
        self._sync_buttons()

    def _on_document_selection(self) -> None:
        item = self.listDocuments.currentItem()
        if item:
            detail = str(item.data(Qt.UserRole + 1) or '')
            self.lblDocumentDetail.setText(detail or '已保留本机原件；回复时将从索引文字中检索。')
            self.lblDocumentDetail.setStyleSheet(
                f'color:{WARN if detail else SUB};font-size:10px;'
            )
        else:
            self.lblDocumentDetail.setText('原始文件保留在本机；纯文本 RAG 使用提取后的内容。')
            self.lblDocumentDetail.setStyleSheet(f'color:{SUB};font-size:10px;')
        self.btnDeleteDocument.setEnabled(bool(item) and not self._removing)

    def _sync_buttons(self) -> None:
        has_product = self.selected_product_id() is not None
        self.btnSave.setEnabled(has_product)
        self.btnDeleteProduct.setEnabled(has_product)
        self.btnUse.setEnabled(has_product)
        self.btnImport.setEnabled(not self._importing)
        self.btnDeleteDocument.setEnabled(self.listDocuments.currentItem() is not None and not self._removing)

    def _add_product(self) -> None:
        name = self.inputName.text().strip()
        if not name:
            name, accepted = QInputDialog.getText(self, '新增产品', '请输入产品名称：')
            if not accepted or not name.strip():
                return
            name = name.strip()
            self.inputName.setText(name)
        self.add_requested.emit(name, self.inputCategory.text().strip(), self.inputSummary.toPlainText().strip())

    def _save_product(self) -> None:
        product_id = self.selected_product_id()
        name = self.inputName.text().strip()
        if not product_id or not name:
            self.toast('产品名称不能为空', 'warn')
            return
        self.update_requested.emit(product_id, name, self.inputCategory.text().strip(), self.inputSummary.toPlainText().strip())

    def _delete_product(self) -> None:
        product_id = self.selected_product_id()
        if product_id:
            self.delete_product_requested.emit(product_id)

    def _use_product(self) -> None:
        product = self.selected_product()
        if product:
            self.use_product_requested.emit(int(product['id']), str(product.get('name') or ''))

    def _import_folder(self) -> None:
        product_id = self.selected_product_id()
        self.import_requested.emit(product_id or 0)

    def _on_paths_dropped(self, paths: list[str]) -> None:
        self.import_paths_requested.emit(self.selected_product_id() or 0, paths)

    def _delete_document(self) -> None:
        product_id = self.selected_product_id()
        item = self.listDocuments.currentItem()
        if product_id and item:
            self.delete_document_requested.emit(product_id, int(item.data(Qt.UserRole)))
        else:
            self.toast('请先在资料列表中选中一项', 'warn')
