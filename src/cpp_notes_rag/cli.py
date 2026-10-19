"""CLI companion to the local Streamlit interface."""

import argparse
import json

from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="个人 C++ 笔记问答")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="建立双检索索引（首次下载本机向量模型）")
    build.add_argument("--bm25-only", action="store_true")
    ask = sub.add_parser("ask", help="独立问题，调用 API 回答")
    ask.add_argument("question")
    ask.add_argument("--method", choices=["bm25", "vector"], default="bm25")
    retrieve = sub.add_parser("retrieve", help="仅检索，不调用 API")
    retrieve.add_argument("question")
    retrieve.add_argument("--method", choices=["bm25", "vector"], default="bm25")
    sub.add_parser("status", help="显示本机索引和预算状态")
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "build":
        from .indexing import IndexManager
        result = IndexManager(settings).build(settings.source_dir, vector=not args.bm25_only)
    elif args.command == "ask":
        from .service import QAService
        service = QAService(settings)
        service.warmup(args.method)
        result = service.ask(args.question, args.method).to_dict()
    elif args.command == "retrieve":
        from dataclasses import asdict

        from .indexing import IndexManager
        index = IndexManager(settings)
        index.warmup(args.method)
        result = [asdict(h) for h in index.retrieve(args.question, args.method)]
    else:
        from .cost_tracker import CostTracker
        path = settings.storage_dir / "manifest.json"
        result = {"index": json.loads(path.read_text(encoding="utf-8")) if path.exists() else None,
                  "budget": CostTracker(settings).snapshot()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(args.command == "ask" and result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
