#!/usr/bin/env python3
"""
Analyze YouTube links stored in the daily Google Sheet.

Evidence priority:
1. YouTube transcript/captions when available
2. Optional audio download + Gemini/OpenAI transcription when enabled
3. Metadata only when deep audio mode is disabled

For client-grade reports, run with:
  python3 scripts/analyze_youtube_sheet.py --reanalyze --enable-audio --require-audio
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from google import genai
except Exception:
    genai = None

try:
    from yt_dlp import YoutubeDL
except Exception:
    YoutubeDL = None


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "youtube_scrape_state.json"
AUDIO_CACHE_DIR = ROOT / "data" / "audio_cache"
TRANSCRIPT_CACHE_DIR = ROOT / "data" / "transcript_cache"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

BASE_HEADERS = [
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
]

ANALYSIS_HEADERS = [
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

PRIORITY_HEADERS = [
    "channel_id",
    "subscriber_count",
    "audio_priority",
    "priority_reason",
]

POSITIVE_TERMS = [
    "support", "positive", "good", "great", "excellent", "super", "mass", "vera level",
    "leader", "win", "victory", "cm", "chief minister", "hope", "people support",
    "fans support", "welcome", "strong", "best", "honest", "change", "success",
]

NEGATIVE_TERMS = [
    "negative", "bad", "worst", "waste", "against", "critic", "criticism", "attack",
    "troll", "controversy", "problem", "fake", "fail", "failure", "flop", "angry",
    "arrest", "case", "corrupt", "corruption", "hate", "oppose", "risk",
]


@dataclass
class Analysis:
    status: str
    sentiment: str
    positive_pct: int
    negative_pct: int
    neutral_pct: int
    reason: str
    summary: str
    transcript_source: str
    analyzed_at: str
    narrative_label: str
    narrative_summary: str
    evidence_source: str

    def values(self) -> list[str]:
        return [
            self.status,
            self.sentiment,
            str(self.positive_pct),
            str(self.negative_pct),
            str(self.neutral_pct),
            self.reason,
            self.summary,
            self.transcript_source,
            self.analyzed_at,
            self.narrative_label,
            self.narrative_summary,
            self.evidence_source,
        ]


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", default="")
    parser.add_argument("--target-name", default=os.getenv("ANALYSIS_TARGET_NAME", "Thalapathy Vijay / Actor Vijay / TVK"))
    parser.add_argument("--timezone", default=os.getenv("REPORT_TIMEZONE", "Asia/Kolkata"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--reanalyze", action="store_true")
    parser.add_argument("--enable-audio", action="store_true", help="When captions are missing, download and transcribe audio.")
    parser.add_argument("--require-audio", action="store_true", help="Do not fall back to metadata when transcript/audio is unavailable.")
    parser.add_argument("--keep-audio", action="store_true", help="Keep downloaded audio files in data/audio_cache.")
    parser.add_argument("--sleep-seconds", type=float, default=float(os.getenv("ANALYSIS_SLEEP_SECONDS", "0") or 0))
    parser.add_argument("--priority-only", action="store_true", help="Only analyze rows where audio_priority is yes.")
    args = parser.parse_args()

    sheets = build_sheets_client()
    spreadsheet_id = get_spreadsheet_id()
    worksheet = args.worksheet or current_worksheet_name(args.timezone)
    enable_audio = args.enable_audio or env_bool("ENABLE_AUDIO_TRANSCRIPTION")
    require_audio = args.require_audio or env_bool("REQUIRE_AUDIO_TRANSCRIPTION")
    keep_audio = args.keep_audio or env_bool("KEEP_AUDIO_FILES")

    ensure_analysis_headers(sheets, spreadsheet_id, worksheet)
    rows = read_rows(sheets, spreadsheet_id, worksheet)
    if len(rows) <= 1:
        print("No YouTube rows found to analyze.")
        return 0

    header = normalize_row(rows[0], len(BASE_HEADERS) + len(ANALYSIS_HEADERS) + len(PRIORITY_HEADERS))
    analyzed = 0
    skipped = 0
    failed = 0
    processed = 0
    pending_updates: list[tuple[int, Analysis]] = []

    for sheet_row_number, row in enumerate(rows[1:], start=2):
        row = normalize_row(row, len(header))
        record = dict(zip(header, row))
        if not record.get("video_id"):
            skipped += 1
            continue
        if args.priority_only and record.get("audio_priority", "").strip().lower() != "yes":
            skipped += 1
            continue
        if record.get("analysis_status") == "done" and not args.reanalyze:
            skipped += 1
            continue
        if args.limit and processed >= args.limit:
            break

        try:
            analysis = analyze_record(record, target_name=args.target_name, enable_audio=enable_audio, require_audio=require_audio, keep_audio=keep_audio)
            processed += 1
            if analysis.status == "failed":
                failed += 1
            else:
                analyzed += 1
            pending_updates.append((sheet_row_number, analysis))
        except Exception as error:
            processed += 1
            pending_updates.append((sheet_row_number, failed_analysis(str(error))))
            failed += 1

        if len(pending_updates) >= args.batch_size:
            write_analysis_batch(sheets, spreadsheet_id, worksheet, pending_updates)
            pending_updates = []
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    if pending_updates:
        write_analysis_batch(sheets, spreadsheet_id, worksheet, pending_updates)

    print(json.dumps({
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
        "worksheet": worksheet,
        "audio_enabled": enable_audio,
        "audio_required": require_audio,
        "analyzed_rows": analyzed,
        "skipped_rows": skipped,
        "failed_rows": failed,
        "processed_rows": processed,
    }, indent=2))
    return 0


def analyze_record(record: dict[str, str], target_name: str, enable_audio: bool, require_audio: bool, keep_audio: bool) -> Analysis:
    text, source = get_video_text(record, target_name=target_name, enable_audio=enable_audio, keep_audio=keep_audio)
    text = text.strip()
    if not text:
        if require_audio:
            return Analysis(
                status="failed",
                sentiment="unknown",
                positive_pct=0,
                negative_pct=0,
                neutral_pct=100,
                reason="No transcript/audio evidence available. Metadata fallback is disabled for client-grade audio reports.",
                summary="Audio-based summary could not be created.",
                transcript_source=source,
                analyzed_at=now_iso(),
                narrative_label="unknown",
                narrative_summary="No transcript/audio evidence available.",
                evidence_source=source,
            )
        text = fallback_text(record)
        source = "metadata"

    if source == "gemini-audio":
        try:
            return analysis_from_result(parse_json_from_text(text), source)
        except Exception:
            pass
    if os.getenv("ANALYSIS_PROVIDER", "").strip().lower() == "gemini" and os.getenv("GEMINI_API_KEY", "").strip():
        return analyze_with_gemini_text(record, text, source, target_name)
    if os.getenv("OPENAI_API_KEY", "").strip():
        return analyze_with_openai(record, text, source, target_name)
    return analyze_locally(record, text, source)


def get_video_text(record: dict[str, str], target_name: str, enable_audio: bool, keep_audio: bool) -> tuple[str, str]:
    video_id = record.get("video_id", "").strip()
    if not video_id:
        return "", "missing-video-id"

    cached = read_cached_transcript(video_id)
    if cached:
        return cached["text"], cached["source"]

    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ta", "en", "hi", "te"])
        text = " ".join(item.get("text", "") for item in transcript)
        if text.strip():
            write_cached_transcript(video_id, text, "youtube-transcript")
            return text, "youtube-transcript"
    except Exception:
        pass

    if enable_audio:
        try:
            text = transcribe_video_audio(record, target_name=target_name, keep_audio=keep_audio)
            if text.strip():
                source = "gemini-audio" if os.getenv("TRANSCRIPTION_PROVIDER", "").strip().lower() == "gemini" else "audio-transcription"
                write_cached_transcript(video_id, text, source)
                return text, source
            return "", "audio-empty"
        except Exception as error:
            return "", f"audio-failed: {str(error)[:120]}"

    return "", "metadata"


def fallback_text(record: dict[str, str]) -> str:
    return " ".join([
        record.get("title", ""),
        record.get("channel_title", ""),
        record.get("description", ""),
    ]).strip()


def analyze_locally(record: dict[str, str], text: str, source: str) -> Analysis:
    lowered = text.lower()
    positive_hits = sum(lowered.count(term) for term in POSITIVE_TERMS)
    negative_hits = sum(lowered.count(term) for term in NEGATIVE_TERMS)

    if positive_hits == 0 and negative_hits == 0:
        positive_pct, negative_pct, neutral_pct = 10, 10, 80
    else:
        total_signal = positive_hits + negative_hits
        positive_pct = round((positive_hits / total_signal) * 80) if total_signal else 10
        negative_pct = round((negative_hits / total_signal) * 80) if total_signal else 10
        neutral_pct = max(0, 100 - positive_pct - negative_pct)

    sentiment = dominant_sentiment(positive_pct, negative_pct, neutral_pct)
    narrative_label, narrative_summary = infer_narrative(text, record)
    return Analysis(
        status="done",
        sentiment=sentiment,
        positive_pct=positive_pct,
        negative_pct=negative_pct,
        neutral_pct=neutral_pct,
        reason=f"Local analyzer on {source}: {positive_hits} positive signals, {negative_hits} negative signals.",
        summary=summarize_text(text),
        transcript_source=source,
        analyzed_at=now_iso(),
        narrative_label=narrative_label,
        narrative_summary=narrative_summary,
        evidence_source=source,
    )


def analyze_with_openai(record: dict[str, str], text: str, source: str, target_name: str) -> Analysis:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_ANALYSIS_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    prompt = {
        "task": f"Analyze the narrative and sentiment toward {target_name} in this YouTube evidence.",
        "rules": [
            "Base the report on the supplied transcript/audio text when evidence_source is transcript or audio.",
            "Do not overstate certainty. If the text is unclear, classify neutral or mixed.",
            "Return only JSON.",
        ],
        "required_json": {
            "sentiment": "positive|negative|neutral|mixed",
            "positive_pct": "0-100 integer",
            "negative_pct": "0-100 integer",
            "neutral_pct": "0-100 integer",
            "reason": "short reason based on evidence",
            "summary": "short video summary",
            "narrative_label": "one short label for the main narrative",
            "narrative_summary": "what story/angle the video is pushing",
        },
        "video": {
            "title": record.get("title", ""),
            "channel": record.get("channel_title", ""),
            "url": record.get("url", ""),
            "evidence_source": source,
            "text": text[:12000],
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful political/media narrative analyst. Return only valid JSON."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result = json.loads(payload["choices"][0]["message"]["content"])
    positive_pct = clamp_pct(result.get("positive_pct", 0))
    negative_pct = clamp_pct(result.get("negative_pct", 0))
    neutral_pct = clamp_pct(result.get("neutral_pct", 0))
    sentiment = str(result.get("sentiment") or dominant_sentiment(positive_pct, negative_pct, neutral_pct)).lower()

    return Analysis(
        status="done",
        sentiment=sentiment,
        positive_pct=positive_pct,
        negative_pct=negative_pct,
        neutral_pct=neutral_pct,
        reason=str(result.get("reason", ""))[:500],
        summary=str(result.get("summary", ""))[:1000],
        transcript_source=source,
        analyzed_at=now_iso(),
        narrative_label=str(result.get("narrative_label", "unknown"))[:120],
        narrative_summary=str(result.get("narrative_summary", ""))[:1000],
        evidence_source=source,
    )


def analyze_with_gemini_text(record: dict[str, str], text: str, source: str, target_name: str) -> Analysis:
    result = gemini_generate_json(record, text, source, target_name)
    return analysis_from_result(result, source)


def analyze_with_gemini_audio(record: dict[str, str], audio_path: Path, target_name: str = "the target") -> Analysis:
    if genai is None:
        raise RuntimeError("google-genai package is not installed")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for Gemini audio analysis")
    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    uploaded = client.files.upload(file=str(audio_path))
    response = client.models.generate_content(
        model=model,
        contents=[
            gemini_prompt(record, "audio-transcription", target_name),
            uploaded,
        ],
    )
    result = parse_json_from_text(response.text or "{}")
    return analysis_from_result(result, "gemini-audio")


def gemini_generate_json(record: dict[str, str], text: str, source: str, target_name: str) -> dict[str, Any]:
    if genai is None:
        raise RuntimeError("google-genai package is not installed")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for Gemini analysis")
    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    response = client.models.generate_content(
        model=model,
        contents=[
            gemini_prompt(record, source, target_name),
            text[:12000],
        ],
    )
    return parse_json_from_text(response.text or "{}")


def gemini_prompt(record: dict[str, str], source: str, target_name: str) -> str:
    return json.dumps({
        "task": f"Analyze the YouTube audio/transcript narrative and sentiment toward {target_name}.",
        "rules": [
            "Use the supplied audio/transcript evidence as the primary source.",
            "Return only valid JSON. No markdown.",
            "If unclear, choose neutral or mixed and explain why.",
        ],
        "required_json": {
            "transcript": "brief transcript or transcript summary",
            "sentiment": "positive|negative|neutral|mixed",
            "positive_pct": "0-100 integer",
            "negative_pct": "0-100 integer",
            "neutral_pct": "0-100 integer",
            "reason": "short reason based on evidence",
            "summary": "short video summary",
            "narrative_label": "one short label for the main narrative",
            "narrative_summary": "what story/angle the video is pushing",
        },
        "video": {
            "title": record.get("title", ""),
            "channel": record.get("channel_title", ""),
            "url": record.get("url", ""),
            "evidence_source": source,
        },
    }, ensure_ascii=False)


def analysis_from_result(result: dict[str, Any], source: str) -> Analysis:
    positive_pct = clamp_pct(result.get("positive_pct", 0))
    negative_pct = clamp_pct(result.get("negative_pct", 0))
    neutral_pct = clamp_pct(result.get("neutral_pct", 0))
    sentiment = str(result.get("sentiment") or dominant_sentiment(positive_pct, negative_pct, neutral_pct)).lower()
    summary = str(result.get("summary") or result.get("transcript") or "")[:1000]
    return Analysis(
        status="done",
        sentiment=sentiment,
        positive_pct=positive_pct,
        negative_pct=negative_pct,
        neutral_pct=neutral_pct,
        reason=str(result.get("reason", ""))[:500],
        summary=summary,
        transcript_source=source,
        analyzed_at=now_iso(),
        narrative_label=str(result.get("narrative_label", "unknown"))[:120],
        narrative_summary=str(result.get("narrative_summary", ""))[:1000],
        evidence_source=source,
    )


def transcribe_video_audio(record: dict[str, str], target_name: str, keep_audio: bool) -> str:
    provider = os.getenv("TRANSCRIPTION_PROVIDER", "openai").strip().lower()
    if provider == "gemini":
        audio_path = download_audio(record)
        try:
            analysis = analyze_with_gemini_audio(record, audio_path, target_name)
            return json.dumps({
                "sentiment": analysis.sentiment,
                "positive_pct": analysis.positive_pct,
                "negative_pct": analysis.negative_pct,
                "neutral_pct": analysis.neutral_pct,
                "reason": analysis.reason,
                "summary": analysis.summary,
                "narrative_label": analysis.narrative_label,
                "narrative_summary": analysis.narrative_summary,
            }, ensure_ascii=False)
        finally:
            if not keep_audio:
                try:
                    audio_path.unlink(missing_ok=True)
                except Exception:
                    pass

    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required for audio transcription")
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    audio_path = download_audio(record)
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
        model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip()
        with audio_path.open("rb") as audio_file:
            result = client.audio.transcriptions.create(model=model, file=audio_file, response_format="text")
        return str(result)
    finally:
        if not keep_audio:
            try:
                audio_path.unlink(missing_ok=True)
            except Exception:
                pass


def download_audio(record: dict[str, str]) -> Path:
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp package is not installed")
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    video_id = record.get("video_id", "").strip()
    url = record.get("url", "").strip() or f"https://www.youtube.com/watch?v={video_id}"
    safe = safe_id(video_id)
    output_template = str(AUDIO_CACHE_DIR / f"{safe}.%(ext)s")
    max_filesize_mb = int(os.getenv("AUDIO_MAX_FILESIZE_MB", "24"))
    options = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_filesize_mb * 1024 * 1024,
    }
    before = set(AUDIO_CACHE_DIR.glob(f"{safe}.*"))
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        downloaded = Path(downloader.prepare_filename(info))
    candidates = [downloaded] if downloaded.exists() else []
    candidates.extend(path for path in AUDIO_CACHE_DIR.glob(f"{safe}.*") if path not in before)
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError("audio download did not produce a file")


def failed_analysis(reason: str) -> Analysis:
    return Analysis(
        status="failed",
        sentiment="unknown",
        positive_pct=0,
        negative_pct=0,
        neutral_pct=100,
        reason=reason[:500],
        summary="Analysis failed for this row.",
        transcript_source="error",
        analyzed_at=now_iso(),
        narrative_label="unknown",
        narrative_summary="Could not determine narrative because analysis failed.",
        evidence_source="error",
    )


def read_cached_transcript(video_id: str) -> dict[str, str] | None:
    path = TRANSCRIPT_CACHE_DIR / f"{safe_id(video_id)}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("text"):
        return {"text": payload["text"], "source": payload.get("source", "cached-transcript")}
    return None


def write_cached_transcript(video_id: str, text: str, source: str) -> None:
    TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_CACHE_DIR / f"{safe_id(video_id)}.json"
    path.write_text(json.dumps({
        "video_id": video_id,
        "source": source,
        "cached_at": now_iso(),
        "text": text,
    }, ensure_ascii=False), encoding="utf-8")


def build_sheets_client() -> Any:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    elif service_account_json:
        credentials = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=SCOPES)
    else:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON in .env")
    return build("sheets", "v4", credentials=credentials)


def get_spreadsheet_id() -> str:
    spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    if spreadsheet_id:
        return spreadsheet_id
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if state.get("spreadsheet_id"):
            return state["spreadsheet_id"]
    raise RuntimeError("Missing GOOGLE_SPREADSHEET_ID in .env")


def ensure_analysis_headers(sheets: Any, spreadsheet_id: str, worksheet: str) -> None:
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(worksheet)}!O1:Z1",
        valueInputOption="RAW",
        body={"values": [ANALYSIS_HEADERS]},
    ).execute()


def read_rows(sheets: Any, spreadsheet_id: str, worksheet: str) -> list[list[str]]:
    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(worksheet)}!A:AD",
    ).execute()
    return result.get("values", [])


def write_analysis_batch(sheets: Any, spreadsheet_id: str, worksheet: str, updates: list[tuple[int, Analysis]]) -> None:
    if not updates:
        return
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": [
                {
                    "range": f"{quote_sheet_name(worksheet)}!O{row_number}:Z{row_number}",
                    "values": [analysis.values()],
                }
                for row_number, analysis in updates
            ],
        },
    ).execute()


def current_worksheet_name(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")


def normalize_row(row: list[str], width: int) -> list[str]:
    return row + [""] * max(0, width - len(row))


def dominant_sentiment(positive_pct: int, negative_pct: int, neutral_pct: int) -> str:
    return max({"positive": positive_pct, "negative": negative_pct, "neutral": neutral_pct}, key={"positive": positive_pct, "negative": negative_pct, "neutral": neutral_pct}.get)


def summarize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:900]


def infer_narrative(text: str, record: dict[str, str]) -> tuple[str, str]:
    compact = summarize_text(text)
    lowered = compact.lower()
    checks = [
        ("political momentum", ["cm", "election", "campaign", "rally", "vote", "party", "tvk"]),
        ("controversy or criticism", ["controversy", "critic", "problem", "against", "attack", "case", "troll"]),
        ("support or fan mobilisation", ["support", "fans", "mass", "welcome", "leader", "victory", "people"]),
        ("news update", ["breaking", "latest", "news", "live", "update"]),
        ("entertainment or cinema", ["movie", "film", "song", "trailer", "cinema"]),
    ]
    for label, terms in checks:
        if any(term in lowered for term in terms):
            return label, compact or fallback_text(record)[:900]
    return "general mention", compact or fallback_text(record)[:900]


def clamp_pct(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except Exception:
        return 0
    return max(0, min(100, number))


def parse_json_from_text(text: str) -> dict[str, Any]:
    compact = (text or "").strip()
    if compact.startswith("```"):
        compact = re.sub(r"^```(?:json)?", "", compact, flags=re.IGNORECASE).strip()
        compact = re.sub(r"```$", "", compact).strip()
    try:
        return json.loads(compact)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", compact, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
