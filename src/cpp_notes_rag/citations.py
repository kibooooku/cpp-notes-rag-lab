"""Only references to retrieved evidence with exact source substrings are accepted."""

from pathlib import Path


class CitationError(ValueError):
    pass


def validate_response(response, hits, source_dir: Path):
    status = response.get("status")
    answer = response.get("answer")
    references = response.get("citations")
    if status not in {"supported", "partial", "refused", "uncertain"} or not isinstance(answer, str):
        raise CitationError("模型状态或正文不符合约定。")
    if not answer.strip() or not isinstance(references, list):
        raise CitationError("回答正文或引用缺失。")
    if status == "refused":
        if references or answer.strip() != "文档中没有相关信息":
            raise CitationError("无依据拒答必须使用约定文本且无引用。")
        return status, answer, []
    if not references:
        raise CitationError("有实质内容的回答必须提供真实原文引用。")
    by_id = {h.chunk.chunk_id: h.chunk for h in hits}
    citations = []
    for ref in references:
        if not isinstance(ref, dict):
            raise CitationError("引用结构错误。")
        chunk = by_id.get(ref.get("chunk_id"))
        if chunk is None:
            raise CitationError("引用 ID 不在本轮证据内。")
        # The current protocol asks the model for IDs only. Source excerpts are
        # selected deterministically, so NBSP/code whitespace is never retyped.
        # If an older client supplies a quote, it still must match exactly.
        quote = ref.get("quote", chunk.text)
        if not isinstance(quote, str) or not quote.strip() or quote not in chunk.text:
            raise CitationError("引用 ID 不在本轮证据内，或引文并非逐字原文。")
        source_match = None
        for span in chunk.spans:
            path = (source_dir / span.source_file).resolve()
            if not path.is_relative_to(source_dir.resolve()) or not path.is_file():
                raise CitationError("引用路径不属于当前资料目录。")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            source = "\n".join(lines[span.line_start - 1:span.line_end])
            if quote in source:
                offset = source[:source.index(quote)].count("\n")
                source_match = {"chunk_id": chunk.chunk_id, "source_file": span.source_file,
                                "heading": span.heading, "line_start": span.line_start + offset,
                                "line_end": span.line_start + offset + quote.count("\n"),
                                "quote": quote, "review_flags": chunk.review_flags,
                                "excerpt_origin": "model_exact_quote" if "quote" in ref else "source_chunk"}
                break
        if source_match is None:
            raise CitationError("引文无法在原文件对应行中验证。")
        if source_match not in citations:
            citations.append(source_match)
    return status, answer, citations
