#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi
import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "live_app.sqlite3"
HTML_PATH = ROOT / "public" / "dragon-instagram-dashboard.html"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def main() -> int:
    load_dotenv(ROOT / ".env")
    diagnostic = hiker_diagnostic()
    rows = load_instagram_rows()
    sheet_url = write_google_sheet(rows, diagnostic)
    write_dashboard(rows, diagnostic, sheet_url)
    print(json.dumps({
        "instagram_rows": len(rows),
        "hiker_status": diagnostic["status"],
        "hiker_error": diagnostic["error"],
        "dashboard": str(HTML_PATH),
        "sheet_url": sheet_url,
    }, indent=2, ensure_ascii=False))
    return 0


def hiker_diagnostic() -> dict:
    key = os.getenv("HIKER_API_KEY", "").strip()
    if not key:
        return {"status": "missing_key", "error": "HIKER_API_KEY is missing", "checked_at": now_iso()}
    url = "https://api.hikerapi.com/v1/hashtag/medias/recent?name=DragonGlimpse"
    try:
        response = requests.get(url, headers={"x-access-key": key, "accept": "application/json"}, timeout=(5, 15))
        if response.status_code == 402:
            return {
                "status": "payment_required",
                "error": "Hiker API returned 402 Payment Required for Instagram hashtag media. Paid credits or endpoint access must be enabled before scraping can collect rows.",
                "checked_at": now_iso(),
            }
        if response.status_code == 403:
            return {
                "status": "blocked",
                "error": "Hiker API returned 403 Forbidden. Check that this API key is active and allowed to use Instagram hashtag media endpoints.",
                "checked_at": now_iso(),
            }
        response.raise_for_status()
        payload = response.json()
        return {"status": "ok", "error": "", "checked_at": now_iso(), "sample": str(payload)[:500]}
    except Exception as error:
        return {"status": "blocked", "error": str(error), "checked_at": now_iso()}


def load_instagram_rows() -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute(
        """
        SELECT si.*, sa.status analysis_status, sa.sentiment, sa.positive_pct, sa.negative_pct, sa.neutral_pct,
               sa.confidence, sa.summary, sa.reason, sa.evidence_source, sa.provider, sa.error analysis_error
        FROM social_items si
        LEFT JOIN social_analyses sa ON sa.social_item_id = si.id AND sa.id = (SELECT max(id) FROM social_analyses WHERE social_item_id = si.id)
        WHERE si.project_id = 1 AND si.source_type = 'instagram'
        ORDER BY si.discovered_at DESC, si.likes DESC
        """
    )]
    con.close()
    return rows


def write_google_sheet(rows: list[dict], diagnostic: dict) -> str:
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SPREADSHEET_ID missing in .env")
    service = build("sheets", "v4", credentials=google_credentials())
    title = "Dragon_Instagram_Test"
    ensure_sheet(service, spreadsheet_id, title)
    headers = [
        "source_type", "source_id", "url", "author", "title", "caption", "media_type", "media_url",
        "published_at", "likes", "comments", "views", "keyword", "analysis_status", "sentiment",
        "positive_pct", "negative_pct", "neutral_pct", "confidence", "evidence_source", "provider",
        "summary", "reason", "error",
    ]
    values = [["APP.AI Instagram/Hiker diagnostic", diagnostic["status"], diagnostic["error"], diagnostic["checked_at"]], [], headers]
    for row in rows:
        values.append([row.get(header, "") for header in headers])
    if not rows:
        values.append(["instagram", "", "", "", "No Instagram rows collected yet", "", "", "", "", "", "", "", "DragonGlimpse/NTRNeel", diagnostic["status"], "unknown", 0, 0, 100, 0, "hiker-api", "", "Hiker API returned no usable Instagram hashtag data", diagnostic["error"]])
    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{title}'!A:Z").execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={sheet_id(service, spreadsheet_id, title)}"


def google_credentials():
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or str(ROOT / "service-account.json")
    return service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)


def ensure_sheet(service, spreadsheet_id: str, title: str) -> None:
    workbook = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    if any(sheet["properties"]["title"] == title for sheet in workbook.get("sheets", [])):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()


def sheet_id(service, spreadsheet_id: str, title: str) -> int:
    workbook = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in workbook.get("sheets", []):
        if sheet["properties"]["title"] == title:
            return sheet["properties"]["sheetId"]
    return 0


