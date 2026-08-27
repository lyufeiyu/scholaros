# Contributing

**English** | [简体中文](./CONTRIBUTING.zh-CN.md)

Thank you for improving ScholarOS. Keep the core small and explicit: the domain state machine, paper-source adapters, evidence ledger, and auditable Markdown outputs should retain clear boundaries.

## Development environment

```bash
conda env create -f environment.yml
conda activate scholaros
python -m pip install -e ".[dev]"
```

## Checks before submission

```bash
./scripts/check.sh
```

Add tests for new behavior. Paper-source changes should cover successful responses, source-failure degradation, strict field matching, safe link protocols, and deduplication. Model or workflow changes should cover state persistence and failure recovery.

Changes to writing, review logic, or interface copy must not describe ScholarOS as an autonomous author or a guarantee of submission quality. Preserve the boundaries that researchers hold final responsibility, AI use must be disclosed when required, and all outputs require human verification.

## Pull request guidance

- Describe user-visible behavior and compatibility impact.
- Do not commit `.env`, `.scholaros/`, paper files, API keys, or real user data.
- Do not add paywall bypasses, browser-cookie reuse, or unauthorized full-text scraping to adapters.
- Make Ruff, pytest, JavaScript, and Shell checks pass.
