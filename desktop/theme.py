"""QQ / WeChat visual tokens and PySide6 stylesheet copied from the previous UI baseline."""
from __future__ import annotations

QQ = {
    'bg': '#f4f7fb',
    'panel': '#ffffff',
    'line': '#e4eaf2',
    'text': '#1c2733',
    'sub': '#6c7b8a',
    'brand': '#2794e8',
    'brand_dark': '#177bc8',
    'brand_soft': '#eaf4fd',
    'bubble_out': '#e3f1ff',
    'warn': '#895908',
    'error': '#bd3d4a',
}

WECHAT = {
    'bg': '#f3f7f4',
    'panel': '#ffffff',
    'line': '#e1eae4',
    'text': '#1d2922',
    'sub': '#6d7b72',
    'brand': '#15ae68',
    'brand_dark': '#0b9255',
    'brand_soft': '#e8f6ed',
    'bubble_out': '#ddf3e3',
    'warn': '#895908',
    'error': '#bd3d4a',
}


def palette(mode: str = 'qq') -> dict[str, str]:
    """Return the old UI palette for the selected IM mode."""
    return dict(WECHAT if mode in {'wechat', 'wechat4', 'wxauto'} else QQ)


_QSS = """
QWidget {
    background: %(bg)s;
    color: %(text)s;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}
QLabel { background: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background: %(panel)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 10px;
    padding: 7px 9px;
    selection-background-color: %(brand)s;
}
QComboBox {
    background: %(panel)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 16px;
    padding: 5px 10px;
    selection-background-color: %(brand)s;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {
    border-color: %(brand)s;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border: none;
    background: transparent;
}
QComboBox QAbstractItemView {
    background: %(panel)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 10px;
    padding: 4px;
    outline: none;
    selection-background-color: %(brand_soft)s;
    selection-color: %(text)s;
}
QComboBox QAbstractItemView::item { min-height: 28px; padding: 5px 8px; border-radius: 6px; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid %(brand)s;
}
QPushButton {
    background: %(panel)s;
    border: 1px solid %(line)s;
    border-radius: 10px;
    padding: 7px 12px;
    color: %(text)s;
}
QPushButton:hover { border-color: %(brand)s; background: %(brand_soft)s; }
QPushButton:pressed { background: %(brand_soft)s; }
QPushButton:disabled { color: #89939e; border-color: %(line)s; background: #f0f2f4; }
QPushButton[primary="true"] {
    background: %(brand_dark)s;
    border: 1px solid %(brand_dark)s;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[primary="true"]:hover { background: %(brand)s; border-color: %(brand)s; }
QPushButton[primary="true"]:pressed { background: %(brand_dark)s; }
QPushButton[primary="true"]:disabled {
    background: #d7dde3;
    border-color: #d7dde3;
    color: #69727c;
}
QPushButton#btnDock, QPushButton#btnListen {
    min-height: 32px;
    border-radius: 17px;
    padding: 5px 14px;
    font-weight: 600;
}
QPushButton#btnDock:checked, QPushButton#btnListen:checked {
    background: %(brand_dark)s;
    border-color: %(brand_dark)s;
    color: #ffffff;
}
QPushButton#btnDock:checked:hover, QPushButton#btnListen:checked:hover {
    background: %(brand)s;
    border-color: %(brand)s;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: 10px;
    padding: 6px 8px;
    color: %(sub)s;
}
QToolButton:hover { color: %(text)s; background: %(brand_soft)s; }
QListWidget {
    background: %(panel)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 12px;
    outline: none;
    padding: 3px;
}
QListWidget::item { border: none; border-radius: 8px; padding: 5px 7px; }
QListWidget::item:selected { background: %(brand_dark)s; color: #ffffff; }
QListWidget::item:hover:!selected { background: %(brand_soft)s; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #cbd4dd; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #aebbc8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip {
    color: %(text)s;
    background: %(panel)s;
    border: 1px solid %(line)s;
    border-radius: 6px;
    padding: 5px 7px;
}
"""


def stylesheet(mode: str = 'qq') -> str:
    """Apply the same component system with the selected IM accent color."""
    return _QSS % palette(mode)


# Existing views import these names directly; keep QQ as the startup default.
BG = QQ['bg']
PANEL = QQ['panel']
LINE = QQ['line']
TEXT = QQ['text']
SUB = QQ['sub']
BRAND = QQ['brand']
BRAND_DARK = QQ['brand_dark']
WARN = QQ['warn']
ERROR = QQ['error']
QSS = stylesheet('qq')
