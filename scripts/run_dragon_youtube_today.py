#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_app.app import (  # noqa: E402
    analyze_video_text,
    analyze_video_with_gemini,
    app,
    build_text_evidence,
    collect_youtube,
    db,
    download_audio,
    insert_analysis,
    parse_json,
)
from scripts.build_live_report_dashboard import render_html  # noqa: E402

PROJECT_ID = 1
HOURS = 24
OUTPUT_PATH = ROOT / "public" / "ntr-dragon-today-dashboard.html"


def main() -> int:
    with app.app_context():
        project = db().execute("SELECT * FROM projects WHERE id = ?", (PROJECT_ID,)).fetchone()
        if not project:
            raise SystemExit(f"Project {PROJECT_ID} not found")

        scan = {"found": 0, "new": 0, "priority": 0, "skipped": True}
        if os.getenv("SKIP_YOUTUBE_SCAN", "").strip().lower() not in {"1", "true", "yes"}:
            print(f"[APP.AI] YouTube scan: project={project['name']} hours={HOURS}", flush=True)
            try:
                scan = collect_youtube(project, hours=HOURS)
                print(f"[APP.AI] scan result: {scan}", flush=True)
            except Exception as error:
                scan = {"found": 0, "new": 0, "priority": 0, "error": str(error)[:500]}
                print(f"[APP.AI] scan failed, using already collected rows: {str(error)[:200]}", flush=True)

        since = datetime.now(timezone.utc) - timedelta(hours=HOURS)
        priority_rows = today_rows(project["id"], since, priority=True)
        print(f"[APP.AI] today's priority rows: {len(priority_rows)}", flush=True)
        audio_done = audio_failed = 0
        for row in priority_rows:
            if has_done_audio(row["id"]):
                continue
            try:
                print(f"[APP.AI] audio analyzing: {row['video_id']} | {row['channel_title']}", flush=True)
                result = analyze_video_with_gemini(project, row)
                insert_analysis(row["id"], "done", result)
                audio_done += 1
            except Exception as error:
                insert_analysis(row["id"], "failed", {"error": str(error)[:500], "evidence_source": "gemini-audio"})
                audio_failed += 1
                print(f"[APP.AI] audio failed: {row['video_id']} | {str(error)[:160]}", flush=True)
            time.sleep(2)

        text_rows = today_rows(project["id"], since, needs_text=True)
        print(f"[APP.AI] today's text/fallback rows: {len(text_rows)}", flush=True)
        text_done = text_failed = 0
        for row in text_rows:
            try:
                print(f"[APP.AI] text analyzing: {row['video_id']} | {row['channel_title']}", flush=True)
                result = analyze_video_text(project, row)
                insert_analysis(row["id"], "done", result)
                text_done += 1
            except Exception as error:
                insert_analysis(row["id"], "failed", {"error": str(error)[:500], "evidence_source": "text-analysis"})
                text_failed += 1
                print(f"[APP.AI] text failed: {row['video_id']} | {str(error)[:160]}", flush=True)
            time.sleep(1)

        data = load_today_report_data(project["id"], since)
        OUTPUT_PATH.write_text(render_html(data), encoding="utf-8")
        result = {
            "scan": scan,
            "audio_done": audio_done,
            "audio_failed": audio_failed,
            "text_done": text_done,
            "text_failed": text_failed,
            "report": str(OUTPUT_PATH),
            "summary": data["summary"],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    return 0


def today_rows(project_id: int, since: datetime, priority: bool = False, needs_text: bool = False):
    since_value = since.isoformat().replace("+00:00", "Z")
    where = ["v.project_id = ?", "v.published_at >= ?"]
    params: list[object] = [project_id, since_value]
    if priority:
        where.append("v.audio_priority = 'yes'")
    if needs_text:
        where.append(
            """(
              a.status IS NULL
              OR a.status = 'failed'
              OR (v.audio_priority != 'yes' AND a.evidence_source = 'gemini-audio')
            )"""
        )
    return db().execute(
        f"""
        SELECT v.*, a.status analysis_status, a.evidence_source
        FROM videos v
        LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        WHERE {" AND ".join(where)}
        ORDER BY v.audio_priority = 'yes' DESC, COALESCE(v.subscriber_count, 0) DESC, v.views DESC
        """,
        params,
    ).fetchall()


def has_done_audio(video_db_id: int) -> bool:
    row = db().execute(
        "SELECT 1 FROM analyses WHERE video_id = ? AND status = 'done' AND evidence_source IN ('gemini-audio', 'openai-audio') LIMIT 1",
        (video_db_id,),
    ).fetchone()
    return row is not None


def analyze_video_audio_openai(project, video) -> dict:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")
    audio_path = download_audio(video["url"], video["video_id"])
    try:
        client = OpenAI(api_key=api_key)
        with audio_path.open("rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
                file=audio_file,
                response_format="text",
            )
        text = str(transcript or "").strip()
        if not text:
            raise RuntimeError("OpenAI transcription returned empty text")
        return analyze_text_openai(project, video, f"AUDIO TRANSCRIPT:\n{text}", "openai-audio")
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass


def analyze_video_text_openai(project, video) -> dict:
    text, source = build_text_evidence(video)
    if not text.strip():
        raise RuntimeError("No transcript, comments, title, or description available for text analysis")
    return analyze_text_openai(project, video, text, source)


def analyze_text_openai(project, video, text: str, source: str) -> dict:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")
    client = OpenAI(api_key=api_key)
    prompt = {
        "task": f"Analyze sentiment and narrative toward {project['name']}.",
        "rules": [
            "Use only the supplied evidence.",
            "If evidence_source is openai-audio, treat the transcript as creator/video speech.",
            "If comments are present, comments are audience reaction, not necessarily creator opinion.",
            "Be conservative when evidence is only metadata.",
            "Return JSON only.",
        ],
        "video": {
            "title": video["title"],
            "channel": video["channel_title"],
            "url": video["url"],
            "evidence_source": source,
            "views": video["views"],
            "comments": video["comments"],
        },
        "evidence": text[:14000],
        "required_json": {
            "sentiment": "positive|negative|neutral|mixed",
            "positive_pct": "0-100",
            "negative_pct": "0-100",
            "neutral_pct": "0-100",
            "confidence": "0-100",
            "summary": "short summary",
            "reason": "why this sentiment",
            "narrative_label": "short label",
            "narrative_summary": "main story or angle",
        },
    }
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "You are a precise media sentiment analyst. Return valid JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0.1,
    )
    raw = response.choices[0].message.content or "{}"
    result = parse_json(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip())
    result["provider"] = "openai"
    result["evidence_source"] = source
    return result


