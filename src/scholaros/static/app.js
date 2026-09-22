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
const searchFieldLabels = {
  all: "综合主题",
  title: "论文标题",
  author: "作者姓名",
  doi: "DOI",
  venue: "期刊 / 会议",
};
const searchFieldHints = {
  all: "按有效词项覆盖率过滤宽泛结果；可启用自然语言理解。",
  title: "请输入完整论文标题；规范化大小写、重音和一般标点，C++/C#/F# 等技术符号仍会区分。",
  author: "请输入作者全名；姓氏和后缀须一致，仅结果侧名字可缩写为 G. Hinton。",
  doi: "按 DOI 字段检索，可输入 10.xxxx/xxxx 或完整 DOI 链接。",
  venue: "普通期刊请输入完整名称；顶会可输入 CVPR、AAAI、NIPS/NeurIPS 等简称。",
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
  "paper.tex": "可编辑 LaTeX 源文件",
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
    const detail = typeof data === "object" ? data.detail : data;
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
  element("pageTitle").textContent = name === "workspace" ? "当前研究项目" : "研究工作台";
  if (name === "workspace") {
    element("workspaceEmpty").hidden = Boolean(state.currentProject);
    element("projectWorkspace").hidden = !state.currentProject;
  }
}

function renderSourceOptions(containerId) {
  const container = element(containerId);
  container.replaceChildren();
  const sources = state.health?.sources || [];
  for (const source of sources) {
    if (source.name === "ieee" || source.name === "ieee_metadata") continue;
    const label = document.createElement("label");
    label.className = "source-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = source.name;
    const workflowRestricted = containerId === "createSources" && source.workflow_eligible === false;
    const searchableUnavailableIeee = containerId === "searchSources" && source.name === "ieee";
    input.checked = !workflowRestricted && (source.available || searchableUnavailableIeee);
    input.disabled = workflowRestricted || (!source.available && !searchableUnavailableIeee);
    const text = document.createElement("span");
    const sourceName = {
      semantic_scholar: "Semantic Scholar",
      ieee_metadata: "IEEE 书目元数据",
      ieee: "IEEE Xplore",
    }[source.name] || source.name.toUpperCase();
    const displayName = containerId === "createSources" && source.name === "ieee"
      ? "ieee_metadata"
      : sourceName;
    text.textContent = source.name === "ieee" && containerId !== "createSources"
      ? `${sourceName} · ${source.available ? "已配置 API" : "未配置 API，改用 ieee_metadata 书目元数据"}`
      : displayName;
    if (!source.available) text.title = source.status || "缺少所需 API Key";
    else if (workflowRestricted) text.title = source.name === "ieee"
      ? "受 IEEE API 条款限制，仅用于独立检索"
      : "该来源仅用于独立检索，不会进入自动写作链路";
    label.append(input, text);
    container.append(label);
  }
}

const venueSourceNames = ["CVPR", "ICCV", "ECCV", "NeurIPS", "ICML", "ICLR", "AAAI", "IJCAI", "ACL", "EMNLP", "KDD"];

