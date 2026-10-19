"""Inventory the original C++ Markdown notes using only the standard library.

Read-only for source materials; write a reproducible inventory under data/.
Counts are disclosed separately and do not determine course acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "问答素材"


def narrative_lines(text: str) -> tuple[list[tuple[int, str]], int, bool]:
    output = []
    active_marker = None
    blocks = 0
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            if active_marker is None:
                active_marker = (match[1][0], len(match[1]))
                blocks += 1
            elif match[1][0] == active_marker[0] and len(match[1]) >= active_marker[1]:
                active_marker = None
            continue
        if active_marker is None:
            output.append((number, line))
    return output, blocks, active_marker is not None


def prose_only(text: str) -> str:
    text = re.sub(r"!\[\[[^\]]+\]\]", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"(`+).*?\1", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    return text


def han_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    paths = sorted(MATERIALS.glob("*.md"), key=lambda path: int(re.match(r"\d+", path.name)[0]))
    if not paths:
        raise SystemExit("No Markdown notes found")
    alias_file = ROOT / "data" / "attachment_map.json"
    attachment_aliases = json.loads(alias_file.read_text(encoding="utf-8")) if alias_file.exists() else {}
    records = []
    for path in paths:
        payload = path.read_bytes()
        text = payload.decode("utf-8-sig")
        narrative, block_count, unclosed = narrative_lines(text)
        body = prose_only("\n".join(line for _, line in narrative))
        embeds = []
        for number, line in narrative:
            for match in re.finditer(r"!\[\[([^\]]+)\]\]|!\[[^\]]*\]\(([^)]+)\)", line):
                raw = match[1] or match[2]
                target = raw.split("|", 1)[0]
                resolved = attachment_aliases.get(path.name, {}).get(target, target)
                embeds.append({"line": number, "reference": raw,
                               "resolved_attachment": resolved,
                               "resolution": "explicit_alias" if resolved != target else "literal_path",
                               "local_target_present": not resolved.startswith(("http://", "https://")) and (path.parent / resolved).is_file()})
        records.append({
            "source_file": path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "line_count": len(text.splitlines()),
            "headings": [{"line": number, "text": line} for number, line in narrative if re.match(r"^#{1,6}\s", line)],
            "fenced_code_blocks": block_count,
            "unclosed_code_fence": unclosed,
            "raw_chinese_characters": han_count(text),
            "body_chinese_characters": han_count(body),
            "body_non_whitespace_characters": len(re.sub(r"[\s#*>_~|\-]", "", body)),
            "embedded_images": embeds,
        })
    inventory = {
        "schema_version": 1,
        "originality": "User confirms these are their own C++ notes; this tool measures text, not authorship.",
        "count_method": "Body Han characters exclude fenced code, inline code, HTML tags, image embeds and URLs; range U+3400-U+4DBF/U+4E00-U+9FFF. Non-whitespace characters are a separate metric, not words.",
        "file_count": len(records),
        "total_lines": sum(record["line_count"] for record in records),
        "total_fenced_code_blocks": sum(record["fenced_code_blocks"] for record in records),
        "total_raw_chinese_characters": sum(record["raw_chinese_characters"] for record in records),
        "total_body_chinese_characters": sum(record["body_chinese_characters"] for record in records),
        "single_file_body_han_at_least_1500": [record["source_file"] for record in records if record["body_chinese_characters"] >= 1500],
        "course_1500_requirement_verified": None,
        "missing_embedded_images": [{"source_file": record["source_file"], **embed} for record in records for embed in record["embedded_images"] if not embed["local_target_present"]],
        "attachments": [{"file": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path in sorted(MATERIALS.iterdir()) if path.is_file() and path.suffix.lower() != ".md"],
        "files": records,
    }
    output = ROOT / "data" / "corpus_inventory.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: inventory[key] for key in ("file_count", "total_lines", "total_fenced_code_blocks", "total_raw_chinese_characters", "total_body_chinese_characters", "single_file_body_han_at_least_1500")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
