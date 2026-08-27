# Examples

[English](./README.md) | **简体中文**

这里放可公开分享的最小输入示例，不放真实论文、实验结果、API key 或用户项目数据库。

## 离线演示

```bash
scholaros run "如何评估科研智能体的引用可靠性？" --offline
```

## 带资料的研究项目

```bash
scholaros run "研究局部特征选择与大语言模型结合的方法" \
  --document ./my-paper.pdf \
  --document ./notes.md
```

真实项目请先确认上传材料的授权范围，并在外发检索计划关卡核对实际检索词。
生成的 `paper.md` 只是供研究者审阅的草稿；引用、方法、数据、结果解释和 AI 使用披露必须在分享或投稿前人工核验。
