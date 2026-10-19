import json
from typing import ClassVar

import httpx
import pytest

from cpp_notes_rag.citations import CitationError, validate_response
from cpp_notes_rag.config import Settings
from cpp_notes_rag.generation import DeepSeekClient, GenerationError
from cpp_notes_rag.schemas import Chunk, Hit, SourceSpan
from cpp_notes_rag.service import QAService


@pytest.fixture
def setup_case(tmp_path, monkeypatch):
    source = tmp_path / "notes"
    source.mkdir()
    text = "# 引用\nref = b 是对绑定对象赋值。\n"
    (source / "reference.md").write_text(text, encoding="utf-8")
    settings = Settings(root=tmp_path, source_dir=source)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder")
    hit = Hit(Chunk("c1", "d1", text.strip(), [SourceSpan("reference.md", "引用", 1, 2)]), 1.0, 1)
    return settings, hit


def payload(status="supported"):
    return {"status": status, "answer": "对原先绑定的对象赋值。[1]",
            "citations": [{"chunk_id": "c1", "quote": "ref = b 是对绑定对象赋值。"}]}


def test_real_request_contract_and_validated_source(setup_case):
    settings, hit = setup_case

    def handler(request):
        sent = json.loads(request.content)
        assert sent["thinking"] == {"type": "disabled"}
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["stream"] is False
        assert len(sent["messages"]) == 2
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(payload(), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "prompt_cache_hit_tokens": 10}})

    client = DeepSeekClient(settings, transport=httpx.MockTransport(handler))
    parsed, raw, usage = client.generate("ref = b 做什么？", [hit])
    status, _, citations = validate_response(parsed, [hit], settings.source_dir)
    assert status == "supported"
    assert citations[0]["line_start"] == 2
    assert json.loads(raw)["status"] == "supported"
    assert usage["completion_tokens"] == 30
    ledger = client.cost.snapshot()
    assert ledger["requests"] == 1 and ledger["unsettled_requests"] == 0


@pytest.mark.parametrize("finish", ["length", "content_filter", None])
def test_truncated_outputs_are_errors_with_usage_preserved(setup_case, finish):
    settings, hit = setup_case
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
        "choices": [{"finish_reason": finish, "message": {"content": "{\"status\":"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 1400}}))
    client = DeepSeekClient(settings, transport=transport)
    with pytest.raises(GenerationError) as error:
        client.generate("ref = b？", [hit])
    assert error.value.usage["completion_tokens"] == 1400
    assert client.cost.snapshot()["unsettled_requests"] == 0


def test_network_failure_remains_reserved_and_not_refusal(setup_case):
    settings, hit = setup_case

    def handler(req):
        raise httpx.ReadTimeout("simulated timeout", request=req)

    client = DeepSeekClient(settings, transport=httpx.MockTransport(handler))

    class Index:
        manifest: ClassVar = {"corpus_hash": "test-corpus"}

        def retrieve(self, *_args, **_kwargs):
            return [hit]

    response = QAService(settings, index=Index(), client=client).ask("ref = b？")
    assert response.status == "error"
    assert response.answer != "文档中没有相关信息"
    assert client.cost.snapshot()["unsettled_requests"] == 1


def test_invalid_citation_never_appears_as_supported_answer(setup_case):
    settings, hit = setup_case

    class Index:
        manifest: ClassVar = {"corpus_hash": "test-corpus"}

        def retrieve(self, *_args, **_kwargs):
            return [hit]

    class Client:
        def generate(self, *_args):
            data = payload()
            data["citations"][0]["quote"] = "This is fabricated."
            return data, json.dumps(data), {}

    result = QAService(settings, index=Index(), client=Client()).ask("ref = b？")
    assert result.status == "error" and not result.citations
    assert "fabricated" in result.raw_response


def test_empty_retrieval_refuses_without_api(setup_case):
    settings, _ = setup_case

    class Index:
        manifest: ClassVar = {"corpus_hash": "test-corpus"}

        def retrieve(self, *_args, **_kwargs):
            return []

    class Client:
        def generate(self, *_args):
            pytest.fail("Empty evidence should not call API")

    result = QAService(settings, index=Index(), client=Client()).ask("文档外问题")
    assert result.status == "refused"
    assert result.answer == "文档中没有相关信息"


def test_refusal_cannot_hide_substantive_answer(setup_case):
    settings, hit = setup_case
    with pytest.raises(CitationError):
        validate_response({"status": "refused", "answer": "虽然没有依据但是答案是42", "citations": []},
                          [hit], settings.source_dir)


def test_id_only_citations_preserve_nbsp_and_code_from_actual_source(setup_case):
    settings, hit = setup_case
    text = "直接用栈数组\u00a0`char buffer[8] = {};`，无需手动释放。"
    path = settings.source_dir / "reference.md"
    path.write_text(text, encoding="utf-8")
    hit.chunk.text = text
    hit.chunk.spans[0].line_start = hit.chunk.spans[0].line_end = 1
    response = {"status": "partial", "answer": "可使用栈数组。[1] lambda 未记载。",
                "citations": [{"chunk_id": "c1"}]}
    _, _, references = validate_response(response, [hit], settings.source_dir)
    assert references[0]["quote"] == path.read_text(encoding="utf-8")
    assert "\u00a0" in references[0]["quote"]
    assert references[0]["excerpt_origin"] == "source_chunk"
