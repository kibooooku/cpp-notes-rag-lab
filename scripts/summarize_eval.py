"""Aggregate user-reviewed judgments; incomplete review has no formal percentage."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpp_notes_rag.evaluation import EvaluationError, read_json, summarize_records, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        meta = read_json(args.run_dir / "run_metadata.json")
        if meta.get("kind") != "formal":
            raise EvaluationError("Development runs are diagnostic only; they cannot generate formal metrics")
        records = {m: read_json(args.run_dir / f"results_{m}.json") for m in ("bm25", "vector")}
        with (args.run_dir / "judgments.csv").open(encoding="utf-8-sig", newline="") as stream:
            judgments = list(csv.DictReader(stream))
        summary = summarize_records(records, judgments)
        write_json(args.run_dir / "summary.json", summary)
        with (args.run_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            fields = ["method", "raw_records", "reviewed_records", "accuracy", "trap_hallucination", "top3_any_hit", "multi_complete_coverage", "mean_complete_latency_seconds", "latency_sample_count", "errors"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for s in summary:
                writer.writerow({"method": s["method"], "raw_records": s["raw_records"], "reviewed_records": s["reviewed_records"], "accuracy": s["answer_accuracy"]["rate"], "trap_hallucination": s["trap_hallucination_rate"]["rate"], "top3_any_hit": s["top3_any_hit"]["rate"], "multi_complete_coverage": s["multi_complete_coverage"]["rate"], "mean_complete_latency_seconds": s["first_complete_answer_latency_seconds"], "latency_sample_count": s["latency_sample_count"], "errors": s["api_or_runtime_errors"]})
        print("Wrote summary.json and summary.csv. Blank rates mean the required human review is incomplete.")
    except (EvaluationError, OSError, KeyError, ValueError) as exc:
        parser.exit(2, f"Summary blocked: {exc}\n")


if __name__ == "__main__":
    main()
