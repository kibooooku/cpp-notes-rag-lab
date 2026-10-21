"""Import explicitly entered human scores from the review workbook into a new CSV.

This reads XLSX ZIP/XML with the standard library. It never evaluates formulas,
infers scores, approves rows, or replaces an existing judgment file.
"""
from __future__ import annotations

import argparse
import csv
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cpp_notes_rag.evaluation import (
    CATEGORIES,
    JUDGMENT_FIELDS,
    METHODS,
    EvaluationError,
    bool_cell,
    read_json,
    summarize_records,
)

SHEET_NAME = "人工评分"
HEADER_ROW = 6
FIRST_DATA_ROW = 7
LAST_DATA_ROW = 46
WORKBOOK_FIELDS = (
    "question_id", "method", "category", "model_status", "question",
    "required_points", "answer", "answer_correct", "hallucinated_trap",
    "top3_any_hit", "all_groups_covered", "citation_supported", "reviewer",
    "reviewed", "notes", "elapsed_seconds", "applicable_fields",
    "expected_sources", "evidence_rows", "run_id", "result_source",
)
SCORE_FIELDS = (
    "answer_correct", "hallucinated_trap", "top3_any_hit",
    "all_groups_covered", "citation_supported",
)
HUMAN_FIELDS = {*SCORE_FIELDS, "reviewer", "reviewed", "notes"}
KEY_FIELDS = {"run_id", "question_id", "method", "category"}
REVIEWER_PLACEHOLDERS = {
    "-", "n/a", "na", "n.a.", "none", "null", "unknown", "pending", "todo", "tbd",
    "待填写", "待审核", "待确认", "未填写", "未审核", "ai", "chatgpt", "codex",
    "assistant", "model", "auto", "automatic", "automated",
}


@dataclass(frozen=True)
class Cell:
    value: str = ""
    formula: bool = False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    if element is None or _local(element.tag) == "rPh":
        return ""
    if _local(element.tag) == "t":
        return element.text or ""
    return "".join(_text(child) for child in element)


def _xml(archive: zipfile.ZipFile, path: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(path))
    except (KeyError, ET.ParseError) as exc:
        raise EvaluationError(f"Missing or invalid XLSX XML: {path}") from exc


def _target(target: str) -> str:
    target = target.replace("\\", "/")
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
    if path.startswith("../") or path == "..":
        raise EvaluationError("Invalid workbook relationship target")
    return path


