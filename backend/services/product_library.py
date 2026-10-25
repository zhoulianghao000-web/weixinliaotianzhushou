"""Local RAW document storage and SQLite indexing for product-specific RAG."""
from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from pathlib import Path

try:
    from config import PRODUCTS_PATH
    import db
except ImportError:  # pragma: no cover - package import fallback
    from backend.config import PRODUCTS_PATH
    from backend import db

from services.product_documents import (
    RAW_ONLY_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    document_summary,
    make_chunks,
    parse_document,
)

IGNORED_DIRS = {'.git', '.svn', '__pycache__', '.venv', 'venv', 'node_modules', 'build', 'dist'}


def _product_root(product_id: int) -> Path:
    if int(product_id) <= 0:
        raise ValueError('产品编号无效')
    root = (Path(PRODUCTS_PATH) / str(int(product_id))).resolve()
    base = Path(PRODUCTS_PATH).resolve()
    if not root.is_relative_to(base):
        raise ValueError('产品资料目录不安全')
    return root


def _relative_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('资料路径不安全')
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _source_files(source: Path):
    for path in source.rglob('*'):
        if any(part in IGNORED_DIRS for part in path.relative_to(source).parts):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        yield path


def import_folder(product_id: int, source_dir: str) -> dict:
    product = db.get_product(product_id)
    if not product:
        raise ValueError('产品不存在')
    source = Path(source_dir).expanduser().resolve()
    if not source.exists() or (not source.is_dir() and not source.is_file()):
        raise ValueError('所选资料不存在')
    root = _product_root(product_id)
    if source == root or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError('不能把产品库自身目录作为导入源')

    is_single_file = source.is_file()
    files = [source] if is_single_file else list(_source_files(source))
    if not files:
        raise ValueError('所选目录里没有可导入的文件')
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime()) + '-' + uuid.uuid4().hex[:6]
    source_name = source.stem if is_single_file else (source.name or '资料')
    batch_name = f'{source_name}-{stamp}'
    raw_batch = root / 'raw' / batch_name
    raw_batch.mkdir(parents=True, exist_ok=True)
    outcomes = []

    for source_file in files:
        relative = Path(source_file.name) if is_single_file else source_file.relative_to(source)
        try:
            digest = _sha256(source_file)
            duplicate = db.find_product_document_by_hash(product_id, digest)
            if duplicate:
                outcomes.append({
                    'name': source_file.name,
                    'status': 'duplicate',
                    'detail': '相同文件已在产品库中，已跳过重复副本。',
                })
                continue
            raw_target = raw_batch / relative
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, raw_target)
            relative_path = raw_target.relative_to(root).as_posix()
            suffix = source_file.suffix.lower()
            initial_status = 'raw_only'
            initial_error = '该格式仅保留原件，当前不建立文本索引。'
            doc_id = db.add_product_document(
                product_id,
                name=source_file.name,
                relative_path=relative_path,
                status=initial_status,
                error=initial_error,
                sha256=digest,
            )

            if suffix not in SUPPORTED_EXTENSIONS:
                outcomes.append({'id': doc_id, 'name': source_file.name, 'status': initial_status, 'detail': initial_error})
                continue
            if suffix in RAW_ONLY_EXTENSIONS:
                outcomes.append({'id': doc_id, 'name': source_file.name, 'status': initial_status,
                                 'detail': '3D 原件已保留；纯文本模型需要配套文字说明才能用于检索。'})
                continue

            processed_dir = root / 'processed' / str(doc_id)
            processed_dir.mkdir(parents=True, exist_ok=True)
            try:
                result = parse_document(raw_target, processed_dir)
                markdown = result.get('markdown') or ''
                segments = result.get('segments') or []
                chunks = make_chunks(segments)
                if markdown:
                    (processed_dir / 'document.md').write_text(markdown, encoding='utf-8')
                db.replace_product_chunks(product_id, doc_id, chunks)
                summary = document_summary(segments, fallback=source_file.stem)
                processed_relative = f'processed/{doc_id}/document.md' if markdown else ''
                parser_error = str(result.get('error') or '')
                needs_review = bool(result.get('needs_review'))
                if chunks:
                    status = 'needs_review' if needs_review else 'indexed'
                    detail = parser_error or ('已提取；图像/图表内容可能需要补充文字说明。' if needs_review else '已提取并建立检索索引。')
                else:
                    status = 'needs_review' if needs_review else 'raw_only'
                    detail = parser_error or '未提取到可检索文字，原件仍保留在 RAW 层。'
                if result.get('removed_headers'):
                    detail = (detail + ' 已按本 PDF 的重复边缘文字清理页眉页脚；页码和原件仍可追溯。').strip()
                db.update_product_document(
                    doc_id, processed_path=processed_relative, summary=summary,
                    status=status, error=detail,
                )
                outcomes.append({'id': doc_id, 'name': source_file.name, 'status': status,
                                 'detail': detail, 'chunk_count': len(chunks)})
            except Exception as exc:
                message = f'{type(exc).__name__}: {exc}'[:1200]
                db.update_product_document(doc_id, status='error', error=message)
                outcomes.append({'id': doc_id, 'name': source_file.name, 'status': 'error', 'detail': message})
        except Exception as exc:
            outcomes.append({'name': source_file.name, 'status': 'error',
                             'detail': f'{type(exc).__name__}: {exc}'[:1200]})

    return {
        'product_id': product_id,
        'source_name': source.name,
        'items': outcomes,
        'imported_count': sum(item.get('status') != 'duplicate' and item.get('status') != 'error' for item in outcomes),
        'duplicate_count': sum(item.get('status') == 'duplicate' for item in outcomes),
        'error_count': sum(item.get('status') == 'error' for item in outcomes),
    }


