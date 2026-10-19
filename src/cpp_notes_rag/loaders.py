"""UTF-8 personal-note imports with stable, format-independent identities."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .schemas import Document

SUPPORTED_FORMATS = {".md", ".txt"}


def _normalized_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_documents(paths: list[Path]) -> list[Document]:
    """Read Markdown/TXT only, preferring MD for identical same-stem copies.

    A TXT acceptance copy must retain its original Markdown source text, including
    line breaks, for its identity and original line citations to remain identical.
    Distinct content with the same name is never silently discarded.
    """
    candidates: dict[Path, str] = {}
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"资料不存在：{path}")
        if path.is_dir():
            for candidate in path.rglob("*"):
                if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_FORMATS:
                    continue
                resolved = candidate.resolve()
                if not resolved.is_relative_to(path):
                    raise ValueError(f"资料链接指向批准目录之外，未导入：{candidate.name}")
                candidates[resolved] = candidate.relative_to(path).as_posix()
        elif path.suffix.lower() in SUPPORTED_FORMATS:
            candidates.setdefault(path, path.name)
        else:
            raise ValueError(f"不支持的资料格式 {path.suffix!r}；请使用 Markdown (.md) 或 UTF-8 TXT (.txt)。")

    documents: list[Document] = []
    seen: set[str] = set()
    ordered = sorted(candidates, key=lambda p: (p.stem.casefold(), p.suffix.lower() != ".md", str(p)))
    for path in ordered:
        raw = path.read_bytes()
        try:
            text = _normalized_text(raw.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path.name} 不是有效 UTF-8；请另存为 UTF-8 后导入。") from exc
        if not text.strip():
            continue
        identity_text = f"{path.stem.casefold()}\0{text}"
        document_id = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:24]
        if document_id in seen:
            continue
        seen.add(document_id)
        documents.append(Document(
            document_id=document_id,
            source_file=candidates[path],
            format=path.suffix.lower().lstrip("."),
            text=text,
            sha256=hashlib.sha256(raw).hexdigest(),
        ))
    return documents
