"use strict";

const state = {
  health: null,
  projects: [],
  currentProject: null,
  pollTimer: null,
  toastTimer: null,
};

const terminalStatuses = new Set(["completed", "needs_attention", "failed"]);
const stages = ["scoping", "searching", "synthesizing", "designing", "drafting", "reviewing", "revising"];
const stageLabels = {
  scoping: "正在收敛研究问题",
  searching: "正在跨源检索论文",
  synthesizing: "正在建立证据账本",
  designing: "正在设计研究方法",
  drafting: "正在起草研究稿件",
  reviewing: "正在执行质量检查",
  revising: "正在辅助修订稿件",
  completed: "工作流已完成",
};
const statusLabels = {
  created: "待运行",
  running: "运行中",
  completed: "质量检查通过",
  needs_attention: "待人工确认",
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
  "papers.json": "检索论文清单",
  "evidence.json": "证据账本",
  "research-design.json": "研究设计",
  "paper-draft.md": "论文初稿",
  "review.json": "初审报告",
  "paper.md": "修订论文",
  "final-review.json": "最终质量检查报告",
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
  element("pageTitle").textContent = name === "projects" ? "研究工作台" : "跨源论文检索";
}

function renderSourceOptions(containerId) {
  const container = element(containerId);
  container.replaceChildren();
  const sources = state.health?.sources || [];
  for (const source of sources) {
    const label = document.createElement("label");
    label.className = "source-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = source.name;
    const workflowRestricted = containerId === "createSources" && source.workflow_eligible === false;
    const searchableUnavailableIeee = containerId === "searchSources" && source.name === "ieee";
    input.checked = source.name !== "semantic_scholar" && !workflowRestricted && (source.available || searchableUnavailableIeee);
    input.disabled = workflowRestricted || (!source.available && !searchableUnavailableIeee);
    const text = document.createElement("span");
    const sourceName = source.name === "semantic_scholar" ? "Semantic Scholar" : source.name.toUpperCase();
    text.textContent = source.name === "ieee" ? `${sourceName} · ${source.status}` : sourceName;
    if (!source.available) text.title = source.status || "缺少所需 API Key";
    else if (workflowRestricted) text.title = "受 IEEE API 条款限制，仅用于独立检索";
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
  const status = element("systemStatus");
  status.classList.add("is-on");
  status.lastChild.textContent = " 服务已连接";
  const model = element("modelBadge");
  model.classList.toggle("is-on", health.model_configured);
  model.lastChild.textContent = health.model_configured
    ? ` 模型已配置 · ${health.model_name}`
    : " 未配置模型 · 诚实降级";
  const sourceStatus = element("sourceStatus");
  sourceStatus.replaceChildren();
  for (const source of health.sources) {
    const chip = document.createElement("span");
    chip.className = `source-chip${source.available ? "" : " is-off"}`;
    chip.textContent = source.name === "ieee" ? `ieee · ${source.status}` : source.name;
    chip.title = `${source.access} · ${source.status}`;
    sourceStatus.append(chip);
  }
  renderSourceOptions("createSources");
  renderSourceOptions("searchSources");
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
      body: JSON.stringify({ idea, sources: selectedSources("createSources"), run_now: false, guided: element("guidedMode").checked }),
    });
    state.currentProject = project;
    renderProject(project);
    element("historyList").replaceChildren();
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
  showView("projects");
  try {
    const project = await api(`/api/projects/${projectId}`);
    state.currentProject = project;
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
  state.currentProject = project;
  renderProject(project);
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
  const workspace = element("projectWorkspace");
  workspace.hidden = false;
  element("projectTitle").textContent = project.title || project.idea;
  element("projectMeta").textContent = `项目 ${project.id} · 创建于 ${formatDate(project.created_at)}`;
  const isActive = project.is_active === true || (project.status === "running" && project.is_active !== false);
  const isInterrupted = project.status === "running" && !isActive;
  const status = element("projectStatus");
  status.textContent = isInterrupted ? "已中断" : statusLabels[project.status] || project.status;
  status.className = `status-pill${isActive ? " is-running" : project.status === "failed" || project.status === "needs_attention" || isInterrupted ? " is-error" : ""}`;

  const isTerminal = terminalStatuses.has(project.status);
  const currentIndex = project.stage === "completed" ? stages.length : Math.max(0, stages.indexOf(project.stage));
  const finishedStages = project.stage === "completed" ? stages.length : currentIndex;
  const percent = project.status === "created" ? 0 : Math.round((Math.max(finishedStages, project.status === "running" ? currentIndex + 0.35 : finishedStages) / stages.length) * 100);
  element("progressFill").style.width = `${Math.min(100, percent)}%`;
  element("progressPercent").textContent = `${Math.min(100, percent)}%`;
  element("stageText").textContent = project.error || (isInterrupted ? "上次运行已中断，可从断点继续" : stageLabels[project.stage] || "准备开始");
  document.querySelectorAll("#stageList li").forEach((node, index) => {
    node.classList.toggle("is-done", index < finishedStages || project.stage === "completed");
    node.classList.toggle("is-current", project.status === "running" && index === currentIndex);
  });

  const runButton = element("runProject");
  const needsSearchConfirmation = project.state?.search_confirmation_required === true;
  const checkpoint = project.state?.pending_checkpoint;
  runButton.disabled = isActive;
  runButton.hidden = isActive || needsSearchConfirmation || Boolean(checkpoint);
  runButton.textContent = project.status === "created" ? "开始运行" : "从头重新运行";
  element("resumeProject").hidden = isActive || needsSearchConfirmation || Boolean(checkpoint)
    || project.stage === "completed" || project.status === "created";
  element("approveStage").hidden = isActive || !checkpoint;
  element("rerunStageButton").disabled = isActive;
  const rerunStage = element("rerunStage");
  const latestRerunIndex = checkpoint ? stages.indexOf(checkpoint) : currentIndex;
  for (const option of rerunStage.options) {
    option.disabled = stages.indexOf(option.value) > latestRerunIndex;
  }
  const selectionContext = `${project.id}:${checkpoint || ""}`;
  if (rerunStage.dataset.context !== selectionContext || rerunStage.selectedOptions[0]?.disabled) {
    rerunStage.value = checkpoint || (project.stage === "completed" ? "designing" : project.stage);
    rerunStage.dataset.context = selectionContext;
  }
  const checkpointPreview = element("checkpointPreview");
  element("checkpointPanel").hidden = !checkpoint;
  const key = { scoping: "spec", synthesizing: "evidence", designing: "design" }[checkpoint];
  checkpointPreview.textContent = key ? JSON.stringify(project.state[key], null, 2)
    : "请在研究制品中预览 paper-draft.md，确认初稿后继续质量检查。";
  element("uploadMore").disabled = project.status === "running";
  element("moreSourceFiles").disabled = project.status === "running";
  element("moreResultFiles").disabled = project.status === "running";
  element("deleteProject").disabled = isActive;
  element("deleteProject").title = isActive ? "项目运行中，暂时不能删除" : "永久删除项目及全部制品";
  const confirmation = element("searchConfirmation");
  confirmation.hidden = !needsSearchConfirmation;
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
  renderArtifacts(project);
  renderReview(project);
}

