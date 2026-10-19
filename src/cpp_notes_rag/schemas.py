"""Shared data contracts. Store source positions as inclusive, one-based lines."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceSpan:
    source_file: str
    heading: str
    line_start: int
    line_end: int


@dataclass
class Document:
    document_id: str
    source_file: str
    format: str
    text: str
    sha256: str


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    spans: list[SourceSpan]
    review_flags: list[str] = field(default_factory=list)


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int


@dataclass
class Answer:
    question: str
    method: str
    status: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    latency_seconds: float = 0.0
    retrieval_seconds: float = 0.0
    generation_seconds: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    raw_response: str | None = None
    run_id: str = ""
    corpus_hash: str = ""
    config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
