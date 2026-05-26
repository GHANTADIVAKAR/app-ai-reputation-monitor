#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export LIVE_PORT="${LIVE_PORT:-8000}"
python3 live_app/app.py