function renderVenueOptions() {
  const container = element("venueSources");
  if (!container) return;
  container.replaceChildren();
  for (const venue of venueSourceNames) {
    const label = document.createElement("label");
    label.className = "source-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = venue;
    input.checked = true;
    const text = document.createElement("span");
    text.textContent = venue;
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
  element("brandVersion").textContent = `v${health.version || "0.3.0"}`;
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
  renderSourceOptions("createSources");
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  const container = element("recentProjects");
  container.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted compact";
    empty.textContent = "还没有项目。";
    container.append(empty);
    return;
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-link${state.currentProject?.id === project.id ? " is-active" : ""}`;
    const title = document.createElement("strong");
    title.textContent = project.title || project.idea;
    const meta = document.createElement("span");
    meta.textContent = `${statusLabels[project.status] || project.status} · ${project.id}`;
    button.append(title, meta);
    button.addEventListener("click", () => openProject(project.id));
    container.append(button);
  }
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
    same_field_papers: Number(read("SameFieldPapers").value),
    target_venue_papers: Number(read("TargetVenuePapers").value),
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
        sources: selectedSources("createSources"),
        run_now: false,
        guided: document.querySelector('input[name="executionMode"]:checked')?.value !== "automatic",
        configuration: collectConfiguration(),
      }),
    });
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
    ["学习计划", `同方向 ${config.same_field_papers} 篇 · 目标场景 ${config.target_venue_papers} 篇`],
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
    MechanismFigure: "mechanism_figure", SameFieldPapers: "same_field_papers",
    TargetVenuePapers: "target_venue_papers", ReferenceCountMode: "reference_count_mode",
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
      ? `同方向目标 ${learning.same_field_target} 篇，目标场景“${learning.target_name}”目标 ${learning.target_venue_target} 篇。`
      : `同方向目标 ${learning.same_field_target} 篇；未指定目标期刊或会议。`;
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

function mermaidLabel(value, fallback) {
  const label = String(value || fallback)
    .replace(/["`\n\r;]/g, " ")
    .replace(/[\[\](){}`<>|#]/g, " ")
    .replace(/(?:-->|---|-.->|==>)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (label || fallback).slice(0, 56);
}

function figureMermaid(entry) {
  const title = mermaidLabel(entry.title, "研究流程");
  const question = mermaidLabel(entry.composition?.[0], "研究问题与边界");
  if (entry.kind === "mechanism") {
    return `flowchart LR\n  A["输入与证据"] --> B["处理模块"]\n  B --> C["输出与指标"]\n  C -.-> D["验证反馈"]\n  D -.-> B\n  T["${title}"]:::title -.-> A\n  classDef title fill:#edf5f0,stroke:#2f6550,color:#183b31;`;
  }
  if (entry.kind === "results") {
    return `flowchart LR\n  A["真实结果"] --> B["主要指标"]\n  B --> C["对照与区间"]\n  C --> D["稳健性检查"]\n  Q["${title}"]:::title -.-> A\n  classDef title fill:#f2e3d1,stroke:#b86a2f,color:#6c3e20;`;
  }
  return `flowchart LR\n  A["${question}"] --> B["证据与纳入边界"]\n  B --> C["研究方法"]\n  C --> D["可证伪结论"]\n  D -.-> E["失败条件 / 待验证"]\n  T["${title}"]:::title -.-> A\n  classDef title fill:#edf5f0,stroke:#2f6550,color:#183b31;`;
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
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
        primaryColor: "#edf5f0",
        primaryTextColor: "#183b31",
        primaryBorderColor: "#6d9b84",
        lineColor: "#6c8177",
      },
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
        renderMermaidPreview(diagram, figureMermaid(entry));
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
  const manuscriptNames = new Set(["paper-draft.md", "paper-draft.tex", "paper.md", "paper.tex"]);
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

async function searchPapers(event) {
  event.preventDefault();
  const button = element("searchButton");
  const feedback = element("searchFeedback");
  const query = element("searchQuery").value.trim();
  const field = element("searchField").value;
  const naturalLanguage = element("naturalLanguageSearch").checked;
  const authorAffiliation = field === "author" ? element("authorAffiliation").value.trim() : "";
  const authorTopic = field === "author" ? element("authorTopic").value.trim() : "";
  const authorVenue = field === "author" ? element("authorVenue").value.trim() : "";
  const sources = selectedSources("searchSources");
  const venues = selectedSources("venueSources");
  if (query.length < 2) return;
  if (!sources.length) {
    feedback.textContent = "请至少选择一个论文源。";
    toast("请至少选择一个论文源。", true);
    return;
  }
  setSearchBusy(true);
  feedback.classList.remove("is-error");
  feedback.textContent = "正在并行查询所选论文源…";
  element("searchResults").replaceChildren();
  element("searchPlan").hidden = true;
  element("googleScholarLink").hidden = true;
  try {
    const result = await api("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        query,
        sources,
        limit: 30,
        natural_language: naturalLanguage,
        field,
        author_affiliation: authorAffiliation || null,
        author_topic: authorTopic || null,
        author_venue: authorVenue || null,
        venue_sources: venues,
      }),
    });
    renderSearchResults(result);
    renderSearchPlan(result.search_plan);
    const filtered = result.filtered_out ? `，严格匹配排除 ${result.filtered_out} 条宽泛候选` : "";
    const discovered = result.papers.filter((paper) => (paper.sources || []).includes("llm_discovery")).length;
    feedback.textContent = `找到 ${result.papers.length} 篇去重论文${filtered}${discovered ? `，其中 ${discovered} 篇由模型补充发现` : ""}${result.failures.length ? `，${result.failures.length} 个来源已降级` : ""}。`;
    const scholarLink = element("googleScholarLink");
    scholarLink.href = `https://scholar.google.com/scholar?q=${encodeURIComponent(result.google_scholar_query || result.search_query)}`;
    scholarLink.hidden = false;
  } catch (error) {
    feedback.classList.add("is-error");
    feedback.textContent = `检索失败：${error.message}`;
    toast("检索失败，请查看页面中的错误说明。", true);
  } finally {
    setSearchBusy(false);
  }
}

function setSearchBusy(busy) {
  setBusy(element("searchButton"), busy, "正在检索…");
  document.querySelectorAll("#venueSources input").forEach((input) => {
    input.disabled = busy;
  });
}

