# Project workbench and delivery

**English** | [简体中文](./zh-CN/workbench.md)

ScholarOS 0.2 separates the user workspace from the seven-stage execution cursor. The workspace has eight areas—configuration, materials, contribution, evidence, draft, figures, review, and delivery—while the recoverable state machine remains compatible with older projects.

## Configure the task

Choose one of six work types: build from materials, rewrite an existing manuscript, integrity/evidence audit, independent review, revise from feedback, or transfer to a new venue. Configuration also records the target scene/name, output language, research boundary, author-voice preference, learning-set sizes, reference target, mechanism-figure policy, requested scope, and formats.

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

## Make bounded decisions

After scoping, ScholarOS offers three contribution directions with explicit trade-offs. Selecting one updates the same project and invalidates evidence and later outputs when necessary. The figure storyboard gives each planned figure a job, evidence status, media placeholder, keep/revise/omit decision, and comment. Missing image or video assets do not block planning; a results figure remains marked as waiting when no result material exists.

## Revise in the same project

Feedback can target the manuscript, figures, or the whole project. ScholarOS snapshots the current version before revision, and every later round starts from the latest completed manuscript. Figure feedback is written to the storyboard as a revision request. With no configured model, unperformed manuscript edits remain `manual_required` instead of being reported as applied. Affected artifacts are regenerated only after the corresponding stage runs.

## Prepare delivery

Use the Web delivery action, the API, or:

```bash
scholaros delivery PROJECT_ID
```

The delivery step can create `paper.docx` and `paper.tex` from the reviewed Markdown, then writes `delivery-manifest.json` and `delivery-package.zip`. The manifest lists requested, available, and missing formats; quality/feedback blockers; file sizes; SHA-256 hashes; sharing boundaries; and the fact that local delivery is not submission or publication. `manuscript` and `submission_package` contain only the requested manuscript formats. `local_delivery` also contains evidence, design, figure-story, and internal-review records; those records can include excerpts from uploaded material and must be reviewed before external sharing. The ZIP excludes original uploads, secrets, the database, history, and local paths. PDF is reported as missing unless a real `paper.pdf` exists; ScholarOS does not disguise another file as PDF.

DOCX and TeX are editable baseline exports, not venue-perfect typesetting. Researchers must add final media, verify the original evidence, apply the current venue template, confirm author/ethics/funding metadata, and make the submission decision.
