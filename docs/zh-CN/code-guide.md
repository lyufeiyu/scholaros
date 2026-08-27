# ScholarOS 项目代码说明

[English](../code-guide.md) | **简体中文**

## 目录

```text
src/scholaros/
├── api.py          # FastAPI、Web 首页、后台任务与上传/下载
├── cli.py          # run/search/show/serve 命令
├── config.py       # 环境配置与工作目录
├── domain.py       # 项目、阶段、论文、证据、质量检查等领域对象
├── ingestion.py    # PDF/TXT/Markdown 文本摄取
├── llm.py          # OpenAI-compatible 与测试模型
├── papers.py       # 论文源适配器、并发检索、去重和排序
├── review.py       # 9 类确定性研究草稿质量检查
├── runtime.py      # AgentMessage、ToolRegistry、AgentLoop
├── storage.py      # SQLite 状态/事件与文件制品
├── terminal.py     # 中文交互菜单、运行向导与阶段进度
├── workflow.py     # 七阶段科研辅助流程的编排和恢复
├── writing.py      # 研究规划、证据账本、设计、草稿与修订辅助
└── static/         # Web 工作台 HTML、CSS 与交互脚本
```

## 关键调用链

1. `ResearchWorkflow.create_project()` 创建项目和首个事件。
2. `run()` 从当前 `stage` 继续，阶段开始/结束都会持久化和发事件。
3. `ResearchWriter.scope()` 同时读取 idea 与上传资料摘要；有 source 时必须输出可在单份正文摘录中逐字核验的 `source_basis`，并在检索词外发前执行长度、控制字符、URL/标识符与主题关联校验。
4. 带 source 的项目在 SEARCHING 首次进入 `search_confirmation_required`，确认前零外部请求；`confirm_search_plan()` 继续，`reject_search_plan()` 可修改 idea 并回到 SCOPING。中文依据到英文检索词以及可能敏感但也可能合法的学术词只作人工警告，不以静态禁词误杀。
5. `PaperSearchService.search_many()` 分别检索前三个互补研究短语，按查询簇轮转保底后去重；来源触发限流、鉴权或临时错误后，本批次不再立即重复请求。正常工作流零命中时停止，只有显式 offline 允许继续。
6. `ResearchWriter` 用确定性逻辑建立证据账本；规划、方法设计、写作和修订可经 AgentLoop 调模型，无模型时诚实降级。
7. `PaperReviewer` 对初稿和修订稿分别执行 9 类检查：章节结构、证据、引用、方法要素、图设计、表设计、结果来源、研究者责任声明和正文完整度。结果性数值必须能在上传的 `results` 文本中定位，参考文献列表中的引用键不计作文内引用。
8. `ProjectStore` 保存状态、事件和每阶段制品；最终 `paper.md` 可经 API/CLI 获取。

`paper.md` 是供研究者继续核验和修改的草稿制品，不是系统对论文正确性、学术合规或可投稿性的保证。

## 交互入口

- `environment.yml` 定义默认的 `scholaros` Conda 环境；Python 依赖仍由 `pyproject.toml` 统一声明。
- `./scholaros.sh` 使用 `conda run --no-capture-output -n scholaros`，不激活或污染当前 Shell，并保留终端交互；可用 `SCHOLAROS_CONDA_ENV` 指定其他环境。启动器只检查和运行，不自动创建环境或安装依赖。
- `python -m scholaros` 或无参数 `scholaros` 进入终端向导。
- `./scholaros.sh serve` 启动 Web 工作台；静态资源随 wheel 一起打包。
- Web 的创建逻辑先建立项目、上传可选资料，再启动后台工作流，避免运行中上传覆盖项目快照。
- 所有网络按钮都会把 API 错误显示为页面提示；项目状态由轮询更新，资料补充后通过 `restart=true` 从头运行。重跑保留上传资料、清除上一轮生成制品，避免失败页继续暴露陈旧论文。

完整环境安装、更新、验证和排错见 [环境配置说明](environment.md)。

