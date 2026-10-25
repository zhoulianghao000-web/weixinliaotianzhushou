"""后端 MVP 主应用（CLAUDE.md §4 接口定义 / §5 统一响应信封）。

所有响应一律为 {"code", "message", "data", "ts"}。
启动：python server.py   （等价 uvicorn server:app --host 127.0.0.1 --port 8765）
"""
from __future__ import annotations

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
from adapters import create_adapter  # noqa: E402
from schemas import (  # noqa: E402
    ERR_BAD_PARAM,
    ERR_INTERNAL,
    ERR_LLM_FAILED,
    ERR_NO_CHAT,
    ERR_READ_MESSAGE,
    ERR_WECHAT_UNAVAILABLE,
    DockIn,
    ListenIn,
    ProductFolderImportIn,
    ProductIn,
    ProductUpdateIn,
    QuickReplyIn,
    ReplyIn,
    SendIn,
    SettingsIn,
    clamp_limit,
    degrade,
    fail,
    ok,
)
from services import Docking, generate_reply, llm_config  # noqa: E402
from services import product_library  # noqa: E402

state: dict = {
    'adapter': None,
    'docking': None,
    'current': None,
    'listen': False,
}

# Screenshot OCR can take hundreds of milliseconds or more. Keep it off the
# asyncio event loop so API responses and WebSocket control stay responsive;
# serialize calls because each adapter reuses one OCR engine instance.
_OCR_LOCK = asyncio.Lock()


async def _adapter_messages(adapter, *, who: str | None = None, limit: int = 60):
    if getattr(adapter, 'name', '') in {'qq', 'wechat4'}:
        async with _OCR_LOCK:
            return await asyncio.to_thread(adapter.messages, who=who, limit=limit)
    return adapter.messages(who=who, limit=limit)


async def _adapter_poll_new(adapter):
    if getattr(adapter, 'name', '') in {'qq', 'wechat4'}:
        async with _OCR_LOCK:
            return await asyncio.to_thread(adapter.poll_new)
    return adapter.poll_new()

VALID_ADAPTERS = ('qq', 'wechat4', 'wxauto', 'mock', 'auto')


def effective_adapter_name() -> str:
    """设置页保存的 adapter（DB）优先于 .env。"""
    v = (db.get_setting('adapter') or '').strip().lower()
    return v if v in VALID_ADAPTERS else (config.SIDEBAR_ADAPTER or 'auto').lower()


def effective_width() -> int:
    """设置页保存的宽度（DB）优先于 .env，夹在 300-600。"""
    try:
        return max(300, min(600, int(db.get_setting('sidebar_width') or config.SIDEBAR_WIDTH)))
    except Exception:
        return config.SIDEBAR_WIDTH


def settings_view() -> dict:
    cfg = llm_config()
    return {
        'api_key': cfg['api_key'],
        'base_url': cfg['base_url'],
        'model': cfg['model'],
        'width': effective_width(),
        'adapter': effective_adapter_name(),
    }


def ensure_adapter():
    preferred = effective_adapter_name()
    if preferred == 'auto':
        candidate = create_adapter('auto')
        current = state['adapter']
        if current is None:
            state['adapter'] = candidate
        elif current.name != candidate.name and candidate.status().get('client_running'):
            # Auto follows the active IM. Switching targets invalidates the
            # old chat baseline and stops listening/docking until re-enabled.
            try:
                current.close()
            except Exception:
                pass
            dock = state.get('docking')
            if dock:
                try:
                    dock.stop()
                except Exception:
                    pass
                state['docking'] = None
            state['listen'] = False
            state['current'] = None
            state['adapter'] = candidate
        else:
            try:
                candidate.close()
            except Exception:
                pass
    elif state['adapter'] is None or state['adapter'].name != preferred:
        state['adapter'] = create_adapter(preferred)
    return state['adapter']


def ensure_docking() -> Docking:
    if state['docking'] is None:
        adapter = ensure_adapter()
        target_class = config.TARGET_CLASS
        target_title = config.TARGET_TITLE
        if adapter.name == 'wechat4':
            target_class = ''
            target_title = '微信'
        elif adapter.name == 'wxauto':
            target_class = target_class or 'WeChatMainWndForPC'
            target_title = '微信'
        state['docking'] = Docking(
            config.SIDEBAR_TITLE,
            width=effective_width(),
            target_class=target_class,
            target_title=target_title,
            # QQ 半自动模式：侧边栏要能拿到键盘焦点（让人填"补充要求"、改回复）
            # Only wxauto's legacy keyboard automation requires preserving
            # focus in WeChat. QQ and WeChat 4.x use clipboard/window activation
            # paths that allow the sidebar forms to stay editable.
            no_activate=(adapter.name == 'wxauto'),
        )
    return state['docking']