def load_today_report_data(project_id: int, since: datetime) -> dict:
    from collections import Counter

    since_value = since.isoformat().replace("+00:00", "Z")
    project = dict(db().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    rows = [dict(row) for row in db().execute(
        """
        WITH latest_analysis AS (
          SELECT a.*, row_number() over(partition by a.video_id order by a.id desc) rn
          FROM analyses a
        )
        SELECT
          v.id AS db_video_id,
          v.video_id AS youtube_video_id,
          v.url,
          v.title,
          v.channel_title,
          v.subscriber_count,
          v.audio_priority,
          v.youtube_type,
          v.published_at,
          v.duration_seconds,
          v.views,
          v.likes,
          v.comments,
          v.keyword,
          coalesce(a.status, 'pending') AS analysis_status,
          coalesce(a.sentiment, 'pending') AS sentiment,
          coalesce(a.positive_pct, 0) AS positive_pct,
          coalesce(a.negative_pct, 0) AS negative_pct,
          coalesce(a.neutral_pct, 0) AS neutral_pct,
          coalesce(a.confidence, 0) AS confidence,
          coalesce(a.narrative_label, '') AS narrative_label,
          coalesce(a.narrative_summary, '') AS narrative_summary,
          coalesce(a.summary, '') AS summary,
          coalesce(a.reason, '') AS reason,
          coalesce(a.evidence_source, '') AS evidence_source,
          coalesce(a.provider, '') AS provider,
          coalesce(a.analyzed_at, '') AS analyzed_at,
          coalesce(a.error, '') AS error
        FROM videos v
        LEFT JOIN latest_analysis a ON a.video_id = v.id AND a.rn = 1
        WHERE v.project_id = ? AND v.published_at >= ?
        ORDER BY v.audio_priority = 'yes' DESC, coalesce(v.subscriber_count, 0) DESC, v.views DESC
        """,
        (project_id, since_value),
    )]

    def normalize(value: str) -> str:
        value = (value or "unknown").strip().lower()
        return value if value in {"positive", "negative", "neutral", "mixed", "pending"} else "unknown"

    def evidence(source: str, provider: str) -> str:
        if source in {"gemini-audio", "openai-audio"}:
            return "audio"
        if source == "youtube-transcript":
            return "transcript"
        if source == "metadata-comments":
            return "metadata + comments"
        if source == "metadata":
            return "metadata"
        if provider == "gemini-text":
            return "text"
        return "pending" if not source else "other"

    sentiment_counts = Counter(normalize(row["sentiment"]) for row in rows)
    evidence_counts = Counter(evidence(row["evidence_source"], row["provider"]) for row in rows)
    status_counts = Counter(row["analysis_status"] for row in rows)
    analyzed = sum(1 for row in rows if row["analysis_status"] == "done")
    priority = sum(1 for row in rows if row["audio_priority"] == "yes")
    failed = sum(1 for row in rows if row["analysis_status"] == "failed")
    risk_base = sum(sentiment_counts[k] for k in ("positive", "negative", "neutral", "mixed"))
    risk = round(((sentiment_counts["negative"] + sentiment_counts["mixed"] * 0.5) / risk_base) * 100) if risk_base else 0
    top_risky = sorted(
        [row for row in rows if normalize(row["sentiment"]) in {"negative", "mixed"} or int(row["negative_pct"] or 0) >= 35],
        key=lambda row: (int(row["negative_pct"] or 0), int(row["views"] or 0), int(row["comments"] or 0)),
        reverse=True,
    )[:20]
    top_channels = Counter(row["channel_title"] for row in rows).most_common(12)
    return {
        "project": project | {"description": project["description"] + " Today-only YouTube report for the last 24 hours."},
        "generated_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "summary": {
            "total": len(rows),
            "analyzed": analyzed,
            "priority": priority,
            "failed": failed,
            "risk": risk,
            "sentiment_counts": dict(sentiment_counts),
            "evidence_counts": dict(evidence_counts),
            "status_counts": dict(status_counts),
        },
        "rows": rows,
        "top_risky": top_risky,
        "top_channels": top_channels,
    }


if __name__ == "__main__":
    raise SystemExit(main())
