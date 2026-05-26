from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from werkzeug.security import check_password_hash, generate_password_hash
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

try:
    from google import genai
except Exception:
    genai = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("LIVE_DATA_DIR", str(ROOT / "data"))).expanduser()
DB_PATH = Path(os.getenv("LIVE_DB_PATH", str(DATA_DIR / "live_app.sqlite3"))).expanduser()

load_dotenv(ROOT / ".env")
socket.setdefaulttimeout(int(os.getenv("HTTP_TIMEOUT_SECONDS", "45")))

app = Flask(__name__)
app.secret_key = os.getenv("LIVE_APP_SECRET", "app-ai-local-dev-secret-change-before-production")


@app.template_filter("fmt_int")
def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


@app.template_filter("local_time")
def local_time(value: str) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return value


@app.context_processor
def inject_now() -> dict[str, Any]:
    return {"app_name": "APP.AI", "current_year": datetime.now().year}


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: Exception | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS organizations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          org_id INTEGER NOT NULL,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'client_admin',
          created_at TEXT NOT NULL,
          FOREIGN KEY(org_id) REFERENCES organizations(id)
        );

        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          org_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          target_type TEXT NOT NULL,
          description TEXT DEFAULT '',
          keywords TEXT NOT NULL,
          relevance_terms TEXT DEFAULT '',
          noise_terms TEXT DEFAULT '',
          subscriber_threshold INTEGER NOT NULL DEFAULT 30000,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(org_id) REFERENCES organizations(id)
        );

        CREATE TABLE IF NOT EXISTS scans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          hours INTEGER NOT NULL,
          found_count INTEGER DEFAULT 0,
          new_count INTEGER DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'running',
          notes TEXT DEFAULT '',
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS videos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          video_id TEXT NOT NULL,
          url TEXT NOT NULL,
          title TEXT NOT NULL,
          channel_title TEXT DEFAULT '',
          channel_id TEXT DEFAULT '',
          subscriber_count INTEGER,
          audio_priority TEXT DEFAULT 'unknown',
          priority_reason TEXT DEFAULT '',
          youtube_type TEXT DEFAULT '',
          published_at TEXT DEFAULT '',
          duration_seconds INTEGER DEFAULT 0,
          views INTEGER DEFAULT 0,
          likes INTEGER DEFAULT 0,
          comments INTEGER DEFAULT 0,
          description TEXT DEFAULT '',
          keyword TEXT DEFAULT '',
          discovered_at TEXT NOT NULL,
          UNIQUE(project_id, video_id),
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS analyses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          sentiment TEXT DEFAULT 'unknown',
          positive_pct INTEGER DEFAULT 0,
          negative_pct INTEGER DEFAULT 0,
          neutral_pct INTEGER DEFAULT 100,
          confidence INTEGER DEFAULT 0,
          narrative_label TEXT DEFAULT '',
          narrative_summary TEXT DEFAULT '',
          summary TEXT DEFAULT '',
          reason TEXT DEFAULT '',
          evidence_source TEXT DEFAULT '',
          analyzed_at TEXT NOT NULL,
          provider TEXT DEFAULT '',
          error TEXT DEFAULT '',
          FOREIGN KEY(video_id) REFERENCES videos(id)
        );

        CREATE TABLE IF NOT EXISTS user_project_access (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          project_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(user_id, project_id),
          FOREIGN KEY(user_id) REFERENCES users(id),
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS human_reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          review_sentiment TEXT NOT NULL,
          review_note TEXT DEFAULT '',
          action_status TEXT DEFAULT 'reviewed',
          created_at TEXT NOT NULL,
          FOREIGN KEY(video_id) REFERENCES videos(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS accuracy_labels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          video_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          human_sentiment TEXT NOT NULL,
          ai_sentiment TEXT DEFAULT '',
          match INTEGER DEFAULT 0,
          notes TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(video_id) REFERENCES videos(id),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS verification_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          analysis_id INTEGER NOT NULL,
          video_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          status TEXT NOT NULL,
          sentiment TEXT DEFAULT 'unknown',
          confidence INTEGER DEFAULT 0,
          agrees_with_primary INTEGER DEFAULT 0,
          reason TEXT DEFAULT '',
          summary TEXT DEFAULT '',
          verified_at TEXT NOT NULL,
          error TEXT DEFAULT '',
          FOREIGN KEY(analysis_id) REFERENCES analyses(id),
          FOREIGN KEY(video_id) REFERENCES videos(id)
        );

        CREATE TABLE IF NOT EXISTS alert_rules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          min_subscribers INTEGER DEFAULT 0,
          min_views INTEGER DEFAULT 0,
          sentiment TEXT DEFAULT 'negative',
          negative_pct INTEGER DEFAULT 50,
          keywords TEXT DEFAULT '',
          destination TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS alert_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          rule_id INTEGER NOT NULL,
          video_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          message TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(rule_id) REFERENCES alert_rules(id),
          FOREIGN KEY(video_id) REFERENCES videos(id)
        );

        CREATE TABLE IF NOT EXISTS data_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          source_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned',
          config_json TEXT DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS social_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL,
          source_type TEXT NOT NULL,
          source_id TEXT NOT NULL,
          url TEXT DEFAULT '',
          author TEXT DEFAULT '',
          title TEXT DEFAULT '',
          text TEXT DEFAULT '',
          caption TEXT DEFAULT '',
          media_type TEXT DEFAULT '',
          media_url TEXT DEFAULT '',
          thumbnail_url TEXT DEFAULT '',
          published_at TEXT DEFAULT '',
          likes INTEGER DEFAULT 0,
          comments INTEGER DEFAULT 0,
          shares INTEGER DEFAULT 0,
          views INTEGER DEFAULT 0,
          keyword TEXT DEFAULT '',
          raw_json TEXT DEFAULT '{}',
          discovered_at TEXT NOT NULL,
          UNIQUE(project_id, source_type, source_id),
          FOREIGN KEY(project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS social_analyses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          social_item_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          sentiment TEXT DEFAULT 'unknown',
          positive_pct INTEGER DEFAULT 0,
          negative_pct INTEGER DEFAULT 0,
          neutral_pct INTEGER DEFAULT 100,
          confidence INTEGER DEFAULT 0,
          summary TEXT DEFAULT '',
          reason TEXT DEFAULT '',
          narrative_label TEXT DEFAULT '',
          narrative_summary TEXT DEFAULT '',
          evidence_source TEXT DEFAULT '',
          provider TEXT DEFAULT '',
          analyzed_at TEXT NOT NULL,
          error TEXT DEFAULT '',
          FOREIGN KEY(social_item_id) REFERENCES social_items(id)
        );
        """
    )
    org = conn.execute("SELECT id FROM organizations LIMIT 1").fetchone()
    if org is None:
        now = now_iso()
        cur = conn.execute("INSERT INTO organizations (name, created_at) VALUES (?, ?)", ("APP.AI Demo Client", now))
        org_id = cur.lastrowid
        email = os.getenv("LIVE_ADMIN_EMAIL", "admin@vcheck")
        password = os.getenv("LIVE_ADMIN_PASSWORD", "vcheck123")
        conn.execute(
            "INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (org_id, email, generate_password_hash(password), "admin", now),
        )
        client_email = os.getenv("LIVE_CLIENT_EMAIL", "client@vcheck")
        client_password = os.getenv("LIVE_CLIENT_PASSWORD", "vcheck123")
        conn.execute(
            "INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (org_id, client_email, generate_password_hash(client_password), "client_viewer", now),
        )
        project_id = conn.execute(
            """INSERT INTO projects
            (org_id, name, target_type, description, keywords, relevance_terms, noise_terms, subscriber_threshold, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                org_id,
                "NTR Neel Dragon",
                "movie",
                "YouTube reputation monitoring for Jr NTR and Prashanth Neel's Dragon glimpse.",
                "\n".join(default_dragon_keywords()),
                "\n".join(default_dragon_relevance()),
                "How to Train Your Dragon\ndragon ball\ndragon fruit\nDragon 2025 Pradeep",
                30000,
                now,
                now,
            ),
        ).lastrowid
        client = conn.execute("SELECT id FROM users WHERE email = ?", (client_email,)).fetchone()
        if client:
            conn.execute(
                "INSERT OR IGNORE INTO user_project_access (user_id, project_id, created_at) VALUES (?, ?, ?)",
                (client[0], project_id, now),
            )
    conn.commit()
    conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if not is_admin():
            flash("This area is available only to APP.AI admins.")
            return redirect(url_for("index"))
        return fn(*args, **kwargs)

    return wrapper


