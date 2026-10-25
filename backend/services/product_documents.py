"""Local document extraction and source-aware Markdown generation for the product library."""
from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


TEXT_EXTENSIONS = {'.md', '.markdown', '.txt'}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {
    '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff',
    '.csv', '.tsv', '.docx', '.pptx', '.xlsx', '.gltf', '.glb', '.obj', '.stl', '.fbx', '.3mf',
}
RAW_ONLY_EXTENSIONS = {'.gltf', '.glb', '.obj', '.stl', '.fbx', '.3mf'}
MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP_CHARS = 100


@dataclass
class Segment:
    text: str
    page: int | None = None
    section: str = ''


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _escape_cell(value: Any) -> str:
    return str(value if value is not None else '').replace('\r', ' ').replace('\n', '<br>').replace('|', '\\|').strip()


def _rows_to_markdown(rows: list[list[Any]]) -> str:
    rows = [[_escape_cell(cell) for cell in row] for row in rows]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ''
    width = max(len(row) for row in rows)
    rows = [row + [''] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:]
    if not any(header):
        header = [f'字段{i + 1}' for i in range(width)]
        body = rows
    return '\n'.join([
        '| ' + ' | '.join(header) + ' |',
        '| ' + ' | '.join(['---'] * width) + ' |',
        *('| ' + ' | '.join(row) + ' |' for row in body),
    ])


def _html_to_markdown(source: str) -> str:
    """Convert MinerU table HTML to a compact Markdown table; preserve non-table text."""
    try:
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows: list[list[str]] = []
                self.row: list[str] | None = None
                self.cell: list[str] | None = None

            def handle_starttag(self, tag, attrs):
                if tag == 'tr':
                    self.row = []
                elif tag in ('td', 'th'):
                    self.cell = []

            def handle_data(self, data):
                if self.cell is not None:
                    self.cell.append(data)

            def handle_endtag(self, tag):
                if tag in ('td', 'th') and self.cell is not None:
                    assert self.row is not None
                    self.row.append(' '.join(''.join(self.cell).split()))
                    self.cell = None
                elif tag == 'tr' and self.row is not None:
                    self.rows.append(self.row)
                    self.row = None

        parser = TableParser()
        parser.feed(source)
        if parser.rows:
            return _rows_to_markdown(parser.rows)
    except Exception:
        pass
    text = re.sub(r'<br\s*/?>', '\n', source, flags=re.I)
    text = re.sub(r'</(?:p|div|tr|li)>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    return html.unescape(re.sub(r'[ \t]+', ' ', text)).strip()


def _plain_text_from_node(node: Any) -> str:
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, list):
        return '\n'.join(part for part in (_plain_text_from_node(x) for x in node) if part)
    if not isinstance(node, dict):
        return ''
    html_value = node.get('html')
    if isinstance(html_value, str) and html_value.strip():
        return _html_to_markdown(html_value)
    for key in ('content', 'text', 'latex'):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    pieces = []
    for key in ('spans', 'lines', 'blocks', 'content'):
        value = node.get(key)
        if isinstance(value, (dict, list)):
            text = _plain_text_from_node(value)
            if text:
                pieces.append(text)
    return '\n'.join(pieces)


def _middle_pages(payload: dict) -> tuple[list[dict], dict[int, tuple[float, float]]]:
    pages = payload.get('pages')
    if not isinstance(pages, list):
        pages = payload.get('pdf_info')
    if not isinstance(pages, list):
        return [], {}
    geometry: dict[int, tuple[float, float]] = {}
    ext_pages = ((payload.get('extensions') or {}).get('docvortex_layout') or {}).get('pages') or []
    for item in ext_pages:
        try:
            geometry[int(item.get('page_idx', 0))] = (float(item['width_pt']), float(item['height_pt']))
        except (KeyError, TypeError, ValueError):
            pass
    normalized_pages = []
    for index, page in enumerate(pages):
        page_idx = int(page.get('page_idx', index))
        blocks = page.get('blocks')
        if not isinstance(blocks, list):
            blocks = page.get('para_blocks') or page.get('preproc_blocks') or []
        if page_idx not in geometry:
            size = page.get('page_size') or page.get('size')
            try:
                geometry[page_idx] = (float(size[0]), float(size[1]))
            except (TypeError, ValueError, IndexError):
                max_x = max_y = 0.0
                for block in blocks:
                    bbox = block.get('bbox') if isinstance(block, dict) else None
                    if isinstance(bbox, (tuple, list)) and len(bbox) == 4:
                        max_x = max(max_x, float(bbox[2]))
                        max_y = max(max_y, float(bbox[3]))
                if max_x and max_y:
                    geometry[page_idx] = (max_x, max_y)
        normalized_pages.append({'page_idx': page_idx, 'blocks': blocks})
    return normalized_pages, geometry


