"""Exercise real TXT/BM25 import and exact provenance without an API call."""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cpp_notes_rag.chunking import chunk_documents
from cpp_notes_rag.citations import validate_response
from cpp_notes_rag.config import Settings
from cpp_notes_rag.indexing import IndexManager
from cpp_notes_rag.loaders import load_documents
from cpp_notes_rag.schemas import Hit


def main():
    settings = Settings.load(ROOT)
    originals = load_documents([settings.source_dir])
    target = ROOT / "data" / "import_samples"
    target.mkdir(parents=True, exist_ok=True)
    # A real same-stem TXT export has the same content and source line offsets.
    source = settings.source_dir / "3指针 引用.md"
    exported = target / "3指针 引用.txt"
    exported.write_bytes(source.read_bytes())
    dual = load_documents([source, exported])
    assert len(dual) == 1 and dual[0].source_file.endswith(".md")
    md_chunks = chunk_documents(load_documents([source]))
    txt_chunks = chunk_documents(load_documents([exported]))
    assert [c.chunk_id for c in md_chunks] == [c.chunk_id for c in txt_chunks]
    assert [c.text for c in md_chunks] == [c.text for c in txt_chunks]
    # Isolate the acceptance index; do not replace the application's dual index.
    base = ROOT / ".cache" / "format-validation"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as temporary:
        temporary = Path(temporary)
        notes = temporary / "notes"
        notes.mkdir()
        copy = notes / exported.name
        copy.write_bytes(exported.read_bytes())
        txt_settings = Settings(root=ROOT, source_dir=notes, storage_dir=temporary / "index")
        index = IndexManager(txt_settings)
        manifest = index.build(notes, vector=False)
        hits = index.retrieve("引用 ref = b ptr = &b", "bm25")
        assert hits and all(h.chunk.spans[0].source_file.endswith(".txt") for h in hits)
        # Deterministic provenance validation, not a generated/scored answer.
        first = txt_chunks[0]
        line = next(line for line in first.text.splitlines() if line.strip())
        _, _, citations = validate_response({"status": "supported", "answer": "验证引文 [1]", "citations": [
            {"chunk_id": first.chunk_id, "quote": line}]}, [Hit(first, 1.0, 1)], notes)
        result = {"markdown_documents": len(originals), "txt_imported": True, "same_stem_deduplicated": True,
                  "chunk_identity_preserved": True, "txt_index_chunks": manifest["chunk_count"],
                  "txt_retrieved": len(hits), "txt_citation_verified": bool(citations),
                  "exported_from": source.name, "exported_file": str(exported.relative_to(ROOT)),
                  "line_mapping": "same line number as original: exact byte copy", "api_calls": 0}
    (target / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
