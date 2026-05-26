#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import certifi
import requests

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "live_app.sqlite3"
import sys

sys.path.insert(0, str(ROOT))


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, default=1)
    parser.add_argument("--source", choices=["x", "instagram", "all"], default="all")
    parser.add_argument("--limit-per-keyword", type=int, default=10)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    project = con.execute("SELECT * FROM projects WHERE id = ?", (args.project_id,)).fetchone()
    if not project:
        raise SystemExit(f"Project {args.project_id} not found")
    keywords = split_keywords(args.keywords or project["keywords"])

    inserted = {"x": 0, "instagram": 0}
    if args.source in {"x", "all"}:
        for keyword in keywords[:20]:
            inserted["x"] += ingest_x(con, project["id"], keyword, args.limit_per_keyword)
    if args.source in {"instagram", "all"}:
        for keyword in keywords[:20]:
            inserted["instagram"] += ingest_instagram_hashtag(con, project["id"], keyword, args.limit_per_keyword)
    con.commit()

    analyzed = failed = 0
    if args.analyze:
        result = analyze_pending_social(
            con,
            project,
            limit=sum(inserted.values()) or 50,
            source_type=None if args.source == "all" else args.source,
        )
        analyzed, failed = result["done"], result["failed"]
    print(json.dumps({"inserted": inserted, "analyzed": analyzed, "failed": failed}, indent=2, ensure_ascii=False))
    con.close()
    return 0


def ensure_schema(con: sqlite3.Connection) -> None:
    # Importing live_app.app runs init_db; this direct fallback keeps the script usable standalone.
    from live_app.app import init_db

    init_db()


def ingest_x(con: sqlite3.Connection, project_id: int, keyword: str, limit: int) -> int:
    api_key = os.getenv("GETXAPI_KEY", "").strip()
    if not api_key:
        return 0
    endpoint = "https://api.getxapi.com/twitter/tweet/advanced_search"
    params = urllib.parse.urlencode({"q": keyword, "product": "Latest"})
    payload = get_json(f"{endpoint}?{params}", {"Authorization": f"Bearer {api_key}"})
    tweets = extract_tweets(payload)[:limit]
    count = 0
    for tweet in tweets:
        source_id = str(tweet.get("id") or tweet.get("rest_id") or tweet.get("tweet_id") or "")
        text = str(tweet.get("text") or tweet.get("full_text") or tweet.get("legacy", {}).get("full_text") or "")
        if not source_id or not text:
            continue
        author = extract_author(tweet)
        url = tweet.get("url") or f"https://x.com/{author}/status/{source_id}" if author else f"https://x.com/i/web/status/{source_id}"
        metrics = extract_metrics(tweet)
        count += upsert_social_item(con, {
            "project_id": project_id,
            "source_type": "x",
            "source_id": source_id,
            "url": url,
            "author": author,
            "title": text[:140],
            "text": text,
            "caption": "",
            "media_type": detect_media_type(tweet),
            "media_url": first_media_url(tweet),
            "thumbnail_url": "",
            "published_at": str(tweet.get("created_at") or tweet.get("legacy", {}).get("created_at") or ""),
            "likes": metrics["likes"],
            "comments": metrics["comments"],
            "shares": metrics["shares"],
            "views": metrics["views"],
            "keyword": keyword,
            "raw_json": json.dumps(tweet, ensure_ascii=False)[:50000],
        })
    return count


def ingest_instagram_hashtag(con: sqlite3.Connection, project_id: int, keyword: str, limit: int) -> int:
    api_key = os.getenv("HIKER_API_KEY", "").strip()
    if not api_key:
        return 0
    tag = normalize_hashtag(keyword)
    if not tag:
        return 0
    urls = [
        f"https://api.hikerapi.com/v2/hashtag/medias/recent?name={urllib.parse.quote(tag)}",
        f"https://api.hikerapi.com/v1/hashtag/medias/recent?name={urllib.parse.quote(tag)}",
    ]
    payload = None
    last_error = None
    for url in urls:
        try:
            print(f"instagram hashtag {tag}: trying {urllib.parse.urlparse(url).path}", flush=True)
            payload = get_json(url, {"x-access-key": api_key, "accept": "application/json"})
            break
        except Exception as error:
            last_error = error
    if payload is None:
        print(f"instagram hashtag {tag} failed: {str(last_error)[:160]}")
        return 0
    medias = extract_instagram_media(payload)[:limit]
    print(f"instagram hashtag {tag}: received {len(medias)} media rows", flush=True)
    count = 0
    for media in medias:
        source_id = str(media.get("pk") or media.get("id") or media.get("code") or media.get("shortcode") or "")
        if not source_id:
            continue
        code = str(media.get("code") or media.get("shortcode") or "")
        caption = extract_caption(media)
        author = extract_ig_author(media)
        metrics = extract_ig_metrics(media)
        url = f"https://www.instagram.com/p/{code}/" if code else str(media.get("url") or media.get("permalink") or "")
        count += upsert_social_item(con, {
            "project_id": project_id,
            "source_type": "instagram",
            "source_id": source_id,
            "url": url,
            "author": author,
            "title": caption[:140] or f"Instagram #{tag}",
            "text": caption,
            "caption": caption,
            "media_type": str(media.get("media_type") or media.get("product_type") or ""),
            "media_url": str(media.get("video_url") or first_deep(media, ["video_url", "playback_url"]) or first_deep(media, ["image_versions2", "candidates", "url"]) or ""),
            "thumbnail_url": str(first_deep(media, ["thumbnail_url", "display_url"]) or ""),
            "published_at": str(media.get("taken_at") or media.get("taken_at_date") or ""),
            "likes": metrics["likes"],
            "comments": metrics["comments"],
            "shares": 0,
            "views": metrics["views"],
            "keyword": tag,
            "raw_json": json.dumps(media, ensure_ascii=False)[:50000],
        })
    return count


