"""Workbook imports preserve human decisions and cannot overwrite raw judgments."""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("review_score_import", ROOT / "scripts/import_review_scores.py")
IMPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IMPORTER
SPEC.loader.exec_module(IMPORTER)
from cpp_notes_rag.evaluation import EvaluationError, summarize_records

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"


def column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


class ReviewImportTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache" / "review-import-tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.root = Path(self.temp.name)
        if not self.root.resolve().is_relative_to(cache.resolve()):
            raise RuntimeError("Test cleanup target escaped the workspace cache")
        self.addCleanup(self.temp.cleanup)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.records = {}
        self.rows = []
        for method in ("bm25", "vector"):
            entries = []
            for number in range(1, 21):
                category = "answerable" if number <= 10 else "unanswerable" if number <= 15 else "multi"
                record = {"run_id": "frozen-run", "question_id": f"Q{number:02}", "method": method,
                          "category": category, "question": f"概念问题 {number}",
                          "answer": {"status": "answered", "answer": "原始回答", "error": None},
                          "elapsed_seconds": 1.25}
                entries.append(record)
                row = dict.fromkeys(IMPORTER.WORKBOOK_FIELDS, "")
                row.update(run_id=record["run_id"], question_id=record["question_id"], method=method,
                           category=category, model_status="answered", question=record["question"],
                           answer="原始回答", elapsed_seconds="1.25", result_source=f"results_{method}.json")
                self.rows.append(row)
            self.records[method] = entries
        self.write_records()
        self.original = self.run_dir / "judgments.csv"
        self.original.write_bytes(b"existing human judgments must remain untouched\n")
        self.workbook = self.root / "review.xlsx"
        self.output = self.run_dir / "judgments_from_workbook.csv"

    def write_records(self):
        for method, entries in self.records.items():
            (self.run_dir / f"results_{method}.json").write_text(json.dumps(entries), encoding="utf-8")

    def write_workbook(self, rows=None, headers=None, formulas=None, typed=None, missing_rows=(), extra_rows=None):
        """Build a tiny test ZIP fixture only; no production workbook authoring."""
        rows = self.rows if rows is None else rows
        headers = list(IMPORTER.WORKBOOK_FIELDS) if headers is None else headers
        formulas = formulas or {}
        typed = typed or {}
        shared = ET.Element(f"{{{NS}}}sst")
        sheet = ET.Element(f"{{{NS}}}worksheet")
        data = ET.SubElement(sheet, f"{{{NS}}}sheetData")

        def put_row(number, fields):
            row = ET.SubElement(data, f"{{{NS}}}row", r=str(number))
            for index, field in enumerate(headers, start=1):
                value = fields.get(field, "")
                kind = typed.get((number, field), "inlineStr")
                cell = ET.SubElement(row, f"{{{NS}}}c", r=f"{column_name(index)}{number}", t=kind)
                if (number, field) in formulas:
                    ET.SubElement(cell, f"{{{NS}}}f").text = formulas[number, field]
                    ET.SubElement(cell, f"{{{NS}}}v").text = str(value)
                elif kind == "s":
                    string_index = len(shared)
                    item = ET.SubElement(shared, f"{{{NS}}}si")
                    # Real rich shared strings can contain several text runs.
                    for piece in (str(value)[:2], str(value)[2:]):
                        run = ET.SubElement(item, f"{{{NS}}}r")
                        ET.SubElement(run, f"{{{NS}}}t").text = piece
                    ET.SubElement(cell, f"{{{NS}}}v").text = str(string_index)
                elif kind == "inlineStr":
                    inline = ET.SubElement(cell, f"{{{NS}}}is")
                    ET.SubElement(inline, f"{{{NS}}}t").text = str(value)
                else:
                    ET.SubElement(cell, f"{{{NS}}}v").text = str(value)

        put_row(6, dict(zip(headers, headers)))
        for number, fields in enumerate(rows, start=7):
            if number not in missing_rows:
                put_row(number, fields)
        for number, fields in (extra_rows or {}).items():
            put_row(number, fields)
        book = ET.Element(f"{{{NS}}}workbook")
        sheets = ET.SubElement(book, f"{{{NS}}}sheets")
        ET.SubElement(sheets, f"{{{NS}}}sheet", name="说明", sheetId="1", attrib={f"{{{REL}}}id": "intro"})
        ET.SubElement(sheets, f"{{{NS}}}sheet", name="人工评分", sheetId="2", attrib={f"{{{REL}}}id": "scores"})
        relationships = ET.Element(f"{{{PACKAGE}}}Relationships")
        ET.SubElement(relationships, f"{{{PACKAGE}}}Relationship", Id="intro", Type=REL + "/worksheet", Target="worksheets/intro.xml")
        ET.SubElement(relationships, f"{{{PACKAGE}}}Relationship", Id="scores", Type=REL + "/worksheet", Target="/xl/worksheets/custom_scores.xml")
        ET.SubElement(relationships, f"{{{PACKAGE}}}Relationship", Id="strings", Type=REL + "/sharedStrings", Target="sharedStrings.xml")
        with zipfile.ZipFile(self.workbook, "w") as archive:
            archive.writestr("xl/workbook.xml", ET.tostring(book))
            archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
            archive.writestr("xl/worksheets/custom_scores.xml", ET.tostring(sheet))
            archive.writestr("xl/sharedStrings.xml", ET.tostring(shared))

    def import_rows(self):
        result = IMPORTER.import_review_scores(self.workbook, self.run_dir)
        with self.output.open(encoding="utf-8-sig", newline="") as stream:
            return result, list(csv.DictReader(stream))

    def fill_reviewed(self, row):
        row.update(reviewed="true", reviewer="人工测试审核者", answer_correct="false", citation_supported="true")
        if row["category"] == "unanswerable":
            row["hallucinated_trap"] = "false"
        else:
            row["top3_any_hit"] = "true"
        if row["category"] == "multi":
            row["all_groups_covered"] = "false"

    def assert_blocked(self, message=None):
        with self.assertRaisesRegex(EvaluationError, message or ".*"):
            IMPORTER.import_review_scores(self.workbook, self.run_dir)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.original.read_bytes(), b"existing human judgments must remain untouched\n")

    def test_blank_workbook_stays_zero_reviewed_and_preserves_original(self):
        self.write_workbook()
        result, rows = self.import_rows()
        self.assertEqual(result["records"], 40)
        self.assertEqual(result["reviewed_records"], 0)
        self.assertTrue(all(not row[field] for row in rows for field in IMPORTER.HUMAN_FIELDS))
        self.assertTrue(all(summary["reviewed_records"] == 0 and summary["answer_accuracy"]["rate"] is None
                            for summary in summarize_records(self.records, rows)))
        self.assertEqual(self.original.read_bytes(), b"existing human judgments must remain untouched\n")

    def test_column_reordering_shared_strings_inline_text_and_excel_booleans(self):
        self.fill_reviewed(self.rows[0])
        self.rows[0].update(reviewed="1", answer_correct="0", citation_supported="1", notes="保留原始备注\n与第二行")
        typed = {(7, field): "b" for field in ("reviewed", "answer_correct", "citation_supported")}
        typed.update({(7, "reviewer"): "s", (7, "question_id"): "s", (6, "question_id"): "s"})
        self.write_workbook(headers=list(reversed(IMPORTER.WORKBOOK_FIELDS)), typed=typed)
        result, rows = self.import_rows()
        self.assertEqual(result["reviewed_records"], 1)
        self.assertEqual(rows[0]["answer_correct"], "false")
        self.assertEqual(rows[0]["reviewer"], "人工测试审核者")
        self.assertEqual(rows[0]["notes"], "保留原始备注\n与第二行")

    def test_partial_scores_are_preserved_without_approval(self):
        self.rows[0].update(answer_correct="YES", reviewed="false", notes="尚未核对证据")
        self.rows[1].update(top3_any_hit="0")
        self.write_workbook()
        result, rows = self.import_rows()
        self.assertEqual(result["reviewed_records"], 0)
        self.assertEqual(rows[0]["answer_correct"], "true")
        self.assertEqual(rows[0]["reviewed"], "false")
        self.assertEqual(rows[0]["citation_supported"], "")
        self.assertEqual(rows[1]["top3_any_hit"], "false")
        self.assertEqual(rows[1]["reviewed"], "")

    def test_each_category_requires_only_its_applicable_scores(self):
        for index in (0, 10, 15):
            self.fill_reviewed(self.rows[index])
        self.write_workbook()
        result, rows = self.import_rows()
        self.assertEqual(result["reviewed_records"], 3)
        self.assertEqual(rows[10]["top3_any_hit"], "")
        self.assertEqual(rows[0]["all_groups_covered"], "")
        self.assertEqual(rows[15]["hallucinated_trap"], "")

    def test_unknown_duplicate_missing_and_edited_category_are_rejected(self):
        cases = [("duplicate", 1, "question_id", "Q01"),
                 ("unknown", 0, "question_id", "Q99"),
                 ("run", 0, "run_id", "different-run"),
                 ("method", 0, "method", "hybrid"),
                 ("category", 0, "category", "multi")]
        for name, index, field, value in cases:
            with self.subTest(name=name):
                rows = copy.deepcopy(self.rows)
                rows[index][field] = value
                self.write_workbook(rows=rows)
                self.assert_blocked()
        self.write_workbook(missing_rows={7})
        self.assert_blocked()
        self.write_workbook(extra_rows={47: self.rows[0]})
        self.assert_blocked("Unexpected data")

    def test_invalid_boolean_or_na_is_not_coerced_to_false(self):
        for field, value in [("answer_correct", "n.a."), ("reviewed", "maybe"),
                             ("top3_any_hit", "2"), ("citation_supported", "#N/A")]:
            with self.subTest(field=field):
                rows = copy.deepcopy(self.rows)
                rows[0][field] = value
                self.write_workbook(rows=rows)
                self.assert_blocked("Expected blank/true/false")

    def test_inapplicable_fields_must_be_empty_even_on_unreviewed_rows(self):
        for index, field in [(0, "hallucinated_trap"), (0, "all_groups_covered"),
                             (10, "top3_any_hit"), (10, "all_groups_covered"), (15, "hallucinated_trap")]:
            with self.subTest(index=index, field=field):
                rows = copy.deepcopy(self.rows)
                rows[index][field] = "false"
                self.write_workbook(rows=rows)
                self.assert_blocked("must be blank")

    def test_reviewed_rows_need_real_reviewer_and_complete_scores(self):
        for reviewer in ("", "   ", "N/A", "待填写", "ChatGPT"):
            with self.subTest(reviewer=reviewer):
                rows = copy.deepcopy(self.rows)
                self.fill_reviewed(rows[0])
                rows[0]["reviewer"] = reviewer
                self.write_workbook(rows=rows)
                self.assert_blocked("real human reviewer")
        for index, field in [(0, "citation_supported"), (10, "hallucinated_trap"), (15, "all_groups_covered")]:
            with self.subTest(index=index, field=field):
                rows = copy.deepcopy(self.rows)
                self.fill_reviewed(rows[index])
                rows[index][field] = ""
                self.write_workbook(rows=rows)
                self.assert_blocked("incomplete scores")

    def test_all_human_fields_reject_formulas_even_with_cached_values(self):
        for field in sorted(IMPORTER.HUMAN_FIELDS):
            with self.subTest(field=field):
                self.write_workbook(formulas={(7, field): 'IF(1=1,"true","")'})
                self.assert_blocked("Formula not allowed")

    def test_existing_csvs_are_never_overwritten(self):
        self.write_workbook()
        with self.assertRaisesRegex(EvaluationError, "Refusing to overwrite"):
            IMPORTER.import_review_scores(self.workbook, self.run_dir, self.original)
        self.import_rows()
        before = self.output.read_bytes()
        with self.assertRaisesRegex(EvaluationError, "Refusing to overwrite"):
            IMPORTER.import_review_scores(self.workbook, self.run_dir)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(self.original.read_bytes(), b"existing human judgments must remain untouched\n")

    def test_machine_headers_must_be_present_and_unique(self):
        headers = list(IMPORTER.WORKBOOK_FIELDS)
        headers[headers.index("answer_correct")] = "answer_correct（请评分）"
        self.write_workbook(headers=headers)
        self.assert_blocked("Missing machine field")
        headers = list(IMPORTER.WORKBOOK_FIELDS)
        headers[headers.index("answer_correct")] = "reviewed"
        self.write_workbook(headers=headers)
        self.assert_blocked("duplicate machine field")

    def test_raw_run_must_be_complete_unique_and_consistent(self):
        original = copy.deepcopy(self.records)
        for name in ("missing", "duplicate", "mixed_run", "wrong_method", "wrong_questions"):
            with self.subTest(name=name):
                self.records = copy.deepcopy(original)
                entries = self.records["vector"]
                if name == "missing":
                    entries.pop()
                elif name == "duplicate":
                    entries[-1] = copy.deepcopy(entries[-2])
                elif name == "mixed_run":
                    entries[0]["run_id"] = "other-run"
                elif name == "wrong_method":
                    entries[0]["method"] = "bm25"
                else:
                    entries[0]["question_id"] = "Q21"
                self.write_records()
                self.write_workbook()
                self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