def _block_media(block: dict) -> str:
    value = block.get('image_path') or block.get('path')
    if isinstance(value, str):
        return value
    for child in block.get('blocks') or []:
        if isinstance(child, dict):
            found = _block_media(child)
            if found:
                return found
    for line in block.get('lines') or []:
        if not isinstance(line, dict):
            continue
        for span in line.get('spans') or []:
            if isinstance(span, dict) and span.get('image_path'):
                return str(span['image_path'])
    return ''


def _safe_relative_asset(name: str) -> PurePosixPath | None:
    relative = PurePosixPath(str(name).replace('\\', '/'))
    windows = PureWindowsPath(str(name))
    if relative.is_absolute() or windows.is_absolute() or windows.drive or '..' in relative.parts:
        return None
    return relative


def _normalize_header(text: str) -> str:
    text = re.sub(r'\d+', '#', text.casefold())
    return re.sub(r'[^\w#\u3400-\u9fff]+', '', text, flags=re.UNICODE)


def _detect_marginalia(pages: list[dict], geometry: dict[int, tuple[float, float]]) -> set[tuple[int, int]]:
    """Remove only short, repeated text blocks in page margins; alignment is irrelevant."""
    if len(pages) < 2:
        return set()
    candidates: dict[str, set[int]] = {}
    membership: dict[tuple[int, int], str] = {}
    for page in pages:
        idx = page['page_idx']
        size = geometry.get(idx)
        if not size or size[1] <= 0:
            continue
        height = size[1]
        for block_index, block in enumerate(page['blocks']):
            if not isinstance(block, dict):
                continue
            kind = str(block.get('type') or '').lower()
            if kind in {'table', 'image', 'chart', 'figure', 'equation'}:
                continue
            text = _plain_text_from_node(block)
            text = ' '.join(text.split())
            if not text or len(text) > 180:
                continue
            bbox = block.get('bbox')
            if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
                continue
            try:
                top, bottom = float(bbox[1]), float(bbox[3])
            except (TypeError, ValueError):
                continue
            center = (top + bottom) / (2 * height)
            if center <= 0.22:
                band = 'top'
            elif center >= 0.78:
                band = 'bottom'
            else:
                continue
            normalized = _normalize_header(text)
            if len(normalized) < 2:
                continue
            key = f'{band}:{normalized}'
            candidates.setdefault(key, set()).add(idx)
            membership[(idx, block_index)] = key
    page_count = len(pages)
    required = max(2, math.ceil(page_count * 0.6))
    repeated = {key for key, page_ids in candidates.items() if len(page_ids) >= required}
    return {location for location, key in membership.items() if key in repeated}


def _render_mineru(payload: dict, processed_dir: Path) -> tuple[str, list[Segment], bool, bool]:
    pages, geometry = _middle_pages(payload)
    if not pages:
        return '', [], False, True
    removed = _detect_marginalia(pages, geometry)
    lines: list[str] = []
    segments: list[Segment] = []
    has_unreadable_media = False
    section = ''
    for page_number, page in enumerate(pages, start=1):
        page_idx = page['page_idx']
        actual_page = page_idx + 1
        lines.append(f'<!-- source-page: {actual_page} -->')
        for block_index, block in enumerate(page['blocks']):
            if not isinstance(block, dict) or (page_idx, block_index) in removed:
                continue
            kind = str(block.get('type') or '').lower()
            text = _plain_text_from_node(block)
            media = _block_media(block)
            if text:
                text = text.strip()
                if kind in {'title', 'section_title', 'paragraph_title'}:
                    section = text
                    rendered = f'## {text}'
                else:
                    rendered = text
                lines.append(rendered)
                segments.append(Segment(text=text, page=actual_page, section=section))
            elif media or kind in {'image', 'chart', 'figure'}:
                has_unreadable_media = True
                safe_path = _safe_relative_asset(media)
                if not safe_path:
                    continue
                if media:
                    lines.append(f'![附件：第 {actual_page} 页]({safe_path.as_posix()})')
                lines.append(f'> 第 {actual_page} 页含图像附件；未提取到可检索文字，请补充说明。')
        lines.append('')
    return '\n'.join(lines).strip() + '\n', segments, bool(removed), has_unreadable_media


