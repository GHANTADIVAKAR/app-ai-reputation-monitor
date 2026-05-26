#!/usr/bin/env python3
"""
Validate the YouTube monitoring Google Sheet before showing a client report.

The checks are intentionally audit-friendly:
- confirms the latest date tab exists
- checks every row has a video_id, URL, title, channel and published_at
- detects duplicate video IDs inside each daily tab
- flags stale dashboard data
- verifies a deterministic sample of videos against YouTube Data API
- writes JSON + Markdown reports under data/audit_reports/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "data" / "audit_reports"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", default="", help="Date tab to audit, example 2026-05-19. Default: latest date tab.")
    parser.add_argument("--sample-size", type=int, default=20, help="How many rows to re-check against YouTube API.")
    parser.add_argument("--fresh-hours", type=int, default=30, help="Fail freshness if latest tab is older than this many hours.")
    args = parser.parse_args()

    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    youtube_api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not spreadsheet_id:
        raise RuntimeError("Missing GOOGLE_SPREADSHEET_ID in .env")
    if not youtube_api_key:
        raise RuntimeError("Missing YOUTUBE_API_KEY in .env")

    sheets = build_sheets_client()
    youtube = build("youtube", "v3", developerKey=youtube_api_key)

    tabs = get_date_tabs(sheets, spreadsheet_id)
    if not tabs:
        raise RuntimeError("No date tabs found in Google Sheet")
    tab = args.tab or tabs[-1]
    rows = read_tab_rows(sheets, spreadsheet_id, tab)

    audit = build_local_audit(tab, rows, tabs, args.fresh_hours)
    sample_ids = choose_sample_ids(rows, args.sample_size)
    audit["youtubeVerification"] = verify_with_youtube(youtube, rows, sample_ids)
    audit["status"] = final_status(audit)

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = AUDIT_DIR / f"youtube_audit_{tab}_{stamp}.json"
    md_path = AUDIT_DIR / f"youtube_audit_{tab}_{stamp}.md"
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(audit), encoding="utf-8")

    print(json.dumps({
        "status": audit["status"],
        "tab": tab,
        "rows": audit["rowCount"],
        "unique_video_ids": audit["uniqueVideoIds"],
        "duplicates": audit["duplicateVideoIdCount"],
        "missing_required_fields": audit["missingRequiredFieldCount"],
        "youtube_verified": audit["youtubeVerification"]["verifiedCount"],
        "youtube_critical_mismatches": audit["youtubeVerification"]["criticalMismatchCount"],
        "youtube_warnings": audit["youtubeVerification"]["warningCount"],
        "freshness": audit["freshness"]["status"],
        "json_report": str(json_path),
        "markdown_report": str(md_path),
    }, indent=2, ensure_ascii=False))
    return 0 if audit["status"] == "PASS" else 2


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


def get_date_tabs(sheets: Any, spreadsheet_id: str) -> list[str]:
    workbook = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tabs = []
    for sheet in workbook.get("sheets", []):
        title = sheet["properties"]["title"]
        try:
            datetime.strptime(title, "%Y-%m-%d")
        except ValueError:
            continue
        tabs.append(title)
    return sorted(tabs)


def read_tab_rows(sheets: Any, spreadsheet_id: str, tab: str) -> list[dict[str, str]]:
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(tab)}!A2:W",
    ).execute().get("values", [])
    rows = []
    for index, row in enumerate(values, start=2):
        padded = row + [""] * max(0, len(HEADERS) - len(row))
        record = dict(zip(HEADERS, padded))
        record["_row_number"] = str(index)
        if any(value.strip() for value in record.values()):
            rows.append(record)
    return rows


def build_local_audit(tab: str, rows: list[dict[str, str]], tabs: list[str], fresh_hours: int) -> dict[str, Any]:
    ids = [row.get("video_id", "").strip() for row in rows if row.get("video_id", "").strip()]
    id_counts = Counter(ids)
    duplicate_ids = sorted([video_id for video_id, count in id_counts.items() if count > 1])
    duplicate_rows = []
    by_id = defaultdict(list)
    for row in rows:
        by_id[row.get("video_id", "").strip()].append(row.get("_row_number"))
    for video_id in duplicate_ids[:50]:
        duplicate_rows.append({"video_id": video_id, "rows": by_id[video_id]})

    required = ["video_id", "url", "title", "channel_title", "published_at", "scraped_at"]
    missing_required = []
    bad_url = []
    stale_or_future = []
    now = datetime.now(timezone.utc)
    min_published = now - timedelta(hours=fresh_hours)
    latest_scraped_at = None

    for row in rows:
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            missing_required.append({"row": row["_row_number"], "missing": missing})
        video_id = row.get("video_id", "").strip()
        url = row.get("url", "").strip()
        if video_id and video_id not in url:
            bad_url.append({"row": row["_row_number"], "video_id": video_id, "url": url})
        scraped = parse_datetime(row.get("scraped_at", ""))
        if scraped and (latest_scraped_at is None or scraped > latest_scraped_at):
            latest_scraped_at = scraped
        published = parse_datetime(row.get("published_at", ""))
        if published and (published > now + timedelta(minutes=5) or published < min_published):
            stale_or_future.append({
                "row": row["_row_number"],
                "video_id": video_id,
                "published_at": row.get("published_at", ""),
            })

    freshness = assess_freshness(tab, latest_scraped_at, fresh_hours)
    return {
        "generatedAt": now.isoformat(),
        "tab": tab,
        "latestAvailableTab": tabs[-1] if tabs else "",
        "allDateTabs": tabs,
        "rowCount": len(rows),
        "uniqueVideoIds": len(set(ids)),
        "duplicateVideoIdCount": sum(id_counts[video_id] - 1 for video_id in duplicate_ids),
        "duplicateVideoIds": duplicate_rows,
        "missingRequiredFieldCount": len(missing_required),
        "missingRequiredFields": missing_required[:50],
        "badUrlCount": len(bad_url),
        "badUrls": bad_url[:50],
        "outOfFreshWindowCount": len(stale_or_future),
        "outOfFreshWindowRows": stale_or_future[:50],
        "freshness": freshness,
        "sentimentCounts": dict(Counter((row.get("sentiment") or "unknown").strip().lower() or "unknown" for row in rows)),
        "transcriptSources": dict(Counter((row.get("transcript_source") or "blank").strip().lower() or "blank" for row in rows)),
        "evidenceSources": dict(Counter((row.get("evidence_source") or row.get("transcript_source") or "blank").strip().lower() or "blank" for row in rows)),
        "topChannels": Counter(row.get("channel_title", "Unknown") or "Unknown" for row in rows).most_common(10),
    }


def assess_freshness(tab: str, latest_scraped_at: datetime | None, fresh_hours: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not latest_scraped_at:
        return {"status": "FAIL", "reason": "No scraped_at timestamp found", "latestScrapedAt": ""}
    age_hours = round((now - latest_scraped_at).total_seconds() / 3600, 2)
    status = "PASS" if age_hours <= fresh_hours else "FAIL"
    return {
        "status": status,
        "latestScrapedAt": latest_scraped_at.isoformat(),
        "ageHours": age_hours,
        "freshHoursLimit": fresh_hours,
        "tab": tab,
    }


def choose_sample_ids(rows: list[dict[str, str]], sample_size: int) -> list[str]:
    ids = sorted({row.get("video_id", "").strip() for row in rows if row.get("video_id", "").strip()})
    if sample_size <= 0:
        return []
    # Deterministic sample: same sheet produces same audit sample.
    ids.sort(key=lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())
    return ids[:sample_size]


def verify_with_youtube(youtube: Any, rows: list[dict[str, str]], sample_ids: list[str]) -> dict[str, Any]:
    row_by_id = {row.get("video_id", "").strip(): row for row in rows}
    if not sample_ids:
        return {
            "sampleSize": 0,
            "verifiedCount": 0,
            "criticalMismatchCount": 0,
            "warningCount": 0,
            "missingOnYoutubeCount": 0,
            "missingOnYoutube": [],
            "criticalMismatches": [],
            "warnings": [],
        }

    response = youtube.videos().list(
        part="snippet,statistics,contentDetails",
        id=",".join(sample_ids),
        maxResults=len(sample_ids),
    ).execute()
    items = {item["id"]: item for item in response.get("items", [])}
    critical_mismatches = []
    warnings = []
    missing = []
    for video_id in sample_ids:
        sheet_row = row_by_id.get(video_id, {})
        item = items.get(video_id)
        if not item:
            missing.append({"video_id": video_id, "sheet_row": sheet_row.get("_row_number")})
            continue
        snippet = item.get("snippet", {})
        expected_title = clean(sheet_row.get("title", ""))
        actual_title = clean(snippet.get("title", ""))
        expected_channel = clean(sheet_row.get("channel_title", ""))
        actual_channel = clean(snippet.get("channelTitle", ""))
        expected_published = sheet_row.get("published_at", "")[:19]
        actual_published = snippet.get("publishedAt", "")[:19]
        differences = []
        if expected_title and actual_title and expected_title != actual_title:
            differences.append("title")
        if expected_channel and actual_channel and expected_channel != actual_channel:
            differences.append("channel")
        if expected_published and actual_published and expected_published != actual_published:
            differences.append("published_at")
        if differences:
            warnings.append({
                "video_id": video_id,
                "sheet_row": sheet_row.get("_row_number"),
                "differences": differences,
                "sheet": {
                    "title": sheet_row.get("title", ""),
                    "channel": sheet_row.get("channel_title", ""),
                    "published_at": sheet_row.get("published_at", ""),
                },
                "youtube": {
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "published_at": snippet.get("publishedAt", ""),
                },
            })
    return {
        "sampleSize": len(sample_ids),
        "verifiedCount": len(items),
        "criticalMismatchCount": len(critical_mismatches),
        "warningCount": len(warnings),
        "missingOnYoutubeCount": len(missing),
        "missingOnYoutube": missing,
        "criticalMismatches": critical_mismatches[:25],
        "warnings": warnings[:25],
        "sampleVideoIds": sample_ids,
    }


def final_status(audit: dict[str, Any]) -> str:
    checks = [
        audit["duplicateVideoIdCount"] == 0,
        audit["missingRequiredFieldCount"] == 0,
        audit["badUrlCount"] == 0,
        audit["freshness"]["status"] == "PASS",
        audit["youtubeVerification"]["criticalMismatchCount"] == 0,
        audit["youtubeVerification"].get("missingOnYoutubeCount", 0) == 0,
    ]
    return "PASS" if all(checks) else "FAIL"


def render_markdown(audit: dict[str, Any]) -> str:
    lines = [
        f"# YouTube Data Audit - {audit['tab']}",
        "",
        f"Status: **{audit['status']}**",
        f"Generated at: `{audit['generatedAt']}`",
        "",
        "## Summary",
        f"- Rows audited: {audit['rowCount']}",
        f"- Unique video IDs: {audit['uniqueVideoIds']}",
        f"- Duplicate rows: {audit['duplicateVideoIdCount']}",
        f"- Missing required fields: {audit['missingRequiredFieldCount']}",
        f"- Bad URL/video ID mismatches: {audit['badUrlCount']}",
        f"- Freshness: {audit['freshness']['status']} ({audit['freshness'].get('ageHours', 'n/a')} hours old)",
        f"- YouTube sample verified: {audit['youtubeVerification']['verifiedCount']} / {audit['youtubeVerification']['sampleSize']}",
        f"- YouTube critical mismatches: {audit['youtubeVerification']['criticalMismatchCount']}",
        f"- YouTube mutable-field warnings: {audit['youtubeVerification']['warningCount']}",
        "",
        "## Sentiment Counts",
    ]
    for key, value in sorted(audit["sentimentCounts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Transcript Sources"])
    for key, value in sorted(audit["transcriptSources"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Evidence Sources"])
    for key, value in sorted(audit["evidenceSources"].items()):
        lines.append(f"- {key}: {value}")
    if audit["duplicateVideoIds"]:
        lines.extend(["", "## Duplicate Video IDs"])
        for item in audit["duplicateVideoIds"][:20]:
            lines.append(f"- `{item['video_id']}` rows: {', '.join(item['rows'])}")
    if audit["youtubeVerification"]["criticalMismatches"]:
        lines.extend(["", "## YouTube Critical Verification Mismatches"])
        for item in audit["youtubeVerification"]["criticalMismatches"][:10]:
            lines.append(f"- `{item['video_id']}` row {item['sheet_row']}: {', '.join(item['differences'])}")
    if audit["youtubeVerification"]["warnings"]:
        lines.extend(["", "## YouTube Mutable-Field Warnings"])
        lines.append("These usually happen when a creator edits a title or a live stream receives a final published time.")
        for item in audit["youtubeVerification"]["warnings"][:10]:
            lines.append(f"- `{item['video_id']}` row {item['sheet_row']}: {', '.join(item['differences'])}")
    return "\n".join(lines) + "\n"


def parse_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