def operator_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if not can_operate():
            flash("Your client login is view-only.")
            return redirect(request.referrer or url_for("index"))
        return fn(*args, **kwargs)

    return wrapper


def is_admin() -> bool:
    return bool(g.user and g.user["role"] == "admin")


def can_operate() -> bool:
    return bool(g.user and g.user["role"] in {"admin", "client_admin"})


@app.before_request
def load_user() -> None:
    g.user = None
    if session.get("user_id"):
        g.user = db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        flash("Invalid email or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/reports/<path:filename>")
@login_required
def reports(filename: str):
    return send_from_directory(ROOT / "public", filename)


@app.route("/")
@login_required
def index():
    projects = accessible_projects()
    return render_template("index.html", projects=projects)


@app.route("/projects/new", methods=["GET", "POST"])
@admin_required
def new_project():
    if request.method == "POST":
        now = now_iso()
        db().execute(
            """INSERT INTO projects
            (org_id, name, target_type, description, keywords, relevance_terms, noise_terms, subscriber_threshold, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                g.user["org_id"],
                request.form["name"].strip(),
                request.form.get("target_type", "movie"),
                request.form.get("description", "").strip(),
                request.form.get("keywords", "").strip(),
                request.form.get("relevance_terms", "").strip(),
                request.form.get("noise_terms", "").strip(),
                int(request.form.get("subscriber_threshold", "30000") or 30000),
                now,
                now,
            ),
        )
        db().commit()
        return redirect(url_for("index"))
    return render_template("project_form.html", project=None)


@app.route("/users")
@admin_required
def users():
    rows = db().execute("SELECT * FROM users WHERE org_id = ? ORDER BY created_at DESC", (g.user["org_id"],)).fetchall()
    return render_template("users.html", users=rows)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"].strip()
        role = request.form.get("role", "client_viewer")
        project_id = int(request.form.get("project_id", "0") or 0)
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return redirect(url_for("new_user"))
        try:
            cur = db().execute(
                "INSERT INTO users (org_id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (g.user["org_id"], email, generate_password_hash(password), role, now_iso()),
            )
            if project_id:
                db().execute(
                    "INSERT OR IGNORE INTO user_project_access (user_id, project_id, created_at) VALUES (?, ?, ?)",
                    (cur.lastrowid, project_id, now_iso()),
                )
            db().commit()
            flash(f"Client login created for {email}.")
            return redirect(url_for("users"))
        except sqlite3.IntegrityError:
            flash("That email already exists.")
    projects = db().execute("SELECT * FROM projects WHERE org_id = ? ORDER BY name", (g.user["org_id"],)).fetchall()
    return render_template("user_form.html", projects=projects)


@app.route("/projects/<int:project_id>")
@login_required
def project_dashboard(project_id: int):
    project = get_project(project_id)
    period = request.args.get("period", "daily")
    stats = project_stats(project_id, period)
    videos = latest_videos(project_id)
    scans = db().execute("SELECT * FROM scans WHERE project_id = ? ORDER BY started_at DESC LIMIT 8", (project_id,)).fetchall()
    return render_template("project.html", project=project, stats=stats, videos=videos, scans=scans, period=period)


@app.route("/projects/<int:project_id>/scan", methods=["POST"])
@operator_required
def run_project_scan(project_id: int):
    project = get_project(project_id)
    hours = int(request.form.get("hours", "24") or 24)
    result = collect_youtube(project, hours)
    flash(f"Scan completed: {result['found']} found, {result['new']} new, {result['priority']} priority.")
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.route("/projects/<int:project_id>/analyze", methods=["POST"])
@operator_required
def run_project_analysis(project_id: int):
    project = get_project(project_id)
    limit = int(request.form.get("limit", "5") or 5)
    priority_only = request.form.get("priority_only") == "on"
    result = analyze_project(project, limit=limit, priority_only=priority_only)
    flash(f"Analysis completed: {result['done']} done, {result['failed']} failed, {result['skipped']} skipped.")
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.route("/projects/<int:project_id>/analyze-text", methods=["POST"])
@operator_required
def run_project_text_analysis(project_id: int):
    project = get_project(project_id)
    limit = int(request.form.get("limit", "25") or 25)
    priority_only = request.form.get("priority_only") == "on"
    result = analyze_project_text(project, limit=limit, priority_only=priority_only)
    flash(f"Text analysis completed: {result['done']} done, {result['failed']} failed, {result['skipped']} skipped.")
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.route("/projects/<int:project_id>/social")
@login_required
def social_dashboard(project_id: int):
    project = get_project(project_id)
    rows = db().execute(
        """SELECT si.*, sa.status analysis_status, sa.sentiment, sa.positive_pct, sa.negative_pct, sa.neutral_pct,
        sa.confidence, sa.summary, sa.reason, sa.evidence_source, sa.provider, sa.error
        FROM social_items si
        LEFT JOIN social_analyses sa ON sa.social_item_id = si.id AND sa.id = (SELECT max(id) FROM social_analyses WHERE social_item_id = si.id)
        WHERE si.project_id = ?
        ORDER BY si.discovered_at DESC, si.views DESC, si.likes DESC
        LIMIT 250""",
        (project_id,),
    ).fetchall()
    counts = social_stats(project_id)
    return render_template("social.html", project=project, rows=rows, counts=counts)


@app.route("/projects/<int:project_id>/review", methods=["GET", "POST"])
@login_required
def review_queue(project_id: int):
    project = get_project(project_id)
    if request.method == "POST":
        video_id = int(request.form["video_id"])
        sentiment = request.form["review_sentiment"]
        note = request.form.get("review_note", "").strip()
        action_status = request.form.get("action_status", "reviewed")
        latest = latest_analysis_for_video(video_id)
        db().execute(
            "INSERT INTO human_reviews (video_id, user_id, review_sentiment, review_note, action_status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, g.user["id"], sentiment, note, action_status, now_iso()),
        )
        db().execute(
            "INSERT INTO accuracy_labels (video_id, user_id, human_sentiment, ai_sentiment, match, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (video_id, g.user["id"], sentiment, latest["sentiment"] if latest else "", int(bool(latest and latest["sentiment"] == sentiment)), note, now_iso()),
        )
        db().commit()
        flash("Human review saved and added to the accuracy dataset.")
        return redirect(url_for("review_queue", project_id=project_id))

    videos = review_videos(project_id)
    stats = accuracy_stats(project_id)
    return render_template("review.html", project=project, videos=videos, stats=stats)


@app.route("/projects/<int:project_id>/verify", methods=["POST"])
@operator_required
def verify_project(project_id: int):
    project = get_project(project_id)
    limit = int(request.form.get("limit", "10") or 10)
    result = run_second_model_verification(project, limit)
    flash(f"Second-model verification: {result['done']} done, {result['failed']} failed, {result['skipped']} skipped.")
    return redirect(url_for("review_queue", project_id=project_id))


@app.route("/projects/<int:project_id>/alerts", methods=["GET", "POST"])
@operator_required
def alerts(project_id: int):
    project = get_project(project_id)
    if request.method == "POST":
        db().execute(
            """INSERT INTO alert_rules
            (project_id, name, status, min_subscribers, min_views, sentiment, negative_pct, keywords, destination, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                request.form["name"].strip(),
                request.form.get("status", "active"),
                int(request.form.get("min_subscribers", "0") or 0),
                int(request.form.get("min_views", "0") or 0),
                request.form.get("sentiment", "negative"),
                int(request.form.get("negative_pct", "50") or 50),
                request.form.get("keywords", "").strip(),
                request.form.get("destination", "").strip(),
                now_iso(),
            ),
        )
        db().commit()
        flash("Alert rule created.")
        return redirect(url_for("alerts", project_id=project_id))
    rules = db().execute("SELECT * FROM alert_rules WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
    events = db().execute(
        """SELECT ae.*, ar.name rule_name, v.title, v.url FROM alert_events ae
        JOIN alert_rules ar ON ar.id = ae.rule_id
        JOIN videos v ON v.id = ae.video_id
        WHERE ar.project_id = ? ORDER BY ae.created_at DESC LIMIT 50""",
        (project_id,),
    ).fetchall()
    return render_template("alerts.html", project=project, rules=rules, events=events)


@app.route("/projects/<int:project_id>/alerts/evaluate", methods=["POST"])
@operator_required
def evaluate_alerts_route(project_id: int):
    project = get_project(project_id)
    result = evaluate_alerts(project["id"])
    flash(f"Alerts evaluated: {result} new alert events queued.")
    return redirect(url_for("alerts", project_id=project_id))


@app.route("/projects/<int:project_id>/sources", methods=["GET", "POST"])
@admin_required
def data_sources(project_id: int):
    project = get_project(project_id)
    if request.method == "POST":
        now = now_iso()
        db().execute(
            "INSERT INTO data_sources (project_id, source_type, status, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, request.form["source_type"], request.form.get("status", "planned"), request.form.get("config_json", "{}"), now, now),
        )
        db().commit()
        flash("Data source configuration saved.")
        return redirect(url_for("data_sources", project_id=project_id))
    rows = db().execute("SELECT * FROM data_sources WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
    return render_template("sources.html", project=project, sources=rows)


@app.errorhandler(PermissionError)
def handle_permission_error(error: PermissionError):
    flash(str(error))
    return redirect(url_for("index"))


@app.errorhandler(RuntimeError)
def handle_runtime_error(error: RuntimeError):
    flash(str(error))
    return redirect(request.referrer or url_for("index"))


def get_project(project_id: int) -> sqlite3.Row:
    if is_admin():
        project = db().execute("SELECT * FROM projects WHERE id = ? AND org_id = ?", (project_id, g.user["org_id"])).fetchone()
    else:
        project = db().execute(
            """SELECT p.* FROM projects p
            JOIN user_project_access upa ON upa.project_id = p.id
            WHERE p.id = ? AND p.org_id = ? AND upa.user_id = ?""",
            (project_id, g.user["org_id"], g.user["id"]),
        ).fetchone()
    if not project:
        raise PermissionError("Project not found")
    return project


def accessible_projects() -> list[sqlite3.Row]:
    if is_admin():
        return db().execute("SELECT * FROM projects WHERE org_id = ? ORDER BY updated_at DESC", (g.user["org_id"],)).fetchall()
    return db().execute(
        """SELECT p.* FROM projects p
        JOIN user_project_access upa ON upa.project_id = p.id
        WHERE p.org_id = ? AND upa.user_id = ?
        ORDER BY p.updated_at DESC""",
        (g.user["org_id"], g.user["id"]),
    ).fetchall()


def collect_youtube(project: sqlite3.Row, hours: int) -> dict[str, int]:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing YOUTUBE_API_KEY in .env")
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
    started_at = now_iso()
    scan_id = db().execute(
        "INSERT INTO scans (project_id, started_at, hours, status) VALUES (?, ?, ?, ?)",
        (project["id"], started_at, hours, "running"),
    ).lastrowid
    db().commit()

    published_after = datetime.now(timezone.utc) - timedelta(hours=hours)
    raw_items = search_youtube(youtube, project, published_after)
    video_ids = list(raw_items.keys())
    details = fetch_video_details(youtube, video_ids)
    channel_ids = sorted({item["snippet"].get("channelId", "") for item in details if item.get("snippet", {}).get("channelId")})
    channel_stats = fetch_channel_stats(youtube, channel_ids)

    found = new = priority = 0
    for item in details:
        snippet = item.get("snippet", {})
        keyword = raw_items.get(item["id"], {}).get("keyword", "")
        if not relevant(project, keyword, snippet):
            continue
        found += 1
        duration_seconds = parse_duration(item.get("contentDetails", {}).get("duration", ""))
        channel_id = snippet.get("channelId", "")
        subscribers = channel_stats.get(channel_id)
        threshold = int(project["subscriber_threshold"])
        if subscribers is None:
            audio_priority = "unknown"
            reason = "subscriber count hidden/unavailable"
        elif subscribers >= threshold:
            audio_priority = "yes"
            priority += 1
            reason = f"channel has {subscribers} subscribers >= {threshold}"
        else:
            audio_priority = "no"
            reason = f"channel has {subscribers} subscribers < {threshold}"
        try:
            db().execute(
                """INSERT INTO videos
                (project_id, video_id, url, title, channel_title, channel_id, subscriber_count, audio_priority, priority_reason,
                 youtube_type, published_at, duration_seconds, views, likes, comments, description, keyword, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project["id"], item["id"], f"https://www.youtube.com/watch?v={item['id']}",
                    snippet.get("title", ""), snippet.get("channelTitle", ""), channel_id, subscribers,
                    audio_priority, reason, "Shorts" if duration_seconds <= 60 else "Video",
                    snippet.get("publishedAt", ""), duration_seconds,
                    int(item.get("statistics", {}).get("viewCount", 0) or 0),
                    int(item.get("statistics", {}).get("likeCount", 0) or 0),
                    int(item.get("statistics", {}).get("commentCount", 0) or 0),
                    snippet.get("description", ""), keyword, now_iso(),
                ),
            )
            new += 1
        except sqlite3.IntegrityError:
            db().execute(
                """UPDATE videos SET views=?, likes=?, comments=?, subscriber_count=?, audio_priority=?, priority_reason=?
                WHERE project_id=? AND video_id=?""",
                (
                    int(item.get("statistics", {}).get("viewCount", 0) or 0),
                    int(item.get("statistics", {}).get("likeCount", 0) or 0),
                    int(item.get("statistics", {}).get("commentCount", 0) or 0),
                    subscribers, audio_priority, reason, project["id"], item["id"],
                ),
            )
    db().execute(
        "UPDATE scans SET completed_at=?, found_count=?, new_count=?, status=? WHERE id=?",
        (now_iso(), found, new, "done", scan_id),
    )
    db().commit()
    return {"found": found, "new": new, "priority": priority}


def search_youtube(youtube: Any, project: sqlite3.Row, published_after: datetime) -> dict[str, dict[str, str]]:
    found = {}
    for keyword in split_lines(project["keywords"])[:35]:
        for attempt in range(3):
            try:
                response = youtube.search().list(
                    part="snippet",
                    q=keyword,
                    type="video",
                    order="date",
                    publishedAfter=published_after.isoformat().replace("+00:00", "Z"),
                    maxResults=25,
                    regionCode="IN",
                    safeSearch="none",
                ).execute()
                break
            except HttpError as error:
                if error.resp.status in {403, 429} and attempt < 2 and "rateLimitExceeded" in str(error):
                    time.sleep(70)
                    continue
                raise
        for item in response.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if video_id and video_id not in found:
                found[video_id] = {"keyword": keyword}
        time.sleep(1.5)
    return found


def fetch_video_details(youtube: Any, video_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for chunk in chunks(video_ids, 50):
        response = youtube.videos().list(part="snippet,contentDetails,statistics", id=",".join(chunk)).execute()
        out.extend(response.get("items", []))
    return out


def fetch_channel_stats(youtube: Any, channel_ids: list[str]) -> dict[str, int | None]:
    stats = {}
    for chunk in chunks(channel_ids, 50):
        response = youtube.channels().list(part="statistics", id=",".join(chunk)).execute()
        for item in response.get("items", []):
            raw = item.get("statistics", {})
            if raw.get("hiddenSubscriberCount") or not raw.get("subscriberCount"):
                stats[item["id"]] = None
            else:
                stats[item["id"]] = int(raw["subscriberCount"])
    return stats


def analyze_project(project: sqlite3.Row, limit: int, priority_only: bool) -> dict[str, int]:
    query = """
      SELECT v.* FROM videos v
      LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
      WHERE v.project_id = ? AND (a.status IS NULL OR a.status = 'failed')
    """
    params: list[Any] = [project["id"]]
    if priority_only:
        query += " AND v.audio_priority = 'yes'"
    query += " ORDER BY COALESCE(v.subscriber_count, 0) DESC, v.views DESC LIMIT ?"
    params.append(limit)
    videos = db().execute(query, params).fetchall()
    done = failed = skipped = 0
    for video in videos:
        try:
            result = analyze_video_with_gemini(project, video)
            insert_analysis(video["id"], "done", result)
            done += 1
        except Exception as error:
            insert_analysis(video["id"], "failed", {"error": str(error)[:500], "evidence_source": "gemini-audio"})
            failed += 1
        time.sleep(3)
    return {"done": done, "failed": failed, "skipped": skipped}


def analyze_project_text(project: sqlite3.Row, limit: int, priority_only: bool = False) -> dict[str, int]:
    query = """
      SELECT v.* FROM videos v
      LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
      WHERE v.project_id = ? AND (a.status IS NULL OR a.status = 'failed')
    """
    params: list[Any] = [project["id"]]
    if priority_only:
        query += " AND v.audio_priority = 'yes'"
    query += " ORDER BY v.audio_priority = 'yes' DESC, COALESCE(v.subscriber_count, 0) DESC, v.views DESC LIMIT ?"
    params.append(limit)
    videos = db().execute(query, params).fetchall()
    done = failed = skipped = 0
    for video in videos:
        try:
            result = analyze_video_text(project, video)
            insert_analysis(video["id"], "done", result)
            done += 1
        except Exception as error:
            insert_analysis(video["id"], "failed", {"error": str(error)[:500], "evidence_source": "text-analysis"})
            failed += 1
        time.sleep(1)
    return {"done": done, "failed": failed, "skipped": skipped}


def analyze_video_with_gemini(project: sqlite3.Row, video: sqlite3.Row) -> dict[str, Any]:
    if genai is None:
        raise RuntimeError("google-genai package not installed")
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")
    audio_path = download_audio(video["url"], video["video_id"])
    try:
        client = genai.Client(api_key=api_key)
        uploaded = client.files.upload(file=str(audio_path))
        prompt = json.dumps({
            "task": f"Analyze sentiment and narrative toward {project['name']}.",
            "rules": ["Use audio evidence only.", "Return JSON only.", "If no speech, mark neutral."],
            "video": {"title": video["title"], "channel": video["channel_title"], "url": video["url"]},
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
        }, ensure_ascii=False)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=[prompt, uploaded],
        )
        return parse_json(response.text or "{}") | {"provider": "gemini", "evidence_source": "gemini-audio"}
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass


def analyze_video_text(project: sqlite3.Row, video: sqlite3.Row) -> dict[str, Any]:
    text, source = build_text_evidence(video)
    if not text.strip():
        raise RuntimeError("No transcript, comments, title, or description available for text analysis")
    return analyze_text_with_gemini(project, video, text, source)


def build_text_evidence(video: sqlite3.Row) -> tuple[str, str]:
    transcript = fetch_transcript(video["video_id"])
    if transcript:
        metadata = metadata_text(video)
        return f"{metadata}\n\nTRANSCRIPT:\n{transcript}", "youtube-transcript"

    comments = fetch_comment_sample(video["video_id"])
    parts = [metadata_text(video)]
    if comments:
        parts.append("TOP COMMENTS:\n" + "\n".join(f"- {comment}" for comment in comments))
        return "\n\n".join(parts), "metadata-comments"
    return "\n\n".join(parts), "metadata"


def metadata_text(video: sqlite3.Row) -> str:
    return "\n".join([
        f"Title: {video['title']}",
        f"Channel: {video['channel_title']}",
        f"URL: {video['url']}",
        f"Format: {video['youtube_type']}",
        f"Views: {video['views']}",
        f"Likes: {video['likes']}",
        f"Comments count: {video['comments']}",
        f"Description: {video['description']}",
    ]).strip()


def fetch_transcript(video_id: str) -> str:
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["te", "ta", "hi", "en"])
        text = " ".join(item.get("text", "") for item in transcript)
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return ""


def fetch_comment_sample(video_id: str, max_results: int = 12) -> list[str]:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        return []
    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        response = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=max_results,
            order="relevance",
            textFormat="plainText",
        ).execute()
        comments = []
        for item in response.get("items", []):
            text = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {}).get("textDisplay", "")
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                comments.append(text[:700])
        return comments
    except Exception:
        return []


def analyze_text_with_gemini(project: sqlite3.Row, video: sqlite3.Row, text: str, source: str) -> dict[str, Any]:
    if genai is None:
        raise RuntimeError("google-genai package not installed")
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    prompt = json.dumps({
        "task": f"Analyze sentiment and narrative toward {project['name']} using text evidence.",
        "rules": [
            "Use transcript when present. If only metadata/comments are present, be conservative.",
            "Comments are audience reaction, not necessarily the creator's opinion.",
            "Return JSON only.",
            "If evidence is weak or unclear, mark neutral or mixed with lower confidence.",
        ],
        "video": {"title": video["title"], "channel": video["channel_title"], "url": video["url"], "evidence_source": source},
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
    }, ensure_ascii=False)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt],
    )
    return parse_json(response.text or "{}") | {"provider": "gemini-text", "evidence_source": source}


def download_audio(url: str, video_id: str) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="appai-audio-"))
    output = str(tmp_dir / f"{video_id}.%(ext)s")
    options = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": output,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": int(os.getenv("AUDIO_MAX_FILESIZE_MB", "24")) * 1024 * 1024,
    }
    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_browser and cookies_browser.lower() not in {"0", "false", "none", "off"}:
        options["cookiesfrombrowser"] = (cookies_browser,)
    js_runtimes = split_lines(os.getenv("YTDLP_JS_RUNTIMES", ""))
    if js_runtimes:
        options["js_runtimes"] = {runtime: {} for runtime in js_runtimes}
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        path = Path(downloader.prepare_filename(info))
    if not path.exists():
        matches = list(tmp_dir.glob(f"{video_id}.*"))
        if not matches:
            raise RuntimeError("audio download failed")
        path = matches[0]
    return path


def insert_analysis(video_id: int, status: str, result: dict[str, Any]) -> None:
    db().execute(
        """INSERT INTO analyses
        (video_id, status, sentiment, positive_pct, negative_pct, neutral_pct, confidence, narrative_label,
         narrative_summary, summary, reason, evidence_source, analyzed_at, provider, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            video_id,
            status,
            result.get("sentiment", "unknown"),
            clamp(result.get("positive_pct", 0)),
            clamp(result.get("negative_pct", 0)),
            clamp(result.get("neutral_pct", 100)),
            clamp(result.get("confidence", 0)),
            result.get("narrative_label", ""),
            result.get("narrative_summary", ""),
            result.get("summary", ""),
            result.get("reason", ""),
            result.get("evidence_source", ""),
            now_iso(),
            result.get("provider", ""),
            result.get("error", ""),
        ),
    )
    db().commit()


def project_stats(project_id: int, period: str) -> dict[str, Any]:
    days = 1 if period == "daily" else 7 if period == "weekly" else 30
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db().execute(
        """SELECT v.*, a.status analysis_status, a.sentiment, a.positive_pct, a.negative_pct, a.neutral_pct, a.evidence_source
        FROM videos v
        LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        WHERE v.project_id = ? AND v.discovered_at >= ?""",
        (project_id, since),
    ).fetchall()
    counts = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0, "unknown": 0}
    priority = analyzed = 0
    for row in rows:
        counts[row["sentiment"] if row["sentiment"] in counts else "unknown"] += 1
        if row["audio_priority"] == "yes":
            priority += 1
        if row["analysis_status"] == "done" and row["evidence_source"]:
            analyzed += 1
    total = len(rows)
    known_sentiment = counts["positive"] + counts["negative"] + counts["neutral"] + counts["mixed"]
    risk_base = known_sentiment or total
    risk = round(((counts["negative"] + counts["mixed"] * 0.5) / risk_base) * 100) if risk_base else 0
    return {"total": total, "priority": priority, "analyzed": analyzed, "risk": risk, **counts}


def latest_videos(project_id: int) -> list[sqlite3.Row]:
    return db().execute(
        """SELECT v.*, a.status analysis_status, a.sentiment, a.positive_pct, a.negative_pct, a.neutral_pct,
        a.narrative_label, a.narrative_summary, a.summary, a.reason, a.evidence_source, a.error
        FROM videos v
        LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        WHERE v.project_id = ?
        ORDER BY v.audio_priority = 'yes' DESC, COALESCE(v.subscriber_count, 0) DESC, v.published_at DESC
        LIMIT 120""",
        (project_id,),
    ).fetchall()


def latest_analysis_for_video(video_id: int) -> sqlite3.Row | None:
    return db().execute("SELECT * FROM analyses WHERE video_id = ? ORDER BY id DESC LIMIT 1", (video_id,)).fetchone()


def review_videos(project_id: int) -> list[sqlite3.Row]:
    return db().execute(
        """SELECT v.*, a.id analysis_id, a.status analysis_status, a.sentiment, a.positive_pct, a.negative_pct, a.neutral_pct,
        a.confidence, a.summary, a.reason, a.evidence_source, vr.status verification_status, vr.sentiment verification_sentiment,
        vr.agrees_with_primary, vr.reason verification_reason, hr.review_sentiment, hr.review_note, hr.action_status
        FROM videos v
        LEFT JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        LEFT JOIN verification_runs vr ON vr.video_id = v.id AND vr.id = (SELECT max(id) FROM verification_runs WHERE video_id = v.id)
        LEFT JOIN human_reviews hr ON hr.video_id = v.id AND hr.id = (SELECT max(id) FROM human_reviews WHERE video_id = v.id)
        WHERE v.project_id = ?
        ORDER BY
          hr.id IS NULL DESC,
          a.sentiment IN ('negative','mixed') DESC,
          COALESCE(v.subscriber_count, 0) DESC,
          v.views DESC
        LIMIT 120""",
        (project_id,),
    ).fetchall()


def accuracy_stats(project_id: int) -> dict[str, int]:
    rows = db().execute(
        """SELECT al.* FROM accuracy_labels al
        JOIN videos v ON v.id = al.video_id
        WHERE v.project_id = ?""",
        (project_id,),
    ).fetchall()
    total = len(rows)
    matches = sum(1 for row in rows if row["match"])
    return {"total": total, "matches": matches, "accuracy": round((matches / total) * 100) if total else 0}


def run_second_model_verification(project: sqlite3.Row, limit: int) -> dict[str, int]:
    rows = db().execute(
        """SELECT v.*, a.id analysis_id, a.sentiment primary_sentiment, a.summary primary_summary, a.reason primary_reason,
        a.positive_pct, a.negative_pct, a.neutral_pct
        FROM videos v
        JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        LEFT JOIN verification_runs vr ON vr.analysis_id = a.id
        WHERE v.project_id = ?
          AND a.status = 'done'
          AND vr.id IS NULL
          AND (a.sentiment IN ('negative','mixed') OR a.negative_pct >= 35 OR v.views >= 100000)
        ORDER BY a.sentiment IN ('negative','mixed') DESC, v.views DESC
        LIMIT ?""",
        (project["id"], limit),
    ).fetchall()
    done = failed = skipped = 0
    for row in rows:
        try:
            result = verify_with_openai(project, row)
            db().execute(
                """INSERT INTO verification_runs
                (analysis_id, video_id, provider, status, sentiment, confidence, agrees_with_primary, reason, summary, verified_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["analysis_id"], row["id"], result["provider"], "done", result["sentiment"], clamp(result.get("confidence", 0)),
                    int(result["sentiment"] == row["primary_sentiment"]), result.get("reason", ""), result.get("summary", ""), now_iso(), "",
                ),
            )
            db().commit()
            done += 1
        except Exception as error:
            db().execute(
                """INSERT INTO verification_runs
                (analysis_id, video_id, provider, status, verified_at, error) VALUES (?, ?, ?, ?, ?, ?)""",
                (row["analysis_id"], row["id"], "openai", "failed", now_iso(), str(error)[:500]),
            )
            db().commit()
            failed += 1
    return {"done": done, "failed": failed, "skipped": skipped}


def verify_with_openai(project: sqlite3.Row, row: sqlite3.Row) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for second-model verification")
    model = os.getenv("OPENAI_VERIFICATION_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a careful media reputation QA analyst. Return only JSON."},
            {"role": "user", "content": json.dumps({
                "task": f"Verify the sentiment toward {project['name']}.",
                "rules": ["Use the evidence summary and title only.", "Be conservative.", "Return JSON only."],
                "video": {"title": row["title"], "channel": row["channel_title"], "url": row["url"]},
                "primary_ai": {
                    "sentiment": row["primary_sentiment"],
                    "positive_pct": row["positive_pct"],
                    "negative_pct": row["negative_pct"],
                    "neutral_pct": row["neutral_pct"],
                    "summary": row["primary_summary"],
                    "reason": row["primary_reason"],
                },
                "required_json": {
                    "sentiment": "positive|negative|neutral|mixed",
                    "confidence": "0-100",
                    "summary": "short verification summary",
                    "reason": "whether you agree or disagree and why",
                },
            }, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        raw = json.loads(response.read().decode("utf-8"))
    result = json.loads(raw["choices"][0]["message"]["content"])
    result["provider"] = "openai"
    return result


def evaluate_alerts(project_id: int) -> int:
    rules = db().execute("SELECT * FROM alert_rules WHERE project_id = ? AND status = 'active'", (project_id,)).fetchall()
    if not rules:
        return 0
    videos = db().execute(
        """SELECT v.*, a.sentiment, a.negative_pct, a.summary, a.reason
        FROM videos v
        JOIN analyses a ON a.video_id = v.id AND a.id = (SELECT max(id) FROM analyses WHERE video_id = v.id)
        WHERE v.project_id = ? AND a.status = 'done'""",
        (project_id,),
    ).fetchall()
    created = 0
    for rule in rules:
        keywords = [term.lower() for term in split_lines(rule["keywords"])]
        for video in videos:
            if video["views"] < rule["min_views"]:
                continue
            if (video["subscriber_count"] or 0) < rule["min_subscribers"]:
                continue
            if rule["sentiment"] and rule["sentiment"] != "any" and video["sentiment"] != rule["sentiment"]:
                if video["negative_pct"] < rule["negative_pct"]:
                    continue
            if keywords:
                haystack = " ".join([video["title"], video["channel_title"], video["summary"] or "", video["reason"] or ""]).lower()
                if not any(term in haystack for term in keywords):
                    continue
            exists = db().execute("SELECT 1 FROM alert_events WHERE rule_id = ? AND video_id = ?", (rule["id"], video["id"])).fetchone()
            if exists:
                continue
            message = f"{rule['name']}: {video['sentiment']} signal on {video['channel_title']} - {video['title']} ({video['url']})"
            db().execute(
                "INSERT INTO alert_events (rule_id, video_id, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (rule["id"], video["id"], "queued", message, now_iso()),
            )
            created += 1
    db().commit()
    return created


def chart_values(stats: dict[str, Any]) -> dict[str, int]:
    return {
        "positive": int(stats.get("positive", 0)),
        "negative": int(stats.get("negative", 0)),
        "neutral": int(stats.get("neutral", 0)),
        "mixed": int(stats.get("mixed", 0)),
        "unknown": int(stats.get("unknown", 0)),
    }


def social_stats(project_id: int) -> dict[str, Any]:
    rows = db().execute(
        """SELECT si.source_type, coalesce(sa.status, 'pending') status, coalesce(sa.sentiment, 'pending') sentiment
        FROM social_items si
        LEFT JOIN social_analyses sa ON sa.social_item_id = si.id AND sa.id = (SELECT max(id) FROM social_analyses WHERE social_item_id = si.id)
        WHERE si.project_id = ?""",
        (project_id,),
    ).fetchall()
    by_source: dict[str, int] = {}
    by_sentiment = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0, "unknown": 0, "pending": 0}
    done = failed = 0
    for row in rows:
        by_source[row["source_type"]] = by_source.get(row["source_type"], 0) + 1
        sentiment = row["sentiment"] if row["sentiment"] in by_sentiment else "unknown"
        by_sentiment[sentiment] += 1
        if row["status"] == "done":
            done += 1
        if row["status"] == "failed":
            failed += 1
    return {"total": len(rows), "done": done, "failed": failed, "by_source": by_source, "by_sentiment": by_sentiment}


def relevant(project: sqlite3.Row, keyword: str, snippet: dict[str, Any]) -> bool:
    text = " ".join([keyword, snippet.get("title", ""), snippet.get("description", ""), snippet.get("channelTitle", "")]).lower()
    if any(term.lower() in text for term in split_lines(project["noise_terms"])):
        return False
    terms = split_lines(project["relevance_terms"])
    return not terms or any(term.lower() in text for term in terms)


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    days, hours, minutes, seconds = [int(part or 0) for part in match.groups()]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_json(text: str) -> dict[str, Any]:
    compact = text.strip()
    compact = re.sub(r"^```(?:json)?", "", compact, flags=re.I).strip()
    compact = re.sub(r"```$", "", compact).strip()
    try:
        return json.loads(compact)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", compact, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def split_lines(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n,]+", value or "") if item.strip()]


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_dragon_keywords() -> list[str]:
    return [
        "Dragon Glimpse", "NTR Dragon Glimpse", "NTR Neel Dragon", "Jr NTR Dragon", "Dragon NTR",
        "Dragon Telugu Glimpse", "Dragon Hindi Glimpse", "NTRNeel", "NTRNeel glimpse",
        "Jr NTR Prashanth Neel", "Prashanth Neel Dragon", "Mythri Movie Makers Dragon",
        "#DragonGlimpse", "#NTRNeel", "#JrNTR", "#PrashanthNeel",
    ]


def default_dragon_relevance() -> list[str]:
    return ["Dragon Glimpse", "NTRNeel", "NTR Neel", "Jr NTR", "Prashanth Neel", "Mythri Movie Makers", "Dragon Telugu"]


init_db()


app.jinja_env.globals["chart_values"] = chart_values


if __name__ == "__main__":
    app.run(host=os.getenv("LIVE_HOST", "0.0.0.0"), port=int(os.getenv("LIVE_PORT", "8000")), debug=True)
