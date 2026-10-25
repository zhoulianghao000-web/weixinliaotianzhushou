"""集中读取环境变量（CLAUDE.md §9）。

优先级：系统环境变量 > .env 文件 > 默认值。
python-dotenv 为可选依赖，装了才自动加载 .env。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 打包后（PyInstaller）根目录 = exe 所在目录：.env / data / logs 都在它旁边。
# 注意：打包形态下 __file__ 指向临时解压目录（_MEIxxx），不能用它定位 .env。
ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, 'frozen', False)
    else Path(__file__).resolve().parent.parent
)
BACKEND = ROOT / 'backend'

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / '.env')
except Exception:
    pass

import os  # noqa: E402


def _str(name: str, default: str = '') -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, '') or default)
    except ValueError:
        return default


# ---- 服务 ----
PORT = _int('SIDEBAR_PORT', 8765)
HOST = _str('SIDEBAR_HOST', '127.0.0.1')

# ---- 侧边栏窗口 ----
SIDEBAR_TITLE = _str('SIDEBAR_TITLE', 'WeChat Sidebar')
SIDEBAR_WIDTH = _int('SIDEBAR_WIDTH', 380)
SIDEBAR_ADAPTER = _str('SIDEBAR_ADAPTER', 'auto').lower()

# ---- 贴边跟随的目标 IM 窗口（先按类名找，再按标题找）----
# 默认目标为 QQ NT（窗口标题 "QQ"）；若要贴微信 3.9，设 SIDEBAR_TARGET_CLASS=WeChatMainWndForPC
TARGET_CLASS = _str('SIDEBAR_TARGET_CLASS', '')
TARGET_TITLE = _str('SIDEBAR_TARGET_TITLE', 'QQ')

# ---- 数据库 ----
DB_PATH = Path(os.getenv('SIDEBAR_DB', '') or (ROOT / 'data' / 'sidebar.db'))
PRODUCTS_PATH = DB_PATH.parent / 'products'

# ---- LLM ----
OPENAI_API_KEY = _str('OPENAI_API_KEY', '')
OPENAI_BASE_URL = _str('OPENAI_BASE_URL', 'https://api.deepseek.com/v1')
OPENAI_MODEL = _str('OPENAI_MODEL', 'deepseek-chat')
