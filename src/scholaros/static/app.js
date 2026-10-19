"use strict";

const state = {
  health: null,
  projects: [],
  currentProject: null,
  historyProject: null,
  historyRevision: null,
  activeStage: null,
  activeStageProjectId: null,
  pendingConfiguration: null,
  pollTimer: null,
  experimentPollTimer: null,
  experimentRequestId: 0,
  uploadedFolderProjectId: null,
  uploadedFolderId: null,
  optimizationProposalId: null,
  runPollTimer: null,
  runRequestId: 0,
  runDetailRequestId: 0,
  selectedLocalRunId: null,
  localRunDraftId: null,
  localRunLogSize: 0,
  localPaperRunId: null,
  localPaperRequestId: 0,
  pendingLocalRunId: null,
  sectionViewKey: null,
  sectionData: null,
  selectedSectionIndex: null,
  sectionRequestId: 0,
  toastTimer: null,
  interruptingProjectId: null,
  mermaidInitialized: false,
  diagramSequence: 0,
};

const terminalStatuses = new Set(["completed", "needs_attention", "failed"]);
const stages = ["scoping", "searching", "synthesizing", "designing", "drafting", "reviewing", "revising"];
const stageLabels = {
  scoping: "正在收敛研究问题",
  searching: "正在搜集研究材料",
  synthesizing: "正在建立证据与学习记录",
  designing: "正在设计方法与图表",
  drafting: "正在起草研究稿件",
  reviewing: "正在执行质量检查",
  revising: "正在汇总修订与交付",
  completed: "工作流已完成",
};
const stageWorkspaceCopy = {
  scoping: ["范围与配置", "确认研究边界、交付要求与核心贡献，确认后再重建后续内容。"],
  searching: ["研究材料", "补充资料并调整检索重点；固定论文源与模型补充候选会统一去重。"],
  synthesizing: ["证据与学习", "逐条检查论文、本地文件、证据摘要、定位信息与学习边界。"],
  designing: ["方法与图表", "修改研究设计以及图表构成、数据要求和表达边界，不把计划写成结果。"],
  drafting: ["研究稿件", "直接查看 Markdown 论文版式与 LaTeX 原文本，并按本阶段要求更新。"],
  reviewing: ["质量检查", "调整审阅重点并重新检查结构、引用、方法、图表和结果来源。"],
  revising: ["总览与交付", "汇总研究范围、材料、证据、图表、稿件与检查结果，再准备 Markdown/LaTeX 交付。"],
};
const statusLabels = {
  created: "待运行",
  running: "运行中",
  completed: "质量检查通过",
  needs_attention: "需人工处理",
  failed: "运行失败",
};
const artifactLabels = {
  "learning-plan.json": "文献学习计划",
  "contribution-blueprint.json": "研究贡献方案",
  "papers.json": "检索论文清单",
  "evidence.json": "证据账本",
  "research-design.json": "研究设计",
  "figure-story.json": "图件故事板",
  "table-story.json": "表格设计说明",
  "paper-draft.md": "论文初稿",
  "paper-draft.tex": "论文初稿 LaTeX 原文",
  "review.json": "初审报告",
  "paper.md": "修订论文",
  "final-review.json": "最终质量检查报告",
  "paper.docx": "可编辑 Word 稿",
  "paper.tex": "IEEE 主文稿（交付包内为 main.tex）",
  "ref.bib": "BibTeX 参考文献库",
  "paper.pdf": "阅读版 PDF",
  "delivery-manifest.json": "交付清单",
  "delivery-package.zip": "本地交付包",
};
const configurationLabels = {
  workflow: { build_from_materials: "由材料形成论文", rewrite_existing: "重写已有稿件", audit: "科研诚信与证据核查", review: "独立审阅", revise: "按意见返修", transfer: "适配新目标" },
  scene: { journal: "期刊", conference: "会议", report: "研究报告", review: "综述", competition: "科研竞赛", other: "其他" },
  output_language: { zh: "中文", en: "英文", multilingual: "中英双语", other: "其他 / 跟随材料" },
  research_mode: { agent_decide: "由系统结合材料判断", required: "需要新增研究分析", materials_only: "只使用已有材料与结果" },
  requested_scope: { manuscript: "论文稿件", local_delivery: "完整本地稿包", submission_package: "投稿材料包" },
  author_voice: { off: "不特别保留", standard: "适度保留", strict: "严格保留" },
  mechanism_figure: { prefer: "优先考虑", auto: "由系统判断", omit: "不采用" },
};

const element = (id) => document.getElementById(id);

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error(`无法连接 ScholarOS 服务：${error.message}`);
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    let detail = typeof data === "object" ? data.detail : data;
    if (response.status === 404 && (detail === "Not Found" || !detail)) {
      detail = "接口不存在（HTTP 404）。服务端可能仍是旧版本，请重启 scholaros serve 后重试。";
    }
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

function toast(message, isError = false) {
  const node = element("toast");
  node.textContent = message;
  node.classList.toggle("is-error", isError);
  node.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => node.classList.remove("is-visible"), 3400);
}

function setBusy(button, busy, label) {
  if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.originalLabel;
}

function showView(name) {
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("is-active", node.id === `${name}View`));
  document.querySelectorAll(".nav-button").forEach((node) => node.classList.toggle("is-active", node.dataset.view === name));
  element("pageTitle").textContent = { workspace: "当前研究项目", experiments: "实验", projects: "研究工作台", runs: "本机运行" }[name];
  if (name === "experiments") refreshExperiments().catch((error) => toast(error.message, true));
  else {
    state.experimentRequestId += 1;
    stopExperimentPolling();
  }
  if (name === "runs") refreshLocalRuns().catch((error) => toast(error.message, true));
  else {
    state.runRequestId += 1;
    state.runDetailRequestId += 1;
    stopLocalRunPolling();
  }
  if (name === "workspace") {
    element("workspaceEmpty").hidden = Boolean(state.currentProject);
    element("projectWorkspace").hidden = !state.currentProject;
  }
}

function renderSourceOptions() {
  const container = element("createSources");
  container.replaceChildren();
  const sources = state.health?.sources || [];
  for (const source of sources) {
    if (source.name === "ieee" || source.name === "ieee_metadata") continue;
    const label = document.createElement("label");
    label.className = "source-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = source.name;
    input.checked = source.available;
    input.disabled = !source.available;
    const text = document.createElement("span");
    const sourceName = {
      semantic_scholar: "Semantic Scholar",
      ieee_metadata: "IEEE 书目元数据",
      ieee: "IEEE Xplore",
    }[source.name] || source.name.toUpperCase();
    text.textContent = sourceName;
    if (!source.available) text.title = source.status || "缺少所需 API Key";
    label.append(input, text);
    container.append(label);
  }
}

function selectedSources(containerId) {
  return [...element(containerId).querySelectorAll("input:checked:not(:disabled)")].map((input) => input.value);
}

async function loadHealth() {
  const health = await api("/health");
  state.health = health;
  element("brandVersion").textContent = `v${health.version || "0.4.0"}`;
  const status = element("systemStatus");
  status.classList.add("is-on");
  status.lastChild.textContent = " 服务已连接";
  const model = element("modelBadge");
  if (model) {
    model.classList.toggle("is-on", health.model_configured);
    model.lastChild.textContent = health.model_configured
      ? ` 模型已配置 · ${health.model_name}`
      : " 未配置模型 · 诚实降级";
  }
  const sourceStatus = element("sourceStatus");
  if (sourceStatus) {
    sourceStatus.replaceChildren();
    for (const source of health.sources) {
      if (source.name === "ieee" || source.name === "ieee_metadata") continue;
      const chip = document.createElement("span");
      chip.className = `source-chip${source.available ? "" : " is-off"}`;
      const sourceLabel = {
        semantic_scholar: "Semantic Scholar",
        ieee_metadata: "IEEE 书目元数据",
        ieee: "IEEE Xplore",
      }[source.name] || source.name;
      chip.textContent = source.name === "ieee" ? `${sourceLabel} · ${source.status}` : sourceLabel;
      chip.title = `${source.access} · ${source.status}`;
      sourceStatus.append(chip);
    }
  }
  renderSourceOptions();
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  let localRuns = [];
  try { localRuns = await api("/api/runs"); } catch { localRuns = []; }
  const selector = element("experimentProject");
  const selected = selector.value || state.currentProject?.id
    || (state.projects.length === 1 ? state.projects[0].id : "");
  selector.replaceChildren(new Option("请选择研究项目", ""));
  for (const project of state.projects) selector.add(new Option(project.title || project.idea, project.id));
  if (state.projects.some((project) => project.id === selected)) selector.value = selected;

  const merged = [
    ...state.projects.map((project) => ({ kind: "project", time: project.updated_at || project.created_at || "", item: project })),
    ...localRuns.slice(0, 20).map((run) => ({ kind: "run", time: run.created_at || "", item: run })),
  ].sort((a, b) => String(b.time).localeCompare(String(a.time)));

  const container = element("recentProjects");
  container.replaceChildren();
  if (!merged.length) {
    const empty = document.createElement("p");
    empty.className = "muted compact";
    empty.textContent = "还没有项目或本机运行记录。";
    container.append(empty);
    return;
  }
  for (const { kind, item } of merged.slice(0, 30)) {
    const button = document.createElement("button");
    button.type = "button";
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    if (kind === "project") {
      button.className = `project-link${state.currentProject?.id === item.id ? " is-active" : ""}`;
      title.textContent = item.title || item.idea;
      meta.textContent = `${statusLabels[item.status] || item.status} · ${item.id}`;
      button.addEventListener("click", () => openProject(item.id));
    } else {
      button.className = "project-link";
      title.textContent = `本地运行_${item.note || item.id.slice(0, 10)}`;
      meta.textContent = `本机运行 · ${item.status} · ${formatDate(item.created_at)}`;
      button.addEventListener("click", () => {
        state.selectedLocalRunId = item.id;
        state.localRunLogSize = 0;
        showView("runs");
        refreshLocalRuns().catch((error) => toast(error.message, true));
      });
    }
    button.append(title, meta);
    container.append(button);
  }
}

function stopExperimentPolling() {
  window.clearTimeout(state.experimentPollTimer);
  state.experimentPollTimer = null;
}

function selectedFolderEntries() {
  return [...element("experimentFolder").files].map((file) => ({
    file, path: (file.webkitRelativePath || "").split("/").slice(1).join("/"),
  })).filter((entry) => entry.path && !entry.path.split("/").some((part) => part.startsWith(".")));
}

function folderCommand(main) {
  return main ? `python ${JSON.stringify(main)}` : "";
}

function clearOptimization() {
  state.optimizationProposalId = null;
  element("experimentOptimizationPreview").hidden = true;
  element("experimentOptimizationFeedback").textContent = "";
}

function renderOptimization(proposal) {
  state.optimizationProposalId = proposal.id;
  element("experimentOptimizationReason").textContent = proposal.reason;
  element("experimentOptimizationDiff").textContent = proposal.diff;
  element("experimentOptimizationCode").textContent = proposal.edits
    .map((edit) => `# ${edit.path}\n${edit.code}`).join("\n\n");
  element("experimentOptimizationPreview").hidden = false;
  element("experimentOptimizationFeedback").textContent = "请检查候选代码及差异；运行不会覆盖原文件。";
}

function updateOptimizationMode() {
  const enabled = element("experimentInputMode").value === "folder" && element("experimentOptimizeMode").checked;
  element("experimentOptimizationControls").hidden = !enabled;
  element("experimentBaselineFile").disabled = enabled;
  element("experimentBaselineCommandRow").hidden = enabled || !element("experimentBaselineFile").value;
  if (enabled) {
    element("experimentBaselineFile").value = "";
    element("experimentBaselineCommand").value = "";
  } else clearOptimization();
}

function renderFolderChoices(paths) {
  const scripts = paths.filter((path) => path.endsWith(".py")).sort();
  const data = paths.filter((path) => !path.endsWith(".py")).sort();
  const allowed = element("experimentAlgorithmFiles");
  const hadOptions = allowed.options.length > 0;
  const previousAllowed = new Set([...allowed.selectedOptions].map((item) => item.value));
  allowed.replaceChildren();
  for (const path of scripts) {
    const option = new Option(path, path);
    option.selected = hadOptions ? previousAllowed.has(path) : true;
    allowed.add(option);
  }
  for (const [id, items, empty] of [
    ["experimentMainFile", scripts, "选择 Python 主文件"],
    ["experimentBaselineFile", scripts, "不使用基线文件"],
    ["experimentDataFile", data, "脚本自行读取目录内文件"],
  ]) {
    const select = element(id);
    const previous = select.value;
    select.replaceChildren(new Option(empty, ""));
    for (const path of items) select.add(new Option(path, path));
    if (items.includes(previous)) select.value = previous;
    else if (id === "experimentMainFile" && scripts.length === 1) select.value = scripts[0];
  }
  if (!element("experimentCommand").value && element("experimentMainFile").value) {
    element("experimentCommand").value = folderCommand(element("experimentMainFile").value);
  }
  element("experimentBaselineCommandRow").hidden = !element("experimentBaselineFile").value;
  if (element("experimentBaselineFile").value && !element("experimentBaselineCommand").value) {
    element("experimentBaselineCommand").value = folderCommand(element("experimentBaselineFile").value);
  }
}

function updateExperimentInputMode() {
  const folderMode = element("experimentInputMode").value === "folder";
  element("experimentFolderInputs").hidden = !folderMode;
  element("experimentSingleInputs").hidden = folderMode;
  if (!folderMode) element("experimentOptimizeMode").checked = false;
  updateOptimizationMode();
}

