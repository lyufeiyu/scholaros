# ScholarOS Code Guide

**English** | [简体中文](./zh-CN/code-guide.md)

## Package map

```text
src/scholaros/
├── api.py          # FastAPI, Web entry point, background runs, upload/download
├── cli.py          # run/search/show/serve commands
├── config.py       # environment configuration and project paths
├── domain.py       # project, stage, paper, evidence, and review domain objects
├── ingestion.py    # PDF/TXT/Markdown text ingestion
├── llm.py          # OpenAI-compatible and test models
├── papers.py       # source adapters, concurrent search, deduplication, ranking
├── review.py       # nine deterministic research-draft checks
├── runtime.py      # AgentMessage, ToolRegistry, AgentLoop
├── storage.py      # SQLite state/events and file artifacts
├── terminal.py     # Chinese terminal menu, run wizard, stage progress
├── workflow.py     # seven-stage research-assistance orchestration and recovery
├── writing.py      # planning, evidence, design, drafting, revision assistance
└── static/         # Web HTML, CSS, and interaction scripts
```

## Main call chain

1. `ResearchWorkflow.create_project()` creates a project and its first event.
2. `run()` resumes from the current `stage`; stage start and finish are persisted and emitted as events.
3. `ResearchWriter.scope()` reads the idea and uploaded source excerpts. With source material, it must return a `source_basis` verbatim-verifiable within one excerpt. Before outbound search, terms are checked for length, control characters, URLs/identifiers, and topic relation.
4. A project with source material first enters `search_confirmation_required` during SEARCHING. No external request is made until confirmation. `confirm_search_plan()` continues; `reject_search_plan()` may revise the idea and return to SCOPING. Chinese-to-English mappings and potentially sensitive but legitimate academic terms create human-review warnings rather than static bans.
5. `PaperSearchService.search_many()` searches the first three complementary research phrases, rotates query clusters for minimum coverage, then deduplicates. Once a source returns a rate limit, authentication failure, or temporary error, the same batch does not immediately call it again. A normal workflow stops on zero results; only explicit offline mode may continue.
6. `ResearchWriter` builds the evidence ledger deterministically. Planning, method design, drafting, and revision can call a model through `AgentLoop`; without a model, they degrade honestly.
7. `PaperReviewer` runs nine checks on initial and revised drafts: structure, evidence, citation, method elements, figure design, table design, result provenance, researcher responsibility, and draft depth. Result values must match an indicator and value in uploaded `results` text; citation keys listed only in the references section do not count as in-text citations.
8. `ProjectStore` stores state, events, and stage artifacts. The final `paper.md` is available through the API and CLI.

`paper.md` is a draft artifact for further human verification and revision. It is not a guarantee of correctness, academic compliance, or submission readiness.

## Interaction entry points

- `environment.yml` defines the default `scholaros` Conda environment; Python dependencies remain declared in `pyproject.toml`.
- `./scholaros.sh` uses `conda run --no-capture-output -n scholaros`. It does not activate or contaminate the current shell and preserves terminal interaction. `SCHOLAROS_CONDA_ENV` selects another environment. The launcher checks and runs only; it does not create environments or install packages.
- `python -m scholaros` and bare `scholaros` enter the terminal wizard.
- `./scholaros.sh serve` starts the Web workspace; static assets ship inside the wheel.
- Web project creation persists the project and optional uploads before starting a background run, preventing uploads from replacing a running snapshot.
- Network actions surface API errors in the page. Project state updates through polling. Reruns use `restart=true`, retain user uploads, and remove generated artifacts from the previous run.

See [Environment configuration](environment.md) for installation, update, validation, and troubleshooting.

## Why a small custom runtime

ScholarOS needs a compact but explicit agent harness. `runtime.py` follows four principles:

- `AgentMessage` is the domain source of truth; vendor conversion happens only for a model call.
- Turn, message, and tool events are structured for debugging, persistence, and future strategy learning.
- Required and extra tool arguments are validated, while per-Agent allowlists control capability.
- Steering and follow-up use separate queues for in-run correction and post-run continuation.

LangChain or LangGraph is not included because research-workflow semantics should remain inside this project instead of framework callbacks.

## Adding a paper source

Implement the `PaperSource` protocol:

```python
class MySource:
    name = "my_source"
    available = True

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        ...
```

Then add it to `PaperSearchService.default()`. An adapter must:

- Return the shared `Paper` type and distinguish `landing_url` from an authorized `pdf_url`.
- Use official source fields for `all/title/author/doi/venue`. Unsupported fields must fail explicitly and allow global degradation rather than pretending to be keyword matches.
- Treat source-field queries as candidate retrieval. Push author/affiliation/topic/venue constraints down when supported, paginate bounded author endpoints such as Semantic Scholar, then filter again in `PaperSearchService`. Author parsing distinguishes given/family/suffix and permits only result-side given-name initials. `Paper.author_affiliations` stores affiliations per author, so combined constraints cannot borrow a coauthor's institution. DOI structure is validated, titles use normalized exact equality, venues resolve common aliases and historical names while excluding workshops and neighboring series, English topic queries use meaningful-term coverage, and CJK queries use title/abstract substring matching.
- Record strict-filter exclusions in `SearchResult.filtered_out`. Terminal, CLI, API, and Web interfaces expose `matching_policy` so zero or reduced results can be explained.
- Set `available=False` when authentication is missing instead of failing the global search.
- Never expose keys in exceptions, events, or logs.
- Keep metadata, abstract, open full text, and subscription full text as separate permission layers.

## Replacing the model

Implement `TurnModel.turn(messages, tools) -> ModelTurn`. Vendor-format conversion belongs at the model boundary and must not leak into domain messages. A future Responses API, Claude, or local-model adapter can be added without changing `ResearchWriter`.

## Adding a workflow stage

1. Add an enum member to `domain.Stage`.
2. Add it to `ResearchWorkflow.stage_order`.
3. Implement an idempotent branch in `_run_stages()` and persist its inputs and outputs.
4. Add recovery and failure tests.

An `EXPERIMENTING` stage should only be added after designing a restricted command sandbox, dependency locking, timeouts, data snapshots, and reproducibility logs.

## Data model

- `projects.payload` stores a complete project JSON snapshot, which keeps the MVP easy to evolve.
- `events` is append-only lifecycle history for incremental Web polling and auditing.
- Large text and papers stay out of SQLite and live under `artifacts/<project-id>/`.
- Uploaded files currently persist as extracted text and summary metadata; original binaries are not retained.

A multi-user production deployment should move to PostgreSQL, object storage, project-level access control, and a real task queue. The domain objects and stage protocol can remain.

## Test strategy

- `test_runtime.py`: tool execution, events, error feedback.
- `test_papers.py`: DOI deduplication, source merging, unknown-source degradation, ACM-prefix restrictions.
- `test_review.py`: in-text citations, evidence, method elements, responsibility statements, and result-value provenance.
- `test_workflow.py`: offline end-to-end behavior, artifacts, events, reload, document priority, concurrent-write protection.
- `test_api.py`: static page, project lifecycle, upload, restart parameters, running-state write protection.

Live network APIs are excluded from the stable test suite to avoid external volatility. Use the CLI separately for real-source smoke tests.
