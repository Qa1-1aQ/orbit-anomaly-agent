#!/usr/bin/env bash
# orbit-anomaly-agent MCP 服务可移植启动器
# 自动定位项目根目录与 Python 解释器，供 AGH / 任意 MCP 客户端以 stdio 方式拉起本服务。
# 用法：直接作为 MCP 客户端的 --stdio 可执行文件即可（无需参数）。
set -euo pipefail

# 1) 解析脚本所在目录（兼容软链：~/orbit-agent -> /mnt/d/.../orbit-agent）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 2) 定位项目根：脚本应与 tools/ 同级；若在子目录则上溯一级
if [ -d "$SCRIPT_DIR/tools" ]; then
  ROOT="$SCRIPT_DIR"
else
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

# 3) 选择 Python 解释器：优先项目内 venv，其次系统 python
PY=""
for cand in \
  "$ROOT/.venv/bin/python" \
  "$ROOT/.venv/Scripts/python.exe" \
  "$ROOT/venv/bin/python" \
  "$ROOT/venv/Scripts/python.exe"; do
  if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then PY="$(command -v python)"
  fi
fi
if [ -z "$PY" ]; then
  echo "ERROR: 未找到 Python 解释器。请先创建虚拟环境（python -m venv .venv）或安装 Python。" >&2
  exit 1
fi

# 4) 清除陈旧字节码缓存：改代码后 stdio 子进程若仍加载旧 .pyc 会导致口径回退（如基线 424.237）。
#    跨 WSL/Windows 软链下 Python 对 .pyc 的 mtime 判定不可靠，故每次启动强制清缓存、强制重编译。
find "$ROOT/tools" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 5) 进入项目根并以 stdio 方式启动 MCP 服务
cd "$ROOT"
exec "$PY" "$ROOT/tools/server.py" "$@"
