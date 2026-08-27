# 论文源权限、密钥与合规备注

[English](../permissions.md) | **简体中文**

## 原则

ScholarOS 将四件事分开表示：搜索元数据、读取摘要、访问开放全文、访问订阅全文。检索到 DOI 或 PDF 链接不自动代表用户拥有下载、保存、训练或再分发权。

ScholarOS 只提供科研辅助。检索结果、证据摘要、方法建议和研究草稿都需要研究者人工核验；系统不能替代伦理审批、数据真实性检查、作者署名判断或投稿责任。使用 AI 的范围应按所在机构和目标出版物的规则披露。

## 各来源

### arXiv

- 默认启用官方 Atom API。
- 返回预印本元数据、摘要、落地页与 PDF 链接。
- `journal_ref` 是完整发表引用而非独立规范期刊名，因此严格期刊/会议字段检索会明确跳过 arXiv；这不影响综合主题、标题或作者检索。
- 正式引用时要核对版本、发表版本和许可证。

### OpenAlex

- 默认启用开放 API；建议设置 `SCHOLAROS_CONTACT_EMAIL`。
- 提供聚合元数据、引用量和开放获取状态。
- Works authorship 可提供逐作者机构关系；作者组合检索会先解析 Author、Institution、Source 实体，把机构、主题、会议下推到 Works 候选查询，再核对“该姓名本人所在机构”，但元数据仍可能缺失或滞后。
- 期刊/会议检索先解析 Source 实体及其备选/缩写名称，再查询 Works。
- 聚合链接可能变化，引用仍应落到 DOI/出版物原页。

### Crossref

- 默认启用，无需注册；建议 `mailto` 和明确 User-Agent。
- 提供出版商提交的 DOI 元数据；部分摘要可能仍受版权限制。
- 作者、affiliation、主题与 container title 可作为组合候选查询；ScholarOS 仍会逐条核对作者—机构对应关系，因为 Crossref 字段查询本身是相关性召回。
- 生产部署应缓存、处理 429/5xx 并指数退避；MVP 当前只做单次失败降级。

### Semantic Scholar

- 作者检索先解析 Author 实体，可用其 affiliation 辅助同名消歧；主题/会议会分页核验作者论文，当前单次搜索最多扫描 1000 条候选；期刊/会议使用官方 venue filter。
- 匿名公共额度容易遇到 429，生产使用建议申请并配置 API key。
- 未配置 key 也可尝试；生产建议申请并设置 `SEMANTIC_SCHOLAR_API_KEY`。
- API 配额和条款以官方当前文档为准。
- `openAccessPdf` 是线索，使用前仍核对许可证。

### DBLP

- 默认使用官方 publication search JSON API。
- 主要提供计算机科学书目信息，不承担全文访问。

### ACM

- 当前 `acm` source 通过 Crossref 查询 ACM DOI 前缀 `10.1145`。
- 这是“ACM 出版物元数据支持”，不是 ACM Digital Library 官方公共搜索 API，也不抓取其网页。
- ACM 的开放获取政策和具体论文许可证应以 ACM 当前官方页面与论文记录为准。ScholarOS 当前 ACM 适配器只通过 Crossref 获取 `10.1145` 题录，并提供 DOI/落地页；“开放获取”标记不等于 ScholarOS 已取得批量保存、训练或再分发全文的许可。
- 若未来获得 ACM 官方或机构 connector，新增独立适配器即可，不应把订阅 cookie 写进代码。
- 学校登录是否能访问正文、Premium 功能或第三方出版商内容，取决于学校订阅和当前 ACM 页面提示。
- 当前 ScholarOS 不读取浏览器 cookie，也不自动批量下载订阅全文。若要自动化正文获取，应先让学校图书馆确认许可，再接入正式的机构/TDM 接口。

### IEEE Xplore