async function uploadFolderIfNeeded(projectId) {
  const input = element("experimentFolder");
  if (!input.files.length) {
    if (state.uploadedFolderId) return state.uploadedFolderId;
    const data = await api(`/api/projects/${projectId}/experiments`);
    if (!data.folder) throw new Error("请先选择项目文件夹。 ");
    state.uploadedFolderId = data.folder.id;
    renderFolderChoices(Object.keys(data.folder.files));
    return state.uploadedFolderId;
  }
  if (state.uploadedFolderProjectId === projectId) return state.uploadedFolderId;
  const entries = selectedFolderEntries();
  if (entries.length !== input.files.length) {
    element("experimentCommandFeedback").textContent = "已忽略隐藏文件；请确认目录中不包含密钥。";
  }
  if (!entries.length || entries.reduce((total, { file }) => total + file.size, 0) > 2 * 1024 * 1024 * 1024) {
    throw new Error("目录须至少包含一个普通文件，总量不超过 2 GiB。");
  }
  const body = new FormData();
  for (const { file, path } of entries) {
    body.append("paths", path);
    body.append("files", file, file.name);
  }
  const uploaded = await api(`/api/projects/${projectId}/experiments/folder`, { method: "POST", body });
  state.uploadedFolderProjectId = projectId;
  state.uploadedFolderId = uploaded.id;
  return uploaded.id;
}

async function suggestExperimentCommand() {
  const projectId = element("experimentProject").value;
  const main = element("experimentMainFile").value;
  if (!projectId || !main) return toast("请先选择项目文件夹和主文件。", true);
  const button = element("suggestExperimentCommand");
  setBusy(button, true, "正在分析…");
  try {
    const folderId = await uploadFolderIfNeeded(projectId);
    const suggestion = await api(`/api/projects/${projectId}/experiments/suggest-command`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_id: folderId,
        main_file: main,
        reference_command: element("experimentReferenceCommand").value.trim(),
        allow_model: element("experimentShareCode").checked,
      }),
    });
    element("experimentCommand").value = suggestion.command;
    element("experimentCommandFeedback").textContent = suggestion.source === "model"
      ? "模型建议已填入，请检查后再运行。" : "已填入默认命令；未向模型发送代码。";
    await refreshExperiments();
  } catch (error) { toast(error.message, true); }
  finally { setBusy(button, false, ""); }
}

async function generateOptimization() {
  const projectId = element("experimentProject").value;
  const main = element("experimentMainFile").value;
  const goal = element("experimentOptimizationGoal").value.trim();
  const filesToConsider = [...element("experimentAlgorithmFiles").selectedOptions].map((item) => item.value);
  if (!projectId || !main || goal.length < 4) return toast("请选择主文件并写明改进目标。", true);
  if (!filesToConsider.length) return toast("请选择至少一个可能包含算法的 Python 文件。", true);
  if (!element("experimentShareCode").checked) return toast("请先确认允许向已配置模型发送主文件。", true);
  const button = element("generateOptimization");
  setBusy(button, true, "正在生成…");
  try {
    const folderId = await uploadFolderIfNeeded(projectId);
    const proposal = await api(`/api/projects/${projectId}/experiments/optimize`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folderId, main_file: main,
        files_to_consider: filesToConsider, goal, allow_model: true }),
    });
    const currentFiles = [...element("experimentAlgorithmFiles").selectedOptions].map((item) => item.value);
    if (element("experimentProject").value !== projectId || element("experimentMainFile").value !== main
        || element("experimentOptimizationGoal").value.trim() !== goal
        || state.uploadedFolderId !== folderId || JSON.stringify(currentFiles) !== JSON.stringify(filesToConsider)) {
      clearOptimization();
      return toast("项目或候选文件已变化，请重新生成改进方案。", true);
    }
    renderOptimization(proposal);
  } catch (error) { clearOptimization(); toast(error.message, true); }
  finally { setBusy(button, false, ""); }
}

async function refreshExperiments() {
  const requestId = ++state.experimentRequestId;
  const projectId = element("experimentProject").value;
  const container = element("experimentRecords");
  if (!projectId) {
    container.textContent = "请先选择一个研究项目。";
    stopExperimentPolling();
    return;
  }
  const data = await api(`/api/projects/${projectId}/experiments`);
  if (requestId !== state.experimentRequestId || element("experimentProject").value !== projectId) return;
  if (!element("experimentFolder").files.length && data.folder) {
    if (state.uploadedFolderId && state.uploadedFolderId !== data.folder.id) {
      element("experimentCommandFeedback").textContent = "项目文件夹已被更新，请刷新页面并重新确认主文件与命令。";
    } else {
      state.uploadedFolderId = data.folder.id;
      renderFolderChoices(Object.keys(data.folder.files));
    }
  }
  container.replaceChildren();
  const inputs = document.createElement("p");
  inputs.className = "form-feedback";
  inputs.textContent = `已上传：${data.folder ? `项目文件夹 ${Object.keys(data.folder.files).length} 个文件` : Object.keys(data.inputs).join("、") || "暂无"}。旧运行保留独立快照。`;
  container.append(inputs);
  for (const run of data.runs) {
    const card = document.createElement("article");
    card.className = "experiment-run";
    const title = document.createElement("h5");
    title.textContent = `${run.id.slice(0, 10)} · ${run.status} · ${run.protocol.metric}（${run.protocol.direction}）`;
    card.append(title);
    const detail = document.createElement("p");
    detail.textContent = `假设：${run.protocol.hypothesis}；实验方案差异：${run.protocol.intervention}。已记录 ${run.trials.length} 次原始试验。`;
    card.append(detail);
    if (run.optimized) {
      const note = document.createElement("p");
      note.textContent = `算法优化对照：修改了 ${run.optimized.edits.length} 个 Python 文件；原版和候选版使用相同命令、参数与种子。`;
      card.append(note);
    }
    const provenance = document.createElement("p");
    provenance.textContent = run.protocol.mode === "folder"
      ? `项目文件 ${Object.keys(run.inputs).length} 个，输入哈希已存入运行记录。`
      : `数据 / 基线 / 候选 SHA-256：${Object.values(run.inputs).map((hash) => hash.slice(0, 12)).join(" · ")}`;
    card.append(provenance);
    if (run.summary?.length) {
      const list = document.createElement("ul");
      for (const group of run.summary) {
        const item = document.createElement("li");
        item.textContent = `${group.role} ${JSON.stringify(group.parameters)} · 均值 ${group.mean} · 成功 ${group.count} 次`;
        list.append(item);
      }
      card.append(list);
    }
    if (run.comparison) {
      const comparison = document.createElement("p");
      comparison.textContent = run.comparison.baseline_mean === null
        ? `当前候选中最优参数 ${JSON.stringify(run.comparison.parameters)}：均值 ${run.comparison.candidate_mean}。${run.comparison.note}`
        : `当前候选中最优参数 ${JSON.stringify(run.comparison.parameters)}：基线均值 ${run.comparison.baseline_mean}，候选均值 ${run.comparison.candidate_mean}，差值 ${run.comparison.difference}。${run.comparison.note}`;
      card.append(comparison);
    }
    const failures = run.trials.filter((trial) => trial.status !== "completed");
    if (failures.length || run.error || run.warning) {
      const warning = document.createElement("p");
      warning.textContent = `失败 ${failures.length} 次；${run.error || failures.map((trial) => trial.error).join("；")} ${run.warning || ""}`;
      card.append(warning);
    }
    const raw = document.createElement("details");
    const caption = document.createElement("summary");
    caption.textContent = "查看逐次试验原始记录";
    const content = document.createElement("pre");
    content.textContent = JSON.stringify(run.trials, null, 2);
    raw.append(caption, content);
    card.append(raw);
    if (run.status === "completed" && !data.adopted) {
      const adopt = document.createElement("button");
      adopt.className = "secondary-button";
      adopt.type = "button";
      adopt.textContent = "研究者确认并导入结果";
      adopt.addEventListener("click", async () => {
        if (!window.confirm("确认将这份完整实验报告作为该项目的真实结果资料？每个项目首版只能导入一份实验报告。")) return;
        try {
          await api(`/api/projects/${projectId}/experiments/runs/${run.id}/adopt`, { method: "POST" });
          toast("结果已导入。原有稿件将标记为待更新，请在项目页检查并重跑相关阶段。");
          await refreshExperiments();
        } catch (error) { toast(error.message, true); }
      });
      card.append(adopt);
    }
    container.append(card);
  }
  if (!data.runs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "尚无运行记录；请按上方协议提供材料后启动。";
    container.append(empty);
  }
  stopExperimentPolling();
  if (data.active?.status === "running") {
    state.experimentPollTimer = window.setTimeout(() => refreshExperiments().catch((error) => {
      stopExperimentPolling();
      toast(error.message, true);
    }), 2000);
  }
}

async function runExperiment(event) {
  event.preventDefault();
  const button = element("runExperiment");
  const projectId = element("experimentProject").value;
  if (!projectId) return toast("请先选择研究项目。", true);
  const folderMode = element("experimentInputMode").value === "folder";
  const main = element("experimentMainFile").value;
  const command = element("experimentCommand").value.trim();
  const optimizing = folderMode && element("experimentOptimizeMode").checked;
  if (folderMode && (!main || !command)) return toast("请选择主文件并确认运行命令。", true);
  if (optimizing && !state.optimizationProposalId) return toast("请先生成并审阅候选代码。", true);
  if (folderMode && !window.confirm(`确认在隔离容器中${optimizing ? "对照运行原版和改进版" : "执行"}以下命令？\n\n${command}`)) return;
  let variants;
  try { variants = JSON.parse(element("experimentVariants").value); }
  catch { return toast("参数候选必须是 JSON 数组。", true); }
  const seedParts = element("experimentSeeds").value.split(",").map((part) => part.trim());
  if (seedParts.some((part) => !/^\d+$/.test(part))) return toast("随机种子只能是逗号分隔的非负整数。", true);
  const protocol = {
    hypothesis: element("experimentHypothesis").value.trim(),
    intervention: element("experimentIntervention").value.trim(),
    metric: element("experimentMetric").value.trim(),
    direction: element("experimentDirection").value,
    seeds: seedParts.map(Number),
    variants,
    timeout_seconds: Number(element("experimentTimeout").value),
    acknowledge_code_risk: element("experimentConsent").checked,
    mode: folderMode ? "folder" : "single",
    main_file: folderMode ? main : null,
    baseline_file: folderMode && !optimizing ? element("experimentBaselineFile").value || null : null,
    baseline_command: folderMode && !optimizing ? element("experimentBaselineCommand").value.trim() || null : null,
    dataset_file: folderMode ? element("experimentDataFile").value || null : null,
    command: folderMode ? command : null,
    optimization_proposal_id: optimizing ? state.optimizationProposalId : null,
  };
  setBusy(button, true, "正在准备实验…");
  try {
    if (folderMode) protocol.folder_id = await uploadFolderIfNeeded(projectId);
    else {
      for (const [role, id] of [["dataset", "experimentDataset"], ["baseline", "experimentBaseline"], ["candidate", "experimentCandidate"]]) {
        const file = element(id).files[0];
        if (!file) continue;
        const body = new FormData();
        body.append("file", file);
        await api(`/api/projects/${projectId}/experiments/inputs/${role}`, { method: "POST", body });
      }
    }
    const result = await api(`/api/projects/${projectId}/experiments/runs`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(protocol),
    });
    element("experimentFeedback").textContent = `已开始运行 ${result.run_id.slice(0, 10)}，请在下方检查记录。`;
    await refreshExperiments();
  } catch (error) {
    element("experimentFeedback").textContent = error.message;
    toast(error.message, true);
  } finally { setBusy(button, false, ""); }
}

async function uploadFiles(projectId, files, role) {
  for (const file of files) {
    const body = new FormData();
    body.append("file", file);
    await api(`/api/projects/${projectId}/documents?role=${role}`, { method: "POST", body });
  }
}

function updateIdeaLength() {
  const input = element("idea");
  const count = Array.from(input.value.trim()).length;
  const limit = Number(input.dataset.maxLength);
  const exceeded = count > limit;
  element("ideaLength").textContent = `${count.toLocaleString("en-US")} / ${limit.toLocaleString("en-US")} 字符${exceeded ? " · 已超限，请精简后提交（输入内容已保留）" : ""}`;
  input.setCustomValidity(exceeded ? `研究想法最多支持 ${limit.toLocaleString("en-US")} 个字符。` : "");
  return !exceeded;
}

function expandIdeaOnDemand() {
  const input = element("idea");
  if (input.scrollHeight <= input.clientHeight + 8) return;
  input.classList.add("is-expanded");
  input.style.height = `${Math.min(Math.max(input.scrollHeight + 24, 260), 620)}px`;
}

function collapseIdeaOnDemand() {
  const input = element("idea");
  input.classList.remove("is-expanded");
  input.style.removeProperty("height");
}

function collectConfiguration(prefix = "") {
  const read = (name) => element(configurationFieldId(prefix, name));
  const referenceMode = read("ReferenceCountMode").value;
  const countValue = read("ReferenceCount").value;
  return {
    workflow: read("WorkflowType").value,
    scene: read("SceneType").value,
    target_name: read("TargetName").value.trim(),
    output_language: read("OutputLanguage").value,
    research_mode: read("ResearchMode").value,
    requested_scope: read("RequestedScope").value,
    author_voice: read("AuthorVoice").value,
    latex_template: read("LatexTemplate").value,
    reference_count_mode: referenceMode,
    reference_count: referenceMode === "custom" && countValue ? Number(countValue) : null,
    mechanism_figure: read("MechanismFigure").value,
    formats: ["md", "tex"],
  };
}

function configurationFieldId(prefix, name) {
  return prefix ? `${prefix}${name}` : `${name.charAt(0).toLowerCase()}${name.slice(1)}`;
}

function updateReferenceCountState(prefix = "") {
  const mode = element(configurationFieldId(prefix, "ReferenceCountMode")).value;
  const input = element(configurationFieldId(prefix, "ReferenceCount"));
  input.disabled = mode !== "custom";
  if (input.disabled) input.value = "";
}

