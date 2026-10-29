#!/usr/bin/env sh
# Chạy dashboard trên Linux/macOS (tương đương start.bat). Dùng .venv nếu có.
cd "$(dirname "$0")" || exit 1
[ -f .venv/bin/activate ] && . .venv/bin/activate
exec python3 dashboard.py "$@"
