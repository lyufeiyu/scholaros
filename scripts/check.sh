#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

echo "[1/5] Ruff"
python -m ruff check src tests
echo "[2/5] Import path"
python -c 'from pathlib import Path; import scholaros; expected = (Path.cwd() / "src" / "scholaros").resolve(); actual = Path(scholaros.__file__).resolve().parent; assert actual == expected, f"scholaros 导入路径错误：{actual}（应为 {expected}，请重新执行 pip install -e .）"'
echo "[3/5] Pytest"
python -m pytest -q
echo "[4/5] JavaScript"
node --check src/scholaros/static/app.js
echo "[5/5] Shell"
sh -n scholaros.sh
echo "ScholarOS checks passed."
