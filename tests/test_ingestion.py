from pathlib import Path

import pytest

from cpp_notes_rag.chunking import chunk_documents
from cpp_notes_rag.loaders import load_documents
from cpp_notes_rag.tokenization import retrieval_text, tokenize


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_md_txt_same_origin_is_deduplicated_with_stable_identity(tmp_path):
    text = "# 引用\n```cpp\nint& ref = a;\nref = b;\n```\n"
    md = write(tmp_path / "note.md", text)
    txt = tmp_path / "note.txt"
    txt.write_bytes(("\ufeff" + text.replace("\n", "\r\n")).encode("utf-8"))
    documents = load_documents([txt, md])
    assert len(documents) == 1
    assert documents[0].source_file == "note.md"
    assert load_documents([txt])[0].document_id == documents[0].document_id
    # Changed contents under the same stem are distinct versions, not discarded.
    write(txt, text + "这是补充内容。\n")
    assert len(load_documents([md, txt])) == 2


def test_nested_sources_keep_unambiguous_relative_paths(tmp_path):
    first = tmp_path / "chapter1"
    second = tmp_path / "chapter2"
    first.mkdir()
    second.mkdir()
    write(first / "note.md", "第一章的不同内容。")
    write(second / "note.md", "第二章的不同内容。")
    documents = load_documents([tmp_path])
    assert {doc.source_file for doc in documents} == {"chapter1/note.md", "chapter2/note.md"}
    for chunk in chunk_documents(documents):
        assert (tmp_path / chunk.spans[0].source_file).is_file()


def test_raw_code_and_inclusive_source_positions_survive_chunking(tmp_path):
    text = '# 指针与引用\n正文 <span style="color:red">原样保留</span>。\n\n```cpp\n#include <iostream>\nstd::unique_ptr<int[]> p;\nconst T* a; T* const b = nullptr;\nvirtual ~Entity() = default;\nEntity() = delete;\nvoid f() = 0;\nref = b; ptr = &b; delete[] p;\n```\n'
    document = load_documents([write(tmp_path / "cpp.md", text)])[0]
    chunks = chunk_documents([document], max_tokens=480)
    joined = "\n".join(chunk.text for chunk in chunks)
    for line in text.splitlines():
        if line.strip():
            assert line in joined
    for chunk in chunks:
        span = chunk.spans[0]
        source_slice = "\n".join(text.splitlines()[span.line_start - 1:span.line_end])
        assert chunk.text == source_slice
        assert span.heading == "指针与引用"


def test_long_code_is_lossless_and_explicitly_marked(tmp_path):
    text = "# 类\n```cpp\n" + "\n".join(f"int member_{i} = {i};" for i in range(60)) + "\n```\n"
    document = load_documents([write(tmp_path / "code.md", text)])[0]
    chunks = chunk_documents([document], max_tokens=180)
    assert len(chunks) > 2
    assert any("原始代码块分片" in flag for chunk in chunks for flag in chunk.review_flags)
    flattened = "\n".join(chunk.text for chunk in chunks)
    for line in text.splitlines():
        if line.strip():
            assert line in flattened
    assert all(len(chunk.text) + 2 <= 180 for chunk in chunks)


def test_oversized_single_line_raises_instead_of_truncating(tmp_path):
    document = load_documents([write(tmp_path / "long.txt", "原" * 600)])[0]
    with pytest.raises(ValueError, match="未截断"):
        chunk_documents([document], max_tokens=480)


def test_style_cleanup_cannot_erase_cpp_templates_and_const_distinction():
    raw = '<span style="color:red">引用</span>\n`std::vector<span>`\n```cpp\n#include <iostream>\nstd::vector<int> values;\nconst T* a; T* const b;\n```\n'
    clean = retrieval_text(raw)
    assert "<span style=" not in clean
    assert "`std::vector<span>`" in clean
    assert "#include <iostream>" in clean
    assert "std::vector<int>" in clean
    terms = tokenize(raw)
    assert "constt*" in terms
    assert "t*const" in terms
    assert "std::vector" in terms
    assert "*" in terms


def test_pending_material_review_flags_follow_original_lines(tmp_path):
    text = "\n" * 42 + "ODR 的绝对化说法。\n"
    document = load_documents([write(tmp_path / "4类和结构体 static ODR.md", text)])[0]
    chunk = chunk_documents([document])[0]
    assert "M07:资料待核" in chunk.review_flags
    assert chunk.spans[0].line_start == 43
