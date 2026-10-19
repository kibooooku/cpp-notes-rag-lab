import json
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest


def test_initial_ui_and_method_switch_make_no_api_calls(monkeypatch):
    from cpp_notes_rag.generation import DeepSeekClient

    def forbid(*args, **kwargs):
        raise AssertionError("Page load must not call the generation API")

    monkeypatch.setattr(DeepSeekClient, "generate", forbid)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/cpp_notes_rag/app.py"))
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "从笔记找到答案"
    assert app.sidebar.title[0].value == "CppNotes"
    app.radio[0].set_value("向量 · 语义检索").run(timeout=30)
    assert not app.exception
    assert "向量" in app.radio[0].value


def test_form_submit_runs_once_and_switch_preserves_actual_method(tmp_path, monkeypatch):
    from cpp_notes_rag import service as service_module
    from cpp_notes_rag.generation import DeepSeekClient
    from cpp_notes_rag.schemas import Chunk, Hit, SourceSpan

    root = tmp_path
    source = root / "问答素材"
    source.mkdir()
    text = "引用绑定后，ref = b 是赋值。"
    (source / "reference.md").write_text(text, encoding="utf-8")
    storage = root / "storage"
    storage.mkdir()
    (storage / "manifest.json").write_text(json.dumps({"document_count": 1, "chunk_count": 1}), encoding="utf-8")
    hit = Hit(Chunk("app-c1", "app-d1", text, [SourceSpan("reference.md", "引用", 1, 1)]), 1.0, 1)
    calls = []

    class Index:
        def __init__(self, settings):
            self.manifest = {"corpus_hash": "app-test"}

        def retrieve(self, query, method, k):
            return [hit]

    def generate(self, question, hits):
        calls.append(question)
        parsed = {"status": "supported", "answer": "对原绑定对象赋值。[1]", "citations": [
            {"chunk_id": "app-c1", "quote": text}]}
        return parsed, json.dumps(parsed), {}

    monkeypatch.setenv("CPP_RAG_ROOT", str(root))
    monkeypatch.setattr(service_module, "IndexManager", Index)
    monkeypatch.setattr(DeepSeekClient, "generate", generate)
    st.cache_resource.clear()
    try:
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/cpp_notes_rag/app.py"))
        app.run(timeout=30)
        app.text_area[0].set_value("ref = b 做什么？")
        next(button for button in app.button if button.label == "检索并回答").click().run(timeout=30)
        assert not app.exception
        assert len(calls) == 1
        assert any("原绑定对象赋值" in markdown.value for markdown in app.markdown)
        app.radio[0].set_value("向量 · 语义检索").run(timeout=30)
        assert not app.exception
        assert len(calls) == 1
        assert any("实际方法：bm25" in caption.value for caption in app.caption)
    finally:
        st.cache_resource.clear()
