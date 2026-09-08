#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
  python3 -m venv .venv
fi
export PYTHONUTF8=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/python launcher.py "$@"