function renderSearchPlan(plan) {
  const container = element("searchPlan");
  container.replaceChildren();
  container.append(searchPlanRow("检索字段", searchFieldLabels[plan.field] || plan.field));
  container.append(searchPlanRow("原始输入", plan.input_query));
  if (plan.natural_language) {
    const terms = document.createElement("div");
    terms.className = "search-term-list";
    for (const value of plan.search_terms) {
      const term = document.createElement("span");
      term.className = "search-term";
      term.textContent = value;
      terms.append(term);
    }
    container.append(searchPlanRow("模型生成的英文词组", terms));
  }
  container.append(searchPlanRow("最终发送的检索式", plan.search_query));
  if (plan.field === "author" && plan.author_filters) {
    const filters = [
      plan.author_filters.affiliation && `机构：${plan.author_filters.affiliation}`,
      plan.author_filters.topic && `主题：${plan.author_filters.topic}`,
      plan.author_filters.venue && `会议：${plan.author_filters.venue}`,
    ].filter(Boolean);
    container.append(searchPlanRow("作者附加条件", filters.join("；") || "未限制"));
  }
  container.append(searchPlanRow("匹配规则", plan.matching_policy));
  container.hidden = false;
}

function searchPlanRow(label, value) {
  const row = document.createElement("div");
  row.className = "search-plan-row";
  const heading = document.createElement("strong");
  heading.textContent = label;
  const content = value instanceof Node ? value : document.createElement("span");
  if (!(value instanceof Node)) content.textContent = value;
  row.append(heading, content);
  return row;
}

function updateSearchField() {
  const field = element("searchField").value;
  const natural = element("naturalLanguageSearch");
  const directOnly = field !== "all";
  if (directOnly) natural.checked = false;
  natural.disabled = directOnly;
  element("authorFilters").hidden = field !== "author";
  element("searchFieldHint").textContent = searchFieldHints[field];
  element("searchQuery").placeholder = {
    all: "例如：如何减少科研智能体生成的错误引用？",
    title: "例如：Attention Is All You Need",
    author: "例如：Geoffrey Hinton",
    doi: "例如：10.1145/1234567",
    venue: "例如：CVPR、AAAI、NeurIPS / NIPS 或完整会议名称",
  }[field];
}

function renderSearchResults(result) {
  const container = element("searchResults");
  container.replaceChildren();
  for (const paper of result.papers) {
    const card = document.createElement("article");
    card.className = "paper-card";
    const header = document.createElement("div");
    header.className = "paper-card-header";
    const copy = document.createElement("div");
    const heading = document.createElement("h3");
    const landingUrl = safeExternalUrl(paper.landing_url) || doiUrl(paper.doi);
    const pdfUrl = safeExternalUrl(paper.pdf_url);
    const title = document.createElement(landingUrl ? "a" : "span");
    title.textContent = paper.title;
    if (landingUrl) {
      title.href = landingUrl;
      title.target = "_blank";
      title.rel = "noreferrer";
    }
    heading.append(title);
    const meta = document.createElement("div");
    meta.className = "paper-card-meta";
    const authors = (paper.authors || []).slice(0, 4).join("、") || "作者未知";
    const venue = paper.venue ? ` · ${paper.venue}` : "";
    const paperSources = (paper.sources || []).map((source) => source === "llm_discovery" ? "模型发现" : source);
    meta.textContent = `${authors}${paper.authors?.length > 4 ? " 等" : ""} · ${paper.year || "年份未知"}${venue} · ${paperSources.join(" / ")}`;
    copy.append(heading, meta);
    const badges = document.createElement("div");
    badges.className = "paper-badges";
    if (paper.is_open_access) {
      const oa = document.createElement("span");
      oa.className = "paper-badge is-oa";
      oa.textContent = "Open Access";
      badges.append(oa);
    }
    if (paper.doi) {
      const doi = document.createElement("span");
      doi.className = "paper-badge";
      doi.textContent = "DOI";
      badges.append(doi);
    }
    header.append(copy, badges);
    card.append(header);
    if (paper.abstract) {
      const abstract = document.createElement("p");
      abstract.textContent = paper.abstract;
      card.append(abstract);
    }
    if (landingUrl || pdfUrl) {
      const links = document.createElement("div");
      links.className = "paper-links";
      if (landingUrl) links.append(externalPaperLink("网页 / DOI ↗", landingUrl));
      if (pdfUrl && pdfUrl !== landingUrl) links.append(externalPaperLink("开放 PDF ↗", pdfUrl));
      card.append(links);
    }
    container.append(card);
  }
  for (const failure of result.failures) {
    const note = document.createElement("article");
    note.className = "source-failure";
    const heading = document.createElement("strong");
    heading.textContent = `${failure.source} 已跳过：${failure.reason}`;
    const solution = document.createElement("p");
    solution.textContent = `应对方案：${failure.suggestion}`;
    const state = document.createElement("span");
    state.textContent = failure.retryable ? "可稍后重试" : "需调整后重试";
    note.append(heading, solution, state);
    container.append(note);
  }
}

function externalPaperLink(label, url) {
  const link = document.createElement("a");
  link.textContent = label;
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  return link;
}

function doiUrl(value) {
  return value ? `https://doi.org/${value.split("/").map(encodeURIComponent).join("/")}` : null;
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
