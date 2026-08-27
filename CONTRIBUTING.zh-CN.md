# 贡献指南

[English](./CONTRIBUTING.md) | **简体中文**

感谢你为 ScholarOS 提交改进。项目优先保持核心小而清晰：领域状态机、论文源适配器、证据账本和可审计 Markdown 输出应保持边界明确。

## 开发环境

```bash
conda env create -f environment.yml
conda activate scholaros
python -m pip install -e ".[dev]"
```

## 提交前检查

```bash
./scripts/check.sh
```

新增行为请同时补测试。论文源改动应覆盖：正常响应、来源失败降级、字段严格匹配、链接协议安全和去重；模型/工作流改动应覆盖状态持久化与失败恢复。

涉及写作、审查或界面文案的改动，不得把 ScholarOS 描述为自动作者或投稿质量保证系统；应保留研究者最终责任、AI 使用披露和“输出需人工核验”的边界。

## Pull Request 建议

- 说明用户可观察的变化和兼容性影响。
- 不提交 `.env`、`.scholaros/`、论文文件、API key 或真实用户数据。
- 不把付费墙绕过、浏览器 cookie 或未授权全文抓取加入适配器。
- 让 Ruff、pytest、JavaScript 和 Shell 检查全部通过。
