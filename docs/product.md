# ScholarOS Product Positioning and Communication Boundaries

**English** | [简体中文](./zh-CN/product.md)

## Public positioning

**ScholarOS — A research assistant workspace for traceable evidence and methods.**

ScholarOS serves researchers, students, and laboratories by organizing search, evidence, method design, drafts, and quality-check records. Researchers retain decision-making authority and final responsibility for questions, evidence, experiments, conclusions, authorship, and submission.

## Principles and responsibility boundaries

- ScholarOS is not an author and does not replace supervisors, collaborators, statistical experts, ethics review, or peer review.
- The system produces candidate plans and reviewable drafts, not papers ready for direct submission.
- Researchers must verify original sources, citations, data, and result interpretation and remain responsible for research ethics and academic integrity.
- AI use must be disclosed according to the applicable institution, funder, journal, or conference rules.
- A quality score only reports predefined checks; it does not establish that conclusions are correct or that a paper meets an acceptance standard.

See [Research assistance and academic integrity](research-integrity.md) for the detailed policy.

## User problems

- Search results are scattered across databases, while deduplication and accessibility remain unclear.
- General LLMs can generate prose but do not reliably show which evidence supports each claim.
- A single long conversation cannot reliably restore stages, compare versions, or reproduce work.
- Multi-Agent demos often stop at role-play without shared contracts or verifiable checks.
- Without experimental data, a system may rewrite expected results as established findings.

## Core proposition

ScholarOS does not promise to complete research or papers automatically. It provides four forms of assistance:

1. Stateful research assistance: every stage can be resumed, inspected, and rerun.
2. Explicit evidence boundaries: citations, source failures, and access permissions remain traceable.
3. No data, no invented results: draft a protocol first; researchers verify results and discussion after data exists.
4. Nine deterministic checks run on both initial and revised drafts, with an explicit reminder that checks cannot replace academic judgment.

## Three-minute demo

1. Enter the idea: “Do multi-agent research assistants improve citation reliability?”
2. Show candidate research questions, hypotheses, and English search terms for user confirmation.
3. Show source status for arXiv, OpenAlex, Crossref, ACM, IEEE, and others; explain single-source degradation.
4. Open `evidence.json` to show citation keys, summaries, intended evidence use, and verification reminders.
5. Open `research-design.json` to show baselines, variables, metrics, and falsification conditions.
6. Download `paper.md` to show a human-review draft and two figure plus two table design notes.
7. Show the nine checks and event history in `final-review.json`; passing does not mean the draft is ready to submit.

## Comparison with common tools

| Category | Typical output | ScholarOS difference |
|---|---|---|
| PDF chat | Single-paper Q&A or summary | Keeps the research question, cross-source evidence, and method draft in one project |
| Deep research | One long report | Adds stage state, artifacts, recovery, and deterministic checks |
| AI writer | Fluent manuscript text | Researcher-led workflow, citation allowlist, responsibility statement, and no invented results |
| Multi-Agent demo | Multi-role conversation | Roles have input/output contracts, shared state, and review relationships |
| Reference manager | Saved and cited papers | Assists with research questions, methods, figure/table plans, and drafts |

## Suitable early use cases

- Graduate students preparing topics, proposals, or registered reports under supervision.
- Laboratories building systematic-review records and evidence ledgers.
- AI/CS teams comparing an idea's novelty, method, and falsifiability.
- Instructors teaching students to separate abstracts, original evidence, inference, and experimental results.

## Claims to avoid

- Do not claim “fully automated scientific discovery” or “guaranteed publication.”
- Do not claim that every PDF is understood; scanned files still need OCR, and central claims still need original-text verification.
- Do not claim IEEE or ACM full-text entitlement; metadata search and subscription access are separate.
- Do not claim an existing RL self-evolution system; current events and scores are only potential future strategy data.
- Do not call a template-generated registered report a completed empirical paper.
- Do not market an “AI author,” automatic ghostwriting, or a quality score equivalent to research quality or acceptance probability.

## Sustainable direction

- **Personal:** local-first, bring-your-own model key, Markdown/LaTeX export.
- **Lab:** team projects, institutional-library connectors, approval flows, private object storage.
- **Research infrastructure:** experiment sandboxes, reproducibility bundles, claim-level evidence labels, evaluation sets.

Long-term improvement should come from auditable feedback: search decisions, evidence verification, check failures, and human revisions. Local research data must not be used for training or product analytics without explicit consent and a completed privacy and ethics review.

## 30-second introduction

> ScholarOS is a local research assistant workspace. It helps researchers organize cross-source search, evidence ledgers, method drafts, research drafts, and quality-check records without replacing original-source reading, experimental work, or academic judgment. Every output requires human verification; researchers retain final responsibility for ethics, data, conclusions, authorship, and submission.
