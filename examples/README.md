# Examples

**English** | [简体中文](./README.zh-CN.md)

This directory contains minimal examples safe for public sharing. Do not place real papers, experimental results, API keys, or user project databases here.

## Offline demonstration

```bash
scholaros run "How can citation reliability in research agents be evaluated?" --offline
```

## Research project with source material

```bash
scholaros run "Study methods that combine localized feature selection with large language models" \
  --document ./my-paper.pdf \
  --document ./notes.md
```

For real projects, confirm that uploaded materials may be processed and inspect the actual search terms at the outbound search-plan gate.

Generated `paper.md` is only a draft for researcher review. Citations, methods, data, result interpretation, and AI-use disclosure must be verified by a human before sharing or submission.