async function createProject(event) {
  event.preventDefault();
  const button = element("createButton");
  const feedback = element("createFeedback");
  const idea = element("idea").value.trim();
  if (!updateIdeaLength()) {
    element("idea").reportValidity();
    return;
  }
  if (Array.from(idea).length < 8) {
    toast("研究想法至少需要 8 个字符。", true);
    return;
  }
  setBusy(button, true, "正在创建项目…");
  feedback.textContent = "正在创建项目并准备资料…";
  let project;
  try {
    project = await api("/api/projects", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        idea,
        source_run_id: state.pendingLocalRunId,
        sources: selectedSources("createSources"),
        run_now: false,
        guided: document.querySelector('input[name="executionMode"]:checked')?.value !== "automatic",
        configuration: collectConfiguration(),
      }),
    });
    state.pendingLocalRunId = null;
    element("sourceRunBanner").hidden = true;
    state.currentProject = project;
    state.historyProject = null;
    state.historyRevision = null;
    showView("workspace");
    renderProject(project);
    element("projectWorkspace").scrollIntoView({ behavior: "smooth", block: "start" });
    const sourceFiles = [...element("sourceFiles").files];
    const resultFiles = [...element("resultFiles").files];
    if (sourceFiles.length) {
      feedback.textContent = `正在上传 ${sourceFiles.length} 份参考资料…`;
      await uploadFiles(project.id, sourceFiles, "source");
    }
    if (resultFiles.length) {
      feedback.textContent = `正在上传 ${resultFiles.length} 份真实结果资料…`;
      await uploadFiles(project.id, resultFiles, "results");
    }
    feedback.textContent = "项目已创建，研究工作流正在运行…";
    await api(`/api/projects/${project.id}/run`, { method: "POST" });
    await refreshCurrentProject();
    feedback.textContent = "项目已创建；进度、选择和制品都保存在下方工作区。";
    startPolling();
    toast("研究项目已启动。每个阶段都会持久化保存。")
  } catch (error) {
    feedback.textContent = `未能启动：${error.message}`;
    toast(error.message, true);
    if (project) await openProject(project.id);
  } finally {
    setBusy(button, false, "");
    await loadProjects().catch(() => {});
  }
}

async function openProject(projectId) {
  showView("workspace");
  try {
    const project = await api(`/api/projects/${projectId}`);
    state.currentProject = project;
    state.historyProject = null;
    state.historyRevision = null;
    renderProject(project);
    await loadProjects();
    element("projectWorkspace").scrollIntoView({ behavior: "smooth", block: "start" });
    if (project.is_active === true || (project.status === "running" && project.is_active !== false)) startPolling();
    else stopPolling();
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshCurrentProject() {
  if (!state.currentProject) return;
  const project = await api(`/api/projects/${state.currentProject.id}`);
  if (project.is_active !== true && state.interruptingProjectId === project.id) {
    state.interruptingProjectId = null;
  }
  state.currentProject = project;
  if (state.historyProject) {
    state.historyProject.history = project.history || [];
    renderHistoryTimeline(project.history || []);
  } else {
    renderProject(project);
  }
  if ((terminalStatuses.has(project.status) && project.is_active !== true) || (project.status === "running" && project.is_active === false)) {
    stopPolling();
    await loadProjects();
  }
}

function startPolling() {
  stopPolling();
  if (!state.currentProject || (terminalStatuses.has(state.currentProject.status) && state.currentProject.is_active !== true)) return;
  if (state.currentProject.status === "running" && state.currentProject.is_active === false) return;
  state.pollTimer = window.setInterval(() => refreshCurrentProject().catch((error) => {
    stopPolling();
    toast(error.message, true);
  }), 1200);
}

function stopPolling() {
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function renderProject(project) {
  element("workspaceEmpty").hidden = true;
  const workspace = element("projectWorkspace");
  workspace.hidden = false;
  const isHistory = project.is_history === true;
  const displayStage = isHistory && stages.includes(project.history_stage)
    ? project.history_stage
    : project.stage;
  element("projectTitle").textContent = project.title || project.idea;
  element("projectMeta").textContent = isHistory
    ? `历史版本 · ${formatDate(project.history_created_at)} · ${project.history_reason}`
    : `项目 ${project.id} · 创建于 ${formatDate(project.created_at)}`;
  const isActive = !isHistory
    && (project.is_active === true || (project.status === "running" && project.is_active !== false));
  const isInterrupted = project.state?.interrupted === true
    || (project.status === "running" && !isActive);
  const status = element("projectStatus");
  status.textContent = isHistory
    ? `历史 · ${stageWorkspaceCopy[displayStage]?.[0] || displayStage}`
    : isInterrupted ? "已中断" : statusLabels[project.status] || project.status;
  status.className = `status-pill${isActive ? " is-running" : project.status === "failed" || project.status === "needs_attention" || isInterrupted ? " is-error" : ""}`;

  const isTerminal = terminalStatuses.has(project.status);
  const currentIndex = displayStage === "completed" ? stages.length : Math.max(0, stages.indexOf(displayStage));
  if (state.activeStageProjectId !== project.id || !stages.includes(state.activeStage)) {
    state.activeStageProjectId = project.id;
    state.activeStage = displayStage === "completed" ? "revising" : displayStage;
    state.pendingConfiguration = null;
  }
  const checkpoint = isHistory ? null : project.state?.pending_checkpoint;
  if (checkpoint && stages.includes(checkpoint)) state.activeStage = checkpoint;
  const finishedStages = isHistory
    ? Math.min(stages.length, stages.indexOf(state.activeStage) + 1)
    : project.stage === "completed" ? stages.length : currentIndex;
  const percent = project.status === "created" ? 0 : Math.round((Math.max(finishedStages, project.status === "running" ? currentIndex + 0.35 : finishedStages) / stages.length) * 100);
  element("progressFill").style.width = `${Math.min(100, percent)}%`;
  element("progressPercent").textContent = `${Math.min(100, percent)}%`;
  element("stageText").textContent = isHistory
    ? `正在查看：${project.history_reason}`
    : project.error || (isInterrupted ? "上次运行已中断，可从断点继续" : stageLabels[project.stage] || "准备开始");
  document.querySelectorAll("#stageList li").forEach((node, index) => {
    node.classList.toggle("is-done", index < finishedStages || displayStage === "completed");
    node.classList.toggle("is-current", !isHistory && project.status === "running" && index === currentIndex);
    node.classList.toggle("is-selected", node.dataset.stage === state.activeStage);
    node.querySelector("button")?.setAttribute("aria-selected", String(node.dataset.stage === state.activeStage));
  });
  const runButton = element("runProject");
  const interruptButton = element("interruptProject");
  const needsSearchConfirmation = project.state?.search_confirmation_required === true;
  const isInterrupting = state.interruptingProjectId === project.id;
  interruptButton.hidden = isHistory || !isActive;
  interruptButton.disabled = !isActive || isInterrupting;
  interruptButton.textContent = isInterrupting ? "正在中断…" : "中断运行";
  runButton.disabled = isActive;
  runButton.hidden = isHistory || isActive || needsSearchConfirmation || Boolean(checkpoint);
  runButton.textContent = project.status === "created" ? "开始运行" : "从头重新运行";
  element("resumeProject").hidden = isHistory || isActive || needsSearchConfirmation || Boolean(checkpoint)
    || project.stage === "completed" || project.status === "created";
  element("approveStage").hidden = isHistory || isActive || !checkpoint;
  const checkpointPreview = element("checkpointPreview");
  element("checkpointPanel").hidden = !checkpoint;
  const checkpointPreviewValue = {
    scoping: project.state?.spec,
    searching: {
      queries: project.state?.search_queries || [],
      papers: (project.state?.papers || []).slice(0, 8).map((paper) => ({
        title: paper.title,
        authors: paper.authors,
        year: paper.year,
        sources: paper.sources,
      })),
    },
    synthesizing: project.state?.evidence,
    designing: {
      design: project.state?.design,
      figure_story: project.state?.figure_story,
      table_story: project.state?.table_story,
    },
    drafting: {
      artifacts: project.artifacts?.filter((name) => name.includes("paper-draft")) || [],
      note: "请在下方研究稿件原文区域预览 Markdown 与 LaTeX。",
    },
    reviewing: project.state?.review,
    revising: project.state?.final_review || project.state?.delivery_manifest,
  }[checkpoint];
  checkpointPreview.textContent = checkpoint
    ? JSON.stringify(checkpointPreviewValue || { note: "本阶段已完成，请检查下方工作区。" }, null, 2)
    : "暂无待确认阶段。";
  element("uploadMore").disabled = isHistory || project.status === "running";
  element("moreSourceFiles").disabled = isHistory || project.status === "running";
  element("moreResultFiles").disabled = isHistory || project.status === "running";
  element("refreshProject").disabled = isHistory;
  element("deleteProject").disabled = isHistory || isActive;
  element("deleteProject").title = isHistory
    ? "历史版本为只读，请先返回当前版本"
    : isActive ? "项目运行中，暂时不能删除" : "永久删除项目及全部制品";
  const confirmation = element("searchConfirmation");
  confirmation.hidden = isHistory || !needsSearchConfirmation;
  const queryList = element("pendingSearchQueries");
  queryList.replaceChildren();
  if (needsSearchConfirmation) {
    for (const query of project.state.search_queries || []) {
      const item = document.createElement("li");
      item.textContent = query;
      queryList.append(item);
    }
  }
  const warning = element("searchPlanWarning");
  const warnings = project.state?.search_plan_warnings || [];
  warning.hidden = !needsSearchConfirmation || !warnings.length;
  warning.textContent = warnings.join(" ");
  renderHistoryTimeline(state.currentProject?.history || project.history || []);
  const historyPanel = element("historyPanel");
  const returnButton = element("returnCurrentVersion");
  const historyNotice = element("historyViewNotice");
  historyPanel.open = isHistory || historyPanel.open;
  returnButton.hidden = !isHistory;
  historyNotice.hidden = !isHistory;
  historyNotice.textContent = isHistory
    ? `只读历史版本：${project.history_reason}。阶段内容和制品均来自该版本。`
    : "";
  renderArtifacts(project);
  renderReview(project);
  renderWorkspaceV2(project, isActive || isInterrupted || isHistory);
  renderWorkspaceStage(project, isActive || isInterrupted || isHistory);
  renderManuscriptSections(project);
}

function renderWorkspaceStage(project, isActive) {
  const activeStage = state.activeStage || "scoping";
  const [title, description] = stageWorkspaceCopy[activeStage];
  element("activeStageTitle").textContent = title;
  element("activeStageDescription").textContent = description;
  const grid = document.querySelector(".v2-workbench-grid");
  grid.dataset.activeStage = activeStage;
  grid.querySelectorAll("[data-stage-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.stagePanel !== activeStage;
  });

  const pending = project.state?.pending_stage_edits?.[activeStage];
  const confirmed = project.state?.stage_instructions?.[activeStage];
  const editor = element("stageEditText");
  const editContext = `${project.id}:${activeStage}:${pending?.updated_at || ""}:${confirmed || ""}`;
  if (editor.dataset.context !== editContext) {
    editor.value = pending?.text || confirmed || "";
    editor.dataset.context = editContext;
  }
  const scopeFields = element("scopeEditFields");
  const scopeSpec = project.state?.spec || {};
  const scopeTitle = element("stageScopeTitle");
  const scopeKeywords = element("stageScopeKeywords");
  const scopePending = activeStage === "scoping" ? pending : null;
  scopeFields.hidden = activeStage !== "scoping";
  const scopeContext = `${project.id}:scoping:${scopePending?.updated_at || ""}:${scopeSpec.title || ""}:${(scopeSpec.keywords || []).join("|")}`;
  if (scopeFields.dataset.context !== scopeContext) {
    scopeTitle.value = scopePending?.title ?? scopeSpec.title ?? "";
    scopeKeywords.value = (scopePending?.keywords ?? scopeSpec.keywords ?? []).join(", ");
    scopeFields.dataset.context = scopeContext;
  }
  const currentIndex = project.stage === "completed" ? stages.length - 1 : stages.indexOf(project.stage);
  const canConfirm = stages.indexOf(activeStage) <= Math.max(0, currentIndex);
  editor.disabled = isActive;
  scopeTitle.disabled = isActive || activeStage !== "scoping";
  scopeKeywords.disabled = isActive || activeStage !== "scoping";
  element("saveStageEdit").disabled = isActive;
  element("confirmStageEdit").disabled = isActive || !pending || !canConfirm;
  element("stageEditStatus").textContent = project.is_history
    ? "历史版本为只读，返回当前版本后可继续编辑"
    : pending
      ? `修改草稿已保存于 ${formatDate(pending.updated_at)}，尚未影响现有结果`
      : confirmed
        ? "已应用上一条要求；继续编辑可形成新的修改草稿"
        : canConfirm
          ? "尚未保存修改草稿"
          : "该阶段尚未到达，可先查看，完成上游后再确认修改";
}

function selectWorkspaceStage(stage) {
  if (!state.currentProject || !stages.includes(stage)) return;
  state.activeStage = stage;
  renderProject(state.historyProject || state.currentProject);
}

function renderWorkspaceV2(project, isActive) {
  renderConfiguration(project, isActive);
  renderMaterials(project);
  renderContributionOptions(project);
  renderEvidenceSummary(project);
  renderFigureStory(project);
  renderProjectOverview(project);
}

function renderConfiguration(project, isActive) {
  const config = project.state?.configuration || {};
  const formConfig = state.pendingConfiguration || config;
  const container = element("configurationSummary");
  container.replaceChildren();
  const rows = [
    ["工作类型", configurationLabels.workflow[config.workflow] || config.workflow],
    ["目标", config.target_name || `${configurationLabels.scene[config.scene] || config.scene} · 暂未确定具体名称`],
    ["语言", configurationLabels.output_language[config.output_language] || config.output_language],
    ["研究边界", configurationLabels.research_mode[config.research_mode] || config.research_mode],
    ["LaTeX 模板", config.latex_template === "ieee_journal" ? "IEEE 英文期刊" : "通用稿件"],
    ["交付", `${configurationLabels.requested_scope[config.requested_scope] || config.requested_scope} · ${(config.formats || []).join(" / ").toUpperCase()}`],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    const term = document.createElement("span");
    const description = document.createElement("strong");
    term.textContent = label;
    description.textContent = value || "未设置";
    row.append(term, description);
    container.append(row);
  }
  const fieldMap = {
    WorkflowType: "workflow", SceneType: "scene", TargetName: "target_name",
    OutputLanguage: "output_language", ResearchMode: "research_mode",
    RequestedScope: "requested_scope", AuthorVoice: "author_voice",
    MechanismFigure: "mechanism_figure", LatexTemplate: "latex_template",
    ReferenceCountMode: "reference_count_mode",
    ReferenceCount: "reference_count",
  };
  for (const [field, key] of Object.entries(fieldMap)) {
    const input = element(`project${field}`);
    input.value = formConfig[key] ?? "";
    input.disabled = isActive;
  }
  if (state.pendingConfiguration) {
    const button = element("saveConfiguration");
    button.textContent = "确认配置并更新后续";
    button.dataset.originalLabel = "确认配置并更新后续";
  }
  element("toggleConfigurationEditor").disabled = isActive;
  element("saveConfiguration").disabled = isActive;
  updateReferenceCountState("project");
  if (isActive) {
    element("projectReferenceCount").disabled = true;
  }
}

function renderMaterials(project) {
  const container = element("materialInventory");
  container.replaceChildren();
  const documents = project.state?.documents || [];
  if (!documents.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "尚未上传材料；可以先运行纯问题模式，也可以补充原稿、参考资料或真实结果。";
    container.append(empty);
    return;
  }
  for (const document of documents) {
    const row = document.createElement("div");
    row.className = "material-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    title.textContent = document.name;
    meta.textContent = `${document.role === "results" ? "真实结果" : "参考资料"} · ${String(document.kind || "file").toUpperCase()} · SHA-256 ${String(document.sha256 || "").slice(0, 10)}…`;
    copy.append(title, meta);
    const badge = document.createElement("em");
    badge.textContent = document.role === "results" ? "RESULT" : "SOURCE";
    row.append(copy, badge);
    container.append(row);
  }
}

function artifactActionRow(project, name) {
  const row = document.createElement("div");
  row.className = "artifact-item";
  const label = document.createElement("span");
  label.textContent = artifactLabels[name] || name;
  const actions = document.createElement("div");
  actions.className = "artifact-actions";
  if (name.endsWith(".md") || name.endsWith(".json") || name.endsWith(".tex")) {
    const preview = document.createElement("button");
    preview.type = "button";
    preview.textContent = "预览";
    preview.addEventListener("click", () => previewArtifact(project.id, name, project.history_revision));
    actions.append(preview);
  }
  const download = document.createElement("a");
  download.href = project.history_revision
    ? `/api/projects/${project.id}/history/${project.history_revision}/${encodeURIComponent(name)}`
    : `/api/projects/${project.id}/artifacts/${encodeURIComponent(name)}`;
  download.textContent = "下载";
  download.setAttribute("download", name);
  actions.append(download);
  row.append(label, actions);
  return row;
}

function textList(label, values) {
  const block = document.createElement("div");
  block.className = "labeled-list";
  const heading = document.createElement("h6");
  heading.textContent = label;
  const list = document.createElement("ul");
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  }
  block.append(heading, list);
  return block;
}

function labeledParagraph(label, value, className = "") {
  const block = document.createElement("div");
  block.className = `labeled-paragraph ${className}`.trim();
  const heading = document.createElement("h6");
  const copy = document.createElement("p");
  heading.textContent = label;
  copy.textContent = value || "待补充";
  block.append(heading, copy);
  return block;
}

function renderContributionOptions(project) {
  const container = element("contributionOptions");
  container.replaceChildren();
  const spec = project.state?.spec;
  const blueprint = project.state?.contribution_blueprint || {};
  if (!spec) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "完成范围界定后显示一份可修改的研究贡献方案。";
    container.append(empty);
    return;
  }
  const sections = [
    ["研究问题", blueprint.research_question || spec.question],
    ["核心贡献", blueprint.intended_contribution || spec.contribution],
    ["注意边界", (blueprint.boundaries || []).join(" ")],
    ["方法与论证框架", (blueprint.framework || []).join(" ")],
  ];
  for (const [title, value] of sections) {
    const card = document.createElement("article");
    card.className = "contribution-item";
    const heading = document.createElement("h5");
    const copy = document.createElement("p");
    heading.textContent = title;
    copy.textContent = value || "等待本阶段生成；可在上方写入修改要求后确认更新。";
    card.append(heading, copy);
    container.append(card);
  }
}

