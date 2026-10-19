"""Build indexes for the approved notes folder; never imports eval/gold data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cpp_notes_rag.config import Settings
from cpp_notes_rag.indexing import IndexManager


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="为本人 Markdown/TXT 笔记构建 BM25 与本机向量索引")
    parser.add_argument("--bm25-only", action="store_true", help="只建 BM25，不下载或加载 embedding 模型")
    args = parser.parse_args()
    settings = Settings.load()
    try:
        manifest = IndexManager(settings).build(settings.source_dir, vector=not args.bm25_only)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"建库失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"documents": manifest["document_count"], "chunks": manifest["chunk_count"], "vector": manifest["vector"], "manifest": str(settings.storage_dir / "manifest.json")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
