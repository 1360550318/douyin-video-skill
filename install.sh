#!/usr/bin/env bash
# douyin-video-skill 一键安装（macOS / Linux / Git Bash）
# 用法:
#   bash install.sh            装依赖 + 自检
#   bash install.sh --link     再装进 skill 目录
#   bash install.sh --check    只自检
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" install.py "$@"
