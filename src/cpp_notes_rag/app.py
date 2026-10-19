"""Local Streamlit interface. One submit corresponds to one independent question."""

import json
from pathlib import Path

import streamlit as st

from cpp_notes_rag.config import Settings
from cpp_notes_rag.cost_tracker import CostTracker
from cpp_notes_rag.service import QAService

st.set_page_config(page_title="CppNotes · 笔记问答", page_icon="📚", layout="wide",
                   initial_sidebar_state="expanded")


@st.cache_resource
def get_service():
    return QAService(Settings.load())


def show_sources(answer):
    if not answer.citations:
        return
    st.divider()
    st.markdown("#### 引用原文")
    for i, citation in enumerate(answer.citations, 1):
        st.markdown(f"**[{i}] {citation['source_file']}** · {citation['heading']} · "
                    f"L{citation['line_start']}–L{citation['line_end']}")
        st.code(citation["quote"], language="text", wrap_lines=True)
        pending = [f for f in citation.get("review_flags", []) if "资料待核" in f]
        if pending:
            st.caption("来源含待核表述：" + "、".join(pending))


def main():
    service = get_service()
    settings = service.settings
    manifest_file = settings.storage_dir / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
    with st.sidebar:
        st.title("CppNotes")
        st.caption("我的 C++ 学习笔记")
        method_label = st.radio("检索方式", ["BM25 · 关键词", "向量 · 语义检索"])
        method = "bm25" if method_label.startswith("BM25") else "vector"
        st.caption("两种方法共用同一批笔记和 Top-3 设置。")
        if manifest:
            st.success(f"已建库 · {manifest['document_count']} 篇 / {manifest['chunk_count']} 个片段")
        else:
            st.warning("请先运行建库命令。")
            st.code("python scripts/build_indexes.py", language="powershell")
        if st.button("加载 / 预热所选检索", use_container_width=True):
            try:
                with st.spinner("加载本机索引与模型…"):
                    service.warmup(method)
                st.success("已就绪，可以提问。")
            except Exception:  # noqa: BLE001 -- UI boundary; never expose request secrets or internals.
                st.error("加载未完成。请检查是否已建库、模型是否已下载；命令行可查看诊断。")
        st.divider()
        try:
            snapshot = CostTracker(settings).snapshot()
            st.metric("累计费用上界", f"¥ {snapshot['upper_estimate_cny']:.4f}",
                      help=snapshot["note"])
            st.caption(f"项目上限 ¥20 · {snapshot['requests']} 次 API 请求")
            if snapshot["unsettled_requests"]:
                st.caption(f"{snapshot['unsettled_requests']} 次用量待确认，已保留最大费用预留。")
        except Exception:  # noqa: BLE001 -- An invalid ledger must fail closed in the UI.
            st.error("费用账本需要核查，已停止新增 API 调用。")
        st.caption("问答仅使用笔记文字与代码。图片作为附件展示。")

    st.title("从笔记找到答案")
    st.write("提问、查看证据，再核对原文。资料未记载时会明确拒答。")
    with st.expander("试一试这些问题"):
        samples = ["笔记中 #pragma once 和 #ifndef / #define / #endif 有什么作用？",
                   "class 和 struct 默认访问权限怎样，private 与 protected 又有什么区别？",
                   "笔记记录了 std::string 的 SSO 精确阈值吗？"]
        for sample in samples:
            if st.button(sample, use_container_width=True):
                st.session_state["question_input"] = sample
    with st.form("ask_form"):
        question = st.text_area("你的问题", key="question_input", height=105, max_chars=1500,
                                placeholder="例如：ref = b 与 ptr = &b 在笔记里分别表示什么？")
        submitted = st.form_submit_button("检索并回答", type="primary", disabled=not manifest)
    if submitted:
        if not question.strip():
            st.warning("请先输入问题。")
        else:
            with st.spinner("正在检索笔记并核对回答依据…"):
                st.session_state["last_answer"] = service.ask(question.strip(), method)
            st.rerun()  # Refresh the sidebar ledger; the form does not resubmit on rerun.

    answer_tab, evidence_tab, corpus_tab = st.tabs(["回答与出处", "检索证据", "资料概览"])
    answer = st.session_state.get("last_answer")
    with answer_tab:
        if answer is None:
            st.info("输入问题后，答案和可核查的引用会显示在这里。")
        else:
            st.caption(f"本次问题：{answer.question} · 实际方法：{answer.method}")
            labels = {"supported": "有依据的回答", "partial": "部分内容有依据", "uncertain": "资料待核",
                      "refused": "资料无相关信息", "error": "请求未完成"}
            st.markdown(f"#### {labels.get(answer.status, answer.status)}")
            if answer.status == "error":
                st.error(answer.answer)
            else:
                st.markdown(answer.answer)
            show_sources(answer)
            columns = st.columns(3)
            columns[0].metric("完整响应", f"{answer.latency_seconds:.2f} s")
            columns[1].metric("检索", f"{answer.retrieval_seconds:.2f} s")
            columns[2].metric("生成与验证", f"{answer.generation_seconds:.2f} s")
            st.download_button("下载本次记录 JSON", json.dumps(answer.to_dict(), ensure_ascii=False, indent=2),
                               file_name=f"answer_{answer.run_id[:8]}.json", mime="application/json")
    with evidence_tab:
        if answer is None:
            st.caption("提问后展示实际 Top-3；分数表示检索匹配程度，仍需核对能否支持答案。")
        elif not answer.hits:
            st.info("本次未检索到可用片段。")
        else:
            for hit in answer.hits:
                span = hit.chunk.spans[0]
                with st.expander(f"#{hit.rank} · {span.source_file} · L{span.line_start}–L{span.line_end} "
                                 f"· 分数 {hit.score:.4f}", expanded=hit.rank == 1):
                    st.caption(span.heading + " · " + hit.chunk.chunk_id)
                    st.code(hit.chunk.text, language="markdown", wrap_lines=True)
                    if hit.chunk.review_flags:
                        st.caption("；".join(hit.chunk.review_flags))
    with corpus_tab:
        for path in sorted(settings.source_dir.glob("*")):
            if path.suffix.lower() in {".md", ".txt"}:
                st.write(path.name)
        st.caption("当前笔记中的技术疑点保留待核标记，不自动改写你的原文。")
        image = settings.source_dir / "1图片引用.png"
        if image.exists():
            with st.expander("第一篇的图片附件"):
                st.image(str(image), caption="已关联第一篇的原图片引用；图意未纳入文本索引。")
        with st.expander("导入新的 Markdown / TXT 笔记"):
            upload = st.file_uploader("UTF-8 文件，单份最多 2 MB", type=["md", "txt"])
            if st.button("保存为资料文件", disabled=upload is None):
                name = Path(upload.name).name
                destination = settings.source_dir / name
                data = upload.getvalue()
                try:
                    if len(data) > 2_000_000:
                        raise ValueError("文件超过 2 MB。")
                    if destination.exists():
                        raise ValueError("已有同名原文件，请使用其他文件名；系统保留原笔记。")
                    data.decode("utf-8-sig")
                    destination.write_bytes(data)
                    st.success("文件已保存。请在终端重新建库后继续问答。")
                except (ValueError, UnicodeError):
                    st.error("导入未完成：请使用 UTF-8、最多 2 MB 的文件，并避免与已有文件重名。")


if __name__ == "__main__":
    main()
