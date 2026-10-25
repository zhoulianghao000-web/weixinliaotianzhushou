"""统一响应信封 + 请求模型（CLAUDE.md §5）。

所有接口一律返回：
    成功 {"code": 0, "message": "ok", "data": {...}, "ts": 1760000000}
    错误 {"code": 2001, "message": "未选择会话", "data": null, "ts": 1760000000}
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

# ---- 错误码（CLAUDE.md §5 错误码表）----
CODE_OK = 0
ERR_WECHAT_UNAVAILABLE = 1000
ERR_READ_MESSAGE = 1001
ERR_NO_CHAT = 2001
ERR_LLM_FAILED = 3001
ERR_BAD_PARAM = 4001
ERR_INTERNAL = 5000

ERR_MESSAGES = {
    CODE_OK: 'ok',
    ERR_WECHAT_UNAVAILABLE: '微信不可用',
    ERR_READ_MESSAGE: '读取消息失败',
    ERR_NO_CHAT: '未选择会话',
    ERR_LLM_FAILED: '大模型调用失败',
    ERR_BAD_PARAM: '参数错误',
    ERR_INTERNAL: '内部错误',
}


def ok(data: Any = None, message: str = 'ok') -> dict:
    """成功模板。data 为 None 时仍返回 null（保持字段齐全）。"""
    return {'code': CODE_OK, 'message': message, 'data': data, 'ts': int(time.time())}


def fail(code: int, message: str | None = None, data: Any = None) -> dict:
    """错误模板。message 未给时用错误码默认文案。"""
    return {
        'code': code,
        'message': message or ERR_MESSAGES.get(code, '错误'),
        'data': data,
        'ts': int(time.time()),
    }


def degrade(data: Any, reason: str) -> dict:
    """降级成功：code 仍为 0，但 data.degraded=True 并附带原因。"""
    return {'code': CODE_OK, 'message': f'已降级：{reason}', 'data': data, 'ts': int(time.time())}


# ---- 请求体模型（CLAUDE.md §4）----
class ReplyIn(BaseModel):
    who: str | None = None
    instruction: str = ''
    messages: list[dict] | None = None
    product_id: int | None = Field(default=None, ge=1)


class SendIn(BaseModel):
    who: str | None = None
    text: str


class DockIn(BaseModel):
    on: bool = True
    width: int | None = None


class ListenIn(BaseModel):
    on: bool = False


class SettingsIn(BaseModel):
    """PUT /api/settings：字段全部可选，只更新传入的项（部分即时生效，适配器热切换）。"""
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    width: int | None = Field(default=None, ge=300, le=600)
    adapter: str | None = None


class QuickReplyIn(BaseModel):
    tag: str = ''
    content: str


class ProductIn(BaseModel):
    name: str
    category: str = ''
    summary: str = ''


class ProductUpdateIn(BaseModel):
    name: str | None = None
    category: str | None = None
    summary: str | None = None


class ProductFolderImportIn(BaseModel):
    source_dir: str


class MessageOut(BaseModel):
    type: str
    sender: str = ''
    content: str = ''
    id: str | None = None
    msg_ts: str | None = None


LIMIT_MIN, LIMIT_MAX = 1, 200


def clamp_limit(limit: int | None, default: int = 60) -> int:
    if limit is None:
        return default
    return max(LIMIT_MIN, min(LIMIT_MAX, limit))
