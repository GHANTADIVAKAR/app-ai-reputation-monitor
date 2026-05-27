#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("LIVE_DATA_DIR", str(ROOT / "data"))).expanduser()
DB_PATH = Path(os.getenv("LIVE_DB_PATH", str(DATA_DIR / "live_app.sqlite3"))).expanduser()
OUTPUT_PATH = ROOT / "public" / "ntr-dragon-dashboard.html"


def main() -> int:
    data = load_dashboard_data(project_id=1)
    OUTPUT_PATH.write_text(render_html(data), encoding="utf-8")
    print(OUTPUT_PATH)
    print(json.dumps(data["summary"], indent=2, ensure_ascii=False))
    return 0


def load_dashboard_data(project_id: int) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    project = dict(con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    rows = [dict(row) for row in con.execute(
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
        WHERE v.project_id = ?
        ORDER BY v.audio_priority = 'yes' DESC, coalesce(v.subscriber_count, 0) DESC, v.views DESC
        """,
        (project_id,),
    )]
    con.close()

    sentiment_counts = Counter(normalize(row["sentiment"]) for row in rows)
    evidence_counts = Counter(clean_evidence(row["evidence_source"], row["provider"]) for row in rows)
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
        "project": project,
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


def normalize(value: str) -> str:
    value = (value or "unknown").strip().lower()
    return value if value in {"positive", "negative", "neutral", "mixed", "pending"} else "unknown"


def clean_evidence(source: str, provider: str) -> str:
    source = (source or "").strip()
    provider = (provider or "").strip()
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
    if source:
        return "other"
    return "pending"


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    project = data["project"]
    summary = data["summary"]
    risky_cards = "\n".join(risky_card(row, index + 1) for index, row in enumerate(data["top_risky"][:12])) or "<div class='empty-risk'>No risky videos detected.</div>"
    risky_rows = "\n".join(risky_row(row) for row in data["top_risky"]) or "<tr><td colspan='7'>No risky videos detected.</td></tr>"
    channel_rows = "\n".join(
        f"<div class='channel-row'><span>{html.escape(channel)}</span><strong>{count}</strong></div>"
        for channel, count in data["top_channels"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APP.AI Report - {html.escape(project['name'])}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>{css()}</style>
</head>
<body>
  <nav class="nav">
    <div class="brand"><span>A</span><div><strong>APP.AI</strong><small>Reputation Intelligence</small></div></div>
    <a href="/projects/1">Live App</a>
  </nav>
  <main>
    <section class="hero">
      <p class="eyebrow">Client Intelligence Report</p>
      <h1>{html.escape(project['name'])}</h1>
      <p>{html.escape(project['description'])}</p>
      <div class="hero-meta"><span>Generated {html.escape(data['generated_at'])}</span><span>YouTube monitoring</span><span>Audio + text fallback</span></div>
    </section>

    <section class="metrics">
      <article><span>Total Videos</span><strong>{summary['total']}</strong><small>Last scan dataset</small></article>
      <article><span>Analyzed</span><strong>{summary['analyzed']}</strong><small>Audio/text completed</small></article>
      <article><span>Priority Videos</span><strong>{summary['priority']}</strong><small>30K+ subscriber channels</small></article>
      <article class="risk"><span>Risk Score</span><strong>{summary['risk']}%</strong><small>negative + mixed pressure</small></article>
    </section>

    <section class="panel risk-focus">
      <div class="panel-head">
        <div><h2>Most Risky YouTube Links</h2><p>Client watchlist sorted by negative percentage, reach, and comments.</p></div>
      </div>
      <div class="risk-cards">{risky_cards}</div>
    </section>

    <section class="grid">
      <article class="panel"><h2>Sentiment Split</h2><canvas id="sentimentChart"></canvas></article>
      <article class="panel"><h2>Evidence Coverage</h2><canvas id="evidenceChart"></canvas></article>
      <article class="panel channels"><h2>Top Channels</h2>{channel_rows}</article>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div><h2>Risk Review Queue</h2><p>Videos with negative, mixed, or higher negative percentage signals.</p></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Video</th><th>Channel</th><th>Reach</th><th>Sentiment</th><th>Scores</th><th>Evidence</th><th>Reason</th></tr></thead>
          <tbody>{risky_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div><h2>Full Audit Table</h2><p>Search channel, title, sentiment, evidence, summary, or reason.</p></div>
        <input id="search" placeholder="Search rows...">
      </div>
      <div class="filters">
        <button data-filter="all" class="active">All</button>
        <button data-filter="audio">Audio</button>
        <button data-filter="metadata">Metadata</button>
        <button data-filter="metadata + comments">Comments</button>
        <button data-filter="negative">Negative</button>
        <button data-filter="mixed">Mixed</button>
        <button data-filter="failed">Failed</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Video</th><th>Channel</th><th>Reach</th><th>Priority</th><th>Sentiment</th><th>Evidence</th><th>Summary / Reason</th></tr></thead>
          <tbody id="auditBody"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>window.REPORT_DATA = {payload};{js()}</script>
</body>
</html>"""


def risky_row(row: dict) -> str:
    return f"""<tr>
      <td><a href="{html.escape(row['url'])}" target="_blank" rel="noreferrer">{html.escape(row['title'])}</a></td>
      <td>{html.escape(row['channel_title'])}</td>
      <td>{int(row.get('views') or 0):,} views<br><small>{int(row.get('comments') or 0):,} comments</small></td>
      <td><span class="badge {normalize(row['sentiment'])}">{html.escape(row['sentiment'])}</span></td>
      <td>{row['positive_pct']} / {row['negative_pct']} / {row['neutral_pct']}</td>
      <td>{html.escape(clean_evidence(row['evidence_source'], row['provider']))}</td>
      <td>{html.escape(row['reason'] or row['summary'] or row['error'])}</td>
    </tr>"""


def risky_card(row: dict, rank: int) -> str:
    sentiment = normalize(row["sentiment"])
    reason = row.get("reason") or row.get("summary") or row.get("error") or "Review this video manually."
    return f"""<article class="risk-card {sentiment}">
      <div class="risk-rank">#{rank}</div>
      <div class="risk-main">
        <a class="risk-title" href="{html.escape(row['url'])}" target="_blank" rel="noreferrer">{html.escape(row['title'])}</a>
        <div class="risk-meta"><span>{html.escape(row['channel_title'])}</span><span>{int(row.get('views') or 0):,} views</span><span>{int(row.get('comments') or 0):,} comments</span><span>{html.escape(clean_evidence(row['evidence_source'], row['provider']))}</span></div>
        <p>{html.escape(reason[:220])}</p>
      </div>
      <div class="risk-score">
        <span class="badge {sentiment}">{html.escape(row['sentiment'])}</span>
        <strong>{int(row.get('negative_pct') or 0)}%</strong>
        <small>negative</small>
        <a class="watch-btn" href="{html.escape(row['url'])}">Watch</a>
      </div>
    </article>"""


def css() -> str:
    return """
:root{--ink:#101828;--muted:#667085;--line:#e6edf5;--brand:#2563eb;--teal:#10b981;--red:#dc3d4b;--amber:#f59e0b;--paper:#f5f9ff;--shadow:0 20px 70px rgba(37,99,235,.12)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 10% 0,rgba(37,99,235,.14),transparent 30%),linear-gradient(180deg,#f8fbff,#fff)}
.nav{width:min(1180px,calc(100% - 32px));margin:16px auto;display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:18px;padding:12px 16px;box-shadow:var(--shadow);position:sticky;top:12px;z-index:2}.brand{display:flex;gap:12px;align-items:center}.brand span{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,var(--brand),#06b6d4,var(--teal));color:white;display:grid;place-items:center;font-weight:800}.brand strong,.brand small{display:block}.brand small,.hero p,p,small{color:var(--muted)}.nav a{font-weight:800;color:var(--brand);text-decoration:none}
main{width:min(1180px,calc(100% - 32px));margin:0 auto 48px}.hero{background:linear-gradient(135deg,rgba(15,23,42,.96),rgba(37,99,235,.9),rgba(13,148,136,.88)),url('https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=1800&q=80') center/cover;border-radius:28px;padding:46px;color:white;box-shadow:0 24px 90px rgba(37,99,235,.25)}.eyebrow{color:#9cf4dc;text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:800}.hero h1{font-size:48px;margin:8px 0 12px}.hero p{color:rgba(255,255,255,.78);max-width:720px}.hero-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}.hero-meta span{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.2);border-radius:999px;padding:9px 12px;font-weight:700}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.metrics article,.panel{background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.metrics article{padding:18px}.metrics span,.metrics small{display:block;color:var(--muted)}.metrics strong{font-size:34px;display:block;margin:8px 0}.risk strong{color:var(--red)}
.risk-focus{border-color:#ffd1d1;background:linear-gradient(180deg,#fff7f7,#fff)}.risk-cards{display:grid;gap:12px}.risk-card{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:start;border:1px solid var(--line);border-radius:14px;padding:14px;background:white}.risk-card.negative{border-color:#fda29b;background:#fff7f6}.risk-card.mixed{border-color:#fedf89;background:#fffbeb}.risk-rank{width:38px;height:38px;border-radius:999px;background:#101828;color:white;display:grid;place-items:center;font-weight:900}.risk-title{font-size:16px;color:#175cd3;font-weight:900;text-decoration:none}.risk-meta{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.risk-meta span{font-size:12px;color:#475467;background:#f2f4f7;border-radius:999px;padding:5px 8px}.risk-main p{margin:0;color:#475467}.risk-score{text-align:center;min-width:98px}.risk-score strong{display:block;font-size:28px;color:var(--red);margin-top:8px}.watch-btn{display:inline-flex;justify-content:center;margin-top:10px;background:#dc3d4b;color:white!important;text-decoration:none;border-radius:999px;padding:9px 14px;font-weight:900}.empty-risk{padding:20px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px}.panel{padding:20px;margin-bottom:16px}.panel h2{margin:0 0 12px}.panel-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.panel-head p{margin:4px 0 0}input{border:1px solid var(--line);border-radius:999px;padding:12px 14px;min-width:280px}.channel-row{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:10px 0}.channel-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.filters button{border:1px solid var(--line);background:white;border-radius:999px;padding:9px 12px;font-weight:800;cursor:pointer}.filters button.active{background:var(--brand);color:white}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1050px}th,td{text-align:left;vertical-align:top;border-top:1px solid var(--line);padding:13px 12px;font-size:14px}th{font-size:12px;color:#475467;text-transform:uppercase;background:#f7fbff}td a{color:#175cd3;font-weight:800;text-decoration:none}.badge{display:inline-flex;border-radius:999px;padding:6px 9px;font-size:12px;font-weight:800;text-transform:capitalize;background:#eef2f6}.positive{background:#dcfae6;color:#067647}.negative{background:#fee4e2;color:#b42318}.mixed{background:#fef0c7;color:#b54708}.neutral{background:#e0f2fe;color:#026aa2}.pending,.unknown{background:#eef2f6;color:#475467}.failed{background:#fee4e2;color:#b42318}.summary{max-width:430px}.summary small{display:block;margin-top:6px}.reach small{display:block}
@media(max-width:900px){.metrics,.grid{grid-template-columns:1fr}.hero{padding:30px}.hero h1{font-size:34px}.panel-head{display:block}input{width:100%;min-width:0;margin-top:12px}.risk-card{grid-template-columns:1fr}.risk-score{text-align:left}}
"""


def js() -> str:
    return """
const data = window.REPORT_DATA;
const sc = data.summary.sentiment_counts;
const ec = data.summary.evidence_counts;
const colors = {positive:'#19a974',negative:'#dc3d4b',neutral:'#7c8a9a',mixed:'#f5a524',unknown:'#cbd5e1',pending:'#94a3b8'};
new Chart(document.getElementById('sentimentChart'),{type:'doughnut',data:{labels:Object.keys(sc),datasets:[{data:Object.values(sc),backgroundColor:Object.keys(sc).map(k=>colors[k]||'#94a3b8'),borderWidth:0}]},options:{plugins:{legend:{position:'bottom'}},cutout:'64%'}});
new Chart(document.getElementById('evidenceChart'),{type:'bar',data:{labels:Object.keys(ec),datasets:[{data:Object.values(ec),backgroundColor:'#2563eb',borderRadius:8}]},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}});
const body=document.getElementById('auditBody');let active='all';
function evidence(row){if(row.evidence_source==='gemini-audio'||row.evidence_source==='openai-audio')return 'audio';if(row.evidence_source==='metadata-comments')return 'metadata + comments';if(row.evidence_source==='metadata')return 'metadata';if(row.evidence_source==='youtube-transcript')return 'transcript';return row.provider==='gemini-text'||row.provider==='openai'?'text':(row.evidence_source||'pending')}
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',\"'\":'&#39;'}[m]))}
function render(){const q=document.getElementById('search').value.toLowerCase();const rows=data.rows.filter(r=>{const ev=evidence(r);const hay=[r.title,r.channel_title,r.sentiment,ev,r.summary,r.reason,r.error].join(' ').toLowerCase();const filter=active==='all'||r.sentiment===active||ev===active||r.analysis_status===active;return filter&&hay.includes(q)});body.innerHTML=rows.map(r=>`<tr><td><a href=\"${esc(r.url)}\" target=\"_blank\">${esc(r.title)}</a><small>${esc(r.youtube_type)} · ${esc(r.published_at)}</small></td><td>${esc(r.channel_title)}<small>${Number(r.subscriber_count||0).toLocaleString()} subscribers</small></td><td class=\"reach\">${Number(r.views||0).toLocaleString()} views<small>${Number(r.comments||0).toLocaleString()} comments</small></td><td>${esc(r.audio_priority)}</td><td><span class=\"badge ${esc(r.sentiment)}\">${esc(r.sentiment)}</span><small>${r.positive_pct}/${r.negative_pct}/${r.neutral_pct}</small></td><td>${esc(evidence(r))}<small>${esc(r.analysis_status)}</small></td><td class=\"summary\">${esc(r.summary||r.error)}<small>${esc(r.reason)}</small></td></tr>`).join('')||'<tr><td colspan=\"7\">No matching rows.</td></tr>'}
document.getElementById('search').addEventListener('input',render);document.querySelectorAll('.filters button').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');active=btn.dataset.filter;render()}));render();
"""


if __name__ == "__main__":
    raise SystemExit(main())