def _extract_mineru(path: Path, processed_dir: Path) -> tuple[str, list[Segment], bool, bool] | None:
    executable = shutil.which('mineru-kit')
    if not executable:
        return None
    processed_dir.mkdir(parents=True, exist_ok=True)
    tier = 'basic' if path.suffix.lower() in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'} else 'flash'
    with tempfile.TemporaryDirectory(prefix='sidebar-mineru-') as tmp:
        output_dir = Path(tmp) / 'result'
        output_dir.mkdir()
        command = [executable, 'parse', str(path), '-o', str(output_dir), '--format', 'zip', '--tier', tier]
        env = os.environ.copy()
        # The product library never opts into MinerU's remote parsing API.
        env.pop('MINERU_API_URL', None)
        env.pop('MINERU_API_KEY', None)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3600, env=env)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('本地 MinerU 解析超时；原文件已保留，可稍后重试') from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or 'MinerU 解析失败').strip()
            raise RuntimeError(detail[-1200:])
        archives = list(output_dir.rglob('*.zip'))
        if not archives:
            # Some MinerU versions materialize their outputs directly.
            middle_files = list(output_dir.rglob('middle_json.json'))
            if not middle_files:
                md_files = list(output_dir.rglob('*.md'))
                if md_files:
                    text = md_files[0].read_text(encoding='utf-8', errors='replace')
                    return text, _segments_from_markdown(text), False, False
                raise RuntimeError('MinerU 没有生成可读取的 Markdown/JSON 结果')
            middle_path = middle_files[0]
            payload = json.loads(middle_path.read_text(encoding='utf-8'))
            for asset in output_dir.rglob('*'):
                if asset.is_file() and asset != middle_path:
                    rel = asset.relative_to(output_dir)
                    target = processed_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(asset, target)
        else:
            archive = archives[0]
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                middle_name = next((n for n in names if PurePosixPath(n).name == 'middle_json.json'), None)
                if not middle_name:
                    raise RuntimeError('MinerU 输出中缺少 middle_json.json')
                payload = json.loads(zf.read(middle_name).decode('utf-8'))
                for name in names:
                    rel = _safe_relative_asset(name)
                    if not rel or name.endswith('/'):
                        continue
                    target = processed_dir.joinpath(*rel.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(name))
    return _render_mineru(payload, processed_dir)


def _segments_from_markdown(text: str) -> list[Segment]:
    segments = []
    section = ''
    for part in re.split(r'\n\s*\n', text):
        part = part.strip()
        if not part:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', part)
        if heading:
            section = heading.group(2).strip()
        segments.append(Segment(part, section=section))
    return segments