function renderEvidenceSummary(project) {
  const container = element("evidenceSummary");
  container.replaceChildren();
  const learning = project.state?.learning_plan;
  const evidence = project.state?.evidence || [];
  const failures = project.state?.source_failures || [];
  const artifacts = new Set(project.artifacts || []);

  const fileSection = document.createElement("section");
  fileSection.className = "evidence-section";
  const fileTitle = document.createElement("h5");
  fileTitle.textContent = "阶段文件";
  fileSection.append(fileTitle);
  const files = ["learning-plan.json", "papers.json", "evidence.json"].filter((name) => artifacts.has(name));
  if (!files.length) {
    const pending = document.createElement("p");
    pending.className = "muted";
    pending.textContent = "完成范围、检索与证据综合后，这里会出现可预览和下载的阶段文件。";
    fileSection.append(pending);
  }
  for (const name of files) fileSection.append(artifactActionRow(project, name));
  container.append(fileSection);

  if (learning) {
    const plan = document.createElement("section");
    plan.className = "evidence-section";
    const title = document.createElement("h5");
    title.textContent = "学习计划";
    const summary = document.createElement("p");
    summary.textContent = learning.target_name
      ? `按研究问题检索候选文献；目标场景：${learning.target_name}。候选条目不等于全文已核验。`
      : "按研究问题检索候选文献；候选条目不等于全文已核验。";
    plan.append(title, summary);
    if (learning.topic_queries?.length) {
      const queries = document.createElement("div");
      queries.className = "evidence-tags";
      for (const query of learning.topic_queries) {
        const tag = document.createElement("span");
        tag.textContent = query;
        queries.append(tag);
      }
      plan.append(queries);
    }
    for (const limit of learning.limits || []) {
      const note = document.createElement("small");
      note.textContent = limit;
      plan.append(note);
    }
    container.append(plan);
  }

  const documents = project.state?.documents || [];
  if (documents.length) {
    const local = document.createElement("section");
    local.className = "evidence-section";
    const title = document.createElement("h5");
    title.textContent = "本地材料文件";
    local.append(title);
    for (const document of documents) {
      const row = document.createElement("div");
      row.className = "evidence-file-row";
      const name = document.createElement("strong");
      const detail = document.createElement("span");
      name.textContent = document.name;
      detail.textContent = `${document.role === "results" ? "真实结果" : "参考资料"} · ${String(document.kind || "file").toUpperCase()} · ${document.excerpt || "等待提取内容"}`;
      row.append(name, detail);
      local.append(row);
    }
    container.append(local);
  }

  const ledger = document.createElement("section");
  ledger.className = "evidence-section evidence-records";
  const ledgerTitle = document.createElement("h5");
  ledgerTitle.textContent = `证据账本${evidence.length ? ` · ${evidence.length} 条` : ""}`;
  ledger.append(ledgerTitle);
  if (!evidence.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "尚无证据条目；完成检索或上传材料后生成。";
    ledger.append(empty);
  }
  for (const record of evidence) {
    const details = document.createElement("details");
    details.className = "evidence-entry";
    const heading = document.createElement("summary");
    heading.textContent = `${record.cite_key} · ${record.paper_title}`;
    const summary = document.createElement("p");
    summary.textContent = record.summary;
    details.append(heading, summary);
    if (record.supports?.length) details.append(textList("支持内容", record.supports));
    if (record.caveats?.length) details.append(textList("核验与限制", record.caveats));
    const locator = safeExternalUrl(record.source_locator);
    if (locator) {
      const link = document.createElement("a");
      link.href = locator;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "打开来源定位";
      details.append(link);
    } else if (record.source_locator) {
      const location = document.createElement("code");
      location.textContent = `本地定位：${record.source_locator}`;
      details.append(location);
    }
    ledger.append(details);
  }
  container.append(ledger);

  if (failures.length) {
    const failureSection = document.createElement("section");
    failureSection.className = "evidence-section source-failures";
    const title = document.createElement("h5");
    title.textContent = "未完成的来源";
    failureSection.append(title);
    for (const failure of failures) {
      const item = document.createElement("p");
      item.textContent = `${failure.source}：${failure.reason} ${failure.suggestion || ""}`.trim();
      failureSection.append(item);
    }
    container.append(failureSection);
  }
}

