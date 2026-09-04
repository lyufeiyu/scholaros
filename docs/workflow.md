# Guided work and recovery

**English** | [简体中文](./zh-CN/workflow.md)

## Choose a mode

Research descriptions accept up to 100,000 characters, including background, proposed methods, and constraints. The Web input shows a character count and preserves oversized pasted text for editing. This is an input limit, not a guarantee that every model can process that much text; the description, other materials, and response must fit the configured model's context window.

The existing automatic flow remains the default. Use `scholaros run "Your research question" --guided`, choose `g` in the terminal, or enable guided mode when creating a Web project to review intermediate results.

Guided projects pause after scoping, evidence synthesis, method design, and the initial draft. The search plan has a separate confirmation before outbound queries. Inspect the stage output, then approve it or rerun that stage. Confirmation records your decision; it is not a certification of scientific validity.

In the terminal wizard, `y` approves, `r` reruns the pending stage, and `q` saves and returns to the menu. You can leave a paused project and resume it later using the commands below. In the Web workspace, the pending output appears above the progress indicator; the draft can be previewed in the artifacts section.

## Continue, rerun, or start over

Replace `PROJECT_ID` with the identifier shown by ScholarOS. Run these commands separately as needed.

| Action | CLI | What is retained |
|---|---|---|
| Inspect state and output paths | `scholaros show PROJECT_ID` | Everything |
| Confirm a guided checkpoint | `scholaros approve PROJECT_ID` | Completed output; continues to the next checkpoint |
| Confirm outbound queries | `scholaros confirm-search PROJECT_ID` | Scope and approved search plan |
| Retry an interrupted stage | `scholaros resume PROJECT_ID` | Completed upstream stages |
| Redo method design onward | `scholaros rerun PROJECT_ID --from-stage designing` | Scope, papers, and evidence |
| Redo drafting onward | `scholaros rerun PROJECT_ID --from-stage drafting` | Scope, papers, evidence, and method design |
| Start over | `scholaros rerun PROJECT_ID --from-stage scoping` | Uploaded materials and historical snapshots |
| List old versions | `scholaros history PROJECT_ID` | Read-only operation |

Valid stages are `scoping`, `searching`, `synthesizing`, `designing`, `drafting`, `reviewing`, and `revising`. A rerun cannot skip missing upstream results or pending human confirmation. Downstream outputs and approvals are invalidated; upstream results remain unchanged. A failed stage may repeat its model call—recovery is stage-level, not token-level.

If the final checks require attention, `resume` does not silently start a new paper. Inspect the findings and choose which stage to redo. A live run holds a project lock and rejects competing runs. After a process exits, `resume` can recover its stale running state once that lock is released. Restarting the server alone does not resume projects.

Adding a new reference or changing a document's role returns the project to scoping because the research context may change. Adding result material after synthesis invalidates synthesis and later stages but retains scope and search. Resume the indicated stage after uploading.

## Historical snapshots

Before an explicit rerun, a restart, a search-plan rejection, or a material change to a scoped project, ScholarOS saves its current state and generated artifacts under:

```text
.scholaros/artifacts/PROJECT_ID/history/REVISION_ID/
```

`manifest.json` records the reason, time, project state, and SHA-256 hashes of copied artifacts. Extracted source texts remain in the project's artifact directory; snapshots do not duplicate them or retain original uploaded binaries. If the snapshot fails, the rerun does not clear existing outputs. Generated files are replaced atomically to avoid half-written drafts.

Use **Partial rerun and history** in the Web workspace to browse and download older versions. There is no automatic rollback or retention limit yet; repeated reruns consume additional disk space. Deleting a project also permanently deletes its history. These snapshots are not a substitute for backups, and may contain private research material—do not commit or share them without review.

## API and compatibility

`POST /api/projects` accepts `guided: true`; omitting it preserves the previous behavior. Project actions are `POST /api/projects/{id}/resume`, `/approve`, and `/rerun?stage=designing`. History is available through `GET /api/projects/{id}/history` and `/history/{revision}/{name}`. The existing `/run?restart=true` endpoint remains available and now snapshots the old version first.

New projects store checkpoint metadata in the existing project JSON; no database migration is needed. Older projects without that metadata remain automatic projects. New explicit offline projects stay offline when reopened, resumed, or rerun, including through the Web API. For older offline projects that did not record this flag, create a new offline demo instead of assuming their mode can be inferred.

This is a local, stage-level workflow. It does not yet provide live conversational edits, section-level generation, automatic historical rollback, durable background scheduling, or multi-user approval permissions. Models and external search services can still fail; these controls preserve progress rather than guarantee completion or research quality.