def _native_pdf(path: Path) -> tuple[str, list[Segment], bool, bool]:
    try:
        import fitz
    except Exception:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise RuntimeError('缺少 PDF 解析依赖，请安装产品库依赖') from exc
        reader = PdfReader(str(path))
        pages = [{'page_idx': i, 'blocks': [{'type': 'text', 'content': page.extract_text() or ''}]}
                 for i, page in enumerate(reader.pages)]
        text = '\n\n'.join(f'## 第 {i + 1} 页\n{p["blocks"][0]["content"]}' for i, p in enumerate(pages)).strip()
        segments = [Segment(p['blocks'][0]['content'], i + 1) for i, p in enumerate(pages) if p['blocks'][0]['content'].strip()]
        return text, segments, False, not bool(segments)

    doc = fitz.open(str(path))
    pages = []
    for i, page in enumerate(doc):
        blocks = []
        for block in page.get_text('blocks'):
            if len(block) >= 5 and int(block[6]) == 0:
                blocks.append({'type': 'text', 'bbox': list(block[:4]), 'content': str(block[4]).strip()})
        pages.append({'page_idx': i, 'blocks': blocks})
    geometry = {i: (float(page.rect.width), float(page.rect.height)) for i, page in enumerate(doc)}
    removed = _detect_marginalia(pages, geometry)
    lines = []
    segments = []
    for page in pages:
        page_idx = page['page_idx']
        page_no = page_idx + 1
        lines.append(f'<!-- source-page: {page_no} -->')
        for block_idx, block in enumerate(page['blocks']):
            if (page_idx, block_idx) in removed:
                continue
            text = block['content'].strip()
            if text:
                lines.append(text)
                segments.append(Segment(text, page_no))
        lines.append('')
    doc.close()
    markdown = '\n'.join(lines).strip() + '\n'
    return markdown, segments, bool(removed), not bool(segments)


def _parse_docx(path: Path) -> tuple[str, list[Segment]]:
    from docx import Document
    document = Document(str(path))
    lines = []
    section = ''
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style else '').lower()
        if 'heading' in style or 'title' in style:
            section = text
            lines.append(f'## {text}')
        else:
            lines.append(text)
    for table in document.tables:
        lines.append(_rows_to_markdown([[cell.text for cell in row.cells] for row in table.rows]))
    text = '\n\n'.join(line for line in lines if line)
    return text, _segments_from_markdown(text)


def _parse_pptx(path: Path) -> tuple[str, list[Segment]]:
    from pptx import Presentation
    presentation = Presentation(str(path))
    lines = []
    segments = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines.append(f'## 第 {index} 页')
        slide_lines = []
        for shape in slide.shapes:
            if getattr(shape, 'has_text_frame', False):
                value = '\n'.join(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())
                if value:
                    slide_lines.append(value)
            if getattr(shape, 'has_table', False):
                slide_lines.append(_rows_to_markdown([[cell.text for cell in row.cells] for row in shape.table.rows]))
        lines.extend(slide_lines)
        segments.extend(Segment(value, section=f'第 {index} 页') for value in slide_lines if value)
    return '\n\n'.join(lines), segments


def _parse_xlsx(path: Path) -> tuple[str, list[Segment]]:
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True, data_only=True)
    lines = []
    segments = []
    try:
        for sheet in workbook.worksheets:
            rows = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
            table = _rows_to_markdown(rows)
            if table:
                lines.extend([f'## {sheet.title}', table, ''])
                segments.extend(Segment(part, section=sheet.title) for part in table.splitlines() if part.strip())
    finally:
        workbook.close()
    return '\n'.join(lines).strip(), segments


def _parse_csv(path: Path) -> tuple[str, list[Segment]]:
    text = _read_text(path)
    delimiter = '\t' if path.suffix.lower() == '.tsv' else ','
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    markdown = _rows_to_markdown(rows)
    return markdown, [Segment(markdown)] if markdown else []


