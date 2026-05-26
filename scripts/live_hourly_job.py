from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_app.app import analyze_project, app, collect_youtube, db  # noqa: E402


def main() -> None:
    hours = int(os.getenv("LIVE_SCAN_HOURS", "1"))
    analyze_limit = int(os.getenv("LIVE_ANALYZE_LIMIT", "5"))
    with app.app_context():
        projects = db().execute("SELECT * FROM projects ORDER BY id").fetchall()
        for project in projects:
            print(f"[APP.AI] scanning project={project['id']} name={project['name']} hours={hours}")
            scan = collect_youtube(project, hours=hours)
            print(f"[APP.AI] found={scan['found']} new={scan['new']} priority={scan['priority']}")
            if analyze_limit > 0:
                print(f"[APP.AI] analyzing priority videos limit={analyze_limit}")
                result = analyze_project(project, limit=analyze_limit, priority_only=True)
                print(f"[APP.AI] done={result['done']} failed={result['failed']} skipped={result['skipped']}")


if __name__ == "__main__":
    main()
