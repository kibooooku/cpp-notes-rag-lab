"""Inspect evidence candidates and record an explicitly supplied human review."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpp_notes_rag.evaluation import (
    EvaluationError,
    audit_chunk_candidates,
    chunk_snapshot,
    load_chunks,
    load_questions,
    question_content_hash,
    render_review_sheet,
    source_snapshot,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=ROOT / "eval/questions.json")
    parser.add_argument("--chunks", type=Path, default=ROOT / "storage/chunks.json")
    parser.add_argument("--materials", type=Path, default=ROOT / "问答素材")
    parser.add_argument("--audit-output", type=Path, default=ROOT / "eval/evidence_candidates.json")
    parser.add_argument("--review-sheet", type=Path, default=ROOT / "eval/review-sheet.md")
    parser.add_argument("--approve-ids", nargs="*", help="IDs whose gold, traps and actual evidence were personally checked")
    parser.add_argument("--reviewer", help="Actual human reviewer identity")
    parser.add_argument("--notes", default="")
    parser.add_argument("--confirm-user-review", action="store_true", help="Only supply after the user actually performed this review")
    args = parser.parse_args()
    try:
        questions = load_questions(args.questions)
        sources = source_snapshot(args.materials)
        chunks_hash = chunk_snapshot(args.chunks)
        chunks = load_chunks(args.chunks)
        candidates = audit_chunk_candidates(questions, chunks)
        write_json(args.audit_output, {"corpus_sha256": sources["sha256"], "chunks_sha256": chunks_hash, "automatic_source_overlap_only": True, "questions": candidates})
        if args.approve_ids:
            if not args.confirm_user_review or not args.reviewer or not args.notes.strip():
                raise EvaluationError("Approving requires --reviewer, --notes and --confirm-user-review; AI must not impersonate user review")
            unknown = set(args.approve_ids) - {q["question_id"] for q in questions}
            if unknown:
                raise EvaluationError("Unknown IDs: " + ", ".join(sorted(unknown)))
            by_id = {r["question_id"]: r for r in candidates}
            for q in questions:
                if q["question_id"] not in args.approve_ids:
                    continue
                candidate = by_id[q["question_id"]]
                if q["category"] != "unanswerable" and candidate["minimum_source_overlap_cover"] is None:
                    raise EvaluationError(f"{q['question_id']} has no evidence cover within Top-3; adjust chunks/question before approving")
                if q["category"] == "multi" and candidate["single_chunk_source_overlap"]:
                    raise EvaluationError(f"{q['question_id']} source overlap indicates a single-chunk solution; inspect/redesign before approving")
                q.update(reviewed=True, reviewer=args.reviewer, review_notes=args.notes, source_snapshot=sources["sha256"])
                q["reviewed_content_sha256"] = question_content_hash(q)
                q["evidence_audit"] = {"reviewed": True, "reviewer": args.reviewer, "chunks_sha256": chunks_hash, "single_chunk_insufficient": q["category"] == "multi", "notes": args.notes}
            write_json(args.questions, questions)
        render_review_sheet(args.questions, args.review_sheet)
        pending = [
            q["question_id"] for q in questions
            if q.get("reviewed") is not True
            or not q.get("reviewer")
            or q.get("reviewed_content_sha256") != question_content_hash(q)
            or q.get("source_snapshot") != sources["sha256"]
            or q.get("evidence_audit", {}).get("reviewed") is not True
            or q.get("evidence_audit", {}).get("chunks_sha256") != chunks_hash
        ]
        print(f"Evidence candidates: {args.audit_output}. Still pending: {', '.join(pending) or 'none'}")
    except (EvaluationError, OSError, ValueError) as exc:
        parser.exit(2, f"Review blocked: {exc}\n")


if __name__ == "__main__":
    main()