def upsert_social_item(con: sqlite3.Connection, item: dict[str, Any]) -> int:
    now = now_iso()
    try:
        con.execute(
            """INSERT INTO social_items
            (project_id, source_type, source_id, url, author, title, text, caption, media_type, media_url, thumbnail_url,
             published_at, likes, comments, shares, views, keyword, raw_json, discovered_at)
            VALUES (:project_id, :source_type, :source_id, :url, :author, :title, :text, :caption, :media_type, :media_url,
             :thumbnail_url, :published_at, :likes, :comments, :shares, :views, :keyword, :raw_json, :discovered_at)""",
            item | {"discovered_at": now},
        )
        return 1
    except sqlite3.IntegrityError:
        con.execute(
            """UPDATE social_items SET likes=:likes, comments=:comments, shares=:shares, views=:views, raw_json=:raw_json
            WHERE project_id=:project_id AND source_type=:source_type AND source_id=:source_id""",
            item,
        )
        return 0


def analyze_pending_social(con: sqlite3.Connection, project: sqlite3.Row, limit: int, source_type: str | None = None) -> dict[str, int]:
    source_clause = "AND si.source_type = ?" if source_type else ""
    params: tuple[Any, ...] = (project["id"], source_type, limit) if source_type else (project["id"], limit)
    rows = con.execute(
        f"""SELECT si.* FROM social_items si
        LEFT JOIN social_analyses sa ON sa.social_item_id = si.id AND sa.id = (SELECT max(id) FROM social_analyses WHERE social_item_id = si.id)
        WHERE si.project_id = ? {source_clause} AND (sa.status IS NULL OR sa.status = 'failed')
        ORDER BY si.views DESC, si.likes DESC LIMIT ?""",
        params,
    ).fetchall()
    done = failed = 0
    for row in rows:
        try:
            result = analyze_social_text(project, row)
            con.execute(
                """INSERT INTO social_analyses
                (social_item_id, status, sentiment, positive_pct, negative_pct, neutral_pct, confidence, summary, reason,
                 narrative_label, narrative_summary, evidence_source, provider, analyzed_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"], "done", result.get("sentiment", "unknown"), clamp(result.get("positive_pct", 0)),
                    clamp(result.get("negative_pct", 0)), clamp(result.get("neutral_pct", 100)), clamp(result.get("confidence", 0)),
                    result.get("summary", ""), result.get("reason", ""), result.get("narrative_label", ""),
                    result.get("narrative_summary", ""), result.get("evidence_source", ""), result.get("provider", "gemini-text"),
                    now_iso(), "",
                ),
            )
            con.commit()
            done += 1
        except Exception as error:
            con.execute(
                "INSERT INTO social_analyses (social_item_id, status, analyzed_at, evidence_source, error) VALUES (?, ?, ?, ?, ?)",
                (row["id"], "failed", now_iso(), f"{row['source_type']}-text", str(error)[:500]),
            )
            con.commit()
            failed += 1
    return {"done": done, "failed": failed}


def analyze_social_text(project: sqlite3.Row, item: sqlite3.Row) -> dict[str, Any]:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")
    evidence_source = f"{item['source_type']}-text"
    if item["media_type"] and item["source_type"] == "instagram" and item["media_url"]:
        evidence_source = "instagram-media-metadata"
    text = "\n".join([
        f"Source: {item['source_type']}",
        f"Author: {item['author']}",
        f"URL: {item['url']}",
        f"Text/caption: {item['text'] or item['caption']}",
        f"Media type: {item['media_type']}",
        f"Likes: {item['likes']}",
        f"Comments: {item['comments']}",
        f"Shares: {item['shares']}",
        f"Views: {item['views']}",
    ])
    prompt = {
        "task": f"Analyze sentiment and narrative toward {project['name']} from this social media item.",
        "rules": [
            "Use only supplied text/metadata. If media URL exists but media is not inspected, say evidence is limited.",
            "For X posts, judge author text.",
            "For Instagram captions, judge caption/metadata. Do not pretend to watch video unless audio/vision evidence exists.",
            "Return JSON only.",
        ],
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
        "evidence": text[:14000],
    }
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[json.dumps(prompt, ensure_ascii=False)],
    )
    result = parse_json(response.text or "{}")
    result["provider"] = "gemini-text"
    result["evidence_source"] = evidence_source
    return result


def get_json(url: str, headers: dict[str, str]) -> Any:
    response = requests.get(url, headers=headers, timeout=(5, 15))
    response.raise_for_status()
    return response.json()


def extract_tweets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("tweets", "data", "results", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        out = []
        collect_dicts_with_any(payload, out, {"full_text", "text"}, {"id", "rest_id"})
        return out
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def extract_instagram_media(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "medias", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return flatten_media(value)
        out = []
        collect_dicts_with_any(payload, out, {"caption", "code", "pk"}, {"id", "pk", "code"})
        return out
    if isinstance(payload, list):
        return flatten_media(payload)
    return []


def flatten_media(values: list[Any]) -> list[dict[str, Any]]:
    out = []
    for value in values:
        if isinstance(value, dict):
            media = value.get("media") if isinstance(value.get("media"), dict) else value
            out.append(media)
    return out


def collect_dicts_with_any(value: Any, out: list[dict[str, Any]], text_keys: set[str], id_keys: set[str]) -> None:
    if isinstance(value, dict):
        if any(k in value for k in text_keys) and any(k in value for k in id_keys):
            out.append(value)
        for child in value.values():
            collect_dicts_with_any(child, out, text_keys, id_keys)
    elif isinstance(value, list):
        for child in value:
            collect_dicts_with_any(child, out, text_keys, id_keys)


def extract_author(tweet: dict[str, Any]) -> str:
    user = tweet.get("user") or tweet.get("core", {}).get("user_results", {}).get("result", {}) or tweet.get("author", {})
    legacy = user.get("legacy", {}) if isinstance(user, dict) else {}
    return str(tweet.get("screen_name") or legacy.get("screen_name") or user.get("screen_name") or user.get("username") or "")


def extract_metrics(tweet: dict[str, Any]) -> dict[str, int]:
    legacy = tweet.get("legacy", {}) if isinstance(tweet.get("legacy"), dict) else {}
    return {
        "likes": to_int(tweet.get("favorite_count") or legacy.get("favorite_count")),
        "comments": to_int(tweet.get("reply_count") or legacy.get("reply_count")),
        "shares": to_int(tweet.get("retweet_count") or legacy.get("retweet_count")),
        "views": to_int(tweet.get("view_count") or tweet.get("views") or tweet.get("views_count")),
    }


def extract_caption(media: dict[str, Any]) -> str:
    caption = media.get("caption")
    if isinstance(caption, dict):
        return str(caption.get("text") or "")
    return str(caption or media.get("caption_text") or media.get("text") or "")


def extract_ig_author(media: dict[str, Any]) -> str:
    user = media.get("user") if isinstance(media.get("user"), dict) else {}
    return str(user.get("username") or media.get("username") or "")


def extract_ig_metrics(media: dict[str, Any]) -> dict[str, int]:
    return {"likes": to_int(media.get("like_count") or media.get("likes")), "comments": to_int(media.get("comment_count") or media.get("comments")), "views": to_int(media.get("view_count") or media.get("play_count"))}


def detect_media_type(tweet: dict[str, Any]) -> str:
    media_url = first_media_url(tweet)
    return "media" if media_url else "text"


def first_media_url(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("media_url_https", "media_url", "url", "video_url"):
            if key in value and isinstance(value[key], str) and value[key].startswith("http"):
                return value[key]
        for child in value.values():
            found = first_media_url(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_media_url(child)
            if found:
                return found
    return ""


def first_deep(value: Any, keys: list[str]) -> Any:
    if not keys:
        return value
    key = keys[0]
    if isinstance(value, dict):
        if key in value:
            return first_deep(value[key], keys[1:])
        for child in value.values():
            found = first_deep(child, keys)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = first_deep(child, keys)
            if found:
                return found
    return None


def normalize_hashtag(keyword: str) -> str:
    text = keyword.strip().lstrip("#")
    if " " in text:
        return ""
    return re.sub(r"[^A-Za-z0-9_]", "", text)


def split_keywords(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n,]+", value or "") if item.strip()]


def parse_json(text: str) -> dict[str, Any]:
    compact = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I).strip()
    try:
        return json.loads(compact)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", compact, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
