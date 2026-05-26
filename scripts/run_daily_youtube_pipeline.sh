#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data
python3 scripts/youtube_to_sheets.py >> data/youtube_scraper.log 2>&1
python3 scripts/analyze_youtube_sheet.py >> data/youtube_analyzer.log 2>&1
python3 scripts/build_youtube_dashboard.py >> data/youtube_dashboard.log 2>&1
