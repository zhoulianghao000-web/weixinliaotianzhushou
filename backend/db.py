"""SQLite 数据访问层（CLAUDE.md §6 数据模型）。

7 张表：chats / messages / quick_replies / settings / products / product_documents / product_chunks
时间统一存 UTC ISO8601 字符串；消息写入用 INSERT OR IGNORE 做 id 去重。

自检：`python db.py`  → 建表 + 写入种子数据（幂等）
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from config import DB_PATH
except ImportError:  # 作为包被导入时的兜底
    from backend.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_key    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    last_msg_at TEXT,
    unread      INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    chat_key   TEXT NOT NULL,
    sender     TEXT,
    type       TEXT NOT NULL CHECK (type IN ('friend','self','sys','time','recall')),
    content    TEXT,
    msg_ts     TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chat_key) REFERENCES chats(chat_key)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_key, msg_ts DESC);

CREATE TABLE IF NOT EXISTS quick_replies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tag        TEXT NOT NULL,
    content    TEXT NOT NULL,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL,
    name           TEXT NOT NULL,
    relative_path  TEXT NOT NULL,
    processed_path TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    error          TEXT NOT NULL DEFAULT '',
    sha256         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_documents_product ON product_documents(product_id, created_at);

CREATE TABLE IF NOT EXISTS product_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    page_start  INTEGER,
    page_end    INTEGER,
    section     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES product_documents(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_chunks_product ON product_chunks(product_id, document_id, chunk_index);
"""


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def connect() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


# ---------------- chats ----------------
def upsert_chat(chat_key: str, name: str, last_msg_at: str | None = None, unread: int | None = None) -> None:
    with connect() as conn:
        if unread is None:
            conn.execute(
                """
                INSERT INTO chats (chat_key, name, last_msg_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_key) DO UPDATE SET
                    name=excluded.name,
                    last_msg_at=COALESCE(excluded.last_msg_at, chats.last_msg_at),
                    updated_at=excluded.updated_at
                """,
                (chat_key, name, last_msg_at, now_iso()),
            )
        else:
            conn.execute(
                """
                INSERT INTO chats (chat_key, name, last_msg_at, unread, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_key) DO UPDATE SET
                    name=excluded.name,
                    last_msg_at=COALESCE(excluded.last_msg_at, chats.last_msg_at),
                    unread=excluded.unread,
                    updated_at=excluded.updated_at
                """,
                (chat_key, name, last_msg_at, unread, now_iso()),
            )
        conn.commit()


def list_chats() -> list[dict]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM chats ORDER BY updated_at DESC').fetchall()
    return [dict(r) for r in rows]


# ---------------- messages ----------------
def save_messages(chat_key: str, messages: list[dict]) -> int:
    """批量写入消息，靠主键 id 去重，返回实际新增条数。"""
    saved = 0
    with connect() as conn:
        for m in messages:
            mid = m.get('id') or f'{chat_key}-{m.get("type")}-{hash(str(m.get("content")))}'
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO messages (id, chat_key, sender, type, content, msg_ts, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(mid),
                    chat_key,
                    m.get('sender') or '',
                    m.get('type') or 'friend',
                    m.get('content') or '',
                    m.get('msg_ts'),
                    now_iso(),
                ),
            )
            saved += cur.rowcount
        conn.commit()
    return saved


def list_messages(chat_key: str, limit: int = 60) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            'SELECT id, chat_key, sender, type, content, msg_ts, created_at FROM messages '
            'WHERE chat_key = ? ORDER BY created_at ASC, id ASC LIMIT ?',
            (chat_key, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_messages() -> int:
    with connect() as conn:
        return conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]


# ---------------- quick_replies ----------------
def add_quick_reply(tag: str, content: str, sort: int = 0) -> None:
    with connect() as conn:
        conn.execute(
            'INSERT INTO quick_replies (tag, content, sort, created_at) VALUES (?, ?, ?, ?)',
            (tag, content, sort, now_iso()),
        )
        conn.commit()


def list_quick_replies() -> list[dict]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM quick_replies ORDER BY sort ASC, id ASC').fetchall()
    return [dict(r) for r in rows]


def delete_quick_reply(qid: int) -> bool:
    """按 id 删除话术，返回是否真的删了。"""
    with connect() as conn:
        cur = conn.execute('DELETE FROM quick_replies WHERE id = ?', (qid,))
        conn.commit()
    return cur.rowcount > 0


# ---------------- settings ----------------
def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, str(value)),
        )
        conn.commit()


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


# ---------------- products / product documents / RAG chunks ----------------
def create_product(name: str, category: str = '', summary: str = '') -> dict:
    now = now_iso()
    with connect() as conn:
        cur = conn.execute(
            'INSERT INTO products (name, category, summary, created_at, updated_at) VALUES (?,?,?,?,?)',
            (name.strip(), category.strip(), summary.strip(), now, now),
        )
        row = conn.execute('SELECT * FROM products WHERE id = ?', (cur.lastrowid,)).fetchone()
        conn.commit()
    return dict(row)


