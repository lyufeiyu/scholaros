<p align="right"><a href="https://wubing2023.github.io/PaperSpine/v5/"><img src="website/assets/brand/paperspine-mark.svg" width="24" alt="PaperSpine"></a></p>

<p align="center"><a href="https://wubing2023.github.io/PaperSpine/v5/"><img src="website/assets/brand/paperspine-hero.webp" alt="PaperSpine5 · from zero to a complete paper, text and figures together"></a></p>

# PaperSpine5

[中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Русский](README.ru.md) · [Português](README.pt.md)

[Product page](https://wubing2023.github.io/PaperSpine/v5/en/) · [GitHub Release](https://github.com/WUBING2023/PaperSpine/releases/tag/v0.4.0-alpha.1-dev)

**PaperSpine5: from zero to a complete paper, text and figures together.**

PaperSpine5 is an AI Skill that covers the whole paper workflow. You bring a research direction, existing materials, or experimental data; it searches the literature, organises the argument, builds the outline, writes the full text, produces the scientific figures, verifies citations, works through review and revision, and handles layout, then delivers editable Word / LaTeX sources and a PDF.

Body text, data figures, mechanism diagrams, and method frameworks are all produced inside one task. Start it with `paper-spine`, choose a plan in the web workspace, preview the text and figures, leave revision notes, and download the results. Research materials stay local by default, and every claim, citation, and figure is grounded in real sources and evidence.

## Downloads

- Windows x64 suite: about 26.4 MB.
- Linux glibc x86_64 suite: about 56.2 MB.
- macOS Apple Silicon suite: about 40.5 MB.
- macOS Intel x86_64 suite: about 40.5 MB.
- Standalone `paper-spine` Skill: for an existing host runtime, about 0.72 MB.

Every download is listed in the public release list and verified with SHA-256. Current version: `v0.4.0-alpha.1-dev` prerelease.

## Install and archive V3/V4 discovery conflicts

Windows x64:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex -CleanLegacy
```

macOS / Linux:

```sh
sh ./install.sh --target codex --clean-legacy
```

`-CleanLegacy` archives only known V3/V4 Skill discovery folders. It does not delete paper tasks, host settings, or unknown files. Both platform installers check the byte count, SHA-256, suite integrity, and a startup self-check.

## Check and apply updates

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

If an install is already present, the second command performs a rollback-safe update, keeps your task data, and runs a startup self-check. Automatic update is disabled by default.

## Boundaries

- Self-contained suites are verified on Windows x64, Linux glibc x86_64, macOS arm64, and macOS x86_64. Linux arm64 and musl/Alpine are not claimed.
- macOS packages are not signed or notarized; first launch may require explicit user approval.
- This is an alpha prerelease without an independent cryptographic signature.
- Publishing the product never authorizes manuscript submission, private-data upload, payment, or external contact.
- The support surface is voluntary, unlocks no feature, and reads no payment state.

## Public repository layout

- `dist/codex/skills/paper-spine`, `dist/claude/skills/paper-spine`, and `dist/openclaw/skills/paper-spine`: host projections.
- `dist/claude/commands/paperspine.md`: Claude command entry.
- `install.ps1` and `install.sh`: installation boundaries.
- Key methods/tools: `writing_rationale_matrix`, `citation_support_bank`, `translation_package`, `artifact_check.py`, `reference_inventory.py`, `citation_bank_check.py`, `latex_guard.py`, and `word_guard.py`.

## Development

`src/` holds the Skill source code and `dist/` holds the public host projections. The public repository keeps source and tests, not local tasks, clinical data, caches, or development run logs.

MIT License.
