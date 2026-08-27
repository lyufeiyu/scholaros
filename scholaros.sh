#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONDA_ENV_NAME=${SCHOLAROS_CONDA_ENV:-scholaros}
export SCHOLAROS_PROJECT_DIR="$PROJECT_DIR"
# src 布局迁移后，旧的 editable 安装可能仍指向已删除的根目录包。
# 将当前 checkout 放在导入路径首位，让启动器可以先完成诊断并启动；
# 正式开发仍建议按提示重新执行一次 pip install -e。
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
CHECK_IMPORTS="from importlib.metadata import version; from pathlib import Path; import os, scholaros, fastapi, uvicorn, pydantic, pypdf, multipart, dotenv; version('scholaros'); assert Path(scholaros.__file__).resolve().parent == Path(os.environ['SCHOLAROS_PROJECT_DIR']) / 'src' / 'scholaros'"

if ! command -v conda >/dev/null 2>&1; then
  echo "ScholarOS：未找到 conda。请先安装 Miniconda/Anaconda，并按 docs/environment.md 创建环境。" >&2
  exit 2
fi

if [ "${CONDA_DEFAULT_ENV:-}" = "$CONDA_ENV_NAME" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
  if ! python -c "$CHECK_IMPORTS" >/dev/null 2>&1; then
    echo "ScholarOS：当前 Conda 环境尚未安装当前 checkout，或仍指向旧版本。" >&2
    echo "请在项目目录执行（迁移到 src 布局后需刷新 editable 安装）：python -m pip install -e \".[dev]\"" >&2
    exit 2
  fi
  cd "$PROJECT_DIR"
  exec python -m scholaros "$@"
fi

if [ -n "${VIRTUAL_ENV:-}" ] && [ "${CONDA_DEFAULT_ENV:-}" = "$CONDA_ENV_NAME" ]; then
  echo "ScholarOS：检测到嵌套 Python 虚拟环境 '$VIRTUAL_ENV'，将改用 Conda 环境 '$CONDA_ENV_NAME'。" >&2
fi

if ! conda run -n "$CONDA_ENV_NAME" python -c "$CHECK_IMPORTS" >/dev/null 2>&1; then
  echo "ScholarOS：Conda 环境 '$CONDA_ENV_NAME' 不存在，或尚未安装当前 checkout。" >&2
  echo "首次创建环境：" >&2
  echo "  cd \"$PROJECT_DIR\" && conda env create -f environment.yml" >&2
  echo "环境已存在但项目未安装、仍指向迁移前路径或依赖缺失：" >&2
  echo "  cd \"$PROJECT_DIR\" && conda run -n \"$CONDA_ENV_NAME\" python -m pip install -e \".[dev]\"" >&2
  echo "详细说明：$PROJECT_DIR/docs/environment.md" >&2
  exit 2
fi

cd "$PROJECT_DIR"
exec conda run --no-capture-output -n "$CONDA_ENV_NAME" python -m scholaros "$@"
