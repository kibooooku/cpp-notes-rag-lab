/** Build the blank human-scoring workbook from one frozen, complete formal run.
 * Authoring uses only the bundled @oai/artifact-tool runtime. No API requests.
 * Read-only provenance columns and every retrieved/cited text are preserved.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
function option(flag, fallback) {
  const index = args.indexOf(flag);
  return index < 0 ? fallback : args[index + 1];
}
const runDir = path.resolve(option("--run-dir", path.join(ROOT, "eval/runs/formal-01")));
const outputPath = path.resolve(option("--output", path.join(ROOT, "outputs/formal-20261021/评分表.xlsx")));
const cacheDir = path.resolve(option("--cache-dir", path.join(ROOT, ".cache/formal-review-workbook")));
const runtimeDir = path.resolve(process.env.CODEX_SPREADSHEET_RUNTIME || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"));
for (const target of [outputPath, cacheDir]) {
  const relative = path.relative(ROOT, target);
  assert(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "Output/cache must stay inside this project");
}

await fs.mkdir(cacheDir, { recursive: true });
const runtimeModules = path.join(runtimeDir, "node_modules");
const junctionPath = path.join(cacheDir, "node_modules");
try {
  await fs.stat(junctionPath);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  await fs.symlink(runtimeModules, junctionPath, process.platform === "win32" ? "junction" : "dir");
}
const bundledRequire = createRequire(path.join(cacheDir, "package.json"));
const { FileBlob, SpreadsheetFile, Workbook } = await import(pathToFileURL(bundledRequire.resolve("@oai/artifact-tool")).href);
const json = async (file) => JSON.parse((await fs.readFile(file, "utf8")).replace(/^\uFEFF/, ""));
const hash = (text) => createHash("sha256").update(text).digest("hex");
const blank = (value) => value === null || value === undefined || value === "";
const key = (row) => `${row.run_id}\u0000${row.question_id}\u0000${row.method}`;
const HEADERS = [
  "question_id", "method", "category", "model_status", "question", "required_points", "answer",
  "answer_correct", "hallucinated_trap", "top3_any_hit", "all_groups_covered", "citation_supported",
  "reviewer", "reviewed", "notes", "elapsed_seconds", "applicable_fields", "expected_sources",
  "evidence_rows", "run_id", "result_source",
];
const EVIDENCE_HEADERS = [
  "question_id", "method", "kind", "rank", "chunk_id", "source_location", "full_text", "score",
  "review_flags", "present", "run_id", "result_source", "spans_json",
  "part_index", "part_count", "evidence_id",
];
const FIRST_ROW = 7;
const metadata = await json(path.join(runDir, "run_metadata.json"));
assert.equal(metadata.kind, "formal");
assert.equal(metadata.complete, true, "Wait until the formal run is complete");
assert.equal(metadata.record_count, 40);
const snapshotPath = path.join(runDir, "questions.snapshot.json");
const questionsPath = option("--questions", await fs.stat(snapshotPath).then(() => snapshotPath).catch(() => path.join(ROOT, "eval/questions.json")));
const questions = await json(questionsPath);
assert.equal(questions.length, 20);
const byQuestion = new Map(questions.map((q) => [q.question_id, q]));
assert.equal(byQuestion.size, 20);
const records = [];
for (const method of ["bm25", "vector"]) {
  const rows = await json(path.join(runDir, `results_${method}.json`));
  assert.equal(rows.length, 20, `${method} must contain exactly 20 raw records`);
  for (const row of rows) {
    assert.equal(row.run_id, metadata.run_id);
    assert.equal(row.method, method);
    assert.equal(row.kind, "formal");
    assert.equal(row.question_sha256, metadata.question_sha256);
    const q = byQuestion.get(row.question_id);
    assert(q, `Unknown question ${row.question_id}`);
    assert.equal(row.category, q.category);
    assert.equal(row.question, q.question);
    assert.equal(row.answer.question, row.question);
    assert.equal(row.answer.method, method);
    assert(Array.isArray(row.answer.hits) && Array.isArray(row.answer.citations));
    assert.equal(typeof row.answer.answer, "string");
    assert.equal(typeof row.elapsed_seconds, "number");
    records.push(row);
  }
}
const rawByKey = new Map(records.map((row) => [key(row), row]));
assert.equal(rawByKey.size, 40, "Duplicate (run_id, question_id, method)");
const csvText = (await fs.readFile(path.join(runDir, "judgments.csv"), "utf8")).replace(/^\uFEFF/, "");
const csvBook = await Workbook.fromCSV(csvText, { sheetName: "CSV" });
const [csvHeaders, ...csvValues] = csvBook.worksheets.getItemAt(0).getUsedRange().values;
const csvRows = csvValues.filter((row) => row.some((v) => !blank(v))).map((values) => Object.fromEntries(csvHeaders.map((header, index) => [header, values[index] ?? ""])));
assert.equal(csvRows.length, 40, "judgments.csv must contain 40 rows");
const editableFields = HEADERS.slice(7, 15);
const sourceKeys = new Set();
for (const row of csvRows) {
  assert(rawByKey.has(key(row)), "CSV identity does not match raw results");
  assert(!sourceKeys.has(key(row)), "Duplicate CSV identity");
  assert.equal(row.category, rawByKey.get(key(row)).category);
  sourceKeys.add(key(row));
  for (const field of editableFields) assert(blank(row[field]), `Do not overwrite an existing human judgment: ${row.question_id}/${row.method}/${field}`);
}
const orderedRecords = csvRows.map((row) => rawByKey.get(key(row)));
const publishedDir = path.join(ROOT, "eval/formal", path.basename(runDir));
let provenanceDir = runDir;
try {
  for (const name of ["results_bm25.json", "results_vector.json"]) {
    assert.equal(hash(await fs.readFile(path.join(publishedDir, name))), hash(await fs.readFile(path.join(runDir, name))), "Published raw results differ from the original run");
  }
  provenanceDir = publishedDir;
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const resultFile = (record) => `${path.relative(ROOT, provenanceDir).replaceAll("\\", "/")}/results_${record.method}.json#${record.question_id}`;
const location = (spans) => spans.map((s) => `${s.source_file} / ${s.heading || "无标题"} / L${s.line_start}–${s.line_end}`).join("\n");
const originalEvidence = [];
for (const record of orderedRecords) {
  for (let rank = 1; rank <= 3; rank += 1) {
    const hit = record.answer.hits.find((item) => item.rank === rank) || null;
    const chunk = hit?.chunk;
    const spans = chunk?.spans || [];
    originalEvidence.push([record.question_id, record.method, "top3", rank, chunk?.chunk_id || "", location(spans), chunk?.text || "", hit?.score ?? null, (chunk?.review_flags || []).join("\n"), Boolean(hit), record.run_id, resultFile(record), JSON.stringify(spans)]);
  }
}
const top3RowCount = originalEvidence.length;
for (const record of orderedRecords) {
  record.answer.citations.forEach((citation, index) => {
    const spans = [{ source_file: citation.source_file, heading: citation.heading, line_start: citation.line_start, line_end: citation.line_end }];
    originalEvidence.push([record.question_id, record.method, "citation", index + 1, citation.chunk_id || "", location(spans), citation.quote, null, (citation.review_flags || []).join("\n"), true, record.run_id, resultFile(record), JSON.stringify(spans)]);
  });
}
const textUnits = (line) => Array.from(line).reduce((n, c) => n + (/[^\u0000-\u00FF]/u.test(c) ? 2 : c === "\t" ? 4 : 1), 0);
function splitEvidence(text) {
  const parts = [];
  let lines = [];
  let wrappedLines = 0;
  for (const line of text.split("\n")) {
    const wrapped = Math.max(1, Math.ceil(textUnits(line) / 118));
    if (lines.length && (lines.length >= 18 || wrappedLines + wrapped > 20)) {
      parts.push(lines.join("\n"));
      lines = [];
      wrappedLines = 0;
    }
    lines.push(line);
    wrappedLines += wrapped;
  }
  parts.push(lines.join("\n"));
  assert.equal(parts.join("\n"), text, "Evidence split changed whitespace or content");
  assert(parts.every((part) => part.split("\n").length <= 18));
  return parts;
}
const evidence = [];
const originalByEvidenceId = new Map();
const evidenceLocations = new Map(orderedRecords.map((record) => [key(record), { top3: [], citations: [] }]));
const evidenceGroups = [];
for (const row of originalEvidence) {
  const id = `${row[10]}:${row[0]}:${row[1]}:${row[2]}:${row[3]}`;
  assert(!originalByEvidenceId.has(id));
  originalByEvidenceId.set(id, row);
  const parts = splitEvidence(row[6]);
  const start = FIRST_ROW + evidence.length;
  parts.forEach((text, index) => {
    const displayed = [...row];
    displayed[6] = text;
    evidence.push([...displayed, index + 1, parts.length, id]);
  });
  const end = FIRST_ROW + evidence.length - 1;
  const loc = evidenceLocations.get(key({ run_id: row[10], question_id: row[0], method: row[1] }));
  loc[row[2] === "top3" ? "top3" : "citations"].push([start, end]);
  evidenceGroups.push({ id, kind: row[2], part_count: parts.length, start, end });
}
const top3DisplayCount = evidence.filter((row) => row[2] === "top3").length;
const applicable = (category) => {
  if (category === "unanswerable") return "适用：完整答对、陷阱硬编、引文支持、审核人、已审核、备注。不适用：Top3任一命中、跨片段全覆盖。";
  if (category === "multi") return "适用：完整答对、Top3任一命中、跨片段全覆盖、引文支持、审核人、已审核、备注。不适用：陷阱硬编。";
  return "适用：完整答对、Top3任一命中、引文支持、审核人、已审核、备注。不适用：陷阱硬编、跨片段全覆盖。";
};
const scoring = orderedRecords.map((record) => {
  const q = byQuestion.get(record.question_id);
  const loc = evidenceLocations.get(key(record));
  const sources = q.required_evidence_groups.map((group) => `${group.group_id}: ${group.sources.map((s) => `${s.source_file} L${s.line_start}–${s.line_end}`).join("；可接受替代：")}`).join("\n");
  const rowLocations = `Top3：${loc.top3[0][0]}–${loc.top3.at(-1)[1]} 行` + (loc.citations.length ? `。引文：${loc.citations.map(([start, end]) => start === end ? `${start}` : `${start}–${end}`).join("、")} 行` : "。无实际引文。");
  return [record.question_id, record.method, record.category, record.answer.status, record.question, q.required_points.map((point, index) => `${index + 1}. ${point}`).join("\n"), record.answer.answer,
    null, null, null, null, null, null, null, null,
    record.elapsed_seconds, applicable(record.category), sources || `资料无支持所问结论的证据组。${q.missing_information || ""}`, rowLocations, record.run_id, resultFile(record)];
});
for (const row of [...scoring, ...evidence]) {
  for (const value of row) {
    if (typeof value === "string") assert(value.length <= 32767, "Excel cell limit exceeded; text was not truncated");
  }
}

const workbook = Workbook.create();
const main = workbook.worksheets.add("人工评分");
const details = workbook.worksheets.add("证据明细");
const mainEnd = FIRST_ROW + scoring.length - 1;
const evidenceEnd = FIRST_ROW + evidence.length - 1;
main.getRange(`A6:U${mainEnd}`).values = [HEADERS, ...scoring];
details.getRange(`A6:P${evidenceEnd}`).values = [EVIDENCE_HEADERS, ...evidence];
const font = { name: "Arial", size: 10, color: "#1F2937" };
const header = { fill: "#334155", font: { ...font, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
const inputFill = "#FFF2CC";
const inapplicableFill = "#EEF0F3";
for (const [sheet, lastCol, lastRow] of [[main, "U", mainEnd], [details, "P", evidenceEnd]]) {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastCol}${lastRow}`).format.font = font;
  sheet.getRange(`A6:${lastCol}${lastRow}`).format.wrapText = true;
  sheet.getRange(`A6:${lastCol}${lastRow}`).format.verticalAlignment = "top";
  sheet.getRange(`A6:${lastCol}6`).format = header;
  sheet.getRange(`A6:${lastCol}6`).format.rowHeight = 36;
  sheet.getRange(`A2:${lastCol}2`).format.rowHeight = 24;
  sheet.getRange("A2").format.font = { ...font, size: 14, bold: true };
  sheet.getRange(`A3:${lastCol}5`).format.rowHeight = 20;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(3);
  const table = sheet.tables.add(`A6:${lastCol}${lastRow}`, true, sheet === main ? "HumanReview" : "EvidenceDetails");
  table.showFilterButton = true;
}
main.tabColor = "#334155";
main.getRange("A2").values = [["正式实验人工评分"]];
main.getRange("A3").values = [["浅黄色区域由人工填写。true=是，false=否，空白=未评。灰色字段不适用，保持空白。"]];
main.getRange("A4").values = [["共40条。answerable=有答案，unanswerable=陷阱，multi=跨片段。按概念、机制和适用条件核对，接受语义等价。"]];
main.getRange("A5").values = [["H完整答对，I陷阱硬编，J任一命中，K全组覆盖，L引文支持。M审核人，N已审核，O备注。引文仍须逐字核对原文。"]];
details.getRange("A2").values = [["检索证据与实际引用"]];
details.getRange("A3").values = [[`Top3共${top3RowCount}条，实际引用${originalEvidence.length - top3RowCount}条，分成${evidence.length}个连续显示段。内容逐字保留。`]];
details.getRange("A4").values = [["按题号和方法筛选，再按 evidence_id、part_index 连续阅读。rank 为检索排序或引文序号，part_count 为该条总段数。"]];
const readingPath = path.join(provenanceDir, "review-details.md");
const readingGuide = await fs.stat(readingPath).then(() => `完整阅读版：${path.relative(ROOT, readingPath).replaceAll("\\", "/")}。`).catch(() => "按 result_source 打开完整原始记录。");
details.getRange("A5").values = [[`每段最多18行原文，按换行拼接即为完整证据。${readingGuide}present 不是人工命中判分。`]];
const widths = [11, 12, 15, 15, 42, 54, 68, 15, 19, 15, 22, 20, 15, 12, 45, 18, 50, 60, 38, 35, 65];
const evidenceWidths = [11, 12, 14, 7, 30, 64, 120, 12, 26, 12, 35, 65, 75, 12, 12, 65];
const column = (index) => {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + ((n - 1) % 26)) + result;
  return result;
};
widths.forEach((width, index) => { main.getRange(`${column(index)}1:${column(index)}${mainEnd}`).format.columnWidth = width; });
evidenceWidths.forEach((width, index) => { details.getRange(`${column(index)}1:${column(index)}${evidenceEnd}`).format.columnWidth = width; });
main.getRange(`H7:O${mainEnd}`).format.fill = inputFill;
main.getRange("H6:O6").format.fill = "#715C22";
main.getRange(`P7:P${mainEnd}`).setNumberFormat("0.000");
details.getRange(`H7:H${evidenceEnd}`).setNumberFormat("0.000");
details.getRange(`D7:D${evidenceEnd}`).setNumberFormat("0");
const booleanValidation = { rule: { type: "list", values: ["true", "false"] } };
for (let i = 0; i < scoring.length; i += 1) {
  const row = FIRST_ROW + i;
  const category = scoring[i][2];
  const fields = ["H", "L", "N", ...(category === "unanswerable" ? ["I"] : ["J"]), ...(category === "multi" ? ["K"] : [])];
  for (const name of fields) main.getRange(`${name}${row}`).dataValidation = booleanValidation;
  const inapplicable = category === "unanswerable" ? ["J", "K"] : category === "multi" ? ["I"] : ["I", "K"];
  for (const name of inapplicable) main.getRange(`${name}${row}`).format.fill = inapplicableFill;
  if (scoring[i][3] === "error") main.getRange(`D${row}`).format = { fill: "#FDE7E7", font: { ...font, color: "#A61D24", bold: true } };
}
const overflowRows = [];
function fittedHeight(texts, columnWidths, rowNumber, sheetName) {
  const lines = Math.max(...texts.map((value, index) => {
    if (blank(value) || typeof value !== "string") return 1;
    return value.split(/\r?\n/).reduce((sum, line) => {
      const units = textUnits(line);
      return sum + Math.max(1, Math.ceil(units / Math.max(8, columnWidths[index] - 2)));
    }, 0);
  }));
  const proposed = Math.max(34, lines * 12 + 10);
  if (proposed > 409) overflowRows.push({ sheet: sheetName, row: rowNumber, estimatedHeight: proposed });
  return Math.min(409, proposed);
}
scoring.forEach((row, index) => { main.getRange(`A${FIRST_ROW + index}:U${FIRST_ROW + index}`).format.rowHeight = fittedHeight(row, widths, FIRST_ROW + index, main.name); });
evidence.forEach((row, index) => { details.getRange(`A${FIRST_ROW + index}:P${FIRST_ROW + index}`).format.rowHeight = fittedHeight(row, evidenceWidths, FIRST_ROW + index, details.name); });
assert.equal(overflowRows.length, 0, "Display rows still exceed Excel's maximum readable row height");

// A data cell is text, not a generated Excel formula or an AI grading formula.
for (const [sheet, values, lastCol] of [[main, scoring, "U"], [details, evidence, "P"]]) {
  assert.deepEqual(sheet.getRange(`A7:${lastCol}${FIRST_ROW + values.length - 1}`).values, values);
}
for (const row of main.getRange(`H7:O${mainEnd}`).values) assert(row.every(blank));
if (await fs.stat(outputPath).then(() => true).catch(() => false)) {
  assert(args.includes("--overwrite-empty"), "Output exists; refusing to overwrite possible human scores");
  const existing = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const old = existing.worksheets.getItem("人工评分");
  assert(old.getRange(`H7:O${mainEnd}`).values.every((row) => row.every(blank)), "Existing workbook contains human inputs");
  assert.deepEqual(old.getRange(`A7:C${mainEnd}`).values, scoring.map((row) => row.slice(0, 3)), "Existing workbook identities differ");
  assert.deepEqual(old.getRange(`T7:T${mainEnd}`).values, scoring.map((row) => [row[19]]), "Existing workbook run_id differs");
}
workbook.recalculate();
const errorScan = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 20 }, maxChars: 2000 });
const inspections = [];
for (const [sheetName, range] of [["人工评分", "A6:D8"], ["人工评分", "H6:P8"], ["证据明细", "A6:F8"]]) {
  inspections.push((await workbook.inspect({ kind: "table", range: `${sheetName}!${range}`, include: "values,formulas", tableMaxRows: 3, tableMaxCols: 9, tableMaxCellChars: 100, maxChars: 2500 })).ndjson);
}
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(outputPath);
// Artifact Tool may emit an inspection sidecar; keep supporting material in
// the authorized cache instead of mixing it with the single user deliverable.
try {
  const sidecar = `${outputPath}.inspect.ndjson`;
  await fs.rename(sidecar, path.join(cacheDir, "export-inspection.ndjson"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
for (const [sheetName, expected, lastCol] of [["人工评分", scoring, "U"], ["证据明细", evidence, "P"]]) {
  const actual = reopened.worksheets.getItem(sheetName).getRange(`A7:${lastCol}${FIRST_ROW + expected.length - 1}`).values;
  assert.equal(actual.length, expected.length);
  actual.forEach((row, i) => row.forEach((value, j) => {
    assert(blank(value) && blank(expected[i][j]) || value === expected[i][j], `${sheetName} value changed at row ${FIRST_ROW + i}, column ${column(j)}`);
  }));
}
const restoredGroups = new Map();
for (const row of reopened.worksheets.getItem("证据明细").getRange(`A7:P${evidenceEnd}`).values) {
  const rows = restoredGroups.get(row[15]) || [];
  rows.push(row);
  restoredGroups.set(row[15], rows);
}
assert.equal(restoredGroups.size, originalEvidence.length);
for (const [id, rows] of restoredGroups) {
  rows.sort((a, b) => a[13] - b[13]);
  assert.equal(rows.length, rows[0][14]);
  assert.deepEqual(rows.map((row) => row[13]), Array.from({ length: rows.length }, (_, index) => index + 1));
  assert.equal(rows.map((row) => row[6] ?? "").join("\n"), originalByEvidenceId.get(id)[6], `Reassembled exported evidence changed: ${id}`);
}
const firstSplit = evidenceGroups.find((group) => group.part_count > 1);
const previews = [
  ["人工评分", "A1:G8", "scoring-start.png"],
  ["人工评分", "H1:P8", "scoring-inputs.png"],
  ["人工评分", "A26:G27", "scoring-method-boundary.png"],
  ["证据明细", "A1:G8", "evidence-start.png"],
  ["证据明细", `A${FIRST_ROW + top3DisplayCount - 1}:G${FIRST_ROW + top3DisplayCount}`, "evidence-citation-boundary.png"],
  ["证据明细", `A${firstSplit.start}:G${firstSplit.end}`, "evidence-long-parts.png"],
  ["证据明细", `N${firstSplit.start}:P${firstSplit.end}`, "evidence-parts-index.png"],
];
for (const [sheetName, range, name] of previews) {
  const blob = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(cacheDir, name), new Uint8Array(await blob.arrayBuffer()));
}
const validation = {
  run_id: metadata.run_id, source_questions: path.relative(ROOT, questionsPath).replaceAll("\\", "/"),
  question_snapshot_sha256: hash(await fs.readFile(questionsPath)),
  main_record_count: scoring.length, top3_slot_count: top3RowCount,
  actual_top3_count: originalEvidence.slice(0, top3RowCount).filter((row) => row[9]).length,
  actual_citation_count: originalEvidence.length - top3RowCount,
  evidence_display_rows: evidence.length, segmented_evidence_groups: evidenceGroups.filter((group) => group.part_count > 1).length,
  evidence_groups: evidenceGroups,
  human_cells_blank: true, exported_text_roundtrip_exact: true, segmented_evidence_reassembles_exactly: true,
  error_records_preserved: scoring.filter((row) => row[3] === "error").map((row) => ({ question_id: row[0], method: row[1] })),
  sheets: [main.name, details.name], overflowRows, formula_error_scan: errorScan.ndjson,
  inspections, output: outputPath,
};
await fs.writeFile(path.join(cacheDir, "validation.json"), JSON.stringify(validation, null, 2) + "\n", "utf8");
console.log(JSON.stringify({ output: outputPath, records: scoring.length, top3: top3RowCount, citations: originalEvidence.length - top3RowCount, evidenceDisplayRows: evidence.length, manualFieldsBlank: true, previewDirectory: cacheDir }));
