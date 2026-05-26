#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data
python3 scripts/youtube_to_sheets.py "$@" >> data/youtube_scraper.log 2>&1