def _column(reference: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", reference.upper())
    if not match:
        raise EvaluationError(f"Invalid XLSX cell reference: {reference!r}")
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - ord("A") + 1
    if column > 16384:
        raise EvaluationError(f"Invalid XLSX column: {reference!r}")
    return column, int(match[2])


def _cell(element: ET.Element, shared: list[str]) -> Cell:
    children = {_local(child.tag): child for child in element}
    value_element = children.get("v")
    value = value_element.text or "" if value_element is not None else ""
    kind = element.get("t", "n")
    if kind == "s":
        try:
            index = int(value)
            if index < 0:
                raise ValueError
            value = shared[index]
        except (ValueError, IndexError) as exc:
            raise EvaluationError("Invalid shared-string cell") from exc
    elif kind == "inlineStr":
        value = _text(children.get("is"))
    elif kind == "b":
        if value not in {"0", "1"}:
            raise EvaluationError(f"Invalid XLSX boolean: {value!r}")
        value = "true" if value == "1" else "false"
    return Cell(value, "f" in children)


def read_score_sheet(workbook: Path) -> dict[int, dict[int, Cell]]:
    """Resolve the named sheet through relationships, including rich shared text."""
    try:
        with zipfile.ZipFile(workbook) as archive:
            book = _xml(archive, "xl/workbook.xml")
            relationships = {
                child.get("Id"): child
                for child in _xml(archive, "xl/_rels/workbook.xml.rels")
                if _local(child.tag) == "Relationship"
            }
            sheets = [node for node in book.iter() if _local(node.tag) == "sheet" and node.get("name") == SHEET_NAME]
            if len(sheets) != 1:
                raise EvaluationError(f"Workbook must contain exactly one {SHEET_NAME!r} sheet")
            relation_id = next((v for k, v in sheets[0].attrib.items() if _local(k) == "id"), None)
            relation = relationships.get(relation_id)
            if relation is None or relation.get("TargetMode") == "External" or not relation.get("Target"):
                raise EvaluationError("Review sheet has no valid local worksheet relationship")
            shared_path = "xl/sharedStrings.xml"
            for rel in relationships.values():
                if (rel.get("Type") or "").endswith("/sharedStrings"):
                    if rel.get("TargetMode") == "External":
                        raise EvaluationError("External shared strings are not supported")
                    shared_path = _target(rel.get("Target", ""))
            shared = []
            if shared_path in archive.namelist():
                shared = [_text(node) for node in _xml(archive, shared_path) if _local(node.tag) == "si"]
            sheet = _xml(archive, _target(relation.get("Target", "")))
            rows: dict[int, dict[int, Cell]] = {}
            last_row = 0
            for row in (node for node in sheet.iter() if _local(node.tag) == "row"):
                number = int(row.get("r", str(last_row + 1)))
                if number < 1 or number in rows:
                    raise EvaluationError(f"Invalid or duplicate worksheet row: {number}")
                values: dict[int, Cell] = {}
                previous_column = 0
                for cell in row:
                    if _local(cell.tag) != "c":
                        continue
                    if cell.get("r"):
                        column, cell_row = _column(cell.get("r", ""))
                        if cell_row != number:
                            raise EvaluationError("Cell reference differs from its worksheet row")
                    else:
                        column = previous_column + 1
                    if column in values:
                        raise EvaluationError(f"Duplicate worksheet cell in row {number}")
                    values[column] = _cell(cell, shared)
                    previous_column = column
                rows[number] = values
                last_row = number
            return rows
    except zipfile.BadZipFile as exc:
        raise EvaluationError("Workbook is not a readable XLSX ZIP archive") from exc


def load_run_records(run_dir: Path) -> dict[str, list[dict]]:
    records = {method: read_json(run_dir / f"results_{method}.json") for method in METHODS}
    keys = set()
    run_ids = set()
    per_question = {}
    method_ids = []
    for method, entries in records.items():
        if not isinstance(entries, list) or len(entries) != 20:
            raise EvaluationError(f"Expected exactly 20 raw result records for {method}")
        counts = dict.fromkeys(CATEGORIES, 0)
        ids = set()
        for record in entries:
            if not isinstance(record, dict) or record.get("method") != method:
                raise EvaluationError(f"Raw result method does not match {method}")
            run_id, question_id, category = (record.get(field) for field in ("run_id", "question_id", "category"))
            if not isinstance(run_id, str) or not run_id.strip() or not isinstance(question_id, str) or not question_id.strip():
                raise EvaluationError("Raw results need non-empty run_id and question_id")
            if category not in CATEGORIES:
                raise EvaluationError(f"Unknown raw result category: {category!r}")
            key = (run_id, question_id, method)
            if key in keys:
                raise EvaluationError(f"Duplicate raw result: {key}")
            if question_id in per_question and per_question[question_id] != category:
                raise EvaluationError(f"Raw categories disagree between methods: {question_id}")
            keys.add(key)
            ids.add(question_id)
            run_ids.add(run_id)
            per_question[question_id] = category
            counts[category] += 1
        if counts != CATEGORIES:
            raise EvaluationError(f"Raw results must contain 10 answerable, 5 traps and 5 multi for {method}")
        method_ids.append(ids)
    if len(run_ids) != 1 or method_ids[0] != method_ids[1]:
        raise EvaluationError("Raw files must use the same run and the same 20 questions")
    return records


def read_judgments(workbook: Path, records: dict[str, list[dict]]) -> list[dict]:
    rows = read_score_sheet(workbook)
    headers = {}
    for column, cell in rows.get(HEADER_ROW, {}).items():
        field = cell.value.strip()
        if not field:
            continue
        if cell.formula or field in headers:
            raise EvaluationError(f"Formula or duplicate machine field in header row: {field!r}")
        headers[field] = column
    missing_fields = set(WORKBOOK_FIELDS) - headers.keys()
    if missing_fields:
        raise EvaluationError("Missing machine field headers: " + ", ".join(sorted(missing_fields)))
    expected = {(record["run_id"], record["question_id"], method): record
                for method, entries in records.items() for record in entries}
    if len(expected) != 40:
        raise EvaluationError("Expected 40 unique raw result keys")
    judgments = []
    seen = set()
    for number in range(FIRST_DATA_ROW, LAST_DATA_ROW + 1):
        cells = {field: rows.get(number, {}).get(column, Cell()) for field, column in headers.items()}
        for field in HUMAN_FIELDS | KEY_FIELDS:
            if cells[field].formula:
                raise EvaluationError(f"Formula not allowed in {field}, worksheet row {number}")
        judgment = {field: cells[field].value for field in JUDGMENT_FIELDS}
        for field in KEY_FIELDS | {"reviewer"}:
            judgment[field] = judgment[field].strip()
        key = tuple(judgment[field] for field in ("run_id", "question_id", "method"))
        if key not in expected or key in seen:
            raise EvaluationError(f"Unknown or duplicate workbook result key in row {number}: {key}")
        seen.add(key)
        if judgment["category"] != expected[key]["category"]:
            raise EvaluationError(f"Workbook category differs from raw result in row {number}")
        category = judgment["category"]
        applicable = {"answer_correct", "citation_supported"}
        applicable.add("hallucinated_trap" if category == "unanswerable" else "top3_any_hit")
        if category == "multi":
            applicable.add("all_groups_covered")
        for field in (*SCORE_FIELDS, "reviewed"):
            value = judgment[field].strip()
            if field in SCORE_FIELDS and field not in applicable and value:
                raise EvaluationError(f"Inapplicable {field} must be blank in row {number}; do not enter N/A or false")
            parsed = bool_cell(value)
            judgment[field] = "" if parsed is None else str(parsed).lower()
        if judgment["reviewed"] == "true":
            reviewer = judgment["reviewer"]
            if not reviewer or reviewer.casefold() in REVIEWER_PLACEHOLDERS:
                raise EvaluationError(f"Reviewed row needs a real human reviewer identifier, row {number}")
        judgments.append(judgment)
    for number, values in rows.items():
        if number > LAST_DATA_ROW and any(values.get(column, Cell()).value.strip() or values.get(column, Cell()).formula for column in headers.values()):
            raise EvaluationError(f"Unexpected data after row {LAST_DATA_ROW}: worksheet row {number}")
    if seen != set(expected):
        raise EvaluationError("Workbook is missing raw result keys")
    summarize_records(records, judgments)  # Also require complete applicable scores on reviewed rows.
    return judgments


def import_review_scores(workbook: Path, run_dir: Path, output: Path | None = None) -> dict:
    output = output if output is not None else run_dir / "judgments_from_workbook.csv"
    if output.suffix.lower() != ".csv":
        raise EvaluationError("Output must be a new .csv file")
    if output.exists():
        raise EvaluationError(f"Refusing to overwrite an existing file: {output}")
    records = load_run_records(run_dir)
    judgments = read_judgments(workbook, records)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=JUDGMENT_FIELDS)
            writer.writeheader()
            writer.writerows(judgments)
    except FileExistsError as exc:
        raise EvaluationError(f"Refusing to overwrite an existing file: {output}") from exc
    return {"output": str(output), "records": len(judgments),
            "reviewed_records": sum(row["reviewed"] == "true" for row in judgments)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path, help="The manually scored .xlsx workbook")
    parser.add_argument("--run-dir", type=Path, default=ROOT / "eval/runs/formal-01")
    parser.add_argument("--output", type=Path, help="New CSV path; default: RUN_DIR/judgments_from_workbook.csv")
    args = parser.parse_args()
    try:
        result = import_review_scores(args.workbook, args.run_dir, args.output)
    except (EvaluationError, OSError, ValueError) as exc:
        parser.exit(2, f"Import blocked: {exc}\n")
    print(f"Wrote {result['records']} rows to {result['output']}; explicitly human-reviewed: {result['reviewed_records']}/40.")
    print("Existing judgment files were not changed. Blank or false reviewed cells remain unreviewed.")


if __name__ == "__main__":
    main()
