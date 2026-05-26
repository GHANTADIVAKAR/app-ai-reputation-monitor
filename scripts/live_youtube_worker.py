#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_app.app import analyze_project, analyze_project_text, app, collect_youtube, db, init_db  # noqa: E402
from scripts.build_live_report_dashboard import main as build_report  # noqa: E402


def main() -> int:
    init_db()
    interval_minutes = int(os.getenv("YOUTUBE_WORKER_INTERVAL_MINUTES", "60"))
    scan_hours = int(os.getenv("YOUTUBE_SCAN_HOURS", "24"))
    audio_limit = int(os.getenv("YOUTUBE_AUDIO_ANALYZE_LIMIT", "25"))
    text_limit = int(os.getenv("YOUTUBE_TEXT_ANALYZE_LIMIT", "200"))
    once = os.getenv("WORKER_RUN_ONCE", "").strip().lower() in {"1", "true", "yes"}

    while True:
        with app.app_context():
            projects = db().execute("SELECT * FROM projects ORDER BY id").fetchall()
            for project in projects:
                print(f"[APP.AI worker] collect project={project['id']} hours={scan_hours}", flush=True)
                try:
                    scan = collect_youtube(project, hours=scan_hours)
                    print(f"[APP.AI worker] scan={scan}", flush=True)
                except Exception as error:
                    print(f"[APP.AI worker] collect failed: {str(error)[:500]}", flush=True)

                print(f"[APP.AI worker] audio priority analysis limit={audio_limit}", flush=True)
                try:
                    result = analyze_project(project, limit=audio_limit, priority_only=True)
                    print(f"[APP.AI worker] audio={result}", flush=True)
                except Exception as error:
                    print(f"[APP.AI worker] audio failed: {str(error)[:500]}", flush=True)

                print(f"[APP.AI worker] text fallback analysis limit={text_limit}", flush=True)
                try:
                    result = analyze_project_text(project, limit=text_limit, priority_only=False)
                    print(f"[APP.AI worker] text={result}", flush=True)
                except Exception as error:
                    print(f"[APP.AI worker] text failed: {str(error)[:500]}", flush=True)

            try:
                build_report()
            except Exception as error:
                print(f"[APP.AI worker] report build failed: {str(error)[:500]}", flush=True)

        if once:
            return 0
        print(f"[APP.AI worker] sleeping {interval_minutes} minutes", flush=True)
        time.sleep(max(1, interval_minutes) * 60)


if __name__ == "__main__":
    raise SystemExit(main())
