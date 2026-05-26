#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/analyze_youtube_sheet.py \
  --reanalyze \
  --enable-audio \
  --require-audio \
  --batch-size 10 "$@"

python3 scripts/build_youtube_dashboard.py
