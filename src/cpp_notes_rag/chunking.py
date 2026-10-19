"""Source-mapped Markdown/TXT chunks with explicit, lossless long-block splits."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from markdown_it import MarkdownIt

from .schemas import Chunk, Document, SourceSpan

# Flags annotate pending review without changing or 'correcting' original notes.
# The table is the material-review.md M01–M19 line map, not an adjudication.
_REVIEW_RANGES = {
    "1": [(12, 15, "M01"), (20, 21, "M02")],
    "2": [(3, 10, "M03"), (24, 37, "M04")],
    "3": [(2, 3, "M05"), (24, 27, "M05")],
    "4": [(16, 40, "M06"), (43, 43, "M07"), (45, 50, "M08")],
    "5": [(36, 59, "M09"), (65, 65, "M10"), (130, 130, "M10"), (73, 121, "M11")],
    "6": [(8, 8, "M12"), (26, 27, "M12")],
    "7": [(30, 34, "M13"), (39, 52, "M14"), (158, 158, "M15"), (174, 185, "M15")],
    "8": [(58, 58, "M16"), (72, 72, "M16")],
    "9": [(5, 5, "M17"), (19, 20, "M17"), (56, 56, "M17"), (65, 79, "M18"), (81, 86, "M19")],
}
_REVIEW_SOURCE_NAMES = {
    "1运作原理 编译链接.md", "2变量 头文件 调试 条件.md", "3指针 引用.md",
    "4类和结构体 static ODR.md", "5单例模式和局部静态.md", "6枚举 构造函数 析构函数.md",
    "7多态 虚函数 初始化列表 智能指针.md", "8纯虚函数（接口） 可见性.md", "9数组 字符串.md",
}


def _flags(source_file: str, start: int, end: int) -> list[str]:
    canonical_name = source_file.rsplit(".", 1)[0] + ".md"
    if canonical_name not in _REVIEW_SOURCE_NAMES:
        return []
    return sorted({f"{flag}:资料待核" for lo, hi, flag in _REVIEW_RANGES.get(source_file[:1], []) if start <= hi and end >= lo})


def _blocks(document: Document) -> list[tuple[int, int, str, str]]:
    """Return non-overlapping [start,end) raw line blocks and heading paths."""
    lines = document.text.splitlines(keepends=True)
    # A TXT copy may contain Markdown source. Parsing both consistently preserves
    # identity, code boundaries, and citations when validating either format.
    tokens = MarkdownIt("commonmark", {"html": True}).parse(document.text)
    heading_stack: list[tuple[int, str]] = []
    candidates: list[tuple[int, int, str, str]] = []
    for index, token in enumerate(tokens):
        if token.level != 0 or token.map is None or token.type in {"inline", "heading_close"}:
            continue
        start, end = token.map
        if token.type == "heading_open":
            level = int(token.tag[1:])
            title = tokens[index + 1].content.strip() if index + 1 < len(tokens) else ""
            heading_stack = [(n, h) for n, h in heading_stack if n < level]
            heading_stack.append((level, title))
        heading = " / ".join(title for _, title in heading_stack) or "无标题（原文）"
        # Ignore nested maps (e.g. list items) already captured by parent.
        if candidates and start < candidates[-1][1]:
            continue
        candidates.append((start, end, heading, token.type))
    if not candidates and lines:
        candidates = [(0, len(lines), "无标题（原文）", "paragraph_open")]
    return candidates


def chunk_documents(
    documents: list[Document],
    max_tokens: int = 480,
    *,
    token_counter: Callable[[str], int] | None = None,
) -> list[Chunk]:
    """Chunk raw contiguous source, never silently truncating code or sentences.

    Without a model the conservative character bound is deterministic and offline.
    Vector builds pass the actual embedding tokenizer (including special tokens).
    Oversized code fences are split at source line boundaries, preferring closed
    class/function boundaries. A single oversized line raises an actionable error.
    """
    if max_tokens < 16:
        raise ValueError("chunk_max_tokens 至少为 16。")
    count = token_counter or (lambda text: len(text) + 2)
    chunks: list[Chunk] = []
    for document in documents:
        lines = document.text.splitlines(keepends=True)
        pending: tuple[int, int, str] | None = None

        def emit(start: int, end: int, heading: str, extra_flags: list[str] | None = None, *, _document: Document = document, _lines: list[str] = lines) -> None:
            text = "".join(_lines[start:end]).rstrip("\r\n")
            if not text.strip():
                return
            if count(text) > max_tokens:
                raise ValueError(f"{_document.source_file}:L{start + 1}–L{end} 超过 {max_tokens} token；请拆分这一原文长行后重新导入，未截断内容。")
            chunk_id = hashlib.sha256(f"{_document.document_id}:{start + 1}:{end}:{text}".encode()).hexdigest()[:24]
            chunks.append(Chunk(chunk_id=chunk_id, document_id=_document.document_id, text=text,
                                spans=[SourceSpan(source_file=_document.source_file, heading=heading, line_start=start + 1, line_end=end)],
                                review_flags=_flags(_document.source_file, start + 1, end) + (extra_flags or [])))

        def flush() -> None:
            nonlocal pending
            if pending is not None:
                emit(*pending)
                pending = None

        for start, end, heading, kind in _blocks(document):
            raw = "".join(lines[start:end]).rstrip("\r\n")
            if count(raw) > max_tokens:
                flush()
                # Preserve every raw line; find the largest fitting line group,
                # preferring a nearby full C++ declaration / function boundary.
                cursor = start
                while cursor < end:
                    stop = cursor
                    while stop < end and count("".join(lines[cursor:stop + 1]).rstrip("\r\n")) <= max_tokens:
                        stop += 1
                    if stop == cursor:
                        emit(cursor, cursor + 1, heading)
                    preferred = [i for i in range(cursor + 1, stop + 1) if re.match(r"^\s*}(?:;)?\s*$", lines[i - 1])]
                    # Don't split early if it leaves tiny fragments. Fences are
                    # raw source, and their split status is explicit in metadata.
                    if preferred and preferred[-1] >= cursor + max(2, (stop - cursor) // 2):
                        stop = preferred[-1]
                    has_code = kind in {"fence", "code_block"} or bool(re.search(r"(?m)^\s*(?:`{3,}|~{3,})", raw))
                    split_flags = [f"原始代码块分片:L{start + 1}–L{end}"] if has_code else []
                    emit(cursor, stop, heading, split_flags)
                    cursor = stop
                continue
            if pending is not None:
                p_start, _p_end, p_heading = pending
                combined = "".join(lines[p_start:end]).rstrip("\r\n")
                if p_heading == heading and count(combined) <= max_tokens:
                    pending = (p_start, end, heading)
                    continue
                flush()
            pending = (start, end, heading)
        flush()
    return chunks
