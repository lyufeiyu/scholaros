# ScholarOS Environment Configuration

**English** | [简体中文](./zh-CN/environment.md)

## Environment strategy

ScholarOS runs in a dedicated Conda environment named `scholaros` by default:

- Conda manages Python 3.12 and environment isolation.
- `pyproject.toml` is the single source of truth for runtime and development Python dependencies.
- pip installs the current ScholarOS project into the Conda environment.
- `.env` stores local configuration and secrets only; it does not enter `environment.yml` or version control.
- The project does not require or automatically create a second project-local Python environment.

Installing into `base` is discouraged. It works technically, but FastAPI, Pydantic, and other versions may interfere with unrelated projects.

## First installation

Run from the project root:

```bash
cd /path/to/ScholarOS
conda env create -f environment.yml
conda activate scholaros
scholaros
```

`environment.yml` creates a Python 3.12 environment and performs an editable pip installation. Its `.` means the current directory, so enter the project root before creating or updating the environment; paths containing spaces are supported.

If the environment already exists:

```bash
conda env update -n scholaros -f environment.yml --prune
```

`--prune` removes Conda dependencies no longer declared in the YAML file. Confirm that the environment is dedicated to ScholarOS before using it.

## What `pip install -e ".[dev]"` means

| Fragment | Meaning |
|---|---|
| `pip install` | Use pip from the current Python environment |
| `.` | Install the current directory by reading `pyproject.toml` |
| `-e` | Editable installation; source remains linked to the checkout, so most Python edits need no reinstall |
| `[dev]` | Also install the optional `dev` dependency group from `pyproject.toml` |
| `"..."` | Prevent zsh from interpreting square brackets as a glob expression |

The current `dev` group includes pytest, pytest-asyncio, httpx, Ruff, build, and setuptools. Runtime dependencies such as FastAPI, Uvicorn, Pydantic, and pypdf are installed with or without `[dev]`.

Common forms:

```bash
# Local development: tests and checks included; source edits take effect directly
python -m pip install -e ".[dev]"

# Runtime dependencies only, still editable
python -m pip install -e .

# Regular non-editable installation
python -m pip install .
```

Prefer `python -m pip`; it explicitly uses the pip associated with the current `python`, reducing accidental installation into another environment.

## Daily use

After activation:

```bash
conda activate scholaros
scholaros
```

Start the Web workspace:

```bash
scholaros serve
```

### Command or import failures after moving the repository

An editable install created when the package lived under a root-level `scholaros/` directory may still point to that old path. After migration to `src/scholaros/`, reinstall once:

```bash
cd /path/to/ScholarOS
conda activate scholaros
python -m pip install -e ".[dev]"
scholaros serve
```

If you do not want to refresh the installation immediately, use the repository launcher. It puts the current `src/` on the import path and prints explicit diagnostics:

```bash
./scholaros.sh serve
```

To run without activating the environment:

```bash
./scholaros.sh
./scholaros.sh serve
```

The launcher uses `conda run -n scholaros` internally and does not change the current shell prompt. It only checks and starts an existing environment; it never creates an environment, installs dependencies, or changes Conda configuration silently. `--no-capture-output` connects input and output directly to the terminal, preserving the interactive menu, live logs, and `Ctrl+C`.

To select another prepared Conda environment:

```bash
SCHOLAROS_CONDA_ENV=my-research-env ./scholaros.sh serve
```

Or activate and install into it directly:

```bash
conda activate my-research-env
python -m pip install -e ".[dev]"
scholaros serve
```

## Model and paper-source configuration

Copy the local configuration template:

```bash
cp .env.example .env
```

Main variables:

| Variable | Purpose |
|---|---|
| `SCHOLAROS_HOME` | SQLite and artifact directory; defaults to `.scholaros` |
| `SCHOLAROS_MODEL` | OpenAI-compatible model name |
| `SCHOLAROS_API_BASE` | Model-provider API root |
| `SCHOLAROS_API_KEY_ENV` | Name of the environment variable holding the real model key |
| `SCHOLAROS_MODEL_TIMEOUT_SECONDS` | Read timeout for a non-streaming model response; defaults to 300 seconds, allowed range 30–1800 |
| `SCHOLAROS_MODEL_THINKING` | `auto` preserves the provider default; `enabled` and `disabled` are also accepted |
| `SCHOLAROS_SOURCE_TIMEOUT_SECONDS` | Paper-source network timeout; defaults to 25 seconds, allowed range 5–300 |
| `SCHOLAROS_CONTACT_EMAIL` | Contact email for Crossref/OpenAlex polite usage |
| `SEMANTIC_SCHOLAR_API_KEY` | Optional Semantic Scholar key |
| `IEEE_XPLORE_API_KEY` | Official IEEE Xplore API key |

`.env` is ignored by Git. Never put real secrets into `environment.yml`, README files, or source code.

An `HTTP 401` during natural-language search means the request reached the model service but authentication or authorization was rejected; it is not a paper-source error. Check in order:

1. `SCHOLAROS_API_KEY_ENV` exactly matches the variable holding the key, for example `DEEPSEEK_API_KEY`.
2. `SCHOLAROS_API_BASE` is the provider's OpenAI-compatible root and does not duplicate `/chat/completions`.
3. `SCHOLAROS_MODEL` is a public model name available to the account. An invalid model often returns 400, but should still be verified.
4. Stop the old server completely and start it again after editing `.env`; refreshing the browser does not reload server configuration.

Errors display the effective model, API base, and key variable name—but never the key value—to diagnose a wrong `.env` or an old process.

ScholarOS explicitly sends `stream: false` and waits for one complete model response. Model and paper-source timeouts are independent. `SCHOLAROS_MODEL_THINKING=auto` preserves the provider's default reasoning behavior. If long drafts still time out on a slow connection, increase the model timeout gradually to 600 seconds without slowing paper-source failure feedback.

## Verify the environment

```bash
conda activate scholaros
which python
python --version
python -m pip --version
python -c "from importlib.metadata import version; print(version('scholaros'))"
scholaros --help
pytest -q
```

`which python` should point into Conda's `envs/scholaros`, not `base`.

## Troubleshooting

### The prompt displays multiple Conda environments

Run `conda deactivate` until the unwanted environments are gone, then run `conda activate scholaros`. The launcher does not require manual activation:

```bash
./scholaros.sh serve
```

### `scholaros: command not found`

The environment is usually inactive or the package is not installed:

```bash
conda activate scholaros
python -m pip install -e ".[dev]"
```

To install explicitly into the target environment without activating it:

```bash
cd /path/to/ScholarOS
conda run -n scholaros python -m pip install -e ".[dev]"
```

When `./scholaros.sh` detects a failure, it prints separate instructions for first-time environment creation and installation into an existing environment; it does not execute either operation for you.

### How do I synchronize dependency changes?

```bash
conda env update -n scholaros -f environment.yml --prune
python -m pip install -e ".[dev]"
```

### Is the environment fully locked?

No. `environment.yml` fixes the Python major/minor version while Python packages use compatible ranges from `pyproject.toml`, which suits development. A multi-user production deployment should generate platform-specific lock files rather than manually freezing every package from one machine.