def write_dashboard(rows: list[dict], diagnostic: dict, sheet_url: str) -> None:
    status_class = "ok" if diagnostic["status"] == "ok" else "blocked"
    next_step = "Hiker is ready. Run the Instagram scrape again." if diagnostic["status"] == "ok" else "Enable paid hashtag-media access/credits in Hiker, then rerun the Instagram scrape."
    table_rows = "\n".join(render_row(row) for row in rows) or f"""
      <tr>
        <td colspan="8" class="empty">No Instagram data collected yet. Hiker diagnostic: {escape(diagnostic['status'])} - {escape(diagnostic['error'])}</td>
      </tr>
    """
    HTML_PATH.write_text(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APP.AI Dragon Instagram Test</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    body{{margin:0;font-family:Inter,system-ui,sans-serif;background:#f6f9ff;color:#101828}}
    main{{width:min(1160px,calc(100% - 32px));margin:28px auto}}
    .hero{{background:linear-gradient(135deg,#111827,#2563eb,#0d9488);color:white;border-radius:26px;padding:40px;box-shadow:0 24px 80px rgba(37,99,235,.22)}}
    .hero p{{color:rgba(255,255,255,.78)}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}}
    article,.panel{{background:white;border:1px solid #e6edf5;border-radius:18px;box-shadow:0 18px 60px rgba(16,24,40,.08)}}
    article{{padding:18px}} article span{{color:#667085;display:block}} article strong{{font-size:30px;display:block;margin-top:8px}}
    .panel{{padding:20px}} .status{{display:inline-flex;border-radius:999px;padding:8px 12px;font-weight:800}} .ok{{background:#dcfae6;color:#067647}} .blocked{{background:#fee4e2;color:#b42318}}
    .notice{{border-left:4px solid #f04438;background:#fff4f3;padding:14px 16px;border-radius:12px;color:#7a271a}}
    a{{color:#175cd3;font-weight:800;text-decoration:none}} table{{width:100%;border-collapse:collapse;min-width:1000px}} th,td{{border-top:1px solid #e6edf5;padding:12px;text-align:left;vertical-align:top;font-size:14px}} th{{background:#f8fbff;text-transform:uppercase;color:#475467;font-size:12px}} .wrap{{overflow:auto}} .empty{{text-align:center;color:#667085;padding:34px}}
    @media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <p>APP.AI Instagram Test</p>
    <h1>Dragon Instagram / Hiker Dashboard</h1>
    <p>Same Dragon project as YouTube. This page will show Instagram hashtag/profile data once Hiker API access is active.</p>
    <a href="{sheet_url}" target="_blank" style="color:white">Open Google Sheet</a>
  </section>
  <section class="grid">
    <article><span>Instagram Rows</span><strong>{len(rows)}</strong></article>
    <article><span>Hiker Status</span><strong><span class="status {status_class}">{escape(diagnostic['status'])}</span></strong></article>
    <article><span>Analyzed</span><strong>{sum(1 for row in rows if row.get('analysis_status') == 'done')}</strong></article>
    <article><span>Failed</span><strong>{sum(1 for row in rows if row.get('analysis_status') == 'failed')}</strong></article>
  </section>
  <section class="panel">
    <h2>Diagnostic</h2>
    <p><strong>Status:</strong> {escape(diagnostic['status'])}</p>
    <p><strong>Error:</strong> {escape(diagnostic['error'])}</p>
    <p><strong>Checked:</strong> {escape(diagnostic['checked_at'])}</p>
    <p class="notice"><strong>Next step:</strong> {escape(next_step)}</p>
  </section>
  <section class="panel">
    <h2>Instagram Audit Table</h2>
    <div class="wrap"><table><thead><tr><th>Post</th><th>Author</th><th>Media</th><th>Engagement</th><th>Keyword</th><th>Sentiment</th><th>Evidence</th><th>Summary / Error</th></tr></thead><tbody>{table_rows}</tbody></table></div>
  </section>
</main>
</body>
</html>""", encoding="utf-8")


def render_row(row: dict) -> str:
    return f"""<tr>
      <td><a href="{escape(row.get('url',''))}" target="_blank">{escape(row.get('title') or row.get('caption') or row.get('source_id'))}</a></td>
      <td>{escape(row.get('author',''))}</td>
      <td>{escape(row.get('media_type',''))}</td>
      <td>{row.get('likes',0)} likes<br>{row.get('comments',0)} comments<br>{row.get('views',0)} views</td>
      <td>{escape(row.get('keyword',''))}</td>
      <td>{escape(row.get('sentiment','pending'))}<br>{row.get('positive_pct',0)}/{row.get('negative_pct',0)}/{row.get('neutral_pct',0)}</td>
      <td>{escape(row.get('evidence_source',''))}</td>
      <td>{escape(row.get('summary') or row.get('reason') or row.get('analysis_error',''))}</td>
    </tr>"""


def escape(value) -> str:
    import html

    return html.escape(str(value or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
