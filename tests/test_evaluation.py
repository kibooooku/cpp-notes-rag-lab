"""Evaluation invariants: no unapproved formal runs, no invented scores/errors."""
import copy
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpp_notes_rag.evaluation import (
    EvaluationError,
    audit_chunk_candidates,
    evidence_diagnostics,
    load_questions,
    question_content_hash,
    read_json,
    render_review_sheet,
    run_experiment,
    source_snapshot,
    summarize_records,
    write_json,
)
from cpp_notes_rag.schemas import Answer, Chunk, Hit, SourceSpan


class FakeSettings:
    config_hash = "frozen-settings"

    def to_dict(self):
        return {"generation_model": "fake", "top_k": 3, "api_key": "never publish"}


class FakeService:
    def __init__(self):
        self.calls = []
        self.warmups = []

    def warmup(self, method):
        self.warmups.append(method)

    def ask(self, question, method):
        self.calls.append((question, method))
        if question == "fail":
            raise RuntimeError("network failed for " + "sk-" + "Abcdefghijklmnopqrstuvwxyz")
        return Answer(question, method, "answered", "真实测试替身", usage={"total_tokens": 0})


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache" / "eval-tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.root = Path(self.temp.name)
        if not self.root.resolve().is_relative_to(cache.resolve()):
            raise RuntimeError("Test cleanup target escaped the workspace cache")
        self.addCleanup(self.temp.cleanup)
        self.questions = load_questions(ROOT / "eval/questions.json")

    def approved(self):
        questions = copy.deepcopy(self.questions)
        for q in questions:
            q.update(reviewed=True, reviewer="test human", source_snapshot="corpus")
            q["reviewed_content_sha256"] = question_content_hash(q)
            q["evidence_audit"] = {"reviewed": True, "reviewer": "test human", "chunks_sha256": "chunks", "single_chunk_insufficient": q["category"] == "multi"}
        path = self.root / "approved.json"
        path.write_text(json.dumps(questions), encoding="utf-8")
        return questions, path

    def test_exact_draft_composition_and_formal_gate(self):
        self.assertEqual(len(self.questions), 20)
        self.assertEqual({c: sum(q["category"] == c for q in self.questions) for c in ("answerable", "unanswerable", "multi")}, {"answerable": 10, "unanswerable": 5, "multi": 5})
        draft = copy.deepcopy(self.questions)
        for q in draft:
            q.update(reviewed=False, reviewer=None)
        path = self.root / "unapproved.json"
        write_json(path, draft)
        with self.assertRaisesRegex(EvaluationError, "await user review"):
            load_questions(path, formal=True, snapshot={"sha256": "corpus"}, chunks_sha256="chunks")
        service = FakeService()
        with self.assertRaises(EvaluationError):
            run_experiment(service, FakeSettings(), draft, self.root / "bad", {"corpus_sha256": "corpus", "chunks_sha256": "chunks"})
        self.assertEqual(service.calls, [])

    def test_snapshots_and_chunk_approval_cannot_be_reused_after_change(self):
        _, path = self.approved()
        self.assertEqual(len(load_questions(path, formal=True, snapshot={"sha256": "corpus"}, chunks_sha256="chunks")), 20)
        with self.assertRaisesRegex(EvaluationError, "Source changed"):
            load_questions(path, formal=True, snapshot={"sha256": "different"}, chunks_sha256="chunks")
        with self.assertRaisesRegex(EvaluationError, "chunk evidence audit"):
            load_questions(path, formal=True, snapshot={"sha256": "corpus"}, chunks_sha256="different")
        source = self.root / "notes"
        source.mkdir()
        (source / "a.md").write_text("original", encoding="utf-8")
        before = source_snapshot(source)
        (source / "a.md").write_text("edited", encoding="utf-8")
        self.assertNotEqual(before["sha256"], source_snapshot(source)["sha256"])

    def test_review_sheet_preserves_scoring_boundaries_and_portable_hash(self):
        questions = copy.deepcopy(self.questions)
        for q in questions:
            q.update(reviewed=False, reviewer=None)
        questions[0]["scoring_notes"] = "允许等价解释；不要求示例中的固定数值。"
        path = self.root / "questions.json"
        sheet = self.root / "review.md"
        write_json(path, questions)
        render_review_sheet(path, sheet)
        raw = path.read_bytes()
        # Match the repository's LF policy so checkout cannot stale the hash.
        self.assertNotIn(b"\r", raw)
        self.assertNotIn(b"\r", sheet.read_bytes())
        content = sheet.read_text(encoding="utf-8")
        self.assertIn(hashlib.sha256(raw).hexdigest(), content)
        self.assertIn(questions[0]["scoring_notes"], content)
        self.assertFalse(any(q["reviewed"] for q in read_json(path)))

    def test_hits_use_real_schema_and_traps_have_no_hit_metric(self):
        q = self.questions[0]
        span = q["required_evidence_groups"][0]["sources"][0]
        hit = Hit(Chunk("c", "d", "evidence", [SourceSpan(span["source_file"], "", span["line_start"], span["line_end"])]), 2.0, 1)
        diagnostic = evidence_diagnostics(q, [hit])
        self.assertTrue(diagnostic["any_source_overlap"])
        self.assertTrue(diagnostic["requires_manual_semantic_review"])
        self.assertFalse(evidence_diagnostics(self.questions[10], [hit])["applicable"])

    def test_multi_audit_detects_single_chunk_and_missing_evidence(self):
        q = self.questions[15]
        both = {"chunk_id": "both", "spans": [g["sources"][0] for g in q["required_evidence_groups"]]}
        row = audit_chunk_candidates([q], [both])[0]
        self.assertTrue(row["single_chunk_source_overlap"])
        self.assertEqual(row["minimum_source_overlap_cover"], 1)
        split = [{"chunk_id": f"c{i}", "spans": [g["sources"][0]]} for i, g in enumerate(q["required_evidence_groups"])]
        self.assertEqual(audit_chunk_candidates([q], split)[0]["minimum_source_overlap_cover"], len(q["required_evidence_groups"]))
        self.assertIsNone(audit_chunk_candidates([q], split[:1])[0]["minimum_source_overlap_cover"])

    def test_randomized_run_preserves_errors_and_never_marks_judgments(self):
        questions = load_questions(ROOT / "eval/dev_questions.json")[:2]
        questions[0]["question"] = "fail"
        service = FakeService()
        info = run_experiment(service, FakeSettings(), questions, self.root / "run", {"corpus_sha256": "corpus", "chunks_sha256": "chunks"}, dev=True, seed=5)
        self.assertEqual(info["record_count"], 4)
        self.assertEqual(len(service.calls), 4)
        self.assertEqual(service.warmups, ["bm25", "vector"])
        all_records = []
        for m in ("bm25", "vector"):
            rows = read_json(self.root / "run" / f"results_{m}.json")
            self.assertEqual(len(rows), 2)
            self.assertTrue(any(r["answer"]["status"] == "error" for r in rows))
            all_records += rows
        raw = (self.root / "run" / "run_log.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk-Abc", raw)
        self.assertIn("[REDACTED]", raw)
        self.assertEqual(sorted(r["sequence"] for r in all_records), [1, 2, 3, 4])
        with (self.root / "run" / "judgments.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertTrue(all(not row["reviewed"] and not row["answer_correct"] for row in csv.DictReader(stream)))
        with self.assertRaisesRegex(EvaluationError, "not empty"):
            run_experiment(FakeService(), FakeSettings(), questions, self.root / "run", {}, dev=True)

    def test_incomplete_judgment_has_no_formal_rate_and_errors_count_separately(self):
        questions, _ = self.approved()
        questions[0]["question"] = "fail"
        questions[0]["reviewed_content_sha256"] = question_content_hash(questions[0])
        run_experiment(FakeService(), FakeSettings(), questions, self.root / "formal", {"corpus_sha256": "corpus", "chunks_sha256": "chunks"}, seed=2)
        records = {m: read_json(self.root / "formal" / f"results_{m}.json") for m in ("bm25", "vector")}
        self.assertIsNone(summarize_records(records, [])[0]["answer_accuracy"]["rate"])
        with (self.root / "formal" / "judgments.csv").open(encoding="utf-8-sig", newline="") as stream:
            judgments = list(csv.DictReader(stream))
        for row in judgments:
            row.update(reviewed="true", reviewer="test human", answer_correct="false", citation_supported="false")
            if row["category"] == "unanswerable":
                row["hallucinated_trap"] = "false"
            else:
                row["top3_any_hit"] = "false"
            if row["category"] == "multi":
                row["all_groups_covered"] = "false"
        summaries = summarize_records(records, judgments)
        self.assertTrue(all(s["formal_metrics_complete"] for s in summaries))
        self.assertEqual(summaries[0]["answer_accuracy"]["rate"], 0)
        self.assertEqual(summaries[0]["api_or_runtime_errors"], 1)
        self.assertEqual(summaries[0]["latency_sample_count"], 19)
        judgments[0]["answer_correct"] = ""
        with self.assertRaisesRegex(EvaluationError, "incomplete scores"):
            summarize_records(records, judgments)

    def test_checkpoint_commit_retries_only_transient_replace_failures(self):
        target = self.root / "checkpoint.json"
        target.write_text('{"old": true}', encoding="utf-8")
        original_replace = Path.replace
        calls = []

        def flaky_replace(temporary, destination):
            calls.append(destination)
            if len(calls) <= 2:
                raise PermissionError("Temporary destination lock")
            return original_replace(temporary, destination)

        with patch.object(Path, "replace", flaky_replace), patch("cpp_notes_rag.evaluation.time.sleep") as sleep:
            write_json(target, {"new": True})
        self.assertEqual(read_json(target), {"new": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])

    def test_permanent_checkpoint_lock_preserves_previous_valid_json(self):
        target = self.root / "checkpoint.json"
        target.write_text('{"old": true}', encoding="utf-8")
        with (
            patch.object(Path, "replace", side_effect=PermissionError("Permanent lock")) as replace,
            patch("cpp_notes_rag.evaluation.time.sleep") as sleep,
            self.assertRaises(PermissionError),
        ):
            write_json(target, {"new": True})
        self.assertEqual(read_json(target), {"old": True})
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
