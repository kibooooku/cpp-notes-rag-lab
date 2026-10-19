"""Orchestrate one independent question, evidence, generation, citations and logs."""

import hashlib
import json
import time
import uuid
from dataclasses import asdict

from filelock import FileLock

from .citations import validate_response
from .generation import SYSTEM_PROMPT, DeepSeekClient
from .indexing import IndexManager
from .schemas import Answer


class QAService:
    def __init__(self, settings, index=None, client=None):
        self.settings = settings
        self.index = index or IndexManager(settings)
        self.client = client or DeepSeekClient(settings)
        self.config_hash = hashlib.sha256((settings.config_hash + SYSTEM_PROMPT).encode()).hexdigest()

    @property
    def corpus_hash(self):
        return self.index.manifest.get("corpus_hash", self.index.manifest.get("chunks_hash", ""))

    def warmup(self, method):
        self.index.warmup(method)

    def ask(self, question, method="bm25"):
        start = time.perf_counter()
        result = Answer(question=question, method=method, status="error", answer="", run_id=uuid.uuid4().hex,
                        config_hash=self.config_hash)
        phase_start = start
        try:
            if method not in {"bm25", "vector"}:
                raise ValueError("检索方法必须是 bm25 或 vector。")
            if not isinstance(question, str) or not question.strip() or len(question) > 1500:
                raise ValueError("请输入 1–1500 字的问题。")
            result.hits = self.index.retrieve(question.strip(), method, k=self.settings.top_k)
            result.retrieval_seconds = time.perf_counter() - phase_start
            result.corpus_hash = self.corpus_hash
            threshold = (self.settings.bm25_min_score if method == "bm25"
                         else self.settings.vector_min_score)
            usable = [h for h in result.hits if h.score > threshold]
            if not usable:
                result.status, result.answer = "refused", "文档中没有相关信息"
            else:
                phase_start = time.perf_counter()
                parsed, result.raw_response, result.usage = self.client.generate(question.strip(), usable)
                result.generation_seconds = time.perf_counter() - phase_start
                result.status, result.answer, result.citations = validate_response(
                    parsed, usable, self.settings.source_dir)
        except Exception as exc:  # noqa: BLE001 -- Persist runtime failures as errors, never as refusals.
            # All public exception strings are deliberately safe; do not include raw HTTP request headers.
            from .citations import CitationError
            from .cost_tracker import BudgetError
            from .generation import GenerationError
            safe = isinstance(exc, (ValueError, FileNotFoundError, GenerationError, CitationError, BudgetError))
            result.error = str(exc) if safe else f"{type(exc).__name__}：系统处理失败，请检查索引/运行环境。"
            result.raw_response = getattr(exc, "raw_response", result.raw_response)
            result.usage = getattr(exc, "usage", result.usage)
            result.answer = "请求未完成：" + result.error
            result.status = "error"
            result.generation_seconds = time.perf_counter() - phase_start if result.hits else 0.0
        result.latency_seconds = time.perf_counter() - start
        self._log(result)
        return result

    def _log(self, result):
        path = self.settings.storage_dir / "qa_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=10), path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