# ---------------- §4.10 幂等（防重复发送）----------------
_IDEM_TTL = 3.0
_idem: dict = {}


def _idempotent(key: tuple, executor):
    """3 秒内相同 key 直接返回上次结果，不重复执行。返回 (result, duplicated)。"""
    now = time.time()
    hit = _idem.get(key)
    if hit and now - hit[0] < _IDEM_TTL:
        return hit[1], True
    result = executor()
    _idem[key] = (now, result)
    return result, False


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.seed()
    ensure_adapter()
    ensure_docking()
    yield
    d = state.get('docking')
    if d:
        d.stop()
    a = state.get('adapter')
    if a:
        a.close()


app = FastAPI(title='WeChat Sidebar Backend', version='0.1.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


# ---------------- §4.1 运行状态 ----------------
@app.get('/api/status')
async def status():
    try:
        a = ensure_adapter()
        d = ensure_docking()
        st = a.status()
        client_running = bool(st.get('client_running'))
        if a.name == 'wxauto':
            client_running = True
        st.update(
            {
                'selected_adapter': effective_adapter_name(),
                'client_running': client_running,
                'wechat_running': client_running,
                'docking': bool(d.running),
                'docking_error': d.last_error,
                'listen': state['listen'],
                'current': state['current'],
                'steals_foreground': a.steals_foreground,
            }
        )
        return ok(st)
    except Exception as e:
        return fail(ERR_WECHAT_UNAVAILABLE, f'{type(e).__name__}: {e}')


# ---------------- §4.2 会话列表 ----------------
@app.get('/api/sessions')
async def sessions():
    try:
        items = ensure_adapter().sessions()
        for s in items:
            if s.get('name'):
                db.upsert_chat(s['name'], s['name'], unread=1 if s.get('isnew') else 0)
        return ok({'sessions': items})
    except Exception as e:
        return fail(ERR_WECHAT_UNAVAILABLE, f'{type(e).__name__}: {e}')


# ---------------- §4.3 读取消息 ----------------
@app.get('/api/messages')
async def messages(who: str | None = None, limit: int | None = Query(default=None, ge=1, le=200)):
    limit = clamp_limit(limit)
    if who:
        state['current'] = who
    who = who or state['current']
    if not who:
        return fail(ERR_NO_CHAT)
    try:
        msgs = await _adapter_messages(ensure_adapter(), who=who, limit=limit)
    except Exception as e:
        return fail(ERR_READ_MESSAGE, f'{type(e).__name__}: {e}')
    try:
        if msgs:
            db.save_messages(who, msgs)
        else:
            # OCR can return no live text when the IM is closed, covered, or
            # between frames. Keep the last captured conversation visible.
            msgs = db.list_messages(who, limit=limit)
    except Exception:
        pass  # 落库失败不影响读取
    return ok({'who': who, 'messages': msgs})


# ---------------- §4.4 生成建议回复 ----------------
@app.post('/api/reply')
async def reply(body: ReplyIn):
    who = body.who or state['current']
    if not who:
        return fail(ERR_NO_CHAT)
    msgs = body.messages
    if not msgs:
        try:
            msgs = await _adapter_messages(ensure_adapter(), who=who)
        except Exception as e:
            return fail(ERR_READ_MESSAGE, f'{type(e).__name__}: {e}')
    product_sources = None
    product_context = None
    if body.product_id is not None:
        product = db.get_product(body.product_id)
        if not product:
            return fail(ERR_BAD_PARAM, '所选产品不存在')
        question = ' '.join(
            str(message.get('content') or '').strip()
            for message in (msgs or [])[-60:]
            if message.get('type') == 'friend' and message.get('content')
        )
        product_context = product_library.product_context(body.product_id, question)
        product_sources = product_context
    res = generate_reply(msgs or [], who, body.instruction, product_context=product_context)
    if not res.get('reply'):
        return fail(ERR_LLM_FAILED, '大模型未返回内容')
    data = {
        'who': who,
        'reply': res['reply'],
        'source': res.get('source', 'rule'),
        'degraded': bool(res.get('error')),
    }
    if body.product_id is not None:
        data['product_sources'] = product_sources or []
        data['product_context_used'] = bool(
            data['product_sources'] and not res.get('error') and res.get('source', '').startswith('llm:')
        )
    if res.get('error'):
        data['error'] = res['error']
        return degrade(data, res['error'])
    return ok(data)


# ---------------- §4.5 只填入输入框 ----------------
@app.post('/api/fill')
async def fill(body: SendIn):
    who = body.who or state['current']
    if not who:
        return fail(ERR_NO_CHAT)
    if not body.text.strip():
        return fail(ERR_BAD_PARAM, 'text 不能为空')
    try:
        r, dup = _idempotent(('fill', who, body.text), lambda: ensure_adapter().fill(who, body.text))
    except Exception as e:
        return fail(ERR_INTERNAL, f'{type(e).__name__}: {e}')
    if not r.get('ok'):
        return fail(ERR_INTERNAL, r.get('error') or '填入失败')
    data = {'ok': True, 'detail': r.get('detail') or f'已填入输入框：{who}'}
    if dup:
        data['duplicated'] = True
    return ok(data)


# ---------------- §4.6 填入并发送 ----------------
@app.post('/api/send')
async def send(body: SendIn):
    who = body.who or state['current']
    if not who:
        return fail(ERR_NO_CHAT)
    if not body.text.strip():
        return fail(ERR_BAD_PARAM, 'text 不能为空')
    def _do_send():
        r = ensure_adapter().send(who, body.text)
        if r.get('ok'):
            try:
                db.save_messages(
                    who,
                    [
                        {
                            'id': f'self-{int(time.time() * 1000)}',
                            'sender': '我',
                            'type': 'self',
                            'content': body.text,
                            'msg_ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        }
                    ],
                )
            except Exception:
                pass
        return r

    try:
        r, dup = _idempotent(('send', who, body.text), _do_send)
    except Exception as e:
        return fail(ERR_INTERNAL, f'{type(e).__name__}: {e}')
    if not r.get('ok'):
        return fail(ERR_INTERNAL, r.get('error') or '发送失败')
    data = {'ok': True, 'detail': r.get('detail') or f'已发送：{who}'}
    if dup:
        data['duplicated'] = True
    return ok(data)


# ---------------- §4.7 贴边跟随开关 ----------------
@app.post('/api/dock')
async def dock(body: DockIn):
    d = ensure_docking()
    if body.width:
        d.width = body.width
    try:
        if body.on:
            d.start()
        else:
            d.stop()
    except Exception as e:
        return fail(ERR_INTERNAL, f'{type(e).__name__}: {e}')
    return ok({'docking': d.running, 'error': d.last_error})


# ---------------- §4.8 实时监听开关 ----------------
@app.post('/api/listen')
async def listen(body: ListenIn):
    state['listen'] = bool(body.on)
    return ok({'listen': state['listen']})


# ---------------- §4.9 新消息推送 ----------------
@app.websocket('/ws/messages')
async def ws_messages(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            if state['listen']:
                try:
                    new = await _adapter_poll_new(ensure_adapter())
                except Exception:
                    new = {}
                if new:
                    for chat, messages in new.items():
                        try:
                            db.save_messages(chat, messages)
                        except Exception:
                            pass
                    await ws.send_json({'type': 'new', 'data': new})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


# ---------------- §4.11 设置读写（V2 设置页）----------------
@app.get('/api/settings')
async def get_settings():
    return ok(settings_view())


@app.put('/api/settings')
async def put_settings(body: SettingsIn):
    if body.adapter is not None and body.adapter not in VALID_ADAPTERS:
        return fail(ERR_BAD_PARAM, f'adapter 只能是 {" / ".join(VALID_ADAPTERS)}')

    prev_adapter = effective_adapter_name()
    if body.api_key is not None:
        db.set_setting('openai_api_key', body.api_key.strip())
    if body.base_url is not None:
        db.set_setting('openai_base_url', body.base_url.strip())
    if body.model is not None:
        db.set_setting('llm_model', body.model.strip())
    if body.width is not None:
        db.set_setting('sidebar_width', str(body.width))

    adapter_changed = body.adapter is not None and body.adapter != prev_adapter
    if adapter_changed:
        db.set_setting('adapter', body.adapter)

    data = settings_view()

    # 宽度即时生效：贴边线程直接改
    if body.width is not None and state['docking'] is not None:
        state['docking'].width = body.width

    if adapter_changed:
        # 决策（grill 2026-10-21）：惰性重建——只丢引用不 close，
        # 在途请求拿着旧实例自然跑完；自动关监听（换 IM 后需手动重开）；
        # 贴边线程停掉，下次 ensure_docking 按新 no_activate 重建。
        state['listen'] = False
        # A target name belongs to one adapter. Keeping e.g. "QQ 当前窗口"
        # after switching to WeChat makes WebSocket events for "微信当前窗口"
        # look like messages from an unselected conversation and drops them.
        state['current'] = None
        state['adapter'] = None
        d = state['docking']
        if d:
            try:
                d.stop()
            except Exception:
                pass
            state['docking'] = None
        data['adapter_changed'] = True

    return ok(data)


# ---------------- §4.12 话术库（V3 话术页）----------------
@app.get('/api/quick-replies')
async def quick_replies():
    return ok({'items': db.list_quick_replies()})


@app.post('/api/quick-replies')
async def add_quick_reply(body: QuickReplyIn):
    content = body.content.strip()
    if not content:
        return fail(ERR_BAD_PARAM, 'content 不能为空')
    tag = body.tag.strip() or '常用'
    db.add_quick_reply(tag, content)
    return ok({'items': db.list_quick_replies()})


@app.delete('/api/quick-replies/{qid}')
async def delete_quick_reply(qid: int):
    deleted = db.delete_quick_reply(qid)
    if not deleted:
        return fail(ERR_BAD_PARAM, f'话术不存在：{qid}')
    return ok({'deleted': True, 'items': db.list_quick_replies()})


# ---------------- §4.13 产品库（V4，本地资料 RAG）----------------
@app.get('/api/products')
async def products():
    return ok({'items': db.list_products()})


@app.post('/api/products')
async def create_product(body: ProductIn):
    name = body.name.strip()
    if not name:
        return fail(ERR_BAD_PARAM, '产品名称不能为空')
    item = db.create_product(name, body.category, body.summary)
    return ok({'item': item, 'items': db.list_products()})


@app.put('/api/products/{product_id}')
async def update_product(product_id: int, body: ProductUpdateIn):
    if body.name is not None and not body.name.strip():
        return fail(ERR_BAD_PARAM, '产品名称不能为空')
    item = db.update_product(
        product_id,
        name=body.name if 'name' in body.model_fields_set else None,
        category=body.category if 'category' in body.model_fields_set else None,
        summary=body.summary if 'summary' in body.model_fields_set else None,
    )
    if not item:
        return fail(ERR_BAD_PARAM, '产品不存在或名称不能为空')
    return ok({'item': item, 'items': db.list_products()})


@app.delete('/api/products/{product_id}')
async def delete_product(product_id: int):
    try:
        deleted = product_library.delete_product_copy(product_id)
    except ValueError as exc:
        return fail(ERR_BAD_PARAM, str(exc))
    if not deleted:
        return fail(ERR_BAD_PARAM, '产品不存在')
    return ok({'deleted': True, 'items': db.list_products()})


@app.get('/api/products/{product_id}/documents')
async def product_documents(product_id: int):
    if not db.get_product(product_id):
        return fail(ERR_BAD_PARAM, '产品不存在')
    return ok({'items': db.list_product_documents(product_id)})


@app.post('/api/products/{product_id}/documents/import')
async def import_product_documents(product_id: int, body: ProductFolderImportIn):
    try:
        result = await asyncio.to_thread(product_library.import_folder, product_id, body.source_dir)
    except ValueError as exc:
        return fail(ERR_BAD_PARAM, str(exc))
    except Exception as exc:
        return fail(ERR_INTERNAL, f'{type(exc).__name__}: {exc}')
    result['items_in_library'] = db.list_product_documents(product_id)
    return ok(result)


@app.delete('/api/products/{product_id}/documents/{document_id}')
async def delete_product_document(product_id: int, document_id: int):
    try:
        document = product_library.delete_document_copy(product_id, document_id)
    except ValueError as exc:
        return fail(ERR_BAD_PARAM, str(exc))
    if not document:
        return fail(ERR_BAD_PARAM, '资料不存在')
    return ok({'deleted': True, 'items': db.list_product_documents(product_id)})


if __name__ == '__main__':
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level='info')
