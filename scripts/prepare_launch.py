"""Local launch preflight. Reuse healthy indexes; no paid API requests."""

import argparse
import importlib.util
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def prepare(port: int, skip_port_check: bool = False) -> None:
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise RuntimeError("需要 Python 3.12–3.14，建议按 README 使用 Python 3.12 重建 .venv。")
    required = ("streamlit", "httpx", "markdown_it", "jieba", "rank_bm25", "numpy",
                "sentence_transformers", "transformers", "chromadb", "filelock")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError("缺少依赖：" + ", ".join(missing) + "。请先完成 README 的首次安装。")
    if not skip_port_check:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"本机端口 {port} 不可用，可能已有服务正在运行。请关闭原启动窗口后再试，"
                    "或用 start.bat -Port 8502 选择其他端口；不会终止已有进程。"
                ) from exc

    from chromadb.errors import NotFoundError
    from filelock import FileLock, Timeout

    from cpp_notes_rag.config import Settings
    from cpp_notes_rag.cost_tracker import CostTracker
    from cpp_notes_rag.indexing import IndexManager

    settings = Settings.load(ROOT)
    settings.api_key()  # Validate presence only. Never display or send the key here.
    CostTracker(settings).snapshot()  # A damaged budget ledger must not be reset.
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(settings.storage_dir / "launch_prepare.lock"), timeout=1):
            index = IndexManager(settings)
            try:
                index.load("bm25")
                index.load("vector")
                if index.manifest.get("chunk_max_tokens") != settings.chunk_max_tokens:
                    raise ValueError("切片配置已变化。")
            except (FileNotFoundError, ValueError, KeyError, RuntimeError, NotFoundError):
                print("[准备] 索引缺失、过期或配置变化，正在重建双检索索引。", flush=True)
                print("首次可能下载本机向量模型，请保持窗口开启；这一步不调用问答 API。", flush=True)
                index.build(settings.source_dir, vector=True)
            print(f"[就绪] {index.manifest['document_count']} 篇笔记 / "
                  f"{index.manifest['chunk_count']} 个片段；凭证与费用账本检查通过。", flush=True)
    except Timeout as exc:
        raise RuntimeError("另一个启动窗口正在准备索引，请等待该窗口完成，不要重复启动。") from exc


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--skip-port-check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.port, args.skip_port_check)
    except Exception as exc:  # noqa: BLE001 -- Show startup failures and preserve the launch window.
        # Known local configuration/index errors contain no credential values.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError, OSError)) else type(exc).__name__
        print(f"[启动失败] {message}\n请参照 README.md 的安装与启动说明处理。", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
