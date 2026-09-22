# Project workbench and delivery

**English** | [简体中文](./zh-CN/workbench.md)

ScholarOS presents one focused workspace per stage while keeping the recoverable seven-stage execution cursor compatible with older projects. The left sidebar stays project-level; stage navigation, editing, and outputs remain inside the current project page.

## Configure the task

Choose one of six work types: build from materials, rewrite an existing manuscript, integrity/evidence audit, independent review, revise from feedback, or transfer to a new venue. Configuration also records the target scene/name, output language, research boundary, author-voice preference, learning-set sizes, reference target, mechanism-figure policy, and requested scope. Markdown and LaTeX are always retained together.

Configuration is stored in the project JSON. Older projects receive conservative defaults without a database migration. Changing a field invalidates only the earliest affected stage; previous generated work is snapshotted first. Delivery-only changes remove old exports immediately so stale packages are not presented as current.

`materials_only` skips third-party paper search completely. If the project has no local source or result material, the workflow stops with a clear error instead of silently falling back to network search.

CLI example:

```bash
scholaros run "Your research question" \
  --workflow transfer --scene conference --target-name DemoConf \
  --language en --research-mode materials_only \
  --same-field-papers 5 --target-venue-papers 5 \
  --reference-count 30 --format md --format tex
```

## Edit stages with confirmation

Each stage accepts a pending edit. Saving the edit does not alter current results; confirming it snapshots the project, invalidates that stage and its downstream outputs, and then reruns them. Scoping shows one contribution blueprint—research question, core contribution, boundaries, and argument framework—rather than three indistinguishable choices. The evidence stage exposes the learning-plan, paper-list, and evidence-ledger files plus every local material and evidence record. The design stage provides detailed figure and table responsibilities, composition, fields, data requirements, captions, and claim boundaries even when final media is not yet available.

## Review and revise in the same project

The draft stage exposes both Markdown and LaTeX source; the LaTeX export uses the repository's local IEEEtran journal template, with the target journal's latest author instructions still taking precedence. The quality stage lists every finding with severity, the detected problem, and the recommended change. Revision uses the same confirmed stage-edit mechanism instead of a second feedback form. The last stage combines delivery with a project/material overview so scope, files, evidence, figure/table plans, manuscript, and review status can be checked together.

## Prepare delivery

Use the Web delivery action, the API, or:

```bash
scholaros delivery PROJECT_ID
```

The delivery step keeps `paper.md`, creates `paper.tex`, and writes `delivery-manifest.json` plus `delivery-package.zip`. The manifest lists requested and available formats, quality blockers, file sizes, SHA-256 hashes, sharing boundaries, and the fact that local delivery is not submission or publication. `manuscript` and `submission_package` contain only the requested manuscript formats. `local_delivery` also contains evidence, design, figure/table plans, and internal-review records; those records can include excerpts from uploaded material and must be reviewed before external sharing. The ZIP excludes original uploads, secrets, the database, history, and local paths. The project page renders Markdown as a paper-style preview that can be printed to PDF and exposes LaTeX source directly.

Markdown and TeX are editable baseline exports, not venue-perfect typesetting. Researchers must add final media, verify the original evidence, apply the current venue template, confirm author/ethics/funding metadata, and make the submission decision.