- 在 [IEEE Xplore API Portal](https://developer.ieee.org/) 注册账号，在申请中如实说明 ScholarOS 的非商业科研用途和学校机构信息，等待 IEEE 审核并签发应用 key。
- 把 key 写入项目根目录的 `.env`：`IEEE_XPLORE_API_KEY=你的_key`，然后重启 `scholaros` 或 `scholaros serve`。未配置时 IEEE 仍显示在独立检索来源中，但会自动跳过并说明原因。
- 网页连接状态或 `http://127.0.0.1:8000/health` 中 `ieee.available=true` 只表示 ScholarOS 已读取到 Key，不证明 IEEE 已激活。真正状态以 IEEE 激活邮件和首次检索响应为准；401/403 会提示“尚未激活、无效或无权访问”。不要把 `.env` 或 Key 提交到 Git。
- Metadata Search API 与 Open Access/付费全文 API 是不同权限层。
- 学校/机构网页订阅不自动等于 API 批量全文授权。学校登录适合在浏览器中阅读或下载你获准访问的单篇论文；若要让 ScholarOS 自动取回订阅全文，需由图书馆或 IEEE 另行确认 Full-Text/TDM 授权。
- IEEE 当前 [API Terms of Use](https://developer.ieee.org/API_Terms_of_Use2) 对把 Content 用于 AI/LLM 和数据挖掘设有限制。为避免未经授权把 IEEE 元数据或摘要发送给模型，当前 `ieee` 只能用于 `scholaros search` 或网页“跨源论文检索”，不能加入 Idea→论文工作流。
- 若 IEEE 或学校图书馆为你的具体研究出具书面许可，再增加显式 opt-in 的 AI connector；不要仅凭学校网页登录推定已经获得该许可。

### Google Scholar

- Google Scholar 的覆盖范围与 ScholarOS 当前来源高度重叠，但不相等；其[官方介绍](https://scholar.google.com/intl/engb/scholar/about.html)还列出学位论文、图书、摘要、机构仓储和其他学术网页。
- ScholarOS 不把 Google Scholar 当作自动论文源。[Google Scholar 官方帮助](https://scholar.google.com/intl/us/scholar/help.html)明确不提供批量访问，并要求自动软件遵守 `robots.txt`；项目因此只提供打开 Google Scholar 的手动补充检索链接。
- 手动检索结果可以借助浏览器已有的学校机构会话显示订阅访问入口，但这不把全文自动下载或 AI/TDM 权限授予 ScholarOS。

## 来源故障自助处理

终端、CLI、网页和 API 都会把每一个失败来源单独列出，并在错误后紧跟应对方案。其余来源继续检索，所以这类提示表示“结果可能不完整”，不表示整次搜索失败。

| 来源 | 常见情况 | 应对方案 |
|---|---|---|
| arXiv | 429、5xx、超时 | 降低检索频率并稍后重试；临时取消 arXiv 不影响其他来源。 |
| OpenAlex | 429、5xx、网络错误 | 在 `.env` 设置 `SCHOLAROS_CONTACT_EMAIL` 后重启，降低频率；上游故障时稍后重试。 |
| Crossref | 429、403、5xx | 设置 `SCHOLAROS_CONTACT_EMAIL` 进入 polite pool；429 降频，403 按返回信息联系 Crossref，5xx 稍后重试。 |
| Semantic Scholar | 429、401/403、5xx | 申请并设置 `SEMANTIC_SCHOLAR_API_KEY` 后重启；429 表示限流，Key 无效时检查是否完整，5xx 稍后重试。 |
| DBLP | 429、503、超时 | 尊重 `Retry-After` 并降低频率；503 是 DBLP 服务端临时不可用，稍后重试或暂时取消 DBLP。 |
| ACM | Crossref 的 429/403/5xx | 当前 ACM source 实际经 Crossref 查询；设置 `SCHOLAROS_CONTACT_EMAIL`，按 Crossref 的方案处理，或暂时取消 ACM。 |
| IEEE | 未配置、401/403、429、5xx | 未配置时设置 `IEEE_XPLORE_API_KEY`；邮件仍为 `waiting` 时等待激活；429 等配额恢复；5xx 稍后重试。 |

Semantic Scholar 官方说明匿名请求共享公共限额，繁忙时可能进一步限流；带 Key 可获得独立额度。[官方 API 说明](https://www.semanticscholar.org/product/api)。Crossref 官方建议提供 `mailto`、缓存响应并在 429 后退避，[访问与限流说明](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)。DBLP 官方说明在线 API 有保护性限流，大量查询应降低频率或改用数据集，[官方 FAQ](https://dblp.org/faq/1474706.html)。

ScholarOS 当前对单源失败采用立即降级，不在一次用户请求内自动连续重试，避免在 429 或上游故障时进一步放大流量。界面中的 `retryable` 表示稍后重试是否通常有效，不代表系统已自动重试。

## 学校账号与自动检索

- 对“找论文”而言，无需先登录学校账号：ScholarOS 通过 arXiv、OpenAlex、Crossref、DBLP、ACM 元数据和可选 IEEE API 搜索题录。
- 对“读付费全文”而言，学校登录有帮助：先在浏览器完成学校认证，再从 ScholarOS 搜索结果打开出版商页面，由浏览器使用已有的机构会话。
- 不建议让后端复制学校账号、密码或浏览器 cookie。它既不稳定，也可能超出学校订阅的自动化使用范围；ScholarOS 当前有意不这样做。

## 后续逐步解决清单

1. 申请 IEEE Xplore API key，在 `/health` 确认 `ieee.available=true`。
2. 为生产配置 Crossref 联系邮箱和 Semantic Scholar key。
3. 与所在学校图书馆确认 IEEE/ACM TDM、远程访问与存储条款。
4. 若有机构 API，先写 connector 的权限说明和审计日志，再接入正文。
5. 增加许可证字段和正文缓存过期策略；项目及本地制品删除已支持。
6. 对涉及受试者、医疗、隐私或未公开研究的项目加入伦理审批门禁。
7. 将 AI 辅助披露、作者确认和最终人工签核作为投稿前的外部流程；软件内质量分数不得作为合规证明。

ScholarOS 不提供也不应加入绕过付费墙、验证码、机器人限制或机构许可的实现。