def delete_document_copy(product_id: int, document_id: int) -> dict | None:
    root = _product_root(product_id)
    document = db.get_product_document(document_id)
    if not document or int(document['product_id']) != int(product_id):
        return None
    raw_file = _relative_file(root, document['relative_path'])
    processed_file = _relative_file(root, document['processed_path']) if document.get('processed_path') else None
    document = db.delete_product_document(product_id, document_id)
    if not document:
        return None
    if raw_file.is_file():
        raw_file.unlink()
    try:
        raw_file.parent.rmdir()
    except OSError:
        pass
    if processed_file:
        processed_dir = processed_file.parent
        if processed_dir.exists() and processed_dir.is_dir():
            shutil.rmtree(processed_dir)
    return document


def delete_product_copy(product_id: int) -> bool:
    root = _product_root(product_id)
    if not db.get_product(product_id):
        return False
    quarantine = root.with_name(f'.delete-{root.name}-{uuid.uuid4().hex[:8]}')
    moved = False
    if root.exists():
        root.rename(quarantine)
        moved = True
    try:
        removed = db.delete_product(product_id)
    except Exception:
        if moved and quarantine.exists():
            quarantine.rename(root)
        raise
    if not removed:
        if moved and quarantine.exists():
            quarantine.rename(root)
        return False
    if moved and quarantine.exists():
        shutil.rmtree(quarantine)
    return True


def product_context(product_id: int, query: str, limit: int = 6) -> list[dict]:
    hits = db.search_product_chunks(product_id, query, limit=limit)
    result = []
    budget = 7000
    for hit in hits:
        excerpt = ' '.join(str(hit.get('content') or '').split())
        if not excerpt:
            continue
        excerpt = excerpt[:1800]
        if len(excerpt) > budget:
            excerpt = excerpt[:budget]
        result.append({
            'document_name': hit.get('document_name') or '产品资料',
            'page': hit.get('page_start'),
            'page_end': hit.get('page_end'),
            'section': hit.get('section') or '',
            'excerpt': excerpt,
        })
        budget -= len(excerpt)
        if budget <= 0:
            break
    return result
