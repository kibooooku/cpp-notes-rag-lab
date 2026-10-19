"""Reproducible local BM25 and explicitly embedded Chroma indexes."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chunking import chunk_documents
from .loaders import load_documents
from .schemas import Chunk, Hit, SourceSpan
from .tokenization import retrieval_text, tokenize

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
INDEX_VERSION = 1


def _record(value: Any) -> dict[str, Any]:
    return asdict(value) if is_dataclass(value) else value.model_dump()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class LocalEmbedding:
    """CPU BGE model with checks before every encode; no implicit truncation."""

    def __init__(self, settings: Any):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("向量检索依赖尚未安装，请按 README 安装 sentence-transformers、torch 和 chromadb。") from exc
        self.model = SentenceTransformer(
            settings.model_name,
            revision=settings.embedding_revision or None,
            device="cpu",
            cache_folder=str(Path(settings.storage_dir) / "model_cache"),
            trust_remote_code=False,
        )
        self.max_tokens = min(int(self.model.max_seq_length), 512)
        self.tokenizer = self.model.tokenizer
        first_module = self.model._first_module()
        self.resolved_revision = getattr(getattr(getattr(first_module, "auto_model", None), "config", None), "_commit_hash", None)

    def count_tokens(self, text: str) -> int:
        return self._token_count_exact(retrieval_text(text))

    def _token_count_exact(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False, verbose=False))

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        prepared = [QUERY_PREFIX + text if query else retrieval_text(text) for text in texts]
        for text in prepared:
            size = self._token_count_exact(text)
            if size > self.max_tokens:
                raise ValueError(f"向量输入为 {size} token，模型上限 {self.max_tokens}；请缩短问题或重新切片。输入未被截断。")
        vectors = self.model.encode(prepared, prompt="", normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, batch_size=16)
        result = vectors.tolist()
        if not result or any(not vector or not all(math.isfinite(float(x)) for x in vector) for vector in result):
            raise RuntimeError("本机 embedding 返回无效向量，索引未更新。")
        return result


class IndexManager:
    def __init__(self, settings: Any):
        self.settings = settings
        self.storage_dir = Path(settings.storage_dir)
        self.manifest: dict[str, Any] = {}
        self.chunks: list[Chunk] = []
        self._by_id: dict[str, Chunk] = {}
        self._bm25: Any = None
        self._embedding: LocalEmbedding | None = None
        self._client: Any = None
        self._collection: Any = None

    def _get_embedding(self) -> LocalEmbedding:
        if self._embedding is None:
            self._embedding = LocalEmbedding(self.settings)
        return self._embedding

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings
            except ImportError as exc:
                raise RuntimeError("Chroma 尚未安装，请按 README 安装向量检索依赖。") from exc
            self._client = chromadb.PersistentClient(
                path=str(self.storage_dir / "chroma"),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def build(self, source_dir: Path, vector: bool = True) -> dict[str, Any]:
        source_dir = Path(source_dir).resolve()
        approved = Path(self.settings.source_dir).resolve()
        if source_dir != approved:
            raise ValueError(f"仅允许为配置中的资料目录建库：{approved}；评测题和项目文档不得导入。")
        documents = load_documents([source_dir])
        if not documents:
            raise ValueError("资料目录没有可导入的非空 Markdown/TXT 文件。")
        embedding = self._get_embedding() if vector else None
        max_tokens = int(self.settings.chunk_max_tokens)
        if embedding:
            max_tokens = min(max_tokens, embedding.max_tokens)
        chunks = chunk_documents(documents, max_tokens=max_tokens, token_counter=embedding.count_tokens if embedding else None)
        if not chunks:
            raise ValueError("未生成有效片段，索引未更新。")
        records = [_record(chunk) for chunk in chunks]
        chunks_hash = _digest(records)
        chunk_sizes = [embedding.count_tokens(chunk.text) if embedding else len(chunk.text) + 2 for chunk in chunks]
        collection_name = f"cpp_notes_{chunks_hash[:20]}"
        vector_info: dict[str, Any] = {"enabled": False}
        if embedding:
            model_hash = _digest({"model": self.settings.model_name, "revision": embedding.resolved_revision or self.settings.embedding_revision})[:8]
            collection_name = f"cpp_notes_{chunks_hash[:16]}_{model_hash}"
            vectors = embedding.encode([chunk.text for chunk in chunks])
            collection = self._get_client().get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine", "chunks_hash": chunks_hash},
                embedding_function=None,
            )
            for offset in range(0, len(chunks), 128):
                batch = chunks[offset:offset + 128]
                collection.upsert(
                    ids=[chunk.chunk_id for chunk in batch],
                    documents=[chunk.text for chunk in batch],
                    embeddings=vectors[offset:offset + 128],
                    metadatas=[{"document_id": chunk.document_id, "source_file": chunk.spans[0].source_file} for chunk in batch],
                )
            if collection.count() != len(chunks):
                raise RuntimeError("向量库条数与片段不一致；未提交新 manifest，请重新建库。")
            vector_info = {
                "enabled": True, "collection": collection_name,
                "model_name": self.settings.model_name,
                "configured_revision": self.settings.embedding_revision,
                "resolved_revision": embedding.resolved_revision,
                "dimensions": len(vectors[0]), "metric": "cosine", "query_prefix": QUERY_PREFIX,
                "max_tokens": embedding.max_tokens,
            }
            self._collection = collection
        corpus = [tokenize(chunk.text + "\n" + " / ".join(span.heading for span in chunk.spans)) for chunk in chunks]
        if not any(corpus):
            raise ValueError("资料没有可检索的词项，索引未更新。")
        manifest = {
            "index_version": INDEX_VERSION,
            "built_at": datetime.now(UTC).isoformat(),
            "source_dir": str(source_dir),
            "documents": [{"document_id": doc.document_id, "source_file": doc.source_file, "sha256": doc.sha256, "format": doc.format} for doc in documents],
            "document_count": len(documents), "chunk_count": len(chunks),
            "chunks_hash": chunks_hash, "corpus_hash": chunks_hash,
            "bm25_tokens_hash": _digest(corpus), "chunk_max_tokens": max_tokens,
            "chunk_size_measure": "embedding_tokenizer" if embedding else "conservative_character_bound",
            "chunk_token_range": [min(chunk_sizes), max(chunk_sizes)],
            "vector": vector_info,
            "tokenization_version": "jieba-cpp-v1",
        }
        # The manifest is published last, so partial work never claims a valid
        # completed build. All retrievals verify the stored chunk hash on load.
        _write_json(self.storage_dir / "chunks.json", records)
        _write_json(self.storage_dir / "documents.json", [_record(doc) for doc in documents])
        _write_json(self.storage_dir / "bm25_tokens.json", corpus)
        _write_json(self.storage_dir / "manifest.json", manifest)
        self.manifest = manifest
        self.chunks = chunks
        self._by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self._load_bm25(corpus)
        return manifest

    def _load_bm25(self, corpus: list[list[str]]) -> None:
        from rank_bm25 import BM25Okapi

        self._bm25 = BM25Okapi(corpus)

    def load(self, method: str = "bm25") -> IndexManager:
        if method not in {"bm25", "vector"}:
            raise ValueError("检索方式必须是 bm25 或 vector。")
        manifest_path = self.storage_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("尚未建库，请先运行 python scripts/build_indexes.py。")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("index_version") != INDEX_VERSION:
            raise RuntimeError("索引版本不匹配，请重新建库。")
        raw_chunks = json.loads((self.storage_dir / "chunks.json").read_text(encoding="utf-8"))
        if _digest(raw_chunks) != self.manifest["chunks_hash"]:
            raise RuntimeError("片段文件与 manifest 不匹配，请重新建库。")
        self.chunks = [Chunk(**{**record, "spans": [SourceSpan(**span) for span in record["spans"]]}) for record in raw_chunks]
        self._by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        self._validate_sources()
        if method == "bm25":
            corpus = json.loads((self.storage_dir / "bm25_tokens.json").read_text(encoding="utf-8"))
            if len(corpus) != len(self.chunks) or _digest(corpus) != self.manifest.get("bm25_tokens_hash"):
                raise RuntimeError("BM25 词项与片段数不一致，请重新建库。")
            self._load_bm25(corpus)
        else:
            info = self.manifest["vector"]
            if not info.get("enabled"):
                raise RuntimeError("当前只建立了 BM25 索引，请运行 python scripts/build_indexes.py（不加 --bm25-only）。")
            matching_revisions = {info["configured_revision"], info.get("resolved_revision")}
            if info["model_name"] != self.settings.model_name or self.settings.embedding_revision not in matching_revisions:
                raise RuntimeError("当前 embedding 配置与向量索引不同，请恢复建库配置或重新建库。")
            self._collection = self._get_client().get_collection(name=info["collection"], embedding_function=None)
            if self._collection.count() != len(self.chunks):
                raise RuntimeError("Chroma 记录与片段数不一致，请重新建库。")
        return self

    def warmup(self, method: str = "bm25") -> None:
        self.load(method)
        if method == "vector":
            backend = self._get_embedding()
            self._check_embedding_revision(backend)
            backend.encode(["预热"], query=True)

    def _validate_sources(self) -> None:
        current = load_documents([Path(self.settings.source_dir)])
        expected = {(doc["document_id"], doc["sha256"]) for doc in self.manifest["documents"]}
        actual = {(doc.document_id, doc.sha256) for doc in current}
        if actual != expected:
            raise RuntimeError("资料已变更，现有索引过期；请重新建库以保持原文和引用一致。")

    def _check_embedding_revision(self, backend: LocalEmbedding) -> None:
        expected_revision = self.manifest["vector"].get("resolved_revision")
        if expected_revision and backend.resolved_revision and backend.resolved_revision != expected_revision:
            raise RuntimeError("本机模型 revision 与建库时不同，请使用已冻结模型或重新建库。")

    def retrieve(self, query: str, method: str = "bm25", k: int = 3) -> list[Hit]:
        if method not in {"bm25", "vector"}:
            raise ValueError("检索方式必须是 bm25 或 vector。")
        if not query.strip():
            raise ValueError("请输入非空问题。")
        if k < 1:
            raise ValueError("k 必须大于零。")
        if not self.chunks or (method == "bm25" and self._bm25 is None) or (method == "vector" and self._collection is None):
            self.load(method)
        self._validate_sources()
        if method == "bm25":
            query_tokens = tokenize(query)
            if not query_tokens:
                return []
            scores = self._bm25.get_scores(query_tokens)
            ranked = sorted(enumerate(scores), key=lambda item: (-float(item[1]), self.chunks[item[0]].chunk_id))
            positive = [(index, float(score)) for index, score in ranked if float(score) > 0][:k]
            return [Hit(chunk=self.chunks[index], score=score, rank=rank) for rank, (index, score) in enumerate(positive, start=1)]
        backend = self._get_embedding()
        self._check_embedding_revision(backend)
        vectors = backend.encode([query], query=True)
        result = self._collection.query(query_embeddings=vectors, n_results=min(k, len(self.chunks)), include=["distances"])
        ids = result["ids"][0]
        distances = result["distances"][0]
        hits = []
        for rank, (chunk_id, distance) in enumerate(zip(ids, distances, strict=True), start=1):
            if chunk_id not in self._by_id:
                raise RuntimeError("Chroma 返回未知 chunk_id，索引不一致，请重新建库。")
            hits.append(Hit(chunk=self._by_id[chunk_id], score=1.0 - float(distance), rank=rank))
        return hits
