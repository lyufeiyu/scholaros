# ScholarOS 环境配置说明

[English](../environment.md) | **简体中文**

## 环境策略

ScholarOS 默认运行在名为 `scholaros` 的独立 Conda 环境中：

- Conda 管理 Python 3.12 和环境隔离。
- `pyproject.toml` 是 Python 运行依赖与开发依赖的单一事实来源。
- pip 把当前 ScholarOS 项目安装到该 Conda 环境。
- `.env` 只保存本机配置和密钥，不进入 `environment.yml`，也不提交版本库。
- 项目不再要求或自动创建项目内的第二套 Python 环境。

不建议安装到 `base`。技术上可以，但 FastAPI、Pydantic 等版本可能影响其他项目。

## 第一次安装

在项目根目录执行：

```bash
cd /path/to/ScholarOS
conda env create -f environment.yml
conda activate scholaros
scholaros
```

`environment.yml` 会创建 Python 3.12 环境，并通过 pip 执行可编辑开发安装。
其中的 `.` 表示当前工作目录，因此创建和更新环境时都应先进入项目根目录；这对带空格的项目路径同样适用。

如果环境已经存在，使用更新命令：

```bash
conda env update -n scholaros -f environment.yml --prune
```

`--prune` 会移除 YAML 中已经不再声明的 Conda 依赖。执行前应确认该环境只供 ScholarOS 使用。

## `pip install -e ".[dev]"` 的完整含义

| 片段 | 含义 |
|---|---|
| `pip install` | 调用当前 Python 环境中的 pip 安装包 |
| `.` | 安装当前目录；pip 会读取这里的 `pyproject.toml` |
| `-e` | editable，可编辑安装；源码仍指向当前目录，修改 Python 文件后通常无需重装 |
| `[dev]` | 同时安装 `pyproject.toml` 中的 `dev` 可选依赖组 |
| `"..."` | 避免 zsh 把方括号当作通配符表达式 |

当前 `dev` 组包括 pytest、pytest-asyncio、httpx、Ruff、build 和 setuptools。项目运行依赖如 FastAPI、Uvicorn、Pydantic 和 pypdf 无论是否带 `[dev]` 都会安装。

常见安装形式：

```bash
# 本地开发：包含测试/检查工具，源码修改立即生效
python -m pip install -e ".[dev]"

# 只安装运行依赖，但仍使用可编辑源码
python -m pip install -e .

# 普通非可编辑安装
python -m pip install .
```

推荐写成 `python -m pip`，因为它可以明确使用当前 `python` 对应的 pip，减少装错环境的概率。

## 日常运行

激活后运行：

```bash
conda activate scholaros
scholaros
```

进入 Web 工作台：

```bash
scholaros serve
```

### 目录迁移后命令找不到或导入失败

如果项目曾经在根目录 `scholaros/` 布局下安装过，editable 安装记录可能仍指向旧路径。迁移到
`src/scholaros/` 后，重新安装一次即可：

```bash
cd /path/to/ScholarOS
conda activate scholaros
python -m pip install -e ".[dev]"
scholaros serve
```

暂时不想刷新安装时，也可以使用仓库自带的启动器；它会把当前 `src/` 放到导入路径并给出明确诊断：

```bash
./scholaros.sh serve
```

不想激活环境时，可在项目根目录运行：

```bash
./scholaros.sh
./scholaros.sh serve
```

启动器内部使用 `conda run -n scholaros`，不会改变当前 Shell 的环境提示。
它只检查并启动已经准备好的环境，不会静默创建环境、安装依赖或修改 Conda 配置。
`--no-capture-output` 会把输入输出直接连接到当前终端，因此中文菜单、实时日志和 `Ctrl+C` 可以正常工作。

如需使用另一个已准备好的 Conda 环境：

```bash
SCHOLAROS_CONDA_ENV=my-research-env ./scholaros.sh serve
```

或者直接激活该环境并安装项目：

```bash
conda activate my-research-env
python -m pip install -e ".[dev]"
scholaros serve
```