def parse_document(path: Path, processed_dir: Path) -> dict:
    """Parse one supported local file and return Markdown, chunks' source segments, and review state."""
    suffix = path.suffix.lower()
    if suffix in {'.md', '.markdown'}:
        text = _read_text(path)
        return {'markdown': text, 'segments': _segments_from_markdown(text), 'needs_review': False, 'removed_headers': False}
    if suffix == '.txt':
        text = _read_text(path)
        return {'markdown': text, 'segments': _segments_from_markdown(text), 'needs_review': False, 'removed_headers': False}
    if suffix in {'.csv', '.tsv'}:
        text, segments = _parse_csv(path)
        return {'markdown': text, 'segments': segments, 'needs_review': False, 'removed_headers': False}

    if suffix in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}:
        mineru_error = ''
        try:
            parsed = _extract_mineru(path, processed_dir)
        except Exception as exc:
            parsed = None
            mineru_error = f'MinerU 本地解析失败：{str(exc)[:300]}'
        if parsed is not None:
            markdown, segments, removed_headers, has_unreadable_media = parsed
            needs_review = has_unreadable_media or not bool(segments)
            if suffix == '.pdf' and not segments:
                markdown, segments, removed_headers, empty = _native_pdf(path)
                needs_review = empty
            return {'markdown': markdown, 'segments': segments, 'needs_review': needs_review,
                    'removed_headers': removed_headers}
        if suffix == '.pdf':
            markdown, segments, removed_headers, empty = _native_pdf(path)
            message = mineru_error or (
                        'PDF 已提取文字；尚未安装 MinerU，扫描版 OCR 和复杂版面解析不可用。'
                        if segments else '未检测到可提取文字，可能是扫描版；安装 MinerU 后可进行本地 OCR。'
                    )
            return {'markdown': markdown, 'segments': segments, 'needs_review': empty or bool(mineru_error),
                    'removed_headers': removed_headers, 'error': message}
        return {'markdown': '', 'segments': [], 'needs_review': True,
                'error': mineru_error or '图片原件已保留；安装 MinerU 后可在本机 OCR 提取文字。'}

    mineru_warning = ''
    if suffix in {'.docx', '.pptx'}:
        try:
            parsed = _extract_mineru(path, processed_dir)
            if parsed is not None:
                markdown, segments, removed_headers, has_unreadable_media = parsed
                return {
                    'markdown': markdown,
                    'segments': segments,
                    'needs_review': has_unreadable_media or not bool(segments),
                    'removed_headers': removed_headers,
                }
        except Exception as exc:
            mineru_warning = f'MinerU 未能解析嵌入图片，已回退到文字提取：{str(exc)[:300]}'

    if suffix == '.docx':
        text, segments = _parse_docx(path)
    elif suffix == '.pptx':
        text, segments = _parse_pptx(path)
    elif suffix == '.xlsx':
        text, segments = _parse_xlsx(path)
    elif suffix in RAW_ONLY_EXTENSIONS:
        return {'markdown': '', 'segments': [], 'needs_review': True,
                'error': '3D 原件已保留；纯文本模型无法直接读取模型几何，需补充文字说明。'}
    else:
        return {'markdown': '', 'segments': [], 'needs_review': False,
                'error': '该格式仅保留原件，当前不建立文本索引。'}
    return {
        'markdown': text,
        'segments': segments,
        'needs_review': not bool(segments) or bool(mineru_warning),
        'removed_headers': False,
        'error': mineru_warning,
    }


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = re.split(r'(?<=[。！？.!?；;])\s*|\n+', text)
    result = []
    current = ''
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > limit:
            if current:
                result.append(current)
                current = ''
            start = 0
            while start < len(part):
                end = min(start + limit, len(part))
                result.append(part[start:end])
                start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
            continue
        if current and len(current) + len(part) + 1 > limit:
            result.append(current)
            overlap = current[-CHUNK_OVERLAP_CHARS:] if CHUNK_OVERLAP_CHARS else ''
            current = (overlap + ' ' + part).strip()
        else:
            current = (current + ' ' + part).strip()
    if current:
        result.append(current)
    return result


def make_chunks(segments: list[Segment]) -> list[dict]:
    chunks = []
    buffer: list[Segment] = []
    length = 0

    def flush():
        nonlocal buffer, length
        if not buffer:
            return
        text = '\n'.join(item.text.strip() for item in buffer if item.text.strip())
        pages = [item.page for item in buffer if item.page is not None]
        sections = [item.section for item in buffer if item.section]
        chunks.append({
            'content': text,
            'page_start': min(pages) if pages else None,
            'page_end': max(pages) if pages else None,
            'section': sections[-1] if sections else '',
        })
        buffer = []
        length = 0

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        for part in _split_long(text):
            if buffer and length + len(part) > MAX_CHUNK_CHARS:
                flush()
            buffer.append(Segment(part, segment.page, segment.section))
            length += len(part)
    flush()
    return chunks


def document_summary(segments: list[Segment], fallback: str = '') -> str:
    text = ' '.join(segment.text.strip() for segment in segments if segment.text.strip())
    text = re.sub(r'[#|`*_>\[\]()]', ' ', text)
    text = ' '.join(text.split())
    return (text[:240] + ('…' if len(text) > 240 else '')) if text else fallback
