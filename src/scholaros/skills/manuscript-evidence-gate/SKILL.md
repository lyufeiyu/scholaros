---
name: manuscript-evidence-gate
description: 当已有论文草稿或交付包需要投稿前核对引文键、结果出处、IEEE 结构及人工确认事项时使用；不代替作者批准或自动投稿。
---

# ScholarOS 稿件证据闸门

在起草/修订后、准备对外分享前使用；这是一项**审查辅助**，不是自动投稿，也不能把摘要或模型生成内容视作已经核验的事实。

1. 读取当前项目的 `paper.md`、`papers.json`、`evidence.json`、`final-review.json` 与 `delivery-manifest.json`；缺失时标注“待检查”，不要补造。不要在未授权的情况下上传本地原始材料或未发表稿件。
2. 建立“正文论断 → `[@cite_key]` → 证据账本条目 → 原文定位”的对应关系。摘要、题录或模型推断只能标为候选；检查 DOI/作者/年份/出版源，发现失配时指出具体位置。
3. 逐项核对结果数字、单位、样本、方法、伦理、基金、署名和图表来源。未有真实结果的稿件，不得将设计图和预期贡献写成实验结论。
4. 若选 IEEE 期刊，检查整篇 `paper.tex` 为英文且使用 `IEEEtran` 类、`abstract`、`IEEEkeywords`、Introduction/Conclusion、`thebibliography`/`bibitem`，核对正文 `cite` 的键是否存在；使用当地 TeX 编译器可用时编译并检查告警。作者、期刊特定规则和编译通过状态分别标注，不将模板等同投稿合规。
5. 输出简短问题清单：严重度、稿件位置、证据 ID/缺失信息、建议修订、需要研究者确认的事项。仅当全部阻塞项已核验，才建议研究者进入投稿前人工审核。

<!-- model-instructions:start -->
稿件核验技能：返修时检查正文引用与参考文献及证据键是否对应，数字和结果是否有真实材料来源。不能声称模型已验证原文、模板已满足目标期刊规则或作者已批准投稿；缺口须保留为待人工核验。
<!-- model-instructions:end -->

设计参考：K-Dense-AI 的 MIT 开源 scientific-agent-skills 中 `scientific-writing`、`literature-review`、`peer-review` 的证据溯源/人工责任边界；这里是 ScholarOS 自有轻量流程，不复制上游内容，也不自动安装 168 个外部技能。
