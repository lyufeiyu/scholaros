"""代码分批分析的覆盖、来源与模型输出质量边界。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from scholaros.api import create_app
from scholaros.code_research import MAX_CODE_BYTES, inspect_run_code, inspect_run_readme
from scholaros.local_paper import LocalPaperService
from scholaros.locals import LocalRunService
from scholaros.papers import PaperSearchService
from scholaros.runtime import ModelTurn
from scholaros.workflow import ResearchWorkflow

PLAN = {
    "title": "A Reproducible Study of Activity Phenotype Modeling",
    "direction": "Evaluate activity phenotype algorithms using matched conditions.",
    "method": "The implementation extracts temporal features and fits candidate models.",
    "proposed_method": "Temporal feature selection",
    "datasets": ["Dataset A"], "baselines": ["Baseline A"],
    "metrics": ["Accuracy", "Selected features"],
    "limitations": ["Independent validation remains pending."],
}
SMALL_IDEA = "研究目标：梳理源代码的入口、输入与输出，检查研究问题与技术路线。" * 20
LARGE_IDEA = "\n\n".join(
    f"{title}：依据 README.md:L1 与 README.md:L2 梳理入口、模块调用和算法实现。"
    + f"围绕{title}的证据约束逐项核对。"
    + "明确输入、预处理、目标函数、分组与选择逻辑、输出和资源限制；区分已实现的函数与仅待验证的推断。" * 6
    for title in ("研究对象与问题", "已实现的技术路径", "算法机制与模块协作", "可证伪假设", "数据与对照实验", "局限与后续验证")
)


class RecordingModel:
    def __init__(self, *, idea=SMALL_IDEA, synthesis=None, retry=None):
        self.calls = []
        self.idea = idea
        self.synthesis = synthesis
        self.retry = retry
        self.synthesis_calls = 0

    async def turn(self, messages, tools):
        self.calls.append(messages)
        assert tools == []
        system = messages[0].content
        if "只读源码分析员" in system or "上次模块报告" in system:
            visible = json.loads(messages[1].content)["visible_ranges"][0]
            path, line = visible["path"], visible["start"]
            return ModelTurn(content=(f"模块报告：`{path}:L{line}` 是当前片段可定位的入口或处理逻辑。"
                                      "依据代码中的函数调用与输入输出核对处理顺序，不能把模型假设当作已运行结论。" * 9))
        if "代码模块报告归纳" in system:
            return ModelTurn(content="模块归纳：按入口、算法、结果的顺序建立技术路线并保留代码行号。" * 10)
        self.synthesis_calls += 1
        if self.synthesis_calls == 1 and self.synthesis is not None:
            return ModelTurn(content=self.synthesis)
        if self.synthesis_calls > 1 and self.retry is not None:
            return ModelTurn(content=self.retry)
        return ModelTurn(content=json.dumps({**PLAN, "research_idea": self.idea}, ensure_ascii=False))


def prepared_run(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    record = runs.create(entries=[
        ("README.md", b"# Study README\n\nThis README describes the research problem, pipeline, inputs, metrics, and limitations.\n"),
        ("main.py", b"from pkg.engine import compute\nprint(compute())\n"),
        ("pkg/engine.py", b"def compute():\n    return 1\n"),
        ("datasets/private.csv", b"participant,age\n1,50\n"),
    ], note="study")
    return runs, record["id"]


def test_inspection_reads_entire_long_source_in_order_and_skips_dataset(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    source = ("from pkg.engine import compute\n" + "# details\n" * 6000 + "print('TAIL_MARKER')\n")
    run = runs.create(entries=[("main.py", source.encode()), ("pkg/engine.py", b"def compute():\n    return 1\n"),
                              ("datasets/private.csv", b"participant,age\n1,50\n")], note="long")
    inspection = inspect_run_code(runs, run["id"])
    assert inspection["bytes"] == len(source.encode()) + len(b"def compute():\n    return 1\n")
    assert len(inspection["chunks"]) > 2
    assert "TAIL_MARKER" in "".join(inspection["chunks"])
    assert "participant,age" not in "".join(inspection["chunks"])
    assert inspection["files"][:2] == ["main.py", "pkg/engine.py"]
    assert inspection["outlines"][1]["symbols"][0]["name"] == "compute"
    assert max(map(len, inspection["chunks"])) <= 22_000
    assert inspection["ignored_data_files"] == 1


@pytest.mark.asyncio
async def test_plan_batches_code_and_keeps_detailed_evidence(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    source = "# Research README\n" + "## Implementation detail\n" * 1100
    run = runs.create(entries=[("README.md", source.encode()), ("main.py", b"print(1)\n")], note="long")
    model = RecordingModel(idea=LARGE_IDEA)
    plan = await LocalPaperService(runs, model).plan(run["id"], note="Focus on mechanisms")
    assert plan["idea_source"] == "model"
    assert len(plan["research_idea"]) >= 1600
    assert plan["partial_files_count"] == 0
    assert plan["module_report_count"] == 1
    assert len(plan["module_reports"]) == plan["module_report_count"]
    assert plan["read_files"] == ["README.md"]
    assert LocalPaperService(runs, model).status(run["id"])["stage"] == "complete"
    code_calls = [call for call in model.calls if "只读源码分析员" in call[0].content]
    assert len(code_calls) == plan["module_report_count"]
    assert all(len(call[0].content) + len(call[1].content) + 300 <= 45_000 for call in code_calls)
    assert "TAIL" not in json.dumps(model.calls[-1][1].content)
    # 再次合成会复用相同源码摘要，而不会再为每个片段重复计费。
    await LocalPaperService(runs, model).plan(run["id"], note="Emphasize validation")
    assert len([call for call in model.calls if "只读源码分析员" in call[0].content]) == len(code_calls)


@pytest.mark.asyncio
async def test_weak_long_code_idea_is_rejected_not_replaced_by_short_template(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("README.md", b"# Research README\n" * 1500)], note="long")
    model = RecordingModel(idea=SMALL_IDEA)
    service = LocalPaperService(runs, model)
    plan = await service.plan(run["id"], note="")
    assert plan["idea_source"] == "module_reports_fallback"
    assert "研究对象与待解决问题" in plan["research_idea"]
    assert "README.md:L1" in plan["research_idea"]
    assert service.status(run["id"])["stage"] == "complete"


def test_source_budget_does_not_silently_truncate(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("main.py", b"x" * (MAX_CODE_BYTES + 1))], note="oversize")
    with pytest.raises(ValueError, match="不能保证逐行完整分析"):
        inspect_run_code(runs, run["id"])


def test_api_requires_consent_model_and_no_direct_paper_draft(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    app = create_app(flow)
    run_id = app.state.runs.create(entries=[("main.py", b"print(1)\n")], note="test")["id"]
    with TestClient(app) as client:
        assert client.post(f"/api/runs/{run_id}/paper/plan", json={"allow_model": False}).status_code == 422
        assert client.post(f"/api/runs/{run_id}/paper/plan", json={"allow_model": True}).status_code == 409
        assert client.post(f"/api/runs/{run_id}/paper/draft", json={"plan": PLAN}).status_code == 404
        assert client.get(f"/api/runs/{run_id}/paper/status").json()["stage"] == "idle"


def test_api_plan_links_confirmed_idea_to_project(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    flow.writer.model = RecordingModel()
    app = create_app(flow)
    run_id = app.state.runs.create(entries=[("README.md", b"# Study README\n")], note="study")["id"]
    with TestClient(app) as client:
        assert client.post("/api/projects", json={"idea": "a research idea", "source_run_id": run_id,
                                                  "run_now": False}).status_code == 422
        response = client.post(f"/api/runs/{run_id}/paper/plan", json={"allow_model": True})
        assert response.status_code == 200, response.text
        idea = response.json()["research_idea"]
        assert response.json()["total_files"] == 1
        edited = idea + "\n由研究者检查后再确定具体纳入标准。"
        created = client.post("/api/projects", json={"idea": edited, "source_run_id": run_id,
                                                    "run_now": False, "guided": True,
                                                    "configuration": {"output_language": "en", "scene": "journal",
                                                                      "latex_template": "ieee_journal"}})
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["idea"] == edited
        assert project["state"]["source_local_run_id"] == run_id
        assert app.state.runs.get(run_id)["project_id"] == project["id"]
        assert client.post("/api/projects", json={"idea": edited, "source_run_id": run_id,
                                                  "run_now": False}).status_code == 409


@pytest.mark.asyncio
async def test_synthesis_json_repair_once_preserves_error(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    bad = RecordingModel(synthesis='{"title":"Only title"}', retry='{"title":"Still incomplete"}')
    fallback = await LocalPaperService(runs, bad).plan(run_id, note="")
    assert fallback["idea_source"] == "module_reports_fallback"
    assert "README 分析报告" in fallback["research_idea"]
    assert bad.synthesis_calls == 3
    good = RecordingModel(synthesis='{"title":"Only title"}', retry=json.dumps({**PLAN, "research_idea": SMALL_IDEA}, ensure_ascii=False))
    plan = await LocalPaperService(runs, good).plan(run_id, note="")
    assert plan["title"] == PLAN["title"]
    assert good.synthesis_calls == 2


@pytest.mark.asyncio
async def test_synthesis_runtime_failure_still_completes_from_cached_reports(tmp_path):
    runs, run_id = prepared_run(tmp_path)

    class FailingSynthesisModel(RecordingModel):
        async def turn(self, messages, tools):
            if "只读源码分析员" in messages[0].content:
                return await super().turn(messages, tools)
            raise RuntimeError("model service unavailable")

    plan = await LocalPaperService(runs, FailingSynthesisModel()).plan(run_id, note="focus on robustness")
    assert plan["idea_source"] == "module_reports_fallback"
    assert plan["code_digest"]
    assert "focus on robustness" in plan["research_idea"]
    assert LocalPaperService(runs, None).status(run_id)["stage"] == "complete"


@pytest.mark.asyncio
async def test_retry_after_completed_module_failure_skips_model_synthesis(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    model = RecordingModel()
    service = LocalPaperService(runs, model)
    first = await service.plan(run_id, note="")
    assert first["idea_source"] == "model"
    scope = service.scope(run_id)
    service._progress(run_id, "failed", scope["chunks"], scope["chunks"], "previous synthesis failed")
    calls = len(model.calls)
    second = await service.plan(run_id, note="retry locally")
    assert len(model.calls) == calls
    assert second["idea_source"] == "module_reports_fallback"
    assert "retry locally" in second["research_idea"]
    assert service.status(run_id)["stage"] == "complete"


@pytest.mark.asyncio
async def test_failed_status_does_not_skip_synthesis_after_source_change(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    model = RecordingModel()
    service = LocalPaperService(runs, model)
    await service.plan(run_id, note="")
    scope = service.scope(run_id)
    service._progress(run_id, "failed", scope["chunks"], scope["chunks"], "previous synthesis failed")
    (runs.root / run_id / "workspace" / "README.md").write_text("# Changed README\n")
    calls = len(model.calls)
    plan = await service.plan(run_id, note="new source")
    assert len(model.calls) > calls
    assert plan["idea_source"] == "model"


@pytest.mark.asyncio
async def test_plain_text_synthesis_fallback_keeps_detailed_idea(tmp_path):
    runs, run_id = prepared_run(tmp_path)

    class PlainFallbackModel(RecordingModel):
        def __init__(self):
            super().__init__(synthesis='{"title":"Only title"}', retry='not json')
            self.fallback_idea = LARGE_IDEA

        async def turn(self, messages, tools):
            system = messages[0].content
            if "只输出一篇完整的中文研究构想正文" in system:
                self.calls.append(messages)
                return ModelTurn(content=self.fallback_idea)
            return await super().turn(messages, tools)

    model = PlainFallbackModel()
    plan = await LocalPaperService(runs, model).plan(run_id, note="")
    assert plan["idea_source"] == "model_text_fallback"
    assert plan["research_idea"] == LARGE_IDEA
    assert plan["title"].startswith("A Source-Grounded")
    assert model.synthesis_calls == 2


def test_model_object_prefers_complete_json_after_example_object():
    example = '{"title":"Example schema"}'
    final = json.dumps({**PLAN, "research_idea": SMALL_IDEA}, ensure_ascii=False)
    value = LocalPaperService._model_object(f"示例：{example}\n最终：{final}")
    assert value["direction"] == PLAN["direction"]
    assert value["research_idea"] == SMALL_IDEA


def test_plain_fallback_rejects_unverified_result_claims(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    analysis = inspect_run_code(runs, run_id)
    idea = "\n\n".join([
        "研究对象与待解决问题：依据 main.py:L1 明确待解决问题。" + "对象边界与输入输出需要逐项核对。" * 35,
        "已实现的端到端技术路径：依据 pkg/engine.py:L1 追踪调用。" + "处理顺序和控制流需要逐项核对。" * 35,
        "关键算法机制与模块协作：区分函数输入输出与模块边界。" + "算法机制和资源约束需要逐项核对。" * 35,
        "可证伪假设与研究贡献：提出待验证的机制假设。" + "研究贡献和失败条件需要逐项核对。" * 35,
        "数据与对照实验设计：列出待确认的数据、基线和指标。" + "纳入排除标准和比较预算需要逐项核对。" * 35,
        "局限、证据缺口与下一步验证：代码已经证明准确率达到99%。" + "证据边界和后续验证需要逐项核对。" * 35,
    ])
    with pytest.raises(ValueError, match="结果性结论"):
        LocalPaperService._validated_idea({"research_idea": idea}, analysis, require_detailed=True)


def test_command_and_metric_context_are_redacted():
    command = "python main.py --api-key=SECRET123 OPENAI_TOKEN TOPSECRET Bearer abc123"
    redacted = LocalPaperService._redact_command(command)
    assert "SECRET123" not in redacted
    assert "TOPSECRET" not in redacted
    assert "abc123" not in redacted
    context = LocalPaperService._metric_context({
        "available": True,
        "summary": [{"column": "accuracy", "count": 3, "mean": 0.99, "last": 0.98}],
    })
    assert context == {"available": True, "columns": ["accuracy"], "row_count": 3}


@pytest.mark.asyncio
async def test_fenced_synthesis_json_is_accepted(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    fenced = RecordingModel(synthesis="```json\n" + json.dumps({**PLAN, "research_idea": SMALL_IDEA}, ensure_ascii=False) + "\n```")
    plan = await LocalPaperService(runs, fenced).plan(run_id, note="")
    assert plan["title"] == PLAN["title"]
    assert fenced.synthesis_calls == 1


@pytest.mark.asyncio
async def test_missing_code_anchor_retries_module_report_once(tmp_path):
    runs, run_id = prepared_run(tmp_path)

    class UnanchoredFirstModel(RecordingModel):
        def __init__(self):
            super().__init__()
            self.first = True

        async def turn(self, messages, tools):
            if "只读源码分析员" in messages[0].content and self.first:
                self.first = False
                self.calls.append(messages)
                return ModelTurn(content="模块技术报告：只写了未经代码证据定位的笼统描述。" * 6)
            return await super().turn(messages, tools)

    model = UnanchoredFirstModel()
    plan = await LocalPaperService(runs, model).plan(run_id, note="")
    assert plan["module_report_count"] == 1
    assert len(model.calls) == 3
    assert "上次 README 分析报告不完整" in model.calls[1][0].content


@pytest.mark.asyncio
async def test_oversized_source_blocks_before_model_call(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("README.md", b"x" * (MAX_CODE_BYTES + 1))], note="oversize")
    model = RecordingModel()
    service = LocalPaperService(runs, model)
    with pytest.raises(ValueError, match="不能保证逐行完整分析"):
        await service.plan(run["id"], note="")
    assert not model.calls
    assert service.status(run["id"])["stage"] == "failed"


@pytest.mark.asyncio
async def test_report_cannot_cite_file_line_outside_current_chunk(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("README.md", b"# body\n" * 3000)], note="large")

    class OutOfRangeModel(RecordingModel):
        async def turn(self, messages, tools):
            if "源码分析员" in messages[0].content or "上次模块报告" in messages[0].content:
                self.calls.append(messages)
                return ModelTurn(content="模块报告：`README.md:L5000` 的内容尚未在当前片段展示，"
                                         "却被错误地当作这段代码的依据；此报告不应通过代码证据校验。" * 5)
            return await super().turn(messages, tools)

    service = LocalPaperService(runs, OutOfRangeModel())
    plan = await service.plan(run["id"], note="")
    assert plan["unanchored_chunks"] == [1]
    assert plan["module_reports"][0]["verified"] is False


def test_many_small_modules_keep_complete_model_request_bounded(tmp_path):
    import scholaros.local_paper as module

    runs = LocalRunService(tmp_path / "runs")
    entries = [("main.py", b"from pkg.module_0 import helper\n")]
    entries += [(f"pkg/module_{i}.py", (f"def helper():\n    return {i}\n" * 7).encode()) for i in range(170)]
    run = runs.create(entries=entries, note="many")
    analysis = inspect_run_code(runs, run["id"])
    for index, chunk in enumerate(analysis["chunks"]):
        ranges = analysis["ranges"][index]
        names = {entry["path"] for entry in ranges}
        outlines = [entry for entry in analysis["outlines"] if entry["path"] in names]
        payload = LocalPaperService._module_payload(chunk, outlines, ranges, index, len(analysis["chunks"]))
        assert len(json.dumps(payload, ensure_ascii=False)) <= module.MAX_MODULE_REQUEST_CHARS


@pytest.mark.asyncio
async def test_code_analysis_cache_rejects_changed_skill_and_invalid_report(tmp_path, monkeypatch):
    import scholaros.local_paper as module

    runs, run_id = prepared_run(tmp_path)
    model = RecordingModel()
    service = LocalPaperService(runs, model)
    await service.plan(run_id, note="")
    calls = len([call for call in model.calls if "源码分析员" in call[0].content])
    await service.plan(run_id, note="again")
    assert len([call for call in model.calls if "源码分析员" in call[0].content]) == calls
    original = module.skill_instructions
    monkeypatch.setattr(module, "skill_instructions", lambda role: original(role) + "新增代码分析规则")
    await service.plan(run_id, note="new rule")
    assert len([call for call in model.calls if "源码分析员" in call[0].content]) == calls + 1
    path = runs.root / run_id / "paper" / "code-analysis.json"
    cache = json.loads(path.read_text())
    cache["reports"][0] = "无依据的缓存内容"
    path.write_text(json.dumps(cache))
    await service.plan(run_id, note="repair cache")
    assert len([call for call in model.calls if "源码分析员" in call[0].content]) == calls + 2


def test_repeated_research_paragraphs_fail_detailed_idea_gate():
    analysis = {"bytes": 20_000, "outlines": [
        {"path": "main.py", "lines": 20}, {"path": "core.py", "lines": 20},
    ]}
    repeated = "依据 main.py:L1 与 core.py:L2，我们需要进一步研究并核对现有代码。" * 8
    idea = "\n\n".join(f"{name}：{repeated}" for name in
                      ("研究对象", "技术路径", "算法机制", "假设", "实验设计", "局限"))
    with pytest.raises(ValueError, match="重复段落"):
        LocalPaperService._validated_idea({"research_idea": idea}, analysis)


def test_project_transfer_rejects_changed_code_after_analysis(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    flow.writer.model = RecordingModel()
    app = create_app(flow)
    run_id = app.state.runs.create(entries=[("README.md", b"# Original README\n")], note="study")["id"]
    with TestClient(app) as client:
        plan = client.post(f"/api/runs/{run_id}/paper/plan", json={"allow_model": True})
        assert plan.status_code == 200, plan.text
        run_dir = app.state.runs.root / run_id
        (run_dir / "workspace" / "README.md").write_text("# Changed README\n")
        response = client.post("/api/projects", json={
            "idea": plan.json()["research_idea"], "source_run_id": run_id, "run_now": False,
        })
        assert response.status_code == 409
        assert "源码已变化" in response.json()["detail"]
        assert not flow.store.list_projects()


def test_topics_cannot_be_stacked_in_one_paragraph():
    analysis = {"bytes": 20_000, "outlines": [
        {"path": "main.py", "lines": 20}, {"path": "core.py", "lines": 20},
    ]}
    first = "研究对象、研究问题、技术路径、处理流程、算法机制、核心算法、假设、贡献、实验、验证、局限、风险。" \
            + "源码位置 main.py:L1 与 core.py:L2。" + "需要解释已有的代码与评价边界。" * 15
    others = [f"第 {index} 段不涉及任何研究主题，但长度足够。" + "背景背景背景背景。" * 45 for index in range(5)]
    idea = "\n\n".join([first, *others])
    with pytest.raises(ValueError, match="独立成段"):
        LocalPaperService._validated_idea({"research_idea": idea}, analysis)


def test_module_anchors_accept_common_line_styles_only_inside_visible_ranges():
    outlines = [{"path": "pkg/experiment.py", "lines": 500}]
    ranges = [{"path": "pkg/experiment.py", "start": 20, "end": 45}]
    text = "pkg/experiment.py:21；pkg/experiment.py 第 22 行；pkg/experiment.py#L23；pkg/experiment.py:L50"
    assert LocalPaperService._valid_anchors(text, outlines, ranges) == {
        ("pkg/experiment.py", 21), ("pkg/experiment.py", 22), ("pkg/experiment.py", 23),
    }


def test_readme_analysis_uses_one_root_file_and_one_prompt(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[
        ("README.md", ("# README\n" * 2500).encode()),
        ("nested/README.md", b"must not be selected"),
        ("src/main.py", b"print('must not be read')\n"),
    ], note="large")
    analysis = inspect_run_readme(runs, run["id"])
    assert analysis["files"] == ["README.md"]
    assert len(analysis["chunks"]) == 1
    assert analysis["ignored_data_files"] == 2
    assert "must not be selected" not in analysis["chunks"][0]


@pytest.mark.asyncio
async def test_failed_reanalysis_hides_previous_plan_and_records_safe_reason(tmp_path):
    runs, run_id = prepared_run(tmp_path)
    service = LocalPaperService(runs, RecordingModel())
    await service.plan(run_id, note="")
    assert service.get(run_id)["plan"] is not None

    class EmptyModel(RecordingModel):
        async def turn(self, messages, tools):
            if "源码分析员" in messages[0].content or "上次模块报告" in messages[0].content:
                return ModelTurn(content="")
            return await super().turn(messages, tools)

    failed = LocalPaperService(runs, EmptyModel())
    # 模拟源码变动使缓存失效，而无需运行用户代码或真实模型。
    (runs.root / run_id / "workspace" / "README.md").write_text("# Changed README\n")
    with pytest.raises(ValueError, match="报告长度不合适"):
        await failed.plan(run_id, note="")
    status = failed.status(run_id)
    assert status["stage"] == "failed"
    assert "报告长度不合适" in status["error"]
    assert failed.get(run_id)["plan"] is None
    assert (runs.root / run_id / "paper" / "plan.json").is_file()


def test_api_422_does_not_serve_old_plan_as_new_result(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    flow.writer.model = RecordingModel()
    app = create_app(flow)
    run_id = app.state.runs.create(entries=[("README.md", b"# Old README\n")], note="study")["id"]
    with TestClient(app) as client:
        route = f"/api/runs/{run_id}/paper"
        assert client.post(f"{route}/plan", json={"allow_model": True}).status_code == 200
        (app.state.runs.root / run_id / "workspace" / "README.md").write_text("# New README\n")

        class EmptyModel(RecordingModel):
            async def turn(self, messages, tools):
                return ModelTurn(content="")

        app.state.local_papers.model = EmptyModel()
        failed = client.post(f"{route}/plan", json={"allow_model": True})
        assert failed.status_code == 422
        assert "第 1 个 README 片段报告长度不合适" in failed.json()["detail"]
        assert client.get(f"{route}/status").json()["stage"] == "failed"
        assert client.get(route).json()["plan"] is None


def test_readme_inspection_is_case_insensitive_and_ignores_other_files(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[
        ("ReadMe.MD", b"# Research problem\n"),
        ("src/main.py", b"print('must not be read')\n"),
        ("data/results.csv", b"metric\n0.9\n"),
    ], note="readme")
    analysis = inspect_run_readme(runs, run["id"])
    assert analysis["files"] == ["ReadMe.MD"]
    assert analysis["ignored_data_files"] == 2
    assert "must not be read" not in "".join(analysis["chunks"])


def test_readme_inspection_requires_readme(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("main.py", b"print(1)\n")], note="missing")
    with pytest.raises(ValueError, match="没有 README.md.*只读取这一个文件"):
        inspect_run_readme(runs, run["id"])


def test_api_explains_missing_readme_without_reading_other_files(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    flow.writer.model = RecordingModel()
    app = create_app(flow)
    run_id = app.state.runs.create(entries=[("main.py", b"print('not read')\n")], note="missing readme")["id"]
    with TestClient(app) as client:
        response = client.post(f"/api/runs/{run_id}/paper/plan", json={"allow_model": True})
    assert response.status_code == 422
    assert "没有 README.md" in response.json()["detail"]
    assert "只读取这一个文件" in response.json()["detail"]


def test_get_hides_legacy_non_readme_plan(tmp_path):
    runs = LocalRunService(tmp_path / "runs")
    run = runs.create(entries=[("main.py", b"print(1)\n")], note="legacy")
    paper = runs.root / run["id"] / "paper"
    paper.mkdir()
    runs._write_json(paper / "plan.json", {"read_files": ["main.py"], "research_idea": "old"})
    runs._write_json(paper / "analysis-progress.json", {"stage": "complete", "done": 1, "total": 1})
    assert LocalPaperService(runs, None).get(run["id"])["plan"] is None
