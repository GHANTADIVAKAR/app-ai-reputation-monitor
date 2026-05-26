#!/usr/bin/env python3
"""
Read the Google Sheets YouTube monitoring workbook and build a local dashboard
data file consumed by public/youtube-dashboard.html.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "public" / "youtube_dashboard_data.json"
EMBEDDED_JS_PATH = ROOT / "public" / "youtube_dashboard_embedded.js"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "scraped_at",
    "keyword",
    "youtube_type",
    "title",
    "channel_title",
    "video_id",
    "url",
    "published_at",
    "duration",
    "duration_seconds",
    "views",
    "likes",
    "comments",
    "description",
    "analysis_status",
    "sentiment",
    "positive_pct",
    "negative_pct",
    "neutral_pct",
    "sentiment_reason",
    "analysis_summary",
    "transcript_source",
    "analyzed_at",
    "narrative_label",
    "narrative_summary",
    "evidence_source",
]


def main() -> int:
    load_dotenv(ROOT / ".env")
    sheets = build_sheets_client()
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError("Missing GOOGLE_SPREADSHEET_ID in .env")

    workbook = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    date_tabs = [
        sheet["properties"]["title"]
        for sheet in workbook.get("sheets", [])
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", sheet["properties"]["title"])
    ]
    date_tabs.sort()

    days = []
    all_risky = []
    for tab in date_tabs:
        rows = read_tab_rows(sheets, spreadsheet_id, tab)
        day = summarize_day(tab, rows)
        days.append(day)
        all_risky.extend(day["riskyVideos"])

    all_risky.sort(key=lambda item: (item["negativePct"], item["views"]), reverse=True)
    latest = days[-1] if days else empty_day("")

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
        "latest": latest,
        "days": days,
        "overall": summarize_overall(days, all_risky),
        "topRiskyVideos": all_risky[:50],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    EMBEDDED_JS_PATH.write_text(
        "window.YOUTUBE_DASHBOARD_DATA = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dashboard_data": str(OUTPUT_PATH),
        "embedded_data": str(EMBEDDED_JS_PATH),
        "days": len(days),
        "latest_date": latest.get("date"),
        "latest_total": latest.get("totalVideos"),
        "latest_risk_score": latest.get("riskScore"),
    }, indent=2))
    return 0


def build_sheets_client() -> Any:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    elif service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=SCOPES)
    else:
        raise RuntimeError("Missing Google service account credentials in .env")
    return build("sheets", "v4", credentials=credentials)


def read_tab_rows(sheets: Any, spreadsheet_id: str, tab: str) -> list[dict[str, str]]:
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(tab)}!A2:Z",
    ).execute().get("values", [])

    records = []
    for row in values:
        padded = row + [""] * max(0, len(HEADERS) - len(row))
        record = dict(zip(HEADERS, padded))
        if record.get("video_id"):
            records.append(record)
    return records


def summarize_day(date: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    counts = Counter(normalize_sentiment(row.get("sentiment")) for row in rows)
    total = len(rows)
    positive_avg = average_pct(rows, "positive_pct")
    negative_avg = average_pct(rows, "negative_pct")
    neutral_avg = average_pct(rows, "neutral_pct")
    risk_score = calculate_risk_score(rows, counts, total)
    risky_videos = build_risky_videos(date, rows)

    return {
        "date": date,
        "totalVideos": total,
        "videos": sum(1 for row in rows if row.get("youtube_type") == "Video"),
        "shorts": sum(1 for row in rows if row.get("youtube_type") == "Shorts"),
        "positive": counts["positive"],
        "negative": counts["negative"],
        "neutral": counts["neutral"],
        "mixed": counts["mixed"],
        "unknown": counts["unknown"],
        "positiveAvg": positive_avg,
        "negativeAvg": negative_avg,
        "neutralAvg": neutral_avg,
        "riskScore": risk_score,
        "riskLevel": risk_level(risk_score),
        "topChannels": top_channels(rows),
        "riskyVideos": risky_videos,
    }


def build_risky_videos(date: str, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    risky = []
    for row in rows:
        negative_pct = to_int(row.get("negative_pct"))
        sentiment = normalize_sentiment(row.get("sentiment"))
        if sentiment != "negative" and negative_pct < 40:
            continue
        risky.append({
            "date": date,
            "title": row.get("title", ""),
            "channel": row.get("channel_title", ""),
            "url": row.get("url", ""),
            "videoId": row.get("video_id", ""),
            "youtubeType": row.get("youtube_type", ""),
            "views": to_int(row.get("views")),
            "likes": to_int(row.get("likes")),
            "comments": to_int(row.get("comments")),
            "sentiment": sentiment,
            "positivePct": to_int(row.get("positive_pct")),
            "negativePct": negative_pct,
            "neutralPct": to_int(row.get("neutral_pct")),
            "reason": row.get("sentiment_reason", ""),
            "summary": row.get("analysis_summary", ""),
            "narrativeLabel": row.get("narrative_label", ""),
            "narrativeSummary": row.get("narrative_summary", ""),
            "evidenceSource": row.get("evidence_source", "") or row.get("transcript_source", ""),
            "publishedAt": row.get("published_at", ""),
        })
    risky.sort(key=lambda item: (item["negativePct"], item["views"], item["comments"]), reverse=True)
    return risky[:30]


def summarize_overall(days: list[dict[str, Any]], risky: list[dict[str, Any]]) -> dict[str, Any]:
    if not days:
        return empty_day("")
    return {
        "totalDays": len(days),
        "totalVideos": sum(day["totalVideos"] for day in days),
        "totalNegative": sum(day["negative"] for day in days),
        "totalPositive": sum(day["positive"] for day in days),
        "totalNeutral": sum(day["neutral"] for day in days),
        "averageRiskScore": round(sum(day["riskScore"] for day in days) / len(days)),
        "highestRiskDay": max(days, key=lambda day: day["riskScore"]),
        "riskVideoCount": len(risky),
    }


def calculate_risk_score(rows: list[dict[str, str]], counts: Counter, total: int) -> int:
    if total == 0:
        return 0
    negative_share = counts["negative"] / total
    high_negative = sum(1 for row in rows if to_int(row.get("negative_pct")) >= 60)
    high_negative_share = high_negative / total
    average_negative = average_pct(rows, "negative_pct") / 100
    score = (negative_share * 45) + (high_negative_share * 35) + (average_negative * 20)
    return min(100, round(score))


def top_channels(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    counts = Counter(row.get("channel_title", "Unknown") or "Unknown" for row in rows)
    return [{"channel": channel, "count": count} for channel, count in counts.most_common(10)]


def average_pct(rows: list[dict[str, str]], key: str) -> int:
    values = [to_int(row.get(key)) for row in rows if row.get(key) not in ("", None)]
    if not values:
        return 0
    return round(sum(values) / len(values))


def normalize_sentiment(value: str | None) -> str:
    value = (value or "").strip().lower()
    if value in {"positive", "negative", "neutral", "mixed"}:
        return value
    return "unknown"


def risk_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def to_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def empty_day(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "totalVideos": 0,
        "videos": 0,
        "shorts": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "mixed": 0,
        "unknown": 0,
        "positiveAvg": 0,
        "negativeAvg": 0,
        "neutralAvg": 0,
        "riskScore": 0,
        "riskLevel": "low",
        "topChannels": [],
        "riskyVideos": [],
    }


def quote_sheet_name(sheet_name: str) -> str:
    return f"'{sheet_name.replace("'", "''")}'"


if __name__ == "__main__":
    raise SystemExit(main())