## 模型与论文源配置

从示例复制本机配置：

```bash
cp .env.example .env
```

主要变量：

| 变量 | 作用 |
|---|---|
| `SCHOLAROS_HOME` | SQLite 和论文制品目录，默认 `.scholaros` |
| `SCHOLAROS_MODEL` | OpenAI-compatible 模型名 |
| `SCHOLAROS_API_BASE` | 模型服务 API 根地址 |
| `SCHOLAROS_API_KEY_ENV` | 保存真实模型密钥的环境变量名称 |
| `SCHOLAROS_MODEL_TIMEOUT_SECONDS` | 非流式模型响应的读取超时；默认 300 秒，可设 30—1800 |
| `SCHOLAROS_MODEL_THINKING` | `auto` 跟随模型默认，也可设 `enabled` 或 `disabled` |
| `SCHOLAROS_SOURCE_TIMEOUT_SECONDS` | 论文源网络超时；默认 25 秒，可设 5—300 |
| `SCHOLAROS_CONTACT_EMAIL` | Crossref/OpenAlex 礼貌池联系邮箱 |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar 可选密钥 |
| `IEEE_XPLORE_API_KEY` | IEEE Xplore 官方 API 密钥 |

`.env` 已被 `.gitignore` 忽略。不要把真实密钥写进 `environment.yml`、README 或代码。

自然语言检索出现 `HTTP 401` 时，说明请求已经到达模型服务但被认证或权限拒绝，不是论文源问题。依次检查：

1. `.env` 中 `SCHOLAROS_API_KEY_ENV` 是否与实际密钥变量名完全一致（例如 `DEEPSEEK_API_KEY`）。
2. `SCHOLAROS_API_BASE` 是否为供应商的 OpenAI-compatible 根地址，且没有重复的 `/chat/completions`。
3. `SCHOLAROS_MODEL` 是否为该账号可用的公开模型名；模型名错误通常会返回 400，但也应一并确认。
4. 修改后完全停止旧服务并重新运行；浏览器刷新不会重新加载服务端配置。

当前错误信息会显示生效的模型、API Base 和密钥变量名，但不会显示密钥值，便于定位“读错 `.env`”或“仍在运行旧进程”。

ScholarOS 对模型请求显式发送 `stream: false`，等待单次完整回答；模型与论文源超时彼此独立。设置 `SCHOLAROS_MODEL_THINKING=auto` 时保留供应商默认深度思考。如果长论文在网络较慢时仍超时，可逐步把模型超时调到 600 秒，而不影响论文源检索的失败反馈速度。

## 验证当前环境

```bash
conda activate scholaros
which python
python --version
python -m pip --version
python -c "from importlib.metadata import version; print(version('scholaros'))"
scholaros --help
pytest -q
```

预期 `which python` 指向 Conda 的 `envs/scholaros`，而不是 `base`。

## 常见问题

### 终端前缀显示多个 Conda 环境

先执行 `conda deactivate` 直到退出不需要的环境，再执行 `conda activate scholaros`。如果使用启动器，不需要手动激活环境：

```bash
./scholaros.sh serve
```

### `scholaros: command not found`

通常表示环境没有激活或项目尚未安装：

```bash
conda activate scholaros
python -m pip install -e ".[dev]"
```

如果不想激活环境，也可以明确把项目安装到指定环境：

```bash
cd /path/to/ScholarOS
conda run -n scholaros python -m pip install -e ".[dev]"
```

`./scholaros.sh` 检测失败时会同时显示“首次创建”和“已有环境补装”两类命令，但不会替你执行任何安装操作。

### 修改依赖后怎么同步？

```bash
conda env update -n scholaros -f environment.yml --prune
python -m pip install -e ".[dev]"
```

### 这是完全锁定的环境吗？

不是。`environment.yml` 固定 Python 主版本，Python 包使用 `pyproject.toml` 中的兼容版本范围，适合开发。若进入多人生产部署，应额外生成平台对应的 lock 文件，而不是手工把当前机器的所有包写死。