function renderArtifacts(project) {
  const container = element("artifactList");
  container.replaceChildren();
  const artifacts = project.artifacts || [];
  if (!artifacts.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "运行后将在这里出现研究草稿、证据和质量检查报告。";
    container.append(empty);
    return;
  }
  for (const name of artifacts) {
    const row = document.createElement("div");
    row.className = "artifact-item";
    const label = document.createElement("span");
    label.textContent = artifactLabels[name] || name;
    const actions = document.createElement("div");
    actions.className = "artifact-actions";
    if (name.endsWith(".md")) {
      const preview = document.createElement("button");
      preview.type = "button";
      preview.textContent = "预览";
      preview.addEventListener("click", () => previewArtifact(project.id, name));
      actions.append(preview);
    }
    const download = document.createElement("a");
    download.href = `/api/projects/${project.id}/artifacts/${encodeURIComponent(name)}`;
    download.textContent = "下载";
    download.setAttribute("download", name);
    actions.append(download);
    row.append(label, actions);
    container.append(row);
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
  const findings = review.findings || [];
  if (findings.length) {
    const list = document.createElement("ul");
    list.className = "finding-list";
    for (const finding of findings.slice(0, 4)) {
      const item = document.createElement("li");
      item.textContent = finding.message;
      list.append(item);
    }
    container.append(list);
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

async function loadHistory() {
  if (!state.currentProject) return;
  const projectId = state.currentProject.id;
  try {
    const history = await api(`/api/projects/${projectId}/history`);
    if (state.currentProject?.id !== projectId) return;
    const container = element("historyList");
    container.replaceChildren();
    if (!history.length) container.textContent = "尚无历史版本；首次重做前会自动保存。";
    for (const revision of history) {
      const row = document.createElement("p");
      row.textContent = `${formatDate(revision.created_at)} · ${revision.reason} `;
      for (const name of ["manifest.json", ...Object.keys(revision.artifacts)]) {
        const link = document.createElement("a");
        link.href = `/api/projects/${projectId}/history/${revision.revision}/${encodeURIComponent(name)}`;
        link.textContent = ` ${name} `;
        link.download = name;
        row.append(link);
      }
      container.append(row);
    }
  } catch (error) {
    toast(error.message, true);
  }
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
  if (!state.currentProject || state.currentProject.is_active === true) return;
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
    await loadProjects();
    toast(`项目 ${project.id} 已删除。`);
  } catch (error) {
    try {
      await refreshCurrentProject();
      startPolling();
    } catch {
      state.currentProject = null;
      element("projectWorkspace").hidden = true;
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

async function previewArtifact(projectId, name) {
  try {
    const markdown = await api(`/api/projects/${projectId}/artifacts/${encodeURIComponent(name)}`);
    element("paperPreview").textContent = markdown;
    element("paperPreviewPanel").hidden = false;
    element("paperPreviewPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(error.message, true);
  }
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
  if (query.length < 2) return;
  if (!sources.length) {
    feedback.textContent = "请至少选择一个论文源。";
    toast("请至少选择一个论文源。", true);
    return;
  }
  setBusy(button, true, "正在检索…");
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
      }),
    });
    renderSearchResults(result);
    renderSearchPlan(result.search_plan);
    const filtered = result.filtered_out ? `，严格匹配排除 ${result.filtered_out} 条宽泛候选` : "";
    feedback.textContent = `找到 ${result.papers.length} 篇去重论文${filtered}${result.failures.length ? `，${result.failures.length} 个来源已降级` : ""}。`;
    const scholarLink = element("googleScholarLink");
    scholarLink.href = `https://scholar.google.com/scholar?q=${encodeURIComponent(result.google_scholar_query || result.search_query)}`;
    scholarLink.hidden = false;
  } catch (error) {
    feedback.classList.add("is-error");
    feedback.textContent = `检索失败：${error.message}`;
    toast("检索失败，请查看页面中的错误说明。", true);
  } finally {
    setBusy(button, false, "");
  }
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
    meta.textContent = `${authors}${paper.authors?.length > 4 ? " 等" : ""} · ${paper.year || "年份未知"}${venue} · ${(paper.sources || []).join(" / ")}`;
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
  element("createForm").addEventListener("submit", createProject);
  element("searchForm").addEventListener("submit", searchPapers);
  element("searchField").addEventListener("change", updateSearchField);
  element("refreshProjects").addEventListener("click", () => loadProjects().catch((error) => toast(error.message, true)));
  element("refreshProject").addEventListener("click", () => refreshCurrentProject().catch((error) => toast(error.message, true)));
  element("runProject").addEventListener("click", runCurrentProject);
  element("resumeProject").addEventListener("click", () => projectAction("resume", "resumeProject"));
  element("approveStage").addEventListener("click", () => projectAction("approve", "approveStage"));
  element("loadHistory").addEventListener("click", loadHistory);
  element("rerunStageButton").addEventListener("click", () => {
    const stage = element("rerunStage").value;
    if (window.confirm("将保存历史快照，然后重做所选阶段及其下游。是否继续？")) {
      projectAction(`rerun?stage=${encodeURIComponent(stage)}`, "rerunStageButton");
    }
  });
  element("confirmSearch").addEventListener("click", confirmSearchPlan);
  element("rejectSearch").addEventListener("click", rejectSearchPlan);
  element("deleteProject").addEventListener("click", deleteCurrentProject);
  element("uploadMore").addEventListener("click", uploadMore);
  element("closePreview").addEventListener("click", () => { element("paperPreviewPanel").hidden = true; });
}

async function init() {
  bindEvents();
  element("idea").addEventListener("input", updateIdeaLength);
  updateIdeaLength();
  updateSearchField();
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