function mermaidLabel(value, fallback, limit = 56) {
  const label = String(value || fallback)
    .replace(/["`\n\r;]/g, " ")
    .replace(/[\[\](){}`<>|#]/g, " ")
    .replace(/(?:-->|---|-.->|==>)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (label || fallback).slice(0, limit);
}

function figureMermaid(entry, design = {}) {
  const composition = Array.isArray(entry.composition) ? entry.composition : [];
  const findComposition = (prefix, fallback) => {
    const value = composition.find((item) => String(item).startsWith(prefix));
    return mermaidLabel(value ? String(value).slice(prefix.length) : fallback, fallback, 34);
  };
  const question = findComposition("研究问题：", "研究问题与边界");
  const hypothesis = findComposition("假设节点：", "待检验假设");
  if (entry.kind === "mechanism") {
    const architecture = entry.architecture || {
      inputs: design.datasets?.length ? design.datasets : design.independent_variables,
      constraints: design.independent_variables,
      method: design.proposed_method,
      comparisons: design.baselines,
      outputs: design.metrics?.length ? design.metrics : design.dependent_variables,
      validation: design.falsification,
    };
    const input = mermaidLabel(architecture.inputs?.[0] || entry.data_requirements, "研究材料 / 数据", 30);
    const constraint = mermaidLabel(architecture.constraints?.[0], "纳入规则与预算", 30);
    const method = mermaidLabel(architecture.method || entry.job, "研究方法", 32);
    const comparison = mermaidLabel(architecture.comparisons?.[0], "对照条件", 30);
    const output = mermaidLabel(architecture.outputs?.[0], "主要结局指标", 30);
    const validation = mermaidLabel(architecture.validation || entry.boundary, "预注册失败条件", 34);
    return `flowchart LR
  subgraph IN["输入与约束"]
    I1["${input}"]
    I2["${constraint}"]
  end
  subgraph CORE["方法与比较"]
    M["${method}"]
    B["${comparison}"]
  end
  subgraph OUT["评价"]
    O["${output}"]
    V["${validation}"]
  end
  I1 --> M
  I2 --> M
  M --> O
  B --> O
  O -.-> V
  V -. 修订方案 .-> M
  classDef input fill:#f7f8fa,stroke:#7a8792,color:#26333d,stroke-width:1px;
  classDef method fill:#e8eff3,stroke:#587080,color:#1f3440,stroke-width:1px;
  classDef output fill:#f3f5f6,stroke:#687781,color:#27343c,stroke-width:1px;
  classDef pending fill:#ffffff,stroke:#8a969f,color:#4b5962,stroke-width:1px,stroke-dasharray:4 3;
  class I1,I2 input;
  class M,B method;
  class O output;
  class V pending;`;
  }
  if (entry.kind === "results") {
    const baseline = findComposition("对照：", "对照条件");
    const result = entry.status === "waiting_for_results" ? "结果材料待确认" : "已核验结果";
    return `flowchart LR
  R["${result}"] --> M["主要指标与区间"]
  B["${baseline}"] --> C["同预算比较"]
  M --> C
  C --> S["稳健性与失败案例"]
  classDef neutral fill:#f7f8fa,stroke:#77838c,color:#26333d,stroke-width:1px;
  class R,M,B,C,S neutral;`;
  }
  return `flowchart LR
  Q["${question}"] --> E["证据与纳入规则"]
  H["${hypothesis}"] -.-> D["研究设计"]
  E --> D
  D --> A["分析与对照"]
  A --> O["结论边界"]
  O -.-> F["失败条件"]
  classDef neutral fill:#f7f8fa,stroke:#77838c,color:#26333d,stroke-width:1px;
  classDef pending fill:#ffffff,stroke:#8a969f,color:#4b5962,stroke-width:1px,stroke-dasharray:4 3;
  class Q,E,D,A,O neutral;
  class H,F pending;`;
}

function renderMermaidPreview(container, source) {
  container.className = "diagram-preview";
  container.dataset.source = source;
  container.textContent = "正在绘制流程图…";
  if (!window.mermaid) {
    container.textContent = source;
    container.classList.add("is-fallback");
    return;
  }
  if (!state.mermaidInitialized) {
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        fontSize: "13px",
        primaryColor: "#f7f8fa",
        primaryTextColor: "#26333d",
        primaryBorderColor: "#77838c",
        lineColor: "#65737d",
        clusterBkg: "#ffffff",
        clusterBorder: "#c8d0d5",
      },
      flowchart: { curve: "linear", nodeSpacing: 34, rankSpacing: 48, padding: 12 },
    });
    state.mermaidInitialized = true;
  }
  const renderId = `scholaros-diagram-${++state.diagramSequence}`;
  window.mermaid.render(renderId, source).then(({ svg }) => {
    if (!container.isConnected || container.dataset.source !== source) return;
    container.innerHTML = svg;
  }).catch(() => {
    if (!container.isConnected || container.dataset.source !== source) return;
    container.textContent = source;
    container.classList.add("is-fallback");
  });
}

function buildTablePreview(entry) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-preview";
  const table = document.createElement("table");
  table.setAttribute("aria-label", entry.title || "表格预览");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const columns = Array.isArray(entry.columns) && entry.columns.length
    ? entry.columns
    : ["字段", "内容", "核验状态"];
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.textContent = String(column);
    headRow.append(cell);
  }
  head.append(headRow);
  table.append(head);

  const body = document.createElement("tbody");
  const sourceRows = Array.isArray(entry.rows) && entry.rows.length
    ? entry.rows
    : [entry.rows || "待按研究材料填写"];
  for (const rowValue of sourceRows.slice(0, 4)) {
    const row = document.createElement("tr");
    const values = Array.isArray(rowValue) ? rowValue : [rowValue];
    columns.forEach((_, index) => {
      const cell = document.createElement("td");
      cell.textContent = String(values[index] ?? (index === 0 ? "待填写" : "待核验"));
      row.append(cell);
    });
    body.append(row);
  }
  table.append(body);
  wrapper.append(table);
  const hint = document.createElement("small");
  hint.textContent = Array.isArray(entry.rows)
    ? "当前仅展示规划字段；真实数据需由研究材料填充。"
    : "当前为结构预览；执行研究后再填入真实记录。";
  wrapper.append(hint);
  return wrapper;
}

function renderFigureStory(project) {
  const container = element("figureStory");
  container.replaceChildren();
  const figures = project.state?.figure_story || [];
  const tables = project.state?.table_story || [];
  if (!figures.length && !tables.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "方法设计完成后生成图与表的用途、构成、视觉编码、数据要求、图注和边界。";
    container.append(empty);
    return;
  }
  const groups = [["图件规划", figures, "图"], ["表格规划", tables, "表"]];
  for (const [groupTitle, entries, prefix] of groups) {
    if (!entries.length) continue;
    const section = document.createElement("section");
    const title = document.createElement("h5");
    title.className = "story-group-title";
    title.textContent = groupTitle;
    const grid = document.createElement("div");
    grid.className = "story-card-grid";
    section.append(title, grid);
    for (const entry of entries) {
      const card = document.createElement("article");
      card.className = "figure-card";
      const top = document.createElement("div");
      const heading = document.createElement("h5");
      const stateBadge = document.createElement("span");
      heading.textContent = `${entry.id.replace(`${prefix === "图" ? "figure" : "table"}-`, `${prefix} `)} · ${entry.title}`;
      stateBadge.textContent = entry.status === "waiting_for_results" ? "等待真实结果" : "详细规划";
      top.append(heading, stateBadge);
      const job = document.createElement("p");
      job.textContent = entry.job || entry.purpose;
      card.append(top, job);
      if (prefix === "图") {
        const diagram = document.createElement("div");
        renderMermaidPreview(diagram, figureMermaid(entry, state.currentProject?.state?.design || {}));
        card.append(diagram);
      } else {
        card.append(buildTablePreview(entry));
      }
      const composition = entry.composition || entry.columns;
      if (composition?.length) card.append(textList(prefix === "图" ? "构成" : "字段", composition));
      if (entry.visual_encoding?.length) card.append(textList("视觉编码", entry.visual_encoding));
      if (entry.rows) card.append(textList("行设计", Array.isArray(entry.rows) ? entry.rows : [entry.rows]));
      card.append(labeledParagraph("所需数据", entry.data_requirements));
      card.append(labeledParagraph(prefix === "图" ? "图注草案" : "表注草案", entry.caption));
      card.append(labeledParagraph("表达边界", entry.boundary, "is-warning"));
      grid.append(card);
    }
    container.append(section);
  }
}

function renderProjectOverview(project) {
  const container = element("projectOverview");
  container.replaceChildren();
  const documents = project.state?.documents || [];
  const review = project.state?.final_review || project.state?.review;
  const spec = project.state?.spec || {};
  const rows = [
    ["研究问题", spec.question || project.idea],
    ["研究贡献", spec.contribution || "尚未形成"],
    ["材料", `${documents.length} 份（参考资料 ${documents.filter((item) => item.role !== "results").length}，真实结果 ${documents.filter((item) => item.role === "results").length}）`],
    ["论文与证据", `${(project.state?.papers || []).length} 篇候选论文，${(project.state?.evidence || []).length} 条证据记录，其中模型补充 ${project.state?.llm_discovery_count || 0} 篇`],
    ["图表规划", `${(project.state?.figure_story || []).length} 幅图，${(project.state?.table_story || []).length} 张表`],
    ["质量检查", review ? `${review.score}/100，${(review.findings || []).length} 个待处理或提示项` : "尚未完成"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    const heading = document.createElement("span");
    const copy = document.createElement("strong");
    heading.textContent = label;
    copy.textContent = value;
    row.append(heading, copy);
    container.append(row);
  }
  if (documents.length) {
    const materials = document.createElement("details");
    materials.className = "overview-materials";
    const heading = document.createElement("summary");
    heading.textContent = `上传材料（${documents.length}）`;
    materials.append(heading);
    for (const document of documents) {
      const item = document.createElement("p");
      item.textContent = `${document.role === "results" ? "结果" : "资料"} · ${document.name}`;
      materials.append(item);
    }
    container.append(materials);
  }
  const artifacts = project.artifacts || [];
  if (artifacts.length) {
    const section = document.createElement("section");
    section.className = "overview-artifacts";
    const title = document.createElement("strong");
    title.textContent = `全部材料与制品（${artifacts.length}）`;
    section.append(title);
    const list = document.createElement("div");
    list.className = "artifact-list";
    for (const name of artifacts) {
      list.append(artifactActionRow(project, name));
    }
    section.append(list);
    container.append(section);
  }
}

async function saveDecision(decisionType, itemId, value, comment) {
  if (!state.currentProject) return;
  try {
    state.currentProject = await api(`/api/projects/${state.currentProject.id}/decisions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision_type: decisionType, item_id: itemId, value, comment }),
    });
    renderProject(state.currentProject);
    toast("选择已保存在当前项目中。")
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveProjectConfiguration(event) {
  event.preventDefault();
  if (!state.currentProject) return;
  const button = element("saveConfiguration");
  const nextConfiguration = collectConfiguration("project");
  const draftMatches = state.pendingConfiguration
    && JSON.stringify(state.pendingConfiguration) === JSON.stringify(nextConfiguration);
  if (!draftMatches) {
    state.pendingConfiguration = nextConfiguration;
    button.textContent = "确认配置并更新后续";
    toast("配置修改草稿已保留；再次点击确认后，才会更新受影响的后续阶段。")
    return;
  }
  if (!window.confirm("确认应用配置修改，并使受影响的后续阶段进入待更新状态？")) return;
  setBusy(button, true, "正在保存…");
  try {
    state.currentProject = await api(`/api/projects/${state.currentProject.id}/configuration`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(nextConfiguration),
    });
    const shouldRerunGuided = state.currentProject.state?.guided
      && state.currentProject.state?.pending_clear_from;
    state.pendingConfiguration = null;
    renderProject(state.currentProject);
    element("projectConfigurationForm").hidden = true;
    button.dataset.originalLabel = "保存修改草稿";
    button.textContent = "保存修改草稿";
    if (shouldRerunGuided) {
      await api(`/api/projects/${state.currentProject.id}/run`, { method: "POST" });
      await refreshCurrentProject();
      startPolling();
      toast("配置已确认，正在按新配置重跑受影响阶段；完成后会再次暂停供你检查。")
    } else {
      toast("配置已确认；受影响的后续阶段已进入待更新状态。")
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function saveStageEdit() {
  if (!state.currentProject || !state.activeStage) return;
  const text = element("stageEditText").value.trim();
  const isScoping = state.activeStage === "scoping";
  const titleValue = isScoping ? element("stageScopeTitle").value.trim() : "";
  const keywordValues = isScoping
    ? element("stageScopeKeywords").value.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean)
    : [];
  const title = titleValue || null;
  const keywords = keywordValues.length ? keywordValues : null;
  if (text.length < 2 && !title && !keywords?.length) {
    toast("请填写修改要求，或修改论文题目/搜索关键词。", true);
    return;
  }
  const button = element("saveStageEdit");
  setBusy(button, true, "正在保存…");
  try {
    state.currentProject = await api(
      `/api/projects/${state.currentProject.id}/stage-edits/${state.activeStage}`,
      {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, title, keywords }),
      },
    );
    renderProject(state.currentProject);
    toast("修改草稿已保存，现有结果尚未改变。")
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function confirmStageEdit() {
  if (!state.currentProject || !state.activeStage) return;
  const stage = state.activeStage;
  if (!window.confirm(`确认应用“${stageWorkspaceCopy[stage][0]}”的修改，并更新该阶段及后续流程？`)) return;
  const button = element("confirmStageEdit");
  setBusy(button, true, "正在确认…");
  try {
    await api(`/api/projects/${state.currentProject.id}/stage-edits/${stage}/confirm`, { method: "POST" });
    await refreshCurrentProject();
    startPolling();
    toast("修改已确认，系统正在更新本阶段及后续流程。")
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

function renderArtifacts(project) {
  const container = element("artifactList");
  container.replaceChildren();
  const manuscriptNames = new Set(["paper-draft.md", "paper-draft.tex", "paper.md", "paper.tex", "ref.bib"]);
  const artifacts = (project.artifacts || []).filter((name) => manuscriptNames.has(name));
  if (!artifacts.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "研究稿起草后，这里会同时出现 Markdown 稿件与 LaTeX 原文本。";
    container.append(empty);
    return;
  }
  if (project.state?.pending_clear_from) {
    const warning = document.createElement("p");
    warning.className = "artifact-stale-warning";
    warning.textContent = `配置或选择已变化；从“${stageLabels[project.state.pending_clear_from] || project.state.pending_clear_from}”继续后，下游制品才会更新。`;
    container.append(warning);
  }
  for (const name of artifacts) {
    container.append(artifactActionRow(project, name));
  }
}

function renderReview(project) {
  const container = element("reviewSummary");
  container.replaceChildren();
  const review = project.state?.final_review || project.state?.review;
  if (!review) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "修订完成后显示结构、引用、方法和结果诚信检查。";
    container.append(empty);
    return;
  }
  const score = document.createElement("div");
  score.className = "review-score";
  const number = document.createElement("strong");
  number.textContent = review.score;
  const unit = document.createElement("span");
  const checksPassed = review.metrics?.quality_checks_passed;
  const checksTotal = review.metrics?.quality_checks;
  const checkSummary = Number.isInteger(checksPassed) && Number.isInteger(checksTotal)
    ? ` · ${checksPassed}/${checksTotal} 类检查`
    : "";
  unit.textContent = review.passed
    ? `/ 100 · 已通过${checkSummary}`
    : `/ 100 · 需人工处理${checkSummary}`;
  score.append(number, unit);
  container.append(score);
  const metrics = document.createElement("div");
  metrics.className = "review-metrics";
  for (const [key, value] of Object.entries(review.metrics || {})) {
    const item = document.createElement("span");
    item.textContent = `${key.replaceAll("_", " ")}：${value}`;
    metrics.append(item);
  }
  if (metrics.childElementCount) container.append(metrics);
  const findings = review.findings || [];
  if (findings.length) {
    const list = document.createElement("div");
    list.className = "finding-list";
    for (const finding of findings) {
      const item = document.createElement("article");
      item.className = `finding-item severity-${finding.severity || "info"}`;
      const top = document.createElement("div");
      const severity = document.createElement("span");
      const code = document.createElement("code");
      severity.textContent = { high: "高优先级", medium: "中优先级", low: "低优先级" }[finding.severity] || "检查提示";
      code.textContent = finding.code || "quality_check";
      top.append(severity, code);
      const problem = labeledParagraph("检查出的问题", finding.message);
      const revision = labeledParagraph("建议修改点", finding.suggestion || "由研究者核验并决定是否调整。", "is-revision");
      item.append(top, problem, revision);
      list.append(item);
    }
    container.append(list);
  } else {
    const passed = document.createElement("p");
    passed.className = "review-passed";
    passed.textContent = "当前规则未发现阻塞问题；正式交付前仍需研究者核验原文、数据和引用。";
    container.append(passed);
  }
}

async function interruptCurrentProject() {
  if (!state.currentProject) return;
  const projectId = state.currentProject.id;
  state.interruptingProjectId = projectId;
  stopPolling();
  renderProject(state.currentProject);
  try {
    await api(`/api/projects/${projectId}/interrupt`, { method: "POST" });
    toast("正在中断当前运行并保存断点…")
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      const project = await api(`/api/projects/${projectId}`);
      if (state.currentProject?.id !== projectId) return;
      if (!project.is_active) {
        state.interruptingProjectId = null;
        state.currentProject = project;
        renderProject(project);
        await loadProjects();
        toast("运行已中断，可以从当前断点继续。")
        return;
      }
    }
    startPolling();
  } catch (error) {
    state.interruptingProjectId = null;
    renderProject(state.currentProject);
    toast(error.message, true);
  }
}

async function runCurrentProject() {
  if (!state.currentProject) return;
  const button = element("runProject");
  const resumeConfirmedSearch = state.currentProject.stage === "searching"
    && state.currentProject.state?.search_plan_confirmed === true;
  const restart = state.currentProject.status !== "created" && !resumeConfirmedSearch;
  if (restart && !window.confirm("将保存当前历史版本，然后从头重做所有阶段。只想恢复上次进度，请取消并选择“从断点继续”。")) return;
  setBusy(button, true, restart ? "正在重新启动…" : "正在启动…");
  try {
    await api(`/api/projects/${state.currentProject.id}/run?restart=${restart}`, { method: "POST" });
    await refreshCurrentProject();
    startPolling();
    toast(restart ? "已从头重新运行，上传资料会被保留。" : "项目已开始运行。")
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function projectAction(action, buttonId) {
  if (!state.currentProject) return;
  const projectId = state.currentProject.id;
  const button = element(buttonId);
  setBusy(button, true, "正在处理…");
  try {
    await api(`/api/projects/${projectId}/${action}`, { method: "POST" });
    if (state.currentProject?.id === projectId) {
      await refreshCurrentProject();
      startPolling();
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

function renderHistoryTimeline(history) {
  const container = element("historyList");
  container.replaceChildren();
  if (!history.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "当前还没有已完成的阶段版本。";
    container.append(empty);
    return;
  }
  for (const revision of history) {
    const row = document.createElement("article");
    row.className = `history-item${state.historyRevision === revision.revision ? " is-selected" : ""}`;
    const copy = document.createElement("div");
    const heading = document.createElement("strong");
    const stageName = revision.stage === "completed"
      ? "工作流完成"
      : stageWorkspaceCopy[revision.stage]?.[0] || revision.reason;
    heading.textContent = stageName;
    const meta = document.createElement("span");
    meta.textContent = `${formatDate(revision.created_at)} · ${revision.reason}`;
    copy.append(heading, meta);
    const action = document.createElement("button");
    action.type = "button";
    action.className = "secondary-button";
    action.textContent = state.historyRevision === revision.revision ? "正在查看" : "查看此版本";
    action.disabled = state.historyRevision === revision.revision || state.currentProject?.is_active === true;
    action.addEventListener("click", () => viewHistoryRevision(revision.revision));
    row.append(copy, action);
    container.append(row);
  }
}

async function viewHistoryRevision(revision) {
  if (!state.currentProject || state.currentProject.is_active === true) return;
  const projectId = state.currentProject.id;
  try {
    const manifest = await api(`/api/projects/${projectId}/history/${revision}`);
    if (state.currentProject?.id !== projectId) return;
    const snapshot = {
      ...manifest.project,
      stage: manifest.stage ?? manifest.project?.stage,
      is_active: false,
      is_history: true,
      history_revision: manifest.revision,
      history_stage: manifest.stage,
      history_reason: manifest.reason,
      history_created_at: manifest.created_at,
      artifacts: Object.keys(manifest.artifacts || {}),
      history: state.currentProject.history || [],
    };
    state.historyRevision = revision;
    state.historyProject = snapshot;
    state.activeStageProjectId = projectId;
    state.activeStage = stages.includes(manifest.stage)
      ? manifest.stage
      : snapshot.stage === "completed" ? "revising" : snapshot.stage;
    renderProject(snapshot);
    element("projectWorkspace").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(error.message, true);
  }
}

function returnToCurrentVersion() {
  if (!state.currentProject) return;
  state.historyProject = null;
  state.historyRevision = null;
  state.activeStageProjectId = null;
  element("historyPanel").open = false;
  renderProject(state.currentProject);
  element("projectWorkspace").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function confirmSearchPlan() {
  if (!state.currentProject?.state?.search_confirmation_required) return;
  const button = element("confirmSearch");
  setBusy(button, true, "正在确认…");
  try {
    await api(`/api/projects/${state.currentProject.id}/confirm-search`, { method: "POST" });
    await refreshCurrentProject();
    startPolling();
    toast("检索计划已确认，正在访问所选论文源。")
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function rejectSearchPlan() {
  if (!state.currentProject?.state?.search_confirmation_required) return;
  const revisedIdea = window.prompt(
    "请修改研究说明；写清领域全称、缩写含义和希望结合的方法。取消则保留当前待确认计划。",
    state.currentProject.idea,
  );
  if (revisedIdea === null) return;
  const revisedLength = Array.from(revisedIdea.trim()).length;
  const limit = Number(element("idea").dataset.maxLength);
  if (revisedLength < 8 || revisedLength > limit) {
    toast(`研究说明需要 8—${limit.toLocaleString("en-US")} 个字符。`, true);
    return;
  }
  const button = element("rejectSearch");
  setBusy(button, true, "正在重新界定…");
  try {
    await api(`/api/projects/${state.currentProject.id}/reject-search`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ idea: revisedIdea.trim() }),
    });
    await refreshCurrentProject();
    startPolling();
    toast("旧检索计划已拒绝，正在按修改后的研究说明重新界定。")
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function deleteCurrentProject() {
  if (!state.currentProject || state.currentProject.is_active === true || state.historyProject) return;
  const project = state.currentProject;
  const title = project.title || project.idea;
  const confirmed = window.confirm(
    `确定永久删除项目“${title}”吗？\n\n项目记录、上传资料文本和全部论文制品都会删除，且不能撤销。`
  );
  if (!confirmed) return;
  const button = element("deleteProject");
  setBusy(button, true, "正在删除…");
  try {
    stopPolling();
    await api(`/api/projects/${project.id}`, { method: "DELETE" });
    state.currentProject = null;
    element("projectWorkspace").hidden = true;
    element("paperPreviewPanel").hidden = true;
    showView("workspace");
    await loadProjects();
    toast(`项目 ${project.id} 已删除。`);
  } catch (error) {
    try {
      await refreshCurrentProject();
      startPolling();
    } catch {
      state.currentProject = null;
      element("projectWorkspace").hidden = true;
      showView("workspace");
      await loadProjects().catch(() => {});
    }
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function uploadMore() {
  if (!state.currentProject || state.currentProject.status === "running") return;
  const button = element("uploadMore");
  const sourceFiles = [...element("moreSourceFiles").files];
  const resultFiles = [...element("moreResultFiles").files];
  if (!sourceFiles.length && !resultFiles.length) {
    toast("请先选择要上传的资料。", true);
    return;
  }
  setBusy(button, true, "正在上传…");
  try {
    await uploadFiles(state.currentProject.id, sourceFiles, "source");
    await uploadFiles(state.currentProject.id, resultFiles, "results");
    element("moreSourceFiles").value = "";
    element("moreResultFiles").value = "";
    await refreshCurrentProject();
    toast(`已上传 ${sourceFiles.length + resultFiles.length} 份资料。请点击“重新运行”。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
  }
}

async function previewArtifact(projectId, name, revision = null) {
  try {
    const path = revision
      ? `/api/projects/${projectId}/history/${revision}/${encodeURIComponent(name)}`
      : `/api/projects/${projectId}/artifacts/${encodeURIComponent(name)}`;
    const content = await api(path);
    const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    const isMarkdown = name.endsWith(".md");
    element("previewTitle").textContent = artifactLabels[name] || name;
    element("previewHint").textContent = isMarkdown
      ? "Markdown 已按纸张样式排版，可使用浏览器打印为 PDF。"
      : name.endsWith(".tex")
        ? "当前提供 LaTeX 源码即时预览；本机配置 TeX 编译器后可生成最终 PDF。"
        : "结构化内容预览。";
    element("paperPreviewDocument").hidden = !isMarkdown;
    element("paperPreviewSource").hidden = false;
    element("paperPreviewSourceLabel").textContent = isMarkdown ? "Markdown 原文" : "LaTeX 原文";
    element("printPreview").hidden = !isMarkdown;
    if (isMarkdown) renderMarkdownDocument(text, element("paperPreviewDocument"));
    element("paperPreview").textContent = text;
    element("paperPreviewPanel").hidden = false;
    element("paperPreviewPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(error.message, true);
  }
}

function renderMarkdownDocument(markdown, container) {
  container.replaceChildren();
  const lines = markdown.split(/\r?\n/);
  let list = null;
  let listType = null;
  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index];
    const line = rawLine.trim();
    if (!line) {
      list = null;
      listType = null;
      continue;
    }
    if (/^```/.test(line)) {
      const language = line.slice(3).trim();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        codeLines.push(lines[index]);
        index += 1;
      }
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      if (language) code.dataset.language = language;
      code.textContent = codeLines.join("\n");
      pre.append(code);
      container.append(pre);
      list = null;
      listType = null;
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && isMarkdownTableDivider(lines[index + 1])) {
      const headers = splitMarkdownTableRow(line);
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headingRow = document.createElement("tr");
      for (const value of headers) {
        const cell = document.createElement("th");
        appendInlineMarkdown(cell, value);
        headingRow.append(cell);
      }
      thead.append(headingRow);
      table.append(thead);
      const tbody = document.createElement("tbody");
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = document.createElement("tr");
        for (const value of splitMarkdownTableRow(lines[index])) {
          const cell = document.createElement("td");
          appendInlineMarkdown(cell, value);
          row.append(cell);
        }
        tbody.append(row);
        index += 1;
      }
      index -= 1;
      table.append(tbody);
      container.append(table);
      list = null;
      listType = null;
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const node = document.createElement(`h${heading[1].length}`);
      appendInlineMarkdown(node, heading[2]);
      container.append(node);
      list = null;
      listType = null;
      continue;
    }
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(line)) {
      container.append(document.createElement("hr"));
      list = null;
      listType = null;
      continue;
    }
    const bullet = /^([-*+]\s+|\d+[.)]\s+)(.+)$/.exec(line);
    if (bullet) {
      const nextListType = /^\d/.test(bullet[1]) ? "ol" : "ul";
      if (!list || listType !== nextListType) {
        list = document.createElement(nextListType);
        listType = nextListType;
        container.append(list);
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, bullet[2]);
      list.append(item);
      continue;
    }
    const quote = /^>\s*(.+)$/.exec(line);
    const node = document.createElement(quote ? "blockquote" : "p");
    appendInlineMarkdown(node, quote ? quote[1] : line);
    container.append(node);
    list = null;
    listType = null;
  }
}

function splitMarkdownTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isMarkdownTableDivider(line) {
  const cells = splitMarkdownTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function appendInlineMarkdown(container, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|\$[^$]+\$|\*[^*]+\*|_[^_]+_)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) container.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith("`")) {
      node = document.createElement("code");
      node.textContent = token.slice(1, -1);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      node = document.createElement("strong");
      node.textContent = token.slice(2, -2);
    } else if (token.startsWith("[")) {
      const parts = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(token);
      node = document.createElement("a");
      node.textContent = parts[1];
      node.href = parts[2];
      node.rel = "noopener noreferrer";
    } else if (token.startsWith("$")) {
      node = document.createElement("span");
      node.className = "math-expression";
      node.textContent = token.slice(1, -1);
    } else {
      node = document.createElement("em");
      node.textContent = token.slice(1, -1);
    }
    container.append(node);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
}

function safeExternalUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function stopLocalRunPolling() {
  window.clearTimeout(state.runPollTimer);
  state.runPollTimer = null;
}

function localRunEntries() {
  return [...element("localRunFiles").files].map((file) => ({
    file, path: (file.webkitRelativePath || file.name || "").split("/").slice(1).join("/"),
  })).filter((entry) => entry.path && !entry.path.split("/").some((part) => part.startsWith(".")));
}

function parseMetricColumns(raw) {
  return raw.split(",").map((part) => part.trim()).filter(Boolean);
}

function bindLocalDraft(run) {
  state.localRunDraftId = run.id;
  element("startLocalRun").disabled = false;
  element("deleteLocalRunDraft").hidden = false;
  if (run.command) element("localRunCommand").value = run.command;
  if (run.csv_path) element("localRunCsvPath").value = run.csv_path;
  if (run.metric_columns && run.metric_columns.length) element("localRunColumns").value = run.metric_columns.join(", ");
}

function clearLocalDraft(resetFiles) {
  state.localRunDraftId = null;
  element("startLocalRun").disabled = true;
  element("deleteLocalRunDraft").hidden = true;
  if (resetFiles) {
    element("localRunFiles").value = "";
    element("clearLocalRunFiles").hidden = true;
  }
}

async function createLocalRun(event) {
  event.preventDefault();
  const button = element("createLocalRun");
  if (!element("localRunFiles").files.length) return toast("请先选择代码目录。", true);
  const entries = localRunEntries();
  if (!entries.length || entries.reduce((total, { file }) => total + file.size, 0) > 2 * 1024 * 1024 * 1024)
    return toast("目录须至少包含一个普通文件，总量不超过 2 GiB。", true);
  const body = new FormData();
  body.append("note", element("localRunNote").value.trim());
  for (const { file, path } of entries) {
    body.append("paths", path);
    body.append("files", file, file.name);
  }
  setBusy(button, true, "正在上传…");
  element("localRunFeedback").textContent = "";
  try {
    const run = await api("/api/runs", { method: "POST", body });
    bindLocalDraft(run);
    state.selectedLocalRunId = run.id;
    state.localRunLogSize = 0;
    element("localRunFiles").value = "";
    element("clearLocalRunFiles").hidden = true;
    element("localRunFeedback").textContent = `已建立运行记录 ${run.id.slice(0, 10)}，确认配置后即可启动。`;
    await refreshLocalRuns();
    loadProjects().catch(() => {});
  } catch (error) {
    element("localRunFeedback").textContent = error.message;
    toast(error.message, true);
  } finally { setBusy(button, false, ""); }
}

async function suggestLocalRun(useModel) {
  const runId = state.localRunDraftId;
  if (!runId) return toast("请先上传代码目录。", true);
  const button = element("suggestLocalRun");
  setBusy(button, true, "正在识别…");
  element("localRunSuggestFeedback").textContent = "";
  try {
    const response = await api(`/api/runs/${runId}/suggest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reference_command: element("localRunReference").value.trim(),
        allow_model: Boolean(useModel),
      }),
    });
    if (response.command) element("localRunCommand").value = response.command;
    element("localRunCsvPath").value = response.csv_path || "";
    element("localRunColumns").value = (response.metric_columns || []).join(", ");
    element("localRunSuggestFeedback").textContent = response.source === "model"
      ? "已按 LLM 建议填入，请检查后再启动。" : "已按目录自动识别，可手动修改。";
  } catch (error) {
    element("localRunSuggestFeedback").textContent = error.message;
    toast(error.message, true);
  } finally { setBusy(button, false, ""); }
}

async function startLocalRun() {
  const runId = state.localRunDraftId;
  if (!runId) return toast("请先上传代码目录。", true);
  if (!element("localRunConsent").checked) return toast("请先确认宿主机执行风险。", true);
  const command = element("localRunCommand").value.trim();
  if (!command) return toast("请填写执行命令。", true);
  const button = element("startLocalRun");
  setBusy(button, true, "正在启动…");
  let started = false;
  try {
    await api(`/api/runs/${runId}/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command,
        csv_path: element("localRunCsvPath").value.trim(),
        metric_columns: parseMetricColumns(element("localRunColumns").value),
        acknowledge_risk: element("localRunConsent").checked,
      }),
    });
    started = true;
    state.selectedLocalRunId = runId;
    state.localRunLogSize = 0;
    element("localRunFeedback").textContent = `已启动运行 ${runId.slice(0, 10)}。`;
    await refreshLocalRuns();
  } catch (error) {
    element("localRunFeedback").textContent = error.message;
    toast(error.message, true);
  } finally {
    setBusy(button, false, "");
    if (started) clearLocalDraft(false);
  }
}

function renderLocalRunTree(runId, files) {
  const area = element("localRunTree");
  const preview = element("localRunPreview");
  area.dataset.runId = runId;
  area.replaceChildren();
  preview.hidden = true;
  preview.replaceChildren();
  const root = { folders: new Map(), files: [] };
  for (const path of files) {
    const parts = path.split("/");
    let node = root;
    for (const folder of parts.slice(0, -1)) {
      if (!node.folders.has(folder)) node.folders.set(folder, { folders: new Map(), files: [] });
      node = node.folders.get(folder);
    }
    node.files.push({ name: parts.at(-1), path });
  }
  function appendFiles(node, target) {
    for (const [name, child] of [...node.folders].sort(([a], [b]) => a.localeCompare(b))) {
      const folder = document.createElement("details");
      folder.open = true;
      const summary = document.createElement("summary");
      summary.textContent = `▸ ${name}`;
      folder.append(summary);
      appendFiles(child, folder);
      target.append(folder);
    }
    for (const file of node.files.sort((a, b) => a.name.localeCompare(b.name))) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `◦ ${file.name}`;
      button.title = file.path;
      button.addEventListener("click", async () => {
        area.querySelectorAll("button").forEach((item) => item.classList.remove("is-active"));
        button.classList.add("is-active");
        preview.hidden = false;
        preview.textContent = "正在读取文件…";
        try {
          const result = await api(`/api/runs/${runId}/files?path=${encodeURIComponent(file.path)}`);
          if (state.selectedLocalRunId !== runId || !button.classList.contains("is-active")) return;
          const heading = document.createElement("strong");
          heading.textContent = result.path;
          const source = document.createElement("pre");
          source.textContent = result.content;
          preview.replaceChildren(heading, source);
        } catch (error) {
          if (state.selectedLocalRunId === runId && button.classList.contains("is-active")) preview.textContent = error.message;
        }
      });
      target.append(button);
    }
  }
  appendFiles(root, area);
  if (!files.length) area.textContent = "该记录没有可显示的代码文件。";
}

async function refreshLocalRuns() {
  const requestId = ++state.runRequestId;
  const runs = await api("/api/runs");
  if (requestId !== state.runRequestId) return;
  const container = element("localRunList");
  container.replaceChildren();
  if (!runs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "还没有本机运行记录。";
    container.append(empty);
  }
  for (const run of runs) {
    const card = document.createElement("article");
    card.className = "experiment-run";
    const heading = document.createElement("h5");
    heading.textContent = `${run.note || run.id.slice(0, 10)} · ${run.status} · ${formatDate(run.created_at)}`;
    card.append(heading);
    const line = document.createElement("p");
    line.textContent = run.command
      ? `${run.command}${run.exit_code === null || run.exit_code === undefined ? "" : ` · 退出码 ${run.exit_code}`}`
      : "尚未配置运行命令";
    card.append(line);
    const actions = document.createElement("div");
    actions.className = "run-actions";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "secondary-button";
    open.textContent = { created: "配置并启动", failed: "查看 / 重试" }[run.status] || "查看监控";
    open.addEventListener("click", () => {
      state.selectedLocalRunId = run.id;
      state.localRunLogSize = 0;
      if (run.status === "created" || run.status === "failed") bindLocalDraft(run);
      refreshLocalRuns().catch(() => {});
    });
    actions.append(open);
    if (run.status === "running") {
      const stop = document.createElement("button");
      stop.type = "button";
      stop.className = "danger-button";
      stop.textContent = "停止";
      stop.addEventListener("click", async () => {
        try { await api(`/api/runs/${run.id}/stop`, { method: "POST" }); }
        catch (error) { toast(error.message, true); }
        refreshLocalRuns().catch(() => {});
      });
      actions.append(stop);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger-button";
    remove.textContent = "删除";
    remove.addEventListener("click", async () => {
      const label = run.note ? `（${run.note}）` : "";
      const linked = run.project_id ? `此记录已关联项目 ${run.project_id}，删除后项目仍保留研究说明，但无法从项目回看本次上传的原始代码。` : "";
      if (!window.confirm(`删除该运行记录${label}？代码目录与日志将一并删除。${linked}`)) return;
      try { await api(`/api/runs/${run.id}`, { method: "DELETE" }); }
      catch (error) { toast(error.message, true); return; }
      if (state.selectedLocalRunId === run.id) {
        state.selectedLocalRunId = null;
        state.localRunLogSize = 0;
      }
      if (state.localRunDraftId === run.id) clearLocalDraft(true);
      refreshLocalRuns().catch(() => {});
      loadProjects().catch(() => {});
    });
    actions.append(remove);
    card.append(actions);
    container.append(card);
  }
  if (!runs.some((run) => run.id === state.selectedLocalRunId)) {
    state.selectedLocalRunId = runs[0]?.id || null;
    state.localRunLogSize = 0;
  }
  await refreshLocalRunDetail();
  if (requestId !== state.runRequestId || !element("runsView").classList.contains("is-active")) return;
  stopLocalRunPolling();
  if (runs.some((run) => run.status === "running")) {
    state.runPollTimer = window.setTimeout(() => refreshLocalRuns().catch(() => {}), 2000);
  }
}

async function refreshLocalRunDetail() {
  const requestId = ++state.runDetailRequestId;
  const detail = element("localRunDetail");
  const runId = state.selectedLocalRunId;
  if (!runId) {
    detail.hidden = true;
    element("localRunPaper").hidden = true;
    state.localPaperRunId = null;
    element("localRunTree").textContent = "上传目录后，这里会显示文件树。";
    element("localRunTree").dataset.runId = "";
    element("localRunPreview").hidden = true;
    return;
  }
  let run;
  const after = state.localRunLogSize;
  try { run = await api(`/api/runs/${runId}?after_bytes=${after}&tail_bytes=20000`); }
  catch (error) { detail.hidden = true; return; }
  if (requestId !== state.runDetailRequestId || state.selectedLocalRunId !== runId) return;
  if (element("localRunTree").dataset.runId !== runId) renderLocalRunTree(runId, run.files || []);
  if (state.localPaperRunId !== runId) {
    state.localPaperRunId = runId;
    loadLocalPaper(runId).catch(() => {});
  }
  element("localPaperOpenProject").hidden = !run.project_id;
  element("localPaperOpenProject").dataset.projectId = run.project_id || "";
  detail.hidden = false;
  if (detail.dataset.runId !== runId) {
    detail.replaceChildren();
    detail.dataset.runId = runId;
  }
  let heading = detail.querySelector("h4");
  if (!heading) {
    heading = document.createElement("h4");
    detail.append(heading);
  }
  heading.textContent = `运行：${run.note || run.id.slice(0, 10)}（${run.status}）`;
  let hint = detail.querySelector(".local-run-hint");
  if (!hint) {
    hint = document.createElement("p");
    hint.className = "muted local-run-hint";
    detail.append(hint);
  }
  hint.textContent = {
    created: "请检查执行命令、指标文件和宿主机风险确认，然后点击上方的开始运行。",
    running: "正在本机运行；下方持续更新终端输出及指标。需要中止请点击记录中的“停止”。",
    completed: "运行已结束；请核对指标与终端输出，再判断结果是否可用。",
    failed: `运行未成功${run.error ? `：${run.error}` : "；请检查命令与终端输出，可在记录中选择重试"}。`,
  }[run.status] || "请查看本次运行的指标和日志。";

  let tableArea = detail.querySelector(".run-metrics-area");
  if (!tableArea) {
    tableArea = document.createElement("div");
    tableArea.className = "run-metrics-area";
    detail.append(tableArea);
  }
  tableArea.replaceChildren();
  if (run.metrics && run.metrics.available && (run.metrics.summary || []).length) {
    const table = document.createElement("table");
    table.className = "metrics-table";
    const head = document.createElement("thead");
    const headerRow = document.createElement("tr");
    for (const label of ["指标", "样本数", "均值", "最小值", "最大值", "最新值"]) {
      const th = document.createElement("th"); th.textContent = label; headerRow.append(th);
    }
    head.append(headerRow);
    table.append(head);
    const body = document.createElement("tbody");
    for (const item of run.metrics.summary) {
      const row = document.createElement("tr");
      for (const value of [item.column, item.count, roundMetric(item.mean), roundMetric(item.min), roundMetric(item.max), roundMetric(item.last)]) {
        const td = document.createElement("td"); td.textContent = String(value); row.append(td);
      }
      body.append(row);
    }
    table.append(body);
    tableArea.append(table);
  } else {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = run.metrics?.error || (run.metrics?.available
      ? "指标文件中还没有可统计的数值列。" : `尚未找到指标文件：${run.csv_path || "未配置"}`);
    tableArea.append(note);
  }

  let caption = detail.querySelector(".console-caption");
  if (!caption) {
    caption = document.createElement("p");
    caption.className = "muted console-caption";
    caption.textContent = "终端输出（stdout + stderr）：";
    detail.append(caption);
  }
  let pre = detail.querySelector(".console-log");
  if (!pre) {
    pre = document.createElement("pre");
    pre.className = "console-log";
    detail.append(pre);
  }
  if (after === 0) {
    pre.textContent = run.output || "";
    pre.scrollTop = pre.scrollHeight;
  } else if (run.output) {
    pre.textContent += run.output;
    pre.scrollTop = pre.scrollHeight;
  }
  state.localRunLogSize = run.log_size || after;
}

function fillLocalPaperPlan(plan) {
  if (!plan) {
    element("localPaperPlan").hidden = true;
    element("localPaperTransfer").disabled = true;
    element("localResearchIdea").value = "";
    element("localPaperReportDetails").hidden = true;
    return;
  }
  element("localResearchIdea").value = plan.research_idea || [
    `研究主题：${plan.title || "待确认"}`,
    `研究问题：${plan.direction || "待确认"}`,
    `技术方案：${plan.method || "待确认"}`,
    `数据与对照：${(plan.datasets || []).join("、")}；${(plan.baselines || []).join("、")}`,
    `指标与边界：${(plan.metrics || []).join("、")}；${(plan.limitations || []).join("、")}`,
  ].join("\n\n");
  element("localPaperPlan").hidden = false;
  element("localPaperTransfer").disabled = !plan.code_digest;
  const reports = element("localPaperReportDetails");
  reports.hidden = !plan.module_reports?.length;
  if (!reports.hidden) element("localPaperReportPre").textContent = plan.module_reports
    .map((item) => `第 ${item.chunk} 组代码${item.verified === false ? "（缺少可核对行号，待核实）" : ""}：\n${item.text}`).join("\n\n");
  const coverage = `已读取 ${plan.read_files?.[0] || "README.md"}${plan.source_bytes ? `（${Math.round(plan.source_bytes / 1024)} KiB）` : ""}${plan.ignored_data_files ? `；目录中另有 ${plan.ignored_data_files} 个文件未读取` : ""}。`;
  const fallbackNote = plan.idea_source === "model_text_fallback"
    ? "结构化字段输出未成功，已保留模型生成的详细研究正文；标题、数据集、基线和指标字段仍需你确认。"
    : plan.idea_source === "module_reports_fallback"
      ? "模型最终综合未成功，已从 README 分析报告中本地整理研究说明；请重点检查技术路径并修改标题、数据集、基线和指标。"
    : "";
  element("localPaperFeedback").textContent = !plan.code_digest
    ? "这是旧版分析结果；请点击上方按钮重新按 README.md 分析后再带入项目。"
    : coverage + (plan.unanchored_chunks?.length ? `其中 ${plan.unanchored_chunks.length} 组缺少可核对行号，请重点核实。` : "") + fallbackNote + "请检查技术路径再带入项目。";
}

async function loadLocalPaper(runId) {
  const requestId = ++state.localPaperRequestId;
  element("localRunPaper").hidden = false;
  element("localPaperPlan").hidden = true;
  element("localPaperOutput").hidden = true;
  element("localPaperFeedback").textContent = "";
  try {
    const paper = await api(`/api/runs/${runId}/paper`);
    if (state.selectedLocalRunId !== runId || requestId !== state.localPaperRequestId) return;
    const progress = await api(`/api/runs/${runId}/paper/status`);
    if (state.selectedLocalRunId !== runId || requestId !== state.localPaperRequestId) return;
    fillLocalPaperPlan(["idle", "complete"].includes(progress.stage) ? paper.plan : null);
    if (progress.stage === "failed") {
      element("localPaperFeedback").textContent = `上次代码分析未完成（${progress.done}/${progress.total}）：${progress.error || "请重新分析"}。此前的方案未作为新结果展示。`;
    } else if (!paper.plan && ["idle", "complete"].includes(progress.stage)) {
      try {
        const scope = await api(`/api/runs/${runId}/paper/scope`);
        if (state.selectedLocalRunId === runId && requestId === state.localPaperRequestId) {
          element("localPaperFeedback").textContent = `仅读取 README.md（${Math.round(scope.source_bytes / 1024)} KiB），目录中 ${scope.ignored_data_files} 个其他文件不会发送模型。`;
        }
      } catch (error) {
        if (state.selectedLocalRunId === runId && requestId === state.localPaperRequestId) element("localPaperFeedback").textContent = error.message;
      }
    }
    const archive = element("localPaperOutput");
    archive.hidden = !paper.manuscript;
    const links = element("localPaperDownloads");
    links.replaceChildren();
    for (const name of paper.files || []) {
      const link = document.createElement("a");
      link.href = `/api/runs/${runId}/paper/files/${name}`;
      link.textContent = name;
      links.append(link);
    }
  } catch (error) {
    if (state.selectedLocalRunId === runId) element("localPaperFeedback").textContent = error.message;
  }
}

async function analyzeLocalPaper() {
  const runId = state.selectedLocalRunId;
  if (!runId) return toast("请先选择本机运行记录。", true);
  if (!element("localPaperShareCode").checked) return toast("请先确认允许向模型发送 README.md。", true);
  const button = element("localPaperAnalyze");
  state.localPaperRequestId += 1;
  setBusy(button, true, "正在分析…");
  fillLocalPaperPlan(null);
  element("localPaperFeedback").textContent = "正在阅读 README.md 并整理研究问题、技术方案和验证设计。";
  const requestId = state.localPaperRequestId;
  const progressTimer = window.setInterval(async () => {
    try {
      const progress = await api(`/api/runs/${runId}/paper/status`);
      if (state.selectedLocalRunId !== runId || requestId !== state.localPaperRequestId) return;
      const label = { scanning: "正在查找 README.md", modules: "正在分析 README.md", synthesis: "正在综合研究方案", complete: "分析完成", failed: "分析未完成" }[progress.stage];
      if (label) element("localPaperFeedback").textContent = `${label}${progress.total ? ` · ${progress.done}/${progress.total}` : ""}${progress.error ? `：${progress.error}` : ""}`;
    } catch { /* 临时轮询失败不打断正在进行的模型请求。 */ }
  }, 1800);
  try {
    const plan = await api(`/api/runs/${runId}/paper/plan`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: element("localPaperNote").value.trim(), allow_model: true }),
    });
    if (state.selectedLocalRunId === runId && requestId === state.localPaperRequestId) fillLocalPaperPlan(plan);
  } catch (error) {
    if (state.selectedLocalRunId === runId && requestId === state.localPaperRequestId) {
      element("localPaperFeedback").textContent = `代码分析未完成：${error.message}。此前的方案未作为新结果展示。`;
      toast(error.message, true);
    }
  } finally { window.clearInterval(progressTimer); setBusy(button, false, ""); }
}

