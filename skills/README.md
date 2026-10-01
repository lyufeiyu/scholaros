# 科研辅助 Skills

项目提供六个可按任务选用的技能，描述与触发边界均在各自的 `SKILL.md` 中：

| 技能 | 适用阶段 | 模型角色 |
|---|---|---|
| `research-scoping` | 问题与范围界定 | 研究规划 |
| `literature-discovery` | 检索词、候选论文 | 论文检索规划、论文发现 |
| `evidence-ledger` | 证据边界与论断对齐 | 方法设计、写作、修订 |
| `research-method-design` | 对照、指标、证伪 | 研究方法 |
| `manuscript-writing` | 草稿和返修 | 写作、修订 |
| `manuscript-evidence-gate` | 稿件与交付前检查 | 修订；供 Codex 人工审查 |

模型调用通过 `src/scholaros/prompt_skills.py` 选择当前角色相关的**短约束片段**加入 system prompt，不灌入完整技能文档，也不增加外部检索、付费调用或上传行为。项目根目录 `skills/` 是供 Codex/Agent 阅读的技能源文件；`src/scholaros/skills/` 是 wheel 内运行时副本，测试会检查两者一致。无模型时仍使用原有确定性降级路径。

设计参考：K-Dense-AI/scientific-agent-skills 的 `scientific-writing`、`literature-review`、`peer-review`（MIT）。本项目规则为独立撰写，不整包安装上游依赖；输入未公开材料前仍须核对用户授权与服务边界。