## 为什么自己实现 runtime

ScholarOS 只需要很小但清晰的 agent harness。`runtime.py` 遵循四点设计原则：

- `AgentMessage` 是领域真相，只有模型调用时才转换成供应商格式。
- turn/message/tool 都发结构化事件，可调试、持久化和未来用于策略学习。
- 工具调用前验证必填/额外参数，并用 Agent 级白名单控制能力。
- steering 和 follow-up 分开排队，支持执行中纠偏和停止后的继续任务。

没有引入 LangChain/LangGraph，是为了让科研工作流语义掌握在项目自身，而不是框架回调里。

## 扩展一个论文源

实现 `PaperSource` 协议：

```python
class MySource:
    name = "my_source"
    available = True

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        ...
```

然后加入 `PaperSearchService.default()`。适配器必须：

- 只返回统一 `Paper`，区分 `landing_url` 和合法 `pdf_url`。
- 按 `all/title/author/doi/venue` 使用来源官方字段；不支持时显式失败，由全局检索降级，不能伪装成普通关键词命中。
- 来源字段查询只做候选召回；可组合时先把 author/affiliation/topic/venue 下推给来源，Semantic Scholar 等截断式作者端点必须在明确上限内分页，随后由 `PaperSearchService` 再过滤。作者区分 given/family/suffix，仅允许返回侧 given name 首字母缩写；`Paper.author_affiliations` 按作者保存机构，作者附加条件按 AND 核验，不能使用合作者机构；DOI 先做结构校验；标题采用规范化精确等值；venue 先解析常见计算机会议的简称、正式名和旧称，排除 Workshop 与共享简称的其他会议系列；英文综合主题采用有效词项覆盖门槛，纯中文词采用标题/摘要内的 CJK 子串匹配。
- `SearchResult.filtered_out` 记录被严格门槛排除的候选记录数；终端、CLI、API 和 Web 同时展示 `matching_policy`，便于解释零结果或结果数下降。
- 鉴权缺失时 `available=False`，不要让全局检索失败。
- 不在异常信息、事件或日志中暴露 key。
- 明确元数据、摘要、开放全文和订阅全文的权限差异。

## 替换模型

实现 `TurnModel.turn(messages, tools) -> ModelTurn` 即可。供应商格式转换应留在模型边界，不要污染领域消息。正式接入 Responses API、Claude 或本地模型时，写新适配器并保持 `ResearchWriter` 不变。

## 添加工作流阶段

1. 在 `domain.Stage` 增加枚举。
2. 加到 `ResearchWorkflow.stage_order`。
3. 在 `_run_stages()` 实现幂等阶段，并保存输入/输出制品。
4. 新增恢复与失败测试。

建议下一阶段首先加入 `EXPERIMENTING`：受限命令沙箱、依赖锁定、超时、数据快照和可复现日志都要先设计，再允许 Agent 执行代码。

## 数据模型

- `projects.payload` 保存整个项目 JSON 快照，适合 MVP 快速演进。
- `events` 是只追加的生命周期记录，支持前端增量轮询和审计。
- 大文本/论文不塞进 SQLite，保存在 `artifacts/<project-id>/`。
- 用户上传资料只保存提取文本和摘要元数据；原始文件当前不持久保存。

若进入多人生产环境，应迁移到 PostgreSQL、对象存储、项目级访问控制和真正的任务队列，但核心领域对象与阶段协议可以保留。

## 测试策略

- `test_runtime.py`：工具执行、事件、错误回传。
- `test_papers.py`：DOI 去重、来源合并、未知源降级、ACM 前缀限制。
- `test_review.py`：正文引用、证据、方法要素、研究者责任声明和结果数值来源检查。
- `test_workflow.py`：离线端到端、制品、事件、重载、资料优先级和并发写保护。
- `test_api.py`：静态页面、项目生命周期、上传、重启参数和运行中写保护。

网络 API 不放进稳定测试套件，避免外部波动；单独使用 CLI 做真实源冒烟。
