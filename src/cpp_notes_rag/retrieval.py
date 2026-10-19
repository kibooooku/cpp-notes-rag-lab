"""Public retrieval interface shared by CLI, web UI, and controlled experiments."""

from .indexing import IndexManager
from .schemas import Hit


def retrieve(query: str, method: str, index: IndexManager, k: int = 3) -> list[Hit]:
    return index.retrieve(query, method=method, k=k)


__all__ = ["IndexManager", "retrieve"]
