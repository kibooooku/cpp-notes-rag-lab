"""Run independent questions through both retrievers, preserving raw responses."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cpp_notes_rag.evaluation import (
    EvaluationError,
    chunk_snapshot,
    load_questions,
    run_experiment,
    source_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Use separate development questions; never produce formal grades")
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New empty run directory")
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--methods", nargs="+", choices=["bm25", "vector"], default=["bm25", "vector"])
    parser.add_argument("--materials", type=Path, help="Must match configured source_dir")
    parser.add_argument("--chunks", type=Path, help="Must match configured storage_dir/chunks.json")
    args = parser.parse_args()
    questions_path = args.questions or ROOT / "eval" / ("dev_questions.json" if args.dev else "questions.json")
    try:
        from cpp_notes_rag.config import Settings
        settings = Settings.load(ROOT)
        materials = args.materials or settings.source_dir
        chunks = args.chunks or settings.storage_dir / "chunks.json"
        if materials.resolve() != settings.source_dir.resolve() or chunks.resolve() != (settings.storage_dir / "chunks.json").resolve():
            raise EvaluationError("Snapshot paths must match the actual configured QAService corpus/index")
        sources = source_snapshot(materials)
        chunks_hash = chunk_snapshot(chunks)
        questions = load_questions(questions_path, formal=not args.dev, snapshot=sources, chunks_sha256=chunks_hash)
        if args.dev and any(not q["question_id"].startswith("D") for q in questions):
            raise EvaluationError("Development questions need independent D-prefixed IDs; do not tune against formal Q01–Q20")
        # Delay imports and API client initialization until the gold-review gate passes.
        from cpp_notes_rag.service import QAService

        service = QAService(settings)
        info = run_experiment(service, settings, questions, args.output,
                              {"corpus_sha256": sources["sha256"], "source_files": sources["files"], "chunks_sha256": chunks_hash},
                              seed=args.seed, methods=tuple(args.methods), dev=args.dev)
        print(f"Saved {info['record_count']} raw records to {args.output}. Human judgments remain blank.")
    except (EvaluationError, FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"Evaluation blocked: {exc}\n")


if __name__ == "__main__":
    main()
