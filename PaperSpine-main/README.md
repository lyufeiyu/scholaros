<p align="right"><a href="https://wubing2023.github.io/PaperSpine/v5/"><img src="website/assets/brand/paperspine-mark.svg" width="24" alt="PaperSpine"></a></p>

<p align="center"><a href="https://wubing2023.github.io/PaperSpine/v5/"><img src="website/assets/brand/paperspine-hero.webp" alt="PaperSpine5 · 从零开始，快速完成一篇图文完整的论文"></a></p>

# PaperSpine5

[中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Русский](README.ru.md) · [Português](README.pt.md)

[产品页](https://wubing2023.github.io/PaperSpine/v5/) · [GitHub Release](https://github.com/WUBING2023/PaperSpine/releases/tag/v0.4.0-alpha.1-dev)

**PaperSpine5：从零开始，快速完成一篇图文完整的论文。**

PaperSpine5 是一个覆盖论文全流程的 AI Skill。你提供研究方向、已有资料或实验数据，它帮你查文献、梳理论点、搭建大纲、撰写全文、生成科研配图，并完成引用核验、审阅修改和排版，最终交付可编辑的 Word / LaTeX 源文件和 PDF。

从正文到数据图、机制示意图和方法框架图，在同一个任务中完成。通过 `paper-spine` 启动，在网页中选择方案、预览图文、提出修改意见并下载成果。研究材料优先保存在本地，论点、引用和图表均以真实资料与证据为依据。

## 下载

- Windows x64 套件：约 26.4 MB。
- Linux glibc x86_64 套件：约 56.2 MB。
- macOS Apple Silicon 套件：约 40.5 MB。
- macOS Intel x86_64 套件：约 40.5 MB。
- 独立 `paper-spine` Skill：适用于已有宿主运行环境，约 0.72 MB。

所有下载都列在公开发布清单中，并用 SHA-256 校验。完整版本号为 `v0.4.0-alpha.1-dev` 预发布。

## 安装与旧版本迁移

Windows x64：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex -CleanLegacy
```

macOS / Linux：

```sh
sh ./install.sh --target codex --clean-legacy
```

`-CleanLegacy` 只归档已知 V3/V4 Skill 发现目录，不删除论文任务数据、宿主设置或未知文件。两个平台安装器都会校验字节数、SHA-256、套件内部完整性和首次启动自检。

## 检查与应用更新

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 -CheckOnly
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex
```

```sh
# macOS / Linux
sh ./install.sh --check-only
sh ./install.sh --target codex
```

已经安装过时，第二条命令执行可回滚更新，保留任务数据并做一次启动自检。自动更新默认关闭。

## 边界

- 自包含套件已在 Windows x64、Linux glibc x86_64、macOS arm64 和 macOS x86_64 验证；Linux arm64、musl/Alpine 未声明支持。
- macOS 套件尚未签名或公证，首次运行可能需要用户明确允许。
- 当前为 alpha 预发布，无独立密码学签名。
- 产品发布不授权投稿、上传私有材料、付款或外部联系。
- 支持入口是自愿维护支持，不解锁功能，不读取支付状态。

## 公开仓库结构

- `dist/codex/skills/paper-spine`、`dist/claude/skills/paper-spine`、`dist/openclaw/skills/paper-spine`：宿主投影。
- `dist/claude/commands/paperspine.md`：Claude 命令入口。
- `install.ps1`、`install.sh`：安装边界。
- 关键方法/工具：`writing_rationale_matrix`、`citation_support_bank`、`translation_package`、`artifact_check.py`、`reference_inventory.py`、`citation_bank_check.py`、`latex_guard.py`、`word_guard.py`。

## 开发

`src/` 存放 Skill 源代码，`dist/` 是各宿主的公开投影。公开仓库保留源代码和测试，不保留本地任务、临床数据、缓存或开发运行日志。

MIT License。