def get_product(product_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    return dict(row) if row else None


def list_products() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            '''SELECT p.*, COUNT(d.id) AS document_count
               FROM products p LEFT JOIN product_documents d ON d.product_id = p.id
               GROUP BY p.id ORDER BY p.updated_at DESC, p.id DESC'''
        ).fetchall()
    return [dict(row) for row in rows]


def update_product(product_id: int, *, name: str | None = None,
                   category: str | None = None, summary: str | None = None) -> dict | None:
    product = get_product(product_id)
    if product is None:
        return None
    values = {
        'name': product['name'] if name is None else name.strip(),
        'category': product['category'] if category is None else category.strip(),
        'summary': product['summary'] if summary is None else summary.strip(),
    }
    if not values['name']:
        return None
    with connect() as conn:
        conn.execute(
            'UPDATE products SET name=?, category=?, summary=?, updated_at=? WHERE id=?',
            (values['name'], values['category'], values['summary'], now_iso(), product_id),
        )
        row = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        conn.commit()
    return dict(row) if row else None


def delete_product(product_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute('DELETE FROM products WHERE id=?', (product_id,))
        conn.commit()
    return cur.rowcount > 0


def add_product_document(product_id: int, *, name: str, relative_path: str,
                         processed_path: str = '', summary: str = '', status: str = 'raw_only',
                         error: str = '', sha256: str = '') -> int:
    with connect() as conn:
        cur = conn.execute(
            '''INSERT INTO product_documents
               (product_id,name,relative_path,processed_path,summary,status,error,sha256,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (product_id, name, relative_path, processed_path, summary, status, error, sha256, now_iso()),
        )
        conn.execute('UPDATE products SET updated_at=? WHERE id=?', (now_iso(), product_id))
        conn.commit()
    return int(cur.lastrowid)


def find_product_document_by_hash(product_id: int, sha256: str) -> dict | None:
    if not sha256:
        return None
    with connect() as conn:
        row = conn.execute(
            'SELECT * FROM product_documents WHERE product_id=? AND sha256=? LIMIT 1',
            (product_id, sha256),
        ).fetchone()
    return dict(row) if row else None


def update_product_document(document_id: int, *, processed_path: str | None = None,
                            summary: str | None = None, status: str | None = None,
                            error: str | None = None) -> None:
    current = get_product_document(document_id)
    if not current:
        return
    fields = {
        'processed_path': current['processed_path'] if processed_path is None else processed_path,
        'summary': current['summary'] if summary is None else summary,
        'status': current['status'] if status is None else status,
        'error': current['error'] if error is None else error,
    }
    with connect() as conn:
        conn.execute(
            'UPDATE product_documents SET processed_path=?,summary=?,status=?,error=? WHERE id=?',
            (fields['processed_path'], fields['summary'], fields['status'], fields['error'], document_id),
        )
        conn.commit()


def get_product_document(document_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute('SELECT * FROM product_documents WHERE id=?', (document_id,)).fetchone()
    return dict(row) if row else None


def list_product_documents(product_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            '''SELECT d.*, COUNT(c.id) AS chunk_count
               FROM product_documents d LEFT JOIN product_chunks c ON c.document_id=d.id
               WHERE d.product_id=? GROUP BY d.id ORDER BY d.created_at DESC,d.id DESC''',
            (product_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_product_document(product_id: int, document_id: int) -> dict | None:
    document = get_product_document(document_id)
    if not document or document['product_id'] != product_id:
        return None
    with connect() as conn:
        conn.execute('DELETE FROM product_documents WHERE id=? AND product_id=?', (document_id, product_id))
        conn.execute('UPDATE products SET updated_at=? WHERE id=?', (now_iso(), product_id))
        conn.commit()
    return document


def replace_product_chunks(product_id: int, document_id: int, chunks: list[dict]) -> None:
    with connect() as conn:
        conn.execute('DELETE FROM product_chunks WHERE document_id=?', (document_id,))
        conn.executemany(
            '''INSERT INTO product_chunks
               (document_id,product_id,chunk_index,content,page_start,page_end,section,created_at)
               VALUES (?,?,?,?,?,?,?,?)''',
            [
                (document_id, product_id, i, item['content'], item.get('page_start'),
                 item.get('page_end'), item.get('section') or '', now_iso())
                for i, item in enumerate(chunks)
            ],
        )
        conn.commit()


def search_product_chunks(product_id: int, query: str, limit: int = 6) -> list[dict]:
    """Local Unicode-aware fallback ranker; no remote embedding service is used."""
    import re

    if not query.strip():
        return []
    terms: list[str] = []
    for token in re.findall(r'[a-z0-9]+|[\u3400-\u9fff]+', query.lower()):
        if re.fullmatch(r'[\u3400-\u9fff]+', token):
            if len(token) <= 3:
                terms.append(token)
            for n in (2, 3):
                terms.extend(token[i:i+n] for i in range(max(0, len(token)-n+1)))
        else:
            terms.append(token)
    terms = list(dict.fromkeys(term for term in terms if term))
    if not terms:
        terms = [query.strip().lower()]

    with connect() as conn:
        product = conn.execute('SELECT name,summary FROM products WHERE id=?', (product_id,)).fetchone()
        rows = conn.execute(
            '''SELECT c.id,c.document_id,c.product_id,c.content,c.page_start,c.page_end,c.section,
                      d.name AS document_name,d.summary AS document_summary,d.relative_path
               FROM product_chunks c JOIN product_documents d ON d.id=c.document_id
               WHERE c.product_id=?''', (product_id,),
        ).fetchall()
    scored = []
    if product and product['summary'].strip():
        summary_text = product['summary'].strip()
        score = sum(min(summary_text.lower().count(term), 3) * (2.0 if len(term) > 1 else 0.5) for term in terms)
        if score:
            scored.append({
                'id': None, 'document_id': None, 'product_id': product_id,
                'content': summary_text, 'page_start': None, 'page_end': None,
                'section': '产品摘要', 'document_name': f"{product['name']}（产品摘要）",
                'document_summary': summary_text, 'relative_path': '', 'score': score + 1.0,
            })
    for raw in rows:
        row = dict(raw)
        content = row['content'].lower()
        summary = row['document_summary'].lower()
        title = row['document_name'].lower()
        score = 0.0
        hits = 0
        for term in terms:
            count = content.count(term)
            if count:
                hits += 1
                score += min(count, 3) * (2.0 if len(term) > 1 else 0.5)
            if term in summary:
                score += 1.6
            if term in title:
                score += 1.0
        if score:
            row['score'] = score + hits / max(1, len(terms))
            scored.append(row)
    scored.sort(key=lambda row: (row['score'], row.get('page_start') or 0), reverse=True)
    return scored[:max(1, min(limit, 12))]


# ---------------- 种子数据 ----------------
SEED_CHATS = [
    ('张三', '张三', '2026-10-20T06:32:00Z', 2),
    ('产品交流群', '产品交流群', '2026-10-20T06:20:00Z', 1),
    ('文件传输助手', '文件传输助手', '2026-10-19T09:00:00Z', 0),
]

SEED_MESSAGES = [
    ('seed-1', '张三', '张三', 'friend', '在吗？', '2026-10-20T06:30:00Z'),
    ('seed-2', '张三', '我', 'self', '在的，你说', '2026-10-20T06:31:00Z'),
    ('seed-3', '张三', '张三', 'friend', '上次那个报价单能再便宜点吗？预算卡得紧', '2026-10-20T06:32:00Z'),
    ('seed-4', '产品交流群', '王五', 'friend', '新版原型 v3 出来了，大家看下', '2026-10-20T06:18:00Z'),
    ('seed-5', '产品交流群', '赵六', 'friend', '侧边栏那个交互我感觉有点挤', '2026-10-20T06:19:00Z'),
    ('seed-6', '产品交流群', '我', 'self', '收到，我调整一下间距', '2026-10-20T06:20:00Z'),
    ('seed-7', '文件传输助手', '我', 'self', '会议纪要.docx', '2026-10-19T09:00:00Z'),
]

SEED_QUICK_REPLIES = [
    ('常用', '收到，我这边跟进一下，有进展第一时间同步你。', 0),
    ('常用', '在的，你说～', 1),
    ('商务', '方便说下你们大概的量和交付时间吗？我给你配个合适的方案。', 2),
]


def seed(force: bool = False) -> dict:
    """写入种子数据（幂等）。force=True 会清空重灌。"""
    init_db()
    with connect() as conn:
        if force:
            conn.executescript('DELETE FROM messages; DELETE FROM chats; DELETE FROM quick_replies;')
        conn.executemany(
            'INSERT OR IGNORE INTO chats (chat_key, name, last_msg_at, unread, updated_at) VALUES (?,?,?,?,?)',
            [(k, n, t, u, now_iso()) for k, n, t, u in SEED_CHATS],
        )
        conn.executemany(
            'INSERT OR IGNORE INTO messages (id, chat_key, sender, type, content, msg_ts, created_at) '
            'VALUES (?,?,?,?,?,?,?)',
            [(i, c, s, t, m, ts, now_iso()) for i, c, s, t, m, ts in SEED_MESSAGES],
        )
        conn.executemany(
            'INSERT OR IGNORE INTO quick_replies (id, tag, content, sort, created_at) VALUES (?,?,?,?,?)',
            [(idx + 1, tag, content, sort, now_iso())
             for idx, (tag, content, sort) in enumerate(SEED_QUICK_REPLIES)],
        )
        conn.execute(
            'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('sidebar_width', '380')
        )
        conn.execute(
            'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('llm_model', 'deepseek-chat')
        )
        conn.commit()
    return {
        'chats': len(list_chats()),
        'messages': count_messages(),
        'quick_replies': len(list_quick_replies()),
    }


if __name__ == '__main__':
    import sys

    do_force = '--force' in sys.argv
    init_db()
    stat = seed(force=do_force)
    print(f'数据库：{DB_PATH}')
    print(f'种子数据：{stat}')
