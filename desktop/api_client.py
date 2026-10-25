"""后端 HTTP 客户端 + 异步 Worker（PRD §1 接口契约）。

约定：
- 所有接口返回统一信封 {code, message, data, ts}
- code != 0 视为业务错误，抛 ApiError
- 网络异常同样抛 ApiError（code=None），UI 侧统一走"后端未连接"提示
"""
from __future__ import annotations

import os

import httpx
from PySide6.QtCore import QThread, Signal

DEFAULT_BASE = 'http://127.0.0.1:8765'


class ApiError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


class ApiClient:
    def __init__(self, base: str | None = None):
        self.base = (base or os.getenv('SIDEBAR_API') or DEFAULT_BASE).rstrip('/')

    # ---------- 底层 ----------
    def _request(self, method: str, path: str, *, params=None, json=None, timeout: float = 10.0) -> dict:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, self.base + path, params=params, json=json)
        except Exception as e:
            raise ApiError(f'后端未连接（{self.base}）', code=None) from e
        try:
            envelope = resp.json()
        except Exception as e:
            raise ApiError(f'响应解析失败：HTTP {resp.status_code}', code=None) from e
        if not isinstance(envelope, dict) or 'code' not in envelope:
            raise ApiError('响应格式异常：缺少 code 字段', code=None)
        return envelope

    @staticmethod
    def unwrap(envelope: dict):
        code = envelope.get('code')
        if code != 0:
            raise ApiError(envelope.get('message') or '请求失败', code=code)
        return envelope.get('data') or {}

    # ---------- 接口（PRD §1）----------
    def status(self, timeout: float = 3.0) -> dict:
        return self.unwrap(self._request('GET', '/api/status', timeout=timeout))

    def sessions(self) -> dict:
        return self.unwrap(self._request('GET', '/api/sessions'))

    def messages(self, who: str | None = None, limit: int = 60) -> dict:
        params = {}
        if who:
            params['who'] = who
        if limit:
            params['limit'] = limit
        return self.unwrap(self._request('GET', '/api/messages', params=params))

    def reply(self, who: str | None, instruction: str = '', messages: list | None = None,
              product_id: int | None = None) -> dict:
        body = {'who': who, 'instruction': instruction, 'messages': messages}
        if product_id is not None:
            body['product_id'] = product_id
        return self.unwrap(
            self._request(
                'POST',
                '/api/reply',
                json=body,
                timeout=30.0,
            )
        )

    def fill(self, who: str, text: str) -> dict:
        return self.unwrap(self._request('POST', '/api/fill', json={'who': who, 'text': text}))

    def send(self, who: str, text: str) -> dict:
        return self.unwrap(self._request('POST', '/api/send', json={'who': who, 'text': text}))

    def dock(self, on: bool, width: int | None = None) -> dict:
        body = {'on': on}
        if width:
            body['width'] = width
        return self.unwrap(self._request('POST', '/api/dock', json=body))

    def listen(self, on: bool) -> dict:
        return self.unwrap(self._request('POST', '/api/listen', json={'on': on}))

    # ---------- V2 设置页 / V3 话术库（CLAUDE.md §4.11 / §4.12）----------
    def get_settings(self) -> dict:
        return self.unwrap(self._request('GET', '/api/settings'))

    def put_settings(self, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None}
        return self.unwrap(self._request('PUT', '/api/settings', json=body))

    def quick_replies(self) -> dict:
        return self.unwrap(self._request('GET', '/api/quick-replies'))

    def add_quick_reply(self, tag: str, content: str) -> dict:
        return self.unwrap(
            self._request('POST', '/api/quick-replies', json={'tag': tag, 'content': content})
        )

    def delete_quick_reply(self, qid: int) -> dict:
        return self.unwrap(self._request('DELETE', f'/api/quick-replies/{qid}'))

    # ---------- V4 产品库 / 本地 RAG ----------
    def products(self) -> dict:
        return self.unwrap(self._request('GET', '/api/products'))

    def add_product(self, name: str, category: str = '', summary: str = '') -> dict:
        return self.unwrap(self._request(
            'POST', '/api/products', json={'name': name, 'category': category, 'summary': summary}
        ))

    def update_product(self, product_id: int, **fields) -> dict:
        return self.unwrap(self._request('PUT', f'/api/products/{product_id}', json=fields))

    def delete_product(self, product_id: int) -> dict:
        return self.unwrap(self._request('DELETE', f'/api/products/{product_id}'))

    def product_documents(self, product_id: int) -> dict:
        return self.unwrap(self._request('GET', f'/api/products/{product_id}/documents'))

    def import_product_folder(self, product_id: int, source_dir: str) -> dict:
        return self.unwrap(self._request(
            'POST', f'/api/products/{product_id}/documents/import',
            json={'source_dir': source_dir}, timeout=3600.0,
        ))

    def delete_product_document(self, product_id: int, document_id: int) -> dict:
        return self.unwrap(self._request(
            'DELETE', f'/api/products/{product_id}/documents/{document_id}'
        ))

class ApiWorker(QThread):
    """把任意 ApiClient 调用放到子线程；结果通过 Signal 回主线程（CLAUDE.md §7.4）。"""

    result = Signal(dict)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D102
        try:
            data = self._fn()
            self.result.emit({'ok': True, 'data': data})
        except ApiError as e:
            self.result.emit({'ok': False, 'code': e.code, 'message': e.message})
        except Exception as e:
            self.result.emit({'ok': False, 'code': None, 'message': f'{type(e).__name__}: {e}'})
