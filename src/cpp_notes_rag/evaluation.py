"""Reproducible evaluation with explicit human-review gates; no model judging."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import platform
import random
import re
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

METHODS = ("bm25", "vector")
CATEGORIES = {"answerable": 10, "unanswerable": 5, "multi": 5}
JUDGMENT_FIELDS = [
    "run_id", "question_id", "method", "category", "reviewed", "reviewer",
    "answer_correct", "hallucinated_trap", "top3_any_hit", "all_groups_covered",
    "citation_supported", "notes",
]


class EvaluationError(ValueError):
    """A dataset, snapshot or judgment is not ready for a valid experiment."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    # Windows antivirus/indexing can briefly hold the destination. Retry only
    # this filesystem commit; the previous valid JSON remains intact and no
    # generation request is repeated. Permanent failures remain visible.
    delays = (0.05, 0.1, 0.2)
    for attempt in range(len(delays) + 1):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def question_content_hash(question: dict) -> str:
    metadata = {"reviewed", "reviewer", "review_notes", "source_snapshot", "evidence_audit", "reviewed_content_sha256"}
    return stable_hash({k: v for k, v in question.items() if k not in metadata})


def render_review_sheet(questions_path: Path, output_path: Path) -> None:
    questions = read_json(questions_path)
    approved = sum(q.get("reviewed") is True for q in questions)
    lines = [
        "# 20 题金标准审核单", "",
        f"题库为 `eval/questions.json` 的当前版本：10 道有答案、5 道陷阱、5 道跨片段。**用户批准 {approved}/20 题**。本页便于阅读，JSON 是机器执行的题库。", "",
        "请逐题审核问题、标准答案、每个必要要点、原文证据和技术前提。跨片段题还须在最终切片后验证：没有单个片段包含全部答案，且 Top-3 能容纳必要证据。陷阱需通读全文确认关键内容缺失。", "",
        "**统一判分原则**：考查概念、机制和条件，允许语义等价的解释。原示例中的变量名、类名、函数名、字符串、初始值和数组长度不作为得分条件；除题目明确考查语法外，不要求照抄代码。C++ 标准版本、具有语义作用的关键字/运算符及证据完整性仍需正确。", "",
        "流程与判分字段见 [判分说明](../docs/scoring-guide.md)，原文疑点见 [资料审核清单](../docs/material-review.md)。本页没有模拟审核签字；待修内容可反馈给 AI 整理。", "",
        "生成时题库文件 SHA-256：`" + hashlib.sha256(questions_path.read_bytes()).hexdigest() + "`。如题库改动应重新生成本页，不能依据旧页批准新版。", "",
    ]
    labels = {"answerable": "有答案", "unanswerable": "无答案陷阱", "multi": "跨片段"}
    for q in questions:
        lines += [f"## {q['question_id']} · {labels[q['category']]}", "", "**问题**：" + q["question"], "", "**候选标准答案**：" + q["gold_answer"], "", "**完整答对所需要点**：", ""]
        lines += ["- " + point for point in q["required_points"]]
        if q.get("scoring_notes"):
            lines += ["", "**等价答案与评分边界**：" + q["scoring_notes"]]
        lines += ["", "**必要原文证据**：", ""]
        for group in q["required_evidence_groups"]:
            refs = [f"`{s['source_file']}` L{s['line_start']}–{s['line_end']}" for s in group["sources"]]
            lines.append(f"- 组 {group['group_id']}：" + "；可接受替代：".join(refs))
        if not q["required_evidence_groups"]:
            lines.append("- 无支持所问核心结论的证据组。缺口：" + q["missing_information"])
        lines += ["", "**待核提示**：" + q["review_notes"], ""]
        lines += ["- 避免：" + item for item in q["forbidden_unsupported_claims"]]
        if q["category"] == "multi":
            lines += [f"- 必要证据组：{len(q['required_evidence_groups'])} 组；文件不同本身不能代替语义支持审核。"]
        lines += ["", "审核状态：`reviewed=" + str(q.get("reviewed") is True).lower() + "`。", ""]
    output_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def source_snapshot(materials: Path) -> dict:
    """Hash actual input bytes rather than trusting a possibly stale inventory."""
    files = [
        {"source_file": str(p.relative_to(materials)).replace("\\", "/"),
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(materials.rglob("*"))
        if p.is_file() and p.suffix.lower() in {".md", ".txt"}
    ]
    if not files:
        raise EvaluationError(f"No Markdown/TXT sources in {materials}")
    return {"sha256": stable_hash(files), "files": files}


def chunk_snapshot(chunks_path: Path) -> str:
    if not chunks_path.is_file():
        raise EvaluationError(f"Build the index first; missing {chunks_path}")
    return hashlib.sha256(chunks_path.read_bytes()).hexdigest()


def load_chunks(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]


def load_questions(path: Path, *, formal: bool = False, snapshot: dict | None = None,
                   chunks_sha256: str | None = None) -> list[dict]:
    questions = read_json(path)
    if not isinstance(questions, list) or not questions:
        raise EvaluationError("Question file must be a non-empty JSON array")
    ids = [q.get("question_id") for q in questions]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise EvaluationError("Question IDs must be non-empty and unique")
    for q in questions:
        if q.get("category") not in CATEGORIES or not q.get("question", "").strip():
            raise EvaluationError(f"Invalid category/question: {q.get('question_id')}")
        groups = q.get("required_evidence_groups", [])
        if q["category"] != "unanswerable" and not groups:
            raise EvaluationError(f"Missing necessary evidence: {q['question_id']}")
        if q["category"] == "multi" and not 2 <= len(groups) <= 3:
            raise EvaluationError(f"Multi question needs 2–3 necessary groups: {q['question_id']}")
        if q["category"] == "unanswerable" and groups:
            raise EvaluationError(f"Trap must not declare answer evidence: {q['question_id']}")
        for group in groups:
            if not group.get("sources"):
                raise EvaluationError(f"Empty evidence group: {q['question_id']}")
            for span in group["sources"]:
                if not span.get("source_file") or not 1 <= span.get("line_start", 0) <= span.get("line_end", 0):
                    raise EvaluationError(f"Invalid evidence line range: {q['question_id']}")
    if not formal:
        return questions
    validate_formal_gate(questions, snapshot, chunks_sha256)
    return questions


def validate_formal_gate(questions: list[dict], snapshot: dict | None, chunks_sha256: str | None) -> None:
    actual = {c: sum(q["category"] == c for q in questions) for c in CATEGORIES}
    if len(questions) != 20 or actual != CATEGORIES:
        raise EvaluationError("Formal evaluation requires exactly 20 questions: 10 answerable, 5 traps, 5 multi")
    pending = [q["question_id"] for q in questions if q.get("reviewed") is not True or not q.get("reviewer")]
    if pending:
        raise EvaluationError("Gold answers await user review: " + ", ".join(pending))
    if snapshot is None or chunks_sha256 is None:
        raise EvaluationError("Formal evaluation requires actual source and chunk snapshots")
    for q in questions:
        if q.get("reviewed_content_sha256") != question_content_hash(q):
            raise EvaluationError(f"Question/gold edited after review or review not frozen: {q['question_id']}")
        if q.get("source_snapshot") != snapshot["sha256"]:
            raise EvaluationError(f"Source changed/unfrozen: {q['question_id']}; review gold again")
        audit = q.get("evidence_audit", {})
        if audit.get("reviewed") is not True or not audit.get("reviewer") or audit.get("chunks_sha256") != chunks_sha256:
            raise EvaluationError(f"Final chunk evidence audit awaits user review: {q['question_id']}")
        if q["category"] == "multi" and audit.get("single_chunk_insufficient") is not True:
            raise EvaluationError(f"Multi question can be solved by one chunk or has no audit: {q['question_id']}")


def plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def redact(value: Any) -> Any:
    """Avoid credential-bearing settings or exception strings in public run files."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in k.lower() for s in ("api_key", "authorization", "password", "secret")) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
    return value


def hit_spans(hit: dict) -> list[dict]:
    hit = plain(hit)
    chunk = hit.get("chunk", hit)
    spans = chunk.get("spans", chunk.get("source_spans", []))
    if spans:
        return spans
    if chunk.get("source_file") and chunk.get("line_start") is not None:
        return [{"source_file": chunk["source_file"], "line_start": chunk["line_start"], "line_end": chunk["line_end"]}]
    return []


def normalize_source(source: str) -> str:
    return source.replace("\\", "/").removeprefix("问答素材/")


def group_overlaps_hit(group: dict, hit: dict) -> bool:
    return any(
        normalize_source(span["source_file"]) == normalize_source(ref["source_file"])
        and max(int(span["line_start"]), int(ref["line_start"])) <= min(int(span["line_end"]), int(ref["line_end"]))
        for span in hit_spans(hit) for ref in group["sources"]
    )


def evidence_diagnostics(question: dict, hits: list[dict]) -> dict:
    """Source-range overlap is a candidate hit, NOT a semantic correctness score."""
    if question["category"] == "unanswerable":
        return {"applicable": False, "reason": "Corpus-level trap; related background is not answer evidence"}
    groups = question["required_evidence_groups"]
    hits = [plain(h) for h in hits[:3]]
    covered = [g["group_id"] for g in groups if any(group_overlaps_hit(g, h) for h in hits)]
    return {
        "applicable": True, "source_overlap_only": True,
        "covered_group_ids": covered,
        "any_source_overlap": bool(covered),
        "all_groups_source_overlap": len(covered) == len(groups),
        "requires_manual_semantic_review": True,
    }


def audit_chunk_candidates(questions: list[dict], chunks: list[dict]) -> list[dict]:
    """Find possible mappings/minimum cover; human must inspect the actual contents."""
    report = []
    for q in questions:
        groups = q["required_evidence_groups"]
        if not groups:
            report.append({"question_id": q["question_id"], "category": q["category"], "action": "Re-read full corpus to confirm missing details"})
            continue
        masks = []
        group_chunks = {}
        for index, g in enumerate(groups):
            group_chunks[g["group_id"]] = [c.get("chunk_id", c.get("id")) for c in chunks if group_overlaps_hit(g, c)]
        for c in chunks:
            mask = sum(1 << i for i, g in enumerate(groups) if group_overlaps_hit(g, c))
            if mask:
                masks.append((c.get("chunk_id", c.get("id")), mask))
        target = (1 << len(groups)) - 1
        minimum = None
        examples = []
        for size in range(1, 4):
            for combo in itertools.combinations(masks, size):
                union = 0
                for _, mask in combo:
                    union |= mask
                if union == target:
                    minimum = size
                    examples = [item[0] for item in combo]
                    break
            if minimum is not None:
                break
        report.append({
            "question_id": q["question_id"], "category": q["category"],
            "group_chunk_candidates": group_chunks, "minimum_source_overlap_cover": minimum,
            "example_cover": examples, "single_chunk_source_overlap": minimum == 1,
            "requires_manual_semantic_review": True,
        })
    return report


def safe_config(settings: Any) -> dict:
    allowed = ("model_name", "embedding_revision", "top_k", "chunk_max_tokens", "generation_model", "api_base_url", "api_timeout_seconds", "max_output_tokens", "temperature", "bm25_min_score", "vector_min_score", "budget_cny", "input_cny_per_million", "cached_input_cny_per_million", "output_cny_per_million", "historical_allowance_cny")
    source = plain(settings.to_dict()) if hasattr(settings, "to_dict") else plain(settings)
    if not isinstance(source, dict):
        source = {key: plain(getattr(settings, key)) for key in allowed if hasattr(settings, key)}
    return redact({key: source[key] for key in allowed if key in source})


def run_experiment(service: Any, settings: Any, questions: list[dict], output_dir: Path,
                   metadata: dict, *, seed: int = 20261019, methods: tuple = METHODS,
                   dev: bool = False) -> dict:
    if not methods or any(m not in METHODS for m in methods) or len(set(methods)) != len(methods):
        raise EvaluationError("Methods must be unique bm25/vector values")
    if not dev and tuple(sorted(methods)) != tuple(sorted(METHODS)):
        raise EvaluationError("Formal run must compare both methods")
    if not dev:
        validate_formal_gate(questions, {"sha256": metadata.get("corpus_sha256")}, metadata.get("chunks_sha256"))
    if output_dir.exists() and any(output_dir.iterdir()):
        raise EvaluationError(f"Output directory is not empty: {output_dir}; use a new run directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%S.%fZ")
    config = safe_config(settings)
    info = {
        "run_id": run_id, "kind": "development" if dev else "formal",
        "started_at_utc": started.isoformat(), "seed": seed,
        "config": config, "config_sha256": str(getattr(service, "config_hash", getattr(settings, "config_hash", stable_hash(config)))),
        "settings_sha256": str(getattr(settings, "config_hash", stable_hash(config))),
        "question_sha256": stable_hash(questions), **redact(metadata),
        "cache": "disabled: each question calls service.ask with no history",
        "automatic_retries": 0,
        "python_version": platform.python_version(),
    }
    write_json(output_dir / "run_metadata.json", info)
    cold = {}
    for method in methods:
        before = time.perf_counter()
        try:
            service.warmup(method)
            cold[method] = {"seconds": time.perf_counter() - before, "error": None}
        except Exception as exc:  # noqa: BLE001 -- Preserve unexpected warmup failures as raw experiment records.
            cold[method] = {"seconds": time.perf_counter() - before, "error": redact(f"{type(exc).__name__}: {exc}")}
    write_json(output_dir / "warmup.json", cold)
    info["index_corpus_hash"] = getattr(service, "corpus_hash", None)
    manifest = plain(getattr(getattr(service, "index", None), "manifest", {}))
    if isinstance(manifest, dict):
        info["index_manifest"] = redact({k: v for k, v in manifest.items() if k != "source_dir"})
    write_json(output_dir / "run_metadata.json", info)
    pairs = [(q, m) for q in questions for m in methods]
    random.Random(seed).shuffle(pairs)
    records = {method: [] for method in methods}
    for sequence, (q, method) in enumerate(pairs, start=1):
        before = time.perf_counter()
        try:
            if cold[method]["error"]:
                raise RuntimeError("Warmup failed: " + cold[method]["error"])
            answer = plain(service.ask(q["question"], method))
            if not isinstance(answer, dict):
                raise TypeError("service.ask must return a dataclass or dict")
        except Exception as exc:  # noqa: BLE001 -- Preserve every failed attempt instead of silently losing questions.
            answer = {"question": q["question"], "method": method, "status": "error", "answer": "", "citations": [], "hits": [], "usage": {}, "error": f"{type(exc).__name__}: {exc}"}
        elapsed = time.perf_counter() - before
        answer = redact(answer)
        record = {
            "run_id": run_id, "sequence": sequence, "question_id": q["question_id"],
            "category": q["category"], "question": q["question"], "method": method,
            "kind": info["kind"], "corpus_sha256": metadata.get("corpus_sha256"),
            "index_corpus_hash": info.get("index_corpus_hash"),
            "chunks_sha256": metadata.get("chunks_sha256"), "config_sha256": info["config_sha256"],
            "question_sha256": info["question_sha256"], "answer": answer,
            "elapsed_seconds": elapsed, "attempt": 1,
            "automatic_source_overlap": evidence_diagnostics(q, answer.get("hits", [])),
        }
        records[method].append(record)
        with (output_dir / "run_log.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Checkpoint complete valid JSON on every response, including failures.
        for m in methods:
            write_json(output_dir / f"results_{m}.json", records[m])
    info["finished_at_utc"] = datetime.now(UTC).isoformat()
    info["record_count"] = sum(map(len, records.values()))
    info["complete"] = all(len(records[m]) == len(questions) for m in methods)
    write_json(output_dir / "run_metadata.json", info)
    write_judgment_template(output_dir / "judgments.csv", records)
    return info


def write_judgment_template(path: Path, records: dict[str, list[dict]]) -> None:
    if path.exists():
        raise EvaluationError(f"Refusing to overwrite judgments: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=JUDGMENT_FIELDS)
        writer.writeheader()
        for method, rows in records.items():
            for r in sorted(rows, key=lambda item: item["question_id"]):
                writer.writerow({"run_id": r["run_id"], "question_id": r["question_id"], "method": method, "category": r["category"]})


def bool_cell(value: str) -> bool | None:
    if value.strip().lower() in {"true", "1", "yes"}:
        return True
    if value.strip().lower() in {"false", "0", "no"}:
        return False
    if not value.strip():
        return None
    raise EvaluationError(f"Expected blank/true/false judgment, got {value!r}")


def summarize_records(records: dict[str, list[dict]], judgments: list[dict]) -> list[dict]:
    indexed = {}
    seen = set()
    raw_by_key = {(r["run_id"], r["question_id"], m): r for m, rows in records.items() for r in rows}
    expected_keys = set(raw_by_key)
    all_run_ids = {key[0] for key in expected_keys}
    if len(all_run_ids) > 1:
        raise EvaluationError("Do not mix records from different runs")
    for row in judgments:
        key = (row["run_id"], row["question_id"], row["method"])
        if key not in expected_keys or key in seen:
            raise EvaluationError(f"Unknown or duplicate judgment: {key}")
        seen.add(key)
        if row["category"] != raw_by_key[key]["category"]:
            raise EvaluationError(f"Judgment category differs from raw result: {key}")
        if bool_cell(row.get("reviewed", "")) is True:
            if not row.get("reviewer", "").strip():
                raise EvaluationError(f"Reviewed row needs reviewer: {key}")
            fields = ["answer_correct", "citation_supported"]
            if row["category"] == "unanswerable":
                fields += ["hallucinated_trap"]
            else:
                fields += ["top3_any_hit"]
            if row["category"] == "multi":
                fields += ["all_groups_covered"]
            if any(bool_cell(row.get(field, "")) is None for field in fields):
                raise EvaluationError(f"Reviewed row has incomplete scores: {key}")
            indexed[key] = row
    summaries = []
    for method, rows in records.items():
        if len({(r["run_id"], r["question_id"]) for r in rows}) != len(rows):
            raise EvaluationError(f"Duplicate raw result for {method}")
        judged = [indexed[(r["run_id"], r["question_id"], method)] for r in rows if (r["run_id"], r["question_id"], method) in indexed]
        # Partial progress gives counts; an incomplete denominator never becomes a formal percentage.
        def metric(field, category_set, expected, judged_rows=judged):
            eligible = [j for j in judged_rows if j["category"] in category_set]
            count = sum(bool_cell(j[field]) is True for j in eligible)
            return {"numerator_reviewed": count, "reviewed_denominator": len(eligible), "required_denominator": expected, "rate": count / expected if len(eligible) == expected else None}
        completed = [r for r in rows if not r["answer"].get("error") and r["answer"].get("status") != "error"]
        summaries.append({
            "method": method, "raw_records": len(rows), "reviewed_records": len(judged),
            "answer_accuracy": metric("answer_correct", set(CATEGORIES), 20),
            "trap_hallucination_rate": metric("hallucinated_trap", {"unanswerable"}, 5),
            "top3_any_hit": metric("top3_any_hit", {"answerable", "multi"}, 15),
            "multi_complete_coverage": metric("all_groups_covered", {"multi"}, 5),
            "first_complete_answer_latency_seconds": sum(r["elapsed_seconds"] for r in completed) / len(completed) if completed else None,
            "latency_sample_count": len(completed), "api_or_runtime_errors": len(rows) - len(completed),
            "latency_definition": "Prewarmed question submission through full answer and citations; failures excluded and separately counted",
            "formal_metrics_complete": len(judged) == 20 and len(rows) == 20,
        })
    return summaries
