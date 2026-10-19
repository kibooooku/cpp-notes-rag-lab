"""Chinese and C++ lexical features; original citation text is never edited."""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CODE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)*|\d+|::|\[\]|->|&&|\*|&|~|=|\+\+")
_STOP = {"的", "了", "是", "在", "和", "与", "中", "有", "请", "什么", "哪些", "如何", "怎么", "为什么", "笔记", "根据", "一个", "可以", "是否", "以及"}
_STYLE_TAG = re.compile(r"</?(?:span|div|mark|font|u|br)(?:\s[^>]*)?/?>", re.IGNORECASE)
_INLINE_CODE = re.compile(r"(`+)(.*?)(?<!`)\1(?!`)")


def _strip_prose_style(line: str) -> str:
    output: list[str] = []
    cursor = 0
    for code in _INLINE_CODE.finditer(line):
        output.append(_STYLE_TAG.sub("", line[cursor:code.start()]))
        output.append(code.group())
        cursor = code.end()
    output.append(_STYLE_TAG.sub("", line[cursor:]))
    return "".join(output)


def retrieval_text(text: str) -> str:
    """Remove known prose styling tags outside fences, retaining C++ exactly."""
    output: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = None
            output.append(line)
        else:
            output.append(line if fence else _strip_prose_style(line))
    return "".join(output)


def tokenize(text: str) -> list[str]:
    """Keep qualified names and operators while adding Chinese word features."""
    import jieba

    text = retrieval_text(text)
    tokens: list[str] = []
    for run in _CJK.findall(text):
        words = [word for word in jieba.cut(run, HMM=False) if word.strip() and word not in _STOP]
        tokens.extend(words)
        # Bigrams make compound/unknown Chinese names searchable without erasing
        # lexical segmentation (a query need not repeat the note's exact phrase).
        if len(run) > 1:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1) if run[i:i + 2] not in _STOP)
    for token in _CODE.findall(text):
        normalized = token.casefold()
        tokens.append(normalized)
        if "::" in normalized:
            tokens.extend(normalized.split("::"))
    for pattern in (r"\bconst\s+[A-Za-z_][\w:]*\s*\*", r"\b[A-Za-z_][\w:]*\s*\*\s*const\b", r"=\s*(?:delete|default|0)\b", r"delete\s*\[\s*\]"):
        tokens.extend(re.sub(r"\s+", "", match).casefold() for match in re.findall(pattern, text))
    return tokens