function transferLocalPaper() {
  const idea = element("localResearchIdea").value.trim();
  if (!state.selectedLocalRunId || idea.length < 8) return toast("请先检查并填写研究想法。", true);
  state.pendingLocalRunId = state.selectedLocalRunId;
  element("idea").value = idea;
  element("outputLanguage").value = "en";
  element("sceneType").value = "journal";
  element("latexTemplate").value = "ieee_journal";
  updateIdeaLength();
  element("sourceRunBanner").hidden = false;
  element("sourceRunLabel").textContent = `研究想法来自本机运行 ${state.pendingLocalRunId.slice(0, 10)}；请检查项目配置和资料后再创建。`;
  showView("projects");
  element("createForm").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderManuscriptSections(project) {
  const panel = element("manuscriptSectionsPanel");
  const available = !project.is_history && project.stage === "completed" && (project.artifacts || []).includes("paper.md");
  panel.hidden = !available;
  if (!available) { state.sectionViewKey = null; return; }
  const key = `${project.id}:${project.updated_at}`;
  if (state.sectionViewKey === key) return;
  state.sectionViewKey = key;
  state.sectionData = null;
  state.selectedSectionIndex = null;
  state.sectionRequestId += 1;
  const list = element("manuscriptSectionList");
  list.textContent = "正在加载章节…";
  element("manuscriptSectionEditor").hidden = true;
  api(`/api/projects/${project.id}/manuscript/sections`).then((data) => {
    if (state.sectionViewKey !== key) return;
    state.sectionData = data;
    list.replaceChildren();
    for (const section of data.sections) {
      const card = document.createElement("article");
      card.className = "manuscript-section-card";
      const title = document.createElement("h5");
      title.textContent = section.heading;
      const text = document.createElement("pre");
      text.textContent = section.content || "本节暂无正文";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary-button";
      button.textContent = "修改这一节";
      button.disabled = project.is_active === true || project.status === "running";
      button.addEventListener("click", () => {
        state.selectedSectionIndex = section.index;
        state.sectionRequestId += 1;
        element("manuscriptSectionTitle").textContent = `修改：${section.heading}`;
        element("manuscriptSectionInstruction").value = "";
        element("manuscriptSectionCandidate").value = "";
        element("manuscriptSectionCandidateLabel").hidden = true;
        element("applyManuscriptSection").hidden = true;
        element("manuscriptSectionFeedback").textContent = "";
        element("manuscriptSectionEditor").hidden = false;
        element("manuscriptSectionEditor").scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      card.append(title, text, button);
      list.append(card);
    }
  }).catch((error) => { if (state.sectionViewKey === key) list.textContent = error.message; });
}

async function suggestManuscriptSection() {
  if (!state.currentProject || !state.sectionData || state.selectedSectionIndex == null) return;
  const instruction = element("manuscriptSectionInstruction").value.trim();
  if (instruction.length < 2) return toast("请填写本节的修改要求。", true);
  const projectId = state.currentProject.id;
  const index = state.selectedSectionIndex;
  const baseHash = state.sectionData.base_hash;
  const requestId = ++state.sectionRequestId;
  const button = element("suggestManuscriptSection");
  setBusy(button, true, "正在改写…");
  try {
    const response = await api(`/api/projects/${projectId}/manuscript/sections/suggest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index, base_hash: baseHash, instruction }),
    });
    if (state.currentProject?.id !== projectId || state.selectedSectionIndex !== index
        || state.sectionData?.base_hash !== baseHash || requestId !== state.sectionRequestId
        || element("manuscriptSectionInstruction").value.trim() !== instruction) return;
    element("manuscriptSectionCandidate").value = response.candidate;
    element("manuscriptSectionCandidateLabel").hidden = false;
    element("applyManuscriptSection").hidden = false;
    element("manuscriptSectionFeedback").textContent = "仅生成候选；请检查后再确认替换。";
  } catch (error) {
    if (state.currentProject?.id === projectId && state.selectedSectionIndex === index) {
      element("manuscriptSectionFeedback").textContent = error.message;
      toast(error.message, true);
    }
  } finally { setBusy(button, false, ""); }
}

async function applyManuscriptSection() {
  if (!state.currentProject || !state.sectionData || state.selectedSectionIndex == null) return;
  const content = element("manuscriptSectionCandidate").value.trim();
  if (content.length < 20) return toast("本节修改候选过短。", true);
  const projectId = state.currentProject.id;
  const index = state.selectedSectionIndex;
  const baseHash = state.sectionData.base_hash;
  if (!window.confirm("确认只替换这一节？旧稿会保留在项目历史中，LaTeX 与质量检查将更新。")) return;
  const button = element("applyManuscriptSection");
  setBusy(button, true, "正在保存…");
  try {
    await api(`/api/projects/${projectId}/manuscript/sections/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index, base_hash: baseHash, content,
        instruction: element("manuscriptSectionInstruction").value.trim() }),
    });
    if (state.currentProject?.id !== projectId || state.selectedSectionIndex !== index) return;
    state.sectionViewKey = null;
    await refreshCurrentProject();
    toast(state.currentProject?.state?.section_review_blocked
      ? "已更新所选章节；质量检查未通过，交付前请核对问题。"
      : "已更新所选章节，其余章节保持不变。请复核质量检查。");
  } catch (error) {
    if (state.currentProject?.id === projectId && state.selectedSectionIndex === index) {
      element("manuscriptSectionFeedback").textContent = error.message;
      toast(error.message, true);
    }
  } finally { setBusy(button, false, ""); }
}

