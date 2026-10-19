# C++ 个人笔记问答助手

从本人 C++ 笔记检索证据，用 DeepSeek 生成有出处的回答；资料未覆盖时明确拒答。比较 BM25 与 BGE + Chroma 向量检索，提供本机 Streamlit 界面。

当前为规划与资料审核阶段：9 篇 Markdown 已完整读取并盘点，问答应用、索引和正式 40 条评测尚未实现。

## 文档入口

- [完整项目方案](D:/xjtu_project/qianyan1/.planning/project-plan-cpp-rag.md)
- [四页实验报告结构与录屏安排](D:/xjtu_project/qianyan1/.planning/experiment-report-outline.md)
- [资料审查与待核问题](D:/xjtu_project/qianyan1/docs/material-review.md)
- [20 题评测候选蓝图](D:/xjtu_project/qianyan1/docs/evaluation-blueprint.md)
- [资料清单与字数](D:/xjtu_project/qianyan1/data/corpus_inventory.json)
- [AI 开发错误记录](D:/xjtu_project/qianyan1/docs/ai_error_log.md)

## 已确认设置

- 只用 `问答素材/` 当前原创笔记，保留九篇原文件和图片附件。
- 第一篇图片通过 `data/attachment_map.json` 关联，原文和附件文件名不改动。
- 计划支持 Markdown、TXT；DeepSeek 生成、本机 embedding；总 API 预算不超过20元。
- 20题包含10有答案、5无答案、5跨片段，两方法各跑20题；用户审核金标准并最终核对判分。

## 重新盘点

下面的命令已实际运行，只需 Python 标准库，不读取密钥、不调用外部服务、不修改笔记：

```powershell
python .\scripts\audit_materials.py
```

输出为 `data/corpus_inventory.json`。当前保守正文合计6669汉字，排除代码后单篇最高1458汉字；单份1500字要求仍需明确计数口径，不以多篇总数冒充单篇字数。

## 后续实施

按方案完成资料审核、导入与切片、双检索、生成和引用、界面、评测与交付。安装和启动命令待真实跑通后补充，当前不提供未经实现验证的一键运行命令。

`secrets/`、`.env`、虚拟环境、缓存和索引不提交仓库。`eval/` 的题目、标准答案和结果不能导入知识库。
