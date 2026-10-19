import json

import pytest

from cpp_notes_rag.config import Settings
from cpp_notes_rag.indexing import IndexManager, LocalEmbedding


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / "notes"
    source.mkdir()
    (source / "pointers.md").write_text("# 指针\n空指针使用 nullptr，指针保存地址。", encoding="utf-8")
    (source / "refs.md").write_text("# 引用\n引用必须初始化并绑定对象。", encoding="utf-8")
    (source / "compile.md").write_text("# 编译\n预处理、编译、汇编、链接。", encoding="utf-8")
    return Settings(root=tmp_path, source_dir=source, storage_dir=tmp_path / "storage")


def test_bm25_build_load_and_source_changes_invalidate_index(settings):
    index = IndexManager(settings)
    manifest = index.build(settings.source_dir, vector=False)
    assert manifest["chunk_count"] == 3
    assert manifest["corpus_hash"] == manifest["chunks_hash"]
    loaded = IndexManager(settings).load("bm25")
    hits = loaded.retrieve("nullptr", "bm25")
    assert hits[0].chunk.spans[0].source_file == "pointers.md"
    assert hits[0].score > 0
    assert loaded.retrieve("a_term_absent_everywhere", "bm25") == []
    (settings.source_dir / "refs.md").write_text("引用内容已更新。", encoding="utf-8")
    with pytest.raises(RuntimeError, match="过期"):
        IndexManager(settings).load("bm25")


def test_bm25_token_tampering_is_detected(settings):
    IndexManager(settings).build(settings.source_dir, vector=False)
    path = settings.storage_dir / "bm25_tokens.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    corpus[0] = ["malicious_wrong_terms"]
    path.write_text(json.dumps(corpus), encoding="utf-8")
    with pytest.raises(RuntimeError, match="不一致"):
        IndexManager(settings).load("bm25")


class FakeEmbedding:
    max_tokens = 512
    resolved_revision = "fake-immutable-revision"

    def __init__(self, settings):
        self.calls = []

    def count_tokens(self, text):
        return len(text) + 2

    def encode(self, texts, *, query=False):
        self.calls.append((texts, query))
        return [[1.0, 0.0] if "nullptr" in text else [0.0, 1.0] for text in texts]


class FakeCollection:
    def __init__(self):
        self.records = {}
        self.query_arguments = None

    def upsert(self, *, ids, embeddings, documents, metadatas):
        for ident, vector, text in zip(ids, embeddings, documents, strict=True):
            self.records[ident] = (vector, text)

    def count(self):
        return len(self.records)

    def query(self, **kwargs):
        self.query_arguments = kwargs
        assert "query_embeddings" in kwargs
        assert "query_texts" not in kwargs
        query = kwargs["query_embeddings"][0]
        ranked = sorted(((ident, 1 - sum(a * b for a, b in zip(query, vector))) for ident, (vector, _) in self.records.items()), key=lambda pair: pair[1])[:kwargs["n_results"]]
        return {"ids": [[row[0] for row in ranked]], "distances": [[row[1] for row in ranked]]}


class FakeClient:
    def __init__(self):
        self.collection = FakeCollection()

    def get_or_create_collection(self, **kwargs):
        assert kwargs["embedding_function"] is None
        assert kwargs["metadata"]["hnsw:space"] == "cosine"
        return self.collection

    def get_collection(self, **kwargs):
        assert kwargs["embedding_function"] is None
        return self.collection


def test_vector_and_bm25_share_exact_chunk_ids_and_explicit_vectors(settings, monkeypatch):
    monkeypatch.setattr("cpp_notes_rag.indexing.LocalEmbedding", FakeEmbedding)
    client = FakeClient()
    monkeypatch.setattr(IndexManager, "_get_client", lambda self: client)
    index = IndexManager(settings)
    manifest = index.build(settings.source_dir, vector=True)
    assert set(client.collection.records) == {chunk.chunk_id for chunk in index.chunks}
    assert manifest["vector"]["dimensions"] == 2
    assert manifest["vector"]["resolved_revision"] == FakeEmbedding.resolved_revision
    # Reload proves IDs map to persisted raw chunks, not a different text list.
    reloaded = IndexManager(settings).load("vector")
    hits = reloaded.retrieve("nullptr", "vector")
    assert hits[0].chunk.spans[0].source_file == "pointers.md"
    assert hits[0].score == 1.0
    assert reloaded._embedding.calls[-1][1] is True
    assert client.collection.query_arguments["n_results"] == 3
    assert IndexManager(settings).load("bm25").retrieve("nullptr", "bm25")[0].chunk.chunk_id == hits[0].chunk.chunk_id


def test_actual_embedding_length_guard_rejects_before_model_encode():
    backend = LocalEmbedding.__new__(LocalEmbedding)
    backend.max_tokens = 512
    backend._token_count_exact = lambda text: 513
    class NeverEncode:
        def encode(self, *args, **kwargs):
            pytest.fail("oversized input reached model.encode and could be truncated")
    backend.model = NeverEncode()
    with pytest.raises(ValueError, match="未被截断"):
        backend.encode(["超长输入"])


def test_embedding_uses_clean_copy_without_mutating_source():
    source = '<span style="color:red">引用</span>\n```cpp\nstd::vector<int> x;\n```'
    backend = LocalEmbedding.__new__(LocalEmbedding)
    backend.max_tokens = 512
    backend._token_count_exact = lambda text: len(text) + 2

    class FakeArray:
        def tolist(self):
            return [[1.0, 0.0]]

    class CapturingModel:
        def encode(self, texts, **kwargs):
            assert "<span style=" not in texts[0]
            assert "std::vector<int>" in texts[0]
            return FakeArray()

    backend.model = CapturingModel()
    assert backend.encode([source]) == [[1.0, 0.0]]
    assert '<span style="color:red">' in source


def test_vector_revision_mismatch_is_not_silently_accepted(settings, monkeypatch):
    monkeypatch.setattr("cpp_notes_rag.indexing.LocalEmbedding", FakeEmbedding)
    client = FakeClient()
    monkeypatch.setattr(IndexManager, "_get_client", lambda self: client)
    IndexManager(settings).build(settings.source_dir, vector=True)
    reloaded = IndexManager(settings).load("vector")
    embedding = FakeEmbedding(settings)
    embedding.resolved_revision = "different-revision"
    reloaded._embedding = embedding
    with pytest.raises(RuntimeError, match="revision"):
        reloaded.retrieve("nullptr", "vector")


def test_build_rejects_project_or_eval_directory(settings):
    with pytest.raises(ValueError, match="评测题"):
        IndexManager(settings).build(settings.root, vector=False)