function roundMetric(value) {
  return typeof value === "number" ? Number(value.toFixed(4)) : value;
}

function formatDate(value) {
  if (!value) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function bindEvents() {
  document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  document.querySelectorAll("#stageList [data-stage]").forEach((item) => {
    item.querySelector("button").addEventListener("click", () => selectWorkspaceStage(item.dataset.stage));
  });
  element("createForm").addEventListener("submit", createProject);
  element("experimentForm").addEventListener("submit", runExperiment);
  element("experimentProject").addEventListener("change", () => {
    state.uploadedFolderProjectId = null;
    state.uploadedFolderId = null;
    clearOptimization();
    element("experimentFolder").value = "";
    element("experimentCommand").value = "";
    element("experimentBaselineCommand").value = "";
    renderFolderChoices([]);
    refreshExperiments().catch((error) => toast(error.message, true));
  });
  element("experimentInputMode").addEventListener("change", updateExperimentInputMode);
  element("experimentFolder").addEventListener("change", () => {
    state.uploadedFolderProjectId = null;
    state.uploadedFolderId = null;
    clearOptimization();
    element("experimentCommand").value = "";
    element("experimentBaselineCommand").value = "";
    renderFolderChoices(selectedFolderEntries().map((entry) => entry.path));
  });
  element("experimentMainFile").addEventListener("change", () => {
    clearOptimization();
    element("experimentCommand").value = folderCommand(element("experimentMainFile").value);
  });
  element("experimentOptimizeMode").addEventListener("change", () => {
    updateOptimizationMode();
  });
  element("experimentOptimizationGoal").addEventListener("input", clearOptimization);
  element("experimentAlgorithmFiles").addEventListener("change", clearOptimization);
  element("generateOptimization").addEventListener("click", generateOptimization);
  element("experimentBaselineFile").addEventListener("change", () => {
    const baseline = element("experimentBaselineFile").value;
    element("experimentBaselineCommandRow").hidden = !baseline;
    element("experimentBaselineCommand").value = folderCommand(baseline);
  });
  element("suggestExperimentCommand").addEventListener("click", suggestExperimentCommand);
  element("refreshExperiment").addEventListener("click", () => refreshExperiments().catch((error) => toast(error.message, true)));
  element("localRunForm").addEventListener("submit", createLocalRun);
  element("localRunFiles").addEventListener("change", () => {
    clearLocalDraft(false);
    element("clearLocalRunFiles").hidden = !element("localRunFiles").files.length;
  });
  element("clearLocalRunFiles").addEventListener("click", () => clearLocalDraft(true));
  element("deleteLocalRunDraft").addEventListener("click", async () => {
    const runId = state.localRunDraftId;
    if (!runId) return;
    if (!window.confirm("删除这条刚上传的运行目录？其代码与日志将一并删除。")) return;
    try { await api(`/api/runs/${runId}`, { method: "DELETE" }); }
    catch (error) { toast(error.message, true); return; }
    if (state.selectedLocalRunId === runId) {
      state.selectedLocalRunId = null;
      state.localRunLogSize = 0;
    }
    clearLocalDraft(true);
    element("localRunFeedback").textContent = "已删除上传目录。";
    refreshLocalRuns().catch(() => {});
    loadProjects().catch(() => {});
  });
  element("startLocalRun").addEventListener("click", startLocalRun);
  element("suggestLocalRun").addEventListener("click", () => suggestLocalRun(element("localRunAllowModel").checked));
  element("refreshLocalRuns").addEventListener("click", () => refreshLocalRuns().catch((error) => toast(error.message, true)));
  element("localPaperAnalyze").addEventListener("click", analyzeLocalPaper);
  element("localPaperTransfer").addEventListener("click", transferLocalPaper);
  element("localPaperOpenProject").addEventListener("click", () => {
    const projectId = element("localPaperOpenProject").dataset.projectId;
    if (projectId) openProject(projectId);
  });
  element("cancelSourceRun").addEventListener("click", () => {
    state.pendingLocalRunId = null;
    element("sourceRunBanner").hidden = true;
  });
  element("suggestManuscriptSection").addEventListener("click", suggestManuscriptSection);
  element("manuscriptSectionInstruction").addEventListener("input", () => {
    state.sectionRequestId += 1;
    element("manuscriptSectionCandidateLabel").hidden = true;
    element("applyManuscriptSection").hidden = true;
  });
  element("applyManuscriptSection").addEventListener("click", applyManuscriptSection);
  element("refreshProjects").addEventListener("click", () => loadProjects().catch((error) => toast(error.message, true)));
  element("refreshProject").addEventListener("click", () => refreshCurrentProject().catch((error) => toast(error.message, true)));
  element("runProject").addEventListener("click", runCurrentProject);
  element("interruptProject").addEventListener("click", interruptCurrentProject);
  element("resumeProject").addEventListener("click", () => projectAction("resume", "resumeProject"));
  element("approveStage").addEventListener("click", () => projectAction("approve", "approveStage"));
  element("returnCurrentVersion").addEventListener("click", returnToCurrentVersion);
  element("confirmSearch").addEventListener("click", confirmSearchPlan);
  element("rejectSearch").addEventListener("click", rejectSearchPlan);
  element("deleteProject").addEventListener("click", deleteCurrentProject);
  element("uploadMore").addEventListener("click", uploadMore);
  element("referenceCountMode").addEventListener("change", () => updateReferenceCountState());
  element("projectReferenceCountMode").addEventListener("change", () => updateReferenceCountState("project"));
  element("toggleConfigurationEditor").addEventListener("click", () => {
    const form = element("projectConfigurationForm");
    form.hidden = !form.hidden;
  });
  element("projectConfigurationForm").addEventListener("submit", saveProjectConfiguration);
  element("projectConfigurationForm").addEventListener("input", () => {
    state.pendingConfiguration = null;
    const button = element("saveConfiguration");
    button.textContent = "保存修改草稿";
    button.dataset.originalLabel = "保存修改草稿";
  });
  element("saveStageEdit").addEventListener("click", saveStageEdit);
  element("confirmStageEdit").addEventListener("click", confirmStageEdit);
  element("copyConfiguration").addEventListener("click", async () => {
    if (!state.currentProject) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.currentProject.state?.configuration || {}, null, 2));
      toast("配置摘要已复制。")
    } catch {
      toast("浏览器未允许复制，请使用配置编辑区查看。", true);
    }
  });
  element("goToCreateProject").addEventListener("click", () => {
    showView("projects");
    element("idea").focus();
  });
  element("closePreview").addEventListener("click", () => { element("paperPreviewPanel").hidden = true; });
  element("printPreview").addEventListener("click", () => window.print());
}

async function init() {
  bindEvents();
  updateExperimentInputMode();
  element("idea").addEventListener("input", () => {
    updateIdeaLength();
    if (element("idea").matches(":hover")) expandIdeaOnDemand();
  });
  element("idea").addEventListener("mouseenter", expandIdeaOnDemand);
  element("idea").addEventListener("mouseleave", collapseIdeaOnDemand);
  element("idea").addEventListener("blur", collapseIdeaOnDemand);
  updateIdeaLength();
  updateReferenceCountState();
  try {
    await Promise.all([loadHealth(), loadProjects()]);
  } catch (error) {
    const status = element("systemStatus");
    status.classList.remove("is-on");
    status.lastChild.textContent = " 服务不可用";
    toast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
